from onepass.text_norm import normalize_chinese_text


def test_drop_ascii_parens_and_squash_trailing_english():
    payload = "新英格兰人（The New Englander）迎来了春天。\n奇点已更为临近（The Singularity Is Nearer）thenew"
    normalized = normalize_chinese_text(
        payload,
        collapse_lines=False,
        drop_ascii_parens=True,
        squash_mixed_english=True,
    )
    assert "The New Englander" not in normalized
    assert "The Singularity" not in normalized
    assert "thenew" not in normalized
    assert "新英格兰人迎来了春天。" in normalized
    assert normalized.endswith("奇点已更为临近")
