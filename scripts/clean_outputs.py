"""Utility to safely clean generated outputs under ``out/``.

This script deletes or moves to trash the derived assets produced by the
OnePass Audio pipeline. Only files inside ``out/`` are touched and the default
behaviour is to relocate them into ``out/.trash/<timestamp>/`` so that the user
can recover them when necessary.

The implementation intentionally uses only the Python standard library and
relies on ``pathlib.Path`` for path manipulations to satisfy project
requirements.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence

PROJ_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJ_ROOT / "out"
TRASH_DIR = OUT_DIR / ".trash"

GENERATED_SET = {"generated", "subs", "edl", "markers", "logs"}
WHAT_MAP = {
    "subs": ("*.keepLast.clean.srt", "*.keepLast.clean.vtt", "*.keepLast.clean.txt"),
    "edl": ("*.keepLast.edl.json",),
    "markers": ("*.keepLast.audition_markers.csv",),
    "logs": ("*.log", "*.diff.md"),
    "render": ("*.clean.wav", "*.list.txt"),
}


class CleanupError(RuntimeError):
    """Raised when the cleanup process cannot continue safely."""


@dataclass
class CleanResult:
    """Summary of a cleanup operation."""

    files: int
    bytes_total: int
    skipped: int = 0


def _resolve_paths_for_stem(stem: str, categories: set[str]) -> Iterator[Path]:
    """Yield candidate paths for ``stem`` limited to ``categories``."""

    stem = stem.strip()
    if not stem:
        return

    patterns: set[str] = set()
    expanded: set[str] = set()
    if "all" in categories:
        expanded.update({"generated", "render"})
    expanded.update(categories)
    if "generated" in expanded:
        expanded.update(GENERATED_SET)

    for key in sorted(expanded):
        if key in {"generated", "all"}:
            continue
        if key not in WHAT_MAP:
            continue
        for pattern in WHAT_MAP[key]:
            if pattern.endswith(".list.txt"):
                yield from OUT_DIR.glob(f"{stem}.*.list.txt")
            else:
                candidate = OUT_DIR / pattern.replace("*", stem)
                yield candidate


def _resolve_paths_for_all(categories: set[str]) -> Iterator[Path]:
    """Yield candidate paths for ``--all`` covering ``categories``."""

    expanded: set[str] = set()
    if "all" in categories:
        expanded.update({"generated", "render"})
    expanded.update(categories)
    if "generated" in expanded:
        expanded.update(GENERATED_SET)

    patterns: set[str] = set()
    for key in sorted(expanded):
        if key in {"generated", "all"}:
            continue
        patterns.update(WHAT_MAP.get(key, ()))

    seen: set[Path] = set()
    for pattern in patterns:
        if pattern.endswith(".list.txt"):
            iterator = OUT_DIR.glob(pattern)
        elif "*" in pattern:
            iterator = OUT_DIR.glob(pattern)
        else:
            iterator = OUT_DIR.glob(pattern)
        for candidate in iterator:
            if candidate in seen:
                continue
            seen.add(candidate)
            yield candidate


def _ensure_within_out(path: Path) -> None:
    """Ensure ``path`` resides inside ``out/``."""

    try:
        path.resolve().relative_to(OUT_DIR.resolve())
    except ValueError as exc:  # pragma: no cover - defensive
        raise CleanupError(f"路径越界：{path}") from exc


def human_size(num_bytes: int) -> str:
    """Return a human readable string for ``num_bytes`` (base 1024)."""

    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num_bytes)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if value >= 100 or units[idx] == "B":
        return f"{value:.0f} {units[idx]}"
    if value >= 10:
        return f"{value:.1f} {units[idx]}"
    return f"{value:.2f} {units[idx]}"


def _collect_targets(stems: Sequence[str] | None, categories: set[str]) -> list[Path]:
    """Collect files targeted for removal/move."""

    if not OUT_DIR.exists():
        return []

    files: list[Path] = []
    if stems is None:
        iterator = _resolve_paths_for_all(categories)
    else:
        iterator = (
            path
            for stem in stems
            for path in _resolve_paths_for_stem(stem, categories)
        )
    for path in iterator:
        if not path.exists():
            continue
        try:
            _ensure_within_out(path)
        except CleanupError:
            raise
        if path.is_dir():
            continue
        files.append(path)
    unique_files = []
    seen = set()
    for path in files:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(path)
    unique_files.sort(key=lambda p: (str(p.parent), p.name))
    return unique_files


def _confirm(prompt: str) -> bool:
    """Ask the user for a yes/no confirmation."""

    reply = input(f"{prompt} [y/N]: ").strip().lower()
    return reply in {"y", "yes"}


def perform_cleanup(
    *,
    stems: Sequence[str] | None,
    categories: set[str],
    mode: str,
    assume_yes: bool = False,
    dry_run: bool = False,
    reporter: Callable[[str], None] | None = print,
    emit_summary: bool = True,
) -> CleanResult:
    """Perform cleanup and return summary statistics."""

    if mode not in {"trash", "hard"}:
        raise CleanupError(f"未知清理模式：{mode}")

    targets = _collect_targets(stems, categories)
    if not targets:
        return CleanResult(files=0, bytes_total=0, skipped=0)

    total_bytes = 0
    for path in targets:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass

    action_desc = "移动到 .trash/" if mode == "trash" else "永久删除"
    if reporter:
        reporter("扫描结果：")
    for path in targets:
        size = 0
        try:
            size = path.stat().st_size
        except OSError:
            pass
        rel = path.resolve().relative_to(OUT_DIR.resolve())
        if reporter:
            reporter(f"  - {rel} ({human_size(size)})")
    if reporter:
        reporter(f"共 {len(targets)} 个文件，合计 {human_size(total_bytes)}。")
    if dry_run:
        if reporter:
            reporter("dry-run 模式：未执行任何清理操作。")
        if emit_summary and reporter:
            reporter(
                f"CLEAN_SUMMARY files=0 bytes={total_bytes} skipped=0"
            )
        return CleanResult(files=0, bytes_total=total_bytes)

    if not assume_yes:
        if not _confirm(f"确认{action_desc}以上文件吗？"):
            raise CleanupError("用户取消操作。")
        if mode == "hard":
            if not _confirm("危险操作：将永久删除，确认继续吗？"):
                raise CleanupError("用户取消操作。")

    cleaned = 0
    skipped = 0
    if mode == "trash":
        timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        batch_root = TRASH_DIR / timestamp
        batch_root.mkdir(parents=True, exist_ok=True)
        for path in targets:
            if TRASH_DIR in path.parents:
                if reporter:
                    reporter(f"跳过已在 .trash/ 中的文件：{path.name}")
                skipped += 1
                continue
            dest_dir = batch_root / path.parent.relative_to(OUT_DIR)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / path.name
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{path.stem}_{counter}{path.suffix}"
                counter += 1
            try:
                shutil.move(str(path), dest)
            except OSError as exc:
                raise CleanupError(f"移动失败：{path} -> {dest} ({exc})") from exc
            cleaned += 1
    else:  # hard delete
        for path in targets:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise CleanupError(f"删除失败：{path} ({exc})") from exc
            cleaned += 1

    if reporter:
        reporter(f"已{action_desc} {cleaned} 个文件，共 {human_size(total_bytes)}。")
        if skipped:
            reporter(f"另外跳过 {skipped} 个文件（已在 .trash/ 中）。")
    if emit_summary and reporter:
        reporter(
            f"CLEAN_SUMMARY files={cleaned} bytes={total_bytes} skipped={skipped}"
        )
    return CleanResult(files=cleaned, bytes_total=total_bytes, skipped=skipped)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="清理 out/ 下的生成产物，可安全移动至 .trash/ 或直接删除。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python scripts/clean_outputs.py --stem 001 --what generated --trash\n"
            "  python scripts/clean_outputs.py --all --what all --hard --yes\n"
        ),
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stem", nargs="+", help="指定要清理的章节 stem，可多个")
    group.add_argument("--all", action="store_true", help="清理 out/ 下所有匹配的产物")

    parser.add_argument(
        "--what",
        default="generated",
        help="清理范围，逗号分隔：generated|subs|edl|markers|logs|render|all（默认 generated）",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--trash", action="store_true", help="移动到 out/.trash/（默认）")
    mode_group.add_argument("--hard", action="store_true", help="直接删除（危险，不可恢复）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览将要清理的文件，不执行")
    parser.add_argument("--yes", action="store_true", help="自动确认所有提示")

    return parser.parse_args(argv)


def _normalize_categories(raw: str) -> set[str]:
    cats = set()
    for part in raw.split(","):
        part = part.strip().lower()
        if not part:
            continue
        cats.add(part)
    if not cats:
        cats.add("generated")
    return cats


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        categories = _normalize_categories(args.what)
        if args.all:
            stems: Sequence[str] | None = None
        else:
            stems = args.stem

        if args.hard:
            mode = "hard"
        else:
            mode = "trash"

        if not OUT_DIR.exists():
            print("提示：输出目录 out/ 不存在。")
            return 1

        try:
            result = perform_cleanup(
                stems=stems,
                categories=categories,
                mode=mode,
                assume_yes=args.yes,
                dry_run=args.dry_run,
            )
        except CleanupError as exc:
            print(f"错误：{exc}")
            return 2

        if args.dry_run:
            return 0
        if result.files == 0:
            return 1
        return 0
    except CleanupError as exc:
        print(f"错误：{exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
