"""scripts/env_check.py

用途：检查本地环境是否满足 OnePass Audio 项目运行要求，并生成报告文件。
依赖：标准库 pathlib、subprocess、json、sys、shutil、platform、datetime。
示例用法：
    python scripts/env_check.py
    python -m scripts.env_check
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "out"


@dataclass
class CheckOutcome:
    """Result of a single environment check."""

    name: str
    status: str
    detail: str
    fix: str
    data: dict | None = None

    @property
    def ok(self) -> bool:
        """Return True when the check fully passes."""

        return self.status == "OK"


def ensure_out_dir() -> Path:
    """Ensure that the shared out/ directory exists and return its path."""

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR


def run_command(args: Sequence[str]) -> tuple[int, str, str]:
    """Execute a command and return the exit code, stdout, and stderr."""

    try:
        completed = subprocess.run(
            args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except FileNotFoundError:
        return 127, "", ""


def check_python_version() -> CheckOutcome:
    """Verify that the running Python interpreter meets the minimum version."""

    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        return CheckOutcome(
            name="Python 版本",
            status="OK",
            detail=f"当前 Python {version}",
            fix="无需操作",
        )
    return CheckOutcome(
        name="Python 版本",
        status="FAIL",
        detail=f"当前 Python {version}，低于 3.10 要求",
        fix="请安装 Python 3.10 或更高版本并重新运行",
    )


def check_pip() -> CheckOutcome:
    """Confirm that pip is available for the current interpreter."""

    code, stdout, stderr = run_command([sys.executable, "-m", "pip", "--version"])
    if code == 0:
        detail = stdout or "pip 可用"
        return CheckOutcome("pip 可用性", "OK", detail, "无需操作")
    detail = stderr or "无法执行 python -m pip --version"
    return CheckOutcome(
        name="pip 可用性",
        status="FAIL",
        detail=detail,
        fix="请确保已安装 pip，或重新安装 Python 以包含 pip",
    )


def check_virtual_env() -> CheckOutcome:
    """Warn when the interpreter does not appear to be inside .venv."""

    venv_path = (PROJECT_ROOT / ".venv").resolve()
    current_prefix = Path(sys.prefix).resolve()
    if current_prefix == venv_path or venv_path in current_prefix.parents:
        detail = f"当前使用虚拟环境：{current_prefix}"
        return CheckOutcome("虚拟环境", "OK", detail, "无需操作")

    if hasattr(sys, "base_prefix") and sys.prefix != sys.base_prefix:
        detail = f"当前虚拟环境：{current_prefix}"
        return CheckOutcome("虚拟环境", "OK", detail, "无需操作")

    detail = "当前未启用项目虚拟环境 (.venv)"
    fix = "建议运行 python -m venv .venv 并激活虚拟环境"
    return CheckOutcome("虚拟环境", "WARN", detail, fix)


def parse_ffmpeg_version(output_lines: Iterable[str]) -> str | None:
    """Extract the ffmpeg version from command output."""

    for line in output_lines:
        line = line.strip()
        if line.lower().startswith("ffmpeg version"):
            return line
    return None


def check_ffmpeg() -> CheckOutcome:
    """Check whether ffmpeg is available and report its version."""

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        detail = "未在 PATH 中找到 ffmpeg 可执行文件"
        fix = "请通过 winget/choco 或访问 https://ffmpeg.org/download.html 安装"
        return CheckOutcome("ffmpeg", "FAIL", detail, fix)

    code, stdout, stderr = run_command([ffmpeg_path, "-version"])
    if code == 0:
        version_line = parse_ffmpeg_version(stdout.splitlines()) or f"已检测到 {ffmpeg_path}"
        return CheckOutcome("ffmpeg", "OK", version_line, "无需操作")
    detail = stderr or f"无法执行 {ffmpeg_path}"
    fix = "请确认 ffmpeg 安装完整，或重新安装后重试"
    return CheckOutcome("ffmpeg", "FAIL", detail, fix)


def check_whisper_cli() -> CheckOutcome:
    """Ensure whisper-ctranslate2 CLI is available via any entry point."""

    candidates: List[Sequence[str]] = [
        ["whisper-ctranslate2", "--help"],
        [sys.executable, "-m", "whisper_ctranslate2", "--help"],
    ]
    if platform.system() == "Windows":
        win_cli = PROJECT_ROOT / ".venv" / "Scripts" / "whisper-ctranslate2.exe"
        candidates.append([str(win_cli), "--help"])

    errors: List[str] = []
    for cmd in candidates:
        code, stdout, stderr = run_command(cmd)
        if code == 0:
            detail = stdout.splitlines()[0] if stdout else "whisper-ctranslate2 CLI 可用"
            return CheckOutcome("whisper-ctranslate2 CLI", "OK", detail, "无需操作")
        errors.append(" ".join(cmd) + f": {stderr or '不可用'}")

    detail = "；".join(errors)
    fix = "请安装 whisper-ctranslate2，例如运行 python -m pip install whisper-ctranslate2"
    return CheckOutcome(
        "whisper-ctranslate2 CLI",
        "FAIL",
        detail,
        fix,
        data={"install": ["whisper-ctranslate2"]},
    )


def check_python_dependencies() -> CheckOutcome:
    """Check required Python packages can be imported and report versions."""

    packages = ["faster_whisper", "rapidfuzz", "srt", "pandas"]
    missing: List[str] = []
    infos: List[str] = []
    for pkg in packages:
        try:
            module = __import__(pkg)
        except ImportError:
            missing.append(pkg)
            continue
        version = getattr(module, "__version__", None) or getattr(module, "version", None)
        if callable(version):
            try:
                version = version()
            except Exception:  # pragma: no cover - defensive
                version = None
        infos.append(f"{pkg} {version}" if version else f"{pkg} 已导入")

    if missing:
        detail = "缺少依赖：" + ", ".join(missing)
        fix = "请运行 python -m pip install -r requirements.txt"
        return CheckOutcome(
            "Python 依赖导入",
            "FAIL",
            detail,
            fix,
            data={"missing": missing},
        )

    detail = "，".join(infos) if infos else "依赖已导入"
    return CheckOutcome("Python 依赖导入", "OK", detail, "无需操作")


def run_all_checks() -> List[CheckOutcome]:
    """Execute every environment check and return their outcomes."""

    return [
        check_python_version(),
        check_pip(),
        check_virtual_env(),
        check_ffmpeg(),
        check_whisper_cli(),
        check_python_dependencies(),
    ]


def attempt_auto_fix(outcomes: List[CheckOutcome]) -> bool:
    """Try to automatically resolve known failure cases.

    Returns True when at least one fix command was executed.
    """

    executed = False

    for item in outcomes:
        if item.status == "OK":
            continue

        if item.name == "Python 依赖导入" and item.data and item.data.get("missing"):
            missing = list(dict.fromkeys(item.data["missing"]))
            print(f"[AUTO] 尝试安装缺失依赖：{', '.join(missing)}")
            code, _, stderr = run_command([sys.executable, "-m", "pip", "install", *missing])
            if code == 0:
                print("[AUTO] 缺失依赖安装完成")
            else:
                print(f"[AUTO] 缺失依赖安装失败：{stderr}")
            executed = True
            continue

        if item.name == "whisper-ctranslate2 CLI" and item.data:
            packages = item.data.get("install") or []
            if packages:
                print(f"[AUTO] 尝试安装 {', '.join(packages)}")
                code, _, stderr = run_command([sys.executable, "-m", "pip", "install", *packages])
                if code == 0:
                    print("[AUTO] whisper-ctranslate2 安装完成")
                else:
                    print(f"[AUTO] whisper-ctranslate2 安装失败：{stderr}")
                executed = True

    return executed


def build_markdown_report(timestamp: str, outcomes: List[CheckOutcome]) -> str:
    """Create the Markdown representation of the report."""

    lines = [
        "# 环境自检报告",
        f"- 生成时间：{timestamp}",
        "",
        "| 检查项 | 状态 | 详情 | 如何修复 |",
        "| --- | --- | --- | --- |",
    ]
    symbols = {"OK": "✅ 通过", "WARN": "⚠️ 警告", "FAIL": "❌ 未通过"}
    for item in outcomes:
        status_label = symbols.get(item.status, item.status)
        lines.append(
            f"| {item.name} | {status_label} | {item.detail.replace('|', '\\|')} | {item.fix.replace('|', '\\|')} |"
        )
    return "\n".join(lines) + "\n"


def build_json_report(timestamp: str, outcomes: List[CheckOutcome]) -> dict:
    """Create the JSON-friendly representation of the report."""

    summary = {
        "ok_count": sum(1 for item in outcomes if item.status == "OK"),
        "fail_count": sum(1 for item in outcomes if item.status == "FAIL"),
        "warn_count": sum(1 for item in outcomes if item.status == "WARN"),
    }
    items = [
        {
            "name": item.name,
            "ok": item.ok,
            "status": item.status,
            "detail": item.detail,
            "fix": item.fix,
        }
        for item in outcomes
    ]
    return {"checked_at": timestamp, "items": items, "summary": summary}


def print_console(outcomes: List[CheckOutcome]) -> None:
    """Print a human-readable summary to stdout."""

    for item in outcomes:
        label = "OK" if item.status == "OK" else item.status
        message = f"[{label}] {item.name}: {item.detail}"
        if item.status != "OK":
            message += f" | 修复: {item.fix}"
        print(message)


def main() -> int:
    """Run all environment checks and produce output files."""

    ensure_out_dir()
    timestamp = datetime.now().isoformat()
    outcomes = run_all_checks()
    if attempt_auto_fix(outcomes):
        print("[AUTO] 自动修复已执行，重新检查环境……")
        outcomes = run_all_checks()
        timestamp = datetime.now().isoformat()
    print_console(outcomes)

    json_report = build_json_report(timestamp, outcomes)
    markdown_report = build_markdown_report(timestamp, outcomes)

    json_path = OUT_DIR / "env_report.json"
    md_path = OUT_DIR / "env_report.md"
    json_path.write_text(json.dumps(json_report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report, encoding="utf-8")

    if any(item.status == "FAIL" for item in outcomes):
        return 2
    if any(item.status == "WARN" for item in outcomes):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - safeguard
        print(f"[ERROR] 环境自检脚本执行失败：{exc}")
        sys.exit(2)
