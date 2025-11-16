"""Deprecated CLI stub for :mod:`legacy.scripts.normalize_original`."""
from __future__ import annotations

import sys
from warnings import warn

from legacy.scripts.normalize_original import main

warn(
    "scripts/normalize_original.py 已迁移至 legacy/scripts/normalize_original.py",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":  # pragma: no cover - CLI shim
    raise SystemExit(main())
