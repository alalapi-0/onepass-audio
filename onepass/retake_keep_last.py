"""保留最后一遍的核心策略与导出工具。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import csv
import json

from .asr_loader import Word
from .text_norm import build_char_index_map, cjk_or_latin_seq, normalize_for_align

__all__ = [
    "KeepSpan",
    "RetakeResult",
    "compute_retake_keep_last",
    "export_srt",
    "export_txt",
    "export_audition_markers",
    "export_edl_json",
]


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


def _normalize_words(words: list[Word]) -> tuple[list[str], str, list[tuple[int, int]]]:
    """返回规范化后的词文本、拼接字符串与字符索引映射。"""

    # 对每个词进行规范化，去除噪声并统一大小写
    normalized_words = [normalize_for_align(word.text) for word in words]
    # 将规范化后的词拼接成用于匹配的整体字符串
    asr_norm_str = cjk_or_latin_seq(normalized_words)
    # 构建词到字符范围的映射，便于后续定位时间
    char_map = build_char_index_map(normalized_words)
    return normalized_words, asr_norm_str, char_map


def _line_to_units(line: str) -> str:
    """将原文行转换成匹配用的字符序列。"""

    # 利用与词序列相同的规则去掉空白，保持匹配一致性
    return cjk_or_latin_seq([line])


def _find_all_occurrences(haystack: str, needle: str) -> list[tuple[int, int]]:
    """找到 needle 在 haystack 中的所有出现位置。"""

    results: list[tuple[int, int]] = []
    if not needle:
        return results
    start = 0
    while True:
        # 从当前位置开始搜索匹配
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        end = idx + len(needle)
        results.append((idx, end))
        # 继续往后寻找下一个命中
        start = idx + 1
    return results


def _longest_common_substring(a: str, b: str) -> tuple[int, int, int]:
    """返回 a 与 b 的最长公共子串长度及其结尾索引。"""

    if not a or not b:
        return 0, -1, -1
    # 使用经典的 O(n*m) 动态规划算法，每一行复用上一次的结果
    prev = [0] * (len(b) + 1)
    best_len = 0
    best_a_end = -1
    best_b_end = -1
    for i, char_a in enumerate(a, start=1):
        current = [0]
        for j, char_b in enumerate(b, start=1):
            if char_a == char_b:
                # 当字符相等时延长公共子串长度
                length = prev[j - 1] + 1
            else:
                # 不匹配时重置长度
                length = 0
            current.append(length)
            if length > best_len:
                # 记录当前找到的更长子串及其位置
                best_len = length
                best_a_end = i - 1
                best_b_end = j - 1
        # 将当前行结果保存为下一次循环的上一行
        prev = current
    return best_len, best_a_end, best_b_end


def _char_range_to_word_range(char_range: tuple[int, int], char_map: list[tuple[int, int]]) -> tuple[int, int] | None:
    """将字符区间映射为词索引区间。"""

    start_char, end_char = char_range
    if start_char == end_char:
        return None
    start_idx = None
    end_idx = None
    for idx, (w_start, w_end) in enumerate(char_map):
        if start_idx is None and start_char < w_end:
            start_idx = idx
        if w_start < end_char:
            end_idx = idx
        if w_end >= end_char and start_idx is not None:
            break
    if start_idx is None or end_idx is None:
        return None
    return start_idx, end_idx


def _word_range_to_time(word_range: tuple[int, int], words: list[Word]) -> tuple[float, float]:
    """根据词索引区间得到时间区间。"""

    start_idx, end_idx = word_range
    start_time = words[start_idx].start
    end_time = words[end_idx].end
    return start_time, end_time


def compute_retake_keep_last(words: list[Word], original_txt: Path) -> RetakeResult:
    """根据原文 TXT 匹配词序列，仅保留最后一次出现的行。"""

    if not words:
        raise ValueError("词序列为空，无法执行保留最后一遍逻辑。请先导入有效的 ASR JSON。")
    try:
        raw_text = original_txt.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"未找到原文 TXT: {original_txt}. 请确认路径是否正确。") from exc
    except OSError as exc:
        raise OSError(f"读取原文 TXT 失败: {exc}. 请检查文件权限或关闭占用程序。") from exc

    lines = raw_text.splitlines()
    # 预先获取规范化后的词文本、拼接字符串与字符映射
    _, asr_norm_str, char_map = _normalize_words(words)
    if not asr_norm_str:
        raise ValueError("规范化后的词序列为空，可能所有词都是标点或空白。请检查 JSON 输出。")

    keeps: list[KeepSpan] = []
    strict_matches = 0
    fallback_matches = 0
    unmatched = 0

    for index, line in enumerate(lines, start=1):
        norm_line = normalize_for_align(line)
        # 将当前行转换为匹配用字符序列
        units = _line_to_units(norm_line)
        if not units:
            continue
        occurrences = _find_all_occurrences(asr_norm_str, units)
        spans: list[tuple[float, float]] = []
        if occurrences:
            strict_matches += 1
            for occ in occurrences:
                # 将命中的字符区间映射回词索引区间
                word_range = _char_range_to_word_range(occ, char_map)
                if word_range is None:
                    continue
                # 再由词索引换算到时间区间
                spans.append(_word_range_to_time(word_range, words))
        else:
            # 未命中时回退到最长公共子串匹配
            length, _, asr_end = _longest_common_substring(units, asr_norm_str)
            if length >= 0.8 * len(units) and asr_end != -1:
                fallback_matches += 1
                asr_start = asr_end - length + 1
                word_range = _char_range_to_word_range((asr_start, asr_end + 1), char_map)
                if word_range is not None:
                    spans.append(_word_range_to_time(word_range, words))
        if not spans:
            unmatched += 1
            continue
        # 同一行如出现多次，仅保留时间轴上最后一次
        last_span = max(spans, key=lambda item: item[0])
        keeps.append(KeepSpan(line_no=index, text=line, start=last_span[0], end=last_span[1]))

    keeps.sort(key=lambda item: item.start)
    edl_keep_segments = _merge_segments([(span.start, span.end) for span in keeps])
    drops = _invert_segments(edl_keep_segments, words[0].start, words[-1].end)

    stats = {
        "total_words": len(words),
        "total_lines": len(lines),
        "matched_lines": len(keeps),
        "strict_matches": strict_matches,
        "fallback_matches": fallback_matches,
        "unmatched_lines": unmatched,
    }

    return RetakeResult(keeps=keeps, edl_keep_segments=edl_keep_segments, drops=drops, stats=stats)


def _merge_segments(segments: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """合并重叠或相邻的时间片段。"""

    # 丢弃无效区间并按起点排序
    sorted_segments = sorted((s for s in segments if s[1] > s[0]), key=lambda item: item[0])
    if not sorted_segments:
        return []
    merged: list[tuple[float, float]] = [sorted_segments[0]]
    for start, end in sorted_segments[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            # 重叠或相邻时取并集
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _invert_segments(segments: list[tuple[float, float]], start: float, end: float) -> list[tuple[float, float]]:
    """根据 keep 段求补集，即需要丢弃的时间段。"""

    drops: list[tuple[float, float]] = []
    cursor = start
    for seg_start, seg_end in segments:
        if seg_start > cursor:
            # 当前 keep 之前存在未覆盖区间则视为 drop
            drops.append((cursor, seg_start))
        cursor = max(cursor, seg_end)
    if cursor < end:
        drops.append((cursor, end))
    return drops


def export_srt(keeps: list[KeepSpan], out_path: Path) -> Path:
    """将保留行导出为 SRT 字幕文件。"""

    lines = []
    for index, span in enumerate(keeps, start=1):
        # 序号从 1 开始递增
        lines.append(f"{index}")
        # 生成形如 00:00:01,000 --> 00:00:02,000 的时间轴
        lines.append(f"{_format_timestamp(span.start)} --> {_format_timestamp(span.end)}")
        # 保留原文行文本
        lines.append(span.text)
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def export_txt(keeps: list[KeepSpan], out_path: Path) -> Path:
    """导出保留行的纯文本稿。"""

    # 直接逐行拼接原文，保留换行
    content = "\n".join(span.text for span in keeps)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def export_audition_markers(keeps: list[KeepSpan], out_path: Path) -> Path:
    """导出 Adobe Audition 标记 CSV。"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Name", "Start", "Duration", "Type", "Description"])
        for span in keeps:
            duration = max(0.0, span.end - span.start)
            description = span.text[:24]
            # Name 使用行号，Description 保留前 24 字符
            writer.writerow([f"L{span.line_no}", f"{span.start:.3f}", f"{duration:.3f}", "cue", description])
    return out_path


def export_edl_json(
    edl_keep_segments: list[tuple[float, float]],
    source_audio_rel: str | None,
    out_path: Path,
    samplerate: int | None = None,
    channels: int | None = None,
) -> Path:
    """导出仅包含 keep 动作的 EDL JSON。"""

    segments = [
        {"start": start, "end": end, "action": "keep"}
        for start, end in edl_keep_segments
    ]
    payload = {
        "source_audio": source_audio_rel,
        "samplerate": samplerate,
        "channels": channels,
        "segments": segments,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _format_timestamp(seconds: float) -> str:
    """将秒转换为 SRT 时间戳。"""

    total_milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
