"""Deprecated shim for :mod:`legacy.text_split`."""
from __future__ import annotations

from warnings import warn

warn(
    "onepass.text_split 已迁移至 legacy.text_split（仅保留旧 API）",
    DeprecationWarning,
    stacklevel=2,
)

import legacy.text_split as _legacy_text_split
from legacy.text_split import *  # type: ignore[F401,F403]

__all__ = getattr(_legacy_text_split, "__all__", [])
