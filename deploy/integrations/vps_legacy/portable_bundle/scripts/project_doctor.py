from __future__ import annotations

import importlib
import contextlib
import io
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import argparse

MIN_PYTHON = (3, 8)
REQUIRED_PACKAGES = [
    "requests",
    "paramiko",
    "qrcode",
    "PySimpleGUI",
]

PLATFORM_CHOICES = {
    "windows": "Windows",
    "macos": "macOS",
}
ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ROOT / "PROJECT_HEALTH_REPORT.md"
LEGACY_DIR = ROOT / "legacy"


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str


def check_python_version() -> CheckResult:
    current = sys.version_info
    ok = current >= MIN_PYTHON
    message = f"当前 Python 版本：{sys.version.split()[0]}（要求 ≥ {'.'.join(map(str, MIN_PYTHON))}）"
    if not ok:
        message += " —— 请升级 Python"
    return CheckResult("Python 版本", ok, message)


def check_pip() -> CheckResult:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        message = completed.stdout.strip() or completed.stderr.strip() or "pip 可用"
        ok = True
    except Exception as exc:  # noqa: BLE001 - surface raw exception message
        message = f"无法调用 pip：{exc}"
        ok = False
    return CheckResult("pip 可用性", ok, message)


def check_packages(packages: Iterable[str]) -> CheckResult:
    missing: list[str] = []
    loaded: list[str] = []
    for pkg in packages:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    importlib.import_module(pkg)
        except Exception:  # noqa: BLE001 - 捕获所有导入错误
            missing.append(pkg)
        else:
            loaded.append(pkg)
    if not missing:
        message = "依赖已安装：" + ", ".join(loaded)
        ok = True
    else:
        message = (
            "缺少依赖：" + ", ".join(missing) +
            "。请执行 `pip install -r requirements.txt`"
        )
        ok = False
    return CheckResult("Python 依赖", ok, message)


def check_vultr_api_key() -> CheckResult:
    if os.getenv("VULTR_API_KEY"):
        return CheckResult("VULTR_API_KEY", True, "环境变量已设置。")
    return CheckResult(
        "VULTR_API_KEY",
        False,
        "未检测到 VULTR_API_KEY。请在运行一键部署前设置该环境变量。",
    )


def ensure_artifacts_dir() -> CheckResult:
    try:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        ok = True
        message = f"已确认或创建目录：{ARTIFACTS_DIR.relative_to(ROOT)}"
    except Exception as exc:  # noqa: BLE001
        ok = False
        message = f"创建 artifacts 目录失败：{exc}"
    return CheckResult("artifacts 目录", ok, message)


def build_report(
    results: list[CheckResult], generated_at: datetime, platform: str
) -> str:
    status_summary = [
        f"- [{'✅' if r.ok else '❌'}] {r.name}：{r.message}" for r in results
    ]
    notes: list[str] = []
    platform_label = PLATFORM_CHOICES.get(platform, platform)
    notes.append(f"- 当前选择的本机系统：{platform_label}")
    actual_platform = sys.platform
    if platform == "windows" and os.name != "nt":
        notes.append(
            "- ⚠️ 检测到当前并非 Windows 系统，部分检查结果可能不准确。"
        )
    if platform == "macos" and actual_platform != "darwin":
        notes.append("- ⚠️ 检测到当前并非 macOS 系统，部分检查结果可能不准确。")
    if LEGACY_DIR.exists():
        notes.append("- 项目已精简，跨平台与旧脚本已归档到 legacy/。")
    else:
        notes.append("- 暂未发现 legacy/ 目录。如已执行精简脚本，将在此处提示归档信息。")
    report_lines = [
        f"# 项目体检报告（{platform_label} 本地一键版）",
        f"_Generated at: {generated_at.isoformat().replace("+00:00", "Z")}_",
        "",
        "## 检查结果",
        *status_summary,
        "",
        "## 额外信息",
        *notes,
    ]
    return "\n".join(report_lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PrivateTunnel 环境体检")
    parser.add_argument(
        "--platform",
        choices=sorted(PLATFORM_CHOICES),
        default="windows",
        help="指定本机系统类型。",
    )
    return parser.parse_args()


def check_selected_platform(platform: str) -> CheckResult:
    label = PLATFORM_CHOICES.get(platform, platform)
    current = sys.platform
    if platform == "windows":
        ok = os.name == "nt"
    elif platform == "macos":
        ok = current == "darwin"
    else:
        ok = False
    if ok:
        message = f"检测到平台：{label}"
    else:
        actual = PLATFORM_CHOICES.get("macos" if current == "darwin" else "windows", current)
        message = f"当前平台为：{actual}"
    return CheckResult("操作系统", ok, message)


def main() -> int:
    args = parse_args()
    platform = args.platform
    generated_at = datetime.now(timezone.utc)
    results = [
        check_selected_platform(platform),
        check_python_version(),
        check_pip(),
        check_packages(REQUIRED_PACKAGES),
        check_vultr_api_key(),
        ensure_artifacts_dir(),
    ]

    report_content = build_report(results, generated_at, platform)
    REPORT_PATH.write_text(report_content, encoding="utf-8")
    print(report_content)
    print(f"[doctor] 报告已生成：{REPORT_PATH.relative_to(ROOT)}")

    success = all(r.ok for r in results)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
