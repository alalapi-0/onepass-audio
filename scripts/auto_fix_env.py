#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动修复本地环境缺失组件的调度器（Windows/macOS）。"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

PROJ_ROOT = Path(__file__).resolve().parents[1]
if str(PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJ_ROOT))

from onepass.ux import (  # noqa: E402
    format_cmd,
    log_err,
    log_info,
    log_ok,
    log_warn,
    run_streamed,
    section,
)


@dataclass
class TaskResult:
    key: str
    status: str  # OK / WARN / FAIL / SKIP
    detail: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OnePass 自动修复环境工具")
    parser.add_argument("--yes", action="store_true", help="对脚本内交互默认回答 yes")
    parser.add_argument("--no", action="store_true", help="对脚本内交互默认回答 no")
    parser.add_argument(
        "--only",
        help="仅运行指定组件，逗号分隔，例如 pwsh,openssh",
        default="",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅打印将执行的命令，不做修改")
    return parser.parse_args()


def _resolve_shell(candidates: Iterable[str]) -> Optional[str]:
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def _run_shell_script(shell: Sequence[str], script_path: Path, extra_args: Sequence[str], dry_run: bool) -> int:
    cmd = [*shell, str(script_path), *extra_args]
    log_info(f"运行：{format_cmd(cmd)}")
    if dry_run:
        log_info("[DryRun] 未实际执行。")
        return 0
    return run_streamed(cmd, cwd=PROJ_ROOT, heartbeat_s=30.0, show_cmd=False)


def _run_windows_tasks(args: argparse.Namespace, selected: Optional[set[str]]) -> List[TaskResult]:
    section("安装/修复 Windows 依赖")
    results: List[TaskResult] = []
    ps_path = _resolve_shell(["pwsh", "powershell.exe", "powershell"])
    if not ps_path:
        log_err("未找到可用的 PowerShell，可在系统中搜索 powershell.exe 后重试。")
        results.append(TaskResult("pwsh", "FAIL", "PowerShell 不可用，无法执行修复脚本。"))
        return results

    scripts_dir = PROJ_ROOT / "deploy" / "bootstrap"
    tasks: List[Tuple[str, str, Path, bool]] = [
        ("pwsh", "安装/升级 PowerShell 7", scripts_dir / "ensure_pwsh_win.ps1", False),
        ("openssh", "启用 OpenSSH 客户端/服务", scripts_dir / "ensure_openssh_win.ps1", False),
        ("git", "安装 Git for Windows", scripts_dir / "ensure_git_win.ps1", False),
        ("rsync", "可选：安装 rsync (MSYS2)", scripts_dir / "ensure_rsync_win.ps1", True),
    ]

    base_shell = [ps_path, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]

    for key, description, script_path, optional in tasks:
        if selected and key not in selected:
            log_info(f"跳过 {key}（未在 --only 中指定）。")
            results.append(TaskResult(key, "SKIP", "手动跳过"))
            continue
        if not script_path.exists():
            detail = f"脚本缺失：{script_path}"
            status = "WARN" if optional else "FAIL"
            log_warn(detail) if status == "WARN" else log_err(detail)
            results.append(TaskResult(key, status, detail))
            continue
        log_info(f"开始：{description}")
        rc = _run_shell_script(base_shell, script_path, [], args.dry_run)
        if args.dry_run:
            results.append(TaskResult(key, "SKIP", "DryRun"))
            continue
        if rc == 0:
            log_ok(f"{description} 完成。")
            results.append(TaskResult(key, "OK", "完成"))
        elif rc == 1:
            log_warn(f"{description} 返回警告，建议检查输出。")
            results.append(TaskResult(key, "WARN", "脚本返回码 1"))
        else:
            log_err(f"{description} 失败，返回码 {rc}。")
            results.append(TaskResult(key, "FAIL", f"返回码 {rc}"))
    return results


def _run_macos_tasks(args: argparse.Namespace, selected: Optional[set[str]]) -> List[TaskResult]:
    section("安装/修复 macOS 依赖")
    results: List[TaskResult] = []
    scripts_dir = PROJ_ROOT / "deploy" / "bootstrap"
    tasks: List[Tuple[str, str, Path, bool, List[str]]] = [
        ("brew", "安装 Homebrew", scripts_dir / "ensure_homebrew_macos.sh", False, []),
        ("openssh", "安装 openssh/rsync 等基础工具", scripts_dir / "ensure_base_macos.sh", False, []),
    ]

    for key, description, script_path, optional, extra in tasks:
        if selected and key not in selected:
            log_info(f"跳过 {key}（未在 --only 中指定）。")
            results.append(TaskResult(key, "SKIP", "手动跳过"))
            continue
        if not script_path.exists():
            detail = f"脚本缺失：{script_path}"
            status = "WARN" if optional else "FAIL"
            log_warn(detail) if status == "WARN" else log_err(detail)
            results.append(TaskResult(key, status, detail))
            continue
        log_info(f"开始：{description}")
        cmd = ["/bin/bash", str(script_path)]
        if args.yes:
            cmd.append("--yes")
        elif args.no:
            cmd.append("--no")
        cmd.extend(extra)
        log_info(f"运行：{format_cmd(cmd)}")
        if args.dry_run:
            log_info("[DryRun] 未实际执行。")
            results.append(TaskResult(key, "SKIP", "DryRun"))
            continue
        rc = run_streamed(cmd, cwd=PROJ_ROOT, heartbeat_s=30.0, show_cmd=False)
        if rc == 0:
            log_ok(f"{description} 完成。")
            results.append(TaskResult(key, "OK", "完成"))
        elif rc == 1:
            log_warn(f"{description} 返回警告，建议检查输出。")
            results.append(TaskResult(key, "WARN", "脚本返回码 1"))
        else:
            log_err(f"{description} 失败，返回码 {rc}。")
            results.append(TaskResult(key, "FAIL", f"返回码 {rc}"))
    return results


def _collect_selected(only: str) -> Optional[set[str]]:
    if not only:
        return None
    items = {item.strip().lower() for item in only.split(",") if item.strip()}
    return items if items else None


def _run_self_check_windows() -> List[TaskResult]:
    section("自检（Windows）")
    results: List[TaskResult] = []

    def _command_status(cmd: List[str], key: str, ok_detail: str) -> TaskResult:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as exc:  # pragma: no cover - 平台相关
            return TaskResult(key, "FAIL", str(exc))
        output = proc.stdout.strip() or proc.stderr.strip()
        if proc.returncode == 0:
            return TaskResult(key, "OK", output or ok_detail)
        preview = " ".join((output.splitlines() or ["" ])[0:3])
        return TaskResult(key, "WARN", f"退出码 {proc.returncode}：{preview}")

    if shutil.which("pwsh"):
        results.append(TaskResult("pwsh", "OK", "PowerShell 7 已安装"))
    else:
        results.append(TaskResult("pwsh", "FAIL", "未检测到 pwsh"))

    results.append(_command_status(["ssh", "-V"], "ssh", "ssh 可用"))
    results.append(_command_status(["scp", "-V"], "scp", "scp 可用"))

    if shutil.which("git"):
        git_res = _command_status(["git", "--version"], "git", "git 可用")
        results.append(git_res)
    else:
        results.append(TaskResult("git", "WARN", "未检测到 git"))

    if shutil.which("rsync") or Path("C:/msys64/usr/bin/rsync.exe").exists():
        rsync_cmd = ["rsync", "--version"] if shutil.which("rsync") else [
            "C:/msys64/usr/bin/rsync.exe",
            "--version",
        ]
        results.append(_command_status(rsync_cmd, "rsync", "rsync 可用"))
    else:
        results.append(TaskResult("rsync", "WARN", "未检测到 rsync，将回退 scp"))

    ps_path = _resolve_shell(["pwsh", "powershell.exe", "powershell"])
    if ps_path:
        cmd = [
            ps_path,
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "(Get-Service -Name 'ssh-agent' -ErrorAction SilentlyContinue)?.Status",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            status = proc.stdout.strip()
            if status.lower() == "running":
                results.append(TaskResult("ssh-agent", "OK", "ssh-agent 正在运行"))
            elif status:
                results.append(TaskResult("ssh-agent", "WARN", f"ssh-agent 状态：{status}"))
            else:
                results.append(TaskResult("ssh-agent", "WARN", "未找到 ssh-agent 服务"))
        except OSError as exc:  # pragma: no cover - 平台相关
            results.append(TaskResult("ssh-agent", "WARN", f"无法检测 ssh-agent：{exc}"))
    else:
        results.append(TaskResult("ssh-agent", "WARN", "无法检测 ssh-agent（缺少 PowerShell）"))

    if shutil.which("winget"):
        results.append(TaskResult("winget", "OK", "winget 可用"))
    else:
        results.append(TaskResult("winget", "WARN", "未检测到 winget (App Installer)"))

    return results


def _run_self_check_macos() -> List[TaskResult]:
    section("自检（macOS）")
    results: List[TaskResult] = []

    def _command_status(cmd: List[str], key: str, ok_detail: str) -> TaskResult:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as exc:  # pragma: no cover - 平台相关
            return TaskResult(key, "FAIL", str(exc))
        output = proc.stdout.strip() or proc.stderr.strip()
        if proc.returncode == 0:
            return TaskResult(key, "OK", output or ok_detail)
        preview = " ".join((output.splitlines() or ["" ])[0:3])
        return TaskResult(key, "WARN", f"退出码 {proc.returncode}：{preview}")

    if shutil.which("brew"):
        results.append(_command_status(["brew", "--version"], "brew", "brew 可用"))
    else:
        results.append(TaskResult("brew", "WARN", "未检测到 Homebrew"))

    results.append(_command_status(["ssh", "-V"], "ssh", "ssh 可用"))
    results.append(_command_status(["scp", "-V"], "scp", "scp 可用"))
    results.append(_command_status(["rsync", "--version"], "rsync", "rsync 可用"))

    agent_proc = subprocess.run(
        ["/bin/sh", "-c", "eval '$(ssh-agent -s)' >/dev/null && ssh-add -l"],
        capture_output=True,
        text=True,
    )
    if agent_proc.returncode == 0:
        results.append(TaskResult("ssh-agent", "OK", "ssh-agent 可用"))
    else:
        preview = agent_proc.stderr.strip() or agent_proc.stdout.strip()
        results.append(TaskResult("ssh-agent", "WARN", preview or "ssh-agent 未就绪"))

    return results


def _summarize(results: List[TaskResult]) -> int:
    status_map = {"OK": [], "WARN": [], "FAIL": [], "SKIP": []}
    for item in results:
        status_map.setdefault(item.status, []).append(item)

    section("结果汇总")
    exit_code = 0
    for status in ("OK", "WARN", "FAIL", "SKIP"):
        for item in status_map.get(status, []):
            message = f"{item.key}: {item.detail}" if item.detail else item.key
            if status == "OK":
                log_ok(message)
            elif status == "WARN":
                log_warn(message)
                exit_code = max(exit_code, 1)
            elif status == "FAIL":
                log_err(message)
                exit_code = 2
            else:
                log_info(f"{item.key}: {item.detail}")
    return exit_code


def main() -> int:
    args = _parse_args()
    if args.yes and args.no:
        log_err("--yes 与 --no 不能同时使用。")
        return 2

    selected = _collect_selected(args.only)

    system = platform.system().lower()
    log_info(f"检测到系统：{system}")

    task_results: List[TaskResult] = []
    if system == "windows":
        task_results.extend(_run_windows_tasks(args, selected))
        task_results.extend(_run_self_check_windows())
    elif system == "darwin":
        task_results.extend(_run_macos_tasks(args, selected))
        task_results.extend(_run_self_check_macos())
    else:
        log_err("当前脚本仅支持 Windows 或 macOS。")
        return 2

    exit_code = _summarize(task_results)
    if exit_code == 0:
        log_ok("自动修复完成，所有检查通过。")
    elif exit_code == 1:
        log_warn("自动修复完成，但存在警告项。请查看日志获取详情。")
    else:
        log_err("自动修复过程中存在失败项，请根据日志手动处理后重试。")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
