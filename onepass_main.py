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


def _prompt(text: str, default: str | None = None) -> str:
    if default:
        prompt = f"{text} [{default}]: "
    else:
        prompt = f"{text}: "
    return input(prompt).strip() or (default or "")


def _prompt_bool(text: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        resp = input(f"{text} ({suffix}): ").strip().lower()
        if not resp:
            return default
        if resp in {"y", "yes"}:
            return True
        if resp in {"n", "no"}:
            return False
        print("请输入 y 或 n。")


def _prompt_int(text: str, default: int) -> int:
    while True:
        raw = _prompt(text, str(default))
        try:
            return int(raw)
        except ValueError:
            print("请输入整数。")


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
    regen: bool = False,
    hard_delete: bool = False,
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
    if regen:
        cmd.append("--regen")
        if hard_delete:
            cmd.append("--hard-delete")
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


def handle_clean(args: argparse.Namespace) -> int:
    section("清理产物")
    script = PROJ_ROOT / "scripts" / "clean_outputs.py"
    if not _check_script_exists(script, "#7"):
        return 2

    cmd: List[str] = [sys.executable, str(script)]
    if getattr(args, "all", False):
        cmd.append("--all")
    else:
        stems = getattr(args, "stem", None) or []
        cmd.append("--stem")
        cmd.extend(stems)
    if args.what:
        cmd.extend(["--what", args.what])
    if args.hard:
        cmd.append("--hard")
    elif args.trash:
        cmd.append("--trash")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.yes:
        cmd.append("--yes")

    _print_command(cmd)
    start = time.monotonic()
    rc = run_streamed(cmd, heartbeat_s=15.0, show_cmd=False)
    elapsed = time.monotonic() - start
    if rc == 0:
        log_ok(f"完成，耗时 {elapsed:.1f}s，返回码 {rc}")
        return 0
    if rc == 1:
        log_warn(f"未找到可清理的文件，耗时 {elapsed:.1f}s。")
        return 1
    log_err(f"清理失败，耗时 {elapsed:.1f}s，返回码 {rc}")
    return 2


def handle_regen(args: argparse.Namespace) -> int:
    verbose_flag = _determine_verbose(args)
    section("重新生成")
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
        ("hard-delete", "是" if args.hard_delete else "否"),
    ]
    for key, value in summary:
        log_info(f"{key:>9s}: {value}")

    if not _ensure_path_exists(json_path, " JSON 文件"):
        return 2
    if not _ensure_path_exists(original_path, " 原始文本文件"):
        return 2
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log_err(f"创建输出目录失败：{exc}")
        return 2

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
    cmd = _build_process_command(
        json_path,
        original_path,
        outdir,
        aggr,
        config_path,
        args.dry_run,
        verbose_flag,
        regen=True,
        hard_delete=args.hard_delete,
    )
    _print_command(cmd)
    start = time.monotonic()
    rc = run_streamed(cmd, heartbeat_s=45.0, show_cmd=False)
    elapsed = time.monotonic() - start
    if rc == 0:
        log_ok(f"完成，耗时 {elapsed:.1f}s，返回码 {rc}")
        return 0
    if rc == 1:
        log_warn(f"操作以返回码 1 结束，耗时 {elapsed:.1f}s。")
        return 1
    log_err(f"重新生成失败，耗时 {elapsed:.1f}s，返回码 {rc}")
    return 2


