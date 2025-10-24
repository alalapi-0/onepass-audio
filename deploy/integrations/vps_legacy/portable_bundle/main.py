from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import shlex
import shutil
import textwrap

from core.project_overview import generate_project_overview
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

if sys.version_info < (3, 8):
    raise SystemExit(
        "当前 Python 解释器版本过低。本工具至少需要 Python 3.8，请改用 python3 运行。"
    )

import paramiko

from core.port_config import resolve_listen_port


if os.name == "nt":
    os.system("")

BLUE = "\033[34m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"

ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = ROOT / "artifacts"
try:
    LISTEN_PORT, LISTEN_PORT_SOURCE = resolve_listen_port()
except ValueError as exc:
    raise SystemExit(f"无效的 WireGuard 端口配置：{exc}") from exc


PLATFORM_CHOICES = {
    "windows": "Windows",
    "macos": "macOS",
}
SELECTED_PLATFORM: str | None = None


@dataclass
class SSHResult:
    """Result of a remote SSH command execution."""

    returncode: int
    stdout: str
    stderr: str
    backend: str


@dataclass
class SSHContext:
    """Connection parameters for remote SSH execution."""

    hostname: str
    key_path: Path


class DeploymentError(RuntimeError):
    """Raised when the automated WireGuard deployment fails."""


@dataclass(frozen=True)
class MenuAction:
    """Define an interactive menu option for the CLI."""

    key: str
    description: str
    handler: Callable[[], None]


LOG_FILE: Path | None = None
SSH_CTX: SSHContext | None = None
_PARAMIKO_CLIENT: paramiko.SSHClient | None = None
_SUBPROCESS_TEXT_KWARGS = {"text": True, "encoding": "utf-8", "errors": "replace"}


def _colorize(message: str, color: str) -> str:
    """Return ``message`` wrapped in ANSI color codes."""

    return f"{color}{message}{RESET}"


def _log_to_file(message: str) -> None:
    """Append ``message`` to the deploy log if enabled."""

    if LOG_FILE is None:
        return
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{message}\n")
    except OSError:
        # Logging must never block deployment.
        pass


def logwrite(message: str, *, color: str | None = None) -> None:
    """Print ``message`` (optionally colorized) and persist to the log file."""

    text = _colorize(message, color) if color else message
    print(text)
    _log_to_file(message)


def log_info(message: str) -> None:
    """Print an informational message in blue."""

    logwrite(message, color=BLUE)


def log_success(message: str) -> None:
    """Print a success message in green."""

    logwrite(message, color=GREEN)


def log_warning(message: str) -> None:
    """Print a warning message in yellow."""

    logwrite(message, color=YELLOW)


def log_error(message: str) -> None:
    """Print an error message in red."""

    logwrite(message, color=RED)


def log_section(title: str) -> None:
    """Print a visual separator for a workflow step."""

    divider = "=" * 24
    log_info(divider)
    log_info(title)


def _stream_command_output(
    stdout: paramiko.ChannelFile, stderr: paramiko.ChannelFile, show_output: bool
) -> tuple[int, str, str]:
    """Stream ``stdout``/``stderr`` until completion and return the exit code.

    Parameters
    ----------
    stdout, stderr:
        Paramiko file-like objects representing the remote command output streams.
    show_output:
        Whether to echo remote output to the local console in real-time.
    """

    channel = stdout.channel
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    printed_any = False
    last_printed = ""

    while True:
        stdout_drained = True
        stderr_drained = True

        if channel.recv_ready():
            data = channel.recv(4096)
            if data:
                stdout_drained = False
                text = data.decode("utf-8", errors="ignore")
                stdout_chunks.append(text)
                if show_output:
                    print(text, end="", flush=True)
                    printed_any = True
                    last_printed = text
            else:
                stdout_drained = True

        if channel.recv_stderr_ready():
            data = channel.recv_stderr(4096)
            if data:
                stderr_drained = False
                text = data.decode("utf-8", errors="ignore")
                stderr_chunks.append(text)
                if show_output:
                    print(text, end="", flush=True)
                    printed_any = True
                    last_printed = text
            else:
                stderr_drained = True

        if channel.exit_status_ready() and stdout_drained and stderr_drained:
            break

        if stdout_drained and stderr_drained:
            time.sleep(0.1)

    exit_code = channel.recv_exit_status()
    if show_output and printed_any and not last_printed.endswith("\n"):
        print()

    stdout_data = "".join(stdout_chunks).strip()
    stderr_data = "".join(stderr_chunks).strip()
    return exit_code, stdout_data, stderr_data


def _run_remote_script(
    client: paramiko.SSHClient,
    script: str,
    description: str,
    *,
    timeout: int = 1200,
    show_output: bool = True,
) -> bool:
    """Execute ``script`` on ``client`` using ``bash`` and report errors."""

    try:
        stdin, stdout, stderr = client.exec_command("bash -s", get_pty=False, timeout=timeout)
        if not script.endswith("\n"):
            script += "\n"
        stdin.write(script)
        stdin.flush()
        stdin.channel.shutdown_write()
        stdin.close()
        exit_code, stdout_data, stderr_data = _stream_command_output(stdout, stderr, show_output)
    except Exception as exc:  # noqa: BLE001 - we want to surface any Paramiko errors
        log_error(f"❌ {description}失败：{exc}")
        return False

    if exit_code != 0:
        details = stderr_data or stdout_data or f"退出码 {exit_code}"
        log_error(f"❌ {description}失败：{details}")
        return False
    return True


def _run_remote_command(
    client: paramiko.SSHClient,
    command: str,
    description: str,
    timeout: int = 600,
    *,
    show_output: bool = True,
) -> bool:
    """Run a single command via Paramiko with unified error handling."""

    try:
        stdin, stdout, stderr = client.exec_command(command, get_pty=False, timeout=timeout)
        stdin.channel.shutdown_write()
        exit_code, stdout_data, stderr_data = _stream_command_output(stdout, stderr, show_output)
    except Exception as exc:  # noqa: BLE001
        log_error(f"❌ {description}失败：{exc}")
        return False

    if exit_code != 0:
        details = stderr_data or stdout_data or f"退出码 {exit_code}"
        log_error(f"❌ {description}失败：{details}")
        return False
    return True


def _init_deploy_log() -> Path:
    """Create a timestamped deployment log inside ``artifacts``."""

    global LOG_FILE
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = ARTIFACTS_DIR / f"deploy-{timestamp}.log"
    LOG_FILE = log_path
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as handle:
            handle.write(f"# PrivateTunnel Step3 log {timestamp}\n")
    except OSError:
        # Even if writing fails, keep the path so subsequent logs still attempt writes.
        pass
    return log_path


def _set_ssh_context(hostname: str, key_path: Path) -> None:
    """Record the SSH connection context for subsequent helper calls."""

    global SSH_CTX
    _close_paramiko_client()
    SSH_CTX = SSHContext(hostname=hostname, key_path=key_path)


def _require_ssh_context() -> SSHContext:
    """Return the active SSH context or raise an internal error."""

    if SSH_CTX is None:
        raise DeploymentError("内部错误：SSH 上下文未初始化。")
    return SSH_CTX


def _close_paramiko_client() -> None:
    """Close and reset the cached Paramiko client if it exists."""

    global _PARAMIKO_CLIENT
    if _PARAMIKO_CLIENT is not None:
        try:
            _PARAMIKO_CLIENT.close()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass
        _PARAMIKO_CLIENT = None


def _load_paramiko_pkey(path: Path) -> paramiko.PKey:
    """Load an SSH private key compatible with Paramiko."""

    errors: list[str] = []
    try:
        return paramiko.Ed25519Key.from_private_key_file(str(path))
    except Exception as exc:  # noqa: BLE001 - collect and retry with other key types
        errors.append(f"Ed25519: {exc}")
    try:
        return paramiko.RSAKey.from_private_key_file(str(path))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"RSA: {exc}")
    try:
        return paramiko.ECDSAKey.from_private_key_file(str(path))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ECDSA: {exc}")
    raise DeploymentError(f"无法解析私钥 {path}: {'; '.join(errors)}")


