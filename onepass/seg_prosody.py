"""Prosody-aware sentence splitter for align.txt generation.

This module scores candidate breakpoints using lexical cues, micro pauses
inferred from ASR word-level timestamps, and punctuation heuristics.  A simple
dynamic-programming search then selects the break sequence with the highest
overall utility while keeping every segment inside a configurable character
window.

The implementation intentionally keeps the public surface compact so that the
CLI can fall back to the legacy ``smart_split`` logic whenever prerequisites
such as ``*.words.json`` files are missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
from typing import Iterable, Sequence

from .asr_loader import Word
from .text_normalizer import DEFAULT_HARD_PUNCT, DEFAULT_SOFT_PUNCT

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BreakCandidate:
    """Represents a potential break position in ``text``."""

    position: int
    score: float = 0.0
    reasons: set[str] = field(default_factory=set)
    pause_ms: float = 0.0
    forced: bool = False
    quote_depth: int = 0
    paren_depth: int = 0


@dataclass(slots=True)
class ProsodyConfig:
    """Configuration bundle for :func:`split_text_with_prosody`."""

    enabled: bool = True
    pause_gap_ms: float = 160.0
    micro_silence_db: int = -35
    soft_join_max: int = 18
    soft_split_min: int = 14
    lex_cues: tuple[str, ...] = ()
    enum_cues: tuple[str, ...] = ()
    quote_protect: bool = True
    paren_protect: bool = True
    seg_len_min: int = 6
    seg_len_max: int = 26
    break_cost_soft: float = -0.6
    break_bonus_hard: float = 1.2
    break_bonus_pause: float = 0.8
    break_bonus_lex: float = 0.5
    break_penalty_quote: float = -1.0
    break_penalty_paren: float = -0.7
    break_penalty_too_short: float = -1.2
    break_penalty_too_long: float = -0.9
    hard_punct: str = DEFAULT_HARD_PUNCT
    soft_punct: str = DEFAULT_SOFT_PUNCT
    punct_attach: str = "left"

    def attach_left(self) -> bool:
        return (self.punct_attach or "left").lower() != "right"


@dataclass(slots=True)
class ProsodySplitResult:
    """Structured response returned by :func:`split_text_with_prosody`."""

    lines: list[str]
    break_positions: list[int]
    break_reasons: list[str]
    candidates: list[BreakCandidate]
    fallback_reason: str | None = None


def _char_set(source: str | Sequence[str] | None, fallback: str) -> set[str]:
    if source is None:
        source = fallback
    chars: set[str] = set()
    if isinstance(source, str):
        chars.update(ch for ch in source if ch and not ch.isspace())
    else:
        for item in source:
            if isinstance(item, str):
                chars.update(ch for ch in item if ch and not ch.isspace())
    if not chars:
        chars.update(ch for ch in fallback if ch and not ch.isspace())
    return chars


def _scan_scopes(text: str) -> tuple[list[int], list[int]]:
    quote_depth = [0] * (len(text) + 1)
    paren_depth = [0] * (len(text) + 1)
    quote_stack: list[str] = []
    paren_stack: list[str] = []
    quote_pairs = {
        "\u201c": "\u201d",
        "\u2018": "\u2019",
        "\u300c": "\u300d",
        "\u300e": "\u300f",
        "\u3010": "\u3011",
        "\u300a": "\u300b",
        '"': '"',
        "'": "'",
    }
    paren_pairs = {
        "(": ")",
        "[": "]",
        "{": "}",
        "\u3010": "\u3011",
        "\u300c": "\u300d",
        "\u3008": "\u3009",
        "\uff08": "\uff09",
        "\uff3b": "\uff3d",
        "\u201c": "\u201d",
    }
    for idx, ch in enumerate(text):
        quote_depth[idx] = len(quote_stack)
        paren_depth[idx] = len(paren_stack)
        if quote_stack and ch == quote_stack[-1]:
            quote_stack.pop()
        elif ch in quote_pairs:
            quote_stack.append(quote_pairs[ch])
        if paren_stack and ch == paren_stack[-1]:
            paren_stack.pop()
        elif ch in paren_pairs:
            paren_stack.append(paren_pairs[ch])
    quote_depth[-1] = len(quote_stack)
    paren_depth[-1] = len(paren_stack)
    return quote_depth, paren_depth


def _normalize_cues(cues: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for cue in cues:
        cue_str = str(cue or "").strip()
        if cue_str:
            normalized.append(cue_str)
    return tuple(dict.fromkeys(normalized))


def _iter_phrase_hits(text: str, cues: Iterable[str]) -> Iterable[int]:
    for cue in cues:
        if not cue:
            continue
        start = 0
        while True:
            hit = text.find(cue, start)
            if hit < 0:
                break
            yield hit + len(cue)
            start = hit + len(cue)


def _map_words(text: str, words: Sequence[Word]) -> list[tuple[int, int] | None]:
    positions: list[tuple[int, int] | None] = []
    if not text or not words:
        return positions
    cursor = 0
    for word in words:
        token = word.text.strip()
        if not token:
            positions.append(None)
            continue
        found = text.find(token, cursor)
        if found < 0:
            found = text.find(token)
        if found < 0:
            positions.append(None)
            continue
        start = found
        end = found + len(token)
        positions.append((start, end))
        cursor = end
    return positions


def _merge_micro_pauses(
    inferred: list[tuple[int, float]],
    supplied: Iterable[tuple[int, float]] | None,
) -> list[tuple[int, float]]:
    merged = list(inferred)
    if supplied:
        for pos, gap in supplied:
            if pos < 0:
                continue
            merged.append((pos, max(0.0, float(gap))))
    merged.sort(key=lambda item: item[0])
    return merged


def score_break_candidates(
    text: str,
    word_times: Sequence[Word] | None,
    micro_pauses: Iterable[tuple[int, float]] | None,
    config: ProsodyConfig,
) -> tuple[list[BreakCandidate], list[tuple[int, int] | None]]:
    """Score all candidate breakpoints and return the lookup tables."""

    text = text or ""
    hard_set = _char_set(config.hard_punct, DEFAULT_HARD_PUNCT)
    soft_set = _char_set(config.soft_punct, DEFAULT_SOFT_PUNCT)
    soft_set.difference_update(hard_set)
    quote_depth, paren_depth = _scan_scopes(text)
    attach_left = config.attach_left()
    n = len(text)
    candidates: dict[int, BreakCandidate] = {}

    def _ensure_candidate(pos: int) -> BreakCandidate | None:
        if pos <= 0 or pos >= n:
            return None
        entry = candidates.get(pos)
        if entry is None:
            entry = BreakCandidate(position=pos)
            entry.quote_depth = quote_depth[pos]
            entry.paren_depth = paren_depth[pos]
            candidates[pos] = entry
        return entry

    for idx, ch in enumerate(text):
        if ch in hard_set:
            pos = idx + 1 if attach_left else idx
            entry = _ensure_candidate(pos)
            if entry:
                entry.reasons.add("hard")
                entry.forced = True
        elif ch in soft_set:
            pos = idx + 1 if attach_left else idx
            entry = _ensure_candidate(pos)
            if entry:
                entry.reasons.add("soft")

    cues = _normalize_cues((*config.lex_cues, *config.enum_cues))
    for hit in _iter_phrase_hits(text, cues):
        entry = _ensure_candidate(hit)
        if entry:
            entry.reasons.add("lex")

    word_spans = _map_words(text, word_times or [])
    inferred_pauses: list[tuple[int, float]] = []
    if word_times:
        for idx in range(len(word_times) - 1):
            left_span = word_spans[idx]
            right_span = word_spans[idx + 1]
            if left_span is None or right_span is None:
                continue
            gap = max(0.0, (word_times[idx + 1].start - word_times[idx].end) * 1000.0)
            if gap >= config.pause_gap_ms:
                inferred_pauses.append((left_span[1], gap))
    for pos, gap in _merge_micro_pauses(inferred_pauses, micro_pauses):
        entry = _ensure_candidate(pos)
        if entry:
            entry.reasons.add("pause")
            entry.pause_ms = max(entry.pause_ms, gap)

    for candidate in candidates.values():
        score = 0.0
        if "hard" in candidate.reasons:
            score += config.break_bonus_hard
        else:
            score += config.break_cost_soft
        if "pause" in candidate.reasons and config.break_bonus_pause:
            ratio = 0.0
            if config.pause_gap_ms > 0:
                ratio = min(candidate.pause_ms / config.pause_gap_ms, 3.0)
            score += config.break_bonus_pause * (0.5 + 0.5 * ratio)
        if "lex" in candidate.reasons:
            score += config.break_bonus_lex
        if candidate.quote_depth and config.quote_protect:
            score += config.break_penalty_quote
        if candidate.paren_depth and config.paren_protect:
            score += config.break_penalty_paren
        candidate.score = score

    ordered = sorted(candidates.values(), key=lambda item: item.position)
    return ordered, word_spans


def _length_score(length: int, config: ProsodyConfig) -> float:
    if length <= 0:
        return config.break_penalty_too_short * 2
    if length < config.seg_len_min:
        ratio = (config.seg_len_min - length) / max(config.seg_len_min, 1)
        return config.break_penalty_too_short * (1.0 + ratio)
    if length > config.seg_len_max:
        ratio = (length - config.seg_len_max) / max(config.seg_len_max, 1)
        return config.break_penalty_too_long * (1.0 + ratio)
    score = 0.0
    if length <= config.soft_join_max:
        score += config.break_penalty_too_short * 0.35
    if length >= config.soft_split_min:
        score += abs(config.break_penalty_too_long) * 0.25
    return score


def _run_dp(
    text: str,
    start: int,
    end: int,
    candidates: list[BreakCandidate],
    config: ProsodyConfig,
) -> list[int]:
    if end - start <= 0:
        return []
    local = [c for c in candidates if start < c.position <= end]
    local_map = {c.position: c for c in local}
    max_span = max(config.seg_len_max, 12)
    cursor = start + max_span
    while cursor < end:
        if cursor not in local_map:
            synth = BreakCandidate(position=cursor, score=config.break_cost_soft * 0.5)
            synth.reasons.add("synthetic")
            local.append(synth)
            local_map[cursor] = synth
        cursor += max_span
    positions = sorted({start, end, *[c.position for c in local]})
    dp_scores: dict[int, tuple[float, int | None]] = {start: (0.0, None)}
    for pos in positions[1:]:
        best_val = -math.inf
        best_prev: int | None = None
        for prev in positions:
            if prev >= pos:
                break
            base = dp_scores.get(prev)
            if base is None:
                continue
            seg_len = pos - prev
            length_bonus = _length_score(seg_len, config)
            candidate = local_map.get(pos)
            candidate_score = candidate.score if candidate else 0.0
            total = base[0] + length_bonus + candidate_score
            if total > best_val:
                best_val = total
                best_prev = prev
        if best_prev is not None:
            dp_scores[pos] = (best_val, best_prev)
    if end not in dp_scores:
        return []
    sequence: list[int] = []
    cursor_pos = end
    while cursor_pos != start:
        sequence.append(cursor_pos)
        _, prev = dp_scores[cursor_pos]
        if prev is None or prev == cursor_pos:
            break
        cursor_pos = prev
    sequence.reverse()
    return sequence


def split_text_with_prosody(
    text: str,
    word_times: Sequence[Word] | None,
    config: ProsodyConfig,
    *,
    micro_pauses: Iterable[tuple[int, float]] | None = None,
) -> ProsodySplitResult:
    """Split text using prosody-aware scoring."""

    text = text or ""
    if not config.enabled or not text:
        return ProsodySplitResult(lines=[text.strip()] if text.strip() else [], break_positions=[], break_reasons=[], candidates=[], fallback_reason="prosody_disabled")
    candidates, _ = score_break_candidates(text, word_times, micro_pauses, config)
    if not candidates:
        return ProsodySplitResult(lines=[], break_positions=[], break_reasons=[], candidates=[], fallback_reason="no_candidates")
    hard_breaks = [c.position for c in candidates if "hard" in c.reasons]
    hard_breaks.extend([0, len(text)])
    hard_breaks = sorted(set(pos for pos in hard_breaks if 0 <= pos <= len(text)))
    all_segments: list[str] = []
    break_positions: list[int] = []
    break_reasons: list[str] = []
    candidate_map = {c.position: c for c in candidates}
    for left, right in zip(hard_breaks, hard_breaks[1:]):
        if right - left <= 0:
            continue
        seq = _run_dp(text, left, right, candidates, config)
        if not seq or seq[-1] != right:
            seq = list(seq)
            seq.append(right)
        chunk_start = left
        for pos in seq:
            segment = text[chunk_start:pos].strip()
            if segment:
                all_segments.append(segment)
                break_positions.append(pos)
                candidate = candidate_map.get(pos)
                if candidate is None:
                    reason = "PROSODY"
                else:
                    reason = "+".join(sorted(candidate.reasons)) or "PROSODY"
                break_reasons.append(reason)
            chunk_start = pos
    return ProsodySplitResult(
        lines=all_segments,
        break_positions=break_positions,
        break_reasons=break_reasons,
        candidates=candidates,
        fallback_reason=None,
    )


__all__ = [
    "BreakCandidate",
    "ProsodyConfig",
    "ProsodySplitResult",
    "score_break_candidates",
    "split_text_with_prosody",
]