def handle_batch(args: argparse.Namespace) -> int:
    section("批量生成")
    if args.hard_delete and not args.regen:
        log_err("--hard-delete 需搭配 --regen 使用。")
        return 2
    script = PROJ_ROOT / "scripts" / "bulk_process.ps1"
    if not _check_script_exists(script, "#10"):
        return 2

    pwsh = shutil.which("pwsh")
    if not pwsh:
        log_err("未检测到 PowerShell 7 (pwsh)。请安装后重试，或直接调用 scripts/bulk_process.ps1。")
        return 2

    aggr = max(0, min(100, args.aggr))
    cmd: List[str] = [
        pwsh,
        "-File",
        str(script),
        "-Aggressiveness",
        str(aggr),
    ]
    if args.render:
        cmd.append("-Render")
    if args.regen:
        cmd.append("-Regen")
    if args.hard_delete:
        cmd.append("-HardDelete")
    if args.dry_run:
        cmd.append("-DryRun")
    if args.audio_required:
        cmd.append("-AudioRequired")
    if args.audio_pattern:
        cmd.extend(["-AudioExtPattern", args.audio_pattern])
    if args.config:
        config_path = Path(args.config).expanduser()
        if not config_path.is_absolute():
            config_path = (PROJ_ROOT / config_path).resolve()
        cmd.extend(["-Config", str(config_path)])
    if args.auto_asr:
        cmd.append("-AutoASR")
    if args.asr_model:
        cmd.extend(["-AsrModel", args.asr_model])
    if args.asr_device:
        cmd.extend(["-AsrDevice", args.asr_device])
    if args.asr_language:
        cmd.extend(["-AsrLanguage", args.asr_language])
    if args.asr_compute_type:
        cmd.extend(["-AsrComputeType", args.asr_compute_type])
    if args.asr_workers is not None:
        cmd.extend(["-AsrWorkers", str(args.asr_workers)])
    if args.asr_no_vad:
        cmd.append("-AsrNoVad")
    if args.asr_overwrite:
        cmd.append("-AsrOverwrite")
    if args.asr_dry_run:
        cmd.append("-AsrDryRun")

    _print_command(cmd)
    start = time.monotonic()
    rc = run_streamed(cmd, heartbeat_s=60.0, show_cmd=False)
    elapsed = time.monotonic() - start
    if rc == 0:
        log_ok(f"完成，耗时 {elapsed:.1f}s，返回码 {rc}")
        return 0
    if rc == 1:
        log_warn(f"批量任务部分成功（exit 1），耗时 {elapsed:.1f}s。")
        return 1
    log_err(f"批量任务失败，耗时 {elapsed:.1f}s，返回码 {rc}")
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

    clean_parser = subparsers.add_parser("clean", parents=[parent], help="清理 out/ 下旧产物")
    clean_group = clean_parser.add_mutually_exclusive_group(required=True)
    clean_group.add_argument("--stem", nargs="+", help="指定要清理的章节 stem，可多个")
    clean_group.add_argument("--all", action="store_true", help="清理全部章节产物")
    clean_parser.add_argument(
        "--what",
        default="generated",
        help="清理范围，逗号分隔：generated|subs|edl|markers|logs|render|all（默认 generated）",
    )
    action_group = clean_parser.add_mutually_exclusive_group()
    action_group.add_argument("--trash", action="store_true", help="移动到 out/.trash/（默认）")
    action_group.add_argument("--hard", action="store_true", help="直接删除（危险）")
    clean_parser.add_argument("--dry-run", action="store_true", help="仅预览不执行")
    clean_parser.add_argument("--yes", action="store_true", help="自动确认")
    clean_parser.set_defaults(func=handle_clean)

    regen_parser = subparsers.add_parser("regen", parents=[parent], help="清理旧产物后重新生成一章")
    regen_parser.add_argument("--json", required=True, help="ASR JSON 文件路径")
    regen_parser.add_argument("--original", required=True, help="原始文本文件路径")
    regen_parser.add_argument("--outdir", default="out", help="输出目录（默认 out）")
    regen_parser.add_argument("--aggr", type=int, default=50, help="去口癖力度 0-100（默认 50）")
    regen_parser.add_argument("--config", default=str(CONFIG_DEFAULT), help="配置文件路径")
    regen_parser.add_argument("--dry-run", action="store_true", help="仅生成文本类产物，不渲染音频")
    regen_parser.add_argument("--hard-delete", action="store_true", help="搭配 --regen，直接删除旧产物")
    regen_parser.set_defaults(func=handle_regen)

    batch_parser = subparsers.add_parser("batch", parents=[parent], help="批量遍历全部章节")
    batch_parser.add_argument("--aggr", type=int, default=50, help="去口癖力度 0-100（默认 50）")
    batch_parser.add_argument("--config", default=str(CONFIG_DEFAULT), help="配置文件路径")
    batch_parser.add_argument("--render", action="store_true", help="批量渲染清洁音频")
    batch_parser.add_argument("--regen", action="store_true", help="在每章处理前清理旧产物")
    batch_parser.add_argument("--hard-delete", action="store_true", help="搭配 --regen，直接删除旧产物")
    batch_parser.add_argument("--dry-run", action="store_true", help="仅运行干跑模式")
    batch_parser.add_argument("--audio-required", action="store_true", help="缺少音频即判 FAIL")
    batch_parser.add_argument(
        "--audio-pattern",
        default="*.m4a,*.wav,*.mp3,*.flac",
        help="音频文件匹配模式，逗号分隔",
    )
    batch_parser.add_argument("--auto-asr", action="store_true", help="缺少 JSON 时自动转写音频")
    batch_parser.add_argument("--asr-model", help="AutoASR 使用的模型名称")
    batch_parser.add_argument("--asr-device", help="AutoASR 推理设备")
    batch_parser.add_argument("--asr-language", help="AutoASR 语言参数")
    batch_parser.add_argument("--asr-compute-type", help="AutoASR compute_type 参数")
    batch_parser.add_argument("--asr-workers", type=int, help="AutoASR 并发数量")
    batch_parser.add_argument("--asr-no-vad", action="store_true", help="AutoASR 禁用 VAD")
    batch_parser.add_argument("--asr-overwrite", action="store_true", help="AutoASR 覆盖已存在文件")
    batch_parser.add_argument("--asr-dry-run", action="store_true", help="AutoASR 仅打印命令")
    batch_parser.set_defaults(func=handle_batch)

    return parser


