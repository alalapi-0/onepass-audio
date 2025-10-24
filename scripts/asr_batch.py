"""scripts.asr_batch
用途：批量调用 whisper-ctranslate2 生成词级 JSON，支持并发执行与实时日志。
依赖：Python 标准库 argparse、concurrent.futures、os、pathlib、shutil、subprocess、threading、time；内部模块 ``onepass.ux``。
示例：
  python scripts/asr_batch.py --model medium --device cuda --workers 2
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from onepass.ux import enable_ansi, format_cmd, log_err, log_info, log_ok, log_warn, run_streamed, section, ts

PROJ_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class TranscriptionTask:
    """描述一次音频转写任务。"""

    stem: str
    audio_path: Path
    output_path: Path
    command: List[str]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量调用 whisper-ctranslate2 生成词级 JSON")
    parser.add_argument("--audio-dir", default="data/audio", help="音频目录（默认 data/audio）")
    parser.add_argument("--out-dir", default="data/asr-json", help="输出目录（默认 data/asr-json）")
    parser.add_argument(
        "--pattern",
        default="*.m4a,*.wav,*.mp3,*.flac",
        help="匹配音频文件的模式，逗号分隔（默认 *.m4a,*.wav,*.mp3,*.flac）",
    )
    parser.add_argument(
        "--model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="whisper-ctranslate2 模型（默认 small）",
    )
    parser.add_argument("--language", default="zh", help="转写语言，默认 zh，可设为 auto")
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="推理设备：auto|cpu|cuda（默认 auto 自动探测）",
    )
    parser.add_argument("--compute-type", default="auto", help="whisper-ctranslate2 compute_type，默认 auto 随设备选择")
    parser.add_argument("--vad", dest="vad", action="store_true", default=True, help="启用 VAD 断句（默认开启）")
    parser.add_argument("--no-vad", dest="vad", action="store_false", help="禁用 VAD 断句")
    parser.add_argument("--workers", type=int, default=1, help="并发任务数量（默认 1）")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的 JSON（默认跳过已存在文件）")
    parser.add_argument("--retry", type=int, default=1, help="失败重试次数（默认 1）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印即将执行的命令，不真正运行")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", action="store_true", help="强制开启详细日志")
    verbosity.add_argument("--quiet", action="store_true", help="关闭大部分日志")
    return parser.parse_args(argv)


def determine_verbose(args: argparse.Namespace) -> bool:
    env_verbose = os.environ.get("ONEPASS_VERBOSE", "1") != "0"
    if getattr(args, "quiet", False):
        return False
    if getattr(args, "verbose", False):
        return True
    return env_verbose


def ensure_dependency(command: str, friendly: str, required: bool = True) -> str | None:
    found = shutil.which(command)
    if found:
        return found
    message = f"未找到 {friendly}（命令：{command}），请先运行 scripts/install_deps.ps1 安装依赖。"
    if required:
        log_err(message)
        sys.exit(2)
    log_warn(message)
    return None


def detect_device(preferred: str) -> str:
    if preferred != "auto":
        return preferred
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        log_info("未检测到 nvidia-smi，默认使用 CPU。")
        return "cpu"
    if result.returncode == 0:
        gpu_name = (result.stdout.strip().splitlines() or ["GPU"])[0]
        log_info(f"检测到 GPU：{gpu_name}，将使用 cuda 设备。")
        return "cuda"
    log_info("nvidia-smi 返回非零，默认使用 CPU。")
    return "cpu"


def resolve_compute_type(arg: str, device: str) -> str:
    if arg != "auto":
        return arg
    if device == "cuda":
        return "float16"
    if device == "cpu":
        return "int8_float16"
    return "float32"


def gather_audio_files(audio_dir: Path, patterns: Sequence[str]) -> list[tuple[str, Path]]:
    candidates: dict[str, Path] = {}
    for raw_pattern in patterns:
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        for audio_path in sorted(audio_dir.glob(pattern)):
            if not audio_path.is_file():
                continue
            stem = audio_path.stem
            if stem in candidates:
                existing = candidates[stem]
                if existing != audio_path:
                    log_warn(
                        f"检测到重复 stem：{stem} -> {existing.name} 与 {audio_path.name}，将保留 {existing.name}。"
                    )
                continue
            candidates[stem] = audio_path
    return sorted(candidates.items(), key=lambda item: item[0])


def build_command(
    executable: str,
    audio_path: Path,
    out_dir: Path,
    language: str,
    model: str,
    device: str,
    compute_type: str,
    vad: bool,
) -> list[str]:
    return [
        executable,
        str(audio_path),
        "--task",
        "transcribe",
        "--language",
        language,
        "--model",
        model,
        "--device",
        device,
        "--compute_type",
        compute_type,
        "--word_timestamps",
        "true",
        "--vad_filter",
        "true" if vad else "false",
        "--output_format",
        "json",
        "--output_dir",
        str(out_dir),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    enable_ansi()
    args = parse_args(argv)
    verbose_flag = determine_verbose(args)

    section("参数确认")
    audio_dir = (PROJ_ROOT / args.audio_dir).resolve()
    out_dir = (PROJ_ROOT / args.out_dir).resolve()
    log_info(f"音频目录：{audio_dir}")
    log_info(f"输出目录：{out_dir}")
    log_info(f"模型：{args.model}")
    log_info(f"语言：{args.language}")
    log_info(f"并发：{args.workers}")
    log_info(f"dry-run：{'是' if args.dry_run else '否'}")

    if not audio_dir.exists():
        log_err(f"音频目录不存在：{audio_dir}")
        return 2
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log_err(f"创建输出目录失败：{exc}")
        return 2

    patterns = [pattern.strip() for pattern in args.pattern.split(",")]
    audio_items = gather_audio_files(audio_dir, patterns)
    if not audio_items:
        log_warn("未找到符合条件的音频文件。")
        return 1 if not args.dry_run else 0

    device = detect_device(args.device)
    compute_type = resolve_compute_type(args.compute_type, device)

    if args.retry < 0:
        log_warn("--retry 不能为负数，已按 0 处理。")
        args.retry = 0
    if device == "cpu" and args.workers > 1:
        log_warn("CPU 模式下设置多并发可能更慢或占用更高，建议 workers=1。")
    workers = max(1, args.workers)

    whisper_cmd = ensure_dependency("whisper-ctranslate2", "whisper-ctranslate2 CLI", required=not args.dry_run)
    ensure_dependency("ffmpeg", "ffmpeg", required=not args.dry_run)
    ensure_dependency("ffprobe", "ffprobe", required=False)

    section("任务规划")
    tasks: list[TranscriptionTask] = []
    skipped = 0
    command_exec = whisper_cmd or "whisper-ctranslate2"
    for stem, audio_path in audio_items:
        target_path = out_dir / f"{stem}.json"
        action = "覆盖" if (target_path.exists() and args.overwrite) else ("跳过" if target_path.exists() else "生成")
        if verbose_flag:
            log_info(f"{stem}: {audio_path.name} → {target_path.name} · 动作 {action}")
        if target_path.exists() and not args.overwrite:
            skipped += 1
            continue
        cmd = build_command(
            executable=command_exec,
            audio_path=audio_path,
            out_dir=out_dir,
            language=args.language,
            model=args.model,
            device=device,
            compute_type=compute_type,
            vad=args.vad,
        )
        tasks.append(TranscriptionTask(stem=stem, audio_path=audio_path, output_path=target_path, command=cmd))

    if args.dry_run:
        for task in tasks:
            log_info(f"[DRY-RUN] {task.stem}: {format_cmd(task.command)}")
        log_warn("dry-run 模式：未执行任何命令。")
        return 0

    if not tasks:
        log_info("无需执行任何任务。")
        return 1 if skipped > 0 else 0

    section("执行转写")
    stats = {
        "success": 0,
        "failure": 0,
        "skipped": skipped,
        "total": len(audio_items),
    }
    lock = threading.Lock()
    stop_event = threading.Event()

    def _update_progress() -> None:
        while not stop_event.is_set():
            with lock:
                message = (
                    f"[{ts()}] 进度：完成 {stats['success']} / 跳过 {stats['skipped']} / 失败 {stats['failure']} / 总 {stats['total']}"
                )
            print("\r" + message + " " * 6, end="", flush=True)
            time.sleep(1.0)
        with lock:
            final = (
                f"[{ts()}] 进度：完成 {stats['success']} / 跳过 {stats['skipped']} / 失败 {stats['failure']} / 总 {stats['total']}"
            )
        print("\r" + final + " " * 6, flush=True)

    monitor = threading.Thread(target=_update_progress, daemon=True)
    monitor.start()

    results: list[tuple[str, int]] = []
    start_time = time.perf_counter()

    def _run_task(task: TranscriptionTask) -> tuple[str, int]:
        log_info(f"[{task.stem}] 开始执行")
        if verbose_flag:
            log_info(f"[{task.stem}] 命令：{format_cmd(task.command)}")
        task_start = time.perf_counter()
        rc = run_streamed(
            task.command,
            prefix=f"[{task.stem}] ",
            heartbeat_s=45.0,
            show_cmd=False,
        )
        elapsed = time.perf_counter() - task_start
        with lock:
            if rc == 0:
                stats["success"] += 1
            else:
                stats["failure"] += 1
        if rc == 0:
            log_ok(f"{task.stem} 完成，耗时 {elapsed:.1f}s")
        else:
            log_err(f"{task.stem} 失败（返回码 {rc}），耗时 {elapsed:.1f}s")
        return task.stem, rc

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_run_task, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    stop_event.set()
    monitor.join()
    total_elapsed = time.perf_counter() - start_time

    section("汇总")
    log_info(f"成功：{stats['success']}")
    log_info(f"跳过：{stats['skipped']}")
    log_info(f"失败：{stats['failure']}")
    log_info(f"总耗时：{total_elapsed:.1f}s")

    if stats["failure"]:
        for stem, rc in results:
            if rc != 0:
                log_err(f"{stem} 转写失败，返回码 {rc}")
        return 2

    if stats["success"] == 0 and stats["skipped"] > 0:
        return 1
    log_ok("批量转写完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
