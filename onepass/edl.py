"""onepass.edl
===============

Generate "keep last take" edit decision lists from alignment results.

Example
-------
>>> from pathlib import Path
>>> from onepass.asr_loader import load_words
>>> from onepass.align import align_sentences
>>> from onepass import textnorm
>>> words = load_words(Path('data/asr-json/001.json'))  # doctest: +SKIP
>>> sentences = [textnorm.Sentence(text=textnorm.normalize_sentence(s),
...             tokens=textnorm.tokenize_for_match(textnorm.normalize_sentence(s)))
...             for s in textnorm.split_sentences('示例文本')]  # doctest: +SKIP
>>> align = align_sentences(words, sentences)  # doctest: +SKIP
>>> edl = build_keep_last_edl(words, align)  # doctest: +SKIP
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from .align import AlignResult
from .asr_loader import Word


@dataclass
class EDLAction:
    """Represents a cut action inside the EDL timeline."""

    type: str
    start: float
    end: float
    reason: str


@dataclass
class EDL:
    """Container for the generated edit decision list."""

    audio_stem: str
    sample_rate: float | None
    actions: List[EDLAction]
    stats: Dict[str, float | int | None]
    created_at: str


def merge_intervals(
    intervals: List[Tuple[float, float]],
    *,
    join_gap: float = 0.05,
) -> List[Tuple[float, float]]:
    """Merge *intervals* when they touch or overlap.

    ``join_gap`` specifies how close two intervals must be in seconds to be
    merged. A value of ``0.05`` merges gaps shorter than 50 milliseconds, which
    helps prevent fragmented cuts when removing duplicate takes.
    """

    if not intervals:
        return []

    intervals = sorted(intervals, key=lambda pair: pair[0])
    merged: List[Tuple[float, float]] = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + join_gap:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def build_keep_last_edl(words: List[Word], align: AlignResult) -> EDL:
    """Create an EDL that removes all but the final occurrence of each sentence."""

    duplicate_intervals: List[Tuple[float, float]] = []

    for windows in align.dups.values():
        for window in windows:
            duplicate_intervals.append((window.start, window.end))

    merged = merge_intervals(duplicate_intervals)
    actions = [
        EDLAction(type="cut", start=start, end=end, reason="dup_sentence")
        for start, end in merged
    ]

    total_cut = sum(max(0.0, action.end - action.start) for action in actions)
    edl = EDL(
        audio_stem="",
        sample_rate=None,
        actions=actions,
        stats={
            "total_input_sec": None,
            "total_cut_sec": total_cut,
            "num_sentences": len(align.kept),
            "num_unaligned": len(align.unaligned),
        },
        created_at=datetime.now(tz=timezone.utc).isoformat(),
    )
    return edl


__all__ = ["EDL", "EDLAction", "build_keep_last_edl", "merge_intervals"]
