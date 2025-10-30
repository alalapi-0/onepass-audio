"""Interactive helper to normalise original transcript texts."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from onepass import __version__
from onepass.ux import (
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
NORMALISE_SCRIPT = ROOT_DIR / "scripts" / "normalize_texts.py"
SOURCE_DIR = ROOT_DIR / "data" / "original_txt"
REPORT_PATH = ROOT_DIR / "out" / "textnorm_report.md"


def _print_banner() -> None:
    print_header("OnePass Audio — 录完即净，一遍过")
    print_info(f"版本: {__version__}")
    print_info("原文文本规范化工具。\n")


def _invoke_text_normalisation(dry_run: bool) -> None:
    """Invoke the text normalisation script with preset arguments."""

    if not NORMALISE_SCRIPT.is_file():
        print_error(f"未找到文本规范化脚本: {NORMALISE_SCRIPT}")
        return

    if not SOURCE_DIR.exists():
        print_warning(f"源目录不存在，已自动创建: {SOURCE_DIR}")
        SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    if not SOURCE_DIR.is_dir():
        print_error(f"源目录路径不是文件夹: {SOURCE_DIR}")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(NORMALISE_SCRIPT),
        "--src",
        str(SOURCE_DIR),
        "--inplace",
        "--report",
        str(REPORT_PATH),
        "--punct",
        "ascii",
        "--t2s",
    ]
    if dry_run:
        cmd.append("--dry-run")

    print_info("正在调用文本规范化脚本…")
    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))
    except FileNotFoundError as exc:
        print_error(f"无法执行文本规范化脚本: {exc}")
        return

    if result.returncode == 0:
        if dry_run:
            print_success(f"Dry-Run 完成，报告已生成: {REPORT_PATH}")
            print_info("请先检查报告再确认是否写回。")
        else:
            print_success(f"文本规范化完成，报告已生成: {REPORT_PATH}")
    elif result.returncode == 1:
        print_warning(f"脚本报告未检测到可规范化的内容。报告: {REPORT_PATH}")
    else:
        print_error("文本规范化脚本执行失败，请查看上方输出。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="原文文本规范化工具（默认 dry-run，使用 --write 直接写回）"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="执行规范化并写回源文件，而非 dry-run。",
    )
    args = parser.parse_args()

    _print_banner()
    print_header("预处理：原文规范化（NFKC + 兼容字清洗）")
    print_info("10) 预处理：原文规范化（NFKC + 兼容字清洗）")
    if args.write:
        print_warning("将直接执行原文规范化，并写回源文件。")
    else:
        print_info("将直接执行原文规范化（默认 dry-run）。")

    _invoke_text_normalisation(dry_run=not args.write)


if __name__ == "__main__":
    main()
