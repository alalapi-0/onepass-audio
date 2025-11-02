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

MIN_SENT_CHARS = 12  # 句子长度低于该阈值不参与去重
MAX_DUP_GAP_SEC = 30.0  # 相邻命中间隔超过该值则认为不是重录
MAX_WINDOW_SEC = 90.0  # 单段 drop 上限
PAD_BEFORE = 0.00  # 预留向前补偿（当前轮保持为 0）
PAD_AFTER = 0.00  # 预留向后补偿（当前轮保持为 0）


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
    audio_floor = words[0].start  # 记录全局起点，后续 padding 需要参考
    audio_ceiling = words[-1].end  # 记录全局终点

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
            padded_start = max(audio_floor, span_start - max(0.0, pad_before))
            padded_end = min(audio_ceiling, span_end + max(0.0, pad_after))
            if padded_end <= padded_start:  # 排除无效区间
                continue
            keeps.append(
                KeepSpan(
                    line_no=index,
                    text=line,
                    start=padded_start,
                    end=padded_end,
                )
            )

    keeps.sort(key=lambda item: item.start)  # 按起始时间排序
    edl_keep_segments = _merge_segments([(span.start, span.end) for span in keeps])  # 合并时间段
    drops = _invert_segments(edl_keep_segments, words[0].start, words[-1].end)  # 计算补集
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
    }

    return RetakeResult(keeps=keeps, edl_keep_segments=edl_keep_segments, drops=drops, stats=stats)  # 返回结果


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

    out_path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    with out_path.open("w", encoding="utf-8", newline="") as csvfile:  # 打开 CSV 文件
        writer = csv.writer(csvfile)  # 创建写入器
        writer.writerow(["Name", "Start", "Duration", "Type", "Description"])  # 写入表头
        for span in keeps:  # 遍历所有保留段
            duration = max(0.0, span.end - span.start)  # 计算持续时间，确保非负
            description = span.text[:24]  # 截取描述预览
            writer.writerow([f"L{span.line_no}", f"{span.start:.3f}", f"{duration:.3f}", "cue", description])  # 写入一行
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


def _format_timestamp(seconds: float) -> str:
    """将秒转换为 SRT 时间戳。"""

    total_milliseconds = int(round(seconds * 1000))  # 将秒转换为毫秒整数
    hours, remainder = divmod(total_milliseconds, 3600_000)  # 拆分出小时
    minutes, remainder = divmod(remainder, 60_000)  # 继续拆分分钟
    secs, millis = divmod(remainder, 1000)  # 最后得到秒和毫秒
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"  # 按 SRT 格式输出
