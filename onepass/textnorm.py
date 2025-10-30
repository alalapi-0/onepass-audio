r"""Utilities for sentence preparation and configurable text normalisation."""
from __future__ import annotations

import importlib
import importlib.util
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

# Re-exported symbols for backwards compatibility with the previous API.
__all__ = [
    "Sentence",
    "split_sentences",
    "normalize_sentence",
    "tokenize_for_match",
    "TextNormConfig",
    "DEFAULT_COMPAT_MAP",
    "load_custom_map",
    "normalize_text",
    "find_nonstandard_chars",
]


@dataclass
class Sentence:
    """Container representing a normalised sentence and its token sequence."""

    text: str
    tokens: List[str]


# Regular expressions reused by legacy helpers.
_SENTENCE_PATTERN = re.compile(r"[^。！？；?!;\r\n]+[。！？；?!;]*", re.MULTILINE)
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_PUNCTUATION_RE = re.compile(r"\s*([。！？；?!;,，、])")


def split_sentences(raw_text: str) -> List[str]:
    """Split *raw_text* into coarse sentences based on punctuation marks."""

    sentences: List[str] = []
    for match in _SENTENCE_PATTERN.finditer(raw_text):
        # Normalise whitespace around the matched sentence.
        sentence = match.group().strip()
        if sentence:
            sentences.append(sentence)
    return sentences


def normalize_sentence(text: str) -> str:
    """Collapse whitespace and punctuation for fuzzy matching."""

    text = text.replace("\u3000", " ")  # Replace full-width spaces.
    text = re.sub(r"\s+", " ", text)  # Compress whitespace runs.
    text = _PUNCTUATION_RE.sub(r"\1", text)  # Remove spacing before punctuation.
    return text.strip()


def tokenize_for_match(text: str) -> List[str]:
    """Tokenise *text* into ASCII word chunks and single CJK characters."""

    tokens: List[str] = []
    pending_ascii: List[str] = []
    for ch in text:
        if ch.isspace():
            # Flush buffered ASCII tokens when encountering whitespace.
            if pending_ascii:
                tokens.append("".join(pending_ascii))
                pending_ascii.clear()
            continue
        if _ASCII_WORD_RE.fullmatch(ch):
            pending_ascii.append(ch.lower())
            continue
        if pending_ascii:
            tokens.append("".join(pending_ascii))
            pending_ascii.clear()
        tokens.append(ch)
    if pending_ascii:
        tokens.append("".join(pending_ascii))
    return tokens


@dataclass(slots=True)
class TextNormConfig:
    """Configuration flags controlling ``normalize_text`` behaviour."""

    nfkc: bool = True
    strip_bom: bool = True
    strip_zw: bool = True
    collapse_spaces: bool = True
    punct_style: str = "ascii"
    map_compat: bool = True
    opencc_mode: str | None = None
    custom_map_path: str | None = "config/textnorm_custom_map.json"


DEFAULT_COMPAT_MAP: Dict[str, str] = {
    # Common radicals and compatibility characters frequently seen in OCR text.
    "⼈": "人",
    "⼒": "力",
    "⾔": "言",
    "⽹": "网",
    "⻔": "门",
    "⻢": "马",
    "⻓": "长",
    "⻋": "车",
    "⼀": "一",
    "⼆": "二",
    "⼗": "十",
    "⽬": "目",
    "⼿": "手",
    "⼤": "大",
}


# The following sets define invisible or suspicious characters worth reporting.
_ZERO_WIDTH_CHARS = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\ufeff",
    "\u2060",
}
_SUSPECT_CONTROL_CATEGORIES = {"Cf", "Cc"}


# Punctuation translation tables for the configurable styles.
_PUNCT_ASCII_TABLE: Sequence[Tuple[str, str]] = (
    ("，", ","),
    ("。", "."),
    ("！", "!"),
    ("？", "?"),
    ("：", ":"),
    ("；", ";"),
    ("（", "("),
    ("）", ")"),
    ("【", "["),
    ("】", "]"),
    ("《", "<"),
    ("》", ">"),
    ("「", '"'),
    ("」", '"'),
    ("『", '"'),
    ("』", '"'),
    ("‘", "'"),
    ("’", "'"),
    ("“", '"'),
    ("”", '"'),
    ("、", ","),
    ("……", "..."),
    ("…", "..."),
    ("——", "-"),
    ("—", "-"),
    ("－", "-"),
)
_PUNCT_CJK_TABLE: Sequence[Tuple[str, str]] = (
    (",", "，"),
    (".", "。"),
    ("!", "！"),
    ("?", "？"),
    (":", "："),
    (";", "；"),
    ("(", "（"),
    (")", "）"),
    ("[", "【"),
    ("]", "】"),
    ("<", "《"),
    (">", "》"),
    ("\"", "”"),
    ("'", "’"),
    ("-", "——"),
    ("...", "……"),
)


_OPENCC_SPEC = importlib.util.find_spec("opencc")
_OPENCC_MODULE = importlib.import_module("opencc") if _OPENCC_SPEC else None
# Track whether we have already warned the user about missing opencc support.
_OPENCC_WARNING_EMITTED = False


