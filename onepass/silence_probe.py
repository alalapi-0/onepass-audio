"""静音区间探测工具。"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

__all__ = ["probe_silence_ffmpeg", "nearest_silence_boundary"]


LOGGER = logging.getLogger("onepass.silence_probe")

_S_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_E_RE = re.compile(r"silence_end:\s*([0-9.]+)")


def _merge_ranges(ranges: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not ranges:
        return []
    ranges.sort()
    merged: List[Tuple[float, float]] = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1e-3:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def probe_silence_ffmpeg(audio: Path, noise_db: float = -35.0, min_d: float = 0.18) -> List[Tuple[float, float]]:
    """调用 ffmpeg 的 ``silencedetect`` 滤镜解析静音区间。

    任何失败都返回空列表，避免影响主流程。
    """

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(audio),
        "-af",
        f"silencedetect=n={noise_db}dB:d={min_d}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        LOGGER.warning("未找到 ffmpeg，无法探测静音。")
        return []
    except OSError as exc:
        LOGGER.warning("调用 ffmpeg 失败，跳过静音探测: %s", exc)
        return []

    stderr = result.stderr or ""
    ranges: List[Tuple[float, float]] = []
    pending: float | None = None
    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match_start = _S_RE.search(line)
        if match_start:
            try:
                pending = float(match_start.group(1))
            except ValueError:
                pending = None
            continue
        match_end = _E_RE.search(line)
        if match_end and pending is not None:
            try:
                end_val = float(match_end.group(1))
            except ValueError:
                pending = None
                continue
            if end_val > pending:
                ranges.append((pending, end_val))
            pending = None

    if not ranges:
        if result.returncode != 0:
            LOGGER.warning(
                "silence_probe: ffmpeg 返回码 %s，未解析到静音区间。",
                result.returncode,
            )
        return []

    merged = _merge_ranges(ranges)
    if result.returncode != 0:
        LOGGER.warning(
            "silence_probe: ffmpeg 返回码 %s，但成功解析到 %s 个静音区间。",
            result.returncode,
            len(merged),
        )
    return merged


def nearest_silence_boundary(
    t: float, ranges: List[Tuple[float, float]], radius: float
) -> Optional[float]:
    """返回距离 ``t`` 最近且在 ``radius`` 内的静音边界。"""

    if not ranges:
        return None
    best: Optional[float] = None
    best_dist: Optional[float] = None
    for start, end in ranges:
        for boundary in (start, end):
            distance = abs(boundary - t)
            if distance <= radius and (best_dist is None or distance < best_dist):
                best = boundary
                best_dist = distance
    return best
