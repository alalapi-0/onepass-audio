"""保留最后一遍的核心策略与导出工具。"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


from .asr_loader import Word
from .edl_writer import EDLWriteResult, write_edl
from .markers_writer import write_audition_csv
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
from .match_core import MatchResult, TokenStream, build_token_stream, match_line_to_tokens
from .text_norm import (
    apply_alias_map,
    build_char_index_map,
    cjk_or_latin_seq,
    normalize_for_align,
    normalize_text,
)
from .utils.subproc import run_cmd

LOGGER = logging.getLogger(__name__)

__all__ = [
    "EDLWriteResult",
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


def _is_align_text(path: Path) -> bool:
    """Return True if the given path points to a *.align.txt variant."""

    try:
        name = path.name.lower()
    except Exception:
        return False
    return name.endswith(".align.txt")

MIN_SENT_CHARS = 12  # 句子长度低于该阈值不参与去重
MAX_DUP_GAP_SEC = 30.0  # 相邻命中间隔超过该值则认为不是重录
MAX_WINDOW_SEC = 90.0  # 单段 drop 上限
FALLBACK_MAX_KEEP_SEC = 20.0  # 兜底段最长保留时长

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
    fallback_used: bool = False
    fallback_reason: str | None = None
    fallback_marker_note: str | None = None
    audio_duration: float = 0.0
    edl_fallback: bool = False
    edl_fallback_reason: str | None = None
    edl_segment_metadata: list[dict[str, object]] | None = None
    unmatched_samples: list[dict[str, object]] | None = None
    match_debug_rows: list[dict[str, object]] | None = None


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
        cp = run_cmd(cmd)
    except FileNotFoundError:
        return fallback
    if cp.returncode != 0:
        return fallback
    output = (cp.stdout or "").strip() or (cp.stderr or "").strip()
    try:
        value = float(output)
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


def _normalize_words(
    words: list[Word],
    alias_map: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[str], str, list[tuple[int, int]]]:
    """返回规范化后的词文本、拼接字符串与字符索引映射。"""

    normalized_words = [normalize_for_align(word.text) for word in words]
    if alias_map:
        normalized_words = [apply_alias_map(token, alias_map) for token in normalized_words]
    asr_norm_str = cjk_or_latin_seq(normalized_words)
    if alias_map:
        asr_norm_str = apply_alias_map(asr_norm_str, alias_map)
    char_map = build_char_index_map(normalized_words)
    return normalized_words, asr_norm_str, char_map


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


_HASH_BASE = 257
_HASH_MASK = (1 << 64) - 1


def _rolling_hash_power(base: int, exp: int) -> int:
    """计算 base**exp 在 2**64 空间内的值。"""

    if exp <= 0:
        return 1
    return pow(base, exp, 1 << 64)


def _fast_candidates(words_chars: str, line_chars: str, k: int, limit: int) -> list[int]:
    """通过 Rabin–Karp 滚动哈希快速筛选可能的窗口起点。"""

    if not words_chars or not line_chars or limit <= 0:
        return []
    limit = max(1, limit)
    max_k = min(max(k, 3), len(words_chars), len(line_chars))
    best_hits: Counter[int] = Counter()
    for current_k in range(max_k, 2, -1):
        if len(line_chars) < current_k or len(words_chars) < current_k:
            continue
        high = _rolling_hash_power(_HASH_BASE, current_k - 1)
        index: dict[int, list[int]] = defaultdict(list)
        hash_value = 0
        for idx, ch in enumerate(words_chars):
            hash_value = ((hash_value * _HASH_BASE) + ord(ch)) & _HASH_MASK
            if idx + 1 >= current_k:
                start = idx + 1 - current_k
                index[hash_value].append(start)
                leading = ord(words_chars[start])
                hash_value = (hash_value - (leading * high)) & _HASH_MASK
        if not index:
            continue
        line_hash = 0
        for idx, ch in enumerate(line_chars):
            line_hash = ((line_hash * _HASH_BASE) + ord(ch)) & _HASH_MASK
            if idx + 1 >= current_k:
                candidate_positions = index.get(line_hash)
                if candidate_positions:
                    segment = line_chars[idx + 1 - current_k : idx + 1]
                    for pos in candidate_positions:
                        if words_chars[pos : pos + current_k] == segment:
                            best_hits[pos] += 1
                leading = ord(line_chars[idx + 1 - current_k])
                line_hash = (line_hash - (leading * high)) & _HASH_MASK
        if best_hits:
            break
    if not best_hits:
        snippet_len = min(max_k, len(line_chars))
        snippet = line_chars[:snippet_len]
        pos = words_chars.find(snippet)
        if pos >= 0:
            best_hits[pos] = 1
    ordered = sorted(best_hits.items(), key=lambda item: (-item[1], item[0]))
    return [pos for pos, _ in ordered[:limit]]


def _preview_text(text: str, limit: int = 120) -> str:
    """裁剪文本用于日志打印。"""

    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    cleaned = [ch for ch in text if not ch.isspace()]
    if not cleaned:
        return 0.0
    ascii_count = sum(1 for ch in cleaned if ord(ch) < 128)
    return ascii_count / len(cleaned)


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


def _fallback_keep_all(
    words: Sequence[Word],
    audio_duration: float,
    lines: Sequence[str],
) -> list[KeepSpan]:
    """兜底策略：整段保留，确保至少有一个 KEEP 片段。"""

    end_time = audio_duration
    if end_time <= 0 and words:
        end_time = words[-1].end
    if end_time < 0:
        end_time = 0.0
    text = next((line.strip() for line in lines if line.strip()), "KEEP_ALL_FALLBACK")
    keep = KeepSpan(line_no=0, text=text or "KEEP_ALL_FALLBACK", start=0.0, end=end_time)
    return [keep]


def _fallback_align_greedy(
    words: Sequence[Word],
    lines: Sequence[str],
    words_chars: str,
    char_map: Sequence[tuple[int, int]],
    *,
    min_anchor_ngram: int,
    max_windows: int,
    expand_window: bool = False,
) -> list[KeepSpan]:
    """贪心对齐兜底：使用锚点快速吸附文本行。"""

    if not words_chars:
        return []
    keeps: list[KeepSpan] = []
    cursor = 0
    min_k = max(3, min_anchor_ngram)
    word_list = list(words)
    for line_no, line in enumerate(lines, start=1):
        norm_line = normalize_for_align(line)
        units = _line_to_units(norm_line)
        if not units:
            continue
        anchor_len = min(len(units), min_k)
        candidates = _fast_candidates(words_chars, units, anchor_len, max_windows)
        if not candidates and anchor_len > 3:
            candidates = _fast_candidates(words_chars, units, 3, max_windows)
        if not candidates:
            continue
        best_pos = min(
            candidates,
            key=lambda pos: (pos < cursor, abs(pos - cursor)),
        )
        window_start = best_pos
        window_end = min(len(words_chars), best_pos + max(len(units), anchor_len + min_anchor_ngram))
        if window_end <= window_start:
            continue
        word_range = _char_range_to_word_range((window_start, window_end), char_map)
        if word_range is None:
            continue
        if expand_window:
            expand = max(1, min_anchor_ngram)
            start_idx = max(0, word_range[0] - expand)
            end_idx = min(len(word_list) - 1, word_range[1] + expand)
            start_time = word_list[start_idx].start
            end_time = word_list[end_idx].end
        else:
            start_time, end_time = _word_range_to_time(word_range, word_list)
        if keeps and start_time < keeps[-1].end:
            start_time = keeps[-1].end
        if end_time < start_time:
            end_time = start_time
        keeps.append(KeepSpan(line_no=line_no, text=line, start=start_time, end=end_time))
        cursor = window_end
    return keeps


def _build_match_stages(initial_anchor: int, initial_ratio: float) -> list[dict[str, object]]:
    """构造匹配退化阶梯。"""

    stages: list[dict[str, object]] = [
        {
            "label": "base",
            "anchor": max(1, int(initial_anchor)),
            "ratio": max(0.05, float(initial_ratio)),
        }
    ]
    stage1_anchor = max(3, min(stages[0]["anchor"], 4))
    stage1_ratio = max(stages[0]["ratio"], 0.46)
    if stage1_anchor != stages[-1]["anchor"] or not math.isclose(stage1_ratio, stages[-1]["ratio"], rel_tol=1e-3):
        stages.append({"label": "stage-1", "anchor": stage1_anchor, "ratio": stage1_ratio})
    stage2_anchor = max(3, min(stage1_anchor, 3))
    stage2_ratio = max(stage1_ratio, 0.55)
    if stage2_anchor != stages[-1]["anchor"] or not math.isclose(stage2_ratio, stages[-1]["ratio"], rel_tol=1e-3):
        stages.append({"label": "stage-2", "anchor": stage2_anchor, "ratio": stage2_ratio})
    return stages


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
    alias_map: Mapping[str, Sequence[str]] | None = None,
    match_alias_map: Mapping[str, str] | None = None,
    debug_label: str | None = None,
    fast_match: bool = True,
    max_windows: int = 200,
    match_timeout: float = 60.0,
    max_distance_ratio: float = 0.35,
    min_anchor_ngram: int = 6,
    fallback_policy: str = "greedy",
    compute_timeout_sec: float = 300.0,
    no_collapse_align: bool = True,
    drop_ascii_parens: bool = False,
    collect_match_debug: bool = False,
) -> RetakeResult:
    """根据原文 TXT 匹配词序列，仅保留最后一次出现的行。"""

    drop_ascii_parens = bool(drop_ascii_parens)
    if not words:  # 无词序列时无法继续
        raise ValueError("词序列为空，无法执行保留最后一遍逻辑。请先导入有效的 ASR JSON。")
    try:
        raw_text = original_txt.read_text(encoding="utf-8-sig")  # 读取原文文本
    except FileNotFoundError as exc:  # 文件不存在
        raise FileNotFoundError(f"未找到原文 TXT: {original_txt}. 请确认路径是否正确。") from exc
    except OSError as exc:  # 其他 I/O 异常
        raise OSError(f"读取原文 TXT 失败: {exc}. 请检查文件权限或关闭占用程序。") from exc

    align_line_count_read: int | None = None
    if no_collapse_align and _is_align_text(original_txt):
        lines = raw_text.splitlines()
        align_line_count_read = len(lines)
        LOGGER.info("[align] read lines=%s file=%s", align_line_count_read, original_txt)
    else:
        normalized_text = normalize_text(
            raw_text,
            collapse_lines=False,
            drop_foreign_brackets=False,
            alias_map=alias_map,
        )
        lines = normalized_text.splitlines()  # 按行拆分原文
    fallback_policy_input = str(fallback_policy or "greedy")
    fallback_policy = fallback_policy_input.strip().lower()
    if fallback_policy == "greedy":
        fallback_policy = "align-greedy"
    elif fallback_policy == "greedy+expand":
        fallback_policy = "align-greedy-expand"
    if fallback_policy not in {"safe", "keep-all", "align-greedy", "align-greedy-expand"}:
        LOGGER.warning("未知 fallback_policy=%s，已回退为 align-greedy", fallback_policy)
        fallback_policy = "align-greedy"
    _, asr_norm_str, char_map = _normalize_words(words, alias_map)  # 获取规范化词串与索引
    char_map = list(char_map)
    if not asr_norm_str:  # 如果规范化后为空
        raise ValueError("规范化后的词序列为空，可能所有词都是标点或空白。请检查 JSON 输出。")

    token_stream: TokenStream | None = None
    try:
        token_payload = [
            {"text": word.text, "start": word.start, "end": word.end}
            for word in words
        ]
        token_stream = build_token_stream(token_payload, match_alias_map)
    except Exception:
        token_stream = None
    LOGGER.info(
        "[match] stem=%s lines=%s tokens=%s alias=%s",
        debug_label or "-",
        len(lines),
        len(words),
        bool(match_alias_map),
    )

    params_snapshot = {
        "fast_match": bool(fast_match),
        "max_windows": int(max_windows),
        "match_timeout": float(match_timeout),
        "max_distance_ratio": float(max_distance_ratio),
        "min_anchor_ngram": int(min_anchor_ngram),
        "fallback_policy": str(fallback_policy),
        "fallback_policy_input": fallback_policy_input,
        "min_sent_chars": int(min_sent_chars),
        "max_dup_gap_sec": float(max_dup_gap_sec),
        "compute_timeout_sec": float(compute_timeout_sec),
        "alias_map": bool(alias_map),
        "match_alias_map": bool(match_alias_map),
        "no_collapse_align": bool(no_collapse_align),
    }
    LOGGER.info("参数快照: %s", json.dumps(params_snapshot, ensure_ascii=False, sort_keys=True))

    start_ts = time.monotonic()
    compute_deadline: float | None = None
    if compute_timeout_sec and compute_timeout_sec > 0:
        compute_deadline = start_ts + compute_timeout_sec
    per_line_timeout: float | None = None
    if match_timeout and match_timeout > 0:
        per_line_timeout = float(match_timeout)
    current_distance_ratio = float(max_distance_ratio)
    current_anchor_ngram = max(1, int(min_anchor_ngram))
    fast_window_limit = max(1, int(max_windows))
    slow_window_limit = max(fast_window_limit, len(words) or 1)

    audio_duration = _resolve_audio_duration(words, audio_path)
    pause_intervals_base: list[tuple[float, float]] = []
    silence_count = 0
    if pause_align:
        pause_intervals_base = infer_pause_boundaries(words, pause_gap_sec)
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
            pause_intervals_base.extend(clamped_silence)
        pause_intervals_base = _merge_ranges(pause_intervals_base)

    active_deadline = compute_deadline

    def _align_once(min_sent_cutoff: int, dup_gap: float) -> dict[str, object]:
        local_keeps: list[KeepSpan] = []
        strict_count = 0
        fuzzy_count = 0
        unmatched_count = 0
        len_gate_skip = 0
        neighbor_skip = 0
        mismatch_details: list[dict[str, object]] = []
        unmatched_details: list[dict[str, object]] = []
        coarse_total = 0
        coarse_passed = 0
        fine_evaluated = 0
        pruned_candidates = 0
        search_elapsed = 0.0
        ascii_relaxed = 0
        match_rows: list[dict[str, object]] | None = [] if collect_match_debug else None
        for index, line in enumerate(lines, start=1):
            if active_deadline and time.monotonic() > active_deadline:
                raise TimeoutError("match deadline")
            norm_line = normalize_for_align(line)
            units = _line_to_units(norm_line)
            row_record: dict[str, object] | None = None
            if match_rows is not None:
                row_record = {
                    "idx": index,
                    "seg_len": len(line),
                    "score": 1.0,
                    "method": "skip-empty" if not units else "fail",
                    "anchor_hits": 0,
                    "tok_lo": -1,
                    "tok_hi": -1,
                    "t_start": -1.0,
                    "t_end": -1.0,
                    "text_preview": line.replace("\t", " ").replace("\n", " "),
                }
            if not units:
                if match_rows is not None and row_record is not None:
                    match_rows.append(row_record)
                continue
            spans: list[tuple[float, float]] = []
            match_result: MatchResult | None = None
            effective_timeout = per_line_timeout
            local_deadline = active_deadline
            if local_deadline is not None:
                remaining = max(0.0, local_deadline - time.monotonic())
                if effective_timeout is None:
                    effective_timeout = remaining
                else:
                    effective_timeout = min(effective_timeout, remaining)
            line_ratio = current_distance_ratio
            if drop_ascii_parens and _ascii_ratio(line) > 0.6:
                line_ratio = max(line_ratio, 0.5)
                ascii_relaxed += 1
            window_limit = fast_window_limit if fast_match else slow_window_limit
            if token_stream is not None and token_stream.canonical_text:
                match_start = time.monotonic()
                match_result = match_line_to_tokens(
                    norm_line,
                    token_stream,
                    match_alias_map,
                    min_anchor_ngram=current_anchor_ngram,
                    max_windows=window_limit,
                    max_distance_ratio=line_ratio,
                    match_timeout=effective_timeout,
                    prefer_latest=True,
                )
                search_elapsed += time.monotonic() - match_start
            if match_result:
                spans.append((match_result.time_start, match_result.time_end))
                if match_result.method == "anchor+lev":
                    strict_count += 1
                else:
                    fuzzy_count += 1
                hits = max(1, int(match_result.anchor_hits or 0))
                coarse_total += hits
                coarse_passed += hits
                fine_evaluated += 1
                if row_record is not None:
                    row_record.update(
                        {
                            "score": match_result.score,
                            "method": match_result.method,
                            "anchor_hits": match_result.anchor_hits,
                            "tok_lo": match_result.tok_start,
                            "tok_hi": match_result.tok_end,
                            "t_start": match_result.time_start,
                            "t_end": match_result.time_end,
                        }
                    )
            else:
                unmatched_count += 1
                preview_line = _preview_text(line, 120)
                if len(mismatch_details) < 10:
                    mismatch_details.append(
                        {
                            "line_no": index,
                            "text": preview_line,
                            "text_view": _preview_text(units, 120),
                            "words_view": "-",
                            "distance": None,
                        }
                    )
                if len(unmatched_details) < 10:
                    unmatched_details.append(
                        {
                            "line_no": index,
                            "text": preview_line,
                            "closest_words_snippet": "-",
                            "distance": None,
                        }
                    )
                LOGGER.warning(
                    "[unmatched] line=%s text=%s",
                    index,
                    _preview_text(units, 120),
                )
                if match_rows is not None and row_record is not None:
                    match_rows.append(row_record)
                continue
            spans.sort(key=lambda item: item[0])
            sentence_length = len(units)
            if sentence_length < max(0, min_sent_cutoff):
                len_gate_skip += max(0, len(spans) - 1)
                filtered_spans = spans
            else:
                filtered_spans, skipped_by_gap = _filter_spans_by_gap(spans, dup_gap)
                neighbor_skip += skipped_by_gap
            for span_start, span_end in filtered_spans:
                local_keeps.append(
                    KeepSpan(
                        line_no=index,
                        text=line,
                        start=span_start,
                        end=span_end,
                    )
                )
            if match_rows is not None and row_record is not None:
                match_rows.append(row_record)
        return {
            "keeps": local_keeps,
            "strict_matches": strict_count,
            "fallback_matches": fuzzy_count,
            "unmatched": unmatched_count,
            "len_gate_skipped": len_gate_skip,
            "neighbor_gap_skipped": neighbor_skip,
            "mismatch_samples": mismatch_details,
            "unmatched_examples": unmatched_details,
            "coarse_total": coarse_total,
            "coarse_passed": coarse_passed,
            "fine_evaluated": fine_evaluated,
            "pruned_candidates": pruned_candidates,
            "search_elapsed_sec": search_elapsed,
            "ascii_relaxed_lines": ascii_relaxed,
            "match_debug_rows": match_rows,
        }

    keeps: list[KeepSpan] = []
    edl_keep_segments: list[tuple[float, float]] = []
    segment_metadata: list[dict[str, object]] = []
    drops: list[tuple[float, float]] = []
    debug_rows: list[dict] = []
    stats: dict[str, object] | None = None
    strict_matches = 0
    fallback_matches = 0
    unmatched = 0
    len_gate_skipped = 0
    neighbor_gap_skipped = 0
    mismatch_samples: list[dict[str, object]] = []
    unmatched_examples: list[dict[str, object]] = []
    window_splits = 0
    timed_out = False
    match_engine = "anchor-ngram"
    match_debug_rows_final: list[dict[str, object]] | None = None

    current_min_sent = int(min_sent_chars)
    current_dup_gap = float(max_dup_gap_sec)
    recomputed = False
    coarse_total = 0
    coarse_passed = 0
    fine_evaluated_total = 0
    pruned_candidates_total = 0
    search_elapsed_total = 0.0
    ascii_relaxed = 0

    degrade_reason: str | None = None
    degrade_history: list[dict[str, object]] = []
    stage_plan = _build_match_stages(current_anchor_ngram, current_distance_ratio)
    stage_index = 0

    while stage_index < len(stage_plan):
        stage = stage_plan[stage_index]
        current_anchor_ngram = int(stage["anchor"])
        current_distance_ratio = float(stage["ratio"])
        try:
            alignment = _align_once(current_min_sent, current_dup_gap)
        except TimeoutError:
            timed_out = True
            degrade_reason = degrade_reason or "timeout"
            degrade_history.append(
                {
                    "reason": "timeout",
                    "stage": stage["label"],
                    "anchor_ngram": current_anchor_ngram,
                    "distance_ratio": current_distance_ratio,
                }
            )
            stage_index += 1
            if stage_index < len(stage_plan):
                next_stage = stage_plan[stage_index]
                LOGGER.warning(
                    "降级: stem=%s reason=timeout -> stage=%s anchor=%s ratio=%.2f",
                    debug_label or "-",
                    next_stage["label"],
                    next_stage["anchor"],
                    next_stage["ratio"],
                )
                if compute_timeout_sec and compute_timeout_sec > 0:
                    compute_deadline = time.monotonic() + compute_timeout_sec
                else:
                    compute_deadline = None
                active_deadline = compute_deadline
                continue
            LOGGER.warning(
                "降级失败: stem=%s reason=timeout -> 启用回退策略 %s",
                debug_label or "-",
                fallback_policy,
            )
            break
        except KeyboardInterrupt:
            timed_out = True
            degrade_reason = degrade_reason or "interrupt"
            degrade_history.append(
                {
                    "reason": "interrupt",
                    "stage": stage["label"],
                    "anchor_ngram": current_anchor_ngram,
                    "distance_ratio": current_distance_ratio,
                }
            )
            stage_index += 1
            if stage_index < len(stage_plan):
                if compute_timeout_sec and compute_timeout_sec > 0:
                    compute_deadline = time.monotonic() + compute_timeout_sec
                else:
                    compute_deadline = None
                active_deadline = compute_deadline
                continue
            LOGGER.warning(
                "降级失败: stem=%s reason=interrupt -> 启用回退策略 %s",
                debug_label or "-",
                fallback_policy,
            )
            break
        if not alignment:
            degrade_reason = degrade_reason or "no-match"
            stage_index += 1
            if stage_index < len(stage_plan):
                next_stage = stage_plan[stage_index]
                degrade_history.append(
                    {
                        "reason": "no-match",
                        "stage": stage["label"],
                        "next_stage": next_stage["label"],
                        "anchor_ngram": current_anchor_ngram,
                        "distance_ratio": current_distance_ratio,
                    }
                )
                LOGGER.warning(
                    "降级: stem=%s reason=no-match -> stage=%s anchor=%s ratio=%.2f",
                    debug_label or "-",
                    next_stage["label"],
                    next_stage["anchor"],
                    next_stage["ratio"],
                )
                if compute_timeout_sec and compute_timeout_sec > 0:
                    compute_deadline = time.monotonic() + compute_timeout_sec
                else:
                    compute_deadline = None
                active_deadline = compute_deadline
                continue
            LOGGER.warning(
                "降级失败: stem=%s reason=no-match -> 启用回退策略 %s",
                debug_label or "-",
                fallback_policy,
            )
            break
        if collect_match_debug:
            match_debug_rows_final = list(alignment.get("match_debug_rows") or [])
        raw_keeps = list(alignment.get("keeps", []))
        strict_matches = int(alignment.get("strict_matches", 0))
        fallback_matches = int(alignment.get("fallback_matches", 0))
        unmatched = int(alignment.get("unmatched", 0))
        len_gate_skipped = int(alignment.get("len_gate_skipped", 0))
        neighbor_gap_skipped = int(alignment.get("neighbor_gap_skipped", 0))
        mismatch_samples = list(alignment.get("mismatch_samples", []))
        unmatched_examples = list(alignment.get("unmatched_examples", []))
        coarse_total = int(alignment.get("coarse_total", 0))
        coarse_passed = int(alignment.get("coarse_passed", 0))
        fine_evaluated_total = int(alignment.get("fine_evaluated", 0))
        pruned_candidates_total = int(alignment.get("pruned_candidates", 0))
        search_elapsed_total = float(alignment.get("search_elapsed_sec", 0.0))
        ascii_relaxed = int(alignment.get("ascii_relaxed_lines", 0))

        pause_intervals = list(pause_intervals_base)
        active_indices, final_segments, refine_stats, debug_rows = _refine_segments(
            raw_keeps,
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
        keeps = [raw_keeps[idx] for idx in range(len(raw_keeps)) if idx in active_indices]
        keeps.sort(key=lambda item: item.start)
        edl_keep_segments = final_segments
        drops = _invert_segments(edl_keep_segments, 0.0, audio_duration)
        drops, window_splits = _split_long_segments(drops, max_window_sec)

        matched_line_numbers = {span.line_no for span in keeps if span.line_no > 0}
        keep_duration = sum(max(0.0, end - start) for start, end in edl_keep_segments)
        stats = {
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
            "keep_duration": keep_duration,
            "silence_regions": silence_count,
            "mismatch_examples": mismatch_samples[:3],
            "coarse_total": coarse_total,
            "coarse_passed": coarse_passed,
            "fine_evaluated": fine_evaluated_total,
            "pruned_candidates": pruned_candidates_total,
            "search_elapsed_sec": search_elapsed_total,
            "ascii_relaxed_lines": ascii_relaxed,
        }
        stats.update(refine_stats)
        stats["aligned_lines"] = stats["matched_lines"]
        stats["unaligned_lines"] = max(0, stats["total_lines"] - stats["matched_lines"])
        if align_line_count_read is not None:
            stats["align_line_count_read"] = align_line_count_read
        if unmatched >= 3 and mismatch_samples:
            preview = "; ".join(
                f"L{sample['line_no']}: {sample['text']}" for sample in mismatch_samples[:3]
            )
            LOGGER.warning(
                "检测到 %s 行未匹配。示例: %s。可尝试调整字符映射/同音合并或放宽匹配阈值。",
                unmatched,
                preview,
            )
        if audio_duration > 0:
            stats["cut_ratio"] = max(0.0, min(1.0, (audio_duration - keep_duration) / audio_duration))
        else:
            stats["cut_ratio"] = 0.0
        if stats.get("coarse_total", 0):
            stats["prune_rate"] = max(
                0.0,
                min(1.0, 1.0 - (stats.get("coarse_passed", 0) / max(stats.get("coarse_total", 1), 1))),
            )
        else:
            stats["prune_rate"] = 0.0

        if not recomputed and stats["cut_ratio"] > 0.6 and raw_keeps:
            new_min_sent = max(current_min_sent + 4, int(math.ceil(current_min_sent * 1.2)))
            new_dup_gap = max(0.5, current_dup_gap * 0.6) if current_dup_gap > 0 else current_dup_gap
            if new_min_sent != current_min_sent or not math.isclose(new_dup_gap, current_dup_gap, rel_tol=1e-2):
                LOGGER.warning(
                    "降级: stem=%s reason=cut_ratio_guard -> 参数调整: min_sent=%s->%s, max_dup_gap=%.2f->%.2f",
                    debug_label or "-",
                    current_min_sent,
                    new_min_sent,
                    current_dup_gap,
                    new_dup_gap,
                )
                previous_params = {
                    "min_sent": current_min_sent,
                    "max_dup_gap": current_dup_gap,
                }
                degrade_reason = degrade_reason or "cut_ratio_guard"
                current_min_sent = new_min_sent
                current_dup_gap = new_dup_gap
                degrade_history.append(
                    {
                        "reason": "cut_ratio_guard",
                        "previous": previous_params,
                        "adjusted": {
                            "min_sent": current_min_sent,
                            "max_dup_gap": current_dup_gap,
                        },
                    }
                )
                recomputed = True
                continue
        if stats.get("matched_lines", 0) == 0 and stage_index + 1 < len(stage_plan):
            degrade_reason = degrade_reason or "no-match"
            next_stage = stage_plan[stage_index + 1]
            degrade_history.append(
                {
                    "reason": "no-match",
                    "stage": stage["label"],
                    "next_stage": next_stage["label"],
                    "anchor_ngram": current_anchor_ngram,
                    "distance_ratio": current_distance_ratio,
                }
            )
            stage_index += 1
            continue
        break

    if stats is None:
        stats = {
            "total_words": len(words),
            "total_lines": len(lines),
            "matched_lines": 0,
            "strict_matches": 0,
            "fallback_matches": 0,
            "unmatched_lines": len(lines),
            "aligned_lines": 0,
            "unaligned_lines": len(lines),
            "len_gate_skipped": 0,
            "neighbor_gap_skipped": 0,
            "max_window_splits": 0,
            "audio_duration": audio_duration,
            "keep_duration": 0.0,
            "silence_regions": silence_count,
            "mismatch_examples": [],
            "coarse_total": 0,
            "coarse_passed": 0,
            "fine_evaluated": 0,
            "pruned_candidates": 0,
            "search_elapsed_sec": 0.0,
            "prune_rate": 0.0,
        }
        keeps = []
        edl_keep_segments = []
        drops = []
        debug_rows = []

    fallback_reasons: list[str] = []
    if timed_out:
        fallback_reasons.append("timeout")
    if stats.get("matched_lines", 0) == 0:
        fallback_reasons.append("no-match")
    if not edl_keep_segments:
        fallback_reasons.append("empty-segments")

    fallback_used = False
    fallback_note: str | None = None

    if fallback_reasons:
        fallback_used = True
        degrade_history.append(
            {
                "reason": "fallback",
                "policy": fallback_policy,
                "fallback_reasons": list(fallback_reasons),
            }
        )
        fallback_keeps: list[KeepSpan] | None = None
        fallback_engine = match_engine
        if "no-match" in fallback_reasons and degrade_reason is None:
            degrade_reason = "low_match"
            LOGGER.warning(
                "降级: stem=%s reason=low_match -> 启用回退策略 %s",
                debug_label or "-",
                fallback_policy,
            )
        if fallback_policy in {"safe", "align-greedy", "align-greedy-expand"}:
            fallback_keeps = _fallback_align_greedy(
                words,
                lines,
                asr_norm_str,
                char_map,
                min_anchor_ngram=min_anchor_ngram,
                max_windows=max_windows,
                expand_window=fallback_policy == "align-greedy-expand",
            )
            if fallback_keeps:
                fallback_engine = "fallback-align-greedy"
                fallback_note = "NO_MATCH_FALLBACK_ALIGN_GREEDY"
        if not fallback_keeps and fallback_policy in {"safe", "keep-all", "align-greedy", "align-greedy-expand"}:
            fallback_keeps = _fallback_keep_all(words, audio_duration, lines)
            fallback_engine = "fallback-keep-all"
            fallback_note = "NO_MATCH_FALLBACK_KEEP_ALL"
        if not fallback_keeps:
            fallback_keeps = _fallback_keep_all(words, audio_duration, lines)
            fallback_engine = "fallback-keep-all"
            fallback_note = "NO_MATCH_FALLBACK_KEEP_ALL"

        pause_intervals = list(pause_intervals_base)
        active_indices, final_segments, refine_stats, debug_rows = _refine_segments(
            fallback_keeps,
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
        keeps = [fallback_keeps[idx] for idx in range(len(fallback_keeps)) if idx in active_indices]
        keeps.sort(key=lambda item: item.start)
        edl_keep_segments = final_segments or [(0.0, 0.0)]
        edl_keep_segments = _split_segments_by_limit(
            edl_keep_segments,
            max_duration=FALLBACK_MAX_KEEP_SEC,
            keep_spans=keeps,
            silence_intervals=pause_intervals,
        )
        if "timeout" in fallback_reasons:
            segment_metadata = [{"fallback_reason": "timeout"} for _ in edl_keep_segments]
        drops = _invert_segments(edl_keep_segments, 0.0, audio_duration)
        drops, window_splits = _split_long_segments(drops, max_window_sec)
        stats.update(refine_stats)
        keep_duration = sum(max(0.0, end - start) for start, end in edl_keep_segments)
        stats["keep_duration"] = keep_duration
        if audio_duration > 0:
            stats["cut_ratio"] = max(0.0, min(1.0, (audio_duration - keep_duration) / audio_duration))
        else:
            stats["cut_ratio"] = 0.0
        stats["matched_lines"] = max(
            stats.get("matched_lines", 0),
            len({span.line_no for span in keeps if span.line_no > 0}),
        )
        match_engine = fallback_engine
        LOGGER.warning(
            "触发兜底策略(%s) -> %s", ",".join(fallback_reasons), fallback_engine
        )

    edl_segments_count = len(edl_keep_segments)
    unmatched_samples = unmatched_examples[:5]

    if not edl_keep_segments:
        fallback_used = True
        fallback_reason_value = stats.get("fallback_reason") or "no_keep_segments"
        if stats.get("fallback_reason"):
            details = list(stats.get("fallback_reason_details", []))
            if not details:
                details = [stats["fallback_reason"]]
            if "no_keep_segments" not in details:
                details.append("no_keep_segments")
            stats["fallback_reason_details"] = details
        else:
            stats["fallback_reason"] = "no_keep_segments"
        if fallback_note is None:
            fallback_note = "NO_KEEP_SEGMENTS_FALLBACK"
        resolved_duration = audio_duration
        if resolved_duration <= 0 and words:
            resolved_duration = max(0.0, words[-1].end)
        fallback_segment: list[tuple[float, float]] = []
        if resolved_duration > 0:
            fallback_segment = [(0.0, resolved_duration)]
        elif words:
            start_time = max(0.0, words[0].start)
            end_time = max(0.0, words[-1].end)
            if end_time > start_time:
                fallback_segment = [(start_time, end_time)]
        if fallback_segment:
            edl_keep_segments = fallback_segment
            edl_segments_count = len(edl_keep_segments)
            if not keeps:
                fallback_text = next((line.strip() for line in lines if line.strip()), "KEEP_ALL_FALLBACK")
                keeps = [
                    KeepSpan(
                        line_no=0,
                        text=fallback_text or "KEEP_ALL_FALLBACK",
                        start=fallback_segment[0][0],
                        end=fallback_segment[0][1],
                    )
                ]
            keep_duration = sum(max(0.0, end - start) for start, end in edl_keep_segments)
            stats["keep_duration"] = keep_duration
            if audio_duration > 0:
                stats["cut_ratio"] = max(0.0, min(1.0, (audio_duration - keep_duration) / audio_duration))
        else:
            edl_keep_segments = []
            edl_segments_count = 0
        stats.setdefault("fallback_reason", fallback_reason_value)
        LOGGER.warning("EDL 产物保证策略已触发（原因：%s）", stats.get("fallback_reason"))

    stats["fallback_used"] = fallback_used
    stats["fallback_policy_input"] = fallback_policy_input
    if fallback_reasons:
        stats.setdefault("fallback_reason", fallback_reasons[0])
        if len(fallback_reasons) > 1:
            stats.setdefault("fallback_reason_details", fallback_reasons)
    else:
        stats.setdefault("fallback_reason", "")
    stats["timed_out"] = timed_out
    stats["timeout_fallback"] = bool(timed_out and fallback_used)
    stats["match_engine"] = match_engine
    stats["params_snapshot"] = params_snapshot
    if degrade_history:
        stats["degrade_history"] = degrade_history
    total_elapsed = time.monotonic() - start_ts
    stats["latency_ms"] = int(total_elapsed * 1000)
    stats["elapsed_sec"] = total_elapsed
    if degrade_reason:
        stats["degrade_reason"] = degrade_reason
    else:
        stats.setdefault("degrade_reason", "")
    stats["distance_ratio_final"] = current_distance_ratio
    stats["anchor_ngram_final"] = current_anchor_ngram
    stats["unmatched_examples"] = unmatched_examples[:10]
    stats["kept_count"] = len(edl_keep_segments)
    stats["deleted_count"] = len(drops)
    stats["cut_seconds"] = max(0.0, audio_duration - stats.get("keep_duration", 0.0))
    stats["max_window_splits"] = window_splits
    stats["edl_segments_count"] = edl_segments_count
    stats["segment_count"] = edl_segments_count
    edl_fallback_flag = bool(
        edl_segments_count == 0
        or stats.get("fallback_reason") == "no_keep_segments"
        or "no_keep_segments" in stats.get("fallback_reason_details", [])
    )
    stats["edl_fallback"] = edl_fallback_flag

    if unmatched_samples:
        stats["unmatched_samples"] = unmatched_samples

    return RetakeResult(
        keeps=keeps,
        edl_keep_segments=edl_keep_segments,
        drops=drops,
        stats=stats,
        debug_rows=debug_rows,
        match_debug_rows=match_debug_rows_final,
        fallback_used=fallback_used,
        fallback_reason=stats.get("fallback_reason") or None,
        fallback_marker_note=fallback_note,
        audio_duration=audio_duration,
        edl_fallback=edl_fallback_flag,
        edl_fallback_reason=stats.get("fallback_reason") or None,
        edl_segment_metadata=segment_metadata or None,
        unmatched_samples=unmatched_samples,
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
    alias_map: Mapping[str, Sequence[str]] | None = None,
    debug_label: str | None = None,
    no_collapse_align: bool = True,
) -> SentenceReviewResult:
    """执行句子级审阅模式的匹配与统计。"""

    if not words:
        raise ValueError("词序列为空，无法执行句子级审阅逻辑。")
    try:
        raw_text = original_txt.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"未找到原文 TXT: {original_txt}. 请确认路径是否正确。") from exc
    except OSError as exc:
        raise OSError(f"读取原文 TXT 失败: {exc}. 请检查文件权限或是否被占用。") from exc

    align_line_count_read: int | None = None
    if no_collapse_align and _is_align_text(original_txt):
        align_line_count_read = len(raw_text.splitlines())
        LOGGER.info("[align] read lines=%s file=%s", align_line_count_read, original_txt)

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
    if align_line_count_read is not None:
        stats["align_line_count_read"] = align_line_count_read
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


def _collect_boundary_points(
    keep_spans: Sequence[KeepSpan],
    silence_intervals: Sequence[tuple[float, float]],
) -> list[float]:
    """Collect candidate cut points from text spans and silence windows."""

    points: set[float] = set()
    for span in keep_spans:
        points.add(max(0.0, span.start))
        points.add(max(0.0, span.end))
    for start, end in silence_intervals:
        points.add(max(0.0, start))
        points.add(max(0.0, end))
    return sorted(points)


def _split_segments_by_limit(
    segments: Sequence[tuple[float, float]],
    *,
    max_duration: float,
    keep_spans: Sequence[KeepSpan],
    silence_intervals: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Ensure fallback keep segments respect the configured duration limit."""

    if max_duration <= 0:
        return [segment for segment in segments if segment[1] > segment[0]]
    candidates = _collect_boundary_points(keep_spans, silence_intervals)
    min_gap = max(0.4, min(max_duration / 4, 1.5))
    epsilon = 1e-3
    constrained: list[tuple[float, float]] = []
    for start, end in segments:
        if end - start <= max_duration + epsilon:
            constrained.append((start, end))
            continue
        stack = [(start, end)]
        while stack:
            current_start, current_end = stack.pop()
            if current_end - current_start <= max_duration + epsilon:
                constrained.append((current_start, current_end))
                continue
            target = current_start + max_duration
            valid_candidates = [
                point
                for point in candidates
                if (current_start + min_gap) <= point <= min(current_end - min_gap, target)
            ]
            if valid_candidates:
                cut = min(valid_candidates, key=lambda value: abs(target - value))
            else:
                cut = min(current_end, current_start + max_duration)
            if cut <= current_start + epsilon or cut >= current_end - epsilon:
                cut = min(current_end, current_start + max_duration)
            stack.append((cut, current_end))
            stack.append((current_start, cut))
    constrained.sort(key=lambda item: (item[0], item[1]))
    return constrained


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


