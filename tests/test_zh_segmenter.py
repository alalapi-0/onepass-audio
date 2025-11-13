"""Unit tests for the zh_segmenter module."""
from __future__ import annotations

from onepass.zh_segmenter import Segment, segment


def _texts(result: list[Segment]) -> list[str]:
    return [seg.text for seg in result]


def test_punct_mode_basic() -> None:
    text = "你好世界！再会？保重。"
    result = segment(text, split_mode="punct", min_len=1)
    assert _texts(result) == ["你好世界！", "再会？", "保重。"]


def test_all_punct_respects_parentheses() -> None:
    text = "开场白，（括号，不断）然后，结束。"
    result = segment(text, split_mode="all-punct", min_len=1)
    assert _texts(result) == ["开场白，", "（括号，不断）然后，", "结束。"]


def test_punct_len_split_long_sentence() -> None:
    text = (
        "这是一个特别长特别长的句子，包含了多处弱停顿，"
        "需要在长度到达阈值时拆分，否则会影响后续对齐。"
    )
    result = segment(
        text,
        split_mode="punct+len",
        min_len=8,
        max_len=20,
        hard_max=24,
    )
    assert len(result) >= 3
    assert all(seg.length <= 24 for seg in result)


def test_hard_max_break_forces_split() -> None:
    text = "一" * 45
    result = segment(
        text,
        split_mode="punct+len",
        min_len=5,
        max_len=8,
        hard_max=10,
        weak_punct_enable=False,
    )
    assert all(seg.length <= 10 for seg in result)
    assert sum(seg.length for seg in result) == len(text)


def test_merge_short_segments_prefers_previous() -> None:
    text = "短句。也短。后面是一段长度合理的句子不会被合并。"
    result = segment(text, split_mode="punct", min_len=5)
    assert _texts(result)[0] == "短句。也短。"


def test_disable_keep_quotes_allows_inner_split() -> None:
    text = "（括号，内部）外面，结束。"
    result = segment(
        text,
        split_mode="all-punct",
        min_len=1,
        keep_quotes=False,
    )
    assert _texts(result) == ["（括号，", "内部）外面，", "结束。"]


def test_handles_ellipsis_and_dash() -> None:
    text = "第一段……第二段——第三段。"
    result = segment(text, split_mode="all-punct", min_len=1)
    assert _texts(result) == ["第一段……", "第二段——", "第三段。"]


def test_segment_records_offsets() -> None:
    text = "  Hello，世界！"
    result = segment(
        text,
        split_mode="all-punct",
        weak_punct_enable=True,
        min_len=1,
    )
    assert result[0].text == "Hello，"
    assert result[0].start == 2
    assert result[0].end == 8
    assert result[1].text == "世界！"
