"""onepass.clean
用途: 提供口癖识别与过滤功能。
依赖: Python 标准库 ``re``；内部使用 ``onepass.types.Word``。
示例: ``from onepass.clean import remove_fillers``。
"""
from __future__ import annotations

import re

from .types import Word

_BASE_FILLERS_EXTRA = {"like", "you know", "kind of", "sort of", "嗯哼", "欸"}


def _normalize_token(token: str) -> str:
    return re.sub(r"\s+", "", token.lower())


def is_filler(token: str, cfg: dict, strict: bool = False) -> bool:
    """判断给定 token 是否为口癖词。"""

    fillers = {t.lower() for t in cfg.get("filler_terms", [])}
    normalized = _normalize_token(token)
    if normalized in fillers:
        return True
    if strict:
        extended = fillers | {_normalize_token(t) for t in _BASE_FILLERS_EXTRA}
        return normalized in extended
    return False


def remove_fillers(words: list[Word], cfg: dict, strict: bool = False) -> list[Word]:
    """过滤掉被视作口癖的词并返回新的词列表。"""

    filtered: list[Word] = []
    fillers = cfg.get("filler_terms", [])
    filler_set = {t.lower() for t in fillers}
    extra_set = filler_set | {_normalize_token(t) for t in _BASE_FILLERS_EXTRA}
    for word in words:
        token_norm = _normalize_token(word.text)
        if strict:
            match = token_norm in extra_set
        else:
            match = token_norm in filler_set
        if match:
            continue
        filtered.append(word)
    return filtered
