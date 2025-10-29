"""User experience helpers for the OnePass Audio CLI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

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
    """ANSI escape sequences used to colourise terminal output."""

    prefix: str
    suffix: str = "\x1b[0m"

    def apply(self, message: str) -> str:
        return f"{self.prefix}{message}{self.suffix}"


STYLE_HEADER = AnsiStyle("\x1b[95m")
STYLE_INFO = AnsiStyle("\x1b[94m")
STYLE_SUCCESS = AnsiStyle("\x1b[92m")
STYLE_WARNING = AnsiStyle("\x1b[93m")
STYLE_ERROR = AnsiStyle("\x1b[91m")


def _supports_colour() -> bool:
    """Return True when the active terminal most likely supports colour."""

    try:
        import sys

        return sys.stdout.isatty()
    except Exception:  # pragma: no cover - extremely defensive
        return False


def _colourise(message: str, style: AnsiStyle) -> str:
    if not message:
        return message
    if not _supports_colour():
        return message
    return style.apply(message)


def print_header(title: str) -> None:
    """Print a stylised section header."""

    border = "=" * len(title)
    print(_colourise(border, STYLE_HEADER))
    print(_colourise(title, STYLE_HEADER))
    print(_colourise(border, STYLE_HEADER))


def _print_with_style(message: str, style: AnsiStyle) -> None:
    print(_colourise(message, style))


def print_info(message: str) -> None:
    _print_with_style(message, STYLE_INFO)


def print_success(message: str) -> None:
    _print_with_style(message, STYLE_SUCCESS)


def print_warning(message: str) -> None:
    _print_with_style(message, STYLE_WARNING)


def print_error(message: str) -> None:
    _print_with_style(message, STYLE_ERROR)


def prompt_text(prompt: str, *, default: str | None = None, allow_empty: bool = False) -> str:
    """Prompt the user for a free-form string value."""

    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value or allow_empty:
            return value
        print_warning("输入不能为空，请重新输入。")


def _validate_path(path: Path, *, should_exist: bool, path_type: str) -> Path:
    if should_exist and not path.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    if should_exist and path_type == "file" and not path.is_file():
        raise FileNotFoundError(f"不是有效的文件: {path}")
    if should_exist and path_type == "directory" and not path.is_dir():
        raise FileNotFoundError(f"不是有效的目录: {path}")
    return path


def prompt_existing_file(prompt: str, *, default: Path | None = None) -> Path:
    """Prompt for a path that must resolve to an existing file."""

    while True:
        raw = prompt_text(prompt, default=str(default) if default else None)
        path = Path(raw).expanduser().resolve()
        try:
            return _validate_path(path, should_exist=True, path_type="file")
        except FileNotFoundError as exc:
            print_error(str(exc))


def prompt_existing_directory(prompt: str, *, default: Path | None = None) -> Path:
    """Prompt for a path that must resolve to an existing directory."""

    while True:
        raw = prompt_text(prompt, default=str(default) if default else None)
        path = Path(raw).expanduser().resolve()
        try:
            return _validate_path(path, should_exist=True, path_type="directory")
        except FileNotFoundError as exc:
            print_error(str(exc))


def prompt_choice(prompt: str, options: Sequence[str], *, default: int | None = None) -> str:
    """Prompt the user to choose from *options* by index."""

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
    """Prompt the user with a yes/no question."""

    mapping = {"y": True, "yes": True, "n": False, "no": False}
    default_hint = None
    if default is True:
        default_hint = "y"
    elif default is False:
        default_hint = "n"

    while True:
        suffix = f" [{default_hint}]" if default_hint else ""
        value = input(f"{prompt}{suffix}: ").strip().lower()
        if not value and default is not None:
            return default
        if value in mapping:
            return mapping[value]
        print_warning("请输入 y/yes 或 n/no。")
