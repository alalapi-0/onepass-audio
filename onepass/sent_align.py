"""句子级审阅模式使用的切分、匹配与审阅工具集。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

try:  # 优先尝试使用 rapidfuzz，以便获得更稳健的模糊匹配得分
    from rapidfuzz import fuzz  # type: ignore

    _HAS_RAPIDFUZZ = True
except Exception:  # 未安装或加载失败时自动回退到内置 LCS
    fuzz = None
    _HAS_RAPIDFUZZ = False

from .asr_loader import Word
from .text_norm import (
    build_char_index_map,
    cjk_or_latin_seq,
    merge_hard_wraps,
    normalize_for_align,
)

__all__ = [
    "SENT_PUNCTS",
    "RIGHT_QUOTES",
    "LEFT_QUOTES",
    "MIN_SENT_CHARS",
    "LOW_CONF",
    "MAX_DUP_GAP_SEC",
    "MERGE_ADJ_GAP_SEC",
    "to_sentences",
    "normalize_for_match",
    "MatchHit",
    "find_hits_for_sentence",
    "choose_final_hits",
    "KeepSpan",
    "merge_adjacent_spans",
    "ReviewPoint",
    "collect_review_points",
    "SentenceAlignResult",
    "build_asr_index",
    "align_sentences_from_text",
]

# 句末标点与配套阈值（更保守的默认配置）
SENT_PUNCTS = "。！？!?…；;"
RIGHT_QUOTES = "”’』」》】）"
LEFT_QUOTES = "“‘『「《【（"
MIN_SENT_CHARS = 12
LOW_CONF = 0.78
MAX_DUP_GAP_SEC = 25.0
MERGE_ADJ_GAP_SEC = 1.0

# 预编译的辅助正则，用于标记不可切分的区域
_URL_PATTERN = re.compile(r"(?i)\b(?:https?|ftp)://[^\s]+")
_EMAIL_PATTERN = re.compile(r"(?i)\b[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_DOMAIN_PATTERN = re.compile(r"(?i)\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
_COMMON_ABBREV = re.compile(
    r"(?i)\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|Fig|Dept|Inc|Ltd|Co|Corp|No|Ps|P\.S|Ph\.D|B\.Sc|M\.Sc|U\.S|U\.K|U\.N)\."
)
_MULTI_ABBREV = re.compile(r"\b(?:[A-Za-z]\.){2,}")


def _mark_span(flags: list[bool], start: int, end: int) -> None:
    """在给定区间内标记“不可作为句末”的字符位置。"""

    start = max(0, start)  # 防止出现负数索引
    end = min(len(flags), end)  # 防止越界
    for pos in range(start, end):  # 逐个位置写入标记
        flags[pos] = True


def _collapse_spaces(text: str) -> str:
    """折叠多余空白，避免切句后出现大量空格。"""

    return re.sub(r"\s+", " ", text).strip()


def to_sentences(raw_text: str) -> list[tuple[int, str]]:
    """按句切分文本，返回带编号的句子列表。"""

    merged = merge_hard_wraps(raw_text)  # 先合并硬换行，减少误切
    normalized = merged.replace("\r\n", "\n").replace("\r", "\n")  # 统一换行符
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)  # 连续空行折叠
    text = normalized.replace("\n", " ")  # 段内换行统一为空格
    text = _collapse_spaces(text)  # 折叠多余空白
    if not text:
        return []

    protected = [False] * len(text)  # 标记不可在句点处切分的位置

    for match in _URL_PATTERN.finditer(text):  # URL 中的句点不能切分
        _mark_span(protected, match.start(), match.end())
    for match in _EMAIL_PATTERN.finditer(text):  # 邮箱地址整体保护
        _mark_span(protected, match.start(), match.end())
    for match in _DOMAIN_PATTERN.finditer(text):  # 普通域名同样保护
        _mark_span(protected, match.start(), match.end())
    for match in _COMMON_ABBREV.finditer(text):  # 常见缩写（含头衔等）
        _mark_span(protected, match.start(), match.end())
    for match in _MULTI_ABBREV.finditer(text):  # 多段缩写（如 U.S.A.）
        _mark_span(protected, match.start(), match.end())

    for idx, ch in enumerate(text):  # 遍历数字小数，保护其中的句点
        if ch == "." and idx > 0 and idx + 1 < len(text):
            left = text[idx - 1]
            right = text[idx + 1]
            if left.isdigit() and right.isdigit():
                protected[idx] = True

    sentences: list[str] = []  # 存放切分后的句子
    length = len(text)
    start_idx = 0  # 当前句子的起始下标
    skip_indices: set[int] = set()  # 需要跳过的 ellipsis 辅助索引
    i = 0
    while i < length:
        ch = text[i]
        if ch == "." and not protected[i]:  # 英文句点的处理
            run = 1
            while i + run < length and text[i + run] == ".":  # 检查连续的点
                run += 1
            if run >= 3:  # 将 "..." 视作省略号
                for extra in range(1, run):
                    skip_indices.add(i + extra)
                end_index = i + run
            else:
                end_index = i + 1
        elif ch == "…":  # 中文省略号的处理
            run = 1
            while i + run < length and text[i + run] == "…":  # 合并连续的省略号
                skip_indices.add(i + run)
                run += 1
            end_index = i + run
        elif ch in SENT_PUNCTS:
            end_index = i + 1
        else:
            i += 1
            continue

        if i in skip_indices:  # 已经被标记为“不可再次终止”的位置
            i += 1
            continue

        while end_index < length and text[end_index] in RIGHT_QUOTES:  # 右引号统一归入句尾
            end_index += 1

        sentence = _collapse_spaces(text[start_idx:end_index])  # 获取句子并折叠空白
        if sentence:
            sentences.append(sentence)

        start_idx = end_index  # 更新下一句的起点
        while start_idx < length and text[start_idx].isspace():  # 跳过句首空白
            start_idx += 1
        i = max(start_idx, end_index)  # 防止索引回退导致死循环

    tail = _collapse_spaces(text[start_idx:])  # 处理尾部剩余文本
    if tail:
        sentences.append(tail)

    return [(index, sent) for index, sent in enumerate(sentences, start=1)]


def normalize_for_match(text: str) -> str:
    """对句子做轻量规范化，返回用于匹配的字符串。"""

    normalized = normalize_for_align(text)  # 复用对齐规范化逻辑
    return "".join(ch for ch in normalized if not ch.isspace())  # 去除所有空白


@dataclass
class MatchHit:
    """句子命中的时间区间与匹配得分。"""

    sent_idx: int = 0
    sent_text: str = ""
    score: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0


def build_asr_index(words: Sequence[Word]) -> tuple[list[str], str, list[tuple[int, int]]]:
    """构建词级索引，返回规范化词串及其字符映射。"""

    normalized_words = [normalize_for_align(word.text) for word in words]  # 逐词规范化
    asr_norm = cjk_or_latin_seq(normalized_words)  # 拼接为连续字符串
    char_map = build_char_index_map(normalized_words)  # 建立词到字符区间的映射
    return normalized_words, asr_norm, char_map


def _char_range_to_word_range(
    char_range: tuple[int, int], word_char_map: Sequence[tuple[int, int]]
) -> tuple[int, int] | None:
    """将字符区间映射到词索引区间。"""

    start_char, end_char = char_range
    if start_char >= end_char:  # 空区间直接忽略
        return None
    start_idx: int | None = None
    end_idx: int | None = None
    for idx, (w_start, w_end) in enumerate(word_char_map):  # 顺序遍历所有词区间
        if start_idx is None and start_char < w_end:  # 找到覆盖起点的词
            start_idx = idx
        if w_start < end_char:  # 记录所有覆盖终点的词
            end_idx = idx
        if w_end >= end_char and start_idx is not None:  # 已覆盖整段即可结束
            break
    if start_idx is None or end_idx is None:
        return None
    return start_idx, end_idx


def _word_range_to_time(word_range: tuple[int, int], words: Sequence[Word]) -> tuple[float, float]:
    """根据词索引区间换算出时间区间。"""

    start_idx, end_idx = word_range
    start_time = words[start_idx].start  # 起点取首个词的开始时间
    end_time = words[end_idx].end  # 终点取最后一个词的结束时间
    return start_time, end_time


def _longest_common_substring(a: str, b: str) -> tuple[int, int, int]:
    """计算最长公共子串长度，并返回在两个字符串中的结束索引。"""

    if not a or not b:  # 任一为空时直接返回默认值
        return 0, -1, -1
    prev = [0] * (len(b) + 1)  # 上一行 DP 结果
    best_len = 0
    best_a_end = -1
    best_b_end = -1
    for i, char_a in enumerate(a, start=1):  # 枚举字符串 a
        current = [0]
        for j, char_b in enumerate(b, start=1):  # 枚举字符串 b
            if char_a == char_b:  # 字符相等时累加长度
                length = prev[j - 1] + 1
            else:
                length = 0
            current.append(length)
            if length > best_len:  # 更新最长记录
                best_len = length
                best_a_end = i - 1
                best_b_end = j - 1
        prev = current  # 为下一轮迭代准备
    return best_len, best_a_end, best_b_end


def find_hits_for_sentence(
    sent_norm: str,
    asr_norm: str,
    word_char_map: Sequence[tuple[int, int]],
    words: Sequence[Word],
) -> list[MatchHit]:
    """在规范化字符串中寻找句子的所有候选命中。"""

    hits: list[MatchHit] = []
    if not sent_norm or not asr_norm:
        return hits

    start = 0
    while True:  # 严格子串匹配，可能出现多次
        idx = asr_norm.find(sent_norm, start)
        if idx == -1:
            break
        char_range = (idx, idx + len(sent_norm))
        word_range = _char_range_to_word_range(char_range, word_char_map)
        if word_range is not None:
            span_start, span_end = _word_range_to_time(word_range, words)
            hits.append(MatchHit(score=1.0, start_time=span_start, end_time=span_end))
        start = idx + 1  # 继续查找后续命中

    if hits:  # 已经找到严格命中则直接返回
        return hits

    best_range: tuple[int, int] | None = None
    best_score = 0.0
    if _HAS_RAPIDFUZZ and fuzz is not None:  # 尝试 rapidfuzz 对齐
        alignment = fuzz.partial_ratio_alignment(sent_norm, asr_norm)
        if alignment and alignment.dest_end > alignment.dest_start:
            best_range = (alignment.dest_start, alignment.dest_end)
            best_score = alignment.score / 100.0

    if best_range is None:  # 回退到最长公共子串
        lcs_len, _, b_end = _longest_common_substring(sent_norm, asr_norm)
        if lcs_len > 0 and b_end >= 0:
            b_start = b_end - lcs_len + 1
            best_range = (b_start, b_end + 1)
            best_score = lcs_len / len(sent_norm)

    if best_range is None:
        return hits

    word_range = _char_range_to_word_range(best_range, word_char_map)
    if word_range is None:
        return hits

    span_start, span_end = _word_range_to_time(word_range, words)
    hits.append(MatchHit(score=float(best_score), start_time=span_start, end_time=span_end))
    return hits


def choose_final_hits(
    hits: Sequence[MatchHit],
    *,
    min_sent_chars: int = MIN_SENT_CHARS,
    max_dup_gap_sec: float = MAX_DUP_GAP_SEC,
    low_conf: float = LOW_CONF,
) -> list[MatchHit]:
    """按句聚合候选命中，仅保留高置信度且最新的一次。"""

    grouped: dict[int, list[MatchHit]] = {}
    for hit in hits:  # 先按句号聚合
        grouped.setdefault(hit.sent_idx, []).append(hit)

    final_hits: list[MatchHit] = []
    for sent_idx, sent_hits in grouped.items():
        if not sent_hits:
            continue
        eligible = [h for h in sent_hits if h.score >= low_conf]  # 过滤低置信候选
        if not eligible:
            continue
        eligible.sort(key=lambda item: item.start_time)  # 按时间排序
        normalized_len = len(normalize_for_match(sent_hits[0].sent_text))
        if normalized_len < max(0, min_sent_chars):  # 过短的句子不做去重
            final_hits.extend(eligible)
            continue
        cluster: list[MatchHit] = [eligible[0]]
        for hit in eligible[1:]:
            gap = hit.start_time - cluster[-1].start_time
            if gap <= max_dup_gap_sec:
                cluster.append(hit)
            else:
                final_hits.append(cluster[-1])  # 保留簇中的最后一次
                cluster = [hit]
        if cluster:
            final_hits.append(cluster[-1])

    final_hits.sort(key=lambda item: item.start_time)
    return final_hits


@dataclass
class KeepSpan:
    """相邻句子命中合并后的 keep 段信息。"""

    start: float
    end: float
    sent_indices: list[int]
    text_preview: str


def merge_adjacent_spans(
    final_hits: Sequence[MatchHit],
    *,
    merge_gap_sec: float = MERGE_ADJ_GAP_SEC,
) -> list[KeepSpan]:
    """根据时间距离将命中句合并为更长的保留段。"""

    if not final_hits:
        return []
    sorted_hits = sorted(final_hits, key=lambda item: item.start_time)
    spans: list[KeepSpan] = []
    group: list[MatchHit] = [sorted_hits[0]]
    for hit in sorted_hits[1:]:  # 依次检视命中序列
        last = group[-1]
        gap = hit.start_time - last.end_time
        if gap <= merge_gap_sec:
            group.append(hit)
        else:
            spans.append(_group_to_span(group))
            group = [hit]
    if group:
        spans.append(_group_to_span(group))
    return spans


def _group_to_span(group: Sequence[MatchHit]) -> KeepSpan:
    """将命中分组转换为 keep 段，并生成预览文本。"""

    start = group[0].start_time
    end = group[-1].end_time
    sent_indices = [hit.sent_idx for hit in group]
    preview_sentences = [hit.sent_text for hit in group[:3]]  # 取前三句做预览
    preview = " / ".join(preview_sentences)
    if len(group) > 3:
        preview += " …"
    if len(preview) > 60:
        preview = preview[:60].rstrip() + "…"
    return KeepSpan(start=start, end=end, sent_indices=sent_indices, text_preview=preview)


@dataclass
class ReviewPoint:
    """需要人工复核的句子及其定位信息。"""

    sent_idx: int
    sent_text: str
    kind: str  # 'no_match' 或 'low_conf'
    at_time: float


def collect_review_points(
    sentences: Sequence[tuple[int, str]],
    final_hits: Sequence[MatchHit],
    best_hits: dict[int, MatchHit | None],
    *,
    low_conf: float,
    audio_start: float = 0.0,
) -> list[ReviewPoint]:
    """汇总未命中或低置信的句子，生成审阅提示点。"""

    review_points: list[ReviewPoint] = []
    final_by_sent = {hit.sent_idx: hit for hit in final_hits}
    previous_end = max(0.0, audio_start)
    for sent_idx, sent_text in sentences:
        final_hit = final_by_sent.get(sent_idx)
        if final_hit is not None:  # 已命中则刷新最近的结束时间
            previous_end = max(previous_end, final_hit.end_time)
            continue
        best_hit = best_hits.get(sent_idx)
        if best_hit is None:  # 完全未匹配
            review_points.append(
                ReviewPoint(sent_idx=sent_idx, sent_text=sent_text, kind="no_match", at_time=previous_end)
            )
            continue
        if best_hit.score < low_conf:  # 低置信命中
            at_time = best_hit.start_time if best_hit.start_time > 0 else previous_end
            review_points.append(
                ReviewPoint(sent_idx=sent_idx, sent_text=sent_text, kind="low_conf", at_time=at_time)
            )
    return review_points


@dataclass
class SentenceAlignResult:
    """句子级匹配的完整结构化结果。"""

    sentences: list[tuple[int, str]]
    hits: list[MatchHit]
    keep_spans: list[KeepSpan]
    review_points: list[ReviewPoint]
    stats: dict[str, object]


def align_sentences_from_text(
    raw_text: str,
    words: Sequence[Word],
    *,
    min_sent_chars: int = MIN_SENT_CHARS,
    max_dup_gap_sec: float = MAX_DUP_GAP_SEC,
    merge_gap_sec: float = MERGE_ADJ_GAP_SEC,
    low_conf: float = LOW_CONF,
) -> SentenceAlignResult:
    """综合切句、匹配与统计信息，产出句子级审阅结果。"""

    if not words:
        raise ValueError("词级序列为空，无法进行句子级匹配。")

    word_list = list(words)
    sentences = to_sentences(raw_text)
    _, asr_norm, char_map = build_asr_index(word_list)
    if not asr_norm:
        raise ValueError("规范化后的词串为空，可能全部为标点或空白。")

    all_hits: list[MatchHit] = []
    best_hits: dict[int, MatchHit | None] = {}
    low_conf_count = 0
    unmatched_count = 0
    strict_hits = 0
    fuzzy_hits = 0

    for sent_idx, sent_text in sentences:
        sent_norm = normalize_for_match(sent_text)
        if not sent_norm:
            best_hits[sent_idx] = None
            unmatched_count += 1
            continue
        hits = find_hits_for_sentence(sent_norm, asr_norm, char_map, word_list)
        for hit in hits:
            hit.sent_idx = sent_idx
            hit.sent_text = sent_text
        if hits:
            all_hits.extend(hits)
            best_hit = max(hits, key=lambda item: item.score)
            best_hits[sent_idx] = best_hit
            if best_hit.score < low_conf:
                low_conf_count += 1
            if best_hit.score == 1.0:
                strict_hits += 1
            else:
                fuzzy_hits += 1
        else:
            best_hits[sent_idx] = None
            unmatched_count += 1

    final_hits = choose_final_hits(
        all_hits,
        min_sent_chars=min_sent_chars,
        max_dup_gap_sec=max_dup_gap_sec,
        low_conf=low_conf,
    )
    keep_spans = merge_adjacent_spans(final_hits, merge_gap_sec=merge_gap_sec)
    review_points = collect_review_points(
        sentences,
        final_hits,
        best_hits,
        low_conf=low_conf,
        audio_start=word_list[0].start,
    )

    stats: dict[str, object] = {
        "total_sentences": len(sentences),
        "matched_sentences": len({hit.sent_idx for hit in final_hits}),
        "low_conf_sentences": low_conf_count,
        "unmatched_sentences": unmatched_count,
        "strict_hit_sentences": strict_hits,
        "fuzzy_hit_sentences": fuzzy_hits,
        "keep_span_count": len(keep_spans),
        "longest_keep_span": max((span.end - span.start for span in keep_spans), default=0.0),
    }

    return SentenceAlignResult(
        sentences=list(sentences),
        hits=final_hits,
        keep_spans=keep_spans,
        review_points=review_points,
        stats=stats,
    )

