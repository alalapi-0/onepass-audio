"""Lightweight text canonicalization helpers for alignment and matching."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import unicodedata
from pathlib import Path
from typing import Dict

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_ROMAN_RE = re.compile(r"[A-Za-z0-9]+(?:[A-Za-z0-9 ]+[A-Za-z0-9]+)?")

_ALIAS_SORT_CACHE: dict[int, list[str]] = {}


@dataclass(slots=True)
class CanonicalAliasMap:
    """Holds a variant->canonical dictionary with cached sorted keys."""

    mapping: Dict[str, str]

    def replacements(self) -> list[str]:
        key = id(self.mapping)
        cached = _ALIAS_SORT_CACHE.get(key)
        if cached is None:
            cached = sorted(self.mapping.keys(), key=len, reverse=True)
            _ALIAS_SORT_CACHE[key] = cached
        return cached


def load_alias_map(path: str | Path | None) -> dict[str, str]:
    """Load alias mapping as ``variant -> canonical`` dictionary."""

    if not path:
        return {}
    candidate = Path(path).expanduser()
    try:
        if not candidate.exists():
            return {}
        data = json.loads(candidate.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return {}
    mapping: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            canonical = ""
            variants: list[str] = []
            if isinstance(value, str):
                canonical = str(value or "").strip()
                variants = [str(key or "").strip()]
            elif isinstance(value, (list, tuple)):
                canonical = str(key or "").strip()
                variants = [str(item or "").strip() for item in value]
            else:
                canonical = str(key or "").strip()
                variants = [canonical]
            canonical = canonical or str(key or "").strip()
            canonical = canonical or ""
            if not canonical:
                continue
            for variant in variants:
                if not variant:
                    continue
                mapping[variant] = canonical
            mapping.setdefault(canonical, canonical)
    return mapping


def apply_alias(text: str, alias: dict[str, str] | CanonicalAliasMap | None) -> str:
    if not text or not alias:
        return text
    if isinstance(alias, CanonicalAliasMap):
        mapping = alias.mapping
        keys = alias.replacements()
    else:
        mapping = alias
        keys = sorted(alias.keys(), key=len, reverse=True)
    result = text
    for key in keys:
        replacement = mapping.get(key)
        if not replacement or key not in result:
            continue
        result = result.replace(key, replacement)
    return result


def canonicalize(text: str, alias: dict[str, str] | CanonicalAliasMap | None = None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _WS_RE.sub(" ", value)
    if value:
        def _merge(match: re.Match[str]) -> str:
            return match.group(0).replace(" ", "")

        value = _ROMAN_RE.sub(_merge, value)
    if alias:
        value = apply_alias(value, alias)
    return value


__all__ = ["CanonicalAliasMap", "apply_alias", "canonicalize", "load_alias_map"]
