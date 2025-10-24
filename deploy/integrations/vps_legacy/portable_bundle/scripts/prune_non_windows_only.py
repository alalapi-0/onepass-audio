from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "legacy"
MOVE_CANDIDATES = [
    "apps/ios",
    "apps/mac",
    "apps/macos",
    "apps/windows",
    "server",
    "docs/BUILD_IOS.md",
    "docs/BUILD_MAC.md",
    "docs/BUILD_DESKTOP.md",
    "docs/NETWORK_EXTENSION.md",
    "docs/NE*.md",
]
DELETE_CANDIDATES: list[str] = [
    # 添加需要直接删除的路径（可包含通配符）
]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def iter_paths(patterns: Iterable[str]) -> Iterable[Path]:
    for pattern in patterns:
        if any(ch in pattern for ch in "*?[]"):
            yield from ROOT.glob(pattern)
        else:
            yield ROOT / pattern


def safe_move(path: Path, dst_dir: Path, moved: list[str]) -> None:
    if not path.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / path.name
    if target.exists():
        idx = 1
        while (dst_dir / f"{path.name}.bak{idx}").exists():
            idx += 1
        target = dst_dir / f"{path.name}.bak{idx}"
    shutil.move(str(path), str(target))
    moved.append(str(path.relative_to(ROOT)))


def safe_delete(path: Path, deleted: list[str]) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    deleted.append(str(path.relative_to(ROOT)))


def rewrite_workflow_for_windows_only() -> str:
    if not WORKFLOW.exists():
        return "no-op（未找到 ci.yml）"
    original = WORKFLOW.read_text(encoding="utf-8")
    backup = WORKFLOW.with_suffix(".yml.bak")
    backup.write_text(original, encoding="utf-8")
    WORKFLOW.write_text(
        """name: Minimal CI (Windows Local Only)

on: [push, pull_request]

jobs:
  noop:
    runs-on: ubuntu-latest
    steps:
      - run: echo 'CI minimized for Windows local tool; iOS/macOS jobs removed.'
""",
        encoding="utf-8",
    )
    return "ci.yml 已重写（备份保存为 ci.yml.bak）"


def collect_archived_records(moved: List[str]) -> list[str]:
    records: list[str] = []
    seen: set[str] = set()

    def add_record(source: str, target_name: str) -> None:
        entry = f"{source} -> legacy/{target_name}"
        if entry not in seen:
            seen.add(entry)
            records.append(entry)

    # Newly moved paths first
    for rel in moved:
        base = Path(rel).name
        matches = sorted(LEGACY.glob(f"{base}*"))
        if matches:
            add_record(rel, matches[0].name)
        else:
            add_record(rel, base)

    # Include existing archives so report stays complete on repeated runs
    for pattern in MOVE_CANDIDATES:
        base = Path(pattern).name
        parent = Path(pattern).parent
        is_wildcard = any(ch in pattern for ch in "*?[]")
        if is_wildcard:
            for item in sorted(LEGACY.glob(base)):
                src_parent = parent
                if str(src_parent) in {"", "."}:
                    source = item.name
                else:
                    source = f"{src_parent.as_posix()}/{item.name}"
                add_record(source, item.name)
        else:
            for item in sorted(LEGACY.glob(f"{base}*")):
                if item.name == base:
                    source = pattern
                else:
                    source = f"{pattern}（已重命名为 {item.name}）"
                add_record(source, item.name)

    records.sort()
    return records


def main() -> int:
    moved: list[str] = []
    deleted: list[str] = []

    LEGACY.mkdir(exist_ok=True)

    for candidate in iter_paths(MOVE_CANDIDATES):
        safe_move(candidate, LEGACY, moved)

    for candidate in iter_paths(DELETE_CANDIDATES):
        safe_delete(candidate, deleted)

    workflow_note = rewrite_workflow_for_windows_only()
    archived_records = collect_archived_records(moved)

    report_path = ROOT / "PROJECT_PRUNE_REPORT.md"
    lines: list[str] = [
        "# 项目精简报告（Windows 本地一键版）",
        f"_Generated at: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}_",
        "",
        "## 已归档到 `legacy/`",
    ]
    if archived_records:
        lines.extend(f"- {item}" for item in archived_records)
    else:
        lines.append("- （无）")

    lines.extend(
        [
            "",
            "## 已删除",
        ]
    )
    if deleted:
        lines.extend(f"- {item}" for item in deleted)
    else:
        lines.append("- （无）")

    lines.extend(
        [
            "",
            "## CI 调整",
            f"- {workflow_note}",
            "",
            "## 保留的关键路径",
            "- requirements.txt",
            "- scripts/project_doctor.py",
            "- scripts/prune_non_windows_only.py",
            "- main.py",
            "- run_vpn.bat",
            "- artifacts/ （运行时自动创建）",
            "- PROJECT_HEALTH_REPORT.md / PROJECT_PRUNE_REPORT.md",
        ]
    )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[prune] moved: {len(moved)}, deleted: {len(deleted)}")
    print(f"[prune] report: {report_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
