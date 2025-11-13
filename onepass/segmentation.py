"""Sentence segmentation utilities for align text generation."""
from __future__ import annotations

import re
from typing import List

__all__ = ["split_text"]

_ALL_PUNCT = "。！？!?；;：:、，,"
_PERIOD_ONLY = "。！？!?"
_MIN_SEGMENT = 1


def _normalise_text(text: str) -> str:
    """Normalise whitespace and newlines for segmentation."""

    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\u3000", " ", normalized)
    normalized = re.sub(r"\t", " ", normalized)
    normalized = re.sub(r"[ \f\v]+", " ", normalized)
    return normalized.strip()


def _split_with_punct(block: str, punct: str) -> List[str]:
    pattern = f"([{re.escape(punct)}])"
    parts = re.split(pattern, block)
    segments: List[str] = []
    for idx in range(0, len(parts), 2):
        core = parts[idx].strip()
        if not core:
            continue
        suffix = parts[idx + 1] if idx + 1 < len(parts) else ""
        merged = f"{core}{suffix.strip()}".strip()
        if merged:
            segments.append(merged)
    return segments


def split_text(
    text: str,
    mode: str = "all-punct",
    *,
    lang: str | None = None,
    min_len: int = 6,
) -> List[str]:
    """Split text into sentences according to punctuation strategy."""

    del lang  # reserved for future extension
    normalized = _normalise_text(text)
    if not normalized:
        return []
    selected = mode.strip().lower() if mode else "all-punct"
    if selected not in {"all-punct", "period-only"}:
        raise ValueError("split mode must be all-punct or period-only")
    punct = _ALL_PUNCT if selected == "all-punct" else _PERIOD_ONLY
    blocks = [segment.strip() for segment in normalized.split("\n") if segment.strip()]
    sentences: List[str] = []
    for block in blocks:
        sentences.extend(_split_with_punct(block, punct))
    if not sentences:
        return []
    threshold = max(min_len, _MIN_SEGMENT)
    merged: List[str] = []
    for sentence in sentences:
        if not merged:
            merged.append(sentence)
            continue
        if len(sentence) < threshold:
            merged[-1] = f"{merged[-1]}{sentence}".strip()
        else:
            merged.append(sentence)
    return merged
