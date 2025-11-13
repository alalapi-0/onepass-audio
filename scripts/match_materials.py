"""Utilities for matching words/text/audio materials by stem."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from onepass.batch_utils import iter_files, stem_from_words_json

LOGGER = logging.getLogger("onepass.cli")


@dataclass(slots=True)
class MaterialKit:
    """A bundle of resources that share the same stem."""

    stem: str
    words: Path | None = None
    align: Path | None = None
    norm: Path | None = None
    text: Path | None = None
    audio: Path | None = None

    @property
    def resolved_words(self) -> Path | None:
        return self.words.resolve() if self.words else None

    def best_text(self) -> Path | None:
        """Return the preferred text variant for alignment."""

        return self.align or self.norm or self.text


def parse_glob_list(patterns: str | Iterable[str]) -> list[str]:
    """Split glob strings that may use semicolons or whitespace separators."""

    if isinstance(patterns, str):
        tokens: List[str] = [patterns]
    else:
        tokens = [str(item) for item in patterns]

    parsed: list[str] = []
    for token in tokens:
        if not token:
            continue
        for part in re.split(r"[;\s]+", token.replace(",", ";")):
            chunk = part.strip()
            if not chunk:
                continue
            parsed.append(chunk)
    return parsed


def _scan_with_logging(root: Path, patterns: Iterable[str], category: str) -> list[Path]:
    matches = iter_files(root, list(patterns))
    for path in matches:
        LOGGER.info("[hit][%s] stem=%s path=%s", category, path.stem, path.resolve())
    return matches


def is_canonical_stem(stem: str) -> bool:
    """Return True when the stem represents a canonical/derivative text."""

    lowered = stem.lower().strip()
    if not lowered:
        return False
    if lowered.endswith(".canonical"):
        return True
    return ".canonical." in lowered


def match_materials(
    materials_root: Path,
    norm_root: Path,
    text_patterns: Iterable[str],
    glob_words: str,
    glob_audio: str,
    *,
    include_canonical_kits: bool = False,
) -> list[MaterialKit]:
    """Collect all available resources and group them by stem."""

    materials_root = materials_root.expanduser().resolve()
    norm_root = norm_root.expanduser().resolve()

    kits: dict[str, MaterialKit] = {}

    word_patterns = parse_glob_list(glob_words)
    for path in _scan_with_logging(materials_root, word_patterns, "words"):
        stem = stem_from_words_json(path)
        if not include_canonical_kits and is_canonical_stem(stem):
            LOGGER.info("[skip][words] canonical stem=%s path=%s", stem, path)
            continue
        key = stem.lower()
        kit = kits.setdefault(key, MaterialKit(stem=stem))
        kit.words = path.resolve()

    text_patterns_list = list(text_patterns)
    seen_text: set[Path] = set()
    for base in (norm_root, materials_root):
        if not base.exists():
            continue
        for path in iter_files(base, text_patterns_list):
            resolved = path.resolve()
            if resolved in seen_text:
                continue
            seen_text.add(resolved)
            name = path.name.lower()
            stem = ""
            variant = "text"
            if name.endswith(".align.txt"):
                stem = name[: -len(".align.txt")]
                variant = "align"
            elif name.endswith(".norm.txt"):
                stem = name[: -len(".norm.txt")]
                variant = "norm"
            elif name.endswith(".txt"):
                stem = name[: -len(".txt")]
            else:
                continue
            if not include_canonical_kits and is_canonical_stem(stem):
                LOGGER.info("[skip][%s] canonical stem=%s path=%s", variant, stem, resolved)
                continue
            key = stem.lower()
            kit = kits.setdefault(key, MaterialKit(stem=stem))
            if variant == "align" and kit.align is None:
                kit.align = resolved
            elif variant == "norm" and kit.norm is None:
                kit.norm = resolved
            elif kit.text is None:
                kit.text = resolved
            LOGGER.info("[hit][%s] stem=%s path=%s", variant, kit.stem, resolved)

    audio_patterns = parse_glob_list(glob_audio)
    for path in _scan_with_logging(materials_root, audio_patterns, "audio"):
        stem = path.stem
        if not include_canonical_kits and is_canonical_stem(stem):
            LOGGER.info("[skip][audio] canonical stem=%s path=%s", stem, path)
            continue
        key = stem.lower()
        kit = kits.setdefault(key, MaterialKit(stem=stem))
        kit.audio = kit.audio or path.resolve()

    return [kits[key] for key in sorted(kits)]


__all__ = ["MaterialKit", "is_canonical_stem", "match_materials", "parse_glob_list"]
