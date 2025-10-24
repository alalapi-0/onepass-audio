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
    delete_instance,
    get_instance,
    list_instances,
    list_os,
    list_plans,
    list_regions,
    list_ssh_keys,
    wait_for_instance_active,
)
from onepass.ux import (  # noqa: E402
    Spinner,
    format_cmd,
    log_err,
    log_info,
    log_ok,
    log_warn,
    run_streamed,
    section,
)

ENV_FILE = CUR_DIR / "vultr.env"
STATE_FILE = CUR_DIR / "state.json"
SYNC_ENV_FILE = PROJ_ROOT / "deploy" / "sync" / "sync.env"
SYNC_DEFAULTS: Dict[str, str] = {
    "VPS_HOST": "",
    "VPS_USER": "ubuntu",
    "VPS_SSH_KEY": "",
    "VPS_REMOTE_DIR": "/home/ubuntu/onepass",
    "LOCAL_AUDIO": "onepass/data/audio",
    "REMOTE_AUDIO": "/home/ubuntu/onepass/data/audio",
    "REMOTE_ASR_JSON": "/home/ubuntu/onepass/data/asr-json",
    "REMOTE_LOG_DIR": "/home/ubuntu/onepass/out",
    "USE_RSYNC_FIRST": "true",
    "BWLIMIT_Mbps": "0",
    "CHECKSUM": "true",
    "ASR_MODEL": "medium",
    "ASR_LANGUAGE": "zh",
    "ASR_DEVICE": "auto",
    "ASR_COMPUTE": "auto",
    "ASR_WORKERS": "1",
    "AUDIO_PATTERN": "*.m4a,*.wav,*.mp3,*.flac",
}
SYNC_KEY_ORDER: List[str] = [
    "VPS_HOST",
    "VPS_USER",
    "VPS_SSH_KEY",
    "VPS_REMOTE_DIR",
    "LOCAL_AUDIO",
    "REMOTE_AUDIO",
    "REMOTE_ASR_JSON",
    "REMOTE_LOG_DIR",
    "USE_RSYNC_FIRST",
    "BWLIMIT_Mbps",
    "CHECKSUM",
    "ASR_MODEL",
    "ASR_LANGUAGE",
    "ASR_DEVICE",
    "ASR_COMPUTE",
    "ASR_WORKERS",
    "AUDIO_PATTERN",
]


