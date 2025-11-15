from copy import deepcopy

from onepass.text_normalizer import TextNormConfig, normalize_text_for_export


_BASE_CHAR_MAP = {
    "delete": [],
    "map": {},
    "normalize_width": True,
    "normalize_space": True,
    "preserve_cjk_punct": True,
}


def _normalize(payload: str, cfg: TextNormConfig) -> str:
    cmap = deepcopy(_BASE_CHAR_MAP)
    return normalize_text_for_export(payload, cmap, cfg)


def test_preserve_fullwidth_parens_by_default() -> None:
    cfg = TextNormConfig()
    text = "示例（括注内容）依旧保留"
    normalized = _normalize(text, cfg)
    assert "（括注内容）" in normalized


def test_ascii_paren_mapping_removes_fullwidth_when_enabled() -> None:
    cfg = TextNormConfig(preserve_fullwidth_parens=False, ascii_paren_mapping=True)
    normalized = _normalize("原文（括注）", cfg)
    assert "（括注）" not in normalized
    assert "括注" in normalized
    assert "(" not in normalized and ")" not in normalized


def test_custom_map_cannot_force_drop_when_preserve_enabled() -> None:
    custom_cfg = TextNormConfig()
    cmap = deepcopy(_BASE_CHAR_MAP)
    cmap["map"] = {"（": "("}
    normalized = normalize_text_for_export("段落（重要注释）", cmap, custom_cfg)
    assert "（重要注释）" in normalized


def test_preserve_flag_overrides_ascii_mapping_request() -> None:
    cfg = TextNormConfig(preserve_fullwidth_parens=True, ascii_paren_mapping=True)
    normalized = _normalize("保留（全角注）", cfg)
    assert "（全角注）" in normalized
