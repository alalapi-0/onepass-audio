"""scripts.deploy_cli
用途：为 OnePass Audio 提供统一的部署 CLI，封装 provision/upload/run_asr/fetch/status 流程。
依赖：Python 标准库 argparse、pathlib、sys；内部模块 ``onepass.deploy_api``、``onepass.ux``。
示例：
  python scripts/deploy_cli.py upload_audio --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from onepass.deploy_api import (
    get_current_provider_name,
    get_provider,
    load_provider_config,
    set_current_provider,
)
from onepass.ux import enable_ansi, log_err, log_info, log_ok, log_warn

PROJ_ROOT = Path(__file__).resolve().parent.parent


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="仅打印命令，不实际执行")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OnePass Audio 部署 CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.required = True

    provision = subparsers.add_parser("provision", help="远程初始化或准备环境")
    _add_common_options(provision)

    upload = subparsers.add_parser("upload_audio", help="同步 data/audio/ 到远端")
    _add_common_options(upload)
    upload.add_argument("--no-delete", action="store_true", help="禁用远端多余文件删除")

    run = subparsers.add_parser("run_asr", help="在远端运行 scripts/asr_batch.py")
    run.add_argument("--pattern", help="音频匹配模式", default=None)
    run.add_argument("--model", help="whisper 模型", default=None)
    run.add_argument("--language", help="识别语言", default=None)
    run.add_argument("--device", help="推理设备", default=None)
    run.add_argument("--compute", help="compute_type 参数", default=None)
    run.add_argument("--workers", type=int, help="并发数量", default=None)
    _add_common_options(run)

    fetch = subparsers.add_parser("fetch_outputs", help="下载远端 data/asr-json/")
    fetch.add_argument("--since", help="仅同步该 ISO 时间之后的产物", default=None)
    _add_common_options(fetch)

    status = subparsers.add_parser("status", help="查看远端作业状态")

    provider = subparsers.add_parser("provider", help="查看或切换部署 provider")
    provider.add_argument(
        "--set",
        choices=["builtin", "legacy", "sshfs", "sync"],
        help="切换 provider",
    )
    provider.add_argument("--show", action="store_true", help="仅显示当前 provider")

    return parser


def _resolve_defaults(args: argparse.Namespace) -> tuple[str, str, str, str, int]:
    config = load_provider_config()
    defaults = config.get("common", {})
    pattern = args.pattern or str(defaults.get("audio_pattern", "*.m4a,*.wav,*.mp3,*.flac"))
    model = args.model or str(defaults.get("model", "medium"))
    language = args.language or str(defaults.get("language", "zh"))
    device = args.device or str(defaults.get("device", "auto"))
    compute = args.compute or str(defaults.get("compute", "auto"))
    workers = args.workers or int(defaults.get("workers", 1))
    return pattern, model, language, device, compute, workers


def handle_provision(args: argparse.Namespace) -> int:
    provider = get_provider()
    return provider.provision(dry_run=args.dry_run)


def handle_upload(args: argparse.Namespace) -> int:
    provider = get_provider()
    audio_dir = PROJ_ROOT / "data" / "audio"
    return provider.upload_audio(audio_dir, dry_run=args.dry_run, no_delete=args.no_delete)


def handle_run(args: argparse.Namespace) -> int:
    pattern, model, language, device, compute, workers = _resolve_defaults(args)
    provider = get_provider()
    return provider.run_asr(
        audio_pattern=pattern,
        model=model,
        language=language,
        device=device,
        compute=compute,
        workers=workers,
        dry_run=args.dry_run,
    )


def handle_fetch(args: argparse.Namespace) -> int:
    provider = get_provider()
    local_dir = PROJ_ROOT / "data" / "asr-json"
    since = args.since
    rc = provider.fetch_outputs(local_dir, since_iso=since, dry_run=args.dry_run)
    if rc == 0 and not args.dry_run:
        verify_script = PROJ_ROOT / "scripts" / "verify_asr_words.py"
        if verify_script.exists():
            log_info("自动校验 ASR JSON words 字段……")
            result = subprocess.run([sys.executable, str(verify_script)], cwd=PROJ_ROOT)
            if result.returncode != 0:
                log_warn(f"verify_asr_words 返回码 {result.returncode}。")
    return rc


def handle_status(args: argparse.Namespace) -> int:
    provider = get_provider()
    return provider.status()


def handle_provider(args: argparse.Namespace) -> int:
    config = load_provider_config()
    current = get_current_provider_name(config)
    if args.set:
        if args.set == current:
            log_info(f"当前 provider 已是 {current}")
            return 0
        set_current_provider(args.set)
        log_ok(f"已切换 provider 为 {args.set}")
        return 0
    if args.show or not args.set:
        log_info(f"当前 provider：{current}")
        if not args.show and not args.set:
            log_info("可使用 --set builtin|legacy|sshfs|sync 进行切换。")
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    parser = _build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "provision": handle_provision,
        "upload_audio": handle_upload,
        "run_asr": handle_run,
        "fetch_outputs": handle_fetch,
        "status": handle_status,
        "provider": handle_provider,
    }

    handler = handlers.get(args.command)
    if handler is None:
        log_err("未实现的子命令。")
        return 2
    try:
        rc = handler(args)
    except FileNotFoundError as exc:
        log_err(str(exc))
        return 2
    if rc == 0:
        log_ok("完成。")
    elif rc == 1:
        log_warn("子命令返回 1（可能表示跳过）。")
    else:
        log_err(f"子命令返回码 {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
