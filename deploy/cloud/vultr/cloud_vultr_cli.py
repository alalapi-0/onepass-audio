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
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import textwrap
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CUR_DIR = Path(__file__).resolve().parent
PROJ_ROOT = CUR_DIR.parents[2]
if str(PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJ_ROOT))

from deploy.cloud.vultr import vultr_api  # noqa: E402
from deploy.cloud.vultr.vultr_api import (  # noqa: E402
    VultrError,
    create_instance,
    create_ssh_key,
    delete_instance,
    get_instance,
    get_account_info,
    extract_region_availability,
    list_gpu_plans,
    list_instances,
    list_os,
    list_plans,
    list_regions,
    list_ssh_keys,
    resolve_os_id,
    wait_for_instance_active,
)
from onepass import ux  # noqa: E402
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
ACTIVE_PROFILE_PATH = PROJ_ROOT / "deploy" / "profiles" / ".env.active"

ENV_RUNTIME_PREFIXES: Tuple[str, ...] = (
    "VULTR_",
    "SSH_",
    "REMOTE_",
    "INSTANCE_",
    "CREATE_",
    "POLL_",
    "VPS_",
    "ASR_",
    "USE_",
    "BWLIMIT_",
    "CHECKSUM_",
    "AUDIO_",
)
ENV_RUNTIME_EXPLICIT_KEYS: Tuple[str, ...] = ("TAG",)
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


# ==== BEGIN: OnePass Patch · R4.4 (state helpers) ====
_STATE_PATH = os.path.join("deploy", "cloud", "vultr", "state.json")


def _now_iso() -> str:
    """UTC ISO8601（无小数秒），示例：2025-10-25T12:00:00Z"""

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _state_load() -> Dict[str, Any]:
    """读 state.json；不存在或损坏时返回 {}。不抛异常。"""

    try:
        if not os.path.exists(_STATE_PATH):
            return {}
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _state_save(obj: Dict[str, Any]) -> None:
    """原子写 state.json（先写临时文件，再 os.replace）；异常向上抛。"""

    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix="state.", suffix=".json", dir=os.path.dirname(_STATE_PATH)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _STATE_PATH)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _state_get_last_used() -> Dict[str, Any]:
    """返回 state['last_used']，若无返回 {}。字段：region/os/plan_id/profile/ts"""

    st = _state_load()
    last = st.get("last_used") or {}
    allow = {k: last.get(k) for k in ("region", "os", "plan_id", "profile", "ts")}
    return {k: v for k, v in allow.items() if v}


def _state_update_last_used(**kwargs: Any) -> None:
    """
    更新 last_used 的若干字段；仅写入非空值。自动写入 ts。
    用法：_state_update_last_used(region="nrt", os="ubuntu-22.04", plan_id="vcg-...", profile="prod_24g")
    """

    st = _state_load()
    last = st.get("last_used") or {}
    for k in ("region", "os", "plan_id", "profile"):
        v = kwargs.get(k, None)
        if v not in (None, ""):
            last[k] = v
    last["ts"] = _now_iso()
    st["last_used"] = last
    _state_save(st)


# ==== END: OnePass Patch · R4.4 (state helpers) ====