def _parse_env_text(text: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _read_env_file(optional: bool = False) -> Dict[str, str]:
    if not ENV_FILE.exists():
        if optional:
            log_warn("未找到 vultr.env，将使用默认值并跳过部分检查。")
            return {}
        raise FileNotFoundError(
            "未找到 vultr.env。请复制 deploy/cloud/vultr/vultr.env.example 并填写后重试。"
        )
    return _parse_env_text(ENV_FILE.read_text(encoding="utf-8"))


def _read_custom_env(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    return _parse_env_text(path.read_text(encoding="utf-8"))


def _write_env_file(path: Path, data: Dict[str, str], order: Iterable[str]) -> None:
    ordered_keys = list(order)
    lines: List[str] = []
    seen: set[str] = set()
    for key in ordered_keys:
        if key in data:
            lines.append(f"{key}={data[key]}")
            seen.add(key)
    for key in sorted(k for k in data if k not in seen):
        lines.append(f"{key}={data[key]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    public_key = (
        private_key.with_suffix(private_key.suffix + ".pub")
        if private_key.suffix
        else Path(str(private_key) + ".pub")
    )
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
    return str(match.get("id"))


def _resolve_plan(value: str, api_key: str) -> str:
    plans = list_plans(api_key)
    match = _match_item(plans, value, "id", "name", "description", "vcpu_count", "vcpus")
    if not match:
        raise VultrError(f"无法匹配 plan：{value}")
    return str(match.get("id"))


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
            return str(item.get("id")), str(item.get("name"))
    created = create_ssh_key(key_name, public_key, api_key)
    key_info = created.get("ssh_key", created)
    log_ok(f"已上传 SSH Key：{key_info.get('name')} ({key_info.get('id')})")
    return str(key_info.get("id")), str(key_info.get("name"))


def cmd_env_check(args: argparse.Namespace) -> int:
    section("步骤①：检查本机环境")
    if args.dry_run:
        log_warn("dry-run 对 env-check 无实际意义，将继续执行只读检查。")
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


def cmd_create(args: argparse.Namespace) -> int:
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

    if args.dry_run:
        log_info("[DryRun] 跳过实例创建，仅展示参数。")
        return 0

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


def _call_setup_script(private_key: str, ip: str, user: str, dry_run: bool) -> int:
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
    if dry_run:
        log_info(f"[DryRun] {format_cmd(cmd)}")
        return 0
    return run_streamed(cmd, heartbeat_s=30.0, show_cmd=False)


def _test_ssh_connectivity(user: str, private_key: str, host: str, dry_run: bool) -> bool:
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
    if dry_run:
        log_info(f"[DryRun] 将测试 SSH 连通性：{format_cmd([*ssh_cmd_base, target, 'exit'])}")
        return True
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


def cmd_prepare_local(args: argparse.Namespace) -> int:
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
    rc = _call_setup_script(env.get("SSH_PRIVATE_KEY_RESOLVED", ""), instance_ip or "", user, args.dry_run)
    if rc == 2:
        log_err("PowerShell 辅助脚本执行失败。")
        return 2
    if rc == 1:
        log_warn("PowerShell 辅助脚本返回警告，继续进行 SSH 测试。")
    key_path = env.get("SSH_PRIVATE_KEY_RESOLVED", "")
    if instance_ip and _test_ssh_connectivity(user, key_path, instance_ip, args.dry_run):
        return 0
    return 2 if not args.dry_run else 0


def _format_duration(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


def cmd_list(args: argparse.Namespace) -> int:
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
            str(inst.get("id", "")),
            str(inst.get("label", "")),
            str(inst.get("region", "")),
            str(inst.get("plan", "")),
            str(inst.get("status", "")),
            str(inst.get("main_ip", "")),
            _format_duration(str(inst.get("date_created", ""))),
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


def _ensure_state_for_sync() -> Tuple[Dict[str, str], Dict[str, str]]:
    state = _load_state()
    if not state.get("main_ip"):
        raise RuntimeError("state.json 缺少 main_ip，请先创建实例。")
    env = _ensure_env_and_key()
    return state, env


def cmd_write_sync_env(args: argparse.Namespace) -> int:
    section("写入 deploy/sync/sync.env")
    try:
        state, env = _ensure_state_for_sync()
    except (RuntimeError, VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    existing = _read_custom_env(SYNC_ENV_FILE)
    new_data = dict(existing or SYNC_DEFAULTS)
    for key, value in SYNC_DEFAULTS.items():
        new_data.setdefault(key, value)
    new_data.update(
        {
            "VPS_HOST": state.get("main_ip", ""),
            "VPS_USER": env.get("SSH_USER", "ubuntu"),
            "VPS_SSH_KEY": env.get("SSH_PRIVATE_KEY", ""),
            "VPS_REMOTE_DIR": env.get("REMOTE_DIR", SYNC_DEFAULTS["VPS_REMOTE_DIR"]),
        }
    )
    main_remote_dir = new_data["VPS_REMOTE_DIR"].rstrip("/") or SYNC_DEFAULTS["VPS_REMOTE_DIR"]
    new_data["VPS_REMOTE_DIR"] = main_remote_dir
    new_data["REMOTE_AUDIO"] = f"{main_remote_dir}/data/audio"
    new_data["REMOTE_ASR_JSON"] = f"{main_remote_dir}/data/asr-json"
    new_data["REMOTE_LOG_DIR"] = f"{main_remote_dir}/out"

    changes: List[str] = []
    for key in ["VPS_HOST", "VPS_USER", "VPS_SSH_KEY", "VPS_REMOTE_DIR"]:
        old = existing.get(key) if existing else None
        new = new_data.get(key, "")
        if old != new:
            before = old if old else "<未设置>"
            changes.append(f"{key}: {before} -> {new}")
    if changes:
        log_info("将更新以下连接字段：")
        for item in changes:
            log_info(f" - {item}")
    else:
        log_info("连接字段无需更新。")
    if args.dry_run:
        log_warn("dry-run 模式：未写入 sync.env。")
        return 0
    _write_env_file(SYNC_ENV_FILE, new_data, SYNC_KEY_ORDER)
    log_ok(f"已写入 {SYNC_ENV_FILE.relative_to(PROJ_ROOT)}")
    return 0


def _run_step(name: str, cmd: List[str], dry_run: bool) -> int:
    log_info(f"开始：{name}")
    log_info(f"命令：{format_cmd(cmd)}")
    if dry_run:
        log_info("[DryRun] 已跳过执行。")
        return 0
    start = time.perf_counter()
    rc = run_streamed(cmd, cwd=PROJ_ROOT)
    elapsed = time.perf_counter() - start
    if rc == 0:
        log_ok(f"完成：{name}（耗时 {elapsed:.1f}s，返回码 0）")
    elif rc == 1:
        log_warn(f"警告：{name} 返回 1（耗时 {elapsed:.1f}s）")
    else:
        log_err(f"失败：{name}（返回码 {rc}，耗时 {elapsed:.1f}s）")
    return rc


def cmd_asr_bridge(args: argparse.Namespace) -> int:
    section("一键桥接：上传 → 远端 ASR → 回收 → 校验")
    script = PROJ_ROOT / "scripts" / "deploy_cli.py"
    if not script.exists():
        log_err("未找到 scripts/deploy_cli.py")
        return 2
    verify_script = PROJ_ROOT / "scripts" / "verify_asr_words.py"
    steps: List[Tuple[str, List[str]]] = []
    steps.append(("切换 provider 为 sync", [sys.executable, str(script), "provider", "--set", "sync"]))

    upload_cmd = [sys.executable, str(script), "upload_audio"]
    if args.no_delete:
        upload_cmd.append("--no-delete")
    if args.dry_run:
        upload_cmd.append("--dry-run")
    steps.append(("上传音频", upload_cmd))

    run_cmd = [sys.executable, str(script), "run_asr"]
    if args.pattern:
        run_cmd.extend(["--pattern", args.pattern])
    if args.model:
        run_cmd.extend(["--model", args.model])
    if args.workers is not None:
        run_cmd.extend(["--workers", str(args.workers)])
    if args.overwrite:
        run_cmd.append("--overwrite")
    if args.dry_run:
        run_cmd.append("--dry-run")
    steps.append(("远端执行 ASR", run_cmd))

    fetch_cmd = [sys.executable, str(script), "fetch_outputs"]
    if args.dry_run:
        fetch_cmd.append("--dry-run")
    steps.append(("回收转写结果", fetch_cmd))

    if verify_script.exists():
        verify_cmd = [sys.executable, str(verify_script)]
        steps.append(("校验 JSON words 字段", verify_cmd))
    else:
        log_warn("未找到 verify_asr_words.py，跳过校验步骤。")

    overall_rc = 0
    for name, cmd in steps:
        rc = _run_step(name, cmd, dry_run=args.dry_run)
        if rc >= 2:
            return 2
        if rc == 1 and overall_rc == 0:
            overall_rc = 1
        if rc != 0 and name == "校验 JSON words 字段":
            overall_rc = max(overall_rc, rc)
    if overall_rc == 0:
        log_ok("云端⇄本地互通桥完成。")
    elif overall_rc == 1:
        log_warn("流程完成但存在警告，请查看日志。")
    return overall_rc


def _prompt_confirm(instance_id: str, dry_run: bool) -> bool:
    suffix = instance_id[-4:]
    if dry_run:
        log_info(f"[DryRun] 跳过确认，假定输入：{suffix}")
        return True
    prompt = input(f"请输入实例 ID ({instance_id}) 的后四位以确认删除：").strip()
    if prompt == suffix:
        return True
    log_err("输入不匹配，已取消删除。")
    return False


def _wait_instance_destroyed(instance_id: str, api_key: str) -> int:
    log_info("轮询实例状态，等待 destroyed …")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(6)
        try:
            info = get_instance(instance_id, api_key)
        except VultrError as exc:
            message = str(exc)
            if "404" in message:
                log_ok("实例已从控制台移除。")
                return 0
            log_warn(f"查询实例状态失败：{message}")
            continue
        status = info.get("instance", {}).get("status")
        log_info(f"当前状态：{status}")
        if status == "destroyed":
            log_ok("Vultr 报告实例已销毁。")
            return 0
    log_err("轮询超时，实例仍未销毁。")
    return 2


def cmd_delete(args: argparse.Namespace) -> int:
    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    instance_id = args.id
    if not instance_id:
        log_err("请通过 --id 指定实例 ID。")
        return 2
    api_key = env["VULTR_API_KEY"].strip()
    log_warn("删除实例会立即停止计费，过程不可恢复。")
    if not _prompt_confirm(instance_id, args.dry_run):
        return 1
    if args.dry_run:
        log_info(f"[DryRun] 将删除实例 {instance_id}。")
        return 0
    try:
        delete_instance(instance_id, api_key)
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    log_info("删除请求已发起。")
    rc = _wait_instance_destroyed(instance_id, api_key)
    if rc == 0:
        log_warn("已删除实例，记得在 Vultr 控制台确认账单。")
    return rc


def cmd_delete_current(args: argparse.Namespace) -> int:
    state = _load_state()
    instance_id = state.get("instance_id")
    if not instance_id:
        log_err("state.json 中缺少当前实例 ID。")
        return 2
    rc = cmd_delete(argparse.Namespace(id=instance_id, dry_run=args.dry_run))
    if rc == 0 and not args.dry_run:
        try:
            STATE_FILE.write_text("{}\n", encoding="utf-8")
        except OSError as exc:
            log_warn(f"清理 state.json 失败：{exc}")
        log_warn("已删除当前实例，记得切换 provider 或更新 sync.env。")
    return rc


def _filter_items(items: List[dict], text: Optional[str]) -> List[dict]:
    if not text:
        return items
    keyword = text.lower()
    result: List[dict] = []
    for item in items:
        joined = " ".join(str(v) for v in item.values())
        if keyword in joined.lower():
            result.append(item)
    return result


def _print_table(headers: List[str], rows: List[List[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))
    header_line = " | ".join(headers[idx].ljust(widths[idx]) for idx in range(len(headers)))
    print(header_line)
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(str(row[idx]).ljust(widths[idx]) for idx in range(len(headers))))


def cmd_regions(args: argparse.Namespace) -> int:
    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    try:
        items = list_regions(env["VULTR_API_KEY"].strip())
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    filtered = _filter_items(items, args.filter)
    if not filtered:
        log_warn("未找到匹配的 region。")
        return 1
    headers = ["ID", "City", "Country", "Continent", "State"]
    rows = [
        [
            str(item.get("id", "")),
            str(item.get("city", "")),
            str(item.get("country", "")),
            str(item.get("continent", "")),
            str(item.get("status", "")),
        ]
        for item in filtered
    ]
    _print_table(headers, rows)
    return 0


def cmd_plans(args: argparse.Namespace) -> int:
    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    try:
        items = list_plans(env["VULTR_API_KEY"].strip())
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    filtered = _filter_items(items, args.filter)
    if not filtered:
        log_warn("未找到匹配的 plan。")
        return 1
    headers = ["ID", "Description", "vCPUs", "RAM", "Disk", "Bandwidth"]
    rows = []
    for item in filtered:
        row = [
            str(item.get("id", "")),
            str(item.get("description") or item.get("name", "")),
            str(item.get("vcpu_count") or item.get("vcpus", "")),
            str(item.get("ram") or item.get("memory", "")),
            str(item.get("disk", "")),
            str(item.get("bandwidth", "")),
        ]
        rows.append(row)
    _print_table(headers, rows)
    return 0


def cmd_os(args: argparse.Namespace) -> int:
    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    try:
        items = list_os(env["VULTR_API_KEY"].strip())
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    filtered = _filter_items(items, args.filter)
    if not filtered:
        log_warn("未找到匹配的操作系统模板。")
        return 1
    headers = ["ID", "Name", "Family", "Arch"]
    rows = [
        [
            str(item.get("id", "")),
            str(item.get("name", "")),
            str(item.get("family", "")),
            str(item.get("arch", "")),
        ]
        for item in filtered
    ]
    _print_table(headers, rows)
    return 0


def _resolve_ssh_target() -> Tuple[str, Path, str]:
    state, env = _ensure_state_for_sync()
    host = state.get("main_ip")
    if not host:
        raise RuntimeError("state.json 缺少 main_ip")
    user = env.get("SSH_USER", "ubuntu")
    key_raw = env.get("SSH_PRIVATE_KEY_RESOLVED") or env.get("SSH_PRIVATE_KEY", "")
    key_path = _expand_path(key_raw)
    return host, key_path, user


def cmd_ssh(args: argparse.Namespace) -> int:
    try:
        host, key_path, user = _resolve_ssh_target()
    except (RuntimeError, VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    if not key_path.exists():
        log_err(f"SSH 私钥不存在：{key_path}")
        return 2
    cmd = ["ssh", "-i", str(key_path), f"{user}@{host}"]
    log_info(f"命令：{format_cmd(cmd)}")
    if args.dry_run:
        log_info("[DryRun] 未实际连接。")
        return 0
    return subprocess.call(cmd)


def cmd_tail_log(args: argparse.Namespace) -> int:
    try:
        state, env = _ensure_state_for_sync()
    except (RuntimeError, VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    host = state.get("main_ip")
    remote_dir = env.get("REMOTE_DIR", SYNC_DEFAULTS["VPS_REMOTE_DIR"]).rstrip("/")
    key_path = _expand_path(env.get("SSH_PRIVATE_KEY", ""))
    if not host or not key_path.exists():
        log_err("缺少连接信息，无法 tail 日志。")
        return 2
    log_path = f"{remote_dir}/out/asr_job.log"
    cmd = [
        "ssh",
        "-i",
        str(key_path),
        f"{env.get('SSH_USER', 'ubuntu')}@{host}",
        f"tail -f {log_path}",
    ]
    log_info(f"命令：{format_cmd(cmd)}")
    if args.dry_run:
        log_info("[DryRun] 未实际连接。")
        return 0
    return run_streamed(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vultr 云端部署向导 CLI")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    def _add_dry_run(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dry-run", action="store_true", help="仅展示将执行的操作")

    env_parser = sub.add_parser("env-check", help="检查本机环境")
    _add_dry_run(env_parser)
    env_parser.set_defaults(func=cmd_env_check)

    create_parser = sub.add_parser("create", help="创建 VPS 实例")
    _add_dry_run(create_parser)
    create_parser.set_defaults(func=cmd_create)

    prepare_parser = sub.add_parser("prepare-local", help="准备本机接入 VPS")
    _add_dry_run(prepare_parser)
    prepare_parser.set_defaults(func=cmd_prepare_local)

    list_parser = sub.add_parser("list", help="列出 Vultr 实例")
    _add_dry_run(list_parser)
    list_parser.set_defaults(func=cmd_list)

    sync_parser = sub.add_parser("write-sync-env", help="生成/更新 deploy/sync/sync.env")
    _add_dry_run(sync_parser)
    sync_parser.set_defaults(func=cmd_write_sync_env)

    bridge_parser = sub.add_parser(
        "asr-bridge",
        help="上传音频 → 远端 ASR → 回收 JSON → 校验",
    )
    bridge_parser.add_argument("--workers", type=int, help="远端并发 workers 数", default=None)
    bridge_parser.add_argument("--model", help="Whisper 模型名称", default=None)
    bridge_parser.add_argument("--pattern", help="音频匹配模式 (CSV)", default=None)
    bridge_parser.add_argument("--overwrite", action="store_true", help="覆盖已存在 JSON")
    bridge_parser.add_argument("--no-delete", action="store_true", help="上传时保留远端多余文件")
    _add_dry_run(bridge_parser)
    bridge_parser.set_defaults(func=cmd_asr_bridge)

    delete_parser = sub.add_parser("delete", help="删除指定实例")
    delete_parser.add_argument("--id", required=True, help="实例 ID")
    _add_dry_run(delete_parser)
    delete_parser.set_defaults(func=cmd_delete)

    del_current = sub.add_parser("delete-current", help="删除 state.json 中记录的实例")
    _add_dry_run(del_current)
    del_current.set_defaults(func=cmd_delete_current)

    regions_parser = sub.add_parser("regions", help="列出可用 Region")
    regions_parser.add_argument("--filter", help="过滤关键词", default=None)
    _add_dry_run(regions_parser)
    regions_parser.set_defaults(func=cmd_regions)

    plans_parser = sub.add_parser("plans", help="列出可选 Plan")
    plans_parser.add_argument("--filter", help="过滤关键词", default=None)
    _add_dry_run(plans_parser)
    plans_parser.set_defaults(func=cmd_plans)

    os_parser = sub.add_parser("os", help="列出操作系统模板")
    os_parser.add_argument("--filter", help="过滤关键词", default=None)
    _add_dry_run(os_parser)
    os_parser.set_defaults(func=cmd_os)

    ssh_parser = sub.add_parser("ssh", help="快捷 SSH 登录当前实例")
    _add_dry_run(ssh_parser)
    ssh_parser.set_defaults(func=cmd_ssh)

    tail_parser = sub.add_parser("tail-log", help="实时查看远端 ASR 日志")
    _add_dry_run(tail_parser)
    tail_parser.set_defaults(func=cmd_tail_log)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log_warn("用户取消操作。")
        return 1
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    except FileNotFoundError as exc:
        log_err(str(exc))
        return 2
    except RuntimeError as exc:
        log_err(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
