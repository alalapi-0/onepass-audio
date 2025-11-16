import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from onepass._legacy_text_norm import normalize_text


def test_normalize_text_applies_alias_map() -> None:
    alias_map = {
        "法厄同": ["法恶童", "法尔同"],
        "神祇": ["神奇"],
        "信息观": ["信息官"],
    }
    raw = "法恶童 的 神奇 信息官"
    normalized = normalize_text(raw, alias_map=alias_map)
    assert "法厄同" in normalized
    assert "神祇" in normalized
    assert "信息观" in normalized
    assert "法恶童" not in normalized
    assert "神奇" not in normalized
    assert "信息官" not in normalized
