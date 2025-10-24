"""Vultr 云端部署向导 CLI。

用途：统一驱动环境检测、VPS 创建、网络接入准备与实例巡检。
约束：仅使用 Python 标准库，依赖 ``onepass.ux`` 日志工具。
示例：
    python deploy/cloud/vultr/cloud_vultr_cli.py env-check
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

CUR_DIR = Path(__file__).resolve().parent
PROJ_ROOT = CUR_DIR.parents[2]
if str(PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJ_ROOT))

from deploy.cloud.vultr.vultr_api import (  # noqa: E402
    VultrError,
    create_instance,
    create_ssh_key,
    get_instance,
    list_instances,
    list_os,
    list_plans,
    list_regions,
    list_ssh_keys,
    wait_for_instance_active,
)
from onepass.ux import Spinner, log_err, log_info, log_ok, log_warn, run_streamed, section  # noqa: E402

ENV_FILE = CUR_DIR / "vultr.env"
STATE_FILE = CUR_DIR / "state.json"


def _read_env_file(optional: bool = False) -> Dict[str, str]:
    if not ENV_FILE.exists():
        if optional:
            log_warn("未找到 vultr.env，将使用默认值并跳过部分检查。")
            return {}
        raise FileNotFoundError(
            "未找到 vultr.env。请复制 deploy/cloud/vultr/vultr.env.example 并填写后重试。"
        )
    data: Dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "=" not in text:
            log_warn(f"忽略无法解析的行：{text}")
            continue
        key, value = text.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _expand_path(value: str) -> Path:
    expanded = os.path.expandvars(value)
    return Path(expanded).expanduser()


def _format_exception(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _check_command(command: str, args: Optional[List[str]] = None) -> Tuple[bool, str]:
    args = args or ["--version"]
    path = shutil.which(command)
    if not path:
        return False, f"未检测到 {command}，请确认已安装并加入 PATH。"
    try:
        proc = subprocess.run([path, *args], capture_output=True, text=True, check=False)
    except OSError as exc:  # pragma: no cover - 平台相关
        return False, f"无法执行 {command}：{exc}"
    output = proc.stdout.strip() or proc.stderr.strip()
    if proc.returncode != 0:
        return False, f"执行 {command} {args} 失败：{output}"
    return True, output


def _load_state() -> Dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log_warn(f"无法解析 state.json：{exc}")
        return {}


def _write_state(data: Dict[str, str]) -> None:
    STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_env_check(_: argparse.Namespace) -> int:
    section("步骤①：检查本机环境")
    env = _read_env_file(optional=True)
    status_fail: List[str] = []
    status_warn: List[str] = []

    log_info(f"操作系统：{platform.system()} {platform.release()}")
    log_info(f"Python 版本：{platform.python_version()}")

    ok, message = _check_command(sys.executable, ["--version"])
    if ok:
        log_ok(f"Python 可用：{message}")
    else:
        log_err(message)
        status_fail.append("python")

    pwsh_path = shutil.which("pwsh")
    if pwsh_path:
        ok, message = _check_command("pwsh", ["-NoLogo", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
        if ok:
            log_ok(f"PowerShell 7+ 可用：{message}")
        else:
            log_warn(message)
            status_warn.append("pwsh")
    else:
        log_err("未找到 PowerShell 7 (pwsh)，请安装 https://aka.ms/powershell 并重启终端。")
        status_fail.append("pwsh")

    for cmd in ("ssh", "scp"):
        ok, message = _check_command(cmd)
        if ok:
            log_ok(f"{cmd} 可用：{message}")
        else:
            log_err(message)
            status_fail.append(cmd)

    if platform.system().lower() == "windows":
        log_info("Windows 环境将调用 cloud_env_check.ps1 进行服务检测。")
    else:
        log_info("macOS/Linux 将调用 cloud_env_check.ps1 做补充检测。")

    if env:
        key_path_value = env.get("SSH_PRIVATE_KEY")
        if key_path_value:
            priv_path = _expand_path(key_path_value)
            if priv_path.exists():
                log_ok(f"检测到私钥：{priv_path}")
                try:
                    mode = priv_path.stat().st_mode
                    if mode & 0o077:
                        log_warn("私钥权限较宽松，建议执行 chmod 600 确保安全。")
                        status_warn.append("ssh-key-perm")
                except OSError as exc:  # pragma: no cover
                    log_warn(f"无法检查私钥权限：{exc}")
            else:
                log_warn(f"未找到私钥 {priv_path}，可使用 ssh-keygen -t ed25519 生成。")
                status_warn.append("ssh-key-missing")
        else:
            log_warn("vultr.env 中未配置 SSH_PRIVATE_KEY，将跳过私钥检查。")
            status_warn.append("ssh-key-config")
    else:
        status_warn.append("env")

    ps_script = CUR_DIR / "cloud_env_check.ps1"
    if ps_script.exists() and pwsh_path:
        cmd = [pwsh_path, "-NoLogo", "-NoProfile", "-File", str(ps_script)]
        log_info("调用 PowerShell 环境检查脚本…")
        rc = run_streamed(cmd, heartbeat_s=30.0, show_cmd=False)
        if rc == 2:
            status_fail.append("cloud_env_check")
        elif rc == 1:
            status_warn.append("cloud_env_check")
    elif not pwsh_path:
        log_warn("由于未安装 pwsh，跳过 PowerShell 补充检测。")
        status_warn.append("pwsh-missing")
    else:
        log_warn("未找到 cloud_env_check.ps1 脚本。")
        status_warn.append("ps-script")

    if status_fail:
        log_err("环境检测存在阻塞项，请修复后重试。")
        return 2
    if status_warn:
        log_warn("环境检测有警告，但可继续执行后续步骤。")
        return 1
    log_ok("环境检测通过。")
    return 0


def _ensure_env_and_key() -> Dict[str, str]:
    env = _read_env_file(optional=False)
    api_key = env.get("VULTR_API_KEY", "").strip()
    if not api_key:
        raise VultrError("VULTR_API_KEY 未配置。")
    key_path_value = env.get("SSH_PRIVATE_KEY")
    if not key_path_value:
        raise VultrError("SSH_PRIVATE_KEY 未在 vultr.env 中配置。")
    private_key = _expand_path(key_path_value)
    if not private_key.exists():
        raise VultrError(f"未找到 SSH 私钥：{private_key}")
    env["SSH_PRIVATE_KEY_RESOLVED"] = str(private_key)
    public_key = private_key.with_suffix(private_key.suffix + ".pub") if private_key.suffix else Path(str(private_key) + ".pub")
    if public_key.exists():
        env["SSH_PUBLIC_KEY"] = public_key.read_text(encoding="utf-8").strip()
        env["SSH_PUBLIC_KEY_PATH"] = str(public_key)
    else:
        raise VultrError(f"未找到公钥 {public_key}，请执行 ssh-keygen 生成后重试。")
    return env


def _match_item(items: Iterable[dict], value: str, *fields: str) -> Optional[dict]:
    value_lower = value.lower()
    for item in items:
        for field in fields:
            field_value = item.get(field)
            if field_value is None:
                continue
            if str(field_value).lower() == value_lower:
                return item
    return None


def _resolve_region(value: str, api_key: str) -> str:
    regions = list_regions(api_key)
    match = _match_item(regions, value, "id", "city", "country", "continent")
    if not match:
        raise VultrError(f"无法匹配 region：{value}")
    return match.get("id")


def _resolve_plan(value: str, api_key: str) -> str:
    plans = list_plans(api_key)
    match = _match_item(plans, value, "id", "name", "description", "vcpu_count", "vcpus")
    if not match:
        raise VultrError(f"无法匹配 plan：{value}")
    return match.get("id")


def _resolve_os(value: str, api_key: str) -> int:
    try:
        return int(value)
    except ValueError:
        pass
    os_list = list_os(api_key)
    match = _match_item(os_list, value, "id", "name", "family", "slug", "description")
    if not match:
        raise VultrError(f"无法匹配操作系统：{value}")
    os_id = match.get("id")
    if isinstance(os_id, int):
        return os_id
    try:
        return int(str(os_id))
    except ValueError as exc:  # pragma: no cover
        raise VultrError(f"无法解析 OS ID：{os_id}") from exc


def _ensure_ssh_key(env: Dict[str, str]) -> Tuple[str, str]:
    api_key = env["VULTR_API_KEY"].strip()
    public_key = env["SSH_PUBLIC_KEY"]
    key_name = env.get("INSTANCE_LABEL", "onepass") + "-ssh"
    existing = list_ssh_keys(api_key)
    for item in existing:
        remote_key = item.get("ssh_key", "").strip()
        if remote_key == public_key:
            log_ok(f"复用已存在的 SSH Key：{item.get('name')} ({item.get('id')})")
            return item.get("id"), item.get("name")
    created = create_ssh_key(key_name, public_key, api_key)
    key_info = created.get("ssh_key", created)
    log_ok(f"已上传 SSH Key：{key_info.get('name')} ({key_info.get('id')})")
    return key_info.get("id"), key_info.get("name")


def cmd_create(_: argparse.Namespace) -> int:
    section("步骤②：创建 Vultr VPS")
    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2

    api_key = env["VULTR_API_KEY"].strip()
    try:
        ssh_key_id, ssh_key_name = _ensure_ssh_key(env)
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2

    try:
        region = _resolve_region(env.get("VULTR_REGION", "sgp"), api_key)
        plan = _resolve_plan(env.get("VULTR_PLAN", "vc2-2c-4gb"), api_key)
        os_id = _resolve_os(env.get("VULTR_OS", "ubuntu-22.04"), api_key)
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2

    label = env.get("INSTANCE_LABEL", "onepass-asr")
    tag = env.get("TAG") or None
    log_info(f"即将创建实例：region={region} plan={plan} os_id={os_id} label={label}")
    log_info(f"使用 SSH Key：{ssh_key_name} ({ssh_key_id})")

    try:
        resp = create_instance(region, plan, os_id, label, [ssh_key_id], api_key, tag=tag)
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2

    instance = resp.get("instance", resp)
    instance_id = instance.get("id")
    main_ip = instance.get("main_ip") or instance.get("ip")
    if not instance_id:
        log_err("API 未返回实例 ID。")
        return 2
    log_ok(f"实例创建请求已提交：{instance_id}")

    spinner = Spinner()
    spinner.start("等待实例进入 active 状态")
    timeout_s = int(env.get("CREATE_TIMEOUT_SEC", "900") or "900")
    poll_s = int(env.get("POLL_INTERVAL_SEC", "8") or "8")
    try:
        wait_for_instance_active(instance_id, timeout_s, poll_s, api_key)
    except VultrError as exc:
        spinner.stop_err("实例未能在指定时间内就绪。")
        log_err(_format_exception(exc))
        return 2
    spinner.stop_ok("实例已就绪。")

    info = get_instance(instance_id, api_key)
    instance_info = info.get("instance", info)
    main_ip = instance_info.get("main_ip") or main_ip
    log_ok(f"实例 {instance_id} 已激活，主 IP：{main_ip}")

    state = {
        "instance_id": instance_id,
        "main_ip": main_ip,
        "ssh_key_id": ssh_key_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_state(state)
    log_ok(f"已写入状态文件：{STATE_FILE}")
    ssh_user = env.get("SSH_USER", "ubuntu")
    private_key_path = env.get("SSH_PRIVATE_KEY_RESOLVED")
    if main_ip:
        log_info(f"示例：ssh -i \"{private_key_path}\" {ssh_user}@{main_ip}")
    else:
        log_warn("未获取到实例 IP，请稍后在 Vultr 控制台确认。")
    return 0


def _call_setup_script(private_key: str, ip: str, user: str) -> int:
    pwsh_path = shutil.which("pwsh")
    script = CUR_DIR / "setup_local_access.ps1"
    if not script.exists() or not pwsh_path:
        if not pwsh_path:
            log_warn("未找到 pwsh，跳过 PowerShell 辅助脚本。")
        else:
            log_warn("未找到 setup_local_access.ps1，跳过辅助脚本。")
        return 0
    cmd = [
        pwsh_path,
        "-NoLogo",
        "-NoProfile",
        "-File",
        str(script),
        "-PrivateKey",
        private_key,
        "-InstanceIp",
        ip,
        "-User",
        user,
    ]
    log_info("调用 PowerShell 本地网络准备脚本…")
    return run_streamed(cmd, heartbeat_s=30.0, show_cmd=False)


def _test_ssh_connectivity(user: str, private_key: str, host: str) -> bool:
    if not host:
        log_warn("state.json 中缺少实例 IP，跳过连通性测试。")
        return False
    ssh_cmd_base = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
    ]
    if private_key:
        ssh_cmd_base.extend(["-i", private_key])
    target = f"{user}@{host}"
    for attempt in range(1, 6):
        log_info(f"第 {attempt} 次尝试连接 {target} …")
        cmd = [*ssh_cmd_base, target, "exit"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            log_ok("SSH 连通性验证成功。")
            return True
        log_warn(f"SSH 测试失败：{proc.stderr.strip() or proc.stdout.strip()}")
        time.sleep(5)
    log_err("连续多次尝试均未能连通实例，请检查网络与安全组。")
    return False


def cmd_prepare_local(_: argparse.Namespace) -> int:
    section("步骤③：准备本机接入 VPS 网络")
    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    state = _load_state()
    instance_ip = state.get("main_ip")
    if not instance_ip:
        log_warn("state.json 中缺少 main_ip，将继续执行但无法验证连通性。")
    user = env.get("SSH_USER", "ubuntu")
    rc = _call_setup_script(env.get("SSH_PRIVATE_KEY_RESOLVED", ""), instance_ip or "", user)
    if rc == 2:
        log_err("PowerShell 辅助脚本执行失败。")
        return 2
    if rc == 1:
        log_warn("PowerShell 辅助脚本返回警告，继续进行 SSH 测试。")
    key_path = env.get("SSH_PRIVATE_KEY_RESOLVED", "")
    if instance_ip and _test_ssh_connectivity(user, key_path, instance_ip):
        return 0
    return 2


def _format_duration(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


def cmd_list(_: argparse.Namespace) -> int:
    section("步骤④：列出 Vultr 实例")
    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    api_key = env["VULTR_API_KEY"].strip()
    try:
        items = list_instances(api_key)
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    state = _load_state()
    current_id = state.get("instance_id")
    headers = ["ID", "Label", "Region", "Plan", "Status", "Main IP", "Created"]
    rows: List[List[str]] = []
    for item in items:
        inst = item.get("instance", item)
        row = [
            inst.get("id", ""),
            inst.get("label", ""),
            inst.get("region", ""),
            inst.get("plan", ""),
            inst.get("status", ""),
            inst.get("main_ip", ""),
            _format_duration(inst.get("date_created", "")),
        ]
        if row[0] == current_id:
            row[1] = f"{row[1]} (current)"
        rows.append(row)
    if not rows:
        log_warn("账户中暂无实例。")
        return 0
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))
    line = " | ".join(h.ljust(widths[idx]) for idx, h in enumerate(headers))
    print(line)
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(str(cell).ljust(widths[idx]) for idx, cell in enumerate(row)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vultr 云端部署向导 CLI")
    sub = parser.add_subparsers(dest="command")

    env_parser = sub.add_parser("env-check", help="检查本机环境")
    env_parser.set_defaults(func=cmd_env_check)

    create_parser = sub.add_parser("create", help="创建 VPS 实例")
    create_parser.set_defaults(func=cmd_create)

    prepare_parser = sub.add_parser("prepare-local", help="准备本机接入 VPS")
    prepare_parser.set_defaults(func=cmd_prepare_local)

    list_parser = sub.add_parser("list", help="列出 Vultr 实例")
    list_parser.set_defaults(func=cmd_list)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log_warn("用户取消操作。")
        return 1
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
