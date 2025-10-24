"""scripts/asr_batch.py
用途：批量调用 whisper-ctranslate2 CLI，将 data/audio/ 中的音频转写为词级时间戳 JSON。
依赖：Python 3.10+（标准库）、whisper-ctranslate2 CLI、ffmpeg/ffprobe（用于转写与可选时长读取）。
示例用法：
    # CPU 自动选择、默认 small、中文、开启 VAD
    python scripts/asr_batch.py --audio-dir data/audio --out-dir data/asr-json

    # 指定模型与 GPU，2 并发
    python scripts/asr_batch.py --model medium --device cuda --workers 2

    # 自动跳过已有 JSON；想强制重跑就加 --overwrite
    python scripts/asr_batch.py --overwrite

    # 仅打印命令，不执行
    python scripts/asr_batch.py --dry-run
"""

from __future__ import annotations

import argparse
import concurrent.futures
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import List, Sequence

PROJ_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class TranscriptionTask:
    """描述一次音频转写任务。"""

    stem: str
    audio_path: Path
    output_path: Path
    command: List[str]


@dataclass
class TaskResult:
    """记录任务执行结果，用于汇总统计。"""

    stem: str
    succeeded: bool
    attempts: int
    returncode: int | None
    message: str | None
    elapsed: float


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

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
    parser.add_argument(
        "--language",
        default="zh",
        help="转写语言，默认 zh，可设为 auto",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="推理设备：auto|cpu|cuda（默认 auto 自动探测）",
    )
    parser.add_argument(
        "--compute-type",
        default="auto",
        help="whisper-ctranslate2 compute_type，默认 auto 随设备选择",
    )
    parser.add_argument(
        "--vad",
        dest="vad",
        action="store_true",
        default=True,
        help="启用 VAD 断句（默认开启）",
    )
    parser.add_argument(
        "--no-vad",
        dest="vad",
        action="store_false",
        help="禁用 VAD 断句",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并发任务数量，CPU 建议 1，GPU 建议 ≤2（默认 1）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的 JSON（默认跳过已存在文件）",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=1,
        help="失败重试次数（默认 1）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印即将执行的命令，不真正运行",
    )
    return parser.parse_args(argv)


def ensure_dependency(command: str, friendly: str, required: bool = True) -> str | None:
    """检查命令是否存在，缺失时输出提示。"""

    found = shutil.which(command)
    if found:
        return found
    prefix = "[错误]" if required else "[警告]"
    message = f"{prefix} 未找到 {friendly}（命令：{command}）。请先运行 scripts/install_deps.ps1 安装依赖。"
    print(message)
    if required:
        sys.exit(2)
    return None


