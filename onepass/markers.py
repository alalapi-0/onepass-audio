"""onepass.markers
==================

Adobe Audition marker export helpers derived from generated EDL files.

Example
-------
>>> from onepass.edl import EDL
>>> from onepass.markers import write_audition_markers
>>> edl = EDL(audio_stem='001', sample_rate=None, actions=[], stats={}, created_at='...')
>>> write_audition_markers(edl, Path('out/001.keepLast.audition_markers.csv'))  # doctest: +SKIP
"""
from __future__ import annotations

import csv
from pathlib import Path

from .edl import EDL


def write_audition_markers(edl: EDL, out_csv: Path) -> None:
    """Write Adobe Audition markers for ``edl`` to ``out_csv``."""

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = [["Name", "Start", "Duration", "Type", "Description"]]
    for index, action in enumerate(edl.actions, start=1):
        name_suffix = f"{index:03d}"
        start = f"{action.start:.3f}"
        end = f"{action.end:.3f}"
        duration = max(0.0, action.end - action.start)
        rows.append([f"CUT_{name_suffix}", start, "0", "Marker", "cut duplicate sentence window"])
        rows.append([f"END_{name_suffix}", end, "0", "Marker", "end duplicate sentence window"])
        rows.append([f"CUTSPAN_{name_suffix}", start, f"{duration:.3f}", "Marker", "duplicate sentence span"])
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


__all__ = ["write_audition_markers"]
