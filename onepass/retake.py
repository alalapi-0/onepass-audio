"""onepass.retake
用途: 在检测重录句子时保留最后一次的朗读。
依赖: Python 标准库与 ``rapidfuzz``；内部引用 ``onepass.types`` 与 ``onepass.textnorm``。
示例: ``from onepass.retake import find_retake_keeps``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from rapidfuzz import fuzz

from .textnorm import norm_text, split_sentences
from .types import KeepSpan, Word


@dataclass
class _SentenceHits:
    index: int
    hits: list[KeepSpan]


def _token_length(token: str) -> int:
    stripped = token.replace(" ", "")
    return max(1, len(stripped))


def _merge_intervals(intervals: Iterable[tuple[float, float]], merge_gap: float, pad: float) -> list[tuple[float, float]]:
    expanded: List[tuple[float, float]] = []
    for start, end in intervals:
        s = max(0.0, start - pad)
        e = max(s, end + pad)
        expanded.append((s, e))
    expanded.sort()
    merged: List[tuple[float, float]] = []
    for start, end in expanded:
        if not merged:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        if start <= prev_end + merge_gap:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _sentence_hits(sentence: str, words: list[Word], cfg: dict) -> list[KeepSpan]:
    normalized = norm_text(sentence)
    target_len = max(1, len(normalized.replace(" ", "")))
    min_chars = max(1, int(target_len * 0.6))
    max_chars = max(min_chars, int(target_len * 1.4))
    threshold = float(cfg.get("retake_sim_threshold", 0.88))
    tokens = [w.text for w in words]
    token_lengths = [_token_length(t) for t in tokens]
    hits: list[KeepSpan] = []
    for i in range(len(words)):
        char_count = 0
        for j in range(i, len(words)):
            char_count += token_lengths[j]
            if char_count < min_chars:
                continue
            if char_count > max_chars:
                break
            window_text = "".join(tokens[i : j + 1])
            score = fuzz.token_set_ratio(normalized, window_text) / 100.0
            if score >= threshold:
                start = words[i].start
                end = words[j].end
                hits.append(KeepSpan(i=i, j=j, score=float(score), start=start, end=end))
    return hits


def find_retake_keeps(
    words: list[Word], original_text: str, cfg: dict
) -> tuple[list[KeepSpan], list[tuple[float, float]]]:
    """寻找需保留的句子窗口以及前次重录的裁剪区间。"""

    sentences = split_sentences(original_text)
    sentence_hits: list[_SentenceHits] = []
    for idx, sentence in enumerate(sentences):
        hits = _sentence_hits(sentence, words, cfg)
        if hits:
            sentence_hits.append(_SentenceHits(index=idx, hits=hits))
    keeps: list[KeepSpan] = []
    to_cut: list[tuple[float, float]] = []
    last_end = 0.0
    for entry in sentence_hits:
        hits = sorted(entry.hits, key=lambda h: (h.end, h.score))
        chosen = None
        for candidate in reversed(hits):
            if candidate.end >= last_end:
                chosen = candidate
                break
        if chosen is None:
            chosen = hits[-1]
        keeps.append(chosen)
        last_end = max(last_end, chosen.end)
        for span in hits:
            if span is chosen:
                continue
            to_cut.append((span.start, span.end))
    merge_gap = float(cfg.get("merge_gap_s", 0.25))
    pad = float(cfg.get("safety_pad_s", 0.08))
    merged_cuts = _merge_intervals(to_cut, merge_gap, pad)
    keeps.sort(key=lambda k: (k.start, k.end))
    return keeps, merged_cuts