def _interactive_clean() -> int:
    stems_input = _prompt("输入要清理的章节 stem（逗号分隔，留空为全部）", "")
    if stems_input:
        stems = [item.strip() for item in stems_input.split(",") if item.strip()]
        use_all = False
        if not stems:
            log_warn("未输入有效 stem，默认清理全部。")
            use_all = True
    else:
        stems = []
        use_all = True
    what = _prompt("清理范围 (generated|subs|edl|markers|logs|render|all)", "generated")
    hard = _prompt_bool("是否直接删除（跳过 .trash/）?", False)
    dry_run = _prompt_bool("仅预览 (dry-run)?", False)
    if not _prompt_bool("确认开始执行？", True):
        log_warn("已取消清理。")
        return 1
    args = argparse.Namespace(
        stem=stems,
        all=use_all,
        what=what,
        hard=hard,
        trash=not hard,
        dry_run=dry_run,
        yes=False,
        verbose=False,
        quiet=False,
    )
    return handle_clean(args)


def _interactive_regen() -> int:
    json_default = "data/asr-json/001.json"
    json_path = _prompt("ASR JSON 路径", json_default)
    stem = Path(json_path).stem
    original_default = f"data/original_txt/{stem}.txt" if stem else "data/original_txt/001.txt"
    original_path = _prompt("原文 TXT 路径", original_default)
    outdir = _prompt("输出目录", "out")
    aggr = _prompt_int("去口癖力度 (0-100)", 50)
    config_input = _prompt("配置文件路径（留空使用默认）", str(CONFIG_DEFAULT))
    if not config_input:
        config_input = str(CONFIG_DEFAULT)
    dry_run = _prompt_bool("仅生成字幕/EDL（dry-run）?", False)
    hard_delete = _prompt_bool("清理时直接删除（慎用）?", False)
    if not _prompt_bool("确认重新生成？", True):
        log_warn("已取消重新生成。")
        return 1
    args = argparse.Namespace(
        json=json_path,
        original=original_path,
        outdir=outdir,
        aggr=aggr,
        config=config_input,
        dry_run=dry_run,
        hard_delete=hard_delete,
        verbose=False,
        quiet=False,
    )
    return handle_regen(args)


