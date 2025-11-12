"""兼容旧路径的子进程工具，转发至 :mod:`onepass.utils.subproc`."""
from __future__ import annotations

from .utils.subproc import run_cmd  # noqa: F401  # re-export

__all__ = ["run_cmd"]
