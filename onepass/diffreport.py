"""onepass.diffreport
用途: 将句级保留/删除结果写成 Markdown 差异报告。
依赖: Python 标准库 pathlib、textwrap；内部工具 ``onepass.types.fmt_time_s``。
示例: ``from onepass.diffreport import write_diff_markdown``。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .types import fmt_time_s


def _fmt_time_range(entry: dict | None) -> str:
    if not entry:
        return "-"
    start = fmt_time_s(float(entry.get("start", 0.0)))
    end = fmt_time_s(float(entry.get("end", 0.0)))
    score = entry.get("score")
    if score is None:
        return f"{start} ~ {end}"
    return f"{start} ~ {end} (score={score:.2f})"


def _fmt_deleted(entries: Iterable[dict]) -> str:
    items = [
        f"{fmt_time_s(float(item.get('start', 0.0)))} ~ {fmt_time_s(float(item.get('end', 0.0)))}"
        + (f" (score={float(item.get('score', 0.0)):.2f})" if "score" in item else "")
        for item in entries
    ]
    if not items:
        return "-"
    return "<br/>".join(items)


def _escape(text: str) -> str:
    return text.replace("|", "\\|")


def write_diff_markdown(stem: str, diff_items: list[dict], outdir: Path) -> Path:
    """将差异项目写入 ``out/<stem>.diff.md`` 并返回路径。"""

    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{stem}.diff.md"
    lines = [
        f"# {stem} 差异报告",
        "",
        "| # | 原文句子 | 命中次数 | 保留哪一遍(起止) | 删除了哪些(起止) | 最高相似度 | 备注 |",
        "| - | - | - | - | - | - | - |",
    ]
    for idx, item in enumerate(diff_items, start=1):
        raw = _escape(str(item.get("sent_raw", "")))
        hit_count = int(item.get("hit_count", 0))
        kept = _fmt_time_range(item.get("kept"))
        deleted = _fmt_deleted(item.get("deleted", []))
        max_score = float(item.get("max_score", 0.0))
        remark = _escape(str(item.get("remark", "")))
        lines.append(
            f"| {idx} | {raw} | {hit_count} | {kept} | {deleted} | {max_score:.2f} | {remark} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), "utf-8")
    return path