def load_custom_map(path: str | None) -> Dict[str, str]:
    """Load additional compatibility mappings from *path* if it exists."""

    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - invalid user input
        raise ValueError(f"无法解析自定义映射 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("自定义映射文件必须是键值对 JSON 对象。")
    # Filter only string-to-string overrides to avoid crashes.
    return {str(key): str(value) for key, value in data.items()}


def _apply_punctuation(text: str, style: str) -> Tuple[str, int]:
    """Return text with punctuation converted according to *style*."""

    if style == "keep":
        return text, 0
    replaced = 0
    working = text
    table = _PUNCT_ASCII_TABLE if style == "ascii" else _PUNCT_CJK_TABLE
    for source, target in table:
        if not source:
            continue
        # Replace occurrences and update replacement statistics.
        new_text = working.replace(source, target)
        if new_text != working:
            if len(source) == 1:
                replaced += working.count(source)
            else:
                replaced += working.count(source)
            working = new_text
    return working, replaced


def _strip_zero_width(text: str) -> Tuple[str, int]:
    """Remove zero-width and control characters from *text*."""

    removed = 0
    chars: List[str] = []
    for ch in text:
        if ch in _ZERO_WIDTH_CHARS:
            removed += 1
            continue
        if unicodedata.category(ch) in _SUSPECT_CONTROL_CATEGORIES and ch not in {"\n", "\r", "\t"}:
            removed += 1
            continue
        chars.append(ch)
    return "".join(chars), removed


def _collapse_whitespace(text: str) -> Tuple[str, int]:
    """Collapse consecutive spaces/tabs while preserving paragraph breaks."""

    changed = 0
    normalised_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    segments = normalised_newlines.split("\n")
    lines: List[str] = []
    for line in segments:
        stripped = re.sub(r"[ \t\f\v]+", " ", line.strip())
        if stripped != line:
            changed += 1
        lines.append(stripped)
    return "\n".join(lines), changed


def _ensure_trailing_newline(text: str) -> str:
    """Guarantee that *text* ends with a single newline character."""

    if not text.endswith("\n"):
        return text + "\n"
    return text


def normalize_text(text: str, cfg: TextNormConfig) -> Tuple[str, Dict[str, int]]:
    """Normalise *text* according to *cfg* and return statistics."""

    stats: Dict[str, int] = {
        "len_before": len(text),
        "len_after": len(text),
        "replaced_compat": 0,
        "removed_zw": 0,
        "bom_removed": 0,
        "punct_changes": 0,
        "space_collapses": 0,
    }

    working = text

    if cfg.nfkc:
        # Apply Unicode NFKC folding before more specific replacements.
        working = unicodedata.normalize("NFKC", working)

    if cfg.strip_bom and working.startswith("\ufeff"):
        # Remove Unicode BOM characters that occasionally sneak into files.
        working = working.lstrip("\ufeff")
        stats["bom_removed"] = 1

    if cfg.strip_zw:
        # Drop zero-width or control characters that hinder alignment.
        working, removed = _strip_zero_width(working)
        stats["removed_zw"] = removed

    compat_map: Dict[str, str] = DEFAULT_COMPAT_MAP.copy() if cfg.map_compat else {}
    if cfg.map_compat and cfg.custom_map_path:
        compat_map.update(load_custom_map(cfg.custom_map_path))
    if compat_map:
        replaced = 0
        chars: List[str] = []
        for ch in working:
            # Replace compatibility radicals with their common forms.
            target = compat_map.get(ch)
            if target is not None:
                replaced += 1
                chars.append(target)
            else:
                chars.append(ch)
        working = "".join(chars)
        stats["replaced_compat"] = replaced

    working, punct_changes = _apply_punctuation(working, cfg.punct_style)
    stats["punct_changes"] = punct_changes

    if cfg.collapse_spaces:
        # Compress repeated spaces/tabs to reduce alignment noise.
        working, collapsed = _collapse_whitespace(working)
        stats["space_collapses"] = collapsed

    if cfg.opencc_mode == "t2s":
        if _OPENCC_MODULE is not None:
            converter = _OPENCC_MODULE.OpenCC("t2s")
            working = converter.convert(working)
        else:
            global _OPENCC_WARNING_EMITTED
            if not _OPENCC_WARNING_EMITTED:
                print(
                    "提示: 未安装 opencc，已跳过繁转简，可执行 `pip install opencc` 启用。",
                    flush=True,
                )
                _OPENCC_WARNING_EMITTED = True

    working = _ensure_trailing_newline(working)
    stats["len_after"] = len(working)

    return working, stats


def find_nonstandard_chars(text: str) -> Dict[str, int]:
    """Count suspicious characters in *text* for reporting purposes."""

    suspect_chars = set(DEFAULT_COMPAT_MAP)
    suspect_chars.update(_ZERO_WIDTH_CHARS)
    counts: Dict[str, int] = {}
    for ch in text:
        if ch in suspect_chars or unicodedata.category(ch) in _SUSPECT_CONTROL_CATEGORIES:
            counts[ch] = counts.get(ch, 0) + 1
    return counts

