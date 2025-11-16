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

DEFAULT_HARD_PUNCT = "。！？!?．.;；……—"
DEFAULT_SOFT_PUNCT = "，、,:：；;"
ALL_PUNCT: tuple[str, ...] = tuple(
    sorted({ch for ch in DEFAULT_HARD_PUNCT + DEFAULT_SOFT_PUNCT if ch and not ch.isspace()})
)
_ALL_PUNCT_RE = re.compile("([{}])".format(re.escape("".join(ALL_PUNCT))))
_WS_RE = re.compile(r"[ \t\r\f\v\u00A0\u2000-\u200D\u3000\uFEFF]+")

__all__ = [
    "TextNormConfig",
    "DEFAULT_HARD_PUNCT",
    "DEFAULT_SOFT_PUNCT",
    "ALL_PUNCT",
    "load_normalize_char_map",
    "load_match_alias_map",
    "normalize_text_for_export",
    "split_sentences_with_rules",
    "collapse_soft_linebreaks",
    "hard_collapse_whitespace",
]


@dataclass(slots=True)
class TextNormConfig:
    """Configuration used by normalization and sentence splitting."""

    drop_ascii_parens: bool = True
    preserve_fullwidth_parens: bool = True
    ascii_paren_mapping: bool = False
    squash_mixed_english: bool = False
    collapse_lines: bool = True
    hard_collapse_lines: bool = True
    max_len: int = 24
    min_len: int = 8
    hard_max: int = 32
    hard_puncts: str = DEFAULT_HARD_PUNCT
    soft_puncts: str = DEFAULT_SOFT_PUNCT
    attach_side: str = "left"
    quote_protect: bool = True
    paren_protect: bool = True
    split_mode: str = "punct+len"


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

_NBSP_RE = re.compile(r"[\u00A0\u1680\u180E\u2000-\u200B\u202F\u205F\u3000]")
_SOFT_BREAK_RE = re.compile(r"[ \t\f\v]*\r?\n[ \t\f\v]*")
_ASCII_SPACE_RUN = re.compile(r"([ -~])\s{2,}([ -~])")
_SOFT_STRIP = " \t\f\v\r\n"

_ASCII_PARENS = {"(", ")", "[", "]", "{", "}"}
_FULLWIDTH_PARENS = {"（", "）"}
_FULLWIDTH_PAREN_TO_ASCII = str.maketrans({"（": "(", "）": ")"})
_CJK_RANGE = "\u3400-\u9FFF\uF900-\uFAFF\u3040-\u30FF"
_RE_ASCII_BLOCK = re.compile(rf"([A-Za-z0-9]+)(\s+)(?=[{_CJK_RANGE}])")
_RE_ASCII_BLOCK_LEFT = re.compile(fr"(?<=[{_CJK_RANGE}])\s+([A-Za-z0-9]+)")
_RE_CJK_GAP = re.compile(fr"(?<=[{_CJK_RANGE}])\s+(?=[{_CJK_RANGE}])")
_RE_SPACE_RUN = re.compile(r" {2,}")
_RE_LINE_BREAK = re.compile(r"\s*\n\s*")
_RE_DASH_VARIANTS = re.compile(r"[‒–—―﹘﹣]+")
_RE_ELLIPSIS = re.compile(r"\.{4,}")
HARD_PUNCT = set(DEFAULT_HARD_PUNCT)


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


def _apply_char_map(text: str, char_map: Mapping[str, object], cfg: TextNormConfig) -> str:
    normalized = text
    if char_map.get("normalize_width"):
        normalized = _legacy_norm.fullwidth_halfwidth_normalize(
            normalized,
            preserve_cjk_punct=bool(char_map.get("preserve_cjk_punct", True)),
        )
    delete_chars = {ord(ch): None for ch in char_map.get("delete", [])}
    if delete_chars:
        normalized = normalized.translate(delete_chars)
    mapping = dict(char_map.get("map", {}))
    if cfg.preserve_fullwidth_parens:
        for paren in _FULLWIDTH_PARENS:
            if paren in mapping and mapping[paren] != paren:
                mapping[paren] = paren
    if mapping:
        normalized = normalized.translate(str.maketrans(mapping))
    if cfg.ascii_paren_mapping and not cfg.preserve_fullwidth_parens:
        normalized = normalized.translate(_FULLWIDTH_PAREN_TO_ASCII)
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


