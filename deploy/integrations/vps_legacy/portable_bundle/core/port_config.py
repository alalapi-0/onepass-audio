"""Helpers for resolving the default WireGuard listen port."""
from __future__ import annotations

import os

ENV_KEYS = (
    "PRIVATETUNNEL_WG_PORT",
    "PT_WG_PORT",
    "WG_PORT",
)

DEFAULT_WG_PORT = 443


def _parse_port(value: str, *, source: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError(f"环境变量 {source} 的值必须是有效的整数端口号，当前为: {value!r}") from exc

    if not 1 <= port <= 65535:
        raise ValueError(
            f"环境变量 {source} 的值 {port} 超出有效范围 (1-65535)。"
        )
    return port


def resolve_listen_port() -> tuple[int, str | None]:
    """Return the listen port and the environment variable that defined it."""

    for key in ENV_KEYS:
        value = os.environ.get(key)
        if value:
            return _parse_port(value, source=key), key
    return DEFAULT_WG_PORT, None


def get_default_wg_port() -> int:
    """Return the WireGuard listen port derived from environment variables.

    The helper inspects ``PRIVATETUNNEL_WG_PORT`` (preferred), ``PT_WG_PORT`` and
    ``WG_PORT``. The first non-empty variable wins. Values must be integers within
    the 1-65535 range. When no overrides are present the default ``443`` is
    returned.
    """

    port, _ = resolve_listen_port()
    return port

