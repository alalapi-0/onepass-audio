"""保后序列约束工具。"""
from __future__ import annotations

from typing import Iterable, List, Tuple


class MonotonicViolation(RuntimeError):
    """用于标记软模式中的严重异常。"""


def enforce_monotonic(
    segments: Iterable[dict],
    *,
    mode: str = "strict",
    epsilon: float = 0.02,
) -> Tuple[List[dict], List[dict]]:
    """根据模式约束片段序列的时间单调性。"""

    normalized_mode = (mode or "strict").lower()
    if normalized_mode not in {"off", "soft", "strict"}:
        normalized_mode = "strict"
    eps = max(0.0, float(epsilon))
    kept: List[dict] = []
    dropped: List[dict] = []
    last_end = None
    for row in segments:
        start = float(row.get("snap_t0", row.get("start", 0.0)) or 0.0)
        end = float(row.get("snap_t1", row.get("end", 0.0)) or 0.0)
        if end <= start:
            row["kept"] = 0
            row.setdefault("drop_reason", "invalid")
            dropped.append(row)
            continue
        if last_end is None:
            last_end = end
            row["kept"] = 1
            kept.append(row)
            continue
        if end + eps < last_end:
            if normalized_mode == "off":
                row["kept"] = 1
                kept.append(row)
                last_end = max(last_end, end)
                continue
            if normalized_mode == "soft":
                row["pre_take"] = True
                row["kept"] = 1
                kept.append(row)
                last_end = max(last_end, end)
                continue
            row["kept"] = 0
            row["drop_reason"] = row.get("drop_reason") or "pre_take"
            dropped.append(row)
            continue
        if start + eps < (last_end or 0.0):
            if normalized_mode == "soft":
                adjusted = max(last_end or start, start)
                if adjusted >= end:
                    row["kept"] = 0
                    row["drop_reason"] = row.get("drop_reason") or "pre_take"
                    dropped.append(row)
                    continue
                row["snap_t0"] = adjusted
                row["pre_take"] = True
            elif normalized_mode == "strict":
                row["kept"] = 0
                row["drop_reason"] = row.get("drop_reason") or "pre_take"
                dropped.append(row)
                continue
        row["kept"] = 1
        kept.append(row)
        last_end = max(last_end, end)
    return kept, dropped


__all__ = ["enforce_monotonic", "MonotonicViolation"]
