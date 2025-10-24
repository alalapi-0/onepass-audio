"""Utilities for working with SSH connections and private keys."""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

try:  # Paramiko is optional at runtime, fall back to ssh.exe if unavailable.
    import paramiko  # type: ignore
except ImportError as _paramiko_exc:  # pragma: no cover - import guard
    paramiko = None  # type: ignore[assignment]
    _PARAMIKO_IMPORT_ERROR = _paramiko_exc
else:
    _PARAMIKO_IMPORT_ERROR = None


class SSHKeyLoadError(RuntimeError):
    """Raised when a private key cannot be parsed."""


class SmartSSHError(RuntimeError):
    """Raised when both Paramiko and ``ssh.exe`` backends fail."""

    def __init__(self, message: str, attempts: List["SSHAttempt"]):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class SSHAttempt:
    """Metadata about one backend attempt."""

    backend: str
    error: str
    stdout: str = ""
    stderr: str = ""
    returncode: Optional[int] = None


@dataclass
class SSHResult:
    """Return value for :func:`smart_ssh`."""

    backend: str
    returncode: int
    stdout: str
    stderr: str


@dataclass
class SSHProbeResult:
    """Outcome for :func:`probe_publickey_auth`."""

    success: bool
    attempts: int
    stdout: str = ""
    stderr: str = ""
    returncode: Optional[int] = None
    error: str = ""


def _default_home() -> Path:
    expanded = os.path.expandvars(r"%USERPROFILE%")
    return Path(expanded) if expanded and "%" not in expanded else Path.home()


def _default_ssh_executable() -> str:
    return "ssh.exe" if platform.system().lower().startswith("win") else "ssh"


