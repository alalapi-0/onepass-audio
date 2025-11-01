"""OnePass Audio 命令行交互与终端输出的辅助函数。"""
from __future__ import annotations  # 启用未来注解语法

from dataclasses import dataclass  # 构建不可变样式对象
from pathlib import Path  # 统一路径操作
from typing import Sequence  # 类型注解：选项序列

__all__ = [
    "AnsiStyle",
    "print_header",
    "print_info",
    "print_success",
    "print_warning",
    "print_error",
    "prompt_text",
    "prompt_existing_file",
    "prompt_existing_directory",
    "prompt_choice",
    "prompt_yes_no",
]


@dataclass(frozen=True)
class AnsiStyle:
    """描述一组 ANSI 转义序列，便于着色终端输出。"""

    prefix: str  # 前缀转义码
    suffix: str = "\x1b[0m"  # 后缀重置码

    def apply(self, message: str) -> str:
        """将样式应用到消息文本。"""

        return f"{self.prefix}{message}{self.suffix}"


STYLE_HEADER = AnsiStyle("\x1b[95m")  # 标题样式：洋红
STYLE_INFO = AnsiStyle("\x1b[94m")  # 普通信息：蓝色
STYLE_SUCCESS = AnsiStyle("\x1b[92m")  # 成功信息：绿色
STYLE_WARNING = AnsiStyle("\x1b[93m")  # 警告信息：黄色
STYLE_ERROR = AnsiStyle("\x1b[91m")  # 错误信息：红色


def _supports_colour() -> bool:
    """探测当前终端是否支持彩色输出。"""

    try:
        import sys  # 延迟导入，避免在非交互环境报错

        return sys.stdout.isatty()  # 仅当 stdout 连接到终端才启用颜色
    except Exception:  # pragma: no cover - 极端环境下的兼容
        return False


def _colourise(message: str, style: AnsiStyle) -> str:
    """在支持彩色时应用样式，否则保持原文本。"""

    if not message:  # 空字符串无需处理
        return message
    if not _supports_colour():  # 不支持彩色直接返回原文本
        return message
    return style.apply(message)  # 返回带有颜色的文本


def print_header(title: str) -> None:
    """以高亮样式打印区块标题。"""

    border = "=" * len(title)  # 生成同宽度分隔线
    print(_colourise(border, STYLE_HEADER))
    print(_colourise(title, STYLE_HEADER))
    print(_colourise(border, STYLE_HEADER))


def _print_with_style(message: str, style: AnsiStyle) -> None:
    """按给定样式输出一行文本。"""

    print(_colourise(message, style))


def print_info(message: str) -> None:
    """输出一般提示信息。"""

    _print_with_style(message, STYLE_INFO)


def print_success(message: str) -> None:
    """输出成功提示。"""

    _print_with_style(message, STYLE_SUCCESS)


def print_warning(message: str) -> None:
    """输出警告信息。"""

    _print_with_style(message, STYLE_WARNING)


def print_error(message: str) -> None:
    """输出错误信息。"""

    _print_with_style(message, STYLE_ERROR)


def prompt_text(prompt: str, *, default: str | None = None, allow_empty: bool = False) -> str:
    """提示用户输入自由文本，支持默认值与允许留空。"""

    while True:
        suffix = f" [{default}]" if default else ""  # 拼接默认值提示
        value = input(f"{prompt}{suffix}: ").strip()  # 读取输入并去掉首尾空白
        if not value and default is not None:  # 留空时回退默认值
            return default
        if value or allow_empty:  # 非空或允许空字符串时返回
            return value
        print_warning("输入不能为空，请重新输入。")  # 否则继续循环


def _validate_path(path: Path, *, should_exist: bool, path_type: str) -> Path:
    """校验路径是否存在并符合类型要求。"""

    if should_exist and not path.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    if should_exist and path_type == "file" and not path.is_file():
        raise FileNotFoundError(f"不是有效的文件: {path}")
    if should_exist and path_type == "directory" and not path.is_dir():
        raise FileNotFoundError(f"不是有效的目录: {path}")
    return path


def prompt_existing_file(prompt: str, *, default: Path | None = None) -> Path:
    """提示用户输入存在的文件路径。"""

    while True:
        raw = prompt_text(prompt, default=str(default) if default else None)
        path = Path(raw).expanduser().resolve()
        try:
            return _validate_path(path, should_exist=True, path_type="file")
        except FileNotFoundError as exc:
            print_error(str(exc))


def prompt_existing_directory(prompt: str, *, default: Path | None = None) -> Path:
    """提示用户输入存在的目录路径。"""

    while True:
        raw = prompt_text(prompt, default=str(default) if default else None)
        path = Path(raw).expanduser().resolve()
        try:
            return _validate_path(path, should_exist=True, path_type="directory")
        except FileNotFoundError as exc:
            print_error(str(exc))


def prompt_choice(prompt: str, options: Sequence[str], *, default: int | None = None) -> str:
    """提示用户通过编号从选项列表中选择。"""

    if not options:
        raise ValueError("至少需要一个可供选择的选项。")

    option_lines = [f"  {idx + 1}. {option}" for idx, option in enumerate(options)]
    print("\n".join(option_lines))

    while True:
        raw = prompt_text(prompt, default=str(default + 1) if default is not None else None)
        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(options):
                return options[index]
        print_warning("无效的选择，请输入选项前的序号。")


def prompt_yes_no(prompt: str, *, default: bool | None = None) -> bool:
    """以是/否形式提问并返回布尔值。"""

    mapping = {"y": True, "yes": True, "n": False, "no": False}
    default_hint = None
    if default is True:
        default_hint = "y"
    elif default is False:
        default_hint = "n"

    while True:
        hint = f" [{default_hint}]" if default_hint else ""
        raw = input(f"{prompt}{hint} (y/n): ").strip().lower()
        if not raw and default is not None:
            return default
        if raw in mapping:
            return mapping[raw]
        print_warning("请输入 y/n 或者直接回车使用默认值。")
