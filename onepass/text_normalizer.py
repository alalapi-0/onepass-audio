"""Unified text normalization and sentence splitting helpers."""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Dict, List, Mapping

from .debug_utils import is_debug_logging_enabled, log_debug, make_log_limit

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
    "collapse_soft_linebreaks",
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


def _preview_line_for_debug(text: str, limit: int = 80) -> str:
    """Return a single-line preview for verbose logs."""

    if not text:
        return "<empty>"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    compacted = text.replace("\n", " ").strip()
    return compacted[:limit] if compacted else "<empty>"


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
    cfg: TextNormConfig | None = None,
    *,
    preserve_newlines: bool | None = None,
) -> str:
    """Normalize transcript text for downstream export."""

    if not text:
        return ""
    if cfg is None:
        cfg = TextNormConfig()
    collapse_lines = cfg.collapse_lines
    if preserve_newlines is not None:
        collapse_lines = not preserve_newlines

    sample_before: str | None = None
    normalized = _apply_char_map(text, char_map)
    if is_debug_logging_enabled():
        sample_before = _preview_line_for_debug(text)
        log_debug(
            "[normalize] drop_ascii_parens=%s squash_mixed_english=%s collapse_lines=%s order=char_map>whitespace>drop_ascii>mixed-spacing>punct char_map_flags width=%s space=%s delete=%s map=%s",
            bool(cfg.drop_ascii_parens),
            bool(cfg.squash_mixed_english),
            bool(collapse_lines),
            bool(char_map.get("normalize_width")),
            bool(char_map.get("normalize_space")),
            len(char_map.get("delete", [])),
            len(char_map.get("map", {})),
        )
    normalized = _normalize_whitespace(normalized, collapse_lines)
    if cfg.drop_ascii_parens:
        table = {ord(ch): None for ch in _ASCII_PARENS}
        normalized = normalized.translate(table)
    if cfg.squash_mixed_english:
        normalized = _squash_mixed_spacing(normalized)
    normalized = _final_punct_normalize(normalized)
    normalized = normalized.strip()
    if is_debug_logging_enabled():
        log_debug("[normalize] sample.before=%s", sample_before or "<empty>")
        log_debug("[normalize] sample.after=%s", _preview_line_for_debug(normalized))
    return normalized


_HARD_OPENERS = "（〔［【《〈「『“‘(\"'[{"
_HARD_CLOSERS = "）〕］】》〉」』”’)\"'] }"
_HARD_PAIRS = {op: cl for op, cl in zip(_HARD_OPENERS, _HARD_CLOSERS)}
_HARD_REVERSE = {cl: op for op, cl in _HARD_PAIRS.items()}


def _log_split_event(
    event: str,
    state: dict | None,
    *,
    chunk: str,
    marker: str = "",
    reason: str = "",
    attach: str,
    max_len: int,
) -> None:
    if not state:
        return
    log_debug(
        "[split.%s] chunk=%s marker=%s reason=%s attach=%s max_len=%s",
        event,
        chunk,
        marker or "-",
        reason or "-",
        attach,
        max_len,
        limit=state,
    )


def _initial_hard_split(text: str, cfg: TextNormConfig, state: dict | None = None) -> List[str]:
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
                _log_split_event(
                    "hard", state, chunk=_preview_line_for_debug(chunk), marker=ch, reason="stack-clear", attach=cfg.attach_side, max_len=cfg.max_len
                )
                parts.append(chunk)
            current = []
        elif ch in hard_set and stack and state:
            _log_split_event(
                "guard", state, chunk=_preview_line_for_debug("".join(current[-10:])), marker=ch, reason="within-quotes", attach=cfg.attach_side, max_len=cfg.max_len
            )
    tail = "".join(current).strip()
    if tail:
        _log_split_event(
            "tail", state, chunk=_preview_line_for_debug(tail), marker="", reason="flush", attach=cfg.attach_side, max_len=cfg.max_len
        )
        parts.append(tail)
    return parts


def _find_split_index(segment: str, cfg: TextNormConfig, state: dict | None = None) -> int | None:
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
    state: dict | None = None
    if is_debug_logging_enabled():
        state = make_log_limit(400)
    queue = _initial_hard_split(normalized, cfg, state)
    sentences: List[str] = []
    soft_set = set(cfg.soft_puncts)
    attach_left = cfg.attach_side != "right"
    while queue:
        segment = queue.pop(0).strip()
        if not segment:
            continue
        index = _find_split_index(segment, cfg, state)
        if index is None:
            if state and any(ch in cfg.hard_puncts for ch in segment):
                reason = "len<=max" if len(segment) <= cfg.max_len else "hard-guard"
                _log_split_event(
                    "hard-retained",
                    state,
                    chunk=_preview_line_for_debug(segment),
                    marker="",
                    reason=reason,
                    attach=cfg.attach_side,
                    max_len=cfg.max_len,
                )
            sentences.append(segment)
            continue
        left = segment[:index].rstrip()
        right = segment[index:].lstrip()
        if left:
            if attach_left and right and right[0] in soft_set:
                left = left + right[0]
                right = right[1:]
                _log_split_event(
                    "attach-soft",
                    state,
                    chunk=_preview_line_for_debug(left),
                    marker=right[:1],
                    reason="attach-left",
                    attach=cfg.attach_side,
                    max_len=cfg.max_len,
                )
            marker = segment[index - 1] if index > 0 else segment[index : index + 1]
            _log_split_event(
                "soft-split" if marker in soft_set else "len-split",
                state,
                chunk=_preview_line_for_debug(left),
                marker=marker,
                reason="soft" if marker in soft_set else "hard-max",
                attach=cfg.attach_side,
                max_len=cfg.max_len,
            )
            queue.insert(0, right)
            sentences.append(left)
        else:
            queue.insert(0, right)
            _log_split_event(
                "skip-empty",
                state,
                chunk=_preview_line_for_debug(right),
                marker=segment[:1],
                reason="leading-soft",
                attach=cfg.attach_side,
                max_len=cfg.max_len,
            )
    return [sent for sent in sentences if sent]


def collapse_soft_linebreaks(text: str) -> str:
    """Compatibility wrapper exposing the legacy helper under the new module."""

    return _legacy_normalize.collapse_soft_linebreaks(text)


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
