"""Tests covering the new hard whitespace collapse utilities."""
from onepass.text_normalizer import (
    TextNormConfig,
    hard_collapse_whitespace,
    normalize_text_for_export,
)


def _blank_char_map() -> dict:
    return {
        "delete": [],
        "map": {},
        "normalize_width": False,
        "normalize_space": False,
    }


def test_hard_collapse_whitespace_eliminates_tabs_and_fullwidth_space() -> None:
    sample = "第一段。\t第二段 (note)。\n全角空格\u3000第三段。\nABC\n123。"
    collapsed = hard_collapse_whitespace(sample)
    assert collapsed == "第一段。 第二段 (note)。 全角空格 第三段。 ABC 123。"


def test_normalize_text_respects_hard_collapse_flag() -> None:
    sample = "foo\tbar\nqux"
    cfg = TextNormConfig(collapse_lines=False, hard_collapse_lines=True)
    normalized = normalize_text_for_export(sample, _blank_char_map(), cfg)
    assert normalized == "foo bar qux"

    cfg_no_hard = TextNormConfig(collapse_lines=False, hard_collapse_lines=False)
    normalized_preserved = normalize_text_for_export(sample, _blank_char_map(), cfg_no_hard)
    assert "\n" in normalized_preserved
