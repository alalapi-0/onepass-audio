"""Tests for hard punctuation safety nets during splitting."""
from onepass.text_normalizer import (
    DEFAULT_HARD_PUNCT,
    DEFAULT_SOFT_PUNCT,
    TextNormConfig,
    split_sentences_with_rules,
)


def _build_cfg(**overrides) -> TextNormConfig:
    params = dict(
        drop_ascii_parens=False,
        squash_mixed_english=False,
        collapse_lines=True,
        hard_collapse_lines=True,
        max_len=64,
        min_len=8,
        hard_max=80,
        hard_puncts=DEFAULT_HARD_PUNCT,
        soft_puncts=DEFAULT_SOFT_PUNCT,
        attach_side="right",
    )
    params.update(overrides)
    return TextNormConfig(**params)


def test_split_handles_tabs_and_newlines_before_soft_layer() -> None:
    text = "第一段。\t第二段 (note)。\n全角空格　第三段。\nABC\n123。"
    cfg = _build_cfg()
    lines = split_sentences_with_rules(text, cfg)
    assert lines == [
        "第一段。",
        "第二段 (note)。",
        "全角空格 第三段。",
        "ABC 123。",
    ]


def test_split_enforces_hard_punct_even_after_short_merge() -> None:
    text = "他顿了顿。于是继续说：事情还没完。别急。"
    cfg = _build_cfg(min_len=32, max_len=48)
    lines = split_sentences_with_rules(text, cfg)
    assert lines == [
        "他顿了顿。",
        "于是继续说：事情还没完。",
        "别急。",
    ]
