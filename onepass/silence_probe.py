"""静音区间探测工具。"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

from .utils_subproc import run_cmd

__all__ = ["probe_silence_ffmpeg"]


LOGGER = logging.getLogger("onepass.silence_probe")


def probe_silence_ffmpeg(audio: Path, noise_db: int = -35, min_d: float = 0.28) -> List[Tuple[float, float]]:
    """调用 ffmpeg 的 ``silencedetect`` 滤镜解析静音区间。"""

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "info",
        "-i",
        str(audio),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_d}",
        "-f",
        "null",
        "-",
    ]
    try:
        returncode, _stdout, stderr = run_cmd(cmd, capture=True)
    except FileNotFoundError:
        LOGGER.warning("未找到 ffmpeg，无法探测静音。")
        return []
    if returncode != 0:
        LOGGER.warning(
            "silence_probe: ffmpeg 返回码 %s，回退为空列表。stderr=%s",
            returncode,
            (stderr or "").strip().splitlines()[:3],
        )
        return []
    silence_start = re.compile(r"silence_start:\s*([0-9.]+)")
    silence_end = re.compile(r"silence_end:\s*([0-9.]+)")
    ranges: List[Tuple[float, float]] = []
    pending: float | None = None
    for raw_line in (stderr.splitlines() if stderr else []):
        line = raw_line.strip()
        if not line:
            continue
        match_start = silence_start.search(line)
        if match_start:
            try:
                pending = float(match_start.group(1))
            except ValueError:
                pending = None
            continue
        match_end = silence_end.search(line)
        if match_end and pending is not None:
            try:
                end_val = float(match_end.group(1))
            except ValueError:
                pending = None
                continue
            if end_val > pending:
                ranges.append((pending, end_val))
            pending = None
    return ranges