def nuke_known_host(ip: str, port: int = 22) -> None:
    """Remove stale host-key fingerprints for ``ip`` from ``known_hosts``."""

    targets = (ip, f"[{ip}]:{port}")
    for target in targets:
        try:
            subprocess.run(
                ["ssh-keygen", "-R", target],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        except Exception:  # noqa: BLE001 - best-effort cleanup across environments
            pass

    known_hosts = _default_home() / ".ssh" / "known_hosts"
    if known_hosts.exists():
        try:
            lines = known_hosts.read_text(encoding="utf-8", errors="ignore").splitlines()
            keep = [
                line
                for line in lines
                if ip not in line and f"[{ip}]:{port}" not in line
            ]
            if len(keep) != len(lines):
                known_hosts.write_text(
                    "\n".join(keep) + ("\n" if keep else ""),
                    encoding="utf-8",
                )
        except Exception:  # noqa: BLE001 - best-effort cleanup across environments
            pass


def pick_default_key() -> str:
    """Return the preferred default private key path for Windows prompts."""

    home = _default_home()
    ed25519 = home / ".ssh" / "id_ed25519"
    rsa = home / ".ssh" / "id_rsa"
    if ed25519.is_file() and ed25519.stat().st_size > 0:
        return str(ed25519)
    if rsa.is_file() and rsa.stat().st_size > 0:
        return str(rsa)
    # Default to the Ed25519 path so the prompt nudges users towards it.
    return str(ed25519)


def ask_key_path(default_path: str) -> str:
    """Prompt for a private key path with validation suitable for Windows."""

    while True:
        user_in = input(f"私钥路径 [{default_path}]: ").strip()
        key_path = Path(user_in or default_path).expanduser()
        if key_path.is_dir():
            print(
                "❌ 你输入的是目录。请填写**私钥文件**完整路径，例如 C:\\Users\\ASUS\\.ssh\\id_ed25519"
            )
            continue
        if not key_path.exists():
            print(f"⚠️ 找不到私钥文件：{key_path}")
            continue
        if key_path.stat().st_size == 0:
            print(
                f"⚠️ 私钥文件大小为 0：{key_path}，请检查文件内容或重新生成密钥。"
            )
            continue
        return str(key_path)


def wait_port_open(host: str, port: int = 22, timeout: int = 120, interval: int = 5) -> bool:
    """Poll ``host:port`` until it accepts TCP connections or ``timeout`` expires."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                return True
        except OSError:
            time.sleep(interval)
    return False


def probe_publickey_auth(
    host: str,
    key_path: str | os.PathLike[str],
    *,
    retries: int = 12,
    interval: int = 10,
    timeout: int = 15,
    command: Iterable[str] | None = None,
    expect_stdout: str = "ok",
    known_hosts_file: str | os.PathLike[str] | None = None,
) -> SSHProbeResult:
    """Probe SSH public-key authentication until ``command`` succeeds.

    The helper mirrors ``ssh -o BatchMode=yes`` semantics so that password
    prompts are treated as failures.  ``command`` defaults to ``echo ok`` and
    the probe is considered successful when the command exits with code 0 and
    stdout matches ``expect_stdout``.
    """

    key_file = Path(key_path).expanduser()
    nuke_known_host(host)
    ssh_cmd: list[str] = [
        _default_ssh_executable(),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-i",
        str(key_file),
    ]
    if known_hosts_file:
        ssh_cmd += ["-o", f"UserKnownHostsFile={Path(known_hosts_file)}"]
    ssh_cmd.append(f"root@{host}")
    ssh_cmd.extend(list(command) if command is not None else ["echo", "ok"])

    last_stdout = ""
    last_stderr = ""
    last_error = ""
    last_rc: Optional[int] = None

    for attempt in range(1, max(retries, 1) + 1):
        try:
            proc = subprocess.run(
                ssh_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.SubprocessError as exc:
            last_error = str(exc)
            last_stdout = ""
            last_stderr = ""
            last_rc = None
        else:
            last_stdout = (proc.stdout or "").strip()
            last_stderr = (proc.stderr or "").strip()
            last_rc = int(proc.returncode)
            if last_rc == 0 and (not expect_stdout or last_stdout == expect_stdout):
                return SSHProbeResult(True, attempt, last_stdout, last_stderr, last_rc)
            last_error = last_stderr or last_stdout or f"rc={last_rc}"

        if attempt < max(retries, 1):
            time.sleep(interval)

    return SSHProbeResult(False, max(retries, 1), last_stdout, last_stderr, last_rc, last_error)


def _candidate_keys() -> Iterable[type[paramiko.PKey]]:
    """Yield supported Paramiko key classes in preferred order."""

    if paramiko is None:  # pragma: no cover - runtime guard
        return ()
    return (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey)


def load_private_key(path: str | os.PathLike[str]) -> paramiko.PKey:
    """Load a private key from ``path``.

    Keys are attempted in the order Ed25519 → ECDSA → RSA.  DSA keys are
    deliberately unsupported because Paramiko 3.x removed ``DSSKey``.
    """

    if paramiko is None:  # pragma: no cover - runtime guard
        raise SSHKeyLoadError(
            "未安装 Paramiko，无法解析私钥文件。请安装 paramiko>=3.1 或使用 ssh.exe。"
        )

    key_path = Path(path).expanduser()
    if key_path.is_dir():
        raise SSHKeyLoadError(f"给定的私钥路径是目录：{key_path}")

    if not key_path.exists():
        raise SSHKeyLoadError(f"私钥文件不存在：{key_path}")

    errors: list[str] = []
    for key_cls in _candidate_keys():
        try:
            return key_cls.from_private_key_file(str(key_path))
        except paramiko.PasswordRequiredException as exc:
            raise SSHKeyLoadError("私钥受口令保护，请先解锁或改用密码登录。") from exc
        except paramiko.SSHException as exc:
            errors.append(str(exc))

    joined = "; ".join(filter(None, errors)) or "未知错误"
    raise SSHKeyLoadError(f"无法解析私钥文件 {key_path}: {joined}")


def run_ssh_script_via_stdin(
    host: str,
    key_path: str,
    script_text: str,
    *,
    strict_new: bool = True,
    timeout: int = 1200,
    known_hosts_file: str | None = None,
) -> int:
    """Send a multi-line shell script to the remote host via ``ssh`` stdin."""

    opts = ["-i", key_path]
    if strict_new:
        opts += ["-o", "StrictHostKeyChecking=accept-new"]
    if known_hosts_file:
        opts += ["-o", f"UserKnownHostsFile={known_hosts_file}"]
    ssh_cmd = [_default_ssh_executable(), *opts, f"root@{host}", "bash", "-s", "--"]
    print(f"ℹ️ 使用 ssh.exe+STDIN 传输脚本：{' '.join(ssh_cmd)}")
    proc = subprocess.Popen(  # noqa: PLW1510 - communicate handles cleanup
        ssh_cmd,
        stdin=subprocess.PIPE,
        text=True,
    )
    try:
        proc.communicate(script_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return 124
    return int(proc.returncode or 0)


def run_ssh_paramiko_script_via_stdin(
    host: str,
    key_path: str,
    script_text: str,
    *,
    timeout: int = 1200,
) -> Optional[int]:
    """Send ``script_text`` via Paramiko, returning ``None`` if fallback is needed."""

    if paramiko is None:  # pragma: no cover - runtime guard
        if _PARAMIKO_IMPORT_ERROR is not None:
            print(f"⚠️ Paramiko 不可用：{_PARAMIKO_IMPORT_ERROR}，将回退到 ssh.exe")
        else:
            print("⚠️ Paramiko 不可用，将回退到 ssh.exe")
        return None

    try:
        pkey = load_private_key(key_path)
    except SSHKeyLoadError as exc:
        print(f"⚠️ Paramiko 无法加载私钥，将回退到 ssh.exe：{exc}")
        return None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            host,
            username="root",
            pkey=pkey,
            allow_agent=False,
            look_for_keys=False,
            timeout=30,
            banner_timeout=30,
            auth_timeout=30,
        )
        print("ℹ️ 使用 Paramiko 通过 stdin 下发脚本")
        stdin, stdout, stderr = client.exec_command("bash -s --", timeout=timeout)
        stdin.write(script_text)
        stdin.channel.shutdown_write()

        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if out:
            sys.stdout.write(out)
            if not out.endswith("\n"):
                sys.stdout.write("\n")
        if err:
            sys.stderr.write(err)
            if not err.endswith("\n"):
                sys.stderr.write("\n")

        exit_status = stdout.channel.recv_exit_status()
        return int(exit_status)
    except Exception as exc:  # noqa: BLE001 - allow fallback to ssh.exe
        print(f"⚠️ Paramiko 失败：{exc}，将回退到 ssh.exe")
        return None
    finally:
        client.close()


def smart_push_script(
    host: str,
    key_path: str,
    script_text: str,
    *,
    known_hosts_file: str | None = None,
) -> int:
    """Push ``script_text`` via Paramiko first and fall back to ``ssh`` stdin."""

    code = run_ssh_paramiko_script_via_stdin(host, key_path, script_text)
    if code is None:
        code = run_ssh_script_via_stdin(
            host,
            key_path,
            script_text,
            known_hosts_file=known_hosts_file,
        )
    return code


def run_ssh_paramiko(
    host: str,
    username: str,
    key_path: str,
    command: str,
    *,
    port: int = 22,
    timeout: int = 20,
) -> Optional[SSHResult]:
    """Try executing ``command`` via Paramiko.

    Returns ``None`` if Paramiko is unavailable so the caller can fall back to
    the system ``ssh`` client.  On success, an :class:`SSHResult` is returned.
    """

    if paramiko is None:  # pragma: no cover - runtime guard
        if _PARAMIKO_IMPORT_ERROR is not None:
            print(f"⚠️ Paramiko 不可用：{_PARAMIKO_IMPORT_ERROR}，将回退到 ssh.exe")
        else:
            print("⚠️ Paramiko 不可用，将回退到 ssh.exe")
        return None

    pkey = load_private_key(key_path)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            host,
            port=port,
            username=username,
            pkey=pkey,
            allow_agent=False,
            look_for_keys=False,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        print("ℹ️ 使用 Paramiko 执行远端命令")
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        _ = stdin.channel  # keep reference to avoid premature close
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        return SSHResult("paramiko", rc, out, err)
    finally:
        client.close()


def run_ssh_exe(
    host: str,
    username: str,
    key_path: str,
    command: str,
    *,
    port: int = 22,
    timeout: int = 20,
    ssh_executable: Optional[str] = None,
    strict_host_key: str = "accept-new",
    known_hosts_file: str | None = None,
) -> SSHResult:
    """Execute ``command`` using the system ``ssh`` binary."""

    if ssh_executable is None:
        ssh_executable = _default_ssh_executable()

    ssh_cmd = [
        ssh_executable,
        "-i",
        str(Path(key_path)),
        "-o",
        "BatchMode=yes",
        "-o",
        f"StrictHostKeyChecking={strict_host_key}",
        "-p",
        str(port),
        f"{username}@{host}",
        command,
    ]

    if known_hosts_file:
        ssh_cmd += ["-o", f"UserKnownHostsFile={known_hosts_file}"]

    print(f"ℹ️ 使用 ssh.exe：{' '.join(ssh_cmd[:-1])} <remote-cmd>")
    proc = subprocess.run(
        ssh_cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout if timeout and timeout > 0 else None,
    )
    return SSHResult("ssh.exe", proc.returncode, proc.stdout, proc.stderr)


def smart_ssh(
    host: str,
    username: str,
    key_path: str | os.PathLike[str],
    command: str,
    *,
    port: int = 22,
    timeout: int = 20,
    ssh_executable: Optional[str] = None,
    known_hosts_file: str | None = None,
) -> SSHResult:
    """Execute ``command`` on ``host`` using either Paramiko or ``ssh.exe``.

    The function first tries Paramiko.  If Paramiko fails (for example due to a
    transport negotiation issue), it falls back to invoking the local ``ssh``
    binary.  The first backend that executes the command successfully is
    returned.  If both backends fail an exception is raised containing the
    individual errors.
    """

    key_path = Path(key_path).expanduser()
    if key_path.is_dir():
        raise SmartSSHError(f"私钥路径指向目录：{key_path}", [])

    attempts: List[SSHAttempt] = []

    # 1. Try Paramiko (if available)
    try:
        result = run_ssh_paramiko(
            host,
            username,
            key_path=str(key_path),
            command=command,
            port=port,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - intentionally broad for fallback
        attempts.append(SSHAttempt("paramiko", str(exc)))
    else:
        if result is not None:
            return result

    # 2. Fallback to system ssh
    try:
        return run_ssh_exe(
            host,
            username,
            str(key_path),
            command,
            port=port,
            timeout=timeout,
            ssh_executable=ssh_executable,
            known_hosts_file=known_hosts_file,
        )
    except FileNotFoundError as exc:
        attempts.append(SSHAttempt("ssh.exe", f"找不到 ssh 客户端：{exc}"))
    except subprocess.TimeoutExpired as exc:
        attempts.append(SSHAttempt("ssh.exe", f"ssh.exe 超时：{exc}", returncode=None))
    except Exception as exc:  # noqa: BLE001 - fallback diagnostics
        attempts.append(SSHAttempt("ssh.exe", str(exc)))

    raise SmartSSHError("SSH 调用失败；详见 attempts", attempts) from None


__all__ = [
    "ask_key_path",
    "pick_default_key",
    "run_ssh_paramiko_script_via_stdin",
    "run_ssh_script_via_stdin",
    "smart_push_script",
    "SSHAttempt",
    "SSHKeyLoadError",
    "SSHResult",
    "SmartSSHError",
    "run_ssh_exe",
    "run_ssh_paramiko",
    "load_private_key",
    "smart_ssh",
    "wait_port_open",
]

