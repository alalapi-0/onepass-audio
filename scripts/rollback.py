"""Command-line interface for rolling back OnePass Audio snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from onepass.snapshot import (
    SnapshotError,
    compare_with_current,
    resolve_manifest_targets,
    restore_from_snapshot,
)
from onepass.ux import enable_ansi, log_err, log_info, log_ok

PROJ_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJ_ROOT / "out"


def _parse_targets(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [s.strip() for s in raw.split(",") if s.strip()]
    return items or None


def _load_manifest(snapshot_dir: Path) -> dict:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise SnapshotError(f"manifest.json 不存在：{manifest_path}")
    try:
        return json.loads(manifest_path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"manifest.json 不是有效 JSON：{manifest_path}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从快照回滚 out/ 产物")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", help="快照 ID（位于 out/_snapshots/<id>）")
    group.add_argument("--dir", help="快照目录路径")
    parser.add_argument("--targets", help="指定回滚目标（stem 或相对路径，逗号分隔）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入")
    parser.add_argument("--verify", dest="verify", action="store_true", default=True, help="回滚前校验哈希")
    parser.add_argument("--no-verify", dest="verify", action="store_false", help="跳过哈希校验")
    parser.add_argument("--soft", dest="soft", action="store_true", default=True, help="冲突时先备份")
    parser.add_argument("--hard", dest="soft", action="store_false", help="直接覆盖不备份")
    return parser


def _resolve_snapshot_dir(args: argparse.Namespace) -> Path:
    if args.dir:
        snapshot_dir = Path(args.dir).expanduser()
    else:
        snapshot_dir = OUT_DIR / "_snapshots" / args.id
    if not snapshot_dir.is_absolute():
        snapshot_dir = (PROJ_ROOT / snapshot_dir).resolve()
    if not snapshot_dir.exists():
        raise SnapshotError(f"未找到快照目录：{snapshot_dir}")
    return snapshot_dir


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    parser = build_parser()
    args = parser.parse_args(argv)

    targets = _parse_targets(args.targets)

    try:
        snapshot_dir = _resolve_snapshot_dir(args)
        manifest = _load_manifest(snapshot_dir)
    except SnapshotError as exc:
        log_err(str(exc))
        return 2

    out_dir = OUT_DIR
    if not out_dir.exists():
        log_err(f"输出目录不存在：{out_dir}")
        return 2

    diff = compare_with_current(out_dir, manifest)
    log_info(f"快照 ID：{manifest.get('snapshot_id', '未知')} → {snapshot_dir}")
    for key in ("missing", "changed", "extra", "ok"):
        log_info(f"{key}: {len(diff.get(key, []))}")

    try:
        selected_entries = resolve_manifest_targets(manifest, targets)
    except SnapshotError as exc:
        log_err(str(exc))
        return 2

    log_info("本次将回滚以下文件：")
    for entry in selected_entries:
        log_info(f"  - {entry['relpath']} ({entry.get('size', 0)} 字节)")

    if args.dry_run:
        log_ok("dry-run 完成，未执行回滚。")
        return 0

    try:
        restore_from_snapshot(out_dir, snapshot_dir, targets=targets, verify_hash=args.verify, soft=args.soft)
    except SnapshotError as exc:
        log_err(str(exc))
        return 2

    log_ok("回滚流程完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

