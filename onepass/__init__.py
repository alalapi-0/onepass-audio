"""OnePass Audio package initializer.

Provides convenient shortcuts to the core modules that implement the "keep
last take" workflow.

Example
-------
>>> from onepass import load_words, textnorm  # doctest: +SKIP
"""

from .align import AlignResult, MatchWindow, align_sentences
from .asr_loader import Word, load_words
from .edl import EDL, EDLAction, build_keep_last_edl, merge_intervals
from .markers import write_audition_markers
from .textnorm import Sentence, normalize_sentence, split_sentences, tokenize_for_match

__all__ = [
    "AlignResult",
    "MatchWindow",
    "align_sentences",
    "Word",
    "load_words",
    "EDL",
    "EDLAction",
    "build_keep_last_edl",
    "merge_intervals",
    "write_audition_markers",
    "Sentence",
    "normalize_sentence",
    "split_sentences",
    "tokenize_for_match",
]
