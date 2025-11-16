"""Deprecated shim for :mod:`legacy.retake.matcher`."""
from __future__ import annotations

from warnings import warn

warn(
    "onepass.retake.matcher 已迁移至 legacy.retake.matcher（仅保留旧 API）",
    DeprecationWarning,
    stacklevel=2,
)

import legacy.retake.matcher as _legacy_retake_matcher
from legacy.retake.matcher import *  # type: ignore[F401,F403]

__all__ = getattr(_legacy_retake_matcher, "__all__", [])
