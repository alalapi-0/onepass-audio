"""Tests for hard punctuation safety nets during splitting."""
from onepass.text_normalizer import (
    ALL_PUNCT,
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
        quote_protect=True,
        paren_protect=True,
        split_mode="punct+len",
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


def test_all_punct_mode_emits_right_attached_segments() -> None:
    text = "你好，世界。试试看：可以吗？好——好的……行、继续。"
    cfg = _build_cfg(split_mode="all-punct")
    lines = split_sentences_with_rules(text, cfg)
    hard_chars = {ch for ch in DEFAULT_HARD_PUNCT if ch and not ch.isspace()}
    assert lines
    for line in lines:
        assert line[-1] in ALL_PUNCT
        assert sum(1 for ch in line if ch in hard_chars) <= 1


def test_all_punct_mode_respects_quote_and_paren_protection() -> None:
    text = "他说：“先做A，再做B。”然后继续。（注：这是备注A、B、C。）"
    cfg = _build_cfg(split_mode="all-punct")
    lines = split_sentences_with_rules(text, cfg)
    assert lines == [
        "他说：",
        "“先做A，再做B。”",
        "然后继续。",
        "（注：这是备注A、B、C。）",
    ]
