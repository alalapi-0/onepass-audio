"""Unit tests for the sentence segmentation helper."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onepass.segmentation import split_text


def test_all_punct_mode_splits_on_commas_and_periods() -> None:
    """The ``all-punct`` mode should split on commas and full stops."""

    text = "第一句，包含逗号，第二句。第三句？"
    assert split_text(text, mode="all-punct", min_len=1) == [
        "第一句，",
        "包含逗号，",
        "第二句。",
        "第三句？",
    ]


def test_period_only_ignores_commas() -> None:
    """``period-only`` should only split on sentence-ending punctuation."""

    text = "你好，世界！再会？"
    assert split_text(text, mode="period-only", min_len=1) == ["你好，世界！", "再会？"]


def test_short_segments_merge_with_previous() -> None:
    """Segments shorter than the threshold are merged into the previous one."""

    text = "前面是一段很长的句子。短。"
    assert split_text(text, mode="all-punct") == ["前面是一段很长的句子。短。"]
