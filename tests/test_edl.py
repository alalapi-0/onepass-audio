"""Tests for the unified EDL loader."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onepass.edl import SegmentEDL, load


def _write_edl(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_with_segments(tmp_path: Path) -> None:
    """Files with ``segments`` should be parsed as-is."""

    edl_path = _write_edl(
        tmp_path,
        "demo.keepLast.edl.json",
        {
            "segments": [
                {"start": 0.0, "end": 1.5, "action": "keep", "metadata": {"note": "intro"}},
                {"start": 1.5, "end": 3.0, "action": "cut"},
            ]
        },
    )
    doc = load(edl_path)
    assert isinstance(doc, SegmentEDL)
    assert len(doc.segments) == 2
    assert doc.segments[0].metadata == {"note": "intro"}
    assert doc.segments[1].action == "cut"


def test_load_converts_actions_when_segments_missing(tmp_path: Path) -> None:
    """Legacy ``actions`` arrays should be converted into segments."""

    edl_path = _write_edl(
        tmp_path,
        "legacy.keepLast.edl.json",
        {
            "actions": [
                {"type": "cut", "start": 2, "end": 4, "reason": "dup"},
            ]
        },
    )
    doc = load(edl_path)
    assert len(doc.segments) == 1
    assert doc.segments[0].action == "cut"
    assert doc.segments[0].metadata == {"reason": "dup"}


def test_load_raises_when_no_segments(tmp_path: Path) -> None:
    """A descriptive error is raised when no usable segments exist."""

    edl_path = _write_edl(tmp_path, "broken.edl.json", {"segments": []})
    with pytest.raises(ValueError) as excinfo:
        load(edl_path)
    assert str(edl_path) in str(excinfo.value)
