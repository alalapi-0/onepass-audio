"""Deprecated shim for :mod:`legacy.align`."""
from __future__ import annotations

from warnings import warn

warn(
    "onepass.align 已迁移至 legacy.align，建议使用新的流水线入口。",
    DeprecationWarning,
    stacklevel=2,
)

import legacy.align as _legacy_align
from legacy.align import *  # type: ignore[F401,F403]

__all__ = getattr(_legacy_align, "__all__", [])
