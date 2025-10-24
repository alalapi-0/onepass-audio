"""Command-line interface for creating OnePass Audio snapshots."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from onepass.snapshot import (
    NoCandidatesError,
    SnapshotError,
    compare_with_current,
    create_snapshot,
    find_out_files,
    patterns_for_scope,
    write_diff_md,
)
from onepass.ux import enable_ansi, log_err, log_info, log_ok, log_warn

PROJ_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJ_ROOT / "out"

DEFAULT_PATTERN_STR = (
    "*.keepLast.clean.srt,*.keepLast.clean.vtt,*.keepLast.clean.txt,"
    "*.keepLast.edl.json,*.keepLast.audition_markers.csv,*.log,*.diff.md,"
    "*.clean.wav,*.list.txt"
)


def _parse_stems(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    stems = [s.strip() for s in raw.split(",") if s.strip()]
    return stems or None


def _parse_patterns(raw: str | None, what: str) -> list[str]:
    if raw:
        custom = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        custom = None
    return patterns_for_scope(what, custom)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="为 out/ 产物创建快照")
    parser.add_argument("--stems", help="限定章节 stem，逗号分隔", default=None)
    parser.add_argument(
        "--what",
        choices=["generated", "render", "all"],
        default="all",
        help="控制快照范围（默认 all）",
    )
    parser.add_argument(
        "--patterns",
        default=None,
        help=f"自定义匹配模式（逗号分隔，默认 {DEFAULT_PATTERN_STR})",
    )
    parser.add_argument("--note", default=None, help="记录在 manifest.json 中的说明")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不落盘")
    return parser


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    parser = build_parser()
    args = parser.parse_args(argv)

    stems = _parse_stems(args.stems)
    patterns = _parse_patterns(args.patterns, args.what)

    out_dir = OUT_DIR
    if not out_dir.exists():
        log_err(f"输出目录不存在：{out_dir}")
        return 2

    try:
        files = find_out_files(out_dir, stems, patterns)
    except SnapshotError as exc:
        log_err(str(exc))
        return 2

    if not files:
        log_warn("未找到可纳入快照的文件。")
        return 1

    total_bytes = 0
    log_info("即将纳入下列文件：")
    for path in files:
        rel = path.relative_to(out_dir).as_posix()
        size = path.stat().st_size
        total_bytes += size
        log_info(f"  - {rel} ({size} 字节)")
    log_info(f"合计 {len(files)} 个文件，约 {total_bytes} 字节。")

    if args.dry_run:
        log_ok("dry-run 结束，未创建快照。")
        return 0

    try:
        manifest = create_snapshot(out_dir, stems, patterns, note=args.note, what=args.what)
    except NoCandidatesError as exc:
        log_warn(str(exc))
        return 1
    except SnapshotError as exc:
        log_err(str(exc))
        return 2

    diff = compare_with_current(out_dir, manifest)
    snapshot_dir = out_dir / "_snapshots" / manifest["snapshot_id"]
    diff_path = write_diff_md(snapshot_dir, diff)
    log_info(f"diff 报告：{diff_path}")
    print(f"SNAPSHOT_ID {manifest['snapshot_id']} {snapshot_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

