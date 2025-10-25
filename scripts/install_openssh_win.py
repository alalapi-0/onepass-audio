# ==== BEGIN: OnePass Patch · R3 (install_openssh_win) ====
"""Install OpenSSH Client on Windows using DISM without PowerShell."""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
from typing import List


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _run_dism(args: List[str], verbose: bool) -> subprocess.CompletedProcess[str]:
    cmd = ["dism", "/online", *args]
    if verbose:
        print(f"[debug] exec: {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _run_ssh_version(verbose: bool) -> int:
    cmd = ["ssh", "-V"]
    if verbose:
        print(f"[debug] exec: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127
    output = (result.stdout or "")
    if result.stderr:
        output += ("\n" if output else "") + result.stderr
    if output.strip():
        print(output.strip())
    return result.returncode


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装 Windows OpenSSH Client")
    parser.add_argument("--verbose", action="store_true", help="打印命令输出")
    args = parser.parse_args(argv)

    if not _is_admin():
        print("[FAIL] 需要管理员权限，请在管理员终端中运行。", file=sys.stderr)
        return 2

    info = _run_dism(["/Get-CapabilityInfo", "/CapabilityName:OpenSSH.Client~~~~0.0.1.0"], args.verbose)
    if info.returncode == 0 and "State : Installed" in info.stdout:
        print("[OK] OpenSSH Client 已安装，无需重复操作。")
    else:
        print("[INFO] 正在安装 OpenSSH Client……")
        install = _run_dism(["/Add-Capability", "/CapabilityName:OpenSSH.Client~~~~0.0.1.0"], args.verbose)
        if install.returncode != 0:
            combined = (install.stdout or "").splitlines() + (install.stderr or "").splitlines()
            tail = combined[-5:] if combined else []
            print("[FAIL] DISM 安装失败：", file=sys.stderr)
            for line in tail:
                print(line, file=sys.stderr)
            print("[建议] 若受组策略或离线环境限制，可通过“设置 → 可选功能”或联系 IT。", file=sys.stderr)
            return 2
        print("[OK] DISM 已完成安装。")

    code = _run_ssh_version(args.verbose)
    if code != 0:
        print("[WARN] ssh -V 仍未通过，请检查日志。")
        return 2
    print("[OK] OpenSSH Client 可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
# ==== END: OnePass Patch · R3 (install_openssh_win) ====
