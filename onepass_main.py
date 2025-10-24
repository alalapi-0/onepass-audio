# OnePass Audio — 顶层主程序
# 用途：提供命令行与交互式菜单，串联环境自检、素材验证、单章处理与渲染。
# 依赖：Python 3.10+（标准库）；外部脚本：scripts/env_check.py、scripts/validate_assets.py、scripts/retake_keep_last.py、scripts/edl_to_ffmpeg.py
# 用法示例：
#   python onepass_main.py setup
#   python onepass_main.py validate
#   python onepass_main.py process --json data/asr-json/001.json --original data/original_txt/001.txt --outdir out --aggr 60 --dry-run
#   python onepass_main.py render --audio data/audio/001.m4a --edl out/001.keepLast.edl.json --out out/001.clean.wav --xfade --loudnorm

"""OnePass Audio 顶层主程序。

该模块实现命令行接口与交互式菜单，封装安装依赖、素材检查、单章处理和音频渲染流程。
仅使用标准库实现，确保在 Windows + PowerShell 7 环境下可运行。
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


PROJ_ROOT = Path(__file__).resolve().parent
CONFIG_DEFAULT = PROJ_ROOT / "config" / "default_config.json"


@dataclass
class CommandResult:
    """结果对象，包含命令参数与返回码。"""

    args: List[str]
    returncode: int


def print_header(title: str) -> None:
    """Print a formatted header line for sections."""

    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def clamp_aggr(value: int) -> int:
    """Clamp aggressiveness to 0-100 range."""

    if value < 0:
        return 0
    if value > 100:
        return 100
    return value


def ensure_path_exists(path: Path, desc: str) -> bool:
    """Ensure a path exists, printing a descriptive message if missing."""

    if path.exists():
        return True
    rel_path = _rel_to_root(path)
    print(f"[错误] 未找到{desc}：{rel_path}")
    return False


def _rel_to_root(path: Path) -> Path:
    """Return path relative to project root when possible."""

    try:
        return path.resolve().relative_to(PROJ_ROOT)
    except ValueError:
        return path.resolve()


def run_cmd(argv: Iterable[str]) -> CommandResult:
    """Run a command, streaming output and returning the result."""

    args = list(argv)
    printable = " ".join(shlex.quote(arg) for arg in args)
    print(f"[命令] {printable}")
    proc = subprocess.run(args, check=False)
    return CommandResult(args=args, returncode=proc.returncode)


def check_script_exists(script_path: Path, step_hint: str) -> bool:
    """Check if a script exists, printing a hint when missing."""

    if script_path.exists():
        return True
    rel = _rel_to_root(script_path)
    print(f"[错误] 未找到脚本：{rel}，请先完成步骤 {step_hint}")
    return False


def resolve_from_root(path_str: str) -> Path:
    """Resolve a path string relative to the project root."""

    path = Path(path_str)
    if path.is_absolute():
        return path
    return (PROJ_ROOT / path).resolve()


def handle_setup() -> int:
    """Handle the setup subcommand by invoking the PowerShell installer."""

    pwsh = shutil.which("pwsh")
    if not pwsh:
        print("[错误] 未检测到 PowerShell 7 (pwsh)。请安装后重试。")
        return 2

    script = PROJ_ROOT / "scripts" / "install_deps.ps1"
    if not script.exists():
        rel = _rel_to_root(script)
        print(f"[错误] 未找到安装脚本：{rel}")
        return 2

    result = run_cmd([pwsh, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)])
    if result.returncode != 0:
        print("[提示] 如遇执行策略限制，可运行：Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force")
        return 2
    return 0


def handle_validate(args: argparse.Namespace) -> int:
    """Handle asset validation via scripts/validate_assets.py."""

    script = PROJ_ROOT / "scripts" / "validate_assets.py"
    if not check_script_exists(script, "#5"):
        return 2
    cmd = [sys.executable, str(script)]
    if getattr(args, "audio_required", False):
        cmd.append("--audio-required")
    result = run_cmd(cmd)
    return result.returncode


def _build_process_command(
    json_path: Path,
    original_path: Path,
    outdir: Path,
    aggr: int,
    config_path: Path | None,
    dry_run: bool,
) -> List[str]:
    """Construct the command list for the process script."""

    script = PROJ_ROOT / "scripts" / "retake_keep_last.py"
    cmd = [sys.executable, str(script), "--json", str(json_path), "--original", str(original_path), "--outdir", str(outdir), "--aggr", str(aggr)]
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def handle_process(args: argparse.Namespace) -> int:
    """Handle the process subcommand with path validations."""

    script = PROJ_ROOT / "scripts" / "retake_keep_last.py"
    if not check_script_exists(script, "#7"):
        return 2

    json_path = resolve_from_root(args.json)
    original_path = resolve_from_root(args.original)
    outdir = resolve_from_root(args.outdir)
    aggr = clamp_aggr(args.aggr)

    if not ensure_path_exists(json_path, " JSON 文件"):
        return 2
    if not ensure_path_exists(original_path, " 原始文本文件"):
        return 2
    if not outdir.exists():
        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[错误] 创建输出目录失败：{exc}")
            return 2

    config_path: Path | None = None
    if args.config:
        candidate = resolve_from_root(args.config)
        if candidate.exists():
            config_path = candidate
        else:
            print(f"[警告] 未找到配置文件：{_rel_to_root(candidate)}，将使用脚本默认值。")

    cmd = _build_process_command(json_path, original_path, outdir, aggr, config_path, args.dry_run)
    result = run_cmd(cmd)
    return 0 if result.returncode == 0 else 2


def handle_render(args: argparse.Namespace) -> int:
    """Handle the render subcommand invoking edl_to_ffmpeg."""

    script = PROJ_ROOT / "scripts" / "edl_to_ffmpeg.py"
    if not check_script_exists(script, "#8"):
        return 2

    audio_path = resolve_from_root(args.audio)
    edl_path = resolve_from_root(args.edl)
    out_path = resolve_from_root(args.out)

    if not ensure_path_exists(audio_path, " 原始音频文件"):
        return 2
    if not ensure_path_exists(edl_path, " EDL 文件"):
        return 2
    out_dir = out_path.parent
    if not out_dir.exists():
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[错误] 创建输出目录失败：{exc}")
            return 2

    cmd = [sys.executable, str(script), "--audio", str(audio_path), "--edl", str(edl_path), "--out", str(out_path)]
    if args.xfade:
        cmd.append("--xfade")
    if args.loudnorm:
        cmd.append("--loudnorm")

    result = run_cmd(cmd)
    return 0 if result.returncode == 0 else 2


def interactive_menu() -> int:
    """Interactive menu loop for users preferring prompts."""

    while True:
        print_header("OnePass Audio · 主菜单")
        print("1) 环境自检")
        print("2) 素材检查")
        print("3) 单章处理（去口癖 + 保留最后一遍 + 字幕/EDL/标记）")
        print("4) 仅渲染音频（按 EDL）")
        print("5) 退出")
        choice = input("选择（1-5）: ").strip()

        if choice == "1":
            script = PROJ_ROOT / "scripts" / "env_check.py"
            if not check_script_exists(script, "#4"):
                continue
            result = run_cmd([sys.executable, str(script)])
            if result.returncode != 0:
                print(f"[警告] 命令返回码：{result.returncode}")

        elif choice == "2":
            script = PROJ_ROOT / "scripts" / "validate_assets.py"
            if not check_script_exists(script, "#5"):
                continue
            result = run_cmd([sys.executable, str(script)])
            if result.returncode != 0:
                print(f"[警告] 命令返回码：{result.returncode}")

        elif choice == "3":
            script = PROJ_ROOT / "scripts" / "retake_keep_last.py"
            if not check_script_exists(script, "#7"):
                continue

            json_input = input("ASR JSON 路径 (例如 data/asr-json/001.json): ").strip()
            original_input = input("原始文本路径 (例如 data/original_txt/001.txt): ").strip()
            outdir_input = input("输出目录 [默认 out]: ").strip() or "out"
            aggr_input = input("去口癖力度 (0-100) [默认 50]: ").strip() or "50"
            dry_run_input = input("仅生成字幕/EDL/标记？(Y/N) [默认 N]: ").strip().lower()

            try:
                aggr_value = clamp_aggr(int(aggr_input))
            except ValueError:
                print("[错误] 力度必须是整数。")
                continue

            json_path = resolve_from_root(json_input)
            original_path = resolve_from_root(original_input)
            outdir = resolve_from_root(outdir_input)

            if not ensure_path_exists(json_path, " JSON 文件"):
                continue
            if not ensure_path_exists(original_path, " 原始文本文件"):
                continue
            if not outdir.exists():
                try:
                    outdir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    print(f"[错误] 创建输出目录失败：{exc}")
                    continue

            config_path: Path | None = CONFIG_DEFAULT if CONFIG_DEFAULT.exists() else None
            cmd = _build_process_command(
                json_path=json_path,
                original_path=original_path,
                outdir=outdir,
                aggr=aggr_value,
                config_path=config_path,
                dry_run=dry_run_input.startswith("y"),
            )
            result = run_cmd(cmd)
            if result.returncode != 0:
                print(f"[警告] 命令返回码：{result.returncode}")

        elif choice == "4":
            script = PROJ_ROOT / "scripts" / "edl_to_ffmpeg.py"
            if not check_script_exists(script, "#8"):
                continue

            audio_input = input("原始音频路径 (例如 data/audio/001.m4a): ").strip()
            edl_input = input("EDL 路径 (例如 out/001.keepLast.edl.json): ").strip()
            out_input = input("输出音频路径 (例如 out/001.clean.wav): ").strip()
            xfade_input = input("启用 crossfade？(Y/N) [默认 N]: ").strip().lower()
            loudnorm_input = input("启用响度归一？(Y/N) [默认 N]: ").strip().lower()

            audio_path = resolve_from_root(audio_input)
            edl_path = resolve_from_root(edl_input)
            out_path = resolve_from_root(out_input)

            if not ensure_path_exists(audio_path, " 原始音频文件"):
                continue
            if not ensure_path_exists(edl_path, " EDL 文件"):
                continue
            out_dir = out_path.parent
            if not out_dir.exists():
                try:
                    out_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    print(f"[错误] 创建输出目录失败：{exc}")
                    continue

            cmd = [
                sys.executable,
                str(script),
                "--audio",
                str(audio_path),
                "--edl",
                str(edl_path),
                "--out",
                str(out_path),
            ]
            if xfade_input.startswith("y"):
                cmd.append("--xfade")
            if loudnorm_input.startswith("y"):
                cmd.append("--loudnorm")

            result = run_cmd(cmd)
            if result.returncode != 0:
                print(f"[警告] 命令返回码：{result.returncode}")

        elif choice == "5":
            print("[信息] 用户选择退出。")
            return 1
        else:
            print("[提示] 请输入 1-5 之间的选项。")


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""

    parser = argparse.ArgumentParser(description="OnePass Audio 顶层主程序")
    subparsers = parser.add_subparsers(dest="command")

    setup_parser = subparsers.add_parser("setup", help="安装依赖（需要 PowerShell 7）")
    setup_parser.set_defaults(func=lambda _: handle_setup())

    validate_parser = subparsers.add_parser("validate", help="检查素材与配置")
    validate_parser.add_argument(
        "--audio-required",
        action="store_true",
        help="强制音频素材也必须存在",
    )
    validate_parser.set_defaults(func=handle_validate)

    process_parser = subparsers.add_parser("process", help="处理单章音频并生成字幕/EDL")
    process_parser.add_argument("--json", required=True, help="ASR JSON 文件路径")
    process_parser.add_argument("--original", required=True, help="原始文本文件路径")
    process_parser.add_argument("--outdir", default="out", help="输出目录（默认 out）")
    process_parser.add_argument("--aggr", type=int, default=50, help="去口癖力度 0-100（默认 50）")
    process_parser.add_argument("--config", default=str(CONFIG_DEFAULT), help="配置文件路径（默认 config/default_config.json）")
    process_parser.add_argument("--dry-run", action="store_true", help="仅生成字幕/EDL/标记，不渲染音频")
    process_parser.set_defaults(func=handle_process)

    render_parser = subparsers.add_parser("render", help="依据 EDL 渲染音频")
    render_parser.add_argument("--audio", required=True, help="原始音频路径")
    render_parser.add_argument("--edl", required=True, help="EDL JSON 路径")
    render_parser.add_argument("--out", required=True, help="输出音频路径")
    render_parser.add_argument("--xfade", action="store_true", help="启用 crossfade")
    render_parser.add_argument("--loudnorm", action="store_true", help="启用响度归一化")
    render_parser.set_defaults(func=handle_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entrypoint of the CLI and interactive menu."""

    parser = build_parser()
    parsed = parser.parse_args(argv)

    if parsed.command is None:
        return interactive_menu()

    func = parsed.func
    try:
        result = func(parsed)
    except KeyboardInterrupt:
        print("\n[信息] 用户取消操作。")
        return 1
    return result


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
