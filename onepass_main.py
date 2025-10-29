# OnePass Audio — 顶层主程序
# 用途：提供命令行与交互式菜单，串联环境自检、素材验证、单章处理与渲染。
# 依赖：Python 3.10+（标准库）；内部模块 onepass.ux，脚本位于 scripts/ 目录。
# 用法示例：
#   python onepass_main.py setup
#   python onepass_main.py validate --audio-required
#   python onepass_main.py process --json data/asr-json/001.json --original data/original_txt/001.txt --outdir out
#   python onepass_main.py render --audio data/audio/001.m4a --edl out/001.keepLast.edl.json --out out/001.clean.wav

"""OnePass Audio 顶层主程序。

该模块实现命令行接口与交互式菜单，封装安装依赖、素材检查、单章处理和音频渲染流程。
仅使用标准库实现，确保在 Windows + PowerShell 7 环境下可运行。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable, List

from onepass.ux import (
    Spinner,
    enable_ansi,
    format_cmd,
    log_err,
    log_info,
    log_ok,
    log_warn,
    run_streamed,
    section,
)

PROJ_ROOT = Path(__file__).resolve().parent
CONFIG_DEFAULT = PROJ_ROOT / "config" / "default_config.json"


def _determine_verbose(args: argparse.Namespace) -> bool:
    env_verbose = os.environ.get("ONEPASS_VERBOSE", "1") != "0"
    if getattr(args, "quiet", False):
        return False
    if getattr(args, "verbose", False):
        return True
    return env_verbose


def _rel_to_root(path: Path) -> Path:
    try:
        return path.resolve().relative_to(PROJ_ROOT)
    except ValueError:
        return path.resolve()


def _ensure_path_exists(path: Path, desc: str) -> bool:
    if path.exists():
        return True
    log_err(f"未找到{desc}：{_rel_to_root(path)}")
    return False


def _check_script_exists(script_path: Path, step_hint: str) -> bool:
    if script_path.exists():
        return True
    log_err(f"未找到脚本：{_rel_to_root(script_path)}，请先完成步骤 {step_hint}")
    return False


def _print_command(cmd: Iterable[str]) -> None:
    log_info(f"将要执行的命令：{format_cmd(list(cmd))}")


def _make_common_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    group = parent.add_mutually_exclusive_group()
    group.add_argument("--verbose", action="store_true", help="强制打印详细日志")
    group.add_argument("--quiet", action="store_true", help="关闭非必要日志")
    return parent


def handle_setup(args: argparse.Namespace) -> int:
    section("安装依赖")
    pwsh = shutil.which("pwsh")
    if not pwsh:
        log_err("未检测到 PowerShell 7 (pwsh)。请安装后重试。")
        return 2
    script = PROJ_ROOT / "scripts" / "install_deps.ps1"
    if not _check_script_exists(script, "#1"):
        return 2
    cmd = [pwsh, "-File", str(script)]
    _print_command(cmd)
    start = time.monotonic()
    rc = run_streamed(cmd, heartbeat_s=30.0, show_cmd=False)
    elapsed = time.monotonic() - start
    if rc == 0:
        log_ok(f"完成，耗时 {elapsed:.1f}s，返回码 {rc}")
        return 0
    log_err(f"命令失败，耗时 {elapsed:.1f}s，返回码 {rc}")
    log_warn("如遇执行策略限制，可运行：Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force")
    return 2


def handle_validate(args: argparse.Namespace) -> int:
    section("素材检查")
    script = PROJ_ROOT / "scripts" / "validate_assets.py"
    if not _check_script_exists(script, "#5"):
        return 2
    cmd: List[str] = [sys.executable, str(script)]
    if getattr(args, "audio_required", False):
        cmd.append("--audio-required")
    _print_command(cmd)
    start = time.monotonic()
    rc = run_streamed(cmd, heartbeat_s=30.0, show_cmd=False)
    elapsed = time.monotonic() - start
    if rc == 0:
        log_ok(f"完成，耗时 {elapsed:.1f}s，返回码 {rc}")
        return 0
    log_err(f"素材检查失败，耗时 {elapsed:.1f}s，返回码 {rc}")
    return 2


def _build_process_command(
    json_path: Path,
    original_path: Path,
    outdir: Path,
    aggr: int,
    config_path: Path | None,
    dry_run: bool,
    verbose_flag: bool,
) -> List[str]:
    script = PROJ_ROOT / "scripts" / "retake_keep_last.py"
    cmd = [
        sys.executable,
        str(script),
        "--json",
        str(json_path),
        "--original",
        str(original_path),
        "--outdir",
        str(outdir),
        "--aggr",
        str(aggr),
    ]
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    if dry_run:
        cmd.append("--dry-run")
    if not verbose_flag:
        cmd.append("--quiet")
    return cmd


def handle_process(args: argparse.Namespace) -> int:
    verbose_flag = _determine_verbose(args)
    section("单章处理")
    script = PROJ_ROOT / "scripts" / "retake_keep_last.py"
    if not _check_script_exists(script, "#7"):
        return 2

    json_path = Path(args.json).expanduser()
    original_path = Path(args.original).expanduser()
    outdir = Path(args.outdir).expanduser()
    if not json_path.is_absolute():
        json_path = (PROJ_ROOT / json_path).resolve()
    if not original_path.is_absolute():
        original_path = (PROJ_ROOT / original_path).resolve()
    if not outdir.is_absolute():
        outdir = (PROJ_ROOT / outdir).resolve()

    summary = [
        ("stem", json_path.stem),
        ("json", str(_rel_to_root(json_path))),
        ("original", str(_rel_to_root(original_path))),
        ("outdir", str(_rel_to_root(outdir))),
        ("aggr", str(args.aggr)),
        ("config", args.config or "默认"),
        ("dry-run", "是" if args.dry_run else "否"),
    ]
    for key, value in summary:
        log_info(f"{key:>9s}: {value}")

    spinner = Spinner()
    spinner.start("准备参数…")
    if not _ensure_path_exists(json_path, " JSON 文件"):
        spinner.stop_err("缺少 JSON 文件")
        return 2
    if not _ensure_path_exists(original_path, " 原始文本文件"):
        spinner.stop_err("缺少原始文本")
        return 2
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        spinner.stop_err(f"创建输出目录失败：{exc}")
        return 2
    spinner.update("启动处理…")
    spinner.stop_ok("参数准备完成")

    config_path: Path | None = None
    if args.config:
        candidate = Path(args.config).expanduser()
        if not candidate.is_absolute():
            candidate = (PROJ_ROOT / candidate).resolve()
        if candidate.exists():
            config_path = candidate
        else:
            log_warn(f"未找到配置文件：{_rel_to_root(candidate)}，将使用脚本默认值。")

    aggr = max(0, min(100, args.aggr))
    cmd = _build_process_command(json_path, original_path, outdir, aggr, config_path, args.dry_run, verbose_flag)
    _print_command(cmd)
    start = time.monotonic()
    rc = run_streamed(cmd, heartbeat_s=45.0, show_cmd=False)
    elapsed = time.monotonic() - start
    if rc == 0:
        log_ok(f"完成，耗时 {elapsed:.1f}s，返回码 {rc}")
        return 0
    log_err(f"处理失败，耗时 {elapsed:.1f}s，返回码 {rc}")
    return 2


def handle_render(args: argparse.Namespace) -> int:
    verbose_flag = _determine_verbose(args)
    section("音频渲染")
    script = PROJ_ROOT / "scripts" / "edl_to_ffmpeg.py"
    if not _check_script_exists(script, "#8"):
        return 2

    audio_path = Path(args.audio).expanduser()
    edl_path = Path(args.edl).expanduser()
    out_path = Path(args.out).expanduser()
    if not audio_path.is_absolute():
        audio_path = (PROJ_ROOT / audio_path).resolve()
    if not edl_path.is_absolute():
        edl_path = (PROJ_ROOT / edl_path).resolve()
    if not out_path.is_absolute():
        out_path = (PROJ_ROOT / out_path).resolve()

    summary = [
        ("audio", str(_rel_to_root(audio_path))),
        ("edl", str(_rel_to_root(edl_path))),
        ("out", str(_rel_to_root(out_path))),
        ("xfade", "是" if args.xfade else "否"),
        ("loudnorm", "是" if args.loudnorm else "否"),
    ]
    for key, value in summary:
        log_info(f"{key:>9s}: {value}")

    spinner = Spinner()
    spinner.start("检查输入…")
    if not _ensure_path_exists(audio_path, " 原始音频文件"):
        spinner.stop_err("缺少音频文件")
        return 2
    if not _ensure_path_exists(edl_path, " EDL 文件"):
        spinner.stop_err("缺少 EDL 文件")
        return 2
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        spinner.stop_err(f"创建输出目录失败：{exc}")
        return 2
    spinner.update("启动渲染…")
    spinner.stop_ok("渲染即将开始")

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
    if args.xfade:
        cmd.append("--xfade")
    if args.loudnorm:
        cmd.append("--loudnorm")
    if not verbose_flag:
        cmd.append("--quiet")
    _print_command(cmd)
    start = time.monotonic()
    rc = run_streamed(cmd, heartbeat_s=30.0, show_cmd=False)
    elapsed = time.monotonic() - start
    if rc == 0:
        log_ok(f"完成，耗时 {elapsed:.1f}s，返回码 {rc}")
        return 0
    log_err(f"渲染失败，耗时 {elapsed:.1f}s，返回码 {rc}")
    return 2


def handle_asr(args: argparse.Namespace) -> int:
    verbose_flag = _determine_verbose(args)
    section("批量转写")
    script = PROJ_ROOT / "scripts" / "asr_batch.py"
    if not _check_script_exists(script, "#11"):
        return 2

    audio_dir = Path(args.audio_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    if not audio_dir.is_absolute():
        audio_dir = (PROJ_ROOT / audio_dir).resolve()
    if not out_dir.is_absolute():
        out_dir = (PROJ_ROOT / out_dir).resolve()

    summary = [
        ("audio", str(_rel_to_root(audio_dir))),
        ("out", str(_rel_to_root(out_dir))),
        ("model", args.model),
        ("language", args.language),
        ("device", args.device),
        ("compute", args.compute_type),
        ("workers", str(args.workers)),
        ("vad", "开" if args.vad else "关"),
        ("overwrite", "是" if args.overwrite else "否"),
        ("dry-run", "是" if args.dry_run else "否"),
    ]
    for key, value in summary:
        log_info(f"{key:>9s}: {value}")

    cmd = [
        sys.executable,
        str(script),
        "--audio-dir",
        str(audio_dir),
        "--out-dir",
        str(out_dir),
        "--model",
        args.model,
        "--language",
        args.language,
        "--device",
        args.device,
        "--compute-type",
        args.compute_type,
        "--workers",
        str(args.workers),
    ]
    if not args.vad:
        cmd.append("--no-vad")
    if args.overwrite:
        cmd.append("--overwrite")
    if args.dry_run:
        cmd.append("--dry-run")
    if not verbose_flag:
        cmd.append("--quiet")
    _print_command(cmd)
    start = time.monotonic()
    rc = run_streamed(cmd, heartbeat_s=45.0, show_cmd=False)
    elapsed = time.monotonic() - start
    if rc == 0:
        log_ok(f"完成，耗时 {elapsed:.1f}s，返回码 {rc}")
        return 0
    log_err(f"转写失败，耗时 {elapsed:.1f}s，返回码 {rc}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OnePass Audio 顶层主程序")
    parent = _make_common_parent()
    subparsers = parser.add_subparsers(dest="command")

    setup_parser = subparsers.add_parser("setup", parents=[parent], help="安装依赖（需要 PowerShell 7）")
    setup_parser.set_defaults(func=handle_setup)

    validate_parser = subparsers.add_parser("validate", parents=[parent], help="检查素材与配置")
    validate_parser.add_argument("--audio-required", action="store_true", help="强制音频素材也必须存在")
    validate_parser.set_defaults(func=handle_validate)

    asr_parser = subparsers.add_parser("asr", parents=[parent], help="批量转写音频生成 ASR JSON")
    asr_parser.add_argument("--audio-dir", default="data/audio", help="音频目录（默认 data/audio）")
    asr_parser.add_argument("--out-dir", default="data/asr-json", help="输出目录（默认 data/asr-json）")
    asr_parser.add_argument("--model", default="small", help="whisper-ctranslate2 模型（默认 small）")
    asr_parser.add_argument("--language", default="zh", help="转写语言（默认 zh，可设 auto）")
    asr_parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="推理设备：auto|cpu|cuda（默认 auto）",
    )
    asr_parser.add_argument("--compute-type", default="auto", help="compute_type 参数（默认 auto）")
    asr_parser.add_argument("--workers", type=int, default=1, help="并发数量（默认 1）")
    asr_parser.add_argument("--vad", dest="vad", action="store_true", default=True, help="启用 VAD（默认）")
    asr_parser.add_argument("--no-vad", dest="vad", action="store_false", help="禁用 VAD")
    asr_parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的 JSON")
    asr_parser.add_argument("--dry-run", action="store_true", help="仅打印命令不执行")
    asr_parser.set_defaults(func=handle_asr)

    process_parser = subparsers.add_parser("process", parents=[parent], help="处理单章音频并生成字幕/EDL")
    process_parser.add_argument("--json", required=True, help="ASR JSON 文件路径")
    process_parser.add_argument("--original", required=True, help="原始文本文件路径")
    process_parser.add_argument("--outdir", default="out", help="输出目录（默认 out）")
    process_parser.add_argument("--aggr", type=int, default=50, help="去口癖力度 0-100（默认 50）")
    process_parser.add_argument("--config", default=str(CONFIG_DEFAULT), help="配置文件路径（默认 config/default_config.json）")
    process_parser.add_argument("--dry-run", action="store_true", help="仅生成字幕/EDL/记，不渲染音频")
    process_parser.set_defaults(func=handle_process)

    render_parser = subparsers.add_parser("render", parents=[parent], help="依据 EDL 渲染音频")
    render_parser.add_argument("--audio", required=True, help="原始音频路径")
    render_parser.add_argument("--edl", required=True, help="EDL JSON 路径")
    render_parser.add_argument("--out", required=True, help="输出音频路径")
    render_parser.add_argument("--xfade", action="store_true", help="启用 crossfade")
    render_parser.add_argument("--loudnorm", action="store_true", help="启用响度归一化")
    render_parser.set_defaults(func=handle_render)

    return parser


def interactive_menu() -> int:
    section("OnePass Audio · 主菜单")
    while True:
        print("0) 批量转写音频 → 生成 ASR JSON")
        print("1) 环境自检")
        print("2) 素材检查")
        print("3) 单章处理（去口癖 + 保留最后一遍 + 字幕/EDL/标记）")
        print("4) 仅渲染音频（按 EDL）")
        print("5) 退出")
        choice = input("选择（0-5）: ").strip()
        if choice == "5":
            log_warn("用户选择退出。")
            return 1
        log_warn("交互式菜单暂未实现自动执行，请使用命令行子命令。")


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    parser = build_parser()
    parsed = parser.parse_args(argv)
    if parsed.command is None:
        return interactive_menu()
    func = parsed.func
    try:
        return func(parsed)
    except KeyboardInterrupt:
        log_warn("用户取消操作。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
