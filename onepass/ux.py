# ==== BEGIN: OnePass Patch · R4.1 (UX Core) ====
"""
OnePass · 统一控制台 UX 工具
- 颜色标签：ok/run/warn/err
- 输出：out/info/ok/warn/err
- 步骤计时：with step("标题"):
- 分隔线：hr()
- 表格渲染：table(rows, headers=None, maxw=100)

仅依赖标准库，跨平台；在 Windows 上尽力启用 ANSI 颜色，失败则自动降级为无色。
"""

from __future__ import annotations
from contextlib import contextmanager
import ctypes
import os
import shutil
import sys
import time
from typing import Iterable, List, Optional, Sequence

# ---------- 颜色与降级 ----------

def _win_enable_ansi() -> bool:
    """
    尝试在 Windows 10+ 控制台启用 ANSI 颜色（Virtual Terminal Processing）。
    成功返回 True；失败返回 False 并保持原状。
    """
    try:
        if os.name != "nt":
            return True
        # 已有兼容环境（例如 Windows Terminal、VSCode）可直接返回
        if os.environ.get("WT_SESSION") or os.environ.get("ANSICON"):
            return True
        # NO_COLOR 显式禁用颜色
        if os.environ.get("NO_COLOR"):
            return False
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE = -11
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        new_mode = ctypes.c_uint32(mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        if kernel32.SetConsoleMode(handle, new_mode) == 0:
            return False
        return True
    except Exception:
        return False

# 是否允许彩色
_COLOR_ENABLED = _win_enable_ansi() and not os.environ.get("NO_COLOR")

def _c(code: str) -> str:
    return code if _COLOR_ENABLED else ""

GREEN = _c("\033[32m")
CYAN = _c("\033[36m")
YELLOW = _c("\033[33m")
RED = _c("\033[31m")
DIM = _c("\033[2m")
RESET = _c("\033[0m")

# ---------- 标签与输出 ----------

def tag_ok(msg: str) -> str:
    """返回带颜色的 [成功] 标签行（不打印）。"""
    return f"{GREEN}[成功]{RESET} {msg}"

def tag_run(msg: str) -> str:
    """返回带颜色的 [进行中] 标签行（不打印）。"""
    return f"{CYAN}[进行中]{RESET} {msg}"

def tag_warn(msg: str) -> str:
    """返回带颜色的 [建议] 标签行（不打印）。"""
    return f"{YELLOW}[建议]{RESET} {msg}"

def tag_err(msg: str) -> str:
    """返回带颜色的 [错误] 标签行（不打印）。"""
    return f"{RED}[错误]{RESET} {msg}"

def out(msg: str = "", *, flush: bool = False) -> None:
    """打印一行文本；flush=True 时强制刷新。"""
    print(msg)
    if flush:
        sys.stdout.flush()

def info(msg: str) -> None:
    """打印 [进行中] 行。"""
    out(tag_run(msg))

def ok(msg: str) -> None:
    """打印 [成功] 行。"""
    out(tag_ok(msg))

def warn(msg: str) -> None:
    """打印 [建议] 行（非阻塞建议）。"""
    out(tag_warn(msg))

def err(msg: str) -> None:
    """打印 [错误] 行（阻塞/失败类）。"""
    out(tag_err(msg))

# ---------- 步骤计时 ----------

@contextmanager
def step(title: str):
    """
    统一的步骤计时：
    with step("拉取计划"):
        ...  # 中间即使抛异常也会打印失败行，然后把异常继续上抛给调用方
    """
    t0 = time.time()
    info(title)
    try:
        yield
    except SystemExit:
        # 让调用方决定退出码；这里只做统一标签
        raise
    except Exception as e:
        err(f"{title} 失败：{e.__class__.__name__}: {e}")
        raise
    else:
        dt = time.time() - t0
        ok(f"{title} 完成（耗时 {dt:.1f}s）")

# ---------- 分隔线与表格 ----------

def hr(char: str = "-", width: Optional[int] = None) -> None:
    """
    打印分隔线；默认宽度取终端宽（最多 100 列）。
    """
    width = width or shutil.get_terminal_size((100, 20)).columns
    width = min(width, 100)
    out(char * width)

def table(
    rows: Sequence[Sequence[object]],
    headers: Optional[Sequence[object]] = None,
    maxw: int = 100,
) -> None:
    """
    等宽表格渲染（不依赖第三方）：
    - rows: 二维数组
    - headers: 表头（可选）
    - maxw: 最大列宽总宽（默认 100 列），超过会按比例缩短并截断为 '…'
    """
    if not rows and not headers:
        return
    cols = len(headers) if headers else (len(rows[0]) if rows else 0)
    if cols <= 0:
        return

    def trunc(s: str) -> str:
        return s if len(s) <= maxw else (s[: maxw - 1] + "…")

    # 计算每列最大宽度
    lens = [0] * cols
    if headers:
        for i, h in enumerate(headers):
            lens[i] = max(lens[i], len(str(h)))
    for r in rows:
        for i, c in enumerate(r):
            lens[i] = max(lens[i], len(str(c)))

    # 控制总宽度（包括分隔符）
    total = sum(lens) + 3 * (cols - 1)
    if total > maxw:
        scale = (maxw - 3 * (cols - 1)) / max(1, sum(lens))
        lens = [max(6, int(l * scale)) for l in lens]  # 每列至少 6

    lines: List[str] = []
    if headers:
        lines.append(" | ".join(trunc(str(h)).ljust(lens[i]) for i, h in enumerate(headers)))
        lines.append("-+-".join("-" * lens[i] for i in range(cols)))
    for r in rows:
        lines.append(" | ".join(trunc(str(c)).ljust(lens[i]) for i, c in enumerate(r)))
    out("\n".join(lines))
# ==== END: OnePass Patch · R4.1 (UX Core) ====
