"""Helpers for controlling debug logging shared across modules."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

_DEBUG_ALIGN_ENABLED: bool = False


def _env_debug_flag() -> bool | None:
    """Return True/False if ONEPASS_DEBUG enforces a value."""

    raw = os.getenv("ONEPASS_DEBUG")
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"", "0", "false", "off"}:
        return False
    return True


def set_debug_logging(enabled: bool) -> None:
    """Allow CLI switches to toggle onepass.debug output."""

    global _DEBUG_ALIGN_ENABLED
    _DEBUG_ALIGN_ENABLED = bool(enabled)


def is_debug_logging_enabled() -> bool:
    """Return True if --debug-align or ONEPASS_DEBUG enables verbose logs."""

    env_value = _env_debug_flag()
    if env_value is not None:
        return env_value
    return _DEBUG_ALIGN_ENABLED


def get_debug_logger() -> logging.Logger:
    """Return the dedicated logger used for verbose instrumentation."""

    return logging.getLogger("onepass.debug")


def make_log_limit(max_entries: int | None) -> Dict[str, Any]:
    """Create a mutable counter guard for limiting debug spam."""

    return {"count": 0, "limit": max_entries}


def log_debug(message: str, *args: object, limit: Dict[str, Any] | None = None) -> None:
    """Emit a debug log when instrumentation is enabled.

    The optional *limit* dict is expected to be created via make_log_limit and
    prevents unlimited output from long-running stages.
    """

    if not is_debug_logging_enabled():
        return
    if limit is not None:
        max_entries = limit.get("limit")
        if max_entries is not None and max_entries >= 0:
            current = int(limit.get("count", 0))
            if current >= max_entries:
                return
            limit["count"] = current + 1
    get_debug_logger().info(message, *args)
