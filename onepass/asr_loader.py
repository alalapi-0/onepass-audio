"""onepass.asr_loader
=======================

ASR word-level JSON loader used by the alignment pipeline.

Example
-------
>>> from pathlib import Path
>>> from onepass.asr_loader import load_words
>>> words = load_words(Path('data/asr-json/001.json'))
>>> words[0].text  # doctest: +SKIP
'这是'
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence
import json


@dataclass
class Word:
    """Represents a single word item extracted from ASR JSON output."""

    text: str
    start: float
    end: float

    def duration(self) -> float:
        """Return the duration of the word segment in seconds."""
        return self.end - self.start


def _iter_words_from_segment(segment: dict) -> Iterable[Word]:
    words = segment.get("words")
    if not isinstance(words, Sequence):
        return []
    for raw in words:
        word = _word_from_raw(raw)
        if word is not None:
            yield word


def _word_from_raw(raw: dict | None) -> Word | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("word", "")).strip()
    if not text:
        return None
    try:
        start = float(raw["start"])
        end = float(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if end <= start:
        return None
    return Word(text=text, start=start, end=end)


def load_words(json_path: Path) -> List[Word]:
    """Load ASR words from *json_path*.

    Parameters
    ----------
    json_path:
        Path to a JSON file produced by faster-whisper style word-level output.

    Returns
    -------
    list of :class:`Word`
        Words sorted by their ``start`` timestamp and with invalid items removed.
    """

    data = json.loads(json_path.read_text(encoding="utf-8"))
    words: List[Word] = []

    if isinstance(data, dict):
        if isinstance(data.get("segments"), Sequence):
            for segment in data["segments"]:
                if isinstance(segment, dict):
                    words.extend(_iter_words_from_segment(segment))
        if not words and isinstance(data.get("words"), Sequence):
            for raw in data["words"]:
                word = _word_from_raw(raw)
                if word is not None:
                    words.append(word)
    elif isinstance(data, Sequence):
        for raw in data:
            word = _word_from_raw(raw)
            if word is not None:
                words.append(word)

    words.sort(key=lambda w: w.start)
    return words


__all__ = ["Word", "load_words"]
