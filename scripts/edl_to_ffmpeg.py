"""scripts.edl_to_ffmpeg
用途：依据字幕 EDL 构建剪辑命令，调用 ffmpeg 输出干净音频并实时展示进度。
依赖：Python 标准库 argparse、datetime、json、math、os、pathlib、re、subprocess、tempfile；内部模块 ``onepass.ux``。
示例：
  python scripts/edl_to_ffmpeg.py --audio data/audio/001.m4a --edl out/001.keepLast.edl.json --out out/001.clean.wav --xfade
"""
from __future__ import annotations

import argparse
import datetime as _datetime
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from onepass.ux import enable_ansi, format_cmd, log_err, log_info, log_ok, log_warn, run_streamed, section, ts

TOLERANCE = 0.001
LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary"
TIME_PATTERN = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def format_seconds(value: float) -> float:
    """Round seconds to 6 decimal places."""

    return round(value, 6)


def probe_duration(audio: Path) -> Optional[float]:
    """Probe audio duration using ffprobe; return None on failure."""

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio),
    ]
    try:
        result = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        log_warn("未找到 ffprobe，可执行 scripts/install_deps.ps1 安装依赖。")
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or "").strip()
    if not output:
        return None
    try:
        duration = float(output)
    except ValueError:
        return None
    if math.isfinite(duration) and duration > 0:
        return duration
    return None


