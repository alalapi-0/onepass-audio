"""Compatibility shim for :mod:`onepass.text_normalizer`."""
from __future__ import annotations

import warnings

from . import text_normalizer as _text_normalizer

__all__ = getattr(_text_normalizer, "__all__", tuple())

_warned = False


def __getattr__(name: str):  # pragma: no cover - dynamic proxy
    global _warned
    if not _warned:
        warnings.warn(
            "Deprecated, use onepass.text_normalizer",
            DeprecationWarning,
            stacklevel=2,
        )
        _warned = True
    return getattr(_text_normalizer, name)
