"""Unified text normalization and sentence splitting helpers."""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Dict, List, Mapping

from .canonicalize import load_alias_map as _canonical_load_alias_map
from . import _legacy_text_norm as _legacy_norm
from . import _legacy_textnorm as _legacy_textnorm
from . import _legacy_normalize as _legacy_normalize

__all__ = [
    "TextNormConfig",
    "load_normalize_char_map",
    "load_match_alias_map",
    "normalize_text_for_export",
    "split_sentences_with_rules",
]


@dataclass(slots=True)
class TextNormConfig:
    """Configuration used by normalization and sentence splitting."""

    drop_ascii_parens: bool = True
    squash_mixed_english: bool = True
    collapse_lines: bool = True
    max_len: int = 24
    min_len: int = 8
    hard_max: int = 32
    hard_puncts: str = "。！？!?．.;；"
    soft_puncts: str = "，、,:：；;……—"
    attach_side: str = "left"


def load_normalize_char_map(path: str | None) -> Dict[str, object]:
    """Load the character normalization map used by export helpers."""

    candidate = Path(path) if path else None
    if candidate is None:
        candidate = Path(__file__).resolve().parents[1] / "config" / "default_char_map.json"
    return dict(_legacy_norm.load_char_map(candidate))


def load_match_alias_map(path: str | None) -> Dict[str, str]:
    """Load matcher alias configuration (variant -> canonical mapping)."""

    return _canonical_load_alias_map(path)


_SPECIAL_WHITESPACE = {
    "\t": " ",
    "\u00a0": " ",
    "\u200b": "",
    "\u2028": " ",
    "\u2029": " ",
}

_ASCII_PARENS = {"(", ")", "[", "]", "{", "}"}
_CJK_RANGE = "\u3400-\u9FFF\uF900-\uFAFF\u3040-\u30FF"
_RE_CJK_GAP = re.compile(fr"(?<=[{_CJK_RANGE}])\s+(?=[{_CJK_RANGE}])")
_RE_SPACE_RUN = re.compile(r" {2,}")
_RE_ASCII_BLOCK = re.compile(r"([A-Za-z0-9]+)(\s+)(?=[{_CJK_RANGE}])")
_RE_ASCII_BLOCK_LEFT = re.compile(fr"(?<=[{_CJK_RANGE}])\s+([A-Za-z0-9]+)")
_RE_LINE_BREAK = re.compile(r"\s*\n\s*")
_RE_DASH_VARIANTS = re.compile(r"[‒–—―﹘﹣]+")
_RE_ELLIPSIS = re.compile(r"\.{4,}")