def _clean_marker_text(text: str, limit: int = 48) -> str:
    sanitized = re.sub(r"\s+", " ", text).strip()
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[: limit - 1] + "…"


def export_audition_markers(
    keeps: list[KeepSpan],
    out_path: Path,
    *,
    note: str | None = None,
) -> Path:
    """导出 Adobe Audition 标记 CSV，确保至少包含 1 行。"""

    rows: list[dict[str, object]] = []
    total_duration = 0.0
    for span in keeps:
        duration = max(0.0, span.end - span.start)
        total_duration = max(total_duration, span.end)
        rows.append(
            {
                "name": f"L{span.line_no}",
                "start": span.start,
                "end": span.end,
                "duration": duration,
                "type": "cue",
                "comment": f"[keep] {_clean_marker_text(span.text)}".strip(),
            }
        )
    if note and rows:
        rows.append(
            {
                "name": "INFO",
                "start": 0.0,
                "end": 0.0,
                "duration": 0.0,
                "type": "cue",
                "comment": note,
            }
        )
    return write_audition_csv(
        out_path,
        rows,
        total_duration=total_duration,
        fallback_description=note,
    )


def export_edl_json(
    edl_keep_segments: list[tuple[float, float]],
    segment_metadata: Sequence[Mapping[str, object]] | None,
    source_audio_abs: str | None,
    out_path: Path,
    *,
    stem: str,
    samplerate: int | None = None,
    channels: int | None = None,
    source_samplerate: int | None = None,
    audio_root: str | None = None,
    prefer_relative_audio: bool = True,
    path_style: str = "auto",
    fallback_reason: str | None = None,
    fallback_used: bool = False,
) -> EDLWriteResult:
    """导出仅包含 keep 动作的 EDL JSON。"""

    segments: list[dict[str, object]] = []
    for idx, (start, end) in enumerate(edl_keep_segments):
        payload: dict[str, object] = {
            "start": max(0.0, start),
            "end": max(0.0, end),
            "action": "keep",
        }
        if segment_metadata and idx < len(segment_metadata):
            extra = dict(segment_metadata[idx])
            if extra:
                payload.setdefault("metadata", {})
                if isinstance(payload["metadata"], dict):
                    payload["metadata"].update(extra)
        if fallback_reason:
            payload.setdefault("metadata", {})
            if isinstance(payload["metadata"], dict):
                payload["metadata"].setdefault("fallback_reason", fallback_reason)
        segments.append(payload)
    stats_payload: dict[str, object] = {
        "segment_count": len(segments),
        "fallback_used": bool(fallback_used),
    }
    if fallback_reason:
        stats_payload["fallback_reason"] = fallback_reason
    return write_edl(
        out_path,
        source_audio=source_audio_abs,
        segments=segments,
        schema_version=1,
        sample_rate=samplerate,
        channels=channels,
        source_samplerate=source_samplerate,
        stats=stats_payload,
        stem=stem,
        audio_root=audio_root,
        prefer_relative_audio=prefer_relative_audio,
        path_style=path_style,
        ensure_non_empty=bool(segments),
    )


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
    rows: list[dict[str, object]] = []
    total_duration = 0.0
    for hit in sorted(hits, key=lambda item: (item.start_time, item.end_time)):
        duration = max(0.0, hit.end_time - hit.start_time)
        total_duration = max(total_duration, hit.end_time)
        rows.append(
            {
                "name": f"L{hit.sent_idx}",
                "start": hit.start_time,
                "end": hit.end_time,
                "duration": duration,
                "type": "cue",
                "comment": f"[keep] {_clean_marker_text(hit.sent_text, 64)}".strip(),
            }
        )
    for point in review_points:
        label = "[review-low]" if point.kind == "low_conf" else "[review]"
        description = f"{label} {_clean_marker_text(point.sent_text, 64)}".strip()
        start_time = point.at_time if point.at_time is not None else point.start_time or 0.0
        duration = max(
            0.0,
            (point.end_time or start_time or 0.0) - (point.start_time or start_time or 0.0),
        )
        total_duration = max(total_duration, (start_time or 0.0) + duration)
        rows.append(
            {
                "name": f"R{point.sent_idx}",
                "start": start_time or 0.0,
                "end": (start_time or 0.0) + duration,
                "duration": duration,
                "type": "cue",
                "comment": description,
            }
        )
    return write_audition_csv(out_path, rows, total_duration=total_duration)