def _ensure_paramiko_client() -> paramiko.SSHClient:
    """Return a connected Paramiko SSH client, creating one if necessary."""

    global _PARAMIKO_CLIENT
    if _PARAMIKO_CLIENT is not None:
        return _PARAMIKO_CLIENT

    ctx = _require_ssh_context()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = _load_paramiko_pkey(ctx.key_path)
    try:
        client.connect(
            hostname=ctx.hostname,
            username="root",
            pkey=pkey,
            look_for_keys=False,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        raise DeploymentError(f"Paramiko 连接 {ctx.hostname} 失败：{exc}") from exc

    _PARAMIKO_CLIENT = client
    return client


def _log_remote_output(prefix: str, text: str) -> None:
    """Log remote stdout/stderr content line-by-line."""

    if not text:
        return
    for line in text.splitlines():
        logwrite(f"{prefix}{line}")


def _clean_known_host(ip: str) -> None:
    """Remove stale host key fingerprints for ``ip`` prior to SSH attempts."""

    log_info(f"→ 使用 ssh-keygen -R 清理旧指纹（{ip}）…")
    targets = (ip, f"[{ip}]:22")
    for target in targets:
        command = ["ssh-keygen", "-R", target]
        logwrite(f"$ {' '.join(command)}")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                **_SUBPROCESS_TEXT_KWARGS,
                check=False,
            )
        except FileNotFoundError:
            log_warning("⚠️ 未检测到 ssh-keygen，改用内置清理逻辑。")
            break
        except subprocess.SubprocessError as exc:
            log_warning(f"⚠️ 清理 {target} 指纹失败：{exc}")
            continue
        _log_remote_output("[ssh-keygen] ", result.stdout)
        _log_remote_output("[ssh-keygen] ", result.stderr)

    try:
        nuke_known_host(ip)
    except Exception:  # noqa: BLE001 - best effort cleanup
        pass