def _interactive_batch() -> int:
    aggr = _prompt_int("去口癖力度 (0-100)", 50)
    render = _prompt_bool("处理完毕后渲染清洁音频?", False)
    regen = _prompt_bool("每章开跑前先清理旧产物?", False)
    hard_delete = False
    if regen:
        hard_delete = _prompt_bool("改为硬删除旧产物（慎用）?", False)
    dry_run = _prompt_bool("仅 dry-run（不写文件）?", False)
    audio_required = _prompt_bool("缺少音频即判 FAIL?", False)
    config_input = _prompt("配置文件路径（留空使用默认）", str(CONFIG_DEFAULT))
    if not config_input:
        config_input = str(CONFIG_DEFAULT)
    audio_pattern = _prompt("音频匹配模式", "*.m4a,*.wav,*.mp3,*.flac")
    auto_asr = _prompt_bool("缺少 JSON 时自动执行 ASR?", False)
    asr_model = asr_device = asr_language = asr_compute = None
    asr_workers = None
    asr_no_vad = asr_overwrite = asr_dry_run = False
    if auto_asr:
        model_input = _prompt("AutoASR 模型（留空保持默认）", "")
        asr_model = model_input or None
        device_input = _prompt("AutoASR 设备（auto/cpu/cuda）", "")
        asr_device = device_input or None
        lang_input = _prompt("AutoASR 语言（留空保持默认）", "")
        asr_language = lang_input or None
        compute_input = _prompt("AutoASR compute_type（留空保持默认）", "")
        asr_compute = compute_input or None
        workers_input = _prompt("AutoASR 并发数量（留空保持默认）", "")
        if workers_input:
            try:
                asr_workers = int(workers_input)
            except ValueError:
                log_warn("并发数量无效，保持默认。")
                asr_workers = None
        asr_no_vad = _prompt_bool("禁用 VAD?", False)
        asr_overwrite = _prompt_bool("覆盖已存在 JSON?", False)
        asr_dry_run = _prompt_bool("AutoASR 仅打印命令?", False)
    if not _prompt_bool("确认批量执行？", True):
        log_warn("已取消批量执行。")
        return 1
    args = argparse.Namespace(
        aggr=aggr,
        config=config_input,
        render=render,
        regen=regen,
        hard_delete=hard_delete,
        dry_run=dry_run,
        audio_required=audio_required,
        audio_pattern=audio_pattern,
        auto_asr=auto_asr,
        asr_model=asr_model,
        asr_device=asr_device,
        asr_language=asr_language,
        asr_compute_type=asr_compute,
        asr_workers=asr_workers,
        asr_no_vad=asr_no_vad,
        asr_overwrite=asr_overwrite,
        asr_dry_run=asr_dry_run,
        verbose=False,
        quiet=False,
    )
    return handle_batch(args)


def interactive_menu() -> int:
    section("OnePass Audio · 主菜单")
    while True:
        print("0) 批量转写音频 → 生成 ASR JSON")
        print("1) 环境自检")
        print("2) 素材检查")
        print("3) 单章处理（去口癖 + 保留最后一遍 + 字幕/EDL/标记）")
        print("4) 仅渲染音频（按 EDL）")
        print("5) 退出")
        print("6) 重新生成（清理旧产物后重跑一章）")
        print("7) 批量生成（遍历全部章节）")
        print("8) 清理产物（按 stem 或全部）")
        choice = input("选择（0-8）: ").strip()
        if choice == "5":
            log_warn("用户选择退出。")
            return 1
        if choice == "6":
            _interactive_regen()
            continue
        if choice == "7":
            _interactive_batch()
            continue
        if choice == "8":
            _interactive_clean()
            continue
        log_warn("该选项尚未在菜单中实现，请使用命令行子命令。")


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
