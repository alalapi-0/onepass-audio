# ==== BEGIN: OnePass Patch · R3 (env_check_win) ====
"""Windows-specific environment checker for OnePass Audio."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJ_ROOT = Path(__file__).resolve().parent.parent
SYNC_ENV = PROJ_ROOT / "deploy" / "sync" / "sync.env"

Status = str


def _run_command(cmd: List[str], verbose: bool = False) -> Tuple[int, str]:
    """Execute a command and return exit code + combined output."""
    if verbose:
        print(f"[debug] exec: {' '.join(cmd)}")
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, ""
    output = (completed.stdout or "")
    if completed.stderr:
        output += ("\n" if output else "") + completed.stderr
    return completed.returncode, output.strip()


def _is_admin() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _load_sync_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not SYNC_ENV.exists():
        return env
    for line in SYNC_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip().strip('"')
    return env


def _format_status(status: Status, name: str, detail: str) -> str:
    return f"[{status}] {name}: {detail}"


def _print_fix_suggestion() -> None:
    print("修复建议：可运行：python scripts/install_openssh_win.py（需管理员）")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OnePass Audio Windows 环境检查")
    parser.add_argument("--verbose", action="store_true", help="打印底层命令")
    parser.add_argument("--json", action="store_true", help="输出 JSON 总结")
    args = parser.parse_args(argv)

    checks: List[Dict[str, Any]] = []
    statuses: List[str] = []
    suggestions: List[str] = []

    def record(name: str, status: Status, detail: str, data: Dict[str, Any] | None = None) -> None:
        checks.append({"name": name, "status": status, "detail": detail, "data": data or {}})
        statuses.append(status)
        if not args.json:
            print(_format_status(status, name, detail))

    for label, binary in [("ssh", "ssh"), ("scp", "scp")]:
        code, output = _run_command([binary, "-V"], verbose=args.verbose)
        if code == 0 and output:
            first_line = output.splitlines()[0]
            record(f"{label} 版本", "OK", first_line, {"binary": binary, "version": first_line})
        elif code == 127:
            detail = f"未检测到 {label} 命令"
            record(f"{label} 版本", "FAIL", detail, {"binary": binary})
            suggestions.append("python scripts/install_openssh_win.py（需管理员）")
        else:
            record(f"{label} 版本", "FAIL", output or f"无法执行 {label} -V", {"binary": binary})
            suggestions.append("python scripts/install_openssh_win.py（需管理员）")

    code, output = _run_command(["rsync", "--version"], verbose=args.verbose)
    if code == 0 and output:
        first_line = output.splitlines()[0]
        record("rsync", "OK", first_line, {"version": first_line})
    elif code == 127:
        record("rsync", "WARN", "未检测到 rsync，将回退至 scp", {})
    else:
        record("rsync", "WARN", output or "无法执行 rsync --version", {})

    admin = _is_admin()
    record("管理员权限", "OK" if admin else "WARN", "当前以管理员运行" if admin else "当前非管理员运行")

    sync_env = _load_sync_env()
    key_path = os.environ.get("VPS_SSH_KEY") or sync_env.get("VPS_SSH_KEY")
    if key_path:
        resolved = Path(key_path).expanduser()
        if resolved.exists():
            record("VPS_SSH_KEY", "OK", f"已找到 {resolved}")
        else:
            record("VPS_SSH_KEY", "FAIL", f"未找到密钥文件：{resolved}")
    else:
        record("VPS_SSH_KEY", "WARN", "未配置 VPS_SSH_KEY；可在 deploy/sync/sync.env 中设置")

    if platform.system() == "Windows":
        code, output = _run_command(["sc", "query", "ssh-agent"], verbose=args.verbose)
        if code == 0 and "RUNNING" in output.upper():
            record("ssh-agent", "OK", "ssh-agent 服务正在运行")
        elif code == 0:
            record("ssh-agent", "WARN", "ssh-agent 未运行，可根据需要启动")
        else:
            record("ssh-agent", "WARN", "无法查询 ssh-agent 服务状态")

    summary = {"checks": checks, "suggestions": suggestions}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2 if args.verbose else None))

    if "FAIL" in statuses:
        if suggestions and not args.json:
            _print_fix_suggestion()
        return 2
    if "WARN" in statuses:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
# ==== END: OnePass Patch · R3 (env_check_win) ====
