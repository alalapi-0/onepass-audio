from pathlib import Path

from onepass._legacy_text_norm import apply_alias_map, load_alias_map


DEF_ALIAS = Path("config/default_alias_map.json")


def test_numeric_and_english_aliases_are_normalized():
    alias_map = load_alias_map(DEF_ALIAS)
    sample = "我们拥有10万与100万的期待，在2024年singularityisnearer 即将到来。"
    normalized = apply_alias_map(sample, alias_map)
    assert "10万" not in normalized
    assert "100万" not in normalized
    assert "2024年" not in normalized
    assert "singularityisnearer" not in normalized.lower()
    assert "十万" in normalized
    assert "一百万" in normalized
    assert "二零二四年" in normalized
    assert "奇点已更为临近" in normalized
