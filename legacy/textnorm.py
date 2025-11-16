"""Compatibility layer for legacy modules importing :mod:`legacy.textnorm`."""
from __future__ import annotations

import onepass.textnorm as _modern_textnorm
from onepass.textnorm import *  # type: ignore[F401,F403]

__all__ = getattr(_modern_textnorm, "__all__", [])