def build_cuts_from_edl(edl: Dict) -> List[Tuple[float, float]]:
    """Build deletion ranges from EDL actions."""

    actions = edl.get("actions", [])
    cuts: List[Tuple[float, float]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = action.get("type")
        try:
            start = float(action.get("start", 0.0))
            end = float(action.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        if action_type == "tighten_pause":
            target_ms = action.get("target_ms")
            try:
                target = float(target_ms) / 1000.0
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            start = start + target
        elif action_type != "cut":
            continue
        s = format_seconds(start)
        e = format_seconds(end)
        if e <= s:
            continue
        cuts.append((s, e))
    return cuts


def merge_ranges(ranges: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Merge overlapping or adjacent ranges with tolerance."""

    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda item: item[0])
    merged: List[Tuple[float, float]] = []
    cur_start, cur_end = sorted_ranges[0]
    for start, end in sorted_ranges[1:]:
        if start <= cur_end + TOLERANCE:
            cur_end = max(cur_end, end)
        else:
            merged.append((format_seconds(cur_start), format_seconds(cur_end)))
            cur_start, cur_end = start, end
    merged.append((format_seconds(cur_start), format_seconds(cur_end)))
    return merged


def clamp_ranges(ranges: Sequence[Tuple[float, float]], total: Tuple[float, float]) -> List[Tuple[float, float]]:
    """Clamp ranges to the total interval, discarding invalid ones."""

    total_start, total_end = total
    clamped: List[Tuple[float, float]] = []
    for start, end in ranges:
        if end <= total_start or start >= total_end:
            continue
        s = max(start, total_start)
        e = min(end, total_end)
        if e <= s:
            continue
        clamped.append((format_seconds(s), format_seconds(e)))
    return clamped


def subtract_ranges(total: Tuple[float, float], cuts: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Subtract cut ranges from the total interval and return keep ranges."""

    total_start, total_end = total
    if total_end <= total_start:
        return []
    keeps: List[Tuple[float, float]] = []
    cursor = total_start
    for start, end in cuts:
        if start > cursor + TOLERANCE:
            keeps.append((format_seconds(cursor), format_seconds(start)))
        cursor = max(cursor, end)
    if cursor < total_end - TOLERANCE:
        keeps.append((format_seconds(cursor), format_seconds(total_end)))
    elif total_end > cursor >= total_end - TOLERANCE:
        keeps.append((format_seconds(cursor), format_seconds(total_end)))
    return keeps


def write_concat_list(list_path: Path, audio: Path, keeps: Sequence[Tuple[float, float]]) -> None:
    """Write concat demuxer list file for ffmpeg."""

    audio_path = audio.resolve()
    with list_path.open("w", encoding="utf-8", newline="\n") as file_obj:
        for start, end in keeps:
            file_obj.write(f"file '{str(audio_path).replace("'", "'\\''")}'\n")
            file_obj.write(f"inpoint {start:.6f}\n")
            file_obj.write(f"outpoint {end:.6f}\n\n")


def gen_filter_complex(keeps: Sequence[Tuple[float, float]]) -> Tuple[str, str]:
    """Generate filter_complex string and final label for xfade mode."""

    if not keeps:
        return "", ""
    segments: List[str] = []
    labels: List[str] = []
    for idx, (start, end) in enumerate(keeps):
        label = f"s{idx}"
        seg = f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[{label}]"
        segments.append(seg)
        labels.append(label)
    if len(labels) == 1:
        return ";".join(segments), labels[0]
    mixes: List[str] = []
    current = labels[0]
    for idx, label in enumerate(labels[1:], start=1):
        out_label = f"m{idx}"
        mixes.append(f"[{current}][{label}]acrossfade=d=0.015:c1=tri:c2=tri[{out_label}]")
        current = out_label
    filter_complex = ";".join(segments + mixes)
    return filter_complex, current


def determine_verbose(args: argparse.Namespace) -> bool:
    env_verbose = os.environ.get("ONEPASS_VERBOSE", "1") != "0"
    if getattr(args, "quiet", False):
        return False
    if getattr(args, "verbose", False):
        return True
    return env_verbose


def run_ffmpeg(cmd: List[str], total_duration: float, heartbeat: float = 30.0) -> int:
    state = {"shown": False}

    def _on_line(line: str, is_err: bool) -> bool:
        if not is_err:
            return False
        match = TIME_PATTERN.search(line)
        if not match:
            return False
        hours, minutes, seconds = match.groups()
        elapsed = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        percent = 0.0
        if total_duration > 0:
            percent = min(100.0, max(0.0, (elapsed / total_duration) * 100.0))
        message = (
            f"[{ts()}] 渲染进度：已处理 {elapsed:.1f}s / {total_duration:.1f}s"
            f"（≈ {percent:.1f}%）"
        )
        print("\r" + message + " " * 8, end="", flush=True)
        state["shown"] = True
        return True

    rc = run_streamed(cmd, heartbeat_s=heartbeat, show_cmd=False, line_callback=_on_line)
    if state["shown"]:
        print("", flush=True)
    return rc


def ensure_out_dir(path: Path) -> None:
    out_dir = path.parent
    out_dir.mkdir(parents=True, exist_ok=True)


def log_segments(keeps: Sequence[Tuple[float, float]]) -> None:
    log_info("保留片段如下：")
    for idx, (start, end) in enumerate(keeps, start=1):
        duration = end - start
        log_info(f"  #{idx:02d}: {start:.3f}s -> {end:.3f}s (持续 {duration:.3f}s)")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="依据 EDL 渲染干净音频")
    parser.add_argument("--audio", required=True, help="原始音频文件路径")
    parser.add_argument("--edl", required=True, help="EDL JSON 路径")
    parser.add_argument("--out", required=True, help="输出 WAV 文件路径")
    parser.add_argument("--xfade", action="store_true", help="使用 acrossfade 拼接")
    parser.add_argument("--loudnorm", action="store_true", help="对输出做响度归一")
    parser.add_argument("--dry-run", action="store_true", help="仅打印命令，不执行 ffmpeg")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", action="store_true", help="强制开启详细日志")
    verbosity.add_argument("--quiet", action="store_true", help="关闭大部分日志")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    enable_ansi()
    args = parse_args(argv)
    verbose_flag = determine_verbose(args)

    audio_path = Path(args.audio)
    edl_path = Path(args.edl)
    out_path = Path(args.out)

    section("解析 EDL")
    if not audio_path.exists():
        log_err(f"未找到音频文件：{audio_path}")
        return 2
    if not edl_path.exists():
        log_err(f"未找到 EDL 文件：{edl_path}")
        return 2

    ensure_out_dir(out_path)

    try:
        with edl_path.open("r", encoding="utf-8") as edl_file:
            edl = json.load(edl_file)
    except (OSError, json.JSONDecodeError) as exc:
        log_err(f"读取 EDL 失败：{exc}")
        return 2

    section("构建保留片段")
    cuts = build_cuts_from_edl(edl)
    duration = probe_duration(audio_path)
    if duration is None:
        fallback = edl.get("source_duration") or edl.get("duration")
        if isinstance(fallback, (int, float)) and math.isfinite(float(fallback)):
            duration = float(fallback)
            log_warn("ffprobe 失败，使用 EDL 中的 duration 作为总时长。")
    if duration is None:
        log_err("无法获取音频总时长，请确认 ffmpeg/ffprobe 可用或在 EDL 中提供 source_duration。")
        return 2

    duration = format_seconds(duration)
    total_interval = (0.0, duration)
    cuts = clamp_ranges(cuts, total_interval)
    cuts = merge_ranges(cuts)
    cut_total = sum(end - start for start, end in cuts)

    keeps = subtract_ranges(total_interval, cuts)
    keeps = merge_ranges(keeps)
    keeps = [rng for rng in keeps if rng[1] - rng[0] > 0]
    keep_total = sum(end - start for start, end in keeps)

    log_info("总时长 {:.3f}s，删除 {:.3f}s，保留 {:.3f}s，片段 {} 段".format(duration, cut_total, keep_total, len(keeps)))
    if not keeps:
        log_err("没有可用的保留片段，渲染终止。")
        return 2
    if verbose_flag:
        log_segments(keeps)

    section("调用 ffmpeg")
    use_xfade = bool(args.xfade)
    if use_xfade and len(keeps) > 50:
        log_warn("片段数量过多，自动回退到 concat 模式以避免命令行过长。")
        use_xfade = False

    if use_xfade:
        filter_complex, last_label = gen_filter_complex(keeps)
        if not filter_complex or not last_label:
            log_err("构建 filter_complex 失败。")
            return 2
        if args.loudnorm:
            loud_label = "loud"
            filter_complex = f"{filter_complex};[{last_label}]{LOUDNORM_FILTER}[{loud_label}]"
            last_label = loud_label
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(audio_path),
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{last_label}]",
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ]
    else:
        timestamp = _datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        list_dir = out_path.parent
        list_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            prefix=f"concat_{timestamp}_",
            suffix=".list.txt",
            dir=list_dir,
        ) as temp_file:
            list_path = Path(temp_file.name)
            write_concat_list(list_path, audio_path, keeps)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-vn",
            "-c:a",
            "pcm_s16le",
        ]
        if args.loudnorm:
            cmd.extend(["-af", LOUDNORM_FILTER])
        cmd.append(str(out_path))
        log_info(f"concat list 文件：{list_path}")

    log_info(f"将要执行的命令：{format_cmd(cmd)}")
    if args.dry_run:
        log_warn("dry-run 模式：未调用 ffmpeg。")
        return 0

    rc = run_ffmpeg(cmd, total_duration=duration, heartbeat=30.0)
    if rc == 0:
        section("完成")
        log_ok("ffmpeg 渲染完成。")
        return 0
    log_err(f"ffmpeg 执行失败，返回码 {rc}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