def _ssh_run(command: str, *, timeout: int = 900, description: str | None = None) -> SSHResult:
    """Execute ``command`` on the remote host via OpenSSH with Paramiko fallback."""

    ctx = _require_ssh_context()
    ssh_executable = shutil.which("ssh")
    ssh_cmd = [
        ssh_executable or "ssh",
        "-i",
        str(ctx.key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"root@{ctx.hostname}",
        command,
    ]

    if ssh_executable:
        logwrite(f"$ {' '.join(ssh_cmd)}")
        try:
            completed = subprocess.run(
                ssh_cmd,
                capture_output=True,
                **_SUBPROCESS_TEXT_KWARGS,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeploymentError(f"远端命令超时：{description or command}") from exc
        except OSError as exc:
            log_warning(f"⚠️ 调用 OpenSSH 失败：{exc}，将尝试 Paramiko 回退。")
        else:
            _log_remote_output("[stdout] ", completed.stdout)
            _log_remote_output("[stderr] ", completed.stderr)
            if completed.returncode != 0:
                details = completed.stderr.strip() or completed.stdout.strip() or f"退出码 {completed.returncode}"
                raise DeploymentError(
                    f"远端命令失败（{description or command}）：{details}"
                )
            return SSHResult(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                backend="openssh",
            )

    client = _ensure_paramiko_client()
    logwrite(f"(paramiko) $ {command}")
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise DeploymentError(f"Paramiko 执行命令失败：{exc}") from exc

    try:
        exit_code, stdout_data, stderr_data = _stream_command_output(stdout, stderr, show_output=False)
    finally:
        try:
            stdin.close()
        except Exception:  # noqa: BLE001
            pass

    _log_remote_output("[stdout] ", stdout_data)
    _log_remote_output("[stderr] ", stderr_data)
    if exit_code != 0:
        details = stderr_data.strip() or stdout_data.strip() or f"退出码 {exit_code}"
        raise DeploymentError(f"远端命令失败（{description or command}）：{details}")

    return SSHResult(returncode=exit_code, stdout=stdout_data, stderr=stderr_data, backend="paramiko")


def _download_with_scp(remote_path: str, local_path: Path, *, timeout: int = 300) -> bool:
    """Download ``remote_path`` via ``scp`` if available."""

    ctx = _require_ssh_context()
    scp_executable = shutil.which("scp")
    if scp_executable is None:
        log_warning("⚠️ 未检测到 scp，可使用 Paramiko SFTP 回退。")
        return False

    local_path.parent.mkdir(parents=True, exist_ok=True)
    scp_cmd = [
        scp_executable,
        "-i",
        str(ctx.key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"root@{ctx.hostname}:{remote_path}",
        str(local_path),
    ]
    logwrite(f"$ {' '.join(scp_cmd)}")
    try:
        result = subprocess.run(
            scp_cmd,
            capture_output=True,
            **_SUBPROCESS_TEXT_KWARGS,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log_warning(f"⚠️ scp 传输超时：{remote_path}")
        return False
    except OSError as exc:
        log_warning(f"⚠️ 无法执行 scp：{exc}")
        return False

    _log_remote_output("[scp stdout] ", result.stdout)
    _log_remote_output("[scp stderr] ", result.stderr)
    if result.returncode != 0:
        log_warning(f"⚠️ scp 返回码 {result.returncode}：{remote_path}")
        return False
    return True


def _download_with_paramiko(remote_path: str, local_path: Path) -> None:
    """Download ``remote_path`` using Paramiko SFTP."""

    client = _ensure_paramiko_client()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with client.open_sftp() as sftp:
            sftp.get(remote_path, str(local_path))
    except Exception as exc:  # noqa: BLE001
        raise DeploymentError(f"SFTP 下载 {remote_path} 失败：{exc}") from exc


def _download_artifact(remote_path: str, local_path: Path) -> bool:
    """Download ``remote_path`` to ``local_path``.

    Returns ``True`` on success. When both ``scp`` and Paramiko downloads fail the
    error is logged and ``False`` is returned instead of raising, allowing callers
    to decide whether the artifact is optional.
    """

    if _download_with_scp(remote_path, local_path):
        return True
    log_warning("⚠️ scp 下载失败，改用 Paramiko SFTP。")
    try:
        _download_with_paramiko(remote_path, local_path)
    except DeploymentError as exc:
        log_warning(f"⚠️ SFTP 下载失败：{exc}")
        return False
    return True


def _ensure_remote_artifact(remote_path: str, description: str) -> None:
    """Ensure ``remote_path`` exists and is non-empty on the server."""

    check_cmd = f"test -s {shlex.quote(remote_path)} && echo OK || echo MISSING"
    result = _ssh_run(
        f"bash -lc {shlex.quote(check_cmd)}",
        timeout=60,
        description=f"校验远端文件 {remote_path}",
    )
    if "OK" not in result.stdout:
        raise DeploymentError(
            f"远端未生成{description}（{remote_path}），请查看部署日志与 /etc/wireguard/clients。"
        )


def deploy_wireguard_remote_script(
    listen_port: int,
    desktop_ip: str,
    iphone_ip: str,
    server_ip: str,
    dns_servers: str,
    allowed_ips: str,
    desktop_mtu: str,
) -> str:
    """Return the shell script that configures WireGuard end-to-end on the server."""

    return textwrap.dedent(
        f"""
        #!/usr/bin/env bash
        set -euo pipefail

        log()  {{ printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }}
        warn() {{ printf '[%s] ⚠️ %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }}
        err()  {{ printf '[%s] ❌ %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }}

        export DEBIAN_FRONTEND=noninteractive

        WG_PORT=${{WG_PORT:-{listen_port}}}
        WG_DIR=/etc/wireguard
        SERVER_CONF="$WG_DIR/wg0.conf"
        SERVER_PRIV="$WG_DIR/server.private"
        SERVER_PUB_FILE="$WG_DIR/server.public"
        CLIENT_BASE="$WG_DIR/clients"
        DESKTOP_DIR="$CLIENT_BASE/desktop"
        IPHONE_DIR="$CLIENT_BASE/iphone"
        DESKTOP_IP="${{PT_DESKTOP_IP:-{desktop_ip}}}"
        IPHONE_IP="${{PT_IPHONE_IP:-{iphone_ip}}}"
        DNS_SERVERS="${{PT_DNS:-{dns_servers}}}"
        ALLOWED_IPS="${{PT_ALLOWED_IPS:-{allowed_ips}}}"
        DESKTOP_MTU="${{PT_CLIENT_MTU:-{desktop_mtu}}}"
        SERVER_FALLBACK_IP="$(ip -o -4 addr show dev \"$(ip -o -4 route show to default | awk '{{print $5}}' | head -n1)\" | awk '{{print $4}}' | cut -d/ -f1 | head -n1)"

        log "安装 WireGuard 组件"
        apt-get update -y
        apt-get install -y wireguard wireguard-tools qrencode iptables-persistent netfilter-persistent curl

        log "开启 IPv4/IPv6 转发并持久化"
        sysctl -w net.ipv4.ip_forward=1
        sysctl -w net.ipv6.conf.all.forwarding=1
        echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-wireguard-forward.conf
        echo 'net.ipv6.conf.all.forwarding=1' > /etc/sysctl.d/99-wireguard-forward6.conf
        sysctl --system || true

        WAN_IF=$(ip -o -4 route show to default | awk '{{print $5}}' | head -n1)
        if [ -z "${{WAN_IF:-}}" ]; then
          err "ERROR: Failed to detect WAN interface"
          exit 1
        fi
        log "检测到默认路由接口: $WAN_IF"

        log "刷新并写入 NAT/FORWARD/INPUT 规则"
        iptables -t nat -D POSTROUTING -s 10.6.0.0/24 -o "$WAN_IF" -j MASQUERADE 2>/dev/null || true
        iptables -t nat -C POSTROUTING -s 10.6.0.0/24 -o "$WAN_IF" -j MASQUERADE 2>/dev/null || \
        iptables -t nat -A POSTROUTING -s 10.6.0.0/24 -o "$WAN_IF" -j MASQUERADE
        iptables -D FORWARD -i wg0 -o "$WAN_IF" -j ACCEPT 2>/dev/null || true
        iptables -C FORWARD -i wg0 -o "$WAN_IF" -j ACCEPT 2>/dev/null || \
        iptables -A FORWARD -i wg0 -o "$WAN_IF" -j ACCEPT
        iptables -D FORWARD -i "$WAN_IF" -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
        iptables -C FORWARD -i "$WAN_IF" -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
        iptables -A FORWARD -i "$WAN_IF" -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT
        iptables -D INPUT -p udp --dport "$WG_PORT" -j ACCEPT 2>/dev/null || true
        iptables -C INPUT -p udp --dport "$WG_PORT" -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -p udp --dport "$WG_PORT" -j ACCEPT

        if command -v ufw >/dev/null 2>&1; then
          if ufw status | grep -qi "Status: active"; then
            ufw allow "$WG_PORT"/udp || true
            ufw route allow in on wg0 out on "$WAN_IF" || true
            ufw route allow in on "$WAN_IF" out on wg0 || true
            ufw reload || true
          fi
        fi

        netfilter-persistent save || true
        netfilter-persistent reload || true

        umask 077
        mkdir -p "$CLIENT_BASE" "$DESKTOP_DIR" "$IPHONE_DIR"
        chmod 700 "$CLIENT_BASE" "$DESKTOP_DIR" "$IPHONE_DIR"

        if [ ! -f "$SERVER_PRIV" ]; then
          log "生成服务器密钥对"
          wg genkey | tee "$SERVER_PRIV" | wg pubkey > "$SERVER_PUB_FILE"
        fi
        SERVER_PRIVATE=$(cat "$SERVER_PRIV")

        cat >"$SERVER_CONF" <<CFG
[Interface]
Address = 10.6.0.1/24
ListenPort = $WG_PORT
PrivateKey = $SERVER_PRIVATE
SaveConfig = true
CFG
        chmod 600 "$SERVER_CONF"

        systemctl enable wg-quick@wg0
        systemctl restart wg-quick@wg0

        sleep 1

        CURRENT_PORT="$(wg show wg0 listen-port 2>/dev/null | tr -d '[:space:]' || true)"
        if [ -z "$CURRENT_PORT" ] || [ "$CURRENT_PORT" = "0" ]; then
          warn "未检测到 WireGuard 监听端口，尝试设置为 $WG_PORT…"
          if ! output=$(wg set wg0 listen-port "$WG_PORT" 2>&1); then
            warn "wg set 调整监听端口失败：$output"
          fi
          sleep 1
          CURRENT_PORT="$(wg show wg0 listen-port 2>/dev/null | tr -d '[:space:]' || true)"
        fi

        if [ "$CURRENT_PORT" != "$WG_PORT" ]; then
          warn "WireGuard 当前监听端口为 $CURRENT_PORT，尝试使用 wg setconf 强制写入 $WG_PORT…"
          TMP_CFG="$(mktemp)"
          cat >"$TMP_CFG" <<FORCE
[Interface]
PrivateKey = $SERVER_PRIVATE
ListenPort = $WG_PORT
FORCE
          if ! output=$(wg setconf wg0 "$TMP_CFG" 2>&1); then
            warn "wg setconf 强制监听端口失败：$output"
          fi
          rm -f "$TMP_CFG"
          sleep 1
          CURRENT_PORT="$(wg show wg0 listen-port 2>/dev/null | tr -d '[:space:]' || true)"
        fi

        if [ "$CURRENT_PORT" != "$WG_PORT" ]; then
          in_use_msg=""
          if ss -lun 2>/dev/null | grep -q ":$WG_PORT"; then
            in_use_msg=" (检测到其他进程占用 $WG_PORT/udp)"
          fi
          err "ERROR: WireGuard 实际监听端口 ($CURRENT_PORT) 与期望值 ($WG_PORT) 不符$in_use_msg"
          wg show wg0 || true
          ss -lun || true
          systemctl status wg-quick@wg0 --no-pager -l || true
          exit 1
        fi

        if ss -lun 2>/dev/null | grep -q ":$WG_PORT"; then
          log "确认 UDP $WG_PORT 已监听"
        else
          warn "ss 未检测到 UDP $WG_PORT 监听，继续后续步骤 (wg show 正常)"
        fi

        SERVER_PUBLIC_KEY=$(wg show wg0 public-key)
        SERVER_ENDPOINT_IP=$(curl -4 -s ifconfig.me || true)
        if [ -z "$SERVER_ENDPOINT_IP" ]; then
          SERVER_ENDPOINT_IP="$SERVER_FALLBACK_IP"
        fi
        ENDPOINT="${{SERVER_ENDPOINT_IP}}:${{WG_PORT}}"

        ensure_client_keys() {{
          local name="$1"
          local dir="$2"
          local priv_file="$dir/${{name}}_private.key"
          local pub_file="$dir/${{name}}_public.key"
          if [ ! -f "$priv_file" ]; then
            wg genkey | tee "$priv_file" | wg pubkey > "$pub_file"
          else
            cat "$priv_file" | wg pubkey > "$pub_file"
          fi
          chmod 600 "$priv_file" "$pub_file"
        }}

        ensure_client_keys "desktop" "$DESKTOP_DIR"
        ensure_client_keys "iphone" "$IPHONE_DIR"

        DESKTOP_PRIV=$(cat "$DESKTOP_DIR/desktop_private.key")
        DESKTOP_PUB=$(cat "$DESKTOP_DIR/desktop_public.key")
        cat >"$DESKTOP_DIR/desktop.conf" <<CFG
[Interface]
PrivateKey = $DESKTOP_PRIV
Address = $DESKTOP_IP
DNS = $DNS_SERVERS
MTU = $DESKTOP_MTU

[Peer]
PublicKey = $SERVER_PUBLIC_KEY
AllowedIPs = $ALLOWED_IPS
Endpoint = $ENDPOINT
PersistentKeepalive = 25
CFG
        chmod 600 "$DESKTOP_DIR/desktop.conf"

        IPHONE_PRIV=$(cat "$IPHONE_DIR/iphone_private.key")
        IPHONE_PUB=$(cat "$IPHONE_DIR/iphone_public.key")
        cat >"$IPHONE_DIR/iphone.conf" <<CFG
[Interface]
PrivateKey = $IPHONE_PRIV
Address = $IPHONE_IP
DNS = $DNS_SERVERS

[Peer]
PublicKey = $SERVER_PUBLIC_KEY
AllowedIPs = $ALLOWED_IPS
Endpoint = $ENDPOINT
PersistentKeepalive = 25
CFG
        chmod 600 "$IPHONE_DIR/iphone.conf"

        wg set wg0 peer "$DESKTOP_PUB" remove 2>/dev/null || true
        wg set wg0 peer "$DESKTOP_PUB" allowed-ips "$DESKTOP_IP"
        wg set wg0 peer "$IPHONE_PUB" remove 2>/dev/null || true
        wg set wg0 peer "$IPHONE_PUB" allowed-ips "$IPHONE_IP"

        wg-quick save wg0
        systemctl restart wg-quick@wg0

        qrencode -o "$IPHONE_DIR/iphone.png" -s 8 -m 2 <"$IPHONE_DIR/iphone.conf" || true

        missing=0
        for f in "$DESKTOP_DIR/desktop.conf" "$IPHONE_DIR/iphone.conf" "$IPHONE_DIR/iphone.png"; do
          if [ ! -s "$f" ]; then
            err "文件未生成：$f"
            missing=1
          fi
        done
        if [ "$missing" -ne 0 ]; then
          ls -l "$DESKTOP_DIR" "$IPHONE_DIR" || true
          exit 1
        fi

        log "验证配置文件："
        ls -lh "$DESKTOP_DIR" "$IPHONE_DIR" || true

        printf 'SERVER_PUBLIC_KEY=%s\n' "$SERVER_PUBLIC_KEY"
        printf 'DESKTOP_PUBLIC_KEY=%s\n' "$DESKTOP_PUB"
        printf 'IPHONE_PUBLIC_KEY=%s\n' "$IPHONE_PUB"
        printf 'ENDPOINT=%s\n' "$ENDPOINT"
        printf 'WAN_IF=%s\n' "$WAN_IF"

        cat <<SUMMARY
──────────────────────────────
[WireGuard 已配置完毕]
服务器：
  公钥：$SERVER_PUBLIC_KEY
  端点：$ENDPOINT
客户端：
  桌面：/etc/wireguard/clients/desktop/desktop.conf
  iPhone：/etc/wireguard/clients/iphone/iphone.conf
  iPhone二维码：/etc/wireguard/clients/iphone/iphone.png
──────────────────────────────
SUMMARY
        """
    ).strip()

def _wait_for_port_22(ip: str, *, attempts: int = 10, interval: int = 5) -> bool:
    """Probe TCP/22 on ``ip`` every ``interval`` seconds until success or ``attempts`` exhausted."""

    for attempt in range(1, attempts + 1):
        log_info(f"  ↻ 第 {attempt} 次检测：连接 {ip}:22 …")
        try:
            with socket.create_connection((ip, 22), timeout=5):
                log_success("   SSH 端口已开放。")
                return True
        except OSError as exc:
            log_warning(f"⚠️ 连接失败：{exc}")
        time.sleep(interval)
    log_error("❌ 在预设次数内未检测到 SSH 端口开放。")
    return False


def _wait_for_passwordless_ssh(ip: str, key_path: Path, *, attempts: int = 12, interval: int = 10) -> bool:
    """Attempt ``ssh root@ip true`` until passwordless login succeeds."""

    expanded = key_path.expanduser()
    if not expanded.exists():
        log_warning(f"⚠️ 找不到私钥文件：{expanded}，无法完成免密校验。")
        return False

    command = [
        "ssh",
        "-i",
        str(expanded),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"root@{ip}",
        "true",
    ]

    last_stdout = ""
    last_stderr = ""
    for attempt in range(1, attempts + 1):
        log_info(f"  ↻ 第 {attempt} 次免密检测：ssh root@{ip} true")
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                **_SUBPROCESS_TEXT_KWARGS,
                timeout=45,
            )
        except subprocess.TimeoutExpired as exc:
            last_stdout = (exc.stdout or "").strip()
            last_stderr = (exc.stderr or "").strip()
            log_warning("   ssh 命令在 45 秒内未返回，可能受到网络限制或服务器尚未就绪。")
            if last_stdout:
                log_warning(f"   stdout: {last_stdout}")
            if last_stderr:
                log_warning(f"   stderr: {last_stderr}")
        else:
            last_stdout = (result.stdout or "").strip()
            last_stderr = (result.stderr or "").strip()
            if result.returncode == 0:
                log_success("   免密 SSH 校验通过。")
                return True
            if last_stdout:
                log_warning(f"   stdout: {last_stdout}")
            if last_stderr:
                log_warning(f"   stderr: {last_stderr}")
        time.sleep(interval)

    log_error(
        "❌ 免密 SSH 校验失败。"
        + (f" 最近一次 stdout: {last_stdout}" if last_stdout else "")
        + (f" stderr: {last_stderr}" if last_stderr else "")
    )
    return False


def _print_manual_ssh_hint() -> None:
    """Display manual troubleshooting guidance for SSH key injection issues."""

    log_warning("⚠️ 免密连接失败，请在 Vultr 控制台使用 View Console 登录，并执行：")
    log_warning("  cat /root/.ssh/authorized_keys")
    log_warning("  chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys")
    log_warning("  systemctl restart ssh")
    log_warning("然后重新运行部署。")


def create_vps() -> None:
    """Create a Vultr VPS using environment-driven defaults."""

    from core.tools.vultr_manager import (  # pylint: disable=import-outside-toplevel
        VultrError,
        create_instance,
        destroy_instance,
        list_ssh_keys,
        wait_instance_active,
    )

    log_section("🧱 Step 2: Create VPS")
    _log_selected_platform()

    api_key = os.environ.get("VULTR_API_KEY", "")
    if not api_key:
        log_error("❌ 未检测到环境变量 VULTR_API_KEY。请先设置后重试。")
        return

    env_region = os.getenv("VULTR_REGION", "").strip()
    env_plan = os.getenv("VULTR_PLAN", "").strip()
    env_snapshot_id = os.getenv("VULTR_SNAPSHOT_ID", "").strip()
    env_sshkey_name = os.getenv("VULTR_SSHKEY_NAME", "").strip()

    default_region = env_region or "nrt"
    default_plan = env_plan or "vc2-4c-8gb"

    if env_region:
        region = env_region
        log_info(f"→ 使用环境变量 VULTR_REGION={region}")
    else:
        region = input(f"region [{default_region}]: ").strip() or default_region

    if env_plan:
        plan = env_plan
        log_info(f"→ 使用环境变量 VULTR_PLAN={plan}")
    else:
        plan = input(f"plan [{default_plan}]: ").strip() or default_plan

    snapshot_id = ""
    snapshot_desc = env_snapshot_id or "VULTR_SNAPSHOT_ID"
    default_mode = "1" if env_snapshot_id else "2"
    mode_prompt = "实例来源 [1=使用快照"
    if env_snapshot_id:
        mode_prompt += f"({env_snapshot_id})"
    mode_prompt += ", 2=全新 Ubuntu 22.04]"
    mode = input(f"{mode_prompt} [{default_mode}]: ").strip() or default_mode

    use_snapshot = mode == "1"
    if use_snapshot:
        snapshot_input = input(f"snapshot_id [{snapshot_desc}]: ").strip()
        snapshot_id = snapshot_input or env_snapshot_id
        if not snapshot_id:
            log_error("❌ 请选择有效的快照 ID，或返回重新选择全新系统选项。")
            return
        if env_snapshot_id and snapshot_id == env_snapshot_id:
            log_info(f"→ 使用环境变量 VULTR_SNAPSHOT_ID={snapshot_id}")
        else:
            log_info(f"→ 使用 snapshot_id={snapshot_id}")
    else:
        if env_snapshot_id:
            log_info("→ 已选择全新 Ubuntu 22.04，将忽略环境变量 VULTR_SNAPSHOT_ID。")

    selected_keyname = env_sshkey_name
    sshkey_prompt_default = env_sshkey_name or "VULTR_SSHKEY_NAME"
    sshkey_input = input(f"ssh_keyname [{sshkey_prompt_default}]: ").strip()
    selected_keyname = sshkey_input or env_sshkey_name
    if not selected_keyname:
        log_error("❌ 未提供 SSH 公钥名称，请先在 Vultr 控制台创建 SSH 公钥。")
        return

    log_info("→ 查询 SSH 公钥信息…")
    try:
        ssh_keys = list_ssh_keys(api_key)
    except VultrError as exc:
        status_code = None
        cause = exc.__cause__
        if cause is not None:
            status_code = getattr(getattr(cause, "response", None), "status_code", None)
        if status_code == 401:
            log_error(
                "❌ 获取 SSH Key 列表失败，请检查 API Key 权限或 Access Control 白名单（IPv4/IPv6）。"
            )
        else:
            log_error(f"❌ 创建失败：获取 SSH 公钥列表异常：{exc}")
        return

    if not ssh_keys:
        log_error(
            "❌ 获取 SSH Key 列表失败，请检查 API Key 权限或 Access Control 白名单（IPv4/IPv6）。"
        )
        return

    matched_key: dict[str, Any] | None = None
    if selected_keyname:
        for item in ssh_keys:
            if item.get("name") == selected_keyname:
                matched_key = item
                break
    if matched_key is None:
        available = ", ".join(
            item.get("name", "") or item.get("id", "") or "-" for item in ssh_keys
        )
        log_error(
            "❌ 未找到名称匹配 VULTR_SSHKEY_NAME 的 SSH 公钥。请确认环境变量设置正确。\n"
            f"   当前账号可用公钥：{available}"
        )
        return

    ssh_key_id = matched_key.get("id", "")
    ssh_key_name = matched_key.get("name", "")
    ssh_public_text = matched_key.get("ssh_key", "")
    if not ssh_key_id:
        log_error("❌ 匹配到的 SSH 公钥缺少 ID，请在 Vultr 控制台重新创建后再试。")
        return
    log_info(f"→ 已选择 SSH 公钥：{ssh_key_name}")

    log_info("→ 创建实例中…")
    instance_id = ""
    ip = ""
    cloud_init: str | None = None
    if use_snapshot and ssh_public_text:
        cloud_init = (
            "#cloud-config\n"
            "users:\n"
            "  - name: root\n"
            "    ssh_authorized_keys:\n"
            f"      - {ssh_public_text}\n"
            "runcmd:\n"
            "  - systemctl restart ssh\n"
        )
    try:
        instance = create_instance(
            api_key,
            region=region,
            plan=plan,
            snapshot_id=snapshot_id if use_snapshot else None,
            sshkey_ids=[ssh_key_id],
            user_data=cloud_init,
        )
        instance_id = instance.get("id", "")
        if not instance_id:
            raise VultrError("Create instance returned empty id")
        log_info(f"→ 实例已创建，id={instance_id}，等待 active …")
        ready = wait_instance_active(api_key, instance_id, timeout=600, interval=10)
        ip = ready.get("ip")
        if not ip:
            raise VultrError("等待实例 active 时未获取到 IP")
        log_success(f"✅ 实例就绪：id={instance_id}  ip={ip}")
        log_info("→ 执行 ssh-keygen -R 清理旧指纹…")
        subprocess.run(["ssh-keygen", "-R", ip], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_info("→ 第一阶段：检测 SSH 端口 22 是否开放（每 5 秒，最多 10 次）…")
        key_path_default = Path.home() / ".ssh" / "id_ed25519"
        port_ready = _wait_for_port_22(ip)
        if port_ready:
            log_info("→ 第二阶段：校验免密 SSH 是否可用…")
            ssh_ready = _wait_for_passwordless_ssh(ip, key_path_default)
        else:
            ssh_ready = False
        if ssh_ready:
            log_success("✅ 免密 SSH 已生效，可继续部署 WireGuard。")
        else:
            _print_manual_ssh_hint()
    except VultrError as exc:
        log_error(f"❌ 创建失败：{exc}")
        if instance_id:
            try:
                destroy_instance(api_key, instance_id)
                log_warning("⚠️ 已尝试清理未就绪实例。")
            except VultrError as cleanup_exc:
                log_warning(f"⚠️ 清理实例失败：{cleanup_exc}")
        return

    artifacts_dir = ARTIFACTS_DIR
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    instance_info: dict[str, Any] = {
        "id": instance_id,
        "ip": ip,
        "region": region,
        "plan": plan,
        "source": "snapshot" if use_snapshot else "os",
        "ssh_key": ssh_key_name,
        "ssh_key_name": ssh_key_name,
        "ssh_key_id": ssh_key_id,
        "ssh_key_ids": [ssh_key_id],
        "created_at": int(time.time()),
        "cloud_init_injected": bool(cloud_init),
    }
    instance_file = artifacts_dir / "instance.json"
    instance_file.write_text(
        json.dumps(instance_info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log_success(f"已写入 {instance_file}")


def inspect_vps_inventory() -> None:
    """Inspect existing Vultr instances and optionally destroy them."""

    from core.tools.vultr_manager import (  # pylint: disable=import-outside-toplevel
        VultrError,
        destroy_instance,
        list_instances,
    )

    log_section("🧾 Step 4: 检查 Vultr 实例")
    _log_selected_platform()

    api_key = os.environ.get("VULTR_API_KEY", "").strip()
    if not api_key:
        log_error("❌ 未检测到环境变量 VULTR_API_KEY。请先设置后重试。")
        return

    log_info("→ 正在查询账户下的实例…")
    try:
        instances = list_instances(api_key)
    except VultrError as exc:
        log_error(f"❌ 查询实例失败：{exc}")
        return

    if not instances:
        log_success("✅ 当前账户没有任何 Vultr 实例。")
        return

    def describe_instance(index: int, instance: dict[str, Any]) -> str:
        instance_id = instance.get("id", "")
        label = instance.get("label") or "-"
        region = instance.get("region")
        if isinstance(region, dict):
            region_code = region.get("code") or region.get("id") or ""
        else:
            region_code = str(region or "")
        main_ip = instance.get("main_ip") or "-"
        status = instance.get("status") or "-"
        power_status = instance.get("power_status") or "-"
        return (
            f"{index}) id={instance_id} | label={label} | region={region_code or '-'} | "
            f"ip={main_ip} | status={status}/{power_status}"
        )

    while True:
        log_info("→ 当前账号存在以下实例：")
        for idx, item in enumerate(instances, start=1):
            log_info(describe_instance(idx, item))

        choice = input("输入序号销毁实例，或直接回车返回主菜单: ").strip().lower()
        if choice in {"", "q", "quit", "exit"}:
            log_info("→ 已退出实例检查，不执行销毁操作。")
            return
        if not choice.isdigit():
            log_error("❌ 无效选择，请输入列表中的序号或直接回车退出。")
            continue

        index = int(choice)
        if index < 1 or index > len(instances):
            log_error("❌ 序号超出范围，请重试。")
            continue

        target = instances[index - 1]
        instance_id = target.get("id", "")
        label = target.get("label") or instance_id or "实例"
        confirm = input(f"确认销毁实例 {label}? (y/N): ").strip().lower()
        if confirm not in {"y", "yes"}:
            log_info("→ 已取消销毁。")
            continue

        if not instance_id:
            log_error("❌ 目标实例缺少 ID，无法执行销毁。")
            continue

        try:
            destroy_instance(api_key, instance_id)
        except VultrError as exc:
            log_error(f"❌ 销毁实例失败：{exc}")
            continue

        log_success(f"✅ 已提交销毁实例 {instance_id}。")
        instances.pop(index - 1)
        if not instances:
            log_success("✅ 当前账户已无其他 Vultr 实例。")
            return


def _log_selected_platform() -> None:
    if SELECTED_PLATFORM:
        label = PLATFORM_CHOICES.get(SELECTED_PLATFORM, SELECTED_PLATFORM)
        log_info(f"→ 当前本机系统：{label}")
    else:
        log_warning("⚠️ 尚未选择本机系统，可通过第 1 步执行环境检查。")


def _update_server_info(data: dict[str, Any]) -> None:
    artifacts_dir = ARTIFACTS_DIR
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    server_file = artifacts_dir / "server.json"
    existing: dict[str, Any] = {}
    if server_file.exists():
        try:
            existing = json.loads(server_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(data)
    server_file.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _wireguard_windows_candidate_paths() -> list[Path]:
    """Return likely installation paths for WireGuard for Windows."""

    bases: list[Path] = []
    seen: set[Path] = set()
    env_keys = ["ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"]
    for key in env_keys:
        value = os.environ.get(key)
        if not value:
            continue
        base = Path(value) / "WireGuard"
        if base not in seen:
            seen.add(base)
            bases.append(base)

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        base = Path(local_appdata) / "WireGuard"
        if base not in seen:
            seen.add(base)
            bases.append(base)

    fallback_paths = [
        Path(r"C:\Program Files\WireGuard"),
        Path(r"C:\Program Files (x86)\WireGuard"),
    ]
    for base in fallback_paths:
        if base not in seen:
            seen.add(base)
            bases.append(base)

    candidates: list[Path] = []
    for base in bases:
        candidates.append(base / "WireGuard.exe")
        candidates.append(base / "wireguard.exe")
    return candidates


def _locate_wireguard_windows_executable() -> Path | None:
    """Locate the WireGuard for Windows executable if it exists."""

    for candidate in _wireguard_windows_candidate_paths():
        if candidate.is_file():
            return candidate
    binary = shutil.which("wireguard")
    if binary:
        return Path(binary)
    return None


def _install_wireguard_windows_via_powershell() -> bool:
    """Attempt to install WireGuard for Windows using PowerShell."""

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        log_warning("⚠️ 未找到 PowerShell，无法自动安装 WireGuard for Windows。")
        return False

    script = textwrap.dedent(
        r"""
        $ErrorActionPreference = "Stop"
        $installerUrl = "https://download.wireguard.com/windows-client/wireguard-installer.exe"
        $tempPath = Join-Path -Path $env:TEMP -ChildPath "wireguard-installer.exe"
        Invoke-WebRequest -Uri $installerUrl -OutFile $tempPath
        if (-Not (Test-Path $tempPath)) {
            throw "下载 WireGuard 安装程序失败：$tempPath"
        }
        Start-Process -FilePath $tempPath -ArgumentList "/install /quiet" -Verb RunAs -Wait
        """
    ).strip()

    try:
        subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            capture_output=True,
            **_SUBPROCESS_TEXT_KWARGS,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stderr or exc.stdout or "").strip()
        if output:
            log_warning(
                f"⚠️ PowerShell 安装 WireGuard 失败（返回码 {exc.returncode}）。输出：{output}"
            )
        else:
            log_warning(f"⚠️ PowerShell 安装 WireGuard 失败（返回码 {exc.returncode}）。")
        return False
    except FileNotFoundError:
        log_warning("⚠️ 未找到 PowerShell，无法自动安装 WireGuard for Windows。")
        return False

    return True


def _ensure_wireguard_for_windows() -> None:
    """Ensure WireGuard for Windows is installed on the local machine."""

    if os.name != "nt":
        log_warning("⚠️ 当前环境非 Windows，无法自动安装 WireGuard for Windows。")
        return

    existing = _locate_wireguard_windows_executable()
    if existing:
        log_success(f"✅ 已检测到 WireGuard for Windows：{existing}")
        return

    log_info("→ 未检测到 WireGuard for Windows，尝试通过 PowerShell 自动安装 ...")
    if not _install_wireguard_windows_via_powershell():
        log_warning("⚠️ 自动安装 WireGuard for Windows 失败，请手动下载安装包。")
        return

    installed = _locate_wireguard_windows_executable()
    if installed:
        log_success(f"✅ WireGuard for Windows 安装完成：{installed}")
    else:
        log_warning("⚠️ 安装流程执行完毕，但未检测到 WireGuard for Windows，可手动确认。")


def _desktop_usage_tip() -> None:
    if SELECTED_PLATFORM == "windows":
        _ensure_wireguard_for_windows()
        log_info("→ 请在 WireGuard for Windows 中导入生成的 .conf 配置文件后启动隧道。")
    elif SELECTED_PLATFORM == "macos":
        log_info(
            "→ 请安装 WireGuard.app（macOS），双击配置文件或在应用内导入后连接。"
        )
    else:
        log_info(
            "→ 可在任意支持 WireGuard 的桌面客户端中导入该配置以连接 VPS。"
        )


def launch_gui() -> None:
    """打开可视化界面以操作各项功能。"""

    try:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext, simpledialog
    except Exception as exc:  # noqa: BLE001 - 捕获所有异常以保证 CLI 可继续执行
        log_error(f"❌ 无法加载图形界面组件：{exc}")
        return

    import builtins
    import contextlib
    import io

    window = tk.Tk()
    window.title("PrivateTunnel 桌面助手 - 图形界面")

    text_area = scrolledtext.ScrolledText(window, wrap=tk.WORD, width=100, height=30, state=tk.DISABLED)
    text_area.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

    button_frame = tk.Frame(window)
    button_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

    def append_output(message: str) -> None:
        text_area.configure(state=tk.NORMAL)
        text_area.insert(tk.END, message)
        text_area.see(tk.END)
        text_area.configure(state=tk.DISABLED)

    @contextlib.contextmanager
    def patched_streams() -> Any:
        buffer = io.StringIO()
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = buffer
        sys.stderr = buffer
        try:
            yield buffer
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    @contextlib.contextmanager
    def patched_input() -> Any:
        original_input = builtins.input

        def gui_input(prompt: str = "") -> str:
            response = simpledialog.askstring("输入", prompt, parent=window)
            if response is None:
                return ""
            return response

        builtins.input = gui_input
        try:
            yield
        finally:
            builtins.input = original_input

    def run_action(action: Any, description: str) -> None:
        append_output(f"\n=== {description} ===\n")
        window.update_idletasks()
        try:
            with patched_streams() as buffer:
                with patched_input():
                    action()
        except SystemExit as exc:
            append_output(f"程序退出：{exc}\n")
        except Exception as exc:  # noqa: BLE001
            append_output(f"❌ {description} 失败：{exc}\n")
            messagebox.showerror("错误", f"{description} 失败：{exc}")
        else:
            output = buffer.getvalue()
            if output:
                append_output(output)
            messagebox.showinfo("完成", f"{description} 已完成。")

    actions = [
        ("检查本机环境（Windows/macOS）", run_environment_check, "检查本机环境"),
        ("创建 VPS（Vultr）", create_vps, "创建 VPS"),
        ("准备本机接入 VPS 网络", prepare_wireguard_access, "准备本机接入 VPS 网络"),
        ("检查账户中的 Vultr 实例", inspect_vps_inventory, "检查账户中的 Vultr 实例"),
    ]

    for label, func, description in actions:
        button = tk.Button(button_frame, text=label, command=lambda f=func, d=description: run_action(f, d))
        button.pack(fill=tk.X, pady=3)

    tk.Button(button_frame, text="关闭", command=window.destroy).pack(fill=tk.X, pady=(12, 0))

    window.mainloop()


def _load_instance_for_diagnostics() -> tuple[str, Path] | None:
    """Return the Vultr instance IP recorded on disk, if any."""

    inst_path = ARTIFACTS_DIR / "instance.json"
    if not inst_path.exists():
        return None

    try:
        data = json.loads(inst_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001 - diagnostics best-effort
        log_warning(f"⚠️ 无法读取 {inst_path}：{exc}，跳过网络排查。")
        return None

    ip = str(data.get("ip", "")).strip()
    if not ip:
        log_warning(f"⚠️ {inst_path} 缺少 IP 字段，跳过网络排查。")
        return None

    return ip, inst_path


def _diagnostic_ping(ip: str) -> bool:
    """Run a single ping against ``ip`` and report the outcome."""

    log_info(f"→ 排查步骤：ping {ip}")
    ping_cmd = ["ping", "-n" if os.name == "nt" else "-c", "1", ip]
    try:
        result = subprocess.run(  # noqa: S603
            ping_cmd,
            check=False,
            capture_output=True,
            **_SUBPROCESS_TEXT_KWARGS,
            timeout=20,
        )
    except subprocess.SubprocessError as exc:
        log_error(f"❌ 无法执行 ping：{exc}")
        log_info("→ 请确认本机允许发起 ICMP 请求或尝试改用稳定的国际出口网络。")
        return False

    if result.returncode == 0:
        log_success("✅ ping 成功，本地可以访问该实例。")
        return True

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    log_error("❌ ping 失败，可能是网络抖动或运营商屏蔽 ICMP。")
    if stdout:
        log_warning(f"   stdout: {stdout}")
    if stderr:
        log_warning(f"   stderr: {stderr}")
    log_info("→ 建议：检查当前出口网络、关闭可能干扰的代理/防火墙，或稍后重试。")
    return False


def _diagnostic_port_22(ip: str) -> bool:
    """Attempt to establish a TCP connection to ``ip:22`` once."""

    log_info(f"→ 排查步骤：检测 {ip}:22 是否开放")
    try:
        with socket.create_connection((ip, 22), timeout=5):
            log_success("✅ TCP/22 可达，SSH 端口开放。")
            return True
    except OSError as exc:
        log_error(f"❌ 无法连通 {ip}:22：{exc}")
        log_info(
            "→ 建议：确认 VPS 正在运行，并检查云防火墙、本地防火墙或出口线路是否放行 TCP/22。"
        )
        return False


def _resolve_diagnostic_key_path() -> Path | None:
    """Return a reasonable private-key path for diagnostic SSH probes."""

    override = os.environ.get("PT_SSH_PRIVATE_KEY", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    default_prompt = _default_private_key_prompt()
    if default_prompt:
        candidates.append(Path(default_prompt).expanduser())

    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _diagnostic_passwordless_ssh(ip: str, key_path: Path) -> bool:
    """Attempt a single passwordless SSH probe with ``key_path``."""

    log_info(f"→ 排查步骤：使用 {key_path} 验证免密 SSH")
    result = probe_publickey_auth(
        ip,
        key_path,
        retries=1,
        interval=0,
        timeout=15,
    )
    if result.success:
        log_success("✅ 免密 SSH 正常，可直接部署 WireGuard。")
        return True

    log_error("❌ 免密 SSH 验证失败。")
    if result.error:
        log_warning(f"   error: {result.error}")
    if result.stderr:
        log_warning(f"   stderr: {result.stderr}")
    if result.stdout and result.stdout != "ok":
        log_warning(f"   stdout: {result.stdout}")
    log_info("→ 建议：确认 Vultr 实例已注入正确公钥，或通过控制台登录执行授权命令。")
    _print_manual_ssh_hint()
    return False


def _run_network_diagnostics(ip: str) -> bool:
    """Run connectivity diagnostics against the recorded Vultr instance."""

    log_section("🌐 网络连通性排查")
    overall_ok = True

    if not _diagnostic_ping(ip):
        overall_ok = False

    port_ok = _diagnostic_port_22(ip)
    if not port_ok:
        overall_ok = False

    key_path = _resolve_diagnostic_key_path()
    if key_path and port_ok:
        if not _diagnostic_passwordless_ssh(ip, key_path):
            overall_ok = False
    elif not key_path:
        log_warning("⚠️ 未找到可用的私钥文件，跳过免密 SSH 验证。")

    return overall_ok


def _maybe_run_network_diagnostics() -> None:
    """Automatically run network diagnostics when an instance is recorded."""

    instance = _load_instance_for_diagnostics()
    if not instance:
        log_info("→ 未检测到 Vultr 实例记录，跳过网络排查。")
        return

    ip, inst_path = instance
    log_info(f"→ 检测到实例记录：{inst_path}，即将尝试排查与 {ip} 的连通性…")
    if _run_network_diagnostics(ip):
        log_success("✅ 网络排查完成，当前环境可直连 VPS。")
    else:
        log_warning("⚠️ 网络排查发现异常，请根据上方提示处理后再继续。")


def run_environment_check() -> None:
    global SELECTED_PLATFORM

    log_section("🩺 Step 1: 检查本机环境")
    options = {"1": "windows", "2": "macos"}
    while True:
        log_info("请选择本机系统类型：")
        log_info("  1) Windows")
        log_info("  2) macOS")
        log_info("  q) 返回主菜单")
        choice = input("系统选择: ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            log_warning("⚠️ 已取消环境检查。")
            return
        if choice in options:
            SELECTED_PLATFORM = options[choice]
            break
        log_error("❌ 无效选择，请重新输入。")

    label = PLATFORM_CHOICES.get(SELECTED_PLATFORM, SELECTED_PLATFORM)
    log_info(f"→ 将针对 {label} 环境执行体检…")
    command = [
        sys.executable,
        "scripts/project_doctor.py",
        "--platform",
        SELECTED_PLATFORM,
    ]
    code = subprocess.call(command)
    if code == 0:
        log_success("✅ 体检通过。详见 PROJECT_HEALTH_REPORT.md")
    else:
        log_warning("⚠️ 体检发现问题，请按报告提示修复后再继续。")

    _maybe_run_network_diagnostics()


from core.ssh_utils import (
    ask_key_path,
    nuke_known_host,
    pick_default_key,
    probe_publickey_auth,
)


def wait_instance_ping(ip: str, timeout: int = 600, interval: int = 60) -> bool:
    """Ping ``ip`` every ``interval`` seconds until reachable or timeout."""

    deadline = time.time() + timeout
    ping_command = [
        "ping",
        "-n" if os.name == "nt" else "-c",
        "1",
        ip,
    ]
    attempt = 1
    while time.time() < deadline:
        log_info(f"  ↻ 第 {attempt} 次检测：ping {ip}")
        try:
            result = subprocess.run(
                ping_command,
                check=False,
                capture_output=True,
                **_SUBPROCESS_TEXT_KWARGS,
                timeout=30,
            )
        except subprocess.SubprocessError as exc:
            log_warning(f"⚠️ 执行 ping 命令失败：{exc}")
            time.sleep(interval)
            attempt += 1
            continue

        if result.returncode == 0:
            return True

        log_warning("⚠️ 暂未连通，继续等待实例初始化…")
        time.sleep(interval)
        attempt += 1
    return False


def _resolve_env_default(
    *env_keys: str,
    default: str,
) -> tuple[str, str | None]:
    """Return the first non-empty environment override and its key."""

    for key in env_keys:
        value = os.environ.get(key)
        if value:
            return value.strip(), key
    return default, None


def _default_private_key_prompt() -> str:
    """Return the default SSH private key path prompt for Step 3."""

    override = os.environ.get("PT_SSH_PRIVATE_KEY", "").strip()
    if override:
        return override
    if os.name == "nt":
        username = os.environ.get("USERNAME") or os.environ.get("USER") or "User"
        return str(Path(f"C:/Users/{username}/.ssh/id_ed25519"))
    return pick_default_key()



def prepare_wireguard_access() -> None:
    """Configure WireGuard end-to-end, including client provisioning."""

    inst_path = ARTIFACTS_DIR / "instance.json"
    if not inst_path.exists():
        log_section("🛡 Step 3: 准备本机接入 VPS 网络")
        log_error(f"❌ 未找到 {inst_path}，请先创建 VPS。")
        return

    try:
        instance = json.loads(inst_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log_section("🛡 Step 3: 准备本机接入 VPS 网络")
        log_error(f"❌ 解析实例信息失败：{exc}")
        return

    ip = instance.get("ip")
    instance_id = instance.get("id", "")
    if not ip:
        log_section("🛡 Step 3: 准备本机接入 VPS 网络")
        log_error(f"❌ 实例信息缺少 IP 字段，请重新创建或检查 {inst_path}。")
        return

    log_section("🛡 Step 3: 准备本机接入 VPS 网络")
    _log_selected_platform()

    deploy_log_path = _init_deploy_log()
    log_info(f"→ 本次部署日志：{deploy_log_path}")

    log_info(f"→ 目标实例：{ip}")
    if LISTEN_PORT_SOURCE:
        log_info(f"→ WireGuard 监听端口：{LISTEN_PORT} （来自环境变量 {LISTEN_PORT_SOURCE}）")
    else:
        log_info(
            f"→ WireGuard 监听端口：{LISTEN_PORT} （默认值，可通过环境变量 PRIVATETUNNEL_WG_PORT/PT_WG_PORT 覆盖）"
        )

    desktop_ip, desktop_source = _resolve_env_default("PT_DESKTOP_IP", default="10.6.0.3/32")
    if desktop_source:
        log_info(f"→ 桌面客户端 IP：{desktop_ip} （来自环境变量 {desktop_source}）")
    else:
        log_info(
            "→ 桌面客户端 IP：{value} （默认值，可通过环境变量 PT_DESKTOP_IP 覆盖）".format(value=desktop_ip)
        )

    iphone_ip, iphone_source = _resolve_env_default("PT_IPHONE_IP", default="10.6.0.2/32")
    if iphone_source:
        log_info(f"→ iPhone 客户端 IP：{iphone_ip} （来自环境变量 {iphone_source}）")
    else:
        log_info(
            "→ iPhone 客户端 IP：{value} （默认值，可通过环境变量 PT_IPHONE_IP 覆盖）".format(value=iphone_ip)
        )

    dns_value, dns_source = _resolve_env_default("PT_DNS", default="1.1.1.1, 8.8.8.8")
    if dns_source:
        log_info(f"→ 客户端 DNS：{dns_value} （来自环境变量 {dns_source}）")
    else:
        log_info(
            "→ 客户端 DNS：{value} （默认值，可通过环境变量 PT_DNS 覆盖）".format(value=dns_value)
        )

    allowed_ips, allowed_source = _resolve_env_default("PT_ALLOWED_IPS", default="0.0.0.0/0, ::/0")
    if allowed_source:
        log_info(f"→ 客户端 AllowedIPs：{allowed_ips} （来自环境变量 {allowed_source}）")
    else:
        log_info(
            "→ 客户端 AllowedIPs：{value} （默认值，可通过环境变量 PT_ALLOWED_IPS 覆盖）".format(
                value=allowed_ips
            )
        )

    client_mtu_raw = os.environ.get("PT_CLIENT_MTU", "").strip()
    if client_mtu_raw:
        desktop_mtu = client_mtu_raw
        log_info(f"→ 客户端 MTU：{desktop_mtu} （来自环境变量 PT_CLIENT_MTU）")
    else:
        desktop_mtu = "1280"
        log_info("→ 客户端 MTU：1280（默认值，可通过环境变量 PT_CLIENT_MTU 覆盖）")

    default_key_prompt = _default_private_key_prompt()
    key_path = Path(ask_key_path(default_key_prompt)).expanduser()
    log_info(f"→ 使用私钥：{key_path}")

    try:
        _clean_known_host(ip)
    except Exception as exc:  # noqa: BLE001 - cleanup is best effort
        log_warning(f"⚠️ 清理 known_hosts 时出现问题：{exc}")

    try:
        log_info("→ 第一阶段：检测 SSH 端口 22 是否开放（每 5 秒，最多 10 次）…")
        if not _wait_for_port_22(ip):
            _print_manual_ssh_hint()
            raise DeploymentError("未检测到 VPS SSH 端口开放。")

        log_info("→ 第二阶段：校验免密 SSH 是否可用…")
        if not _wait_for_passwordless_ssh(ip, key_path):
            _print_manual_ssh_hint()
            raise DeploymentError("免密 SSH 校验失败，请确认公钥已写入 VPS。")

        log_success("✅ 公钥认证已生效。")

        _set_ssh_context(ip, key_path)
        remote_script = deploy_wireguard_remote_script(
            LISTEN_PORT,
            desktop_ip,
            iphone_ip,
            ip,
            dns_value,
            allowed_ips,
            desktop_mtu,
        )
        script_payload = (
            "cat <<'EOS' >/tmp/privatetunnel-wireguard.sh\n"
            f"{remote_script}\n"
            "EOS\n"
        )
        env_parts = [
            f"{key}={shlex.quote(value)}"
            for key, value in {
                "WG_PORT": str(LISTEN_PORT),
                "PT_DESKTOP_IP": desktop_ip,
                "PT_IPHONE_IP": iphone_ip,
                "PT_DNS": dns_value,
                "PT_ALLOWED_IPS": allowed_ips,
                "PT_CLIENT_MTU": desktop_mtu,
            }.items()
            if value
        ]
        env_prefix = " ".join(env_parts)
        run_line = (
            f"{env_prefix + ' ' if env_prefix else ''}bash /tmp/privatetunnel-wireguard.sh "
            "&& rm -f /tmp/privatetunnel-wireguard.sh"
        )
        command_body = script_payload + run_line + "\n"
        command = f"bash -lc {shlex.quote(command_body)}"
        result = _ssh_run(command, timeout=1800, description="部署 WireGuard 服务端")

        summary: dict[str, str] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            prefixes = ("SERVER_", "DESKTOP_", "IPHONE_", "ENDPOINT=", "WAN_IF=")
            if any(line.startswith(prefix) for prefix in prefixes):
                key, _, value = line.partition("=")
                summary[key] = value.strip()

        server_pub = summary.get("SERVER_PUBLIC_KEY", "")
        desktop_pub = summary.get("DESKTOP_PUBLIC_KEY", "")
        iphone_pub = summary.get("IPHONE_PUBLIC_KEY", "")
        endpoint = summary.get("ENDPOINT", f"{ip}:{LISTEN_PORT}")
        wan_if = summary.get("WAN_IF", "")

        log_success("✅ 远端 WireGuard 已成功部署并完成 NAT/转发配置。")
        if wan_if:
            log_info(f"→ 外网接口：{wan_if}")

        artifacts_dir = ARTIFACTS_DIR
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        desktop_conf_local = artifacts_dir / "desktop.conf"
        iphone_conf_local = artifacts_dir / "iphone.conf"
        iphone_png_local = artifacts_dir / "iphone.png"

        remote_desktop_conf = "/etc/wireguard/clients/desktop/desktop.conf"
        remote_iphone_conf = "/etc/wireguard/clients/iphone/iphone.conf"
        remote_iphone_png = "/etc/wireguard/clients/iphone/iphone.png"

        log_info("→ 校验远端桌面端配置是否生成…")
        _ensure_remote_artifact(remote_desktop_conf, "桌面端配置文件")
        log_info("→ 校验远端 iPhone 配置是否生成…")
        _ensure_remote_artifact(remote_iphone_conf, "iPhone 配置文件")
        log_info("→ 校验远端 iPhone 二维码是否生成…")
        _ensure_remote_artifact(remote_iphone_png, "iPhone 二维码")

        log_info(f"→ 下载桌面端配置到 {desktop_conf_local}")
        if not _download_artifact(remote_desktop_conf, desktop_conf_local):
            raise DeploymentError("下载桌面端配置失败，请手动检查 /etc/wireguard/clients/desktop/desktop.conf。")

        log_info(f"→ 下载 iPhone 配置到 {iphone_conf_local}")
        if not _download_artifact(remote_iphone_conf, iphone_conf_local):
            raise DeploymentError("下载 iPhone 配置失败，请手动检查 /etc/wireguard/clients/iphone/iphone.conf。")

        log_info(f"→ 下载 iPhone 二维码到 {iphone_png_local}")
        if not _download_artifact(remote_iphone_png, iphone_png_local):
            raise DeploymentError("下载 iPhone 二维码失败，请检查远端 /etc/wireguard/clients/iphone/iphone.png。")

        log_success(f"✅ 已下载 iPhone 二维码：{iphone_png_local}")

        for path in (desktop_conf_local, iphone_conf_local, iphone_png_local):
            if not path.exists():
                raise DeploymentError(f"本地文件缺失：{path}")

        def _rel(path: Path) -> str:
            try:
                return str(path.relative_to(ROOT))
            except ValueError:
                return str(path)

        log_success(f"✅ Windows 客户端配置：{_rel(desktop_conf_local)}")
        log_success(f"✅ iPhone 配置：{_rel(iphone_conf_local)}")
        log_success(f"✅ iPhone 二维码：{_rel(iphone_png_local)}")

        server_info: dict[str, Any] = {
            "id": instance_id,
            "ip": ip,
            "server_pub": server_pub,
            "platform": SELECTED_PLATFORM or "",
            "endpoint": endpoint,
            "desktop_ip": desktop_ip,
            "iphone_ip": iphone_ip,
            "desktop_public_key": desktop_pub,
            "iphone_public_key": iphone_pub,
            "desktop_config": str(desktop_conf_local),
            "iphone_config": str(iphone_conf_local),
            "iphone_qr": str(iphone_png_local),
            "allowed_ips": allowed_ips,
            "dns": dns_value,
            "deploy_log": str(deploy_log_path),
        }
        if wan_if:
            server_info["wan_interface"] = wan_if
        _update_server_info(server_info)

        log_info("验证指南：")
        log_info(f"  1. Windows 打开 WireGuard 导入 {_rel(desktop_conf_local)} 并连接。")
        log_info("  2. 连接后运行：curl -4 ifconfig.me / curl -6 ifconfig.me，应显示 VPS 公网地址。")
        log_info("  3. 若能获取公网 IP 但无法上网，请检查代理/安全软件；如丢包，可继续使用默认 MTU=1280。")

        _desktop_usage_tip()
        log_info(f"→ 部署日志已保存至 {deploy_log_path}")
    except DeploymentError as exc:
        log_error(f"❌ 部署失败：{exc}")
        log_info(f"→ 详细日志：{deploy_log_path}")
    finally:
        _close_paramiko_client()
        global SSH_CTX
        SSH_CTX = None


MENU_ACTIONS: tuple[MenuAction, ...] = (
    MenuAction("1", "检查本机环境（Windows/macOS）", run_environment_check),
    MenuAction("2", "创建 VPS（Vultr）", create_vps),
    MenuAction("3", "准备本机接入 VPS 网络", prepare_wireguard_access),
    MenuAction("4", "检查账户中的 Vultr 实例", inspect_vps_inventory),
    MenuAction("5", "打开图形界面", launch_gui),
)

EXIT_CHOICES = {"q", "quit", "exit"}


def _print_main_menu() -> None:
    """Render the interactive menu in a consistent order."""

    print("\n=== PrivateTunnel 桌面助手 ===")
    for action in MENU_ACTIONS:
        print(f"{action.key}) {action.description}")
    print("q) 退出")


def main() -> None:
    try:
        overview_path = generate_project_overview(ROOT, ARTIFACTS_DIR / "project_overview.md")
        log_info(f"→ 已生成项目功能概览：{overview_path}")
    except Exception as exc:  # noqa: BLE001 - 后台任务失败不应阻止主流程
        log_warning(f"⚠️ 生成项目功能概览失败：{exc}")

    while True:
        _print_main_menu()
        choice = input("请选择: ").strip().lower()
        if choice in EXIT_CHOICES:
            break
        for action in MENU_ACTIONS:
            if choice == action.key:
                action.handler()
                break
        else:
            print("无效选项，请重试。")


if __name__ == "__main__":
    main()