def _parse_env_text(text: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _collect_runtime_env() -> Dict[str, str]:
    runtime_env: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key in ENV_RUNTIME_EXPLICIT_KEYS:
            runtime_env[key] = value
            continue
        if any(key.startswith(prefix) for prefix in ENV_RUNTIME_PREFIXES):
            runtime_env[key] = value
    return runtime_env


def _read_env_file(optional: bool = False) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if ENV_FILE.exists():
        data.update(_parse_env_text(ENV_FILE.read_text(encoding="utf-8")))
    runtime_env = _collect_runtime_env()
    if runtime_env:
        data.update(runtime_env)
    if not data and not optional:
        raise FileNotFoundError(
            "未找到 vultr.env，且系统环境变量中也未检测到 Vultr 配置。"
            "请设置相关环境变量或复制 deploy/cloud/vultr/vultr.env.example 并填写后重试。"
        )
    return data


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


# ==== BEGIN: OnePass Patch · R2 (quickstart) ====
class _QuickstartAbort(RuntimeError):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def _qs_step_start(title: str) -> float:
    log_info(f"[进行中] {title}")
    return time.perf_counter()


def _qs_step_ok(summary: List[Tuple[str, str]], title: str, start: float, status: str = "OK") -> None:
    elapsed = time.perf_counter() - start
    summary.append((title, status))
    if status == "Skip":
        log_info(f"[跳过] {title}（耗时 {elapsed:.1f}s）")
    else:
        log_ok(f"[成功] {title}（耗时 {elapsed:.1f}s）")


def _qs_step_fail(summary: List[Tuple[str, str]], title: str, start: float, message: str, code: int) -> int:
    elapsed = time.perf_counter() - start
    summary.append((title, "Fail"))
    log_err(f"[错误] {title}：{message}（耗时 {elapsed:.1f}s）")
    return code


def _qs_run_command(
    ctx: Dict[str, object],
    cmd: List[str],
    *,
    capture: bool = False,
    extra_env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str]:
    verbose = bool(ctx.get("verbose"))
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update({k: str(v) for k, v in extra_env.items() if v is not None})
    if capture:
        result = subprocess.run(
            cmd,
            cwd=PROJ_ROOT,
            text=True,
            capture_output=True,
            env=env,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return result.returncode, result.stdout
    rc = run_streamed(cmd, cwd=PROJ_ROOT, env=env, show_cmd=verbose)
    return rc, ""


def _qs_format_regions(region: str, plan: dict) -> str:
    target = region.lower()
    available = plan.get("available_regions")
    if isinstance(available, dict):
        availability: Dict[str, bool] = {}
        for key, value in available.items():
            availability[str(key)] = bool(value)
        return _format_regions(availability, region, False)
    if isinstance(available, list):
        entries: List[str] = []
        for item in available:
            code = str(item)
            if code.lower() == target:
                entries.append(f"[{code}]")
            else:
                entries.append(code)
        return ", ".join(entries) if entries else "-"
    text = str(available or "").strip()
    if not text:
        return "-"
    if target and target in text.lower():
        return text.replace(region, f"[{region}]")
    return text


def _qs_collect_plans(
    ctx: Dict[str, object],
    api_key: str,
    region: str,
    os_slug: str,
    family_pattern: Optional[re.Pattern[str]],
    min_vram: Optional[int],
) -> List[dict]:
    try:
        plans = list_gpu_plans(region=region or None, os_slug=os_slug, api_key=api_key)
    except VultrError as exc:  # pragma: no cover - network path
        raise _QuickstartAbort(_format_exception(exc), 2) from exc
    if ctx.get("verbose"):
        log_info(f"[详细] 套餐原始条目：{len(plans)}")
    region_lower = region.lower()
    entries: List[dict] = []
    for plan in plans:
        plan_id = str(plan.get("id") or plan.get("plan_id") or plan.get("product_id") or plan.get("slug") or "").strip()
        if not plan_id:
            continue
        available = plan.get("available_regions") or []
        if region and available:
            normalized = {str(item).lower() for item in available} if not isinstance(available, dict) else {str(k).lower() for k, v in available.items() if v}
            if normalized and region_lower not in normalized:
                continue
        family = str(plan.get("family") or _extract_gpu_family(plan) or "-")
        if family_pattern and not family_pattern.search(family):
            continue
        gpu_vram = _extract_gpu_vram_gb(plan)
        if min_vram is not None and (gpu_vram is None or gpu_vram < min_vram):
            continue
        vcpu = _extract_vcpu(plan)
        ram_gb = _extract_ram_gb(plan)
        price_hour = _extract_price_per_hour(plan)
        regions_text = _qs_format_regions(region, plan)
        entries.append(
            {
                "plan_id": plan_id,
                "family": family,
                "gpu_vram": gpu_vram,
                "vcpu": vcpu,
                "ram_gb": ram_gb,
                "price": price_hour,
                "regions": regions_text,
                "raw": plan,
            }
        )
    entries.sort(key=lambda item: (float("inf") if item["price"] is None else item["price"], item["plan_id"]))
    return entries


def _qs_display_plans(entries: List[dict]) -> None:
    headers = ["#", "plan_id", "family", "GPU(GB)", "vCPU", "RAM(GB)", "Price(/h)", "Regions"]
    rows: List[List[str]] = []
    for idx, entry in enumerate(entries, 1):
        gpu = _format_number(entry["gpu_vram"])
        vcpu = _format_number(entry["vcpu"])
        ram = _format_number(entry["ram_gb"])
        price = _format_price(entry["price"])
        rows.append([
            str(idx),
            entry["plan_id"],
            entry["family"],
            gpu,
            vcpu,
            ram,
            price,
            entry["regions"],
        ])
    _print_table(headers, rows)


def _qs_select_plan(
    ctx: Dict[str, object],
    entries: List[dict],
) -> Tuple[str, dict]:
    if not entries:
        raise _QuickstartAbort("未找到符合条件的计划，可尝试更换 --region 或放宽过滤条件。", 1)
    _qs_display_plans(entries)
    auto_yes = bool(ctx.get("auto_yes"))
    if auto_yes:
        selection = 1
    else:
        while True:
            choice = input("请选择计划序号 [默认 1]: ").strip()
            if not choice:
                selection = 1
                break
            if choice.isdigit():
                selection = int(choice)
                if 1 <= selection <= len(entries):
                    break
            log_warn("输入无效，请输入列表中的序号。")
    chosen = entries[selection - 1]
    log_ok(f"已选择 plan：{chosen['plan_id']} ({chosen['family']} · GPU { _format_number(chosen['gpu_vram']) }GB)")
    return chosen["plan_id"], chosen


def cmd_quickstart(args: argparse.Namespace) -> int:
    ctx: Dict[str, object] = {
        "verbose": bool(args.verbose and not args.quiet),
        "quiet": bool(args.quiet),
        "auto_yes": bool(args.yes),
    }
    summary: List[Tuple[str, str]] = []
    overall_start = time.perf_counter()

    step = _qs_step_start("解析 Vultr 配置")
    base_env = _read_env_file(optional=True)
    api_key = (base_env.get("VULTR_API_KEY") or "").strip()
    if not api_key:
        return _qs_step_fail(summary, "解析 Vultr 配置", step, "请在 deploy/cloud/vultr/vultr.env 中填写 VULTR_API_KEY。", 2)

    # ==== BEGIN: OnePass Patch · R4.4 (quickstart use-last) ====
    last = _state_get_last_used() if getattr(args, "use_last", False) else {}
    region = (
        (args.region or "")
        or str(last.get("region") or "")
        or base_env.get("VULTR_REGION")
        or "nrt"
    )
    region = region.strip() or "nrt"
    os_slug = (
        (args.os or "")
        or str(last.get("os") or "")
        or base_env.get("VULTR_OS")
        or "ubuntu-22.04"
    )
    os_slug = os_slug.strip() or "ubuntu-22.04"
    label = (args.label or base_env.get("INSTANCE_LABEL") or "onepass-asr").strip() or "onepass-asr"
    plan_override = (args.plan or str(last.get("plan_id") or "")).strip()
    profile_override = args.profile if args.profile is not None else last.get("profile")
    profile_value = (
        str(profile_override).strip() if profile_override not in (None, "") else ""
    )
    args.region = region
    args.os = os_slug
    args.plan = plan_override or None
    args.profile = profile_value or None
    try:
        min_vram = int(args.min_vram) if args.min_vram is not None else None
    except (TypeError, ValueError):
        return _qs_step_fail(summary, "解析 Vultr 配置", step, "--min-vram 需为整数。", 2)
    family_pattern: Optional[re.Pattern[str]] = None
    if args.family:
        try:
            family_pattern = re.compile(args.family, re.IGNORECASE)
        except re.error as exc:
            return _qs_step_fail(summary, "解析 Vultr 配置", step, f"无效的 --family 正则：{exc}", 2)
    try:
        env = _ensure_env_with_api_key(require_ssh=True)
    except (VultrError, FileNotFoundError) as exc:  # pragma: no cover - runtime path
        return _qs_step_fail(summary, "解析 Vultr 配置", step, _format_exception(exc), 2)

    env.update(
        {
            "INSTANCE_LABEL": label,
            "VULTR_REGION": region,
            "VULTR_OS": os_slug,
        }
    )
    ctx["env"] = env
    ctx["api_key"] = api_key
    ctx["region"] = region
    ctx["os_slug"] = os_slug
    ctx["label"] = label
    ctx["profile"] = profile_value
    ctx["pattern"] = args.pattern or None
    ctx["stems"] = args.stems or None
    ctx["model"] = args.model or None
    ctx["workers"] = args.workers
    ctx["overwrite"] = bool(args.overwrite)
    ctx["no_watch"] = bool(args.no_watch)
    ctx["min_vram"] = min_vram
    ctx["family_pattern"] = family_pattern
    ctx["plan_info"] = {}
    ctx["plan_id"] = (plan_override or "").strip()
    ctx["summary"] = summary
    _qs_step_ok(summary, "解析 Vultr 配置", step)

    if not plan_override:
        step = _qs_step_start("筛选 GPU 套餐")
        try:
            plans = _qs_collect_plans(ctx, api_key, region, os_slug, family_pattern, min_vram)
            plan_id, plan_entry = _qs_select_plan(ctx, plans)
        except _QuickstartAbort as exc:
            return _qs_step_fail(summary, "筛选 GPU 套餐", step, str(exc), exc.code)
        ctx["plan_id"] = plan_id
        ctx["plan_info"] = plan_entry
        _qs_step_ok(summary, "筛选 GPU 套餐", step)
    else:
        ctx["plan_id"] = (plan_override or "").strip()
        ctx["plan_info"] = {"plan_id": ctx["plan_id"], "family": "-", "gpu_vram": None}
    # ==== END: OnePass Patch · R4.4 (quickstart use-last) ====

    step = _qs_step_start("最终确认")
    plan_info = ctx.get("plan_info", {})
    plan_label = plan_info.get("plan_id", ctx.get("plan_id", ""))
    family_text = plan_info.get("family", "-")
    gpu_text = _format_number(plan_info.get("gpu_vram")) if plan_info else "-"
    summary_line = f"Plan: {plan_label} ({family_text} · GPU {gpu_text}GB) · Region: {region} · OS: {os_slug} · Label: {label}"
    log_info(summary_line)
    if not ctx.get("auto_yes"):
        answer = input("确认创建？ [Y/n]: ").strip().lower()
        if answer in {"n", "no"}:
            log_warn("用户取消了创建流程。")
            return _qs_step_fail(summary, "最终确认", step, "用户取消", 1)
    _qs_step_ok(summary, "最终确认", step)

    step = _qs_step_start("创建 Vultr 实例")
    env = ctx["env"]
    env["VULTR_PLAN"] = str(ctx["plan_id"])
    try:
        get_account_info(api_key)
    except VultrError as exc:  # pragma: no cover - network path
        return _qs_step_fail(summary, "创建 Vultr 实例", step, _format_exception(exc), 2)
    try:
        ssh_key_id, ssh_key_name = _ensure_ssh_key(env)
    except VultrError as exc:
        return _qs_step_fail(summary, "创建 Vultr 实例", step, _format_exception(exc), 2)
    try:
        region_id = _resolve_region(region, api_key)
        os_id = _resolve_os(os_slug, api_key)
    except VultrError as exc:
        return _qs_step_fail(summary, "创建 Vultr 实例", step, _format_exception(exc), 2)
    tag = env.get("TAG") or None
    try:
        resp = create_instance(region_id, ctx["plan_id"], os_id, label, [ssh_key_id], api_key, tag=tag)
    except VultrError as exc:
        return _qs_step_fail(summary, "创建 Vultr 实例", step, _format_exception(exc), 2)
    instance = resp.get("instance", resp)
    instance_id = str(instance.get("id", "")).strip()
    main_ip = str(instance.get("main_ip") or instance.get("ip") or "").strip()
    if not instance_id:
        return _qs_step_fail(summary, "创建 Vultr 实例", step, "API 未返回实例 ID。", 2)
    spinner = Spinner()
    spinner.start("等待实例进入 active 状态")
    try:
        wait_for_instance_active(instance_id, int(env.get("CREATE_TIMEOUT_SEC", "900") or "900"), int(env.get("POLL_INTERVAL_SEC", "8") or "8"), api_key)
    except VultrError as exc:  # pragma: no cover - network path
        spinner.stop_err("实例未在限定时间内就绪。")
        return _qs_step_fail(summary, "创建 Vultr 实例", step, _format_exception(exc), 2)
    spinner.stop_ok("实例已就绪")
    info = get_instance(instance_id, api_key)
    inst_info = info.get("instance", info)
    main_ip = str(inst_info.get("main_ip") or main_ip or "").strip()
    state = {
        "instance_id": instance_id,
        "main_ip": main_ip,
        "ssh_key_id": ssh_key_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "region": region,
        "plan": ctx["plan_id"],
    }
    _write_state(state)
    ctx["instance"] = state
    log_ok(f"实例创建完成：{instance_id} · IP {main_ip or '-'} · SSH Key {ssh_key_name}")
    _qs_step_ok(summary, "创建 Vultr 实例", step)

    step = _qs_step_start("写入 sync.env 连接配置")
    try:
        existing = _read_custom_env(SYNC_ENV_FILE)
    except OSError as exc:
        return _qs_step_fail(summary, "写入 sync.env 连接配置", step, f"读取 sync.env 失败：{exc}", 2)
    new_data = dict(existing or SYNC_DEFAULTS)
    for key, value in SYNC_DEFAULTS.items():
        new_data.setdefault(key, value)
    active_profile = _load_active_profile_env()
    default_remote_dir = active_profile.get("REMOTE_DIR", SYNC_DEFAULTS["VPS_REMOTE_DIR"])
    remote_dir = env.get("REMOTE_DIR") or env.get("VPS_REMOTE_DIR") or default_remote_dir
    remote_dir = remote_dir.rstrip("/") or SYNC_DEFAULTS["VPS_REMOTE_DIR"]
    new_data.update(
        {
            "VPS_HOST": state.get("main_ip", ""),
            "VPS_USER": env.get("SSH_USER", "ubuntu"),
            "VPS_SSH_KEY": env.get("SSH_PRIVATE_KEY", ""),
            "VPS_REMOTE_DIR": remote_dir,
            "REMOTE_AUDIO": f"{remote_dir}/data/audio",
            "REMOTE_ASR_JSON": f"{remote_dir}/data/asr-json",
            "REMOTE_LOG_DIR": f"{remote_dir}/out",
        }
    )
    diff_lines: List[str] = []
    for key in ["VPS_HOST", "VPS_USER", "VPS_SSH_KEY", "VPS_REMOTE_DIR"]:
        before = existing.get(key) if existing else None
        after = new_data.get(key, "")
        if before != after:
            diff_lines.append(f"{key}: {before or '<未设置>'} -> {after}")
    try:
        _write_env_file(SYNC_ENV_FILE, new_data, SYNC_KEY_ORDER)
    except OSError as exc:
        return _qs_step_fail(summary, "写入 sync.env 连接配置", step, f"写入失败：{exc}", 2)
    if diff_lines:
        log_info("sync.env 更新字段：")
        for line in diff_lines:
            log_info(f"  - {line}")
    else:
        log_info("连接字段无需更新。")
    _qs_step_ok(summary, "写入 sync.env 连接配置", step)

    step = _qs_step_start("应用运行配置 Profile")
    envsnap_script = PROJ_ROOT / "scripts" / "envsnap.py"
    selected_profile = ctx.get("profile") or ""
    if not envsnap_script.exists():
        log_warn("[建议] 未找到 scripts/envsnap.py，可稍后手动运行 envsnap apply。")
        _qs_step_ok(summary, "应用运行配置 Profile", step, status="Skip")
    else:
        if selected_profile:
            log_info(f"将应用指定 profile：{selected_profile}")
        else:
            profiles_dir = PROJ_ROOT / "deploy" / "profiles"
            default_profile = "prod_24g" if (profiles_dir / "prod_24g.env").exists() else None
            test_profile = "test_subset" if (profiles_dir / "test_subset.env").exists() else None
            if default_profile or test_profile:
                if ctx.get("auto_yes") and default_profile:
                    selected_profile = default_profile
                    log_info(f"已默认选择 profile：{selected_profile}")
                else:
                    selected_profile = _select_profile(default_profile)
                    if not selected_profile and test_profile and ctx.get("auto_yes"):
                        selected_profile = test_profile
            if not selected_profile:
                log_warn("[建议] 未选择 profile，可稍后运行 envsnap.py apply。")
                _qs_step_ok(summary, "应用运行配置 Profile", step, status="Skip")
                selected_profile = ""
        if selected_profile:
            cmd = [sys.executable, str(envsnap_script), "apply", "--profile", selected_profile]
            rc, _ = _qs_run_command(ctx, cmd)
            if rc != 0:
                return _qs_step_fail(summary, "应用运行配置 Profile", step, f"envsnap apply 返回码 {rc}", 2)
            cmd = [sys.executable, str(envsnap_script), "export-remote"]
            rc, _ = _qs_run_command(ctx, cmd)
            if rc != 0:
                return _qs_step_fail(summary, "应用运行配置 Profile", step, f"envsnap export-remote 返回码 {rc}", 2)
            ctx["profile"] = selected_profile
            _qs_step_ok(summary, "应用运行配置 Profile", step)

    step = _qs_step_start("一键链路：上传 → ASR → 回收 → 校验")
    deploy_cli = PROJ_ROOT / "scripts" / "deploy_cli.py"
    if not deploy_cli.exists():
        return _qs_step_fail(summary, "一键链路：上传 → ASR → 回收 → 校验", step, "未找到 scripts/deploy_cli.py", 2)
    run_id = ""
    snapshot_path = ""
    if envsnap_script.exists():
        snap_cmd = [sys.executable, str(envsnap_script), "snapshot"]
        rc, stdout = _qs_run_command(ctx, snap_cmd, capture=True)
        if rc == 0:
            run_id, snapshot_path = _parse_snapshot_output(stdout)
            if run_id:
                log_info(f"已生成快照：run_id={run_id}")
        else:
            return _qs_step_fail(summary, "一键链路：上传 → ASR → 回收 → 校验", step, f"envsnap snapshot 返回码 {rc}", 2)
    provider_cmd = [sys.executable, str(deploy_cli), "provider", "--set", "sync"]
    rc, _ = _qs_run_command(ctx, provider_cmd)
    if rc != 0:
        return _qs_step_fail(summary, "一键链路：上传 → ASR → 回收 → 校验", step, f"provider --set sync 返回码 {rc}", 2)
    upload_cmd = [sys.executable, str(deploy_cli), "upload_audio"]
    rc, _ = _qs_run_command(ctx, upload_cmd)
    if rc != 0:
        return _qs_step_fail(summary, "一键链路：上传 → ASR → 回收 → 校验", step, f"upload_audio 返回码 {rc}", 2)
    run_cmd = [sys.executable, str(deploy_cli), "run_asr"]
    if ctx.get("pattern"):
        run_cmd.extend(["--pattern", str(ctx["pattern"])])
    if ctx.get("model"):
        run_cmd.extend(["--model", str(ctx["model"])])
    if ctx.get("workers") is not None:
        run_cmd.extend(["--workers", str(ctx.get("workers"))])
    if ctx.get("overwrite"):
        run_cmd.append("--overwrite")
    extra_env: Dict[str, str] = {}
    if ctx.get("stems"):
        extra_env["ASR_STEMS"] = str(ctx["stems"])
    if run_id:
        extra_env["ENV_RUN_ID"] = run_id
    if snapshot_path:
        extra_env["ENV_SNAPSHOT_PATH"] = snapshot_path
    rc, _ = _qs_run_command(ctx, run_cmd, extra_env=extra_env or None)
    if rc != 0:
        return _qs_step_fail(summary, "一键链路：上传 → ASR → 回收 → 校验", step, f"run_asr 返回码 {rc}", 2)
    fetch_cmd = [sys.executable, str(deploy_cli), "fetch_outputs"]
    rc, _ = _qs_run_command(ctx, fetch_cmd)
    if rc != 0:
        return _qs_step_fail(summary, "一键链路：上传 → ASR → 回收 → 校验", step, f"fetch_outputs 返回码 {rc}", 2)
    verify_script = PROJ_ROOT / "scripts" / "verify_asr_words.py"
    if verify_script.exists():
        verify_cmd = [sys.executable, str(verify_script)]
        rc, _ = _qs_run_command(ctx, verify_cmd)
        if rc != 0:
            return _qs_step_fail(summary, "一键链路：上传 → ASR → 回收 → 校验", step, f"verify_asr_words 返回码 {rc}", 2)
    else:
        log_warn("[建议] 未找到 verify_asr_words.py，建议完成后手动校验 JSON。")
    ctx["run_id"] = run_id
    _qs_step_ok(summary, "一键链路：上传 → ASR → 回收 → 校验", step)

    step = _qs_step_start("实时镜像 Watch")
    if ctx.get("no_watch"):
        _qs_step_ok(summary, "实时镜像 Watch", step, status="Skip")
    else:
        watch_choice = ""
        if not ctx.get("auto_yes"):
            watch_choice = input("回车进入实时镜像（watch），或输入 n 跳过: ").strip().lower()
        if watch_choice in {"n", "no"}:
            log_info("已跳过 watch。")
            _qs_step_ok(summary, "实时镜像 Watch", step, status="Skip")
        else:
            watch_args = argparse.Namespace(run=str(ctx.get("run_id", "")), interval=3)
            rc = cmd_watch(watch_args)
            if rc > 1:
                return _qs_step_fail(summary, "实时镜像 Watch", step, f"watch 返回码 {rc}", 2)
            if rc == 1:
                log_warn("watch 已被用户中断。")
                _qs_step_ok(summary, "实时镜像 Watch", step, status="Skip")
            else:
                _qs_step_ok(summary, "实时镜像 Watch", step)

    total_elapsed = time.perf_counter() - overall_start
    print("\n步骤状态：")
    for title, status in summary:
        print(f"[{status:<4}] {title}")
    instance = ctx.get("instance", {})
    plan_label = ctx.get("plan_id", "")
    family_text = ctx.get("plan_info", {}).get("family", "-")
    gpu_text = _format_number(ctx.get("plan_info", {}).get("gpu_vram")) if ctx.get("plan_info") else "-"
    log_ok(
        f"Quickstart 完成：plan={plan_label} ({family_text} · GPU {gpu_text}GB) · region={region} · instance={instance.get('instance_id', '-')}")
    log_info(f"主 IP：{instance.get('main_ip', '-')}")
    if ctx.get("profile"):
        log_info(f"运行配置：{ctx['profile']}")
    if ctx.get("run_id"):
        log_info(f"最新 run_id：{ctx['run_id']}")
    outputs_dir = PROJ_ROOT / "data" / "asr-json"
    log_info(f"转写产物目录：{outputs_dir.relative_to(PROJ_ROOT)}")
    log_ok(f"总耗时 {total_elapsed:.1f}s")
    # ==== BEGIN: OnePass Patch · R4.4 (quickstart use-last) ====
    try:
        _state_update_last_used(
            region=region,
            os=os_slug,
            plan_id=ctx.get("plan_id"),
            profile=ctx.get("profile") or None,
        )
    except Exception:
        pass
    # ==== END: OnePass Patch · R4.4 (quickstart use-last) ====
    return 0


# ==== END: OnePass Patch · R2 ==== 


def _load_active_profile_env() -> Dict[str, str]:
    if not ACTIVE_PROFILE_PATH.exists():
        return {}
    try:
        return _parse_env_text(ACTIVE_PROFILE_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        log_warn(f"读取 {ACTIVE_PROFILE_PATH} 失败：{exc}")
        return {}


def _call_envsnap(args: List[str], *, capture: bool = False, dry_run: bool = False) -> Tuple[int, str]:
    script = PROJ_ROOT / "scripts" / "envsnap.py"
    if not script.exists():
        log_err("未找到 scripts/envsnap.py，请确认仓库已更新。")
        return 2, ""
    cmd = [sys.executable, str(script), *args]
    log_info(f"命令：{format_cmd(cmd)}")
    if dry_run:
        log_info("[DryRun] 已跳过 envsnap 操作。")
        return 0, ""
    if capture:
        result = subprocess.run(cmd, cwd=PROJ_ROOT, text=True, capture_output=True)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return result.returncode, result.stdout
    rc = run_streamed(cmd, cwd=PROJ_ROOT)
    return rc, ""


def _parse_snapshot_output(text: str) -> Tuple[str, str]:
    run_id = ""
    snapshot_path = ""
    for line in text.splitlines():
        if line.startswith("RUN_ID="):
            run_id = line.split("=", 1)[1].strip()
        elif line.startswith("SNAPSHOT_PATH="):
            snapshot_path = line.split("=", 1)[1].strip()
    return run_id, snapshot_path


def _expand_path(value: str) -> Path:
    expanded = os.path.expandvars(value)
    return Path(expanded).expanduser()


def _detect_default_private_key() -> Optional[Path]:
    """Try to locate a commonly used SSH private key on the current host."""

    candidates: List[Path] = []
    home = Path.home()
    if home:
        ssh_dir = home / ".ssh"
        candidates.extend(
            ssh_dir / name for name in ("id_ed25519", "id_rsa", "id_ecdsa", "id_dsa")
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _ssh_read_text(host: str, user: str, key_path: Path, remote_path: str) -> Tuple[int, str]:
    cmd = [
        "ssh",
        "-i",
        str(key_path),
        f"{user}@{host}",
        f"cat {shlex.quote(remote_path)}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout


def _scp_remote_file(host: str, user: str, key_path: Path, remote_path: str, local_path: Path) -> bool:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "scp",
        "-q",
        "-i",
        str(key_path),
        f"{user}@{host}:{remote_path}",
        str(local_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def _format_watch_event(data: dict) -> str:
    event_type = str(data.get("event", ""))
    timestamp = str(data.get("timestamp", ""))
    if event_type == "job_start":
        profile = data.get("profile", "")
        pattern = data.get("audio_pattern", "")
        return f"[{timestamp}] job_start · profile={profile} · pattern={pattern}"
    if event_type == "job_summary":
        status = data.get("status", "")
        success = data.get("success", "0")
        skip = data.get("skip", "0")
        failure = data.get("failure", "0")
        return f"[{timestamp}] job_summary · status={status} · success={success} · skip={skip} · failure={failure}"
    return f"[{timestamp}] {event_type} · {json.dumps(data, ensure_ascii=False)}"


def _format_exception(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _check_command(command: str, args: Optional[List[str]] = None) -> Tuple[bool, str, Optional[int]]:
    args = args or ["--version"]
    path = shutil.which(command)
    if not path:
        return False, f"未检测到 {command}，请确认已安装并加入 PATH。", None
    try:
        proc = subprocess.run([path, *args], capture_output=True, text=True, check=False)
    except OSError as exc:  # pragma: no cover - 平台相关
        return False, f"无法执行 {command}：{exc}", None
    output = proc.stdout.strip() or proc.stderr.strip()
    return True, output, proc.returncode


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


def _ensure_env_with_api_key(*, require_ssh: bool) -> Dict[str, str]:
    env = _read_env_file(optional=False)
    api_key = env.get("VULTR_API_KEY", "").strip()
    if not api_key:
        raise VultrError("VULTR_API_KEY 未配置，请设置环境变量或在 vultr.env 中提供。")
    env["VULTR_API_KEY"] = api_key
    if not require_ssh:
        return env
    key_path_value = (env.get("SSH_PRIVATE_KEY") or "").strip()
    fallback_key = _detect_default_private_key()
    if not key_path_value:
        if not fallback_key:
            raise VultrError("SSH_PRIVATE_KEY 未配置，请设置环境变量或在 vultr.env 中提供。")
        private_key = fallback_key
        env["SSH_PRIVATE_KEY"] = str(private_key)
        log_info(f"未配置 SSH_PRIVATE_KEY，自动使用 {private_key}")
    else:
        private_key = _expand_path(key_path_value)
        if not private_key.exists():
            if fallback_key and fallback_key != private_key:
                log_warn(
                    "未找到配置的 SSH 私钥 %s，自动回退到 %s"
                    % (private_key, fallback_key)
                )
                private_key = fallback_key
                env["SSH_PRIVATE_KEY"] = str(private_key)
            else:
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


def _ensure_env_and_key() -> Dict[str, str]:
    return _ensure_env_with_api_key(require_ssh=True)


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
    os_id = resolve_os_id(value, api_key=api_key)
    try:
        return int(os_id)
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
    if args.yes and args.no:
        log_err("--yes 与 --no 不能同时使用。")
        return 2

    section("步骤①：检查本机环境")
    if args.dry_run:
        log_warn("dry-run 对 env-check 无实际意义，将继续执行只读检查。")

    first_run = True

    def run_checks() -> Tuple[List[str], List[str]]:
        nonlocal first_run
        status_fail: List[str] = []
        status_warn: List[str] = []

        env = _read_env_file(optional=True)
        runtime_env = _collect_runtime_env()

        if first_run:
            log_info(f"操作系统：{platform.system()} {platform.release()}")
            log_info(f"Python 版本：{platform.python_version()}")
            sources: List[str] = []
            if ENV_FILE.exists():
                sources.append("vultr.env")
            if runtime_env:
                sources.append("系统环境变量")
            if sources:
                log_info(f"加载 Vultr 配置来源：{', '.join(sources)}")
        first_run = False

        ok, message, code = _check_command(sys.executable, ["--version"])
        if ok and (code == 0 or code is None):
            log_ok(f"Python 可用：{message}")
        elif ok:
            log_warn(f"Python 命令退出码 {code}：{message}")
            status_warn.append("python")
        else:
            log_err(message)
            status_fail.append("python")

        pwsh_path = shutil.which("pwsh")
        if pwsh_path:
            ok, message, code = _check_command(
                "pwsh",
                ["-NoLogo", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            )
            if ok and (code == 0 or code is None):
                log_ok(f"PowerShell 7+ 可用：{message}")
            elif ok:
                log_warn(f"pwsh 返回码 {code}：{message}")
                status_warn.append("pwsh")
        else:
            log_warn(
                "未找到 PowerShell 7 (pwsh)，推荐安装 https://aka.ms/powershell 以使用补充功能。"
            )
            status_warn.append("pwsh-missing")

        for cmd in ("ssh", "scp"):
            ok, message, code = _check_command(cmd, ["-V"])
            if ok and (code == 0 or code is None):
                log_ok(f"{cmd} 可用：{message}")
            elif ok:
                log_warn(f"{cmd} 返回码 {code}：{message}")
                status_warn.append(cmd)
            else:
                log_err(message)
                status_fail.append(cmd)

        ok, message, code = _check_command("rsync", ["--version"])
        if ok and (code == 0 or code is None):
            log_ok(f"rsync 可用：{message.splitlines()[0] if message else message}")
        elif ok:
            log_warn(f"rsync 返回码 {code}：{message}")
            status_warn.append("rsync")
        else:
            log_warn("未检测到 rsync，缺少时将回退到 scp。")
            status_warn.append("rsync-missing")

        fallback_key = _detect_default_private_key()
        if env:
            key_path_value = (env.get("SSH_PRIVATE_KEY") or "").strip()
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
                    if fallback_key and fallback_key != priv_path:
                        log_warn(
                            f"未找到配置的私钥 {priv_path}，将尝试使用默认私钥 {fallback_key}。"
                        )
                        log_ok(f"检测到默认私钥：{fallback_key}")
                    else:
                        log_warn(
                            f"未找到私钥 {priv_path}，可使用 ssh-keygen -t ed25519 生成。"
                        )
                        status_warn.append("ssh-key-missing")
            else:
                if fallback_key:
                    log_warn(
                        f"未在配置中检测到 SSH_PRIVATE_KEY，已发现默认私钥 {fallback_key}。"
                    )
                else:
                    log_warn("未在配置中检测到 SSH_PRIVATE_KEY，将跳过私钥检查。")
                    status_warn.append("ssh-key-config")
        else:
            if fallback_key:
                log_warn(
                    f"未检测到 Vultr 配置，但已发现默认私钥 {fallback_key}。"
                )
            else:
                log_warn("未检测到任何 Vultr 配置，将跳过私钥检查。")
                status_warn.append("vultr-config")

        ps_script = CUR_DIR / "cloud_env_check.ps1"
        pwsh_path = shutil.which("pwsh")
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

        return status_fail, status_warn

    status_fail, status_warn = run_checks()

    if args.auto_fix and (status_fail or status_warn):
        initial_fail = set(status_fail)
        initial_warn = set(status_warn)
        section("步骤②：自动修复缺失环境")
        auto_fix_script = PROJ_ROOT / "scripts" / "auto_fix_env.py"
        if not auto_fix_script.exists():
            log_err(f"未找到自动修复脚本：{auto_fix_script}")
            return 2
        auto_cmd = [sys.executable, str(auto_fix_script)]
        if args.yes:
            auto_cmd.append("--yes")
        elif args.no:
            auto_cmd.append("--no")
        log_info(f"执行自动修复：{format_cmd(auto_cmd)}")
        auto_rc = run_streamed(auto_cmd, cwd=PROJ_ROOT, heartbeat_s=30.0, show_cmd=False)
        if auto_rc == 2:
            log_err("自动修复脚本执行失败。")
        elif auto_rc == 1:
            log_warn("自动修复脚本返回警告，请查看日志确认状态。")

        section("步骤③：自动修复后再次检查")
        status_fail, status_warn = run_checks()
        new_fail = set(status_fail)
        new_warn = set(status_warn)

        resolved_fail = initial_fail - new_fail
        resolved_warn = initial_warn - new_warn
        remaining_fail = new_fail
        remaining_warn = new_warn

        if resolved_fail or resolved_warn:
            resolved = sorted(resolved_fail | resolved_warn)
            log_ok(f"已修复：{', '.join(resolved)}")
        if remaining_fail:
            log_err(f"仍缺失：{', '.join(sorted(remaining_fail))}")
        if remaining_warn:
            log_warn(f"仍存在警告：{', '.join(sorted(remaining_warn))}")

    if status_fail:
        log_err("环境检测存在阻塞项，请修复后重试。")
        return 2
    if status_warn:
        log_warn("环境检测有警告，但可继续执行后续步骤。")
        return 1
    log_ok("环境检测通过。")
    return 0


def cmd_install_deps(args: argparse.Namespace) -> int:
    if args.yes and args.no:
        log_err("--yes 与 --no 不能同时使用。")
        return 2

    section("安装/修复本地部署依赖")
    auto_fix_script = PROJ_ROOT / "scripts" / "auto_fix_env.py"
    if not auto_fix_script.exists():
        log_err(f"未找到自动修复脚本：{auto_fix_script}")
        return 2

    cmd = [sys.executable, str(auto_fix_script)]
    if args.only:
        cmd.extend(["--only", args.only])
    if args.yes:
        cmd.append("--yes")
    if args.no:
        cmd.append("--no")
    if args.dry_run:
        cmd.append("--dry-run")

    log_info(f"执行依赖安装：{format_cmd(cmd)}")
    rc = run_streamed(cmd, cwd=PROJ_ROOT, heartbeat_s=30.0, show_cmd=False)
    if rc == 0:
        log_ok("依赖安装脚本执行完成。")
    elif rc == 1:
        log_warn("依赖安装脚本返回警告，请查看输出确认状态。")
    else:
        log_err("依赖安装脚本执行失败。")
    return rc


# ==== BEGIN: OnePass Patch · R4.2 (create UX) ====
def cmd_create(args: argparse.Namespace) -> int:
    if getattr(args, "legacy", False):
        return _cmd_create_r1(args)

    verbose = getattr(args, "verbose", False)
    quiet = getattr(args, "quiet", False)

    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        message = _format_exception(exc) if isinstance(exc, VultrError) else str(exc)
        ux.err(message)
        return 2

    api_key = env.get("VULTR_API_KEY", "").strip()
    # ==== BEGIN: OnePass Patch · R4.4 (create use-last) ====
    last = _state_get_last_used() if getattr(args, "use_last", False) else {}
    plan_id = (
        getattr(args, "plan", None)
        or last.get("plan_id")
        or env.get("VULTR_PLAN")
        or "vc2-2c-4gb"
    )
    region = (
        getattr(args, "region", None)
        or last.get("region")
        or env.get("VULTR_REGION")
        or "nrt"
    )
    os_slug = (
        getattr(args, "os", None)
        or last.get("os")
        or env.get("VULTR_OS")
        or "ubuntu-22.04"
    )
    label = getattr(args, "label", None) or env.get("INSTANCE_LABEL", "onepass-asr")
    plan_id = str(plan_id).strip()
    region = str(region).strip() or "nrt"
    os_slug = str(os_slug).strip() or "ubuntu-22.04"
    # ==== END: OnePass Patch · R4.4 (create use-last) ====
    assume_yes = getattr(args, "yes", False)
    tag = env.get("TAG") or None

    family = "-"
    vram = "-"
    try:
        plans = vultr_api.list_gpu_plans(region=region, os_slug=os_slug, api_key=api_key)
        for plan in plans:
            if str(plan.get("plan_id")) == str(plan_id):
                family = plan.get("family", "-") or "-"
                vram_value = plan.get("gpu_vram_gb")
                vram = f"{vram_value}GB" if vram_value is not None else "-"
                break
    except Exception as exc:
        if verbose and not quiet:
            ux.warn(f"调试：拉取 plan 详情失败：{exc}")

    step_ctx = ux.step("校验 API Key") if not quiet else nullcontext()
    try:
        with step_ctx:
            vultr_api.get_account_info(api_key=api_key)
    except Exception as exc:
        ux.err("API Key 无效或无权限（请检查 deploy/cloud/vultr/vultr.env 的 VULTR_API_KEY）")
        if verbose and not quiet:
            ux.warn(f"调试：{type(exc).__name__}: {exc}")
        return 2

    if not assume_yes and not quiet:
        ux.out("即将创建实例：")
        ux.out(f"  计划: {family} {vram}")
        ux.out(f"  区域: {region}    OS: {os_slug}")
        ux.out(f"  标签: {label}")
        ans = input("确认？ [Y/n]: ").strip().lower()
        if ans in ("n", "no"):
            ux.warn("已取消")
            return 1

    if getattr(args, "dry_run", False):
        if not quiet:
            ux.warn("[DryRun] 已跳过实例创建，仅展示参数。")
        return 0

    try:
        resolved_region = _resolve_region(region, api_key)
        resolved_plan = _resolve_plan(plan_id, api_key)
        resolved_os = _resolve_os(os_slug, api_key)
    except VultrError as exc:
        ux.err(_format_exception(exc))
        return 2

    def _prepare_ssh_key() -> Tuple[str, str]:
        public_key = env["SSH_PUBLIC_KEY"]
        key_name = env.get("INSTANCE_LABEL", "onepass") + "-ssh"
        try:
            existing = vultr_api.list_ssh_keys(api_key)
        except Exception as exc:
            raise VultrError(str(exc))
        for item in existing:
            remote_key = (item.get("ssh_key") or "").strip()
            if remote_key == public_key:
                if verbose and not quiet:
                    ux.warn(
                        f"调试：复用已存在的 SSH Key：{item.get('name')} ({item.get('id')})"
                    )
                return str(item.get("id")), str(item.get("name"))
        created = vultr_api.create_ssh_key(key_name, public_key, api_key)
        key_info = created.get("ssh_key", created)
        if verbose and not quiet:
            ux.warn(
                f"调试：上传 SSH Key：{key_info.get('name')} ({key_info.get('id')})"
            )
        return str(key_info.get("id")), str(key_info.get("name"))

    try:
        ssh_key_id, _ssh_key_name = _prepare_ssh_key()
    except VultrError as exc:
        ux.err(_format_exception(exc))
        return 2

    timeout_s = int(env.get("CREATE_TIMEOUT_SEC", "900") or "900")
    poll_s = int(env.get("POLL_INTERVAL_SEC", "8") or "8")

    step_ctx = ux.step("创建实例") if not quiet else nullcontext()
    try:
        with step_ctx:
            response = vultr_api.create_instance(
                resolved_region,
                resolved_plan,
                resolved_os,
                label,
                [ssh_key_id],
                api_key,
                tag=tag,
            )
            instance = response.get("instance", response)
            instance_id = instance.get("id") or instance.get("instance_id") or "-"
            main_ip = instance.get("main_ip") or instance.get("ip") or "-"
            if not instance_id or instance_id == "-":
                raise VultrError("API 未返回实例 ID")
            vultr_api.wait_for_instance_active(instance_id, timeout_s, poll_s, api_key)
            info = vultr_api.get_instance(instance_id, api_key)
            instance_info = info.get("instance", info)
            main_ip = instance_info.get("main_ip") or main_ip
            state = {
                "instance_id": instance_id,
                "main_ip": main_ip,
                "ssh_key_id": ssh_key_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_state(state)
    except Exception as exc:
        if quiet:
            if isinstance(exc, VultrError):
                ux.err(_format_exception(exc))
            else:
                ux.err(f"创建失败：{type(exc).__name__}: {exc}")
        elif verbose:
            if isinstance(exc, VultrError):
                ux.warn(f"调试：{_format_exception(exc)}")
            else:
                ux.warn(f"调试：创建失败：{type(exc).__name__}: {exc}")
        return 2

    if quiet:
        print(main_ip if main_ip not in {None, "", "-"} else instance_id)
    else:
        ux.ok(
            f"已创建实例：ID={instance_id}  IP={main_ip}  Region={region}  Plan={plan_id}"
        )
    # ==== BEGIN: OnePass Patch · R4.4 (create use-last) ====
    try:
        _state_update_last_used(region=region, os=os_slug, plan_id=plan_id)
    except Exception:
        pass
    # ==== END: OnePass Patch · R4.4 (create use-last) ====
    return 0


# ==== END: OnePass Patch · R4.2 (create UX) ====
def _cmd_create_r1(args: argparse.Namespace) -> int:
    section("步骤②：创建 Vultr VPS")
    try:
        env = _ensure_env_and_key()
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2

    api_key = env["VULTR_API_KEY"].strip()
    # ==== BEGIN: OnePass Patch · R1 (plans & version check) ====
    try:
        get_account_info(api_key)
    except VultrError:
        log_err("API Key 无效或无权限（请检查 deploy/cloud/vultr/vultr.env 的 VULTR_API_KEY）")
        return 2

    try:
        ssh_key_id, ssh_key_name = _ensure_ssh_key(env)
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    # ==== END: OnePass Patch · R1 ====

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
    active_profile = _load_active_profile_env()
    default_remote_dir = active_profile.get("REMOTE_DIR", SYNC_DEFAULTS["VPS_REMOTE_DIR"])
    new_data.update(
        {
            "VPS_HOST": state.get("main_ip", ""),
            "VPS_USER": env.get("SSH_USER", "ubuntu"),
            "VPS_SSH_KEY": env.get("SSH_PRIVATE_KEY", ""),
            "VPS_REMOTE_DIR": env.get("REMOTE_DIR", default_remote_dir),
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


def _run_step(name: str, cmd: List[str], dry_run: bool, extra_env: Optional[Dict[str, str]] = None) -> int:
    log_info(f"开始：{name}")
    log_info(f"命令：{format_cmd(cmd)}")
    if dry_run:
        log_info("[DryRun] 已跳过执行。")
        return 0
    start = time.perf_counter()
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update({k: str(v) for k, v in extra_env.items() if v is not None})
    rc = run_streamed(cmd, cwd=PROJ_ROOT, env=env)
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

    active_profile = _load_active_profile_env()
    active_profile_name = active_profile.get("ENV_PROFILE", "")
    selected_profile = args.profile or active_profile_name
    run_id = ""
    snapshot_path = ""

    if args.profile:
        log_info(f"应用配置 profile：{args.profile}")
        rc, _ = _call_envsnap(["apply", "--profile", args.profile], dry_run=args.dry_run)
        if rc != 0:
            return rc
        active_profile = _load_active_profile_env()
        active_profile_name = args.profile
    elif active_profile_name:
        log_info(f"沿用当前激活的 profile：{active_profile_name}")
    else:
        log_warn("尚未检测到激活的 profile，请确认已运行 envsnap.py apply。")

    rc, _ = _call_envsnap(["export-remote"], dry_run=args.dry_run)
    if rc != 0:
        return rc

    if not args.dry_run:
        snapshot_args = ["snapshot"]
        if args.note:
            snapshot_args.extend(["--note", args.note])
        snap_rc, snap_stdout = _call_envsnap(snapshot_args, capture=True, dry_run=False)
        if snap_rc != 0:
            return snap_rc
        run_id, snapshot_path = _parse_snapshot_output(snap_stdout)
        if not run_id:
            log_warn("未能从快照输出解析 RUN_ID，将继续流程。")
    else:
        log_warn("dry-run 模式：不会生成快照或触发远端作业。")

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
        extra_env = None
        if name == "远端执行 ASR" and run_id and not args.dry_run:
            extra_env = {"ENV_RUN_ID": run_id, "ENV_SNAPSHOT_PATH": snapshot_path}
        rc = _run_step(name, cmd, dry_run=args.dry_run, extra_env=extra_env)
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

    if not args.dry_run and run_id:
        log_info(f"本次 run_id：{run_id}（profile={selected_profile or '<未设置>'}）")
        if _prompt_bool("是否立即进入 watch 模式查看实时事件?", True):
            watch_args = argparse.Namespace(run=run_id, interval=3)
            return cmd_watch(watch_args)
    return overall_rc


def cmd_watch(args: argparse.Namespace) -> int:
    section("远程 ASR 实时镜像")
    try:
        state, env = _ensure_state_for_sync()
    except (RuntimeError, VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2

    host = state.get("main_ip")
    if not host:
        log_err("state.json 中缺少 main_ip。")
        return 2
    user = env.get("SSH_USER", "ubuntu")
    key_value = env.get("SSH_PRIVATE_KEY_RESOLVED") or env.get("SSH_PRIVATE_KEY", "")
    key_path = _expand_path(key_value)
    if not key_path.exists():
        log_err(f"SSH 私钥不存在：{key_path}")
        return 2

    remote_dir = env.get("REMOTE_DIR") or env.get("VPS_REMOTE_DIR") or SYNC_DEFAULTS["VPS_REMOTE_DIR"]
    remote_dir = remote_dir.rstrip("/")
    run_id = getattr(args, "run", "") or ""

    if not run_id:
        rc, stdout = _ssh_read_text(host, user, key_path, f"{remote_dir}/out/state.json")
        if rc != 0 or not stdout.strip():
            log_err("无法从远端 state.json 获取当前 run_id。")
            return 2
        try:
            state_data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            log_err(f"解析远端 state.json 失败：{exc}")
            return 2
        run_id = str(state_data.get("run_id", "")).strip()
        if not run_id:
            log_err("state.json 中缺少 run_id。")
            return 2

    remote_run_dir = f"{remote_dir}/out/_runs/{run_id}"
    remote_events = f"{remote_run_dir}/events.ndjson"
    remote_state = f"{remote_run_dir}/state.json"
    remote_manifest = f"{remote_run_dir}/manifest.json"

    mirror_dir = PROJ_ROOT / "out" / "remote_mirror" / run_id
    mirror_dir.mkdir(parents=True, exist_ok=True)
    local_events = mirror_dir / "events.ndjson"
    local_state = mirror_dir / "state.json"
    local_manifest = mirror_dir / "manifest.json"

    interval = max(1, int(getattr(args, "interval", 3)))
    header_printed = False
    last_line_count = 0
    manifest_announced = False
    status = ""
    lines: List[str] = []

    try:
        while True:
            events_ok = _scp_remote_file(host, user, key_path, remote_events, local_events)
            if events_ok and local_events.exists():
                content = local_events.read_text(encoding="utf-8")
                lines = content.splitlines()
                if len(lines) > last_line_count:
                    for line in lines[last_line_count:]:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            print(line)
                        else:
                            print(_format_watch_event(event))
                    last_line_count = len(lines)

            state_ok = _scp_remote_file(host, user, key_path, remote_state, local_state)
            if state_ok and local_state.exists():
                try:
                    state_data = json.loads(local_state.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    state_data = {}
                profile_name = state_data.get("profile", "")
                run_mode = state_data.get("run_mode", "")
                status = str(state_data.get("status", status or ""))
                if not header_printed and (profile_name or run_mode):
                    log_info(
                        f"监控 run_id={run_id} · profile={profile_name or '-'} · run_mode={run_mode or '-'}"
                    )
                    header_printed = True

            if _scp_remote_file(host, user, key_path, remote_manifest, local_manifest) and not manifest_announced:
                log_ok(f"已镜像 manifest 至 {local_manifest.relative_to(PROJ_ROOT)}")
                manifest_announced = True

            if status.lower() in {"succeeded", "failed", "cancelled"}:
                if len(lines) == last_line_count and events_ok:
                    log_info(f"远端作业状态：{status}")
                    break
            time.sleep(interval)
    except KeyboardInterrupt:
        log_warn("用户中断了 watch。")
        return 1
    return 0


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


_GPU_FAMILY_PATTERN = re.compile(r"(A40|A16|L40S|A100|L4)", re.IGNORECASE)


def _parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("$", "")
    match = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "-"
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _extract_gpu_family(plan: dict) -> str:
    text = f"{plan.get('name', '')} {plan.get('description', '')}"
    match = _GPU_FAMILY_PATTERN.search(text)
    if match:
        return match.group(1).upper()
    if "GPU" in text.upper():
        return "GPU"
    return "-"


def _extract_gpu_vram_gb(plan: dict) -> Optional[float]:
    candidates = [
        plan.get("gpu_vram_gb"),
        plan.get("gpu_vram"),
        plan.get("gpu_memory"),
        plan.get("gpu_memory_gb"),
    ]
    for value in candidates:
        amount = _parse_float(value)
        if amount is None:
            continue
        if amount > 512:
            return round(amount / 1024, 2)
        return round(amount, 2)
    return None


def _extract_vcpu(plan: dict) -> Optional[float]:
    for key in ("vcpu_count", "vcpu", "vcpus", "cpu_count", "cpu"):
        amount = _parse_float(plan.get(key))
        if amount is not None:
            return amount
    return None


def _extract_ram_gb(plan: dict) -> Optional[float]:
    for key in ("ram", "memory", "ram_gb", "memory_mb"):
        amount = _parse_float(plan.get(key))
        if amount is None:
            continue
        if amount > 1024:
            return round(amount / 1024, 2)
        return round(amount, 2)
    return None


def _extract_storage(plan: dict) -> str:
    amount = None
    for key in ("disk", "storage", "disk_gb"):
        amount = _parse_float(plan.get(key))
        if amount is not None:
            break
    storage_text = ""
    if amount is not None:
        storage_text = f"{_format_number(amount)}GB"
    else:
        for key in ("disk", "storage"):
            raw = plan.get(key)
            if isinstance(raw, str) and raw.strip():
                storage_text = raw.strip()
                break
    disk_type = plan.get("disk_type") or plan.get("storage_type")
    if disk_type:
        storage_text = f"{storage_text} {disk_type}".strip()
    return storage_text or "-"


def _extract_bandwidth(plan: dict) -> str:
    amount = None
    unit = "TB"
    for key in ("bandwidth", "bandwidth_tb", "transfer", "transfer_tb", "monthly_bandwidth_tb"):
        amount = _parse_float(plan.get(key))
        if amount is not None:
            break
    if amount is None:
        for key in ("bandwidth_gb", "transfer_gb"):
            amount = _parse_float(plan.get(key))
            if amount is not None:
                unit = "GB"
                break
    if amount is None:
        text = str(plan.get("bandwidth") or plan.get("transfer") or "").strip()
        return text or "-"
    return f"{_format_number(amount)}{unit}"


def _extract_price_per_hour(plan: dict) -> Optional[float]:
    for key in ("price_per_hour", "price_hourly", "hourly_cost", "hourly"):
        amount = _parse_float(plan.get(key))
        if amount is not None:
            return amount
    for key in ("price", "price_monthly", "monthly_cost"):
        monthly = _parse_float(plan.get(key))
        if monthly is not None:
            return monthly / (30 * 24)
    return None


def _format_price(per_hour: Optional[float]) -> str:
    if per_hour is None:
        return "-"
    return f"${per_hour:.3f}"


def _plan_supports_os(plan: dict, os_id: str) -> bool:
    if not os_id:
        return True
    str_id = str(os_id)
    candidates = [
        plan.get("available_os"),
        plan.get("available_os_ids"),
        plan.get("allowed_os"),
        plan.get("operating_systems"),
        plan.get("os"),
        plan.get("os_id"),
    ]
    seen = False
    for candidate in candidates:
        if not candidate:
            continue
        seen = True
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict):
                    value = item.get("id") or item.get("os_id") or item.get("value")
                    if value and str(value) == str_id:
                        return True
                elif str(item) == str_id:
                    return True
        else:
            if str(candidate) == str_id:
                return True
    return not seen


def _is_region_available(availability: Dict[str, bool], region: str) -> bool:
    region_lower = region.lower()
    for code, available in availability.items():
        if str(code).lower() == region_lower:
            return bool(available)
    return False


def _format_regions(availability: Dict[str, bool], region: Optional[str], highlight: bool) -> str:
    regions = sorted(k for k, available in availability.items() if available)
    if not regions:
        regions_text = "-"
    else:
        entries: List[str] = []
        region_lower = region.lower() if region else None
        for code in regions:
            entry = str(code)
            if region_lower and str(code).lower() == region_lower:
                entry = f"[{entry}]"
            entries.append(entry)
        regions_text = ", ".join(entries)
    if highlight and regions_text != "-":
        regions_text = f"{regions_text} *"
    return regions_text

def _print_table(headers: List[str], rows: List[List[str]], *, max_col_width: int = 36) -> None:
    wrapped_rows: List[List[List[str]]] = []
    widths = [len(h) for h in headers]
    for row in rows:
        wrapped_row: List[List[str]] = []
        for idx, cell in enumerate(row):
            text = str(cell)
            cell_lines: List[str] = []
            for segment in text.splitlines() or [""]:
                wrapped = textwrap.wrap(segment, width=max_col_width) or [segment[:max_col_width]]
                cell_lines.extend(wrapped)
            widths[idx] = min(max(max(widths[idx], *(len(line) for line in cell_lines)), len(headers[idx])), max_col_width)
            wrapped_row.append(cell_lines)
        wrapped_rows.append(wrapped_row)
    header_line = " | ".join(headers[idx].ljust(widths[idx]) for idx in range(len(headers)))
    print(header_line)
    print("-+-".join("-" * w for w in widths))
    for wrapped_row in wrapped_rows:
        row_height = max(len(cell_lines) for cell_lines in wrapped_row)
        for line_idx in range(row_height):
            parts: List[str] = []
            for col_idx, cell_lines in enumerate(wrapped_row):
                value = cell_lines[line_idx] if line_idx < len(cell_lines) else ""
                parts.append(value.ljust(widths[col_idx]))
            print(" | ".join(parts))


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


# ==== BEGIN: OnePass Patch · R4.2 (plans UX) ====
def cmd_plans(args: argparse.Namespace) -> int:
    if getattr(args, "legacy", False):
        return _cmd_plans_r1(args)

    # ==== BEGIN: OnePass Patch · R4.4 (plans use-last) ====
    last = _state_get_last_used() if getattr(args, "use_last", False) else {}
    region_arg = getattr(args, "region", None)
    os_arg = getattr(args, "os", None)
    region = (
        (str(region_arg).strip() if region_arg not in (None, "") else "")
        or str(last.get("region") or "")
        or "nrt"
    )
    region = region.strip().lower() or "nrt"
    os_slug = (
        (str(os_arg).strip() if os_arg not in (None, "") else "")
        or str(last.get("os") or "")
        or "ubuntu-22.04"
    )
    os_slug = os_slug.strip() or "ubuntu-22.04"
    args.region = region
    args.os = os_slug
    family_re = getattr(args, "family", None)
    min_vram = getattr(args, "min_vram", None)
    only_available = getattr(args, "only_available", True)
    as_json = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)
    quiet = getattr(args, "quiet", False)

    if getattr(args, "dry_run", False):
        if not quiet:
            ux.warn(
                f"[DryRun] 将获取 GPU 计划：region={region} os={os_slug} only_available={only_available}"
            )
        return 0

    try:
        env = _ensure_env_with_api_key(require_ssh=False)
    except (VultrError, FileNotFoundError) as exc:
        message = _format_exception(exc) if isinstance(exc, VultrError) else str(exc)
        ux.err(message)
        return 2

    api_key = env.get("VULTR_API_KEY", "").strip()

    if family_re:
        try:
            family_re_compiled = re.compile(family_re, re.IGNORECASE)
        except re.error as exc:
            ux.err(f"无效的 --family 正则：{exc}")
            return 2
    else:
        family_re_compiled = None

    step_ctx = ux.step(f"查询 {region} 可用 GPU 计划") if not quiet else nullcontext()
    try:
        with step_ctx:
            plans = vultr_api.list_gpu_plans(
                region=region if only_available else None,
                os_slug=os_slug,
                api_key=api_key,
            )
    except Exception as exc:
        if quiet:
            if isinstance(exc, VultrError):
                ux.err(_format_exception(exc))
            else:
                ux.err(str(exc))
        elif verbose:
            if isinstance(exc, VultrError):
                ux.warn(f"调试：{_format_exception(exc)}")
            else:
                ux.warn(f"调试：获取计划失败：{exc}")
        return 2

    if family_re_compiled:
        plans = [
            p
            for p in plans
            if family_re_compiled.search(str(p.get("family", "")) or str(p.get("name", "")) or "")
        ]
    if min_vram is not None:
        try:
            mv = int(min_vram)
        except (TypeError, ValueError):
            ux.err("--min-vram 需要整数值")
            return 2
        plans = [p for p in plans if (p.get("gpu_vram_gb") or 0) >= mv]

    if verbose and not quiet:
        ux.warn(f"调试：筛后条数 = {len(plans)}")

    if as_json:
        print(json.dumps(plans, ensure_ascii=False, indent=2))
        if plans:
            try:
                _state_update_last_used(region=region, os=os_slug)
            except Exception:
                pass
            return 0
        return 1

    if not plans:
        ux.warn(f"{region} 暂无满足条件的 GPU 套餐；可试试 --region sgp / lax / fra")
        return 1

    if quiet:
        for plan in plans:
            print(plan.get("plan_id", ""))
        try:
            _state_update_last_used(region=region, os=os_slug)
        except Exception:
            pass
        return 0

    rows = []
    for plan in plans:
        price = plan.get("price_hour")
        if isinstance(price, (int, float)):
            price_display = f"${price:.3f}"
        else:
            price_display = "-"
        regions = plan.get("available_regions")
        regions_display = ",".join(regions) if regions else "?"
        rows.append(
            [
                plan.get("plan_id", ""),
                plan.get("family", "-"),
                plan.get("gpu_vram_gb", "-"),
                plan.get("vcpu", "-"),
                plan.get("ram_gb", "-"),
                plan.get("storage", "-"),
                price_display,
                regions_display,
            ]
        )

    ux.table(
        rows,
        headers=["plan_id", "family", "vRAM", "vCPU", "RAM", "Storage", "Price/h", "Regions"],
        maxw=100,
    )
    ux.out(
        ux.DIM
        + f"› 设置示例：VULTR_PLAN=<plan_id>  VULTR_REGION={region}  VULTR_OS={os_slug}"
        + ux.RESET
    )
    try:
        _state_update_last_used(region=region, os=os_slug)
    except Exception:
        pass
    # ==== END: OnePass Patch · R4.4 (plans use-last) ====
    return 0


# ==== BEGIN: OnePass Patch · R4.2 (plans-nrt alias) ====
def cmd_plans_nrt(args: argparse.Namespace) -> int:
    if getattr(args, "legacy", False):
        return _cmd_plans_nrt_r1(args)
    if not getattr(args, "use_last", False):
        if getattr(args, "region", None) in (None, ""):
            args.region = "nrt"
        if getattr(args, "os", None) in (None, ""):
            args.os = "ubuntu-22.04"
    return cmd_plans(args)


# ==== END: OnePass Patch · R4.2 (plans-nrt alias) ====


# ==== END: OnePass Patch · R4.2 (plans UX) ====
# ==== BEGIN: OnePass Patch · R1 (plans & version check) ====
def _cmd_plans_legacy(args: argparse.Namespace) -> int:
    gpu_only = getattr(args, "gpu_only", False)
    only_available = getattr(args, "only_available", False)
    region = getattr(args, "region", None) or "nrt"
    try:
        env = _ensure_env_with_api_key(require_ssh=False)
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2
    api_key = env["VULTR_API_KEY"].strip()
    if getattr(args, "dry_run", False):
        log_info(
            "[DryRun] 将列出 Vultr 计划："
            f"region={region}, os={getattr(args, 'os', 'ubuntu-22.04')}, gpu_only={gpu_only}, only_available={only_available}"
        )
        return 0
    try:
        os_id = resolve_os_id(getattr(args, "os", "ubuntu-22.04"), api_key=api_key)
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    try:
        if gpu_only:
            plans = [plan for plan in list_plans(api_key) if _plan_matches_gpu_keywords(plan)]
        else:
            plans = list_plans(api_key)
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2

    entries: List[dict] = []
    filter_text_raw = getattr(args, "filter", None)
    filter_text = filter_text_raw.lower() if filter_text_raw else None
    try:
        family_pattern = (
            re.compile(getattr(args, "family", None), re.IGNORECASE)
            if getattr(args, "family", None)
            else None
        )
    except re.error as exc:
        log_err(f"无效的 --family 正则：{exc}")
        return 2
    min_vram = getattr(args, "min_vram", None)
    os_id_str = str(os_id)
    for plan in plans:
        availability = extract_region_availability(plan)
        if region and only_available and not _is_region_available(availability, region):
            continue
        if not _plan_supports_os(plan, os_id_str):
            continue
        plan_id = str(plan.get("id", "")).strip()
        name = str(plan.get("name") or "").strip()
        description = str(plan.get("description") or "").strip()
        family = _extract_gpu_family(plan)
        searchable = f"{plan_id} {name} {description} {family}"
        if filter_text and filter_text not in searchable.lower():
            continue
        if family_pattern and not (
            family_pattern.search(family) or family_pattern.search(searchable)
        ):
            continue
        gpu_vram = _extract_gpu_vram_gb(plan)
        if min_vram is not None and (gpu_vram is None or gpu_vram < min_vram):
            continue
        vcpu = _extract_vcpu(plan)
        ram_gb = _extract_ram_gb(plan)
        storage = _extract_storage(plan)
        bandwidth = _extract_bandwidth(plan)
        price_hour = _extract_price_per_hour(plan)
        highlight = bool(min_vram is not None and gpu_vram is not None and gpu_vram >= min_vram)
        regions_display = _format_regions(availability, region, highlight)
        row = [
            plan_id or "-",
            family,
            _format_number(gpu_vram),
            _format_number(vcpu),
            _format_number(ram_gb),
            storage,
            bandwidth,
            _format_price(price_hour),
            regions_display,
        ]
        entries.append(
            {
                "row": row,
                "plan_id": plan_id,
                "price": price_hour if price_hour is not None else float("inf"),
                "vram": gpu_vram if gpu_vram is not None else -1.0,
                "vcpu": vcpu if vcpu is not None else -1.0,
                "highlight": highlight,
            }
        )

    if not entries:
        msg = "未找到符合条件的计划。"
        if region:
            msg += f" 尝试 plans --region <other>（例如 sgp、lax、fra）。"
        log_warn(msg)
        return 1

    sort_key = getattr(args, "sort", "price")
    if sort_key == "price":
        entries.sort(key=lambda item: (item["price"], item["plan_id"]))
    elif sort_key == "vram":
        entries.sort(key=lambda item: (-item["vram"], item["plan_id"]))
    elif sort_key == "vcpu":
        entries.sort(key=lambda item: (-item["vcpu"], item["plan_id"]))

    headers = [
        "plan_id",
        "family",
        "gpu_vram(GB)",
        "vCPU",
        "RAM(GB)",
        "Storage",
        "Bandwidth",
        "Price(/h)",
        "Regions",
    ]
    rows = [entry["row"] for entry in entries]
    _print_table(headers, rows)

    if min_vram is not None:
        highlights = sum(1 for entry in entries if entry["highlight"])
        if highlights:
            print(f"* 推荐：满足 --min-vram={min_vram}")
        else:
            print(f"未找到满足 --min-vram={min_vram} 的计划。")
    print("选中后可写入：")
    print("  VULTR_PLAN=<plan_id>")
    print(f"  VULTR_REGION={region}")
    print(f"  VULTR_OS={args.os}")
    print("将某个 plan_id 填入 deploy/cloud/vultr/vultr.env 的 VULTR_PLAN= 后，再执行 create")
    print("或直接： python deploy/cloud/vultr/cloud_vultr_cli.py create --plan <plan_id>（若支持覆盖 env）")
    return 0
# ==== END: OnePass Patch · R1 ====



def _cmd_plans_r1(args: argparse.Namespace) -> int:
    # ==== BEGIN: OnePass Patch · R1 (plans & version check) ====
    if getattr(args, "legacy", False):
        return _cmd_plans_legacy(args)

    region = (args.region or "nrt").strip().lower()
    os_slug = (args.os or "ubuntu-22.04").strip() or "ubuntu-22.04"

    family_pattern = None
    if args.family:
        try:
            family_pattern = re.compile(args.family, re.IGNORECASE)
        except re.error as exc:
            log_err(f"无效的 --family 正则：{exc}")
            return 2

    try:
        min_vram = int(args.min_vram) if args.min_vram is not None else None
    except (TypeError, ValueError):
        log_err("--min-vram 需要整数值")
        return 2

    try:
        env = _ensure_env_with_api_key(require_ssh=False)
    except (VultrError, FileNotFoundError) as exc:
        log_err(_format_exception(exc))
        return 2

    api_key = env["VULTR_API_KEY"].strip()
    if args.dry_run:
        log_info(
            f"[DryRun] 将获取 GPU 计划：region={region} os={os_slug} only_available={args.only_available}"
        )
        return 0

    if args.verbose:
        log_info(f"[进行中] 请求 GPU 套餐，region={region}")
    try:
        plans = list_gpu_plans(
            region=region if args.only_available else None,
            os_slug=os_slug,
            api_key=api_key,
        )
    except VultrError as exc:
        log_err(_format_exception(exc))
        return 2
    if args.verbose:
        log_ok(f"获取到 {len(plans)} 条套餐记录（原始数据）")

    filtered: List[dict] = []
    for plan in plans:
        available_regions = plan.get("available_regions") or []
        if args.only_available and available_regions:
            normalized = {str(item).lower() for item in available_regions}
            if region not in normalized:
                continue
        family = plan.get("family") or "-"
        if family_pattern and not family_pattern.search(family):
            continue
        gpu_vram = plan.get("gpu_vram_gb")
        if min_vram is not None and (gpu_vram is None or gpu_vram < min_vram):
            continue
        filtered.append(plan)

    if args.verbose:
        log_info(f"[提示] 按条件过滤后剩余 {len(filtered)} 条记录")

    if args.json:
        print(json.dumps(filtered, ensure_ascii=False, indent=2))
        return 0 if filtered else 1

    if not filtered:
        region_display = {"nrt": "东京"}.get(region, region.upper())
        log_warn(
            f"{region_display}({region})暂无满足条件的 GPU 套餐，可尝试 --region sgp/lax/fra"
        )
        return 1

    headers = ["plan_id", "family", "vRAM", "vCPU", "RAM", "Storage", "Price/h", "Regions"]
    rows: List[List[str]] = []
    for plan in filtered:
        plan_id = plan.get("plan_id") or "-"
        family = plan.get("family") or "-"
        gpu_vram = plan.get("gpu_vram_gb")
        ram_gb = plan.get("ram_gb")
        price_hour = plan.get("price_hour")
        regions = plan.get("available_regions")
        if regions:
            regions_text = ",".join(sorted(str(item).upper() for item in regions))
        else:
            regions_text = "?"
        highlight = bool(min_vram is not None and gpu_vram is not None and gpu_vram >= min_vram)
        family_display = family
        if highlight:
            family_display = f"*{family}" if family != "-" else "*GPU"
        rows.append(
            [
                str(plan_id),
                family_display,
                f"{gpu_vram}GB" if gpu_vram is not None else "-",
                str(plan.get("vcpu") if plan.get("vcpu") is not None else "-"),
                f"{ram_gb}GB" if ram_gb is not None else "-",
                plan.get("storage") or "-",
                f"${price_hour:.4f}" if price_hour is not None else "-",
                regions_text,
            ]
        )

    _print_table(headers, rows, max_col_width=28)

    grey = "[90m"
    reset = "[0m"
    for plan in filtered:
        plan_id = plan.get("plan_id") or "-"
        sample = f"› VULTR_PLAN={plan_id}  VULTR_REGION={region}  VULTR_OS={os_slug}"
        print(f"{grey}{sample}{reset}")

    log_ok("完成 GPU 套餐列表。")
    # ==== END: OnePass Patch · R1 ====
    return 0

def _cmd_plans_nrt_r1(args: argparse.Namespace) -> int:
    # ==== BEGIN: OnePass Patch · R1 (plans & version check) ====
    merged = argparse.Namespace(
        region="nrt",
        os="ubuntu-22.04",
        only_available=not getattr(args, "include_unavailable", False),
        family=getattr(args, "family", None),
        min_vram=getattr(args, "min_vram", None),
        json=getattr(args, "json", False),
        verbose=getattr(args, "verbose", False),
        dry_run=args.dry_run,
        legacy=False,
    )
    return _cmd_plans_r1(merged)
    # ==== END: OnePass Patch · R1 ====


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
    # ==== BEGIN: OnePass Patch · R4.2 (CLI flags) ====
    parser.add_argument("--verbose", action="store_true", help="Print verbose debug details")
    parser.add_argument("--quiet", action="store_true", help="Minimal output (only key results)")
    # ==== END: OnePass Patch · R4.2 (CLI flags) ====
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    def _add_dry_run(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dry-run", action="store_true", help="仅展示将执行的操作")

    quick_parser = sub.add_parser("quickstart", help="快速创建实例并执行完整 ASR 流程")
    quick_parser.add_argument("--region", default=None, help="Region ID（默认 nrt）")
    quick_parser.add_argument(
        "--os",
        dest="os",
        default=None,
        help="操作系统 slug（默认 ubuntu-22.04）",
    )
    quick_parser.add_argument("--family", help="GPU 家族正则（如 A40|L40S|A16）", default=None)
    quick_parser.add_argument("--min-vram", type=int, help="最小 GPU 显存 (GB)", default=None)
    quick_parser.add_argument("--plan", help="直接指定 plan_id（跳过筛选）", default=None)
    quick_parser.add_argument("--label", help="实例标签（默认 onepass-asr）", default=None)
    quick_parser.add_argument("--profile", help="运行 profile 名称", default=None)
    quick_parser.add_argument("--workers", type=int, help="ASR 并发 workers 数", default=None)
    quick_parser.add_argument("--model", help="Whisper 模型名称", default=None)
    quick_parser.add_argument("--pattern", help="音频匹配模式 (CSV)", default=None)
    quick_parser.add_argument("--stems", help="限定 stems (CSV)", default=None)
    quick_parser.add_argument("--overwrite", action="store_true", help="覆盖已有 JSON")
    quick_parser.add_argument("--yes", action="store_true", help="在交互提示中默认选择 Yes")
    quick_parser.add_argument("--no-watch", action="store_true", help="流程结束后跳过 watch")
    quick_parser.add_argument("--verbose", action="store_true", help="打印底层命令与参数")
    quick_parser.add_argument("--quiet", action="store_true", help="仅输出关键节点信息")
    # ==== BEGIN: OnePass Patch · R4.4 (arg flags) ====
    quick_parser.add_argument(
        "--use-last",
        action="store_true",
        help="Prefill missing region/os/plan_id/profile from last_used",
    )
    # ==== END: OnePass Patch · R4.4 (arg flags) ====
    quick_parser.set_defaults(func=cmd_quickstart)

    env_parser = sub.add_parser("env-check", help="检查本机环境")
    _add_dry_run(env_parser)
    env_parser.add_argument(
        "--auto-fix",
        action="store_true",
        help="检测到缺失项时自动调用 scripts/auto_fix_env.py",
    )
    env_parser.add_argument(
        "--yes",
        action="store_true",
        help="自动确认自动修复中的安装提示",
    )
    env_parser.add_argument(
        "--no",
        action="store_true",
        help="拒绝自动修复安装操作，仅查看建议",
    )
    env_parser.set_defaults(func=cmd_env_check)

    install_parser = sub.add_parser("install-deps", help="自动安装/修复部署依赖")
    _add_dry_run(install_parser)
    install_parser.add_argument("--only", help="仅运行指定组件 (逗号分隔)", default="")
    install_parser.add_argument(
        "--yes",
        action="store_true",
        help="自动确认安装脚本中的提示",
    )
    install_parser.add_argument(
        "--no",
        action="store_true",
        help="拒绝安装脚本中的提示，仅查看命令",
    )
    install_parser.set_defaults(func=cmd_install_deps)

    create_parser = sub.add_parser("create", help="创建 VPS 实例")
    _add_dry_run(create_parser)
    create_parser.add_argument("--plan", help="指定 plan_id（覆盖 vultr.env）", default=None)
    create_parser.add_argument("--region", help="Region ID（默认 nrt）", default=None)
    create_parser.add_argument(
        "--os",
        dest="os",
        help="操作系统 slug（默认 ubuntu-22.04）",
        default=None,
    )
    create_parser.add_argument("--label", help="实例标签（默认 onepass-asr）", default=None)
    create_parser.add_argument("--yes", action="store_true", help="在交互提示中默认选择 Yes")
    create_parser.add_argument("--verbose", action="store_true", help="打印调试信息")
    create_parser.add_argument("--quiet", action="store_true", help="仅输出关键结果")
    # ==== BEGIN: OnePass Patch · R4.4 (arg flags) ====
    create_parser.add_argument(
        "--use-last",
        action="store_true",
        help="Prefill missing region/os/plan_id/profile from last_used",
    )
    # ==== END: OnePass Patch · R4.4 (arg flags) ====
    create_parser.add_argument("--legacy", action="store_true", help=argparse.SUPPRESS)
    create_parser.set_defaults(func=cmd_create, legacy=False)

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
    bridge_parser.add_argument("--profile", help="指定运行 profile", default=None)
    bridge_parser.add_argument("--note", help="快照备注", default="")
    _add_dry_run(bridge_parser)
    bridge_parser.set_defaults(func=cmd_asr_bridge)

    watch_parser = sub.add_parser("watch", help="实时镜像远端 ASR 事件")
    watch_parser.add_argument("--run", help="指定 run_id（默认取远端最新一次）", default=None)
    watch_parser.add_argument("--interval", type=int, default=3, help="轮询间隔秒")
    watch_parser.set_defaults(func=cmd_watch)

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
    plans_parser.add_argument("--region", default=None, help="Region ID（默认 nrt）")
    plans_parser.add_argument("--os", default=None, help="操作系统 slug（默认 ubuntu-22.04）")
    plans_parser.add_argument("--family", help="GPU 家族正则 (如 A40|L40S|A16)", default=None)
    plans_parser.add_argument("--min-vram", type=int, help="最小 GPU 显存 (GB)", default=None)
    plans_parser.add_argument(
        "--only-available",
        dest="only_available",
        action="store_true",
        default=True,
        help="仅显示指定 region 当前标记为可用的套餐（默认启用）",
    )
    plans_parser.add_argument(
        "--include-unavailable",
        dest="only_available",
        action="store_false",
        help="包含暂未标记为可用的套餐",
    )
    plans_parser.add_argument("--json", action="store_true", help="以 JSON 形式输出")
    plans_parser.add_argument("--verbose", action="store_true", help="打印调试信息")
    plans_parser.add_argument("--quiet", action="store_true", help="仅输出 plan_id")
    plans_parser.add_argument("--legacy", action="store_true", help=argparse.SUPPRESS)
    # ==== BEGIN: OnePass Patch · R4.4 (arg flags) ====
    plans_parser.add_argument(
        "--use-last",
        action="store_true",
        help="Prefill missing region/os from last_used cache",
    )
    # ==== END: OnePass Patch · R4.4 (arg flags) ====
    _add_dry_run(plans_parser)
    plans_parser.set_defaults(func=cmd_plans, legacy=False)

    plans_nrt_parser = sub.add_parser(
        "plans-nrt",
        help="一键列出东京 nrt + Ubuntu 22.04 可用 GPU 套餐",
    )
    plans_nrt_parser.add_argument("--region", default=None, help="Region ID（默认 nrt）")
    plans_nrt_parser.add_argument(
        "--os",
        dest="os",
        default=None,
        help="操作系统 slug（默认 ubuntu-22.04）",
    )
    plans_nrt_parser.add_argument("--family", help="GPU 家族正则 (如 A40|L40S|A16)", default=None)
    plans_nrt_parser.add_argument("--min-vram", type=int, help="最小 GPU 显存 (GB)", default=None)
    plans_nrt_parser.add_argument(
        "--only-available",
        dest="only_available",
        action="store_true",
        default=True,
        help="仅显示指定 region 当前标记为可用的套餐（默认启用）",
    )
    plans_nrt_parser.add_argument(
        "--include-unavailable",
        dest="only_available",
        action="store_false",
        help="包含暂未标记为可用的套餐",
    )
    plans_nrt_parser.add_argument("--json", action="store_true", help="以 JSON 形式输出")
    plans_nrt_parser.add_argument("--verbose", action="store_true", help="打印调试信息")
    plans_nrt_parser.add_argument("--quiet", action="store_true", help="仅输出 plan_id")
    plans_nrt_parser.add_argument("--legacy", action="store_true", help=argparse.SUPPRESS)
    # ==== BEGIN: OnePass Patch · R4.4 (arg flags) ====
    plans_nrt_parser.add_argument(
        "--use-last",
        action="store_true",
        help="Prefill missing params from last_used cache",
    )
    # ==== END: OnePass Patch · R4.4 (arg flags) ====
    _add_dry_run(plans_nrt_parser)
    plans_nrt_parser.set_defaults(func=cmd_plans_nrt, legacy=False)

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
