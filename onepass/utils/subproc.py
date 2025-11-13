"""统一的子进程调用封装，保证 UTF-8 解码稳定。"""
from __future__ import annotations

import subprocess
from typing import Sequence

__all__ = ["run_cmd"]


def run_cmd(cmd: Sequence[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """执行子进程并捕获输出，避免 UnicodeDecodeError。"""

    completed: subprocess.CompletedProcess[bytes] = subprocess.run(
        list(cmd),
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
    )
    stdout = (completed.stdout or b"").decode("utf-8", errors="ignore")
    stderr = (completed.stderr or b"").decode("utf-8", errors="ignore")
    completed.stdout = stdout  # type: ignore[assignment]
    completed.stderr = stderr  # type: ignore[assignment]
    return completed  # type: ignore[return-value]