def _should_join_with_space(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_char = left[-1]
    right_char = right[0]
    return (
        left_char.isascii()
        and right_char.isascii()
        and left_char.isalnum()
        and right_char.isalnum()
    )


def _collapse_soft_linebreaks(text: str) -> str:
    """Collapse soft line breaks, tabs, and narrow spaces into single spaces."""

    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\t", " ")
    normalized = normalized.replace("\f", " ")
    normalized = normalized.replace("\v", " ")
    normalized = _NBSP_RE.sub(" ", normalized)

    def _join(match: re.Match[str]) -> str:
        left = match.string[: match.start()]
        right = match.string[match.end() :]
        left_char = left.rstrip(_SOFT_STRIP)[-1:]
        right_char = right.lstrip(_SOFT_STRIP)[:1]
        return " " if _should_join_with_space(left_char, right_char) else ""

    normalized = _SOFT_BREAK_RE.sub(_join, normalized)
    normalized = _ASCII_SPACE_RUN.sub(lambda m: f"{m.group(1)} {m.group(2)}", normalized)
    normalized = re.sub(r"[ ]{2,}", " ", normalized)
    return normalized.strip()


def _squash_mixed_spacing(text: str) -> str:
    result = _RE_ASCII_BLOCK.sub(lambda m: m.group(1) + " ", text)
    result = _RE_ASCII_BLOCK_LEFT.sub(lambda m: " " + m.group(1), result)
    return result


def _final_punct_normalize(text: str) -> str:
    compacted = _RE_DASH_VARIANTS.sub("-", text)
    compacted = _RE_ELLIPSIS.sub("...", compacted)
    return compacted


def hard_collapse_whitespace(text: str) -> str:
    """Forcibly collapse all whitespace into single spaces and trim."""

    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\t", " ")
    text = re.sub(r"\n+", "\n", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


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
    normalized = _apply_char_map(text, char_map, cfg)
    if cfg.hard_collapse_lines:
        normalized = hard_collapse_whitespace(normalized)
    if is_debug_logging_enabled():
        sample_before = _preview_line_for_debug(text)
        log_debug(
            "[normalize] drop_ascii_parens=%s preserve_fullwidth_parens=%s ascii_paren_mapping=%s squash_mixed_english=%s collapse_lines=%s order=char_map>whitespace>drop_ascii>mixed-spacing>punct char_map_flags width=%s space=%s delete=%s map=%s",
            bool(cfg.drop_ascii_parens),
            bool(cfg.preserve_fullwidth_parens),
            bool(cfg.ascii_paren_mapping),
            bool(cfg.squash_mixed_english),
            bool(collapse_lines),
            bool(char_map.get("normalize_width")),
            bool(char_map.get("normalize_space")),
            len(char_map.get("delete", [])),
            len(char_map.get("map", {})),
        )
    if collapse_lines:
        normalized = _collapse_soft_linebreaks(normalized)
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
_CLOSER_SET = {ch for ch in _HARD_CLOSERS if not ch.isspace()}
_QUOTE_PAIRS = {
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
    "‹": "›",
    "«": "»",
}
_SYMMETRIC_QUOTES = {'"', "'"}
_QUOTE_OPENERS = set(_QUOTE_PAIRS.keys()) | _SYMMETRIC_QUOTES
_QUOTE_CLOSERS = set(_QUOTE_PAIRS.values()) | _SYMMETRIC_QUOTES
_PAREN_PAIRS = {
    "（": "）",
    "〔": "〕",
    "［": "］",
    "【": "】",
    "《": "》",
    "〈": "〉",
    "(": ")",
    "[": "]",
    "{": "}",
}
_PAREN_OPENERS = set(_PAREN_PAIRS.keys())
_PAREN_CLOSERS = set(_PAREN_PAIRS.values())


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


def _split_hard_layers(text: str, cfg: TextNormConfig, state: dict | None = None) -> List[str]:
    """Split *text* by hard punctuation while keeping closers on the left."""

    hard_set = {ch for ch in cfg.hard_puncts if ch and not ch.isspace()}
    stack: List[str] = []
    layers: List[str] = []
    current: List[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        current.append(ch)
        if ch in _HARD_PAIRS:
            stack.append(_HARD_PAIRS[ch])
        elif ch in _HARD_REVERSE:
            if stack and stack[-1] == ch:
                stack.pop()
        if ch in hard_set and not stack:
            j = i + 1
            while j < length and text[j] in _CLOSER_SET:
                current.append(text[j])
                if stack and stack[-1] == text[j]:
                    stack.pop()
                j += 1
            chunk = "".join(current).strip()
            if chunk:
                _log_split_event(
                    "hard",
                    state,
                    chunk=_preview_line_for_debug(chunk),
                    marker=ch,
                    reason="layer-1",
                    attach=cfg.attach_side,
                    max_len=cfg.max_len,
                )
                layers.append(chunk)
            current = []
            i = j
            continue
        i += 1
    tail = "".join(current).strip()
    if tail:
        _log_split_event(
            "tail",
            state,
            chunk=_preview_line_for_debug(tail),
            marker="",
            reason="flush",
            attach=cfg.attach_side,
            max_len=cfg.max_len,
        )
        layers.append(tail)
    return layers


def _hard_punct_set(cfg: TextNormConfig | None) -> set[str]:
    custom = None
    if cfg is not None:
        custom = {ch for ch in cfg.hard_puncts if ch and not ch.isspace()}
    return custom or set(HARD_PUNCT)


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


def _split_soft_layer(segment: str, cfg: TextNormConfig, state: dict | None = None) -> List[str]:
    queue = [segment]
    sentences: List[str] = []
    soft_set = set(cfg.soft_puncts)
    attach_left = cfg.attach_side != "right"
    while queue:
        chunk = queue.pop(0).strip()
        if not chunk:
            continue
        index = _find_split_index(chunk, cfg, state)
        if index is None:
            sentences.append(chunk)
            continue
        left = chunk[:index].rstrip()
        right = chunk[index:].lstrip()
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
            marker = chunk[index - 1] if index > 0 else chunk[index : index + 1]
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
                marker=chunk[:1],
                reason="leading-soft",
                attach=cfg.attach_side,
                max_len=cfg.max_len,
            )
    return sentences


def _ends_with_hard_punct(text: str, hard_puncts: set[str]) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in hard_puncts


def _merge_short_neighbors(
    sentences: List[str], cfg: TextNormConfig, hard_puncts: set[str] | None = None
) -> List[str]:
    merged: List[str] = []
    puncts = set(hard_puncts) if hard_puncts else _hard_punct_set(cfg)
    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped:
            continue
        if merged and len(stripped) < cfg.min_len and len(merged[-1]) < cfg.min_len:
            if _ends_with_hard_punct(merged[-1], puncts) or _ends_with_hard_punct(stripped, puncts):
                merged.append(stripped)
            else:
                merged[-1] = (merged[-1] + stripped).strip()
        else:
            merged.append(stripped)
    return merged


def _merge_quote_guards(sentences: List[str]) -> List[str]:
    merged: List[str] = []
    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped:
            continue
        if merged and all(ch in _CLOSER_SET for ch in stripped):
            merged[-1] = (merged[-1] + stripped).strip()
        else:
            merged.append(stripped)
    return merged


def _would_cross_block_merge(
    sentences: List[str],
    existing: List[str],
    cfg: TextNormConfig,
) -> int:
    if not sentences or not existing:
        return 0
    first = sentences[0].strip()
    if not first:
        return 0
    if all(ch in _CLOSER_SET for ch in first):
        return 1
    if len(first) < cfg.min_len and len(existing[-1]) < cfg.min_len:
        return 1
    return 0


def _enforce_hard_punct_split(
    lines: List[str], hard_puncts: set[str] | None = None
) -> List[str]:
    puncts = set(hard_puncts) if hard_puncts else set(HARD_PUNCT)
    output: List[str] = []
    for line in lines:
        if not line:
            continue
        start = 0
        local: List[str] = []
        for idx, ch in enumerate(line):
            if ch in puncts:
                segment = line[start : idx + 1]
                if segment:
                    local.append(segment)
                start = idx + 1
        if start < len(line):
            tail = line[start:]
            if tail:
                local.append(tail)
        if not local:
            local = [line]
        output.extend(part.strip() for part in local if part.strip())
    return output


def _enforce_all_punct_split(
    lines: list[str], *, protect_quotes: bool, protect_parens: bool
) -> list[str]:
    output: list[str] = []

    def _consume_chunk(
        chunk: str,
        quote_stack: list[str],
        paren_stack: list[str],
    ) -> None:
        if not chunk:
            return
        for ch in chunk:
            if protect_quotes and ch in _QUOTE_OPENERS:
                closer = _QUOTE_PAIRS.get(ch, ch)
                if closer == ch:
                    if quote_stack and quote_stack[-1] == ch:
                        quote_stack.pop()
                    else:
                        quote_stack.append(ch)
                else:
                    quote_stack.append(closer)
                continue
            if protect_quotes and ch in _QUOTE_CLOSERS:
                if quote_stack and quote_stack[-1] == ch:
                    quote_stack.pop()
            if protect_parens and ch in _PAREN_OPENERS:
                paren_stack.append(_PAREN_PAIRS.get(ch, ch))
            elif protect_parens and ch in _PAREN_CLOSERS:
                if paren_stack and paren_stack[-1] == ch:
                    paren_stack.pop()

    for line in lines:
        if not line:
            continue
        parts = _ALL_PUNCT_RE.split(line)
        buf: list[str] = []
        quote_stack: list[str] = []
        paren_stack: list[str] = []
        pending_flush = False

        def _flush() -> None:
            if not buf:
                return
            segment = "".join(buf).strip()
            if segment:
                output.append(segment)
            buf.clear()

        for part in parts:
            if not part:
                continue
            if len(part) == 1 and part in ALL_PUNCT:
                buf.append(part)
                _consume_chunk(part, quote_stack, paren_stack)
                allow_split = True
                if protect_quotes and quote_stack:
                    allow_split = False
                if protect_parens and paren_stack:
                    allow_split = False
                if allow_split:
                    _flush()
                    pending_flush = False
                else:
                    pending_flush = True
                continue
            for ch in part:
                buf.append(ch)
                _consume_chunk(ch, quote_stack, paren_stack)
                if pending_flush and not quote_stack and not paren_stack:
                    _flush()
                    pending_flush = False
        _flush()
    return output


def split_sentences_with_rules(text: str, cfg: TextNormConfig) -> List[str]:
    """Split text into sentences following hard/soft punctuation rules."""

    if not text:
        return []
    collapse_enabled = bool(getattr(cfg, "collapse_lines", False))
    normalized = (
        _collapse_soft_linebreaks(text)
        if collapse_enabled
        else text.replace("\r\n", "\n").replace("\r", "\n")
    )
    normalized = normalized.strip()
    if not collapse_enabled:
        normalized = _RE_SPACE_RUN.sub(" ", normalized)
    if not normalized:
        return []
    state: dict | None = None
    if is_debug_logging_enabled():
        state = make_log_limit(400)
    hard_puncts = _hard_punct_set(cfg)
    hard_layers = _split_hard_layers(normalized, cfg, state)
    sentences: List[str] = []
    blocked_cross_hard_merges = 0
    for layer in hard_layers:
        soft_sentences = _split_soft_layer(layer, cfg, state)
        soft_sentences = _merge_short_neighbors(soft_sentences, cfg, hard_puncts)
        blocked_cross_hard_merges += _would_cross_block_merge(soft_sentences, sentences, cfg)
        guarded = _merge_quote_guards(soft_sentences)
        sentences.extend(guarded)
    if blocked_cross_hard_merges and is_debug_logging_enabled():
        log_debug(
            "[split] blocked_cross_hard_merges=%s",
            blocked_cross_hard_merges,
        )
    cleaned = [sent for sent in sentences if sent]
    mode = (getattr(cfg, "split_mode", "punct+len") or "punct+len").strip().lower()
    if mode == "all-punct":
        return _enforce_all_punct_split(
            cleaned,
            protect_quotes=bool(getattr(cfg, "quote_protect", True)),
            protect_parens=bool(getattr(cfg, "paren_protect", True)),
        )
    return _enforce_hard_punct_split(cleaned, hard_puncts)


def collapse_soft_linebreaks(text: str) -> str:
    """Public helper collapsing soft line breaks/tabs/nbsp into spaces."""

    return _collapse_soft_linebreaks(text)


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
