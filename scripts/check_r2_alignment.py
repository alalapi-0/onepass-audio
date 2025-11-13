"""Batch alignment quality checker for R2 deliverables."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_report(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _first_lines(path: Path, limit: int = 5) -> list[str]:
    if not path or not path.exists():
        return [f"<missing {path}>" if path else "<path not provided>"]
    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(limit), handle):
                lines.append(line.rstrip("\n"))
    except OSError as exc:
        lines.append(f"<error reading {path}: {exc}>")
    return lines


def evaluate(report_path: Path) -> int:
    report = _load_report(report_path)
    prep_section = report.get("prep_norm", {})
    items = prep_section.get("items") or []
    passes: list[dict[str, Any]] = []
    fails: list[dict[str, Any]] = []
    for item in items:
        stem = item.get("stem") or Path(item.get("file", "")).stem
        if not stem:
            continue
        if str(item.get("status")) != "ok":
            fails.append({"stem": stem, "reason": item.get("message", "failed stage")})
            continue
        total_lines = int(item.get("align_total_lines") or 0)
        split_mode = str(item.get("split_mode") or "")
        entry = {
            "stem": stem,
            "lines": total_lines,
            "mode": split_mode,
            "align_path": Path(item.get("align_path", "")) if item.get("align_path") else None,
            "debug_path": Path(item.get("align_debug_path", "")) if item.get("align_debug_path") else None,
            "reason": "",
        }
        if total_lines >= 100 and split_mode == "punct+len":
            passes.append(entry)
        else:
            entry["reason"] = (
                f"lines={total_lines} (<100)" if total_lines < 100 else f"split_mode={split_mode}"
            )
            fails.append(entry)

    print(f"总计 {len(passes) + len(fails)} 个 stem，达标 {len(passes)}，未达标 {len(fails)}。")
    if passes:
        print("\nPASS 样本：")
        for entry in passes:
            print(f"  - {entry['stem']} lines={entry['lines']} mode={entry['mode']}")
    if fails:
        print("\nFAIL 样本：")
        for entry in fails:
            stem = entry.get("stem")
            reason = entry.get("reason") or "未达标"
            print(f"  - {stem}: {reason}")
            align_path: Path | None = entry.get("align_path")
            debug_path: Path | None = entry.get("debug_path")
            print("    align preview:")
            for line in _first_lines(align_path)[:5]:
                print(f"      {line}")
            print("    debug preview:")
            for line in _first_lines(debug_path)[:5]:
                print(f"      {line}")
    return 0 if not fails else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 batch_report.json 中的分句达标情况")
    parser.add_argument("report", help="batch_report.json 路径")
    args = parser.parse_args()
    return evaluate(Path(args.report).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
