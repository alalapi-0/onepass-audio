"""onepass.textnorm
===================

Text normalisation utilities for preparing transcript sentences before fuzzy
alignment.

Example
-------
>>> raw = Path('data/original_txt/001.txt').read_text(encoding='utf-8')  # doctest: +SKIP
>>> from onepass import textnorm
>>> sentences = [textnorm.Sentence(text=t, tokens=textnorm.tokenize_for_match(t))
...              for t in textnorm.split_sentences(raw)]  # doctest: +SKIP
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List


_SENTENCE_PATTERN = re.compile(r"[^。！？；?!;\r\n]+[。！？；?!;]*", re.MULTILINE)
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_PUNCTUATION_RE = re.compile(r"\s*([。！？；?!;,，、])")


@dataclass
class Sentence:
    """Container representing a normalised sentence and its tokens."""

    text: str
    tokens: List[str]


def split_sentences(raw_text: str) -> List[str]:
    """Split *raw_text* into coarse sentences based on punctuation.

    Consecutive whitespace and blank lines are ignored. Sentence ending
    punctuation such as ``。！？；?!;`` are retained with the preceding text.
    """

    sentences: List[str] = []
    for match in _SENTENCE_PATTERN.finditer(raw_text):
        sentence = match.group().strip()
        if sentence:
            sentences.append(sentence)
    return sentences


def normalize_sentence(s: str) -> str:
    """Normalise a sentence for matching.

    The function collapses whitespace, standardises full-width spaces and trims
    the result while keeping punctuation intact.
    """

    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s)
    s = _PUNCTUATION_RE.sub(r"\1", s)
    return s.strip()


def tokenize_for_match(s: str) -> List[str]:
    """Tokenise *s* for fuzzy matching.

    Chinese and other non-ASCII characters are tokenised per character while
    ASCII alphanumerics are grouped into ``\w+`` style chunks.
    """

    tokens: List[str] = []
    pending_ascii: List[str] = []
    for ch in s:
        if ch.isspace():
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


__all__ = ["Sentence", "split_sentences", "normalize_sentence", "tokenize_for_match"]
