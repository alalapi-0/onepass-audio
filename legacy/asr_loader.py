"""Compatibility layer for legacy modules importing :mod:`legacy.asr_loader`."""
from __future__ import annotations

import onepass.asr_loader as _modern_asr_loader
from onepass.asr_loader import *  # type: ignore[F401,F403]

__all__ = getattr(_modern_asr_loader, "__all__", [])
