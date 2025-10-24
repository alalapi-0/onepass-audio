"""onepass.snapshot

Snapshot and rollback helpers for OnePass Audio outputs.

The module provides utilities to collect generated assets under ``out/``,
materialise snapshots with hard links (fallback to copy), compare snapshots
against the current workspace, and restore files when rolling back.

Only paths inside ``out/`` are ever touched; data sources under ``data/`` are
treated as read-only and remain untouched.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import shutil
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from onepass.ux import log_info, log_ok

PROJ_ROOT = Path(__file__).resolve().parents[1]

OUT_DEFAULT = PROJ_ROOT / "out"
SNAPSHOT_ROOT_NAME = "_snapshots"
TRASH_DIR_NAME = ".trash"

GENERATED_PATTERNS = [
    "*.keepLast.clean.srt",
    "*.keepLast.clean.vtt",
    "*.keepLast.clean.txt",
    "*.keepLast.edl.json",
    "*.keepLast.audition_markers.csv",
    "*.log",
    "*.diff.md",
    "*.list.txt",
]

RENDER_PATTERNS = [
    "*.clean.wav",
]


class SnapshotError(RuntimeError):
    """Raised when snapshot or rollback operations cannot proceed."""


class NoCandidatesError(SnapshotError):
    """Raised when no files match the requested snapshot criteria."""


def _ensure_within_out(path: Path, out_dir: Path) -> None:
    try:
        path.resolve().relative_to(out_dir.resolve())
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise SnapshotError(f"路径越界：{path}") from exc


def _normalise_patterns(patterns: Iterable[str]) -> list[str]:
    return [p.strip() for p in patterns if p and p.strip()]


def patterns_for_scope(what: str, patterns: list[str] | None = None) -> list[str]:
    """Return the effective patterns for the given scope ``what``.

    When ``patterns`` is provided it is treated as the final explicit list;
    otherwise defaults for ``generated`` and ``render`` are combined according
    to ``what``.
    """

    if patterns:
        return _normalise_patterns(patterns)

    what = (what or "all").lower()
    selected: list[str] = []
    if what in {"generated", "all"}:
        selected.extend(GENERATED_PATTERNS)
    if what in {"render", "all"}:
        selected.extend(RENDER_PATTERNS)
    return _normalise_patterns(selected)


def find_out_files(out_dir: Path, stems: list[str] | None, patterns: list[str]) -> list[Path]:
    """Return files under ``out_dir`` matching ``patterns`` and optional ``stems``.

    ``stems`` filters files whose *file name* begins with ``"{stem}."`` or
    ``"{stem}_"`` (also accepting an exact match). Duplicate paths are removed
    while preserving sorted order.
    """

    patterns = _normalise_patterns(patterns)
    if not patterns:
        return []

    stems_set = {s.strip() for s in stems} if stems else None
    candidates: list[Path] = []
    seen: set[Path] = set()

    for pattern in patterns:
        for matched in out_dir.glob(pattern):
            if matched.is_dir():
                continue
            try:
                rel = matched.relative_to(out_dir)
            except ValueError:
                continue
            if stems_set:
                name = rel.name
                if not any(
                    name == stem
                    or name.startswith(f"{stem}.")
                    or name.startswith(f"{stem}_")
                    for stem in stems_set
                ):
                    continue
            if rel in seen:
                continue
            seen.add(rel)
            candidates.append(matched)

    candidates.sort(key=lambda p: p.relative_to(out_dir).as_posix())
    return candidates


def sha256sum(path: Path, bufsize: int = 1024 * 1024) -> str:
    """Compute the SHA256 checksum for ``path`` using chunks of ``bufsize``."""

    digest = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(bufsize)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _make_snapshot_id() -> str:
    now = _dt.datetime.now()
    prefix = now.strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(3)
    return f"{prefix}-{suffix}"


def _rel_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJ_ROOT).as_posix()
    except ValueError:  # pragma: no cover - defensive
        return path.resolve().as_posix()


def _collect_engine_info() -> dict:
    config_path = PROJ_ROOT / "config" / "default_config.json"
    try:
        cfg = json.loads(config_path.read_text("utf-8"))
    except FileNotFoundError:
        cfg = {}
    except json.JSONDecodeError:
        cfg = {}
    return {
        "aggr": None,
        "align_strategy": cfg.get("align_strategy", "hybrid"),
        "align_min_sim": cfg.get("align_min_sim", 0.0),
        "keep_policy": cfg.get("overlap_keep", "last"),
    }


@dataclass
class SnapshotContext:
    out_dir: Path
    snapshot_dir: Path
    files_dir: Path


def _prepare_snapshot_dirs(out_dir: Path, run_id: str) -> SnapshotContext:
    snapshots_root = out_dir / SNAPSHOT_ROOT_NAME
    files_dir = snapshots_root / run_id / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return SnapshotContext(out_dir=out_dir, snapshot_dir=files_dir.parent, files_dir=files_dir)


def create_snapshot(
    out_dir: Path,
    stems: list[str] | None,
    patterns: list[str],
    note: str | None = None,
    what: str = "all",
) -> dict:
    """Create a snapshot for files under ``out_dir``.

    The function copies (hard link preferred) files into ``files/`` and writes
    ``manifest.json``. The manifest dictionary is returned.
    """

    out_dir = out_dir.resolve()
    if not out_dir.exists():
        raise SnapshotError(f"输出目录不存在：{out_dir}")

    log_info("开始扫描匹配的输出文件…")
    files = find_out_files(out_dir, stems, patterns)
    if not files:
        raise NoCandidatesError("未找到可纳入快照的文件。")

    run_id = _make_snapshot_id()
    ctx = _prepare_snapshot_dirs(out_dir, run_id)

    manifest_entries: list[dict] = []
    total_bytes = 0

    for path in files:
        rel = path.relative_to(out_dir)
        dest = ctx.files_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        checksum = sha256sum(path)
        size = path.stat().st_size
        mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat()

        try:
            os.link(path, dest)
        except OSError:
            shutil.copy2(path, dest)

        manifest_entries.append(
            {
                "relpath": rel.as_posix(),
                "size": size,
                "sha256": checksum,
                "mtime": mtime,
            }
        )
        total_bytes += size
        log_info(f"已纳入：{rel.as_posix()} ({size} 字节)")

    manifest = {
        "snapshot_id": run_id,
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "creator": "snapshot.py",
        "note": note or "",
        "out_root": _rel_to_root(out_dir),
        "stems": stems or [],
        "patterns": patterns,
        "entries": manifest_entries,
        "counts": {"files": len(manifest_entries), "bytes": total_bytes},
        "engine": _collect_engine_info(),
    }

    manifest_path = ctx.snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    log_ok(f"快照完成：{run_id} → {manifest_path.parent}")

    return manifest


def compare_with_current(out_dir: Path, manifest: dict) -> dict:
    """Compare ``manifest`` against current ``out_dir`` contents."""

    out_dir = out_dir.resolve()
    entries = manifest.get("entries", [])
    patterns = manifest.get("patterns", [])
    stems = manifest.get("stems") or None

    missing: list[str] = []
    changed: list[str] = []
    ok: list[str] = []

    manifest_map = {entry["relpath"]: entry for entry in entries}

    for rel, entry in manifest_map.items():
        path = out_dir / rel
        if not path.exists():
            missing.append(rel)
            continue
        if not path.is_file():
            changed.append(rel)
            continue
        current_hash = sha256sum(path)
        if current_hash != entry.get("sha256"):
            changed.append(rel)
        else:
            ok.append(rel)

    current_files = find_out_files(out_dir, stems, patterns)
    extras = []
    for path in current_files:
        rel = path.relative_to(out_dir).as_posix()
        if rel not in manifest_map:
            extras.append(rel)

    return {
        "missing": sorted(missing),
        "extra": sorted(extras),
        "changed": sorted(changed),
        "ok": sorted(ok),
    }


def write_diff_md(snapshot_dir: Path, diff: dict) -> Path:
    """Write a Markdown diff report under ``snapshot_dir``."""

    lines = ["# Snapshot Diff Report", "", "| 状态 | 文件 |", "| --- | --- |"]
    for status in ("missing", "extra", "changed", "ok"):
        items = diff.get(status, [])
        if not items:
            lines.append(f"| {status} | — |")
            continue
        for idx, rel in enumerate(items):
            prefix = status if idx == 0 else ""
            lines.append(f"| {prefix} | `{rel}` |")
    content = "\n".join(lines) + "\n"
    diff_path = snapshot_dir / "diff.md"
    diff_path.write_text(content, "utf-8")
    return diff_path


def resolve_manifest_targets(manifest: dict, targets: list[str] | None) -> list[dict]:
    """Return manifest entries matching ``targets`` selection."""

    entries = manifest.get("entries", [])
    return _resolve_targets(entries, targets)


def _resolve_targets(entries: list[dict], targets: list[str] | None) -> list[dict]:
    if not targets:
        return entries

    rel_map = {entry["relpath"]: entry for entry in entries}
    selected: list[dict] = []
    stems = []
    for raw in targets:
        target = raw.strip()
        if not target:
            continue
        if target in rel_map:
            selected.append(rel_map[target])
        else:
            stems.append(target)

    stems_set = set(stems)
    if stems_set:
        for entry in entries:
            name = Path(entry["relpath"]).name
            if any(
                name == stem
                or name.startswith(f"{stem}.")
                or name.startswith(f"{stem}_")
                for stem in stems_set
            ):
                if entry not in selected:
                    selected.append(entry)

    unique = []
    seen = set()
    for entry in selected:
        rel = entry["relpath"]
        if rel in seen:
            continue
        seen.add(rel)
        unique.append(entry)
    if not unique:
        raise SnapshotError("未匹配到任何回滚目标。")
    return unique


def restore_from_snapshot(
    out_dir: Path,
    snapshot_dir: Path,
    targets: list[str] | None = None,
    verify_hash: bool = True,
    soft: bool = True,
) -> dict:
    """Restore files from ``snapshot_dir`` back to ``out_dir``.

    Returns statistics about restored files and created backups.
    """

    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise SnapshotError(f"缺少 manifest.json：{manifest_path}")

    manifest = json.loads(manifest_path.read_text("utf-8"))
    entries = manifest.get("entries", [])
    if not entries:
        raise SnapshotError("快照清单为空。")

    selected = _resolve_targets(entries, targets)
    out_dir = out_dir.resolve()
    files_dir = snapshot_dir / "files"
    if not files_dir.exists():
        raise SnapshotError("快照缺少 files/ 目录。")

    total_bytes = 0
    restored = 0
    backups = 0
    backup_dir: Path | None = None

    for entry in selected:
        rel = Path(entry["relpath"])
        src = files_dir / rel
        if not src.exists():
            raise SnapshotError(f"快照文件缺失：{rel}")
        dest = out_dir / rel
        _ensure_within_out(dest, out_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if verify_hash:
            checksum = sha256sum(src)
            if checksum != entry.get("sha256"):
                raise SnapshotError(f"快照文件校验失败：{rel}")

        if soft and dest.exists():
            if backup_dir is None:
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_dir = out_dir / TRASH_DIR_NAME / f"rollback-{ts}"
            backup_path = backup_dir / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup_path)
            backups += 1

        shutil.copy2(src, dest)
        restored += 1
        total_bytes += int(entry.get("size", 0))
        log_info(f"已回滚：{rel.as_posix()}")

    result = {
        "restored_files": restored,
        "restored_bytes": total_bytes,
        "backups": backups,
        "backup_dir": backup_dir.as_posix() if backup_dir else "",
        "snapshot_id": manifest.get("snapshot_id", ""),
    }

    log_ok(
        f"回滚完成：{restored} 个文件，总计 {total_bytes} 字节；备份 {backups} 个文件。"
        + (f" 备份目录：{result['backup_dir']}" if backups else "")
    )
    return result

