#!/usr/bin/env python3
"""Validate relative Markdown links used in documentation."""

import argparse
import pathlib
import re
import sys
from typing import List

LINK_PATTERN = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)]+)\)")


def iter_markdown_files(root: pathlib.Path) -> List[pathlib.Path]:
    return sorted([p for p in root.rglob("*.md") if "node_modules" not in p.parts])


def is_relative(target: str) -> bool:
    if target.startswith(("http://", "https://", "mailto:", "tel:")):
        return False
    if target.startswith("#"):
        return False
    if target.startswith("data:"):
        return False
    return True


def validate_file(path: pathlib.Path, repo_root: pathlib.Path) -> List[str]:
    broken: List[str] = []
    text = path.read_text(encoding="utf-8")
    for match in LINK_PATTERN.finditer(text):
        target = match.group("target").split("#", 1)[0].strip()
        if not target or target.startswith("?"):
            continue
        if not is_relative(target):
            continue
        resolved = (path.parent / target).resolve()
        if not resolved.exists():
            try:
                rel = resolved.relative_to(repo_root)
            except ValueError:
                rel = resolved
            broken.append(f"{path.relative_to(repo_root)} -> {rel}")
    return broken


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=["README.md", "docs"],
        help="Files or directories to scan for Markdown links",
    )
    args = parser.parse_args()

    repo_root = pathlib.Path.cwd()
    failures: List[str] = []

    for item in args.paths:
        candidate = repo_root / item
        if candidate.is_dir():
            for md in iter_markdown_files(candidate):
                failures.extend(validate_file(md, repo_root))
        elif candidate.is_file():
            failures.extend(validate_file(candidate, repo_root))
        else:
            print(f"warn: {candidate} does not exist", file=sys.stderr)

    if failures:
        print("Broken links detected:")
        for entry in failures:
            print(f"  {entry}")
        return 1

    print("All Markdown links resolved successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
