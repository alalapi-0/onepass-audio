"""Compatibility layer for legacy modules importing :mod:`legacy.textnorm`."""
from __future__ import annotations

import onepass._legacy_textnorm as _modern_textnorm
from onepass._legacy_textnorm import *  # type: ignore[F401,F403]

__all__ = getattr(_modern_textnorm, "__all__", [])
