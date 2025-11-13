"""Launch the FastAPI-based Web UI as a subprocess-friendly script."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Mapping
from urllib.parse import urlencode

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from onepass.web_server import run_web_server

__all__ = ["start_ui", "main"]

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 5173


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the standalone UI server."""

    parser = argparse.ArgumentParser(description="OnePass Audio Web UI server")
    parser.add_argument("--out", default=str(ROOT_DIR / "out"), help="Output directory to inspect")
    parser.add_argument("--audio-root", default=None, help="Optional audio root for waveform preview")
    parser.add_argument("--host", default=_DEFAULT_HOST, help="Bind host, default 127.0.0.1")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help="Listen port, default 5173")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level")
    parser.add_argument("--enable-cors", action="store_true", help="Allow cross-origin requests")
    parser.add_argument("--open-browser", action="store_true", help="Automatically open a browser tab")
    return parser.parse_args()


def _delayed_open(url: str, delay: float = 0.8) -> None:
    """Open the given *url* in a browser tab after *delay* seconds."""

    time.sleep(max(0.0, delay))
    try:
        webbrowser.open(url)
    except Exception:
        # Best-effort helper, no crash if system browser is unavailable.
        pass


def _build_url(host: str, port: int, query: Mapping[str, str] | None = None) -> str:
    """Return the Web UI base URL with optional query parameters."""

    base = f"http://{host}:{port}/"
    if query:
        return f"{base}?{urlencode(query)}"
    return base


def start_ui(
    *,
    out_dir: Path | str | None = None,
    audio_root: Path | str | None = None,
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    enable_cors: bool = False,
    open_browser: bool = False,
    query: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Spawn the UI server process and optionally open a browser tab."""

    out_path = Path(out_dir or ROOT_DIR / "out").expanduser().resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    audio_root_path = Path(audio_root).expanduser().resolve() if audio_root else None

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--out",
        str(out_path),
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "info",
    ]
    if audio_root_path:
        cmd.extend(["--audio-root", str(audio_root_path)])
    if enable_cors:
        cmd.append("--enable-cors")
    spawn_env = os.environ.copy()
    if env:
        spawn_env.update(env)
    if "PYTHONPATH" in spawn_env:
        paths = spawn_env["PYTHONPATH"].split(os.pathsep)
        if str(ROOT_DIR) not in paths:
            spawn_env["PYTHONPATH"] = os.pathsep.join([str(ROOT_DIR), *paths])
    else:
        spawn_env["PYTHONPATH"] = str(ROOT_DIR)
    proc = subprocess.Popen(cmd, cwd=str(ROOT_DIR), env=spawn_env)
    url = _build_url(host, port, query)
    if open_browser:
        threading.Thread(target=_delayed_open, args=(url,), daemon=True).start()
    return proc


def main() -> None:
    """CLI entry point that blocks while the Web UI server is running."""

    args = _parse_args()
    out_dir = Path(args.out).expanduser().resolve()
    audio_root = Path(args.audio_root).expanduser().resolve() if args.audio_root else None
    run_web_server(
        out_dir,
        audio_root,
        host=args.host,
        port=args.port,
        enable_cors=args.enable_cors,
        open_browser=args.open_browser,
        log_level=args.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
