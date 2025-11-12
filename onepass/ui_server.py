"""轻量级静态 Web UI 服务工具。"""
from __future__ import annotations

import http.server
import socketserver
import threading
import time
import webbrowser
from pathlib import Path
from typing import Tuple

__all__ = ["start_static_server", "open_browser_later"]


def start_static_server(root: str, host: str = "127.0.0.1", port: int = 8765) -> Tuple[threading.Thread, str]:
    """启动一个基于 :class:`http.server.SimpleHTTPRequestHandler` 的静态资源服务。

    参数
    ----
    root:
        前端静态资源根目录。
    host, port:
        监听地址与端口。

    返回
    ----
    (thread, url):
        后台服务线程与基础访问 URL。线程以 daemon 方式运行，不会阻塞主线程。
    """

    resolved_root = str(Path(root).resolve())
    handler_cls = type(
        "RootedHandler",
        (http.server.SimpleHTTPRequestHandler,),
        {"directory": resolved_root},
    )
    httpd = socketserver.TCPServer((host, port), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return thread, f"http://{host}:{port}/app.html"


def open_browser_later(url: str, delay: float = 0.8) -> None:
    """在后台延迟打开浏览器标签页。"""

    def _open() -> None:
        time.sleep(delay)
        webbrowser.open_new_tab(url)

    threading.Thread(target=_open, daemon=True).start()
