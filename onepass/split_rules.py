"""Deprecated shim for :mod:`legacy.split_rules`."""
from __future__ import annotations

from warnings import warn

warn(
    "onepass.split_rules 已迁移至 legacy.split_rules（仅供兼容旧流程）",
    DeprecationWarning,
    stacklevel=2,
)

import legacy.split_rules as _legacy_split_rules
from legacy.split_rules import *  # type: ignore[F401,F403]

__all__ = getattr(_legacy_split_rules, "__all__", [])
