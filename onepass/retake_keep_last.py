"""保留最后一遍的核心策略与导出工具。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
import csv
import json
import math
import subprocess

from .asr_loader import Word
from .sent_align import (
    LOW_CONF as SENT_LOW_CONF,
    MAX_DUP_GAP_SEC as SENT_MAX_DUP_GAP_SEC,
    MERGE_ADJ_GAP_SEC,
    MIN_SENT_CHARS as SENT_MIN_SENT_CHARS,
    MatchHit,
    ReviewPoint,
    KeepSpan as SentenceKeepSpan,
    align_sentences_from_text,
)
from .text_norm import build_char_index_map, cjk_or_latin_seq, normalize_for_align

__all__ = [
    "KeepSpan",
    "RetakeResult",
    "SentenceReviewResult",
    "compute_retake_keep_last",
    "compute_sentence_review",
    "export_srt",
    "export_txt",
    "export_audition_markers",
    "export_edl_json",
    "export_sentence_srt",
    "export_sentence_txt",
    "export_sentence_markers",
    "export_sentence_edl_json",
    "infer_pause_boundaries",
]

MIN_SENT_CHARS = 12  # 句子长度低于该阈值不参与去重
MAX_DUP_GAP_SEC = 30.0  # 相邻命中间隔超过该值则认为不是重录
MAX_WINDOW_SEC = 90.0  # 单段 drop 上限

PAUSE_GAP_SEC = 0.45  # 词间间隔超过该值视为自然停顿
PAUSE_SNAP_LIMIT = 0.20  # 段首尾可吸附到停顿边界的最大距离
PAD_BEFORE = 0.08  # EDL 段首补偿
PAD_AFTER = 0.12  # EDL 段尾补偿
MIN_SEGMENT_SEC = 0.18  # 过短的片段需要合并或丢弃
MERGE_GAP_SEC = 0.06  # 吸附+补偿后相邻片段小于该间隔自动合并


@dataclass(slots=True)
class SentenceReviewResult:
    """句子级审阅模式的输出结构。"""

    hits: list[MatchHit]
    keep_spans: list[SentenceKeepSpan]
    review_points: list[ReviewPoint]
    edl_keep_segments: list[tuple[float, float]]
    stats: dict
    audio_start: float
    audio_end: float
    debug_rows: list[dict] | None = None


@dataclass(slots=True)
class KeepSpan:
    """记录保留的原文行与其时间区间。"""

    line_no: int
    text: str
    start: float
    end: float


@dataclass(slots=True)
class RetakeResult:
    """保留最后一遍策略的完整结果。"""

    keeps: list[KeepSpan]
    edl_keep_segments: list[tuple[float, float]]
    drops: list[tuple[float, float]]
    stats: dict
    debug_rows: list[dict] | None = None


def infer_pause_boundaries(words: list[Word], gap: float = PAUSE_GAP_SEC) -> list[tuple[float, float]]:
    """基于词级时间戳推断停顿区间。"""

    if not words:  # 无词直接返回空列表
        return []
    gap = max(0.0, gap)  # 防御性处理负值
    pauses: list[tuple[float, float]] = []
    if gap <= 0.0:  # 阈值为 0 表示不识别停顿
        return pauses
    for prev, current in zip(words, words[1:]):  # 遍历相邻词
        distance = current.start - prev.end  # 计算词间空隙
        if distance >= gap:  # 达到阈值视为停顿区
            left = prev.end
            right = current.start
            if right > left:  # 仅记录有效区间
                pauses.append((left, right))
    return pauses


def _merge_ranges(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """合并重叠或相连的区间。"""

    sorted_ranges = sorted((item for item in intervals if item[1] > item[0]), key=lambda it: it[0])
    if not sorted_ranges:
        return []
    merged: list[tuple[float, float]] = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _snap_value(value: float, candidates: Sequence[float], limit: float) -> float:
    """将时间点吸附到最近的候选边界。"""

    if not candidates:
        return value
    limit = max(0.0, limit)
    best = min(candidates, key=lambda item: abs(item - value))
    if abs(best - value) <= limit:
        return best
    return value


def _resolve_audio_duration(words: Sequence[Word], audio_path: Path | None) -> float:
    """优先通过 ffprobe 获取音频时长，失败时回退到词序列终点。"""

    fallback = words[-1].end if words else 0.0  # 词序列末尾作为兜底
    if audio_path is None:
        return fallback
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:  # 未安装 ffprobe 时直接回退
        return fallback
    if result.returncode != 0:
        return fallback
    try:
        value = float(result.stdout.strip())
    except ValueError:
        return fallback
    if not math.isfinite(value) or value <= 0:
        return fallback
    return value


def _refine_segments(
    keep_items: Sequence[object],
    *,
    audio_duration: float,
    pause_intervals: Sequence[tuple[float, float]],
    pause_snap_limit: float,
    pad_before: float,
    pad_after: float,
    merge_gap_sec: float,
    min_segment_sec: float,
    pause_align: bool,
    debug_label: str | None,
) -> tuple[set[int], list[tuple[float, float]], dict[str, object], list[dict]]:
    """对保留片段应用停顿吸附、补偿、合并及碎片剔除。"""

    # 预处理参数，确保均为非负
    pad_before = max(0.0, pad_before)
    pad_after = max(0.0, pad_after)
    merge_gap_sec = max(0.0, merge_gap_sec)
    min_segment_sec = max(0.0, min_segment_sec)
    pause_candidates: list[float] = []
    for start, end in pause_intervals:
        pause_candidates.extend([start, end])
    pause_candidates = sorted(set(pause_candidates))
    pause_used = pause_align and bool(pause_candidates)
    debug_rows: dict[int, dict] = {}
    segments: list[dict] = []
    pause_snaps = 0
    too_short_dropped = 0
    for index, item in enumerate(keep_items):
        orig_start = getattr(item, "start", 0.0)
        orig_end = getattr(item, "end", 0.0)
        if orig_end <= orig_start:
            row = {
                "item": debug_label or "",
                "index": index,
                "orig_start": orig_start,
                "orig_end": orig_end,
                "snap_start": orig_start,
                "snap_end": orig_end,
                "pad_start": orig_start,
                "pad_end": orig_end,
                "final_start": None,
                "final_end": None,
                "snap_start_used": False,
                "snap_end_used": False,
                "merged_into": "",
                "dropped": True,
                "notes": "invalid_source",
            }
            debug_rows[index] = row
            continue
        snapped_start = orig_start
        snapped_end = orig_end
        snap_start_used = False
        snap_end_used = False
        if pause_used:
            snapped = _snap_value(snapped_start, pause_candidates, pause_snap_limit)
            if snapped != snapped_start and snapped < orig_end:
                snapped_start = snapped
                snap_start_used = True
                pause_snaps += 1
            snapped = _snap_value(snapped_end, pause_candidates, pause_snap_limit)
            if snapped != snapped_end and snapped > snapped_start:
                snapped_end = snapped
                snap_end_used = True
                pause_snaps += 1
        padded_start = max(0.0, snapped_start - pad_before)
        padded_end = min(audio_duration, snapped_end + pad_after)
        if padded_end - padded_start <= 1e-6:
            row = {
                "item": debug_label or "",
                "index": index,
                "orig_start": orig_start,
                "orig_end": orig_end,
                "snap_start": snapped_start,
                "snap_end": snapped_end,
                "pad_start": padded_start,
                "pad_end": padded_end,
                "final_start": None,
                "final_end": None,
                "snap_start_used": snap_start_used,
                "snap_end_used": snap_end_used,
                "merged_into": "",
                "dropped": True,
                "notes": "clamped_to_zero",
            }
            debug_rows[index] = row
            too_short_dropped += 1
            continue
        row = {
            "item": debug_label or "",
            "index": index,
            "orig_start": orig_start,
            "orig_end": orig_end,
            "snap_start": snapped_start,
            "snap_end": snapped_end,
            "pad_start": padded_start,
            "pad_end": padded_end,
            "final_start": None,
            "final_end": None,
            "snap_start_used": snap_start_used,
            "snap_end_used": snap_end_used,
            "merged_into": "",
            "dropped": False,
            "notes": "",
        }
        debug_rows[index] = row
        segments.append(
            {
                "start": padded_start,
                "end": padded_end,
                "indices": [index],
            }
        )
    segments.sort(key=lambda item: item["start"])
    auto_merged = 0
    merged_segments: list[dict] = []
    for segment in segments:
        if not merged_segments:
            merged_segments.append(segment)
            continue
        prev = merged_segments[-1]
        gap = segment["start"] - prev["end"]
        if gap <= merge_gap_sec:
            prev["end"] = max(prev["end"], segment["end"])
            prev_indices = prev["indices"]
            for idx in segment["indices"]:
                if idx not in prev_indices:
                    prev_indices.append(idx)
                debug_rows[idx]["merged_into"] = prev_indices[0]
                note = debug_rows[idx]["notes"]
                debug_rows[idx]["notes"] = ";".join(filter(None, [note, "merge_gap"]))
            auto_merged += 1
        else:
            merged_segments.append(segment)

    refined_segments = merged_segments
    i = 0
    while i < len(refined_segments):
        segment = refined_segments[i]
        duration = segment["end"] - segment["start"]
        if duration >= min_segment_sec or len(refined_segments) == 1:
            i += 1
            continue
        if i > 0:
            prev = refined_segments[i - 1]
            prev["end"] = max(prev["end"], segment["end"])
            for idx in segment["indices"]:
                if idx not in prev["indices"]:
                    prev["indices"].append(idx)
                debug_rows[idx]["merged_into"] = prev["indices"][0]
                note = debug_rows[idx]["notes"]
                debug_rows[idx]["notes"] = ";".join(filter(None, [note, "merge_short_prev"]))
            auto_merged += 1
            refined_segments.pop(i)
            continue
        if i + 1 < len(refined_segments):
            nxt = refined_segments[i + 1]
            nxt["start"] = min(nxt["start"], segment["start"])
            nxt_indices = nxt["indices"]
            for idx in segment["indices"]:
                if idx not in nxt_indices:
                    nxt_indices.insert(0, idx)
                debug_rows[idx]["merged_into"] = nxt_indices[0]
                note = debug_rows[idx]["notes"]
                debug_rows[idx]["notes"] = ";".join(filter(None, [note, "merge_short_next"]))
            auto_merged += 1
            refined_segments.pop(i)
            continue
        for idx in segment["indices"]:
            debug_rows[idx]["dropped"] = True
            note = debug_rows[idx]["notes"]
            debug_rows[idx]["notes"] = ";".join(filter(None, [note, "dropped_short"]))
        too_short_dropped += len(segment["indices"])
        refined_segments.pop(i)
    active_indices: set[int] = set()
    final_segments: list[tuple[float, float]] = []
    for segment in refined_segments:
        start = max(0.0, min(audio_duration, segment["start"]))
        end = max(0.0, min(audio_duration, segment["end"]))
        if end - start <= 1e-6:
            for idx in segment["indices"]:
                debug_rows[idx]["dropped"] = True
                note = debug_rows[idx]["notes"]
                debug_rows[idx]["notes"] = ";".join(filter(None, [note, "dropped_after_merge"]))
            too_short_dropped += len(segment["indices"])
            continue
        final_segments.append((start, end))
        unique_indices = []
        seen: set[int] = set()
        for idx in segment["indices"]:
            if idx in seen:
                continue
            seen.add(idx)
            unique_indices.append(idx)
        segment["indices"] = unique_indices
        for idx in unique_indices:
            active_indices.add(idx)
            debug_rows[idx]["final_start"] = start
            debug_rows[idx]["final_end"] = end
            item_obj = keep_items[idx]
            if hasattr(item_obj, "start") and hasattr(item_obj, "end"):
                setattr(item_obj, "start", start)
                setattr(item_obj, "end", end)
    debug_list = []
    for idx in range(len(keep_items)):
        row = debug_rows.get(
            idx,
            {
                "item": debug_label or "",
                "index": idx,
                "orig_start": None,
                "orig_end": None,
                "snap_start": None,
                "snap_end": None,
                "pad_start": None,
                "pad_end": None,
                "final_start": None,
                "final_end": None,
                "snap_start_used": False,
                "snap_end_used": False,
                "merged_into": "",
                "dropped": True,
                "notes": "missing",
            },
        )
        debug_list.append(row)
    stats = {
        "pause_used": bool(pause_used),
        "pause_snaps": int(pause_snaps),
        "auto_merged": int(auto_merged),
        "too_short_dropped": int(too_short_dropped),
        "pad_ms": {
            "before": int(round(pad_before * 1000)),
            "after": int(round(pad_after * 1000)),
        },
    }
    return active_indices, final_segments, stats, debug_list


def _normalize_words(words: list[Word]) -> tuple[list[str], str, list[tuple[int, int]]]:
    """返回规范化后的词文本、拼接字符串与字符索引映射。"""

    normalized_words = [normalize_for_align(word.text) for word in words]  # 逐词规范化文本
    asr_norm_str = cjk_or_latin_seq(normalized_words)  # 拼接为连续字符串
    char_map = build_char_index_map(normalized_words)  # 构建词到字符区间映射
    return normalized_words, asr_norm_str, char_map  # 返回三项结果


def _line_to_units(line: str) -> str:
    """将原文行转换成匹配用的字符序列。"""

    return cjk_or_latin_seq([line])  # 借用统一逻辑去除空白


def _find_all_occurrences(haystack: str, needle: str) -> list[tuple[int, int]]:
    """找到 needle 在 haystack 中的所有出现位置。"""

    results: list[tuple[int, int]] = []  # 存放所有匹配区间
    if not needle:  # 空串无需匹配
        return results
    start = 0  # 当前搜索起点
    while True:  # 循环查找所有命中
        idx = haystack.find(needle, start)  # 从起点搜索子串
        if idx == -1:  # 未找到则结束循环
            break
        end = idx + len(needle)  # 计算命中区间的结束位置
        results.append((idx, end))  # 记录当前命中区间
        start = idx + 1  # 移动起点继续搜索后续命中
    return results  # 返回全部命中


def _longest_common_substring(a: str, b: str) -> tuple[int, int, int]:
    """返回 a 与 b 的最长公共子串长度及其结尾索引。"""

    if not a or not b:  # 任一为空串直接返回默认值
        return 0, -1, -1
    prev = [0] * (len(b) + 1)  # 上一行动态规划结果
    best_len = 0  # 记录最长长度
    best_a_end = -1  # 记录在 a 中的结束位置
    best_b_end = -1  # 记录在 b 中的结束位置
    for i, char_a in enumerate(a, start=1):  # 枚举 a 的每个字符
        current = [0]  # 当前行动态规划数组
        for j, char_b in enumerate(b, start=1):  # 枚举 b 的每个字符
            if char_a == char_b:  # 字符相等则延长公共子串
                length = prev[j - 1] + 1
            else:  # 不等则长度重置为 0
                length = 0
            current.append(length)  # 记录当前单元格的值
            if length > best_len:  # 若找到更长子串
                best_len = length  # 更新最佳长度
                best_a_end = i - 1  # 记录 a 中的结束索引
                best_b_end = j - 1  # 记录 b 中的结束索引
        prev = current  # 将当前行保存为下一轮的上一行
    return best_len, best_a_end, best_b_end  # 返回长度及结束位置


def _char_range_to_word_range(char_range: tuple[int, int], char_map: list[tuple[int, int]]) -> tuple[int, int] | None:
    """将字符区间映射为词索引区间。"""

    start_char, end_char = char_range  # 提取字符区间边界
    if start_char == end_char:  # 空区间无需映射
        return None
    start_idx = None  # 初始化起始词索引
    end_idx = None  # 初始化结束词索引
    for idx, (w_start, w_end) in enumerate(char_map):  # 遍历词字符区间
        if start_idx is None and start_char < w_end:  # 首个覆盖起始字符的词
            start_idx = idx
        if w_start < end_char:  # 只要词与结束字符有交集就更新
            end_idx = idx
        if w_end >= end_char and start_idx is not None:  # 已覆盖整个区间可提前结束
            break
    if start_idx is None or end_idx is None:  # 映射失败返回 None
        return None
    return start_idx, end_idx  # 返回词索引区间


def _word_range_to_time(word_range: tuple[int, int], words: list[Word]) -> tuple[float, float]:
    """根据词索引区间得到时间区间。"""

    start_idx, end_idx = word_range  # 拆解索引区间
    start_time = words[start_idx].start  # 获取起始词的开始时间
    end_time = words[end_idx].end  # 获取结束词的结束时间
    return start_time, end_time  # 返回时间范围


def compute_retake_keep_last(
    words: list[Word],
    original_txt: Path,
    *,
    min_sent_chars: int = MIN_SENT_CHARS,
    max_dup_gap_sec: float = MAX_DUP_GAP_SEC,
    max_window_sec: float = MAX_WINDOW_SEC,
    pad_before: float = PAD_BEFORE,
    pad_after: float = PAD_AFTER,
    pause_align: bool = True,
    pause_gap_sec: float = PAUSE_GAP_SEC,
    pause_snap_limit: float = PAUSE_SNAP_LIMIT,
    min_segment_sec: float = MIN_SEGMENT_SEC,
    merge_gap_sec: float = MERGE_GAP_SEC,
    silence_ranges: Sequence[tuple[float, float]] | None = None,
    audio_path: Path | None = None,
    debug_label: str | None = None,
) -> RetakeResult:
    """根据原文 TXT 匹配词序列，仅保留最后一次出现的行。"""

    if not words:  # 无词序列时无法继续
        raise ValueError("词序列为空，无法执行保留最后一遍逻辑。请先导入有效的 ASR JSON。")
    try:
        raw_text = original_txt.read_text(encoding="utf-8")  # 读取原文文本
    except FileNotFoundError as exc:  # 文件不存在
        raise FileNotFoundError(f"未找到原文 TXT: {original_txt}. 请确认路径是否正确。") from exc
    except OSError as exc:  # 其他 I/O 异常
        raise OSError(f"读取原文 TXT 失败: {exc}. 请检查文件权限或关闭占用程序。") from exc

    lines = raw_text.splitlines()  # 按行拆分原文
    _, asr_norm_str, char_map = _normalize_words(words)  # 获取规范化词串与索引
    if not asr_norm_str:  # 如果规范化后为空
        raise ValueError("规范化后的词序列为空，可能所有词都是标点或空白。请检查 JSON 输出。")

    keeps: list[KeepSpan] = []  # 存放最终保留段
    strict_matches = 0  # 记录严格匹配次数
    fallback_matches = 0  # 记录回退匹配次数
    unmatched = 0  # 记录未匹配行数
    len_gate_skipped = 0  # 记录因长度过短而跳过去重的次数
    neighbor_gap_skipped = 0  # 记录因近邻间隔过长而跳过去重的次数
    for index, line in enumerate(lines, start=1):  # 遍历每一行原文
        norm_line = normalize_for_align(line)  # 规范化当前行
        units = _line_to_units(norm_line)  # 转换为匹配字符序列
        if not units:  # 空行直接跳过
            continue
        occurrences = _find_all_occurrences(asr_norm_str, units)  # 严格子串匹配
        spans: list[tuple[float, float]] = []  # 存放命中的时间区间
        if occurrences:  # 若有严格匹配
            strict_matches += 1  # 计数
            for occ in occurrences:  # 遍历所有命中
                word_range = _char_range_to_word_range(occ, char_map)  # 字符区间映射到词区间
                if word_range is None:  # 映射失败则跳过
                    continue
                spans.append(_word_range_to_time(word_range, words))  # 词区间转时间区间
        else:  # 严格匹配失败
            length, _, asr_end = _longest_common_substring(units, asr_norm_str)  # 计算 LCS
            if length >= 0.8 * len(units) and asr_end != -1:  # 达到阈值视为近似命中
                fallback_matches += 1  # 回退匹配计数
                asr_start = max(0, asr_end - length + 1)  # 推算子串起点并防止越界
                word_range = _char_range_to_word_range((asr_start, asr_end + 1), char_map)  # 映射到词区间
                if word_range is not None:  # 映射成功
                    spans.append(_word_range_to_time(word_range, words))  # 追加时间区间
        if not spans:  # 若没有任何命中
            unmatched += 1  # 记录未匹配
            continue
        spans.sort(key=lambda item: item[0])  # 先按起点排序方便去重
        sentence_length = len(units)  # 规范化后的长度
        if sentence_length < max(0, min_sent_chars):  # 长度不足阈值时跳过去重
            len_gate_skipped += max(0, len(spans) - 1)  # 累计被跳过的命中数量
            filtered_spans = spans  # 全部保留
        else:
            filtered_spans, skipped_by_gap = _filter_spans_by_gap(spans, max_dup_gap_sec)
            neighbor_gap_skipped += skipped_by_gap
        for span_start, span_end in filtered_spans:  # 逐个保留有效区间
            keeps.append(
                KeepSpan(
                    line_no=index,
                    text=line,
                    start=span_start,
                    end=span_end,
                )
            )

    audio_duration = _resolve_audio_duration(words, audio_path)
    pause_intervals: list[tuple[float, float]] = []
    silence_count = 0
    if pause_align:
        pause_intervals = infer_pause_boundaries(words, pause_gap_sec)
        if silence_ranges:
            clamped_silence = [
                (
                    max(0.0, min(audio_duration, start)),
                    max(0.0, min(audio_duration, end)),
                )
                for start, end in silence_ranges
                if end > start
            ]
            silence_count = len(clamped_silence)
            pause_intervals.extend(clamped_silence)
        pause_intervals = _merge_ranges(pause_intervals)
    active_indices, final_segments, refine_stats, debug_rows = _refine_segments(
        keeps,
        audio_duration=audio_duration,
        pause_intervals=pause_intervals,
        pause_snap_limit=pause_snap_limit,
        pad_before=pad_before,
        pad_after=pad_after,
        merge_gap_sec=merge_gap_sec,
        min_segment_sec=min_segment_sec,
        pause_align=pause_align,
        debug_label=debug_label,
    )
    keeps = [keeps[idx] for idx in range(len(keeps)) if idx in active_indices]
    keeps.sort(key=lambda item: item.start)
    edl_keep_segments = final_segments
    drops = _invert_segments(edl_keep_segments, 0.0, audio_duration)  # 计算补集
    drops, window_splits = _split_long_segments(drops, max_window_sec)

    matched_line_numbers = {span.line_no for span in keeps}  # 统计匹配到的原文行
    stats = {  # 汇总统计信息
        "total_words": len(words),
        "total_lines": len(lines),
        "matched_lines": len(matched_line_numbers),
        "strict_matches": strict_matches,
        "fallback_matches": fallback_matches,
        "unmatched_lines": unmatched,
        "len_gate_skipped": len_gate_skipped,
        "neighbor_gap_skipped": neighbor_gap_skipped,
        "max_window_splits": window_splits,
        "audio_duration": audio_duration,
        "keep_duration": sum(max(0.0, end - start) for start, end in edl_keep_segments),
        "silence_regions": silence_count,
    }
    stats.update(refine_stats)
    keep_duration = stats["keep_duration"]
    if audio_duration > 0:
        stats["cut_ratio"] = max(0.0, min(1.0, (audio_duration - keep_duration) / audio_duration))
    else:
        stats["cut_ratio"] = 0.0

    return RetakeResult(
        keeps=keeps,
        edl_keep_segments=edl_keep_segments,
        drops=drops,
        stats=stats,
        debug_rows=debug_rows,
    )


def compute_sentence_review(
    words: list[Word],
    original_txt: Path,
    *,
    puncts: str | None = None,
    min_sent_chars: int = SENT_MIN_SENT_CHARS,
    max_dup_gap_sec: float = SENT_MAX_DUP_GAP_SEC,
    merge_gap_sec: float = MERGE_ADJ_GAP_SEC,
    low_conf: float = SENT_LOW_CONF,
    pad_before: float = PAD_BEFORE,
    pad_after: float = PAD_AFTER,
    pause_align: bool = True,
    pause_gap_sec: float = PAUSE_GAP_SEC,
    pause_snap_limit: float = PAUSE_SNAP_LIMIT,
    min_segment_sec: float = MIN_SEGMENT_SEC,
    segment_merge_gap_sec: float = MERGE_GAP_SEC,
    silence_ranges: Sequence[tuple[float, float]] | None = None,
    audio_path: Path | None = None,
    debug_label: str | None = None,
) -> SentenceReviewResult:
    """执行句子级审阅模式的匹配与统计。"""

    if not words:
        raise ValueError("词序列为空，无法执行句子级审阅逻辑。")
    try:
        raw_text = original_txt.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"未找到原文 TXT: {original_txt}. 请确认路径是否正确。") from exc
    except OSError as exc:
        raise OSError(f"读取原文 TXT 失败: {exc}. 请检查文件权限或是否被占用。") from exc

    align_result = align_sentences_from_text(
        raw_text,
        words,
        puncts=puncts,
        min_sent_chars=min_sent_chars,
        max_dup_gap_sec=max_dup_gap_sec,
        merge_gap_sec=merge_gap_sec,
        low_conf=low_conf,
    )
    keep_spans = list(align_result.keep_spans)
    audio_duration = _resolve_audio_duration(words, audio_path)
    pause_intervals: list[tuple[float, float]] = []
    silence_count = 0
    if pause_align:
        pause_intervals = infer_pause_boundaries(words, pause_gap_sec)
        if silence_ranges:
            clamped_silence = [
                (
                    max(0.0, min(audio_duration, start)),
                    max(0.0, min(audio_duration, end)),
                )
                for start, end in silence_ranges
                if end > start
            ]
            silence_count = len(clamped_silence)
            pause_intervals.extend(clamped_silence)
        pause_intervals = _merge_ranges(pause_intervals)
    active_indices, final_segments, refine_stats, debug_rows = _refine_segments(
        keep_spans,
        audio_duration=audio_duration,
        pause_intervals=pause_intervals,
        pause_snap_limit=pause_snap_limit,
        pad_before=pad_before,
        pad_after=pad_after,
        merge_gap_sec=segment_merge_gap_sec,
        min_segment_sec=min_segment_sec,
        pause_align=pause_align,
        debug_label=debug_label,
    )
    keep_spans = [keep_spans[idx] for idx in range(len(keep_spans)) if idx in active_indices]
    keep_segments = final_segments
    span_by_sent: dict[int, tuple[float, float]] = {}
    for span in keep_spans:
        for sent_idx in span.sent_indices:
            span_by_sent[sent_idx] = (span.start, span.end)
    for hit in align_result.hits:
        bounds = span_by_sent.get(hit.sent_idx)
        if bounds:
            hit.start_time, hit.end_time = bounds
    audio_start = 0.0
    audio_end = audio_duration
    stats = dict(align_result.stats)
    stats.update(
        {
            "total_words": len(words),
            "audio_start": audio_start,
            "audio_end": audio_end,
            "keep_segments": len(keep_segments),
            "audio_duration": audio_duration,
            "keep_duration": sum(max(0.0, end - start) for start, end in keep_segments),
            "silence_regions": silence_count,
        }
    )
    stats.update(refine_stats)
    if audio_duration > 0:
        stats["cut_ratio"] = max(0.0, min(1.0, (audio_duration - stats["keep_duration"]) / audio_duration))
    else:
        stats["cut_ratio"] = 0.0
    hits_sorted = sorted(align_result.hits, key=lambda item: (item.start_time, item.end_time))
    return SentenceReviewResult(
        hits=hits_sorted,
        keep_spans=keep_spans,
        review_points=align_result.review_points,
        edl_keep_segments=keep_segments,
        stats=stats,
        audio_start=audio_start,
        audio_end=audio_end,
        debug_rows=debug_rows,
    )


def _merge_segments(segments: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """合并重叠或相邻的时间片段。"""

    sorted_segments = sorted((s for s in segments if s[1] > s[0]), key=lambda item: item[0])  # 过滤无效区间并排序
    if not sorted_segments:  # 没有有效片段时返回空列表
        return []
    merged: list[tuple[float, float]] = [sorted_segments[0]]  # 以首个片段为基准
    for start, end in sorted_segments[1:]:  # 遍历其余片段
        last_start, last_end = merged[-1]  # 取已合并的最后一个区间
        if start <= last_end:  # 若当前片段与上一个重叠或相邻
            merged[-1] = (last_start, max(last_end, end))  # 合并为更大的区间
        else:  # 不重叠时直接追加
            merged.append((start, end))
    return merged  # 返回合并结果


def _invert_segments(segments: list[tuple[float, float]], start: float, end: float) -> list[tuple[float, float]]:
    """根据 keep 段求补集，即需要丢弃的时间段。"""

    drops: list[tuple[float, float]] = []  # 存放补集区间
    cursor = start  # 当前未覆盖区域的起点
    for seg_start, seg_end in segments:  # 遍历保留区间
        if seg_start > cursor:  # 如果存在间隔则记为 drop
            drops.append((cursor, seg_start))
        cursor = max(cursor, seg_end)  # 将游标推进到最新位置
    if cursor < end:  # 结尾若仍有空缺
        drops.append((cursor, end))
    return drops  # 返回补集片段


def _filter_spans_by_gap(
    spans: list[tuple[float, float]], max_gap: float
) -> tuple[list[tuple[float, float]], int]:
    """按照近邻间隔过滤重复命中，返回保留区间与跳过次数。"""

    if not spans or max_gap is None:
        return spans, 0
    kept: list[tuple[float, float]] = []
    skipped = 0
    for idx, current in enumerate(spans):  # 遍历所有命中
        if idx == len(spans) - 1:
            kept.append(current)
            continue
        next_span = spans[idx + 1]
        gap = next_span[0] - current[0]
        should_drop = False
        if max_gap is not None:
            if max_gap > 0 and gap <= max_gap:
                should_drop = True
            elif max_gap <= 0 and gap <= 0:
                should_drop = True
        if should_drop:
            continue  # 间隔较短视为重录，丢弃当前命中
        kept.append(current)
        skipped += 1  # 记录因间隔较大而保留前一次命中
    return kept, skipped


def _split_long_segments(
    segments: list[tuple[float, float]], max_duration: float
) -> tuple[list[tuple[float, float]], int]:
    """将超出时长限制的 drop 段拆分成更小的片段。"""

    if max_duration <= 0:
        return [seg for seg in segments if seg[1] > seg[0]], 0
    result: list[tuple[float, float]] = []
    splits = 0
    for start, end in segments:
        if end <= start:
            continue
        remaining_start = start
        while (end - remaining_start) > max_duration:
            chunk_end = remaining_start + max_duration
            result.append((remaining_start, chunk_end))
            splits += 1
            remaining_start = chunk_end
        result.append((remaining_start, end))
    return result, splits


def export_srt(keeps: list[KeepSpan], out_path: Path) -> Path:
    """将保留行导出为 SRT 字幕文件。"""

    lines = []  # 收集字幕行
    for index, span in enumerate(keeps, start=1):  # 遍历所有保留段
        lines.append(f"{index}")  # 写入序号
        lines.append(f"{_format_timestamp(span.start)} --> {_format_timestamp(span.end)}")  # 写入时间轴
        lines.append(span.text)  # 写入原文内容
        lines.append("")  # 空行分隔条目
    out_path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    out_path.write_text("\n".join(lines), encoding="utf-8")  # 写出字幕文本
    return out_path  # 返回输出路径


def export_txt(keeps: list[KeepSpan], out_path: Path) -> Path:
    """导出保留行的纯文本稿。"""

    content = "\n".join(span.text for span in keeps)  # 拼接原文行
    out_path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    out_path.write_text(content, encoding="utf-8")  # 写出文本
    return out_path  # 返回输出路径


def export_audition_markers(keeps: list[KeepSpan], out_path: Path) -> Path:
    """导出 Adobe Audition 标记 CSV。"""

    from .markers import ensure_csv_header, seconds_to_hmsms  # 局部导入避免循环

    header = ["Name", "Start", "Duration", "Type", "Description"]
    out_path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    with out_path.open("w", encoding="utf-8-sig", newline="") as csvfile:  # 打开 CSV 文件
        writer = csv.writer(csvfile)  # 创建写入器
        writer.writerow(header)  # 写入表头
        for span in keeps:  # 遍历所有保留段
            duration = max(0.0, span.end - span.start)  # 计算持续时间，确保非负
            description = span.text[:24]  # 截取描述预览
            writer.writerow(
                [
                    f"L{span.line_no}",
                    seconds_to_hmsms(span.start),
                    seconds_to_hmsms(duration),
                    "cue",
                    description,
                ]
            )  # 写入一行
    ensure_csv_header(header)
    return out_path  # 返回输出路径


def export_edl_json(
    edl_keep_segments: list[tuple[float, float]],
    source_audio_rel: str | None,
    out_path: Path,
    samplerate: int | None = None,
    channels: int | None = None,
) -> Path:
    """导出仅包含 keep 动作的 EDL JSON。"""

    segments = [  # 构造 EDL 片段列表
        {"start": start, "end": end, "action": "keep"}
        for start, end in edl_keep_segments
    ]
    payload = {  # 组装完整的 EDL 结构
        "source_audio": source_audio_rel,
        "samplerate": samplerate,
        "channels": channels,
        "segments": segments,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"  # 序列化 JSON
    out_path.write_text(content, encoding="utf-8")  # 写出文件
    return out_path  # 返回输出路径


def export_sentence_srt(hits: Sequence[MatchHit], out_path: Path) -> Path:
    """将句子级命中导出为 SRT 字幕。"""

    sorted_hits = sorted(hits, key=lambda item: (item.start_time, item.end_time))
    lines: list[str] = []
    for index, hit in enumerate(sorted_hits, start=1):
        lines.append(str(index))
        lines.append(f"{_format_timestamp(hit.start_time)} --> {_format_timestamp(hit.end_time)}")
        lines.append(hit.sent_text)
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def export_sentence_txt(hits: Sequence[MatchHit], out_path: Path) -> Path:
    """导出句子级命中的纯文本稿。"""

    sorted_hits = sorted(hits, key=lambda item: (item.start_time, item.end_time))
    content = "\n".join(hit.sent_text for hit in sorted_hits)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def export_sentence_markers(
    hits: Sequence[MatchHit],
    review_points: Sequence[ReviewPoint],
    out_path: Path,
) -> Path:
    """导出句子级命中与审阅点的 Audition 标记。"""

    from .markers import ensure_csv_header, seconds_to_hmsms

    header = ["Name", "Start", "Duration", "Type", "Description"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)
        for hit in sorted(hits, key=lambda item: (item.start_time, item.end_time)):
            duration = max(0.0, hit.end_time - hit.start_time)
            writer.writerow(
                [
                    f"L{hit.sent_idx}",
                    seconds_to_hmsms(hit.start_time),
                    seconds_to_hmsms(duration),
                    "cue",
                    hit.sent_text[:48],
                ]
            )
        for point in review_points:
            description_prefix = "[LOW]" if point.kind == "low_conf" else "[REVIEW]"
            description = f"{description_prefix} {point.sent_text[:48]}".strip()
            start_time = point.at_time if point.at_time is not None else point.start_time or 0.0
            duration = max(
                0.0,
                (point.end_time or start_time or 0.0) - (point.start_time or start_time or 0.0),
            )
            writer.writerow(
                [
                    f"R{point.sent_idx}",
                    seconds_to_hmsms(start_time or 0.0),
                    seconds_to_hmsms(duration),
                    "cue",
                    description,
                ]
            )
    ensure_csv_header(header)
    return out_path


def export_sentence_edl_json(
    keep_segments: Sequence[tuple[float, float]],
    audio_start: float,
    audio_end: float,
    out_path: Path,
    *,
    review_only: bool,
    source_audio_rel: str | None = None,
    samplerate: int | None = None,
    channels: int | None = None,
) -> Path:
    """根据句子级命中导出专用 EDL JSON。"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if review_only:
        segments = [{"start": audio_start, "end": audio_end, "action": "keep"}]
    else:
        segments = [
            {"start": start, "end": end, "action": "keep"}
            for start, end in keep_segments
        ]
    payload = {
        "source_audio": source_audio_rel,
        "samplerate": samplerate,
        "channels": channels,
        "segments": segments,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _format_timestamp(seconds: float) -> str:
    """将秒转换为 SRT 时间戳。"""

    total_milliseconds = int(round(seconds * 1000))  # 将秒转换为毫秒整数
    hours, remainder = divmod(total_milliseconds, 3600_000)  # 拆分出小时
    minutes, remainder = divmod(remainder, 60_000)  # 继续拆分分钟
    secs, millis = divmod(remainder, 1000)  # 最后得到秒和毫秒
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"  # 按 SRT 格式输出