def export_sentence_edl_json(
    keep_segments: Sequence[tuple[float, float]],
    audio_start: float,
    audio_end: float,
    out_path: Path,
    *,
    review_only: bool,
    source_audio_abs: str | None = None,
    samplerate: int | None = None,
    channels: int | None = None,
    stem: str,
    source_samplerate: int | None = None,
    audio_root: str | None = None,
    prefer_relative_audio: bool = True,
    path_style: str = "auto",
) -> EDLWriteResult:
    """根据句子级命中导出专用 EDL JSON。"""

    if review_only:
        segments = [{"start": audio_start, "end": audio_end, "action": "keep"}]
    else:
        segments = [
            {"start": start, "end": end, "action": "keep"}
            for start, end in keep_segments
        ]
    return write_edl(
        out_path,
        source_audio=source_audio_abs,
        segments=segments,
        schema_version=1,
        sample_rate=samplerate,
        channels=channels,
        source_samplerate=source_samplerate,
        stats={"segment_count": len(segments), "review_only": review_only},
        stem=stem,
        audio_root=audio_root,
        prefer_relative_audio=prefer_relative_audio,
        path_style=path_style,
    )


def _format_timestamp(seconds: float) -> str:
    """将秒转换为 SRT 时间戳。"""

    total_milliseconds = int(round(seconds * 1000))  # 将秒转换为毫秒整数
    hours, remainder = divmod(total_milliseconds, 3600_000)  # 拆分出小时
    minutes, remainder = divmod(remainder, 60_000)  # 继续拆分分钟
    secs, millis = divmod(remainder, 1000)  # 最后得到秒和毫秒
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"  # 按 SRT 格式输出