def _apply_char_map(text: str, char_map: Mapping[str, object]) -> str:
    normalized = text
    if char_map.get("normalize_width"):
        normalized = _legacy_norm.fullwidth_halfwidth_normalize(
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


def _normalize_whitespace(text: str, collapse_lines: bool) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.translate(str.maketrans(_SPECIAL_WHITESPACE))
    if collapse_lines:
        normalized = _RE_LINE_BREAK.sub(" ", normalized)
    parts = [part.strip() for part in normalized.split("\n")]
    compacted = "\n".join(part for part in parts if part)
    compacted = _RE_SPACE_RUN.sub(" ", compacted)
    compacted = _RE_CJK_GAP.sub("", compacted)
    return compacted.strip()


def _squash_mixed_spacing(text: str) -> str:
    result = _RE_ASCII_BLOCK.sub(lambda m: m.group(1) + " ", text)
    result = _RE_ASCII_BLOCK_LEFT.sub(lambda m: " " + m.group(1), result)
    return result


def _final_punct_normalize(text: str) -> str:
    compacted = _RE_DASH_VARIANTS.sub("-", text)
    compacted = _RE_ELLIPSIS.sub("...", compacted)
    return compacted


def normalize_text_for_export(
    text: str,
    char_map: Mapping[str, object],
    cfg: TextNormConfig,
) -> str:
    """Normalize transcript text for downstream export."""

    if not text:
        return ""
    normalized = _apply_char_map(text, char_map)
    normalized = _normalize_whitespace(normalized, cfg.collapse_lines)
    if cfg.drop_ascii_parens:
        table = {ord(ch): None for ch in _ASCII_PARENS}
        normalized = normalized.translate(table)
    if cfg.squash_mixed_english:
        normalized = _squash_mixed_spacing(normalized)
    normalized = _final_punct_normalize(normalized)
    return normalized.strip()


_HARD_OPENERS = "（〔［【《〈「『“‘(\"'[{"
_HARD_CLOSERS = "）〕］】》〉」』”’)\"'] }"
_HARD_PAIRS = {op: cl for op, cl in zip(_HARD_OPENERS, _HARD_CLOSERS)}
_HARD_REVERSE = {cl: op for op, cl in _HARD_PAIRS.items()}


def _initial_hard_split(text: str, cfg: TextNormConfig) -> List[str]:
    hard_set = set(cfg.hard_puncts)
    stack: List[str] = []
    parts: List[str] = []
    current: List[str] = []
    for ch in text:
        current.append(ch)
        if ch in _HARD_PAIRS:
            stack.append(_HARD_PAIRS[ch])
            continue
        if ch in _HARD_REVERSE:
            if stack and stack[-1] == ch:
                stack.pop()
            continue
        if ch in hard_set and not stack:
            chunk = "".join(current).strip()
            if chunk:
                parts.append(chunk)
            current = []
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _find_split_index(segment: str, cfg: TextNormConfig) -> int | None:
    if len(segment) <= cfg.max_len:
        return None
    soft_set = set(cfg.soft_puncts)
    attach_left = cfg.attach_side != "right"
    target = cfg.max_len
    lower_bound = cfg.min_len
    upper_bound = min(len(segment), cfg.hard_max)
    for idx in range(min(target, upper_bound - 1), lower_bound - 1, -1):
        ch = segment[idx]
        if ch in soft_set or ch.isspace():
            return idx + (1 if attach_left else 0)
    for idx in range(target, upper_bound):
        ch = segment[idx]
        if ch in soft_set or ch.isspace():
            return idx + (1 if attach_left else 0)
    if upper_bound >= lower_bound:
        return upper_bound
    return None


def split_sentences_with_rules(text: str, cfg: TextNormConfig) -> List[str]:
    """Split text into sentences following hard/soft punctuation rules."""

    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _RE_LINE_BREAK.sub(" ", normalized)
    normalized = _RE_SPACE_RUN.sub(" ", normalized).strip()
    if not normalized:
        return []
    queue = _initial_hard_split(normalized, cfg)
    sentences: List[str] = []
    soft_set = set(cfg.soft_puncts)
    attach_left = cfg.attach_side != "right"
    while queue:
        segment = queue.pop(0).strip()
        if not segment:
            continue
        index = _find_split_index(segment, cfg)
        if index is None:
            sentences.append(segment)
            continue
        left = segment[:index].rstrip()
        right = segment[index:].lstrip()
        if left:
            if attach_left and right and right[0] in soft_set:
                left = left + right[0]
                right = right[1:]
            queue.insert(0, right)
            sentences.append(left)
        else:
            queue.insert(0, right)
    return [sent for sent in sentences if sent]


# Re-export legacy helpers for compatibility.
for name in getattr(_legacy_norm, "__all__", ()):  # pragma: no cover - compatibility
    if name in __all__:
        continue
    globals()[name] = getattr(_legacy_norm, name)
    __all__.append(name)

for name in getattr(_legacy_textnorm, "__all__", ()):  # pragma: no cover - compatibility
    if name in __all__:
        continue
    globals()[name] = getattr(_legacy_textnorm, name)
    __all__.append(name)

for name in getattr(_legacy_normalize, "__all__", ()):  # pragma: no cover - compatibility
    if name in __all__:
        continue
    globals()[name] = getattr(_legacy_normalize, name)
    __all__.append(name)
