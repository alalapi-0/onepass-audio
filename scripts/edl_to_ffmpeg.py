"""Render a "keep last" EDL to a cleaned WAV file using ffmpeg.

Example
-------
python scripts/edl_to_ffmpeg.py --audio data/audio/001.m4a \
    --edl out/001.keepLast.edl.json --out out/001.clean.wav
"""
from __future__ import annotations

import argparse
import math
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from onepass.edl_utils import edl_to_keep_intervals, human_sec, load_edl


class CommandError(RuntimeError):
    """Raised when an ffmpeg invocation fails."""


def format_cmd(cmd: Sequence[str]) -> str:
    """Return a shell-style representation of *cmd*."""

    return " ".join(shlex.quote(str(part)) for part in cmd)


def run_cmd(cmd: Sequence[str], *, dry_run: bool) -> None:
    """Run *cmd* via subprocess, respecting ``dry_run``."""

    print(format_cmd(cmd))
    if dry_run:
        return

    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise CommandError(f"Executable not found: {cmd[0]}") from exc

    if result.returncode != 0:
        raise CommandError(f"Command failed with exit code {result.returncode}")


def derive_ffprobe_candidates(ffmpeg_path: str) -> List[str]:
    """Return possible ffprobe executables based on the ffmpeg path."""

    candidates: List[str] = []
    ffmpeg = Path(ffmpeg_path)
    suffix = ffmpeg.suffix
    if ffmpeg.name:
        probe_name = "ffprobe" + suffix
        if ffmpeg.parent:
            candidates.append(str(ffmpeg.with_name(probe_name)))
    candidates.append("ffprobe")
    return candidates


