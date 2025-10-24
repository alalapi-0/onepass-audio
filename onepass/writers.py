"""onepass.writers
用途: 输出字幕与纯文本文件。
依赖: Python 标准库 pathlib；内部 ``onepass.types``。
示例: ``from onepass.writers import write_srt``。
"""
from __future__ import annotations

from pathlib import Path

from .types import Segment, ensure_outdir, fmt_time_s


def _fmt_srt_time(seconds: float) -> str:
    return fmt_time_s(seconds).replace(".", ",")


def write_srt(segs: list[Segment], out_path: Path) -> None:
    """以 UTF-8 写出 SRT 文件。"""

    ensure_outdir(out_path.parent)
    lines: list[str] = []
    for idx, seg in enumerate(segs, start=1):
        start = _fmt_srt_time(seg.start)
        end = _fmt_srt_time(seg.end)
        lines.extend([str(idx), f"{start} --> {end}", seg.text, ""])
    out_path.write_text("\n".join(lines).strip() + "\n", "utf-8")


def write_vtt(segs: list[Segment], out_path: Path) -> None:
    """以 UTF-8 写出 VTT 文件。"""

    ensure_outdir(out_path.parent)
    lines = ["WEBVTT", ""]
    for seg in segs:
        start = fmt_time_s(seg.start)
        end = fmt_time_s(seg.end)
        lines.extend([f"{start} --> {end}", seg.text, ""])
    out_path.write_text("\n".join(lines).strip() + "\n", "utf-8")


def write_plain(segs: list[Segment], out_path: Path) -> None:
    """将段落文本逐行输出到文件。"""

    ensure_outdir(out_path.parent)
    text = "\n".join(seg.text for seg in segs)
    out_path.write_text(text + ("\n" if text else ""), "utf-8")
