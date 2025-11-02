"""静音区间探测工具。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Tuple

__all__ = ["probe_silence_ffmpeg"]


def probe_silence_ffmpeg(audio: Path, noise_db: int = -35, min_d: float = 0.28) -> List[Tuple[float, float]]:
    """调用 ffmpeg 的 ``silencedetect`` 滤镜解析静音区间。"""

    cmd = [
        "ffmpeg",  # 外部命令名称
        "-hide_banner",  # 关闭冗余输出
        "-nostats",  # 禁止进度信息
        "-i",  # 指定输入文件
        str(audio),  # 输入音频路径
        "-af",  # 附加音频滤镜
        f"silencedetect=noise={noise_db}dB:d={min_d}",  # 设置噪声阈值与最小静音时长
        "-f",  # 输出格式设为 null
        "null",
        "-",  # 输出到 /dev/null
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:  # 未安装 ffmpeg 时直接回退
        return []
    if result.returncode != 0:  # 命令失败亦视为无静音
        return []
    silence_start = re.compile(r"silence_start: (?P<start>-?\d+(?:\.\d+)?)")
    silence_end = re.compile(r"silence_end: (?P<end>-?\d+(?:\.\d+)?)")
    ranges: List[Tuple[float, float]] = []
    pending: float | None = None
    for line in result.stderr.splitlines():  # silencedetect 输出位于 stderr
        match_start = silence_start.search(line)
        if match_start:
            try:
                pending = float(match_start.group("start"))
            except ValueError:
                pending = None
            continue
        match_end = silence_end.search(line)
        if match_end and pending is not None:
            try:
                end_val = float(match_end.group("end"))
            except ValueError:
                pending = None
                continue
            if end_val > pending:  # 仅记录有效区间
                ranges.append((pending, end_val))
            pending = None
    return ranges
