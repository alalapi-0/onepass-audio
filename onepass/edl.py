"""onepass.edl
用途: 构建剪辑指令列表并序列化为 JSON 结构。
依赖: Python 标准库；内部类型 ``onepass.types``。
示例: ``from onepass.edl import build_edl``。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, List, Sequence

from .types import EDLAction, KeepSpan, Word


def _merge_actions(actions: List[EDLAction]) -> List[EDLAction]:
    if not actions:
        return []
    actions.sort(key=lambda a: (a.start, a.end))
    merged: list[EDLAction] = [actions[0]]
    for action in actions[1:]:
        last = merged[-1]
        if (
            action.type == last.type
            and action.start <= last.end + 1e-3
            and (action.type != "tighten_pause" or action.target_ms == last.target_ms)
        ):
            merged[-1] = replace(last, end=max(last.end, action.end))
        else:
            merged.append(action)
    return merged


def _merge_cut_ranges(ranges: Iterable[tuple[float, float]], keeps: Sequence[KeepSpan]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    keep_windows = [(k.start, k.end) for k in keeps]
    for start, end in sorted(ranges):
        if not merged:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1e-3:
            span_start = min(prev_start, start)
            span_end = max(prev_end, end)
            if any(ks < span_end and ke > span_start for ks, ke in keep_windows):
                merged.append((start, end))
            else:
                merged[-1] = (span_start, span_end)
        else:
            merged.append((start, end))
    return merged


def build_edl(
    words: list[Word], retake_cuts: list[tuple[float, float]], keeps: Sequence[KeepSpan], cfg: dict
) -> list[EDLAction]:
    """根据词时间戳与重录区间构建剪辑动作列表。"""

    merged_cuts = _merge_cut_ranges(retake_cuts, keeps)
    actions: list[EDLAction] = []
    for start, end in merged_cuts:
        actions.append(EDLAction(type="cut", start=start, end=end, reason="retake_earlier"))
    if words:
        long_silence = float(cfg.get("long_silence_s", 0.8))
        target_ms = int(cfg.get("tighten_target_ms", 300))
        prev = words[0]
        for current in words[1:]:
            gap = current.start - prev.end
            if gap >= long_silence:
                actions.append(
                    EDLAction(
                        type="tighten_pause",
                        start=prev.end,
                        end=current.start,
                        reason="long_pause",
                        target_ms=target_ms,
                    )
                )
            prev = current
    return _merge_actions(actions)


def edl_to_json(actions: list[EDLAction], cfg: dict) -> dict:
    """将剪辑动作转换为 JSON 可序列化结构。"""

    payload = {
        "version": 1,
        "xfade_ms": 15,
        "safety_pad_ms": int(float(cfg.get("safety_pad_s", 0.08)) * 1000),
        "actions": [],
    }
    for action in actions:
        entry = {
            "type": action.type,
            "start": action.start,
            "end": action.end,
        }
        if action.reason is not None:
            entry["reason"] = action.reason
        if action.target_ms is not None:
            entry["target_ms"] = action.target_ms
        payload["actions"].append(entry)
    return payload
