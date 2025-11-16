#!/usr/bin/env python3
r"""Batch normalise original transcripts to improve alignment quality."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Ensure the project root is importable when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from onepass._legacy_textnorm import (
    TextNormConfig,
    find_nonstandard_chars,
    normalize_text,
)
from onepass.ux import print_error, print_header, print_info, print_success, print_warning


@dataclass(slots=True)
class FileReport:
    """In-memory representation of per-file normalisation results."""

    path: Path
    changed: bool
    stats: Dict[str, int]
    snippets: List[Tuple[str, str]]
    suspect_counts: Dict[str, int]


def _parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse CLI arguments and return an ``argparse.Namespace`` instance."""

    parser = argparse.ArgumentParser(
        description="Normalise transcript TXT files to maximise alignment accuracy.",
    )
    parser.add_argument("--src", type=Path, default=Path("data/original_txt"), help="Source directory")
    parser.add_argument("--pattern", default="*.txt", help="Glob pattern for source files")
    parser.add_argument("--dst", type=Path, default=Path("data/original_txt"), help="Output directory")
    parser.add_argument("--inplace", action="store_true", help="Overwrite source files in-place")
    parser.add_argument("--no-backup", action="store_true", help="Disable .bak backup when overwriting")
    parser.add_argument("--dry-run", action="store_true", help="Only generate the report without writing files")
    parser.add_argument("--punct", choices=["ascii", "cjk", "keep"], default="ascii", help="Punctuation style")
    parser.add_argument("--t2s", action="store_true", help="Enable traditional to simplified conversion via opencc")
    parser.add_argument(
        "--custom-map",
        type=str,
        default="config/textnorm_custom_map.json",
        help="Optional JSON file that overrides compatibility character replacements",
    )
    parser.add_argument("--report", type=Path, default=Path("out/textnorm_report.md"), help="Markdown report path")
    parser.add_argument("--show-diff", type=int, default=3, help="Number of diff snippets per file in the report")
    return parser.parse_args(argv)


def _iter_source_files(src_dir: Path, pattern: str) -> Iterable[Path]:
    """Yield candidate files matching *pattern* under *src_dir*."""

    yield from sorted(src_dir.glob(pattern))


def _build_config(args: argparse.Namespace) -> TextNormConfig:
    """Create a :class:`TextNormConfig` from parsed CLI arguments."""

    return TextNormConfig(
        punct_style=args.punct,
        opencc_mode="t2s" if args.t2s else None,
        custom_map_path=args.custom_map,
    )


def _summarise_change(stats: Dict[str, int]) -> str:
    """Format key statistics for console output."""

    parts = [
        f"len {stats['len_before']}→{stats['len_after']}",
        f"compat {stats['replaced_compat']}",
        f"zw {stats['removed_zw']}",
        f"punct {stats['punct_changes']}",
    ]
    return ", ".join(parts)


def _clip_preview(text: str, start: int, end: int, radius: int = 24) -> str:
    """Extract a short preview around the modified span."""

    left = max(0, start - radius)
    right = min(len(text), end + radius)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    snippet = text[left:right].replace("\n", "⏎")
    return f"{prefix}{snippet}{suffix}"


