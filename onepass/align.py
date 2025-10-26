"""onepass.align
=================

Sentence-to-word fuzzy alignment with support for keeping only the final
occurrence of each sentence.

Example
-------
>>> from pathlib import Path
>>> from onepass.asr_loader import load_words
>>> from onepass import textnorm
>>> words = load_words(Path('data/asr-json/001.json'))  # doctest: +SKIP
>>> sentences = [textnorm.Sentence(text=textnorm.normalize_sentence(s),
...             tokens=textnorm.tokenize_for_match(textnorm.normalize_sentence(s)))
...             for s in textnorm.split_sentences('示例文本')]  # doctest: +SKIP
>>> result = align_sentences(words, sentences)  # doctest: +SKIP
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional
from rapidfuzz import fuzz

from .asr_loader import Word
from .textnorm import Sentence, normalize_sentence, tokenize_for_match


@dataclass
class MatchWindow:
    """Stores a fuzzy match window in the ASR word timeline."""

    sent_idx: int
    start_idx: int
    end_idx: int
    start: float
    end: float
    score: int


@dataclass
class AlignResult:
    """Alignment output including kept matches, duplicates and misses."""

    kept: Dict[int, Optional[MatchWindow]]
    dups: Dict[int, List[MatchWindow]]
    unaligned: List[int]


def _join_tokens(tokens: Iterable[str]) -> str:
    return "".join(tokens)


def align_sentences(
    words: List[Word],
    sentences: List[Sentence],
    *,
    score_threshold: int = 80,
) -> AlignResult:
    """Align *sentences* against the ordered list of ASR *words*.

    The algorithm scans sliding windows across the ASR word tokens. Each window
    is scored using :func:`rapidfuzz.fuzz.ratio`. All windows scoring above
    ``score_threshold`` (or ``75`` for ultra-short sentences) are collected.
    Among multiple hits for the same sentence, only the window with the latest
    ``end`` timestamp is retained while the preceding windows are marked as
    duplicates.
    """

    kept: Dict[int, Optional[MatchWindow]] = {}
    dups: Dict[int, List[MatchWindow]] = {}
    unaligned: List[int] = []

    if not sentences:
        return AlignResult(kept=kept, dups=dups, unaligned=unaligned)

    word_tokens: List[List[str]] = [
        tokenize_for_match(normalize_sentence(word.text)) for word in words
    ]
    word_token_strings: List[str] = [_join_tokens(toks) for toks in word_tokens]
    token_prefix: List[int] = [0]
    for toks in word_tokens:
        token_prefix.append(token_prefix[-1] + max(len(toks), 1))

    total_words = len(words)

    for sent_idx, sentence in enumerate(sentences):
        target_tokens = sentence.tokens
        if not target_tokens:
            kept[sent_idx] = None
            unaligned.append(sent_idx)
            continue

        base_len = len(target_tokens)
        if base_len <= 6:
            min_tokens = max(1, base_len - 2)
            max_tokens = base_len + 2
            threshold = min(score_threshold, 75)
        else:
            slack = max(1, int(round(base_len * 0.2)))
            min_tokens = max(1, base_len - slack)
            max_tokens = base_len + slack
            threshold = score_threshold

        target_str = _join_tokens(target_tokens)
        matches: List[MatchWindow] = []

        for start_idx in range(total_words):
            for end_idx in range(start_idx, total_words):
                token_count = token_prefix[end_idx + 1] - token_prefix[start_idx]
                if token_count > max_tokens:
                    break
                if token_count < min_tokens:
                    continue
                window_str = "".join(word_token_strings[start_idx : end_idx + 1])
                if not window_str:
                    continue
                score = int(fuzz.ratio(target_str, window_str))
                if score >= threshold:
                    match = MatchWindow(
                        sent_idx=sent_idx,
                        start_idx=start_idx,
                        end_idx=end_idx,
                        start=words[start_idx].start,
                        end=words[end_idx].end,
                        score=score,
                    )
                    matches.append(match)

        if not matches:
            kept[sent_idx] = None
            unaligned.append(sent_idx)
            continue

        matches.sort(key=lambda m: (m.end, m.start))
        kept_match = matches[-1]
        kept[sent_idx] = kept_match
        if len(matches) > 1:
            dups[sent_idx] = matches[:-1]

    return AlignResult(kept=kept, dups=dups, unaligned=unaligned)


__all__ = ["AlignResult", "MatchWindow", "align_sentences"]