def detect_device(preferred: str) -> str:
    """根据用户参数或 nvidia-smi 结果确定运行设备。"""

    if preferred != "auto":
        return preferred

    try:
        result = subprocess.run([
            "nvidia-smi",
            "--query-gpu=name",
            "--format=csv,noheader",
        ], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("[信息] 未检测到 nvidia-smi，默认使用 CPU。")
        return "cpu"

    if result.returncode == 0:
        gpu_name = (result.stdout.strip().splitlines() or ["GPU"])[0]
        print(f"[信息] 检测到 GPU：{gpu_name}，将使用 cuda 设备。")
        return "cuda"

    print("[信息] nvidia-smi 返回非零，默认使用 CPU。")
    return "cpu"


def resolve_compute_type(arg: str, device: str) -> str:
    """根据设备推断 compute_type。"""

    if arg != "auto":
        return arg
    if device == "cuda":
        return "float16"
    if device == "cpu":
        return "int8_float16"
    return "float32"


def format_seconds(seconds: float | None) -> str:
    """格式化秒数显示。"""

    if seconds is None:
        return "未知"
    return f"{seconds:.2f}s"


def rel_to_root(path: Path) -> Path:
    """将路径转换为相对项目根目录的形式。"""

    try:
        return path.relative_to(PROJ_ROOT)
    except ValueError:
        return path


def get_audio_duration(audio_path: Path, ffprobe: str | None) -> float | None:
    """通过 ffprobe 获取音频时长，若不可用则返回 None。"""

    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return float(line)
        except ValueError:
            continue
    return None


def gather_audio_files(audio_dir: Path, patterns: Sequence[str]) -> list[tuple[str, Path]]:
    """按照 stem 搜索音频文件并去重。"""

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
                    print(
                        f"[警告] 检测到重复 stem：{stem} -> {existing.name} 与 {audio_path.name}，将保留 {existing.name}。"
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
    """组装 whisper-ctranslate2 命令行参数。"""

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


def execute_task(task: TranscriptionTask, retry: int, overwrite: bool, lock: Lock) -> TaskResult:
    """执行转写任务，处理重试与日志。"""

    attempts = max(retry, 0) + 1
    if overwrite and task.output_path.exists():
        try:
            task.output_path.unlink()
        except OSError as exc:
            with lock:
                print(f"[警告] 无法删除旧文件 {task.output_path.name}：{exc}")
    last_message: str | None = None
    start_time = time.perf_counter()

    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        attempt_start = time.perf_counter()
        with lock:
            print(f"[开始] {task.stem} · 尝试 {attempt}/{attempts}")
        try:
            completed = subprocess.run(task.command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            elapsed = time.perf_counter() - attempt_start
            message = f"命令执行失败：{exc}"
            with lock:
                print(f"[失败] {task.stem} · {message}")
            return TaskResult(
                stem=task.stem,
                succeeded=False,
                attempts=attempt,
                returncode=None,
                message=message,
                elapsed=time.perf_counter() - start_time,
            )

        elapsed = time.perf_counter() - attempt_start
        if completed.returncode == 0:
            with lock:
                print(f"[完成] {task.stem} · 耗时 {elapsed:.2f}s")
            return TaskResult(
                stem=task.stem,
                succeeded=True,
                attempts=attempt,
                returncode=0,
                message=None,
                elapsed=time.perf_counter() - start_time,
            )

        stderr = completed.stderr.strip()
        snippet = "\n".join(stderr.splitlines()[:5]) if stderr else ""
        message = f"返回码 {completed.returncode}。{snippet}" if snippet else f"返回码 {completed.returncode}"
        last_message = message
        with lock:
            print(f"[失败] {task.stem} · 耗时 {elapsed:.2f}s · {message}")
        if attempt < attempts:
            with lock:
                print(f"[重试] {task.stem} · 将再次尝试 ({attempt + 1}/{attempts})")
        else:
            break

    return TaskResult(
        stem=task.stem,
        succeeded=False,
        attempts=attempts,
        returncode=completed.returncode if completed is not None else None,
        message=last_message,
        elapsed=time.perf_counter() - start_time,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """脚本主入口。"""

    args = parse_args(argv)
    audio_dir = (PROJ_ROOT / args.audio_dir).resolve()
    out_dir = (PROJ_ROOT / args.out_dir).resolve()

    if not audio_dir.exists():
        print(f"[错误] 音频目录不存在：{audio_dir}")
        return 2

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[错误] 创建输出目录失败：{exc}")
        return 2

    patterns = [pattern.strip() for pattern in args.pattern.split(",")]
    audio_items = gather_audio_files(audio_dir, patterns)

    if not audio_items:
        print("[提示] 未找到符合条件的音频文件。")
        return 1 if not args.dry_run else 0

    device = detect_device(args.device)
    compute_type = resolve_compute_type(args.compute_type, device)

    if args.retry < 0:
        print("[警告] --retry 不能为负数，已按 0 处理。")
        args.retry = 0

    if device == "cpu" and args.workers > 1:
        print("[警告] CPU 模式下设置多并发可能更慢或占用更高。建议 workers=1。")

    workers = args.workers
    if workers < 1:
        print("[警告] --workers 至少为 1，已自动设为 1。")
        workers = 1

    whisper_cmd = ensure_dependency("whisper-ctranslate2", "whisper-ctranslate2 CLI", required=not args.dry_run)
    ffmpeg_cmd = ensure_dependency("ffmpeg", "ffmpeg", required=not args.dry_run)
    ffprobe_cmd = ensure_dependency("ffprobe", "ffprobe", required=False)

    if args.dry_run:
        print("[信息] Dry-run 模式，仅展示计划命令。")
    else:
        # 在正式执行前确认依赖存在
        if whisper_cmd is None or ffmpeg_cmd is None:
            return 2

    lock = Lock()
    overall_start = time.perf_counter()

    tasks: list[TranscriptionTask] = []
    skipped = 0
    command_exec = whisper_cmd or "whisper-ctranslate2"

    print("[计划] 即将处理以下音频：")
    for stem, audio_path in audio_items:
        target_path = out_dir / f"{stem}.json"
        duration = get_audio_duration(audio_path, ffprobe_cmd)
        if target_path.exists():
            action = "覆盖" if args.overwrite else "跳过"
        else:
            action = "生成"
        rel_target = rel_to_root(target_path)
        print(f"  - {stem} · {format_seconds(duration)} · 输出 {rel_target} · 动作：{action}")
        if target_path.exists() and not args.overwrite:
            skipped += 1
            continue
        command = build_command(
            executable=command_exec,
            audio_path=audio_path,
            out_dir=out_dir,
            language=args.language,
            model=args.model,
            device=device,
            compute_type=compute_type,
            vad=args.vad,
        )
        tasks.append(
            TranscriptionTask(
                stem=stem,
                audio_path=audio_path,
                output_path=target_path,
                command=command,
            )
        )

    if args.dry_run:
        for task in tasks:
            printable = " ".join(shlex_quote(arg) for arg in task.command)
            print(f"[DRY-RUN] {task.stem}: {printable}")
        print("[信息] Dry-run 完成，无实际执行。")
        return 0

    if not tasks:
        print("[信息] 无需执行任何任务。")
        return 1 if skipped > 0 else 0

    results: list[TaskResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_task = {
            executor.submit(execute_task, task, args.retry, args.overwrite, lock): task
            for task in tasks
        }
        for future in concurrent.futures.as_completed(future_to_task):
            result = future.result()
            results.append(result)

    success_count = sum(1 for r in results if r.succeeded)
    failure_results = [r for r in results if not r.succeeded]
    failure_count = len(failure_results)
    total_elapsed = time.perf_counter() - overall_start

    print("\n[汇总] 执行完成。")
    print(f"  - 成功：{success_count}")
    print(f"  - 跳过：{skipped}")
    print(f"  - 失败：{failure_count}")
    print(f"  - 总耗时：{total_elapsed:.2f}s")

    if failure_count:
        for item in failure_results:
            detail = item.message or "未知错误"
            print(f"    · {item.stem}: {detail}")
        return 2

    if success_count == 0 and skipped > 0:
        return 1
    return 0


def shlex_quote(arg: str) -> str:
    """简单实现的 shell 引号包装。"""

    if not arg:
        return "''"
    if all(ch.isalnum() or ch in "@%+=:,./-" for ch in arg):
        return arg
    return "'" + arg.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
