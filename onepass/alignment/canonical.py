"""Canonical text construction helpers used by the alignment pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PUNCTS = set(list("，。、：；？！…—（）“”‘’《》【】[]{}()<>\",.!?;:_-—·~`'|/\\"))
SPACES = {" ", "\t", "\u3000", "\u00A0"}


@dataclass(slots=True)
class CanonicalRules:
    """Normalization options applied before fuzzy alignment.

    Attributes:
        char_map: Mapping from original characters to replacements.
        strip_punct: Remove punctuation characters before alignment.
        strip_spaces: Remove whitespace characters before alignment.
        casefold: Apply :py:meth:`str.casefold` on the mapped character.
        simp_trad: Placeholder for traditional/simplified conversion.
        to_pinyin: Placeholder for phonetic normalization.
    """

    char_map: Dict[str, str] = field(default_factory=dict)
    strip_punct: bool = True
    strip_spaces: bool = True
    casefold: bool = True
    simp_trad: str = "none"
    to_pinyin: bool = False


def apply_char_map(ch: str, rules: CanonicalRules) -> str:
    """Map a character using the configured ``char_map`` if present."""

    return rules.char_map.get(ch, ch)


def _filter_chars(value: str, rules: CanonicalRules) -> Optional[str]:
    """Remove punctuation and spaces from ``value`` based on ``rules``."""

    kept: list[str] = []
    for part in value:
        if rules.strip_spaces and part in SPACES:
            continue
        if rules.strip_punct and part in PUNCTS:
            continue
        kept.append(part)
    if not kept:
        return None
    return "".join(kept)


def normalize_char(ch: str, rules: CanonicalRules) -> Optional[str]:
    """Normalize a single character.

    Returns ``None`` when the character should be discarded. Otherwise the
    returned value may contain one or more characters.
    """

    mapped = apply_char_map(ch, rules)
    if rules.casefold:
        mapped = mapped.casefold()
    filtered = _filter_chars(mapped, rules)
    return filtered


def build_canonical(raw: str, rules: CanonicalRules) -> tuple[str, List[int]]:
    """Create canonical text for ``raw`` using ``rules``.

    Returns a tuple ``(canonical_text, index_map)``. ``index_map`` has the same
    length as ``canonical_text`` and stores the index of the source character
    from ``raw`` for each canonical character.
    """

    can_chars: List[str] = []
    idx_map: List[int] = []
    for raw_idx, ch in enumerate(raw):
        normalized = normalize_char(ch, rules)
        if not normalized:
            continue
        for part in normalized:
            can_chars.append(part)
            idx_map.append(raw_idx)
    return "".join(can_chars), idx_map


def concat_and_index(
    lines: List[str], rules: CanonicalRules
) -> tuple[str, List[int], List[Tuple[int, int]]]:
    """Concatenate sentence lines and build canonical spans.

    Args:
        lines: Sentence lines from the display layer.
        rules: Canonical normalization rules.

    Returns:
        ``canonical_text``: The normalized text without punctuation/spaces.
        ``index_map``: Mapping from canonical indices back to raw indices.
        ``line_spans``: List of ``(start, end)`` pairs for each line.
    """

    if not lines:
        return "", [], []

    raw_all = "".join(lines)
    canonical_text, index_map = build_canonical(raw_all, rules)

    spans: List[Tuple[int, int]] = []
    raw_cursor = 0
    canon_cursor = 0
    total = len(index_map)

    for line in lines:
        raw_start = raw_cursor
        raw_end = raw_start + len(line)
        raw_cursor = raw_end

        while canon_cursor < total and index_map[canon_cursor] < raw_start:
            canon_cursor += 1
        c_start = canon_cursor
        while canon_cursor < total and index_map[canon_cursor] < raw_end:
            canon_cursor += 1
        spans.append((c_start, canon_cursor))

    return canonical_text, index_map, spans
