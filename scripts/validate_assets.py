#!/usr/bin/env python3
"""Validate alignment assets by checking matching stems between JSON and TXT inputs."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, Set


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that JSON and TXT assets share matching stems before alignment."
    )
    parser.add_argument(
        "--json-dir",
        type=Path,
        default=Path("data/asr-json"),
        help="Directory containing ASR JSON files (default: data/asr-json)",
    )
    parser.add_argument(
        "--txt-dir",
        type=Path,
        default=Path("data/original_txt"),
        help="Directory containing original TXT files (default: data/original_txt)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit the number of stems to display for each category (default: 20)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print counts without listing sample stems.",
    )
    return parser.parse_args(argv)


def ensure_directory(path: Path, flag: str, description: str) -> Path:
    raw = path.expanduser()
    candidate = raw if raw.is_absolute() else Path.cwd() / raw
    try:
        path_display = candidate.relative_to(Path.cwd())
    except ValueError:
        path_display = candidate
    path_display_str = str(path_display)
    if not candidate.exists():
        print(
            f"Error: {description} directory '{path_display_str}' does not exist.\n"
            f"Please create it or provide a different path with {flag}.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not candidate.is_dir():
        print(
            f"Error: {description} path '{path_display_str}' is not a directory.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return candidate.resolve()


def collect_stems(directory: Path, suffixes: Iterable[str]) -> Set[str]:
    suffix_set = {s.lower() for s in suffixes}
    stems: Set[str] = set()
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix.lower() in suffix_set:
            stems.add(entry.stem)
    return stems


def format_examples(items: Iterable[str], limit: int) -> str:
    items_list = sorted(items)
    if limit >= 0:
        items_list = items_list[:limit]
    return "[" + ", ".join(items_list) + "]" if items_list else "[]"


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    json_dir = ensure_directory(args.json_dir, "--json-dir", "JSON")
    txt_dir = ensure_directory(args.txt_dir, "--txt-dir", "TXT")

    json_stems = collect_stems(json_dir, {".json"})
    txt_stems = collect_stems(txt_dir, {".txt"})

    both = sorted(json_stems & txt_stems)
    only_json = sorted(json_stems - txt_stems)
    only_txt = sorted(txt_stems - json_stems)

    def print_category(name: str, stems: list[str]) -> None:
        count = len(stems)
        if args.quiet:
            print(f"{name}: {count} stems")
            return
        examples = format_examples(stems, args.limit)
        print(f"{name}: {count} stems, e.g. {examples}")

    print_category("both", both)
    print_category("only_json", only_json)
    print_category("only_txt", only_txt)

    if both:
        sample = both[0]
        json_sample = json_dir / f"{sample}.json"
        txt_sample = txt_dir / f"{sample}.txt"
        json_rel = Path(os.path.relpath(json_sample, Path.cwd()))
        txt_rel = Path(os.path.relpath(txt_sample, Path.cwd()))
        print(
            "建议下一步命令："
            f"python scripts/make_markers.py --json {json_rel} --original {txt_rel} --outdir out"
        )
        return 0

    print("未找到匹配的 stem，请检查文件命名并重试。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
