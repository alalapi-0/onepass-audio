"""统一的子进程调用封装，保证 UTF-8 解码稳定。"""
from __future__ import annotations

import subprocess
from typing import Sequence

__all__ = ["run_cmd"]


def run_cmd(cmd: Sequence[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """执行子进程并捕获输出，避免 UnicodeDecodeError。"""

    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