def _diff_snippets(before: str, after: str, limit: int) -> List[Tuple[str, str]]:
    """Return representative diff snippets limited to *limit* entries."""

    if limit <= 0:
        return []
    matcher = SequenceMatcher(a=before, b=after)
    snippets: List[Tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before_clip = _clip_preview(before, i1, i2)
        after_clip = _clip_preview(after, j1, j2)
        snippets.append((before_clip, after_clip))
        if len(snippets) >= limit:
            break
    return snippets


def _write_backup(path: Path, original: str) -> None:
    """Persist a ``.bak`` backup next to *path* when overwriting in place."""

    backup_path = path.with_suffix(path.suffix + ".bak")
    backup_path.write_text(original, encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    """Write *content* to *path* ensuring the parent directory exists."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _format_char_table(char_counts: Dict[str, int], limit: int = 10) -> List[str]:
    """Return Markdown table rows describing the top suspicious characters."""

    rows = ["| 字符 | Unicode | 计数 |", "| --- | --- | --- |"]
    sorted_items = sorted(char_counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    for ch, count in sorted_items:
        codepoint = f"U+{ord(ch):04X}"
        display = ch if ch.strip() else repr(ch)
        rows.append(f"| {display} | {codepoint} | {count} |")
    if len(rows) == 2:
        rows.append("| (无) | - | 0 |")
    return rows


def _render_report(report_path: Path, entries: List[FileReport], config: TextNormConfig, args: argparse.Namespace) -> None:
    """Generate a Markdown report summarising normalisation outcomes."""

    total_files = len(entries)
    changed_files = sum(1 for entry in entries if entry.changed)
    totals: Dict[str, int] = {
        "replaced_compat": sum(entry.stats["replaced_compat"] for entry in entries),
        "removed_zw": sum(entry.stats["removed_zw"] for entry in entries),
        "punct_changes": sum(entry.stats["punct_changes"] for entry in entries),
        "bom_removed": sum(entry.stats["bom_removed"] for entry in entries),
        "space_collapses": sum(entry.stats["space_collapses"] for entry in entries),
    }
    aggregate_suspects: Dict[str, int] = {}
    for entry in entries:
        for ch, count in entry.suspect_counts.items():
            aggregate_suspects[ch] = aggregate_suspects.get(ch, 0) + count

    lines: List[str] = []
    lines.append("# 文本规范化报告")
    lines.append("")
    lines.append(f"- 源目录: `{args.src}`")
    lines.append(f"- 模式: {'dry-run' if args.dry_run else '写入'}")
    lines.append(f"- 标点风格: {config.punct_style}")
    lines.append(f"- 繁转简: {'启用' if config.opencc_mode else '关闭'}")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"* 文件总数: {total_files}")
    lines.append(f"* 发生变更: {changed_files}")
    lines.append(f"* 兼容部件替换总数: {totals['replaced_compat']}")
    lines.append(f"* 零宽/控制符清除总数: {totals['removed_zw']}")
    lines.append(f"* 标点调整总数: {totals['punct_changes']}")
    lines.append(f"* BOM 移除计数: {totals['bom_removed']}")
    lines.append(f"* 空白压缩行数: {totals['space_collapses']}")
    lines.append("")
    lines.append("## Top 10 怪字符")
    lines.append("")
    lines.extend(_format_char_table(aggregate_suspects))
    lines.append("")
    lines.append("## 文件统计")
    lines.append("")
    lines.append("| 文件 | 变更 | 兼容替换 | 零宽清除 | 标点调整 | 长度 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for entry in entries:
        name = entry.path.name
        flag = "是" if entry.changed else "否"
        stats = entry.stats
        length = f"{stats['len_before']}→{stats['len_after']}"
        lines.append(
            f"| {name} | {flag} | {stats['replaced_compat']} | {stats['removed_zw']} | {stats['punct_changes']} | {length} |"
        )
    lines.append("")
    lines.append("## 样例对比")
    lines.append("")
    for entry in entries:
        if not entry.snippets:
            continue
        lines.append(f"### {entry.path.name}")
        lines.append("")
        for idx, (before, after) in enumerate(entry.snippets, start=1):
            lines.append(f"**片段 {idx}**")
            lines.append("")
            lines.append("- Before: ``" + before + "``")
            lines.append("- After: ``" + after + "``")
            lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _resolve_destination(src: Path, dst: Path, inplace: bool) -> Path:
    """Return the effective destination directory for normalised files."""

    if inplace:
        return src
    return dst


def main(argv: List[str]) -> int:
    """CLI entry point returning an exit status code."""

    args = _parse_args(argv)
    config = _build_config(args)

    if not args.src.exists() or not args.src.is_dir():
        print_error(f"源目录不存在或不是文件夹: {args.src}")
        return 2

    # Collect candidate TXT files before processing.
    files = list(_iter_source_files(args.src, args.pattern))
    if not files:
        print_warning("未匹配到任何文件。")
        return 1

    destination_root = _resolve_destination(args.src, args.dst, args.inplace)
    entries: List[FileReport] = []  # Preserve per-file results for the report.
    changed_files = 0

    print_header("文本规范化")
    print_info(f"总计待处理文件: {len(files)}")

    for index, path in enumerate(files, start=1):
        print_info(f"[{index}/{len(files)}] {path.name}")
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print_warning(f"跳过无法解码的文件: {path}")
            continue

        # Run the core normalisation routine with the parsed configuration.
        normalised, stats = normalize_text(original, config)
        changed = normalised != original
        snippets = _diff_snippets(original, normalised, args.show_diff)
        suspect_counts = find_nonstandard_chars(original)
        entries.append(FileReport(path=path, changed=changed, stats=stats, snippets=snippets, suspect_counts=suspect_counts))

        if changed:
            changed_files += 1
            print_success("发现差异: " + _summarise_change(stats))
            if not args.dry_run:
                if args.inplace:
                    if not args.no_backup:
                        _write_backup(path, original)
                    target_path = path
                else:
                    target_path = destination_root / path.relative_to(args.src)
                # Persist the normalised text to the selected destination.
                _write_text(target_path, normalised)
        else:
            print_info("未检测到变化")

    # Always render the Markdown report summarising all statistics.
    _render_report(args.report, entries, config, args)
    print_success(f"报告已生成: {args.report}")

    if changed_files == 0:
        print_warning("未发现需要规范化的内容。")
        return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print_error("操作已取消。")
        raise SystemExit(2)
