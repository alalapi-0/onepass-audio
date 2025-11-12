"""Subprocess helpers with UTF-8 decoding resilience."""
from __future__ import annotations

from typing import Sequence
import subprocess

__all__ = ["run_cmd"]


def run_cmd(
    cmd: Sequence[str],
    *,
    capture: bool = True,
    timeout: float | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess command and decode outputs as UTF-8 safely.

    Parameters
    ----------
    cmd:
        Command to execute. Must be a sequence of strings.
    capture:
        When ``True`` (default) the stdout/stderr streams are captured and
        returned as text. When ``False`` the command inherits the current
        process stdio streams and empty strings are returned for stdout/stderr.
    timeout:
        Optional timeout passed to :func:`subprocess.run`.

    Returns
    -------
    tuple
        ``(returncode, stdout_text, stderr_text)``. Text is decoded using
        UTF-8 with ``errors="replace"`` to avoid Windows GBK surprises.
    """

    if capture:
        completed = subprocess.run(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
            timeout=timeout,
        )
        stdout = (completed.stdout or b"").decode("utf-8", "replace")
        stderr = (completed.stderr or b"").decode("utf-8", "replace")
        return completed.returncode, stdout, stderr

    completed = subprocess.run(list(cmd), check=False, timeout=timeout)
    return completed.returncode, "", ""
