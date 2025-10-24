"""onepass.markers
用途: 输出 Adobe Audition 可识别的标记 CSV。
依赖: Python 标准库 os、pathlib；内部 ``onepass.types``。
示例: ``from onepass.markers import write_audition_markers``。
"""
from __future__ import annotations

import os
from pathlib import Path

from .types import EDLAction, ensure_outdir, fmt_time_s


def write_audition_markers(actions: list[EDLAction], out_path: Path) -> None:
    """将剪辑动作写为 Audition 标记 CSV。"""

    ensure_outdir(out_path.parent)
    header = os.getenv("ONEPASS_AU_HEADER", "Name,Start,Duration,Type,Description")
    columns = [col.strip() for col in header.split(",") if col.strip()]
    if not columns:
        columns = ["Name", "Start", "Duration", "Type", "Description"]
    lines = [",".join(columns)]
    for idx, action in enumerate(actions, start=1):
        name_prefix = f"{idx:04d}"
        start = fmt_time_s(action.start)
        duration = fmt_time_s(max(0.0, action.end - action.start))
        if action.type == "cut":
            name = f"{name_prefix}_cutRetake"
            description = "retake_earlier"
        else:
            name = f"{name_prefix}_tighten"
            target = action.target_ms if action.target_ms is not None else 0
            description = f"to {target}ms"
        row = {
            "Name": name,
            "Start": start,
            "Duration": duration,
            "Type": "Cue",
            "Description": description,
        }
        values = [row.get(col, "") for col in columns]
        lines.append(",".join(values))
    out_path.write_text("\n".join(lines) + "\n", "utf-8")
