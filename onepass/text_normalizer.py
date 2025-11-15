"""Final text normalisation helpers for exported artefacts."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from .canonicalize import load_alias_map as _load_match_alias_map
from .text_norm import fullwidth_halfwidth_normalize, load_char_map

__all__ = [
    "load_normalize_char_map",
    "load_match_alias_map",
    "normalize_text_for_export",
    "normalize_alignment_text",
]

_CJK_SCRIPT_RANGE = "\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF"
_RE_CJK_GAP = re.compile(fr"(?<=[{_CJK_SCRIPT_RANGE}])\s+(?=[{_CJK_SCRIPT_RANGE}])")
_RE_SPACE_RUN = re.compile(r" {2,}")
_RE_ELLIPSIS = re.compile(r"\.{4,}")
_RE_DASH_VARIANTS = re.compile(r"[‒–—―﹘﹣]+")
_SPECIAL_WHITESPACE = {
    "\t": " ",
    "\u00a0": " ",
    "\u200b": "",
    "\u2028": " ",
    "\u2029": " ",
}


@lru_cache(maxsize=4)
def load_normalize_char_map(path: str | Path | None) -> Mapping[str, object]:
    """Load a character map used for final export normalisation."""

    if path is None:
        return load_char_map(Path(__file__).resolve().parents[1] / "config" / "default_char_map.json")
    return load_char_map(Path(path))


@lru_cache(maxsize=4)
def load_match_alias_map(path: str | Path | None) -> dict[str, str]:
    """Load alias mapping for matcher canonicalisation, with small caching."""

    if path is None:
        default_path = Path(__file__).resolve().parents[1] / "config" / "default_alias_map.json"
        return _load_match_alias_map(default_path)
    return _load_match_alias_map(Path(path))


def _apply_char_map(text: str, char_map: Mapping[str, object]) -> str:
    """Apply delete/map rules defined in the char map."""

    normalized = text
    if char_map.get("normalize_width"):
        normalized = fullwidth_halfwidth_normalize(
            normalized,
            preserve_cjk_punct=bool(char_map.get("preserve_cjk_punct", True)),
        )
    delete_chars = {ord(ch): None for ch in char_map.get("delete", [])}
    if delete_chars:
        normalized = normalized.translate(delete_chars)
    mapping = char_map.get("map", {})
    if mapping:
        normalized = normalized.translate(str.maketrans(mapping))
    return normalized


def _preclean(text: str, preserve_newlines: bool) -> str:
    """Remove control whitespace and collapse CR/LF variants."""

    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not preserve_newlines:
        normalized = normalized.replace("\n", " ")
    translation = str.maketrans(_SPECIAL_WHITESPACE)
    normalized = normalized.translate(translation)
    return normalized


def _clean_line(text: str) -> str:
    """Strip redundant spaces and CJK gaps inside a single line."""

    if not text:
        return ""
    compacted = _RE_SPACE_RUN.sub(" ", text.strip())
    compacted = _RE_CJK_GAP.sub("", compacted)
    return compacted.strip()


def _final_punct_normalize(text: str) -> str:
    """Normalise dash and ellipsis variants after char map replacement."""

    if not text:
        return ""
    compacted = _RE_DASH_VARIANTS.sub("-", text)
    compacted = _RE_ELLIPSIS.sub("...", compacted)
    return compacted


def normalize_text_for_export(
    text: str,
    *,
    char_map: Mapping[str, object],
    preserve_newlines: bool,
) -> str:
    """Normalise transcript text for final artefact export."""

    if not text:
        return ""
    normalized = _apply_char_map(text, char_map)
    normalized = _preclean(normalized, preserve_newlines)
    if preserve_newlines:
        parts = [_clean_line(part) for part in normalized.split("\n")]
        normalized = "\n".join(parts)
    else:
        normalized = _clean_line(normalized)
    normalized = _final_punct_normalize(normalized)
    normalized = _RE_SPACE_RUN.sub(" ", normalized)
    return normalized.strip()


def normalize_alignment_text(text: str, *, char_map: Mapping[str, object]) -> str:
    """Normalise alignment payload text (single line, no newlines)."""

    return normalize_text_for_export(text, char_map=char_map, preserve_newlines=False)
