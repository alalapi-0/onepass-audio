"""High-level helpers shared across CLI entry points."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .textnorm import Sentence, normalize_sentence, split_sentences, tokenize_for_match


@dataclass
class PreparedSentences:
    """Container holding sentences for alignment and display."""

    alignment: List[Sentence]
    display: List[str]


def prepare_sentences(raw_text: str) -> PreparedSentences:
    """Prepare transcript sentences for alignment and output.

    ``raw_text`` is split into coarse sentences and normalised for fuzzy
    matching. Entries with no meaningful tokens are skipped to keep alignment
    indices consistent between the returned ``alignment`` list and the
    human-readable ``display`` list.
    """

    alignment: List[Sentence] = []
    display: List[str] = []

    for raw_sentence in split_sentences(raw_text):
        trimmed = raw_sentence.strip()
        if not trimmed:
            continue

        normalised = normalize_sentence(trimmed)
        if not normalised:
            continue

        tokens = tokenize_for_match(normalised)
        if not tokens:
            continue

        alignment.append(Sentence(text=normalised, tokens=tokens))
        display.append(trimmed)

    return PreparedSentences(alignment=alignment, display=display)


__all__ = ["PreparedSentences", "prepare_sentences"]