def probe_duration(audio: Path, ffmpeg_exec: str) -> float:
    """Return the duration of *audio* in seconds using ffprobe/ffmpeg."""

    error_messages: List[str] = []
    for candidate in derive_ffprobe_candidates(ffmpeg_exec):
        try:
            result = subprocess.run(
                [
                    candidate,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(audio),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            error_messages.append(f"ffprobe not found: {candidate}")
            continue

        if result.returncode != 0:
            error_messages.append(result.stderr.strip())
            continue

        try:
            duration = float(result.stdout.strip())
        except ValueError:
            error_messages.append("Unable to parse ffprobe duration output")
            continue

        if duration > 0:
            return duration
        error_messages.append("ffprobe returned non-positive duration")

    raise RuntimeError(
        "Could not determine audio duration. "
        + "; ".join(msg for msg in error_messages if msg)
    )


def ensure_ffmpeg_available(executable: str) -> None:
    """Verify that *executable* is runnable."""

    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-version"],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg executable not found. Install ffmpeg or pass --ffmpeg"
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg is not functional. Install a working build or specify --ffmpeg"
        )


def ensure_directory(path: Path) -> None:
    """Create the parent directory for *path* if needed."""

    path.parent.mkdir(parents=True, exist_ok=True)


def write_concat_file(paths: Iterable[Path], concat_file: Path, *, dry_run: bool) -> None:
    """Create the concat demuxer file listing the provided *paths*."""

    lines = []
    for path in paths:
        escaped = str(path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'\n")

    if dry_run:
        print(f"# Would write concat list to {concat_file}")
        for line in lines:
            print(line.rstrip())
        return

    with concat_file.open("w", encoding="utf-8") as fh:
        fh.writelines(lines)


def slice_interval(
    ffmpeg_exec: str,
    audio: Path,
    start: float,
    end: float,
    output: Path,
    *,
    dry_run: bool,
) -> None:
    """Render a single keep interval to *output*."""

    cmd = [
        ffmpeg_exec,
        "-hide_banner",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-to",
        f"{end:.6f}",
        "-i",
        str(audio),
        "-ac",
        "1",
        "-ar",
        "48000",
        "-vn",
        "-sn",
        "-dn",
        str(output),
    ]
    run_cmd(cmd, dry_run=dry_run)


def concat_segments(
    ffmpeg_exec: str,
    segments: Sequence[Path],
    concat_list: Path,
    output: Path,
    *,
    dry_run: bool,
) -> None:
    """Concatenate *segments* using the concat demuxer."""

    cmd = [
        ffmpeg_exec,
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(output),
    ]
    run_cmd(cmd, dry_run=dry_run)


def xfade_segments(
    ffmpeg_exec: str,
    segments: Sequence[Path],
    output: Path,
    *,
    dry_run: bool,
    duration: float = 0.015,
) -> None:
    """Apply cascading acrossfade between *segments*."""

    cmd: List[str] = [ffmpeg_exec, "-hide_banner", "-y"]
    for segment in segments:
        cmd.extend(["-i", str(segment)])

    filter_parts: List[str] = []
    for idx in range(len(segments) - 1):
        left = "[0:a]" if idx == 0 else f"[xf{idx}]"
        right = f"[{idx + 1}:a]"
        out = f"[xf{idx + 1}]"
        filter_parts.append(f"{left}{right}acrossfade=d={duration:.3f}{out}")

    filter_complex = ";".join(filter_parts)
    final_label = f"[xf{len(segments) - 1}]"
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            final_label,
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    run_cmd(cmd, dry_run=dry_run)


def apply_loudnorm(
    ffmpeg_exec: str,
    source: Path,
    output: Path,
    *,
    dry_run: bool,
) -> None:
    """Apply EBU R128 loudness normalization to *source*."""

    cmd = [
        ffmpeg_exec,
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-af",
        "loudnorm=I=-16:LRA=11:TP=-1.5:print_format=summary",
        str(output),
    ]
    run_cmd(cmd, dry_run=dry_run)


def copy_or_move(src: Path, dst: Path, *, keep_src: bool) -> None:
    """Copy or move ``src`` to ``dst`` depending on *keep_src*."""

    if keep_src:
        shutil.copy2(src, dst)
    else:
        shutil.move(src, dst)


def compute_intervals(edl_path: Path, audio_duration: float) -> List[Tuple[float, float]]:
    edl_data = load_edl(edl_path)
    return edl_to_keep_intervals(edl_data, audio_duration=audio_duration)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render audio based on an EDL")
    parser.add_argument("--audio", required=True, type=Path, help="Input audio file")
    parser.add_argument("--edl", required=True, type=Path, help="Input EDL JSON file")
    parser.add_argument("--out", type=Path, help="Output WAV path")
    parser.add_argument("--xfade", action="store_true", help="Enable 15 ms acrossfade between segments")
    parser.add_argument("--loudnorm", action="store_true", help="Apply EBU R128 loudness normalization")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable path")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary slice files")
    return parser.parse_args(argv)


def describe_plan(
    audio: Path,
    duration: float,
    intervals: Sequence[Tuple[float, float]],
    *,
    xfade: bool,
    loudnorm: bool,
    output: Path,
) -> None:
    total_keep = sum(end - start for start, end in intervals)
    print(f"Input: {audio} ({human_sec(duration)})")
    print(f"Keep intervals: {len(intervals)} totaling {human_sec(total_keep)}")
    print(f"Strategy: {'acrossfade' if xfade else 'concat demuxer'}")
    print(f"Loudnorm: {'yes' if loudnorm else 'no'}")
    print(f"Output: {output}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    audio_path = args.audio.resolve()
    edl_path = args.edl.resolve()
    ffmpeg_exec = args.ffmpeg

    try:
        ensure_ffmpeg_available(ffmpeg_exec)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not audio_path.exists():
        print(f"Error: audio file not found: {audio_path}", file=sys.stderr)
        return 1

    if not edl_path.exists():
        print(f"Error: EDL file not found: {edl_path}", file=sys.stderr)
        return 1

    out_path = args.out
    if out_path is None:
        out_dir = Path("out")
        out_path = out_dir / f"{audio_path.stem}.clean.wav"
    out_path = out_path.resolve()

    try:
        duration = probe_duration(audio_path, ffmpeg_exec)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        intervals = compute_intervals(edl_path, duration)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not intervals:
        print("Error: no keep intervals remain after applying EDL", file=sys.stderr)
        return 1

    describe_plan(audio_path, duration, intervals, xfade=args.xfade, loudnorm=args.loudnorm, output=out_path)

    ensure_directory(out_path)

    if len(intervals) == 1 and math.isclose(intervals[0][0], 0.0, abs_tol=1e-6) and math.isclose(intervals[0][1], duration, rel_tol=0, abs_tol=1e-6):
        # No cuts requested – perform a direct transcode to the desired output.
        cmd = [
            ffmpeg_exec,
            "-hide_banner",
            "-y",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "48000",
            "-vn",
            "-sn",
            "-dn",
            str(out_path),
        ]
        try:
            run_cmd(cmd, dry_run=args.dry_run)
        except CommandError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    tmp_dir = Path(tempfile.mkdtemp(prefix="edl_render_"))

    try:
        segment_paths: List[Path] = []
        for idx, (start, end) in enumerate(intervals):
            part_path = tmp_dir / f"part_{idx:04d}.wav"
            try:
                slice_interval(ffmpeg_exec, audio_path, start, end, part_path, dry_run=args.dry_run)
            except CommandError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            segment_paths.append(part_path)

        if len(segment_paths) == 1:
            stage_path = segment_paths[0]
        else:
            if args.xfade:
                stage_path = tmp_dir / "xfaded.wav"
                try:
                    xfade_segments(ffmpeg_exec, segment_paths, stage_path, dry_run=args.dry_run)
                except CommandError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
            else:
                concat_list = tmp_dir / "concat.txt"
                write_concat_file(segment_paths, concat_list, dry_run=args.dry_run)
                stage_path = tmp_dir / "concatted.wav"
                try:
                    concat_segments(ffmpeg_exec, segment_paths, concat_list, stage_path, dry_run=args.dry_run)
                except CommandError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1

        final_source = stage_path
        if args.loudnorm:
            loudnorm_path = tmp_dir / "loudnorm.wav"
            try:
                apply_loudnorm(ffmpeg_exec, final_source, loudnorm_path, dry_run=args.dry_run)
            except CommandError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            final_source = loudnorm_path

        if args.dry_run:
            print(f"# Dry run complete. Final file would be {out_path}")
        else:
            copy_or_move(final_source, out_path, keep_src=args.keep_temp)
    finally:
        if args.keep_temp:
            print(f"Temporary files kept in {tmp_dir}")
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not args.dry_run and not args.keep_temp:
        print(f"Rendered cleaned audio to {out_path}")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
