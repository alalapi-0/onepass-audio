"""静音吸附与段后处理工具。"""
from __future__ import annotations

from typing import Iterable, List, Tuple

from .silence_probe import nearest_silence_boundary

SnapLabel = str


def snap_segment(
    t0: float,
    t1: float,
    silence: Iterable[Tuple[float, float]] | None,
    radius: float,
    min_duration: float,
) -> Tuple[float, float, SnapLabel, bool]:
    """对单个片段进行静音吸附。

    返回 (start, end, snapped_label, too_short)。
    """

    silence_ranges: List[Tuple[float, float]] = list(silence or [])
    start = float(t0 or 0.0)
    end = float(t1 or 0.0)
    snapped = "no-snap"
    if not silence_ranges or radius <= 0:
        snapped = "no-snap"
    else:
        new_start = nearest_silence_boundary(start, silence_ranges, radius)
        new_end = nearest_silence_boundary(end, silence_ranges, radius)
        if new_start is not None and new_start < end:
            start = new_start
        if new_end is not None and new_end > start:
            end = new_end
        if new_start is not None and new_end is not None:
            snapped = "both"
        elif new_start is not None:
            snapped = "start"
        elif new_end is not None:
            snapped = "end"
        else:
            snapped = "no-snap"
    if end < start:
        end = start
    duration = end - start
    too_short = duration < max(0.0, min_duration)
    return start, end, snapped, too_short


__all__ = ["snap_segment", "SnapLabel"]
