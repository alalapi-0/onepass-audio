"""句子级审阅模式的切分、匹配与段合并工具。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

try:  # 尝试使用 rapidfuzz 获得更高质量的相似度匹配
    from rapidfuzz import fuzz  # type: ignore

    _HAS_RAPIDFUZZ = True
except Exception:  # rapidfuzz 缺失或加载失败时回退到内置实现
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
    "MIN_SENT_CHARS",
    "MAX_DUP_GAP_SEC",
    "MERGE_ADJ_GAP_SEC",
    "LOW_CONF",
    "to_sentences",
    "normalize_for_match",
    "MatchHit",
    "find_hits_for_sentence",
    "choose_final_hits",
    "KeepSpan",
    "merge_adjacent_spans",
    "ReviewPoint",
    "build_asr_index",
    "align_sentences_from_text",
]

SENT_PUNCTS = "。！？!?…；;"  # 默认的句末标点集合
MIN_SENT_CHARS = 12  # 句子长度不足该阈值时不执行重复去重
MAX_DUP_GAP_SEC = 30.0  # 判定为同一句重录的最大间隔
MERGE_ADJ_GAP_SEC = 1.2  # 相邻命中合并为同一 keep 段的最大间隙
LOW_CONF = 0.65  # 相似度低于该阈值仅做打点，不视为命中


def to_sentences(raw_text: str, puncts: str | None = None) -> list[tuple[int, str]]:
    """先合并硬换行，再按句末标点拆分，返回编号与原句文本。"""

    puncts = puncts or SENT_PUNCTS
    merged = merge_hard_wraps(raw_text)
    cleaned = merged.replace("\r\n", "\n").replace("\r", "\n")
    text = cleaned.replace("\n", " ")
    sentences: list[tuple[int, str]] = []
    if not text.strip():
        return sentences
    pattern = re.compile(rf"(.+?[{re.escape(puncts)}])", re.S)
    index = 1
    last_end = 0
    for match in pattern.finditer(text):
        sent = match.group(1).strip()
        if sent:
            sentences.append((index, sent))
            index += 1
        last_end = match.end()
    tail = text[last_end:].strip()
    if tail:
        sentences.append((index, tail))
    return sentences


def normalize_for_match(text: str) -> str:
    """复用对齐规范化思路，移除空白与大部分标点后返回匹配用文本。"""

    normalized = normalize_for_align(text)
    return "".join(ch for ch in normalized if not ch.isspace())


@dataclass
class MatchHit:
    """单句匹配命中的时间区间及相似度。"""

    sent_idx: int = 0
    sent_text: str = ""
    score: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0


def build_asr_index(words: list[Word]) -> tuple[list[str], str, list[tuple[int, int]]]:
    """对词序列做规范化，返回词文本、拼接串与字符索引映射。"""

    normalized_words = [normalize_for_align(word.text) for word in words]
    asr_norm = cjk_or_latin_seq(normalized_words)
    char_map = build_char_index_map(normalized_words)
    return normalized_words, asr_norm, char_map


def _char_range_to_word_range(char_range: tuple[int, int], char_map: Sequence[tuple[int, int]]) -> tuple[int, int] | None:
    """将字符区间转换为词索引区间。"""

    start_char, end_char = char_range
    if start_char >= end_char:
        return None
    start_idx = None
    end_idx = None
    for idx, (w_start, w_end) in enumerate(char_map):
        if start_idx is None and start_char < w_end:
            start_idx = idx
        if w_start < end_char:
            end_idx = idx
        if start_idx is not None and w_end >= end_char:
            break
    if start_idx is None or end_idx is None:
        return None
    return start_idx, end_idx


def _word_range_to_time(word_range: tuple[int, int], words: Sequence[Word]) -> tuple[float, float]:
    """根据词索引区间获取开始与结束时间。"""

    start_idx, end_idx = word_range
    return words[start_idx].start, words[end_idx].end


def _longest_common_substring(a: str, b: str) -> tuple[int, int, int]:
    """计算最长公共子串长度及在 ``b`` 中的结束索引。"""

    if not a or not b:
        return 0, -1, -1
    prev = [0] * (len(b) + 1)
    best_len = 0
    best_a_end = -1
    best_b_end = -1
    for i, char_a in enumerate(a, start=1):
        current = [0]
        for j, char_b in enumerate(b, start=1):
            if char_a == char_b:
                length = prev[j - 1] + 1
            else:
                length = 0
            current.append(length)
            if length > best_len:
                best_len = length
                best_a_end = i - 1
                best_b_end = j - 1
        prev = current
    return best_len, best_a_end, best_b_end


def find_hits_for_sentence(
    sent_norm: str,
    asr_norm: str,
    word_char_map: Sequence[tuple[int, int]],
    words: Sequence[Word],
) -> list[MatchHit]:
    """对规范化后的句子进行严格匹配，必要时回退到模糊匹配。"""

    hits: list[MatchHit] = []
    if not sent_norm or not asr_norm:
        return hits
    length = len(sent_norm)
    start = 0
    while True:
        idx = asr_norm.find(sent_norm, start)
        if idx == -1:
            break
        char_range = (idx, idx + length)
        word_range = _char_range_to_word_range(char_range, word_char_map)
        if word_range is not None:
            span_start, span_end = _word_range_to_time(word_range, words)
            hits.append(MatchHit(score=1.0, start_time=span_start, end_time=span_end))
        start = idx + 1
    if hits:
        return hits
    best_range: tuple[int, int] | None = None
    best_score = 0.0
    if _HAS_RAPIDFUZZ and fuzz is not None:
        alignment = fuzz.partial_ratio_alignment(sent_norm, asr_norm)
        if alignment and alignment.dest_end > alignment.dest_start:
            best_range = (alignment.dest_start, alignment.dest_end)
            best_score = alignment.score / 100.0
    if best_range is None:
        lcs_len, _, b_end = _longest_common_substring(sent_norm, asr_norm)
        if lcs_len > 0 and b_end >= 0:
            b_start = b_end - lcs_len + 1
            best_range = (b_start, b_end + 1)
            best_score = lcs_len / length if length else 0.0
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
    """按句聚合匹配结果，仅保留可信度足够且靠后的命中。"""

    by_sentence: dict[int, list[MatchHit]] = {}
    for hit in hits:
        if hit.sent_idx not in by_sentence:
            by_sentence[hit.sent_idx] = []
        by_sentence[hit.sent_idx].append(hit)
    final_hits: list[MatchHit] = []
    for sent_idx, sent_hits in by_sentence.items():
        if not sent_hits:
            continue
        eligible = [h for h in sent_hits if h.score >= low_conf]
        if not eligible:
            continue
        eligible.sort(key=lambda item: item.start_time)
        normalized_len = len(normalize_for_match(sent_hits[0].sent_text))
        if normalized_len < max(0, min_sent_chars):
            final_hits.extend(eligible)
            continue
        cluster: list[MatchHit] = [eligible[0]]
        for hit in eligible[1:]:
            prev = cluster[-1]
            gap = hit.start_time - prev.start_time
            if gap <= max_dup_gap_sec:
                cluster.append(hit)
            else:
                final_hits.append(cluster[-1])
                cluster = [hit]
        if cluster:
            final_hits.append(cluster[-1])
    return final_hits


@dataclass
class KeepSpan:
    """多个相邻句子的合并保留段。"""

    start: float
    end: float
    sent_indices: list[int]
    text_preview: str


def merge_adjacent_spans(
    hits: Sequence[MatchHit],
    *,
    merge_gap_sec: float = MERGE_ADJ_GAP_SEC,
) -> list[KeepSpan]:
    """将时间相邻的命中合并，生成用于 EDL 的保留段。"""

    sorted_hits = sorted(hits, key=lambda item: item.start_time)
    if not sorted_hits:
        return []
    spans: list[KeepSpan] = []
    current_group: list[MatchHit] = [sorted_hits[0]]
    for hit in sorted_hits[1:]:
        last = current_group[-1]
        gap = hit.start_time - last.end_time
        if gap <= merge_gap_sec:
            current_group.append(hit)
        else:
            spans.append(_group_to_span(current_group))
            current_group = [hit]
    if current_group:
        spans.append(_group_to_span(current_group))
    return spans


def _group_to_span(group: Sequence[MatchHit]) -> KeepSpan:
    """把命中分组转换为合并段，并生成预览文本。"""

    start = group[0].start_time
    end = group[-1].end_time
    sent_indices = [hit.sent_idx for hit in group]
    preview_source = " ".join(hit.sent_text for hit in group)
    preview = preview_source[:60]
    if len(preview_source) > 60:
        preview = preview.rstrip() + "…"
    return KeepSpan(start=start, end=end, sent_indices=sent_indices, text_preview=preview)


@dataclass
class ReviewPoint:
    """需要人工复核的句子及其定位时间。"""

    sent_idx: int
    sent_text: str
    kind: str
    at_time: float


@dataclass
class SentenceAlignResult:
    """句子级匹配的完整结果。"""

    sentences: list[tuple[int, str]]
    hits: list[MatchHit]
    keep_spans: list[KeepSpan]
    review_points: list[ReviewPoint]
    stats: dict[str, object]


def align_sentences_from_text(
    raw_text: str,
    words: Sequence[Word],
    *,
    puncts: str | None = None,
    min_sent_chars: int = MIN_SENT_CHARS,
    max_dup_gap_sec: float = MAX_DUP_GAP_SEC,
    merge_gap_sec: float = MERGE_ADJ_GAP_SEC,
    low_conf: float = LOW_CONF,
) -> SentenceAlignResult:
    """执行句子切分与匹配，返回可用于导出的结构化结果。"""

    if not words:
        raise ValueError("词级序列为空，无法进行句子级匹配。")
    word_list = list(words)
    sentences = to_sentences(raw_text, puncts)
    _, asr_norm, char_map = build_asr_index(word_list)
    if not asr_norm:
        raise ValueError("规范化后的词串为空，可能全部为标点或空白。")
    all_hits: list[MatchHit] = []
    best_hit_map: dict[int, MatchHit | None] = {}
    strict_hits = 0
    fuzzy_hits = 0
    unmatched = 0
    low_conf_candidates = 0
    for sent_idx, sent_text in sentences:
        sent_norm = normalize_for_match(sent_text)
        if not sent_norm:
            best_hit_map[sent_idx] = None
            unmatched += 1
            continue
        hits = find_hits_for_sentence(sent_norm, asr_norm, char_map, word_list)
        for hit in hits:
            hit.sent_idx = sent_idx
            hit.sent_text = sent_text
        if hits:
            all_hits.extend(hits)
            best_hit = max(hits, key=lambda item: item.score)
            best_hit_map[sent_idx] = best_hit
            if best_hit.score < low_conf:
                low_conf_candidates += 1
            if any(hit.score == 1.0 for hit in hits):
                strict_hits += 1
            else:
                fuzzy_hits += 1
        else:
            best_hit_map[sent_idx] = None
            unmatched += 1
    final_hits = choose_final_hits(all_hits, min_sent_chars=min_sent_chars, max_dup_gap_sec=max_dup_gap_sec, low_conf=low_conf)
    keep_spans = merge_adjacent_spans(final_hits, merge_gap_sec=merge_gap_sec)
    review_points = _build_review_points(sentences, final_hits, best_hit_map, low_conf)
    stats: dict[str, object] = {
        "total_sentences": len(sentences),
        "matched_sentences": len({hit.sent_idx for hit in final_hits}),
        "low_conf_sentences": low_conf_candidates,
        "unmatched_sentences": unmatched,
        "strict_hit_sentences": strict_hits,
        "fuzzy_hit_sentences": fuzzy_hits,
        "keep_span_count": len(keep_spans),
        "longest_keep_span": max((span.end - span.start for span in keep_spans), default=0.0),
    }
    return SentenceAlignResult(
        sentences=sentences,
        hits=final_hits,
        keep_spans=keep_spans,
        review_points=review_points,
        stats=stats,
    )


def _build_review_points(
    sentences: Sequence[tuple[int, str]],
    final_hits: Sequence[MatchHit],
    best_hit_map: dict[int, MatchHit | None],
    low_conf: float,
) -> list[ReviewPoint]:
    """根据最终命中与最佳候选生成审阅提示点。"""

    review_points: list[ReviewPoint] = []
    final_by_sent = {hit.sent_idx: hit for hit in final_hits}
    previous_end = 0.0
    for sent_idx, sent_text in sentences:
        final_hit = final_by_sent.get(sent_idx)
        if final_hit is not None:
            previous_end = final_hit.end_time
            continue
        best_hit = best_hit_map.get(sent_idx)
        if best_hit is None:
            review_points.append(ReviewPoint(sent_idx=sent_idx, sent_text=sent_text, kind="no_match", at_time=previous_end))
            continue
        if best_hit.score < low_conf:
            at_time = best_hit.start_time if best_hit.start_time > 0 else previous_end
            review_points.append(ReviewPoint(sent_idx=sent_idx, sent_text=sent_text, kind="low_conf", at_time=at_time))
    return review_points
