"""Utility helpers for working with edit decision lists (EDLs).

This module focuses on loading serialized EDL data and transforming the
"cut" actions into a list of "keep" intervals that can be used when
rendering audio.  The helpers here are designed to be light‑weight and rely
solely on the Python standard library so they can be reused by standalone
scripts such as :mod:`scripts.edl_to_ffmpeg`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def load_edl(path: Path) -> Dict[str, Any]:
    """Load an EDL JSON file and return its dictionary representation.

    Parameters
    ----------
    path:
        Path to the JSON file.  The file is read as UTF-8 text.

    Returns
    -------
    dict
        Parsed JSON data.
    """

    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _merge_intervals(intervals: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Merge overlapping or touching intervals."""

    if not intervals:
        return []

    sorted_intervals = sorted(intervals, key=lambda item: item[0])
    merged: List[Tuple[float, float]] = [sorted_intervals[0]]

    for start, end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def _merge_small_gaps(
    intervals: Iterable[Tuple[float, float]],
    *,
    tolerance: float = 0.005,
) -> List[Tuple[float, float]]:
    """Merge keep intervals whose gaps are smaller than ``tolerance`` seconds."""

    merged: List[Tuple[float, float]] = []
    for start, end in intervals:
        if not merged:
            merged.append((start, end))
            continue

        prev_start, prev_end = merged[-1]
        if start - prev_end < tolerance:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def edl_to_keep_intervals(
    edl: Dict[str, Any],
    *,
    audio_duration: Optional[float] = None,
) -> List[Tuple[float, float]]:
    """Convert an EDL to a list of keep intervals.

    The edit decision list is expected to contain a list of "cut" actions.
    This function computes the complement of those intervals within the
    timeline ``[0, audio_duration]``.  If ``audio_duration`` is not supplied,
    the end of the final action or the ``total_input_sec`` statistic (if
    present) is used as an estimate.

    Parameters
    ----------
    edl:
        Parsed EDL dictionary containing an ``"actions"`` list.
    audio_duration:
        Optional hint about the total duration of the source audio in
        seconds.

    Returns
    -------
    list of tuple(float, float)
        Sorted list of non-overlapping intervals representing audio that
        should be retained.
    """

    actions = edl.get("actions")
    if not isinstance(actions, list):
        raise ValueError("EDL JSON is missing an 'actions' list")

    cut_intervals: List[Tuple[float, float]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("type") != "cut":
            continue

        try:
            start = float(action.get("start", 0.0))
            end = float(action.get("end", start))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError("Invalid start/end in EDL action") from exc

        start = max(0.0, start)
        end = max(0.0, end)
        if end <= start:
            continue
        cut_intervals.append((start, end))

    max_end_candidates: List[float] = []
    if audio_duration is not None and audio_duration > 0:
        max_end_candidates.append(audio_duration)

    stats = edl.get("stats")
    if isinstance(stats, dict):
        total_input = stats.get("total_input_sec")
        if isinstance(total_input, (int, float)) and total_input > 0:
            max_end_candidates.append(float(total_input))

    if cut_intervals:
        max_end_candidates.append(max(end for _, end in cut_intervals))

    if not max_end_candidates:
        raise ValueError("Unable to determine audio duration from EDL or input")

    timeline_end = max(max_end_candidates)
    merged_cuts = _merge_intervals(cut_intervals)

    keep_intervals: List[Tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged_cuts:
        start = min(start, timeline_end)
        end = min(end, timeline_end)
        if cursor < start:
            keep_intervals.append((cursor, start))
        cursor = max(cursor, end)

    if cursor < timeline_end:
        keep_intervals.append((cursor, timeline_end))

    filtered: List[Tuple[float, float]] = [
        (max(0.0, start), max(0.0, end))
        for start, end in keep_intervals
        if end - start > 1e-6
    ]

    return _merge_small_gaps(filtered)


def human_sec(seconds: float) -> str:
    """Return a human-friendly string for ``seconds``."""

    if seconds < 0:
        seconds = 0.0

    total_seconds = int(seconds)
    remainder = seconds - total_seconds

    hours, remainder_seconds = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder_seconds, 60)
    frac = remainder
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}.{int(frac * 1000):03d}s"
    if minutes:
        return f"{minutes:d}:{secs:02d}.{int(frac * 1000):03d}s"
    return f"{seconds:.3f}s"


__all__ = ["edl_to_keep_intervals", "human_sec", "load_edl"]
