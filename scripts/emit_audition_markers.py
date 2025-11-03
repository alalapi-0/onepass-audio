"""根据 EDL JSON 生成可供 Adobe Audition 导入的标记 CSV。"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable

from onepass.markers import ensure_csv_header, seconds_to_hmsms

HEADER = ["Name", "Start", "Duration", "Type", "Description"]


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edl", required=True, type=Path, help="输入的 .edl.json 文件路径")
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="输出的 Audition 标记 CSV 路径",
    )
    return parser.parse_args()


def _iter_cut_actions(payload: dict) -> Iterable[dict]:
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise ValueError("EDL JSON 必须包含 actions 列表")
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("type") != "cut":
            continue
        start = action.get("start")
        end = action.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if end <= start:
            continue
        yield {
            "start": float(start),
            "end": float(end),
            "reason": action.get("reason") or "manual",
        }


def _build_rows(cuts: Iterable[dict]) -> list[list[str]]:
    rows: list[list[str]] = [HEADER.copy()]
    for index, action in enumerate(cuts, start=1):
        start = action["start"]
        end = action["end"]
        duration = max(0.0, end - start)
        name_suffix = f"{index:03d}"
        description = action.get("reason", "manual cut")
        rows.append(
            [
                f"CUT_{name_suffix}",
                seconds_to_hmsms(start),
                "00:00:00.000",
                "Marker",
                f"cut start ({description})",
            ]
        )
        rows.append(
            [
                f"END_{name_suffix}",
                seconds_to_hmsms(end),
                "00:00:00.000",
                "Marker",
                f"cut end ({description})",
            ]
        )
        rows.append(
            [
                f"CUTSPAN_{name_suffix}",
                seconds_to_hmsms(start),
                seconds_to_hmsms(duration),
                "Marker",
                description,
            ]
        )
    ensure_csv_header(rows[0])
    return rows


def main() -> None:
    args = _parse_arguments()
    edl_path: Path = args.edl
    out_path: Path = args.out

    if not edl_path.exists():
        raise SystemExit(f"未找到输入文件: {edl_path}")

    try:
        payload = json.loads(edl_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - CLI 容错
        raise SystemExit(f"读取 EDL JSON 失败: {exc}")

    cuts = list(_iter_cut_actions(payload))
    rows = _build_rows(cuts)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"已写出 {len(rows) - 1} 条标记 → {out_path}")


if __name__ == "__main__":
    main()
