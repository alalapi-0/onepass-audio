"""onepass.align
用途: 在 ASR 词序列中查找原文句子的匹配并进行边界微调。
依赖: Python 标准库 logging、math；第三方 ``rapidfuzz``；内部模块 ``onepass.textnorm``。
示例: ``from onepass.align import find_sentence_matches``。
"""
from __future__ import annotations

import logging
from typing import List, Sequence, Tuple

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .textnorm import norm_text, prepare_for_similarity
from .types import Word

logger = logging.getLogger(__name__)

MatchTuple = Tuple[int, int, float, float, float]


def score_similarity(a: str, b: str, cfg: dict) -> float:
    """结合多种评分方式估计 ``a`` 与 ``b`` 的相似度。"""

    norm_a = prepare_for_similarity(a, cfg)
    norm_b = prepare_for_similarity(b, cfg)
    if not norm_a or not norm_b:
        return 0.0
    ts_score = fuzz.token_set_ratio(norm_a, norm_b) / 100.0
    pr_score = fuzz.partial_ratio(norm_a, norm_b) / 100.0
    combined = (ts_score + pr_score) / 2.0
    if combined < 0:
        return 0.0
    if combined > 1:
        return 1.0
    return combined


def _token_lengths(words: Sequence[Word]) -> list[int]:
    lengths: list[int] = []
    for word in words:
        normalized = norm_text(word.text)
        cleaned = normalized.replace(" ", "")
        lengths.append(max(1, len(cleaned)))
    return lengths


def _join_words(words: Sequence[Word], start: int, end: int) -> str:
    return "".join(norm_text(words[idx].text) for idx in range(start, end + 1))


def find_sentence_matches(words: List[Word], sent_norm: str, cfg: dict) -> List[MatchTuple]:
    """在词序列中搜索与句子 ``sent_norm`` 的可能匹配窗口。"""

    if not words or not sent_norm:
        return []
    rho = float(cfg.get("align_window_expand_ratio", 0.35))
    rho = max(0.0, min(rho, 0.9))
    base_len = max(1, len(sent_norm.replace(" ", "")))
    min_chars = max(1, int(round(base_len * (1.0 - rho))))
    max_chars = max(min_chars, int(round(base_len * (1.0 + rho))))
    min_sim = float(cfg.get("align_min_sim", 0.84))

    token_lengths = _token_lengths(words)
    matches: list[MatchTuple] = []
    for i in range(len(words)):
        char_count = 0
        for j in range(i, len(words)):
            char_count += token_lengths[j]
            if char_count < min_chars:
                continue
            if char_count > max_chars:
                break
            window_text = _join_words(words, i, j)
            score = score_similarity(sent_norm, window_text, cfg)
            if score >= min_sim:
                start_s = words[i].start
                end_s = words[j].end
                matches.append((i, j, float(score), start_s, end_s))
    return matches


def _char_offsets(words: Sequence[Word], start: int, end: int) -> list[int]:
    offsets = [0]
    for idx in range(start, end + 1):
        token = norm_text(words[idx].text)
        step = len(token)
        if step <= 0:
            step = 1
        offsets.append(offsets[-1] + step)
    return offsets


def _char_to_local_index(offsets: Sequence[int], char_index: int) -> int:
    if char_index <= 0:
        return 0
    max_idx = len(offsets) - 2
    for idx in range(len(offsets) - 1):
        if char_index < offsets[idx + 1]:
            return min(idx, max_idx)
    return max_idx


def refine_with_dp_if_needed(match: MatchTuple, sent_norm: str, words: List[Word], cfg: dict) -> MatchTuple:
    """必要时对匹配窗口进行动态规划微调。"""

    strategy = str(cfg.get("align_strategy", "fast")).lower()
    if strategy not in {"accurate", "hybrid"}:
        return match
    max_chars = int(cfg.get("align_dp_max_chars", 200))
    if len(sent_norm) > max_chars:
        logger.info(
            "skip dp refine: sentence length %s exceeds limit %s; fallback to fast",
            len(sent_norm),
            max_chars,
        )
        return match

    i, j, score, _, _ = match
    window_text = _join_words(words, i, j)
    if not window_text:
        return match

    offsets = _char_offsets(words, i, j)
    try:
        opcodes = Levenshtein.opcodes(sent_norm, window_text)
    except Exception:  # pragma: no cover - defensive fallback
        return match

    first_equal: int | None = None
    last_equal: int | None = None
    for tag, _src_start, _src_end, dst_start, dst_end in opcodes:
        if tag == "equal":
            if first_equal is None:
                first_equal = dst_start
            last_equal = dst_end
    if first_equal is None or last_equal is None:
        return match

    if first_equal <= 0 and last_equal >= len(window_text):
        return match

    new_local_start = _char_to_local_index(offsets, first_equal)
    new_local_end = _char_to_local_index(offsets, max(first_equal, last_equal - 1))
    new_i = i + new_local_start
    new_j = i + new_local_end
    new_window = _join_words(words, new_i, new_j)
    new_score = score_similarity(sent_norm, new_window, cfg)
    if new_score < score:
        return match
    return (new_i, new_j, new_score, words[new_i].start, words[new_j].end)
