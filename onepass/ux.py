"""onepass.ux
用途：提供控制台实时日志、颜色输出、心跳与子进程流式输出工具，统一 OnePass Audio 的交互体验。
依赖：Python 标准库 ctypes、os、subprocess、threading、time、pathlib。
示例：
  from pathlib import Path
  from onepass.ux import enable_ansi, log_info, run_streamed, Spinner

  enable_ansi()
  log_info("启动任务")
  spinner = Spinner()
  spinner.start("准备中")
  # ...
  spinner.stop_ok("准备完成")
  run_streamed(["python", "scripts/example.py"], cwd=Path("onepass"))
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

__all__ = [
    "Color",
    "Spinner",
    "enable_ansi",
    "format_cmd",
    "log_err",
    "log_info",
    "log_ok",
    "log_warn",
    "run_streamed",
    "section",
    "ts",
]

_COLOR_ENABLED = False
_HEARTBEAT_SUFFIX = " " * 10


class Color:
    """提供 ANSI 颜色包裹字符串的帮助方法。"""

    @staticmethod
    def ok() -> str:
        return "\033[32m" if _COLOR_ENABLED else ""

    @staticmethod
    def warn() -> str:
        return "\033[33m" if _COLOR_ENABLED else ""

    @staticmethod
    def err() -> str:
        return "\033[31m" if _COLOR_ENABLED else ""

    @staticmethod
    def info() -> str:
        return "\033[36m" if _COLOR_ENABLED else ""

    @staticmethod
    def dim() -> str:
        return "\033[2m" if _COLOR_ENABLED else ""

    @staticmethod
    def reset() -> str:
        return "\033[0m" if _COLOR_ENABLED else ""


def enable_ansi() -> None:
    """尝试启用 ANSI 颜色输出。"""

    global _COLOR_ENABLED
    if os.environ.get("ONEPASS_NO_ANSI") == "1":
        _COLOR_ENABLED = False
        return
    if os.name != "nt":
        _COLOR_ENABLED = True
        return
    try:
        import ctypes  # type: ignore

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            new_mode = mode.value | 0x0004
            if kernel32.SetConsoleMode(handle, new_mode):
                _COLOR_ENABLED = True
                return
    except Exception:
        pass
    _COLOR_ENABLED = False


def ts() -> str:
    """返回当前本地时间的 HH:MM:SS 字符串。"""

    return time.strftime("%H:%M:%S", time.localtime())


def _print_with_color(prefix: str, msg: str, color_prefix: str, suffix: str = "") -> None:
    line = f"[{ts()}] {color_prefix}{prefix}{Color.reset()} {msg}{suffix}"
    print(line, flush=True)


def log_info(msg: str) -> None:
    """打印普通信息日志。"""

    _print_with_color("[信息]", msg, Color.info())


def log_ok(msg: str) -> None:
    """打印成功日志。"""

    _print_with_color("[完成]", msg, Color.ok())


def log_warn(msg: str) -> None:
    """打印警告日志。"""

    _print_with_color("[警告]", msg, Color.warn())


def log_err(msg: str) -> None:
    """打印错误日志。"""

    _print_with_color("[错误]", msg, Color.err())


def section(title: str) -> None:
    """打印分割标题。"""

    border = f"{Color.dim()}{'=' * 20}{Color.reset()}"
    print(f"\n{border} {title} {border}", flush=True)


class Spinner:
    """在后台线程中渲染旋转进度动画。"""

    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self) -> None:
        self._text = ""
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._active = False

    def start(self, text: str) -> None:
        """启动动画并显示 ``text``。"""

        with self._lock:
            self._text = text
            if self._active:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._active = True
            self._thread.start()

    def update(self, text: str) -> None:
        """更新当前显示文本。"""

        with self._lock:
            self._text = text

    def stop_ok(self, text: str) -> None:
        """以成功状态停止动画。"""

        self._stop(Color.ok(), text)

    def stop_err(self, text: str) -> None:
        """以失败状态停止动画。"""

        self._stop(Color.err(), text)

    def _stop(self, color: str, text: str) -> None:
        with self._lock:
            if not self._active:
                return
            self._stop_event.set()
            thread = self._thread
            self._active = False
        if thread:
            thread.join()
        print("\r" + " " * 80, end="\r", flush=True)
        _print_with_color("[进度]", text, color)

    def _run(self) -> None:
        index = 0
        while not self._stop_event.is_set():
            with self._lock:
                text = self._text
            frame = self.frames[index % len(self.frames)]
            prefix = f"[{ts()}] {Color.info()}[进度]{Color.reset()}"
            print(f"\r{prefix} {frame} {text}{' ' * 20}", end="", flush=True)
            time.sleep(0.12)
            index += 1


def format_cmd(cmd: Iterable[str]) -> str:
    """将命令列表格式化为一行字符串。"""

    import shlex

    return " ".join(shlex.quote(str(arg)) for arg in cmd)


def run_streamed(
    cmd: List[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    prefix: str = "",
    show_cmd: bool = True,
    heartbeat_s: float = 30.0,
    line_callback: Callable[[str, bool], bool | None] | None = None,
) -> int:
    """以流式方式运行子进程并实时打印输出。"""

    if show_cmd:
        log_info(f"将执行命令：{format_cmd(cmd)}")
    start_ts = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env={**os.environ, **env} if env else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    lock = threading.Lock()
    last_output = {"time": time.monotonic()}
    heartbeat_state = {"shown": False}

    def _emit(line: str, is_err: bool) -> None:
        tag = "STDERR" if is_err else "STDOUT"
        color = Color.warn() if is_err else Color.dim()
        with lock:
            if heartbeat_state["shown"]:
                print("", flush=True)
                heartbeat_state["shown"] = False
            text = line.rstrip("\n")
            skip_default = False
            if line_callback:
                try:
                    handled = line_callback(text, is_err)
                except Exception as callback_error:  # pragma: no cover - defensive path
                    _print_with_color("[警告]", f"行回调异常：{callback_error}", Color.warn())
                else:
                    skip_default = bool(handled)
            if not skip_default:
                prefix_tag = f"{prefix}" if prefix else ""
                message = f"[{ts()}] {color}{prefix_tag}[{tag}]{Color.reset()} {text}"
                print(message, flush=True)
            last_output["time"] = time.monotonic()

    def _reader(stream: Optional[Iterable[str]], is_err: bool) -> None:
        if stream is None:
            return
        for raw_line in stream:
            _emit(raw_line, is_err)
        stream.close()

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout, False), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, True), daemon=True),
    ]
    for t in threads:
        t.start()

    def _heartbeat() -> None:
        while proc.poll() is None:
            time.sleep(1.0)
            if proc.poll() is not None:
                break
            elapsed = time.monotonic() - start_ts
            since_last = time.monotonic() - last_output["time"]
            if since_last < heartbeat_s:
                continue
            text = f"[{ts()}] {Color.info()}{prefix}仍在运行（已用时 {int(elapsed)}s）{Color.reset()}"
            with lock:
                print("\r" + text + _HEARTBEAT_SUFFIX, end="", flush=True)
                heartbeat_state["shown"] = True

    hb_thread = threading.Thread(target=_heartbeat, daemon=True)
    hb_thread.start()

    proc.wait()
    for t in threads:
        t.join()
    hb_thread.join()
    with lock:
        if heartbeat_state["shown"]:
            print("", flush=True)
            heartbeat_state["shown"] = False
    return proc.returncode
