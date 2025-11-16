"""Deprecated shim for :mod:`legacy.normalize_original`."""
from __future__ import annotations

from warnings import warn

warn(
    "onepass.normalize_original 已迁移至 legacy.normalize_original（仅供兼容旧脚本）",
    DeprecationWarning,
    stacklevel=2,
)

import legacy.normalize_original as _legacy_normalize_original
from legacy.normalize_original import *  # type: ignore[F401,F403]

__all__ = getattr(_legacy_normalize_original, "__all__", [])
