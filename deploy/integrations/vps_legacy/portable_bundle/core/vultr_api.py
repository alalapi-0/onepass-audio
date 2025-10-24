"""Vultr API helpers used by the Windows one-click workflow."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

BASE_URL = "https://api.vultr.com/v2"
UBUNTU_22_04_OSID = 1743


class VultrAPIError(RuntimeError):
    """Raised when the Vultr API returns an unexpected response."""


def _headers(api_key: str) -> Dict[str, str]:
    api_key = api_key.strip()
    if not api_key:
        raise VultrAPIError("VULTR_API_KEY 未设置或为空。")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _format_http_error(status: int, text: str, reason: str = "") -> str:
    detail = (text or "").strip() or (reason or "")
    if status == 401:
        hint = "请检查 Vultr API Access Control 是否放行当前公网 IPv4/IPv6。"
        base = f"HTTP 401 {detail}" if detail else "HTTP 401"
        return f"{base}。{hint}"
    if detail:
        return f"HTTP {status} {detail}"
    return f"HTTP {status}"


def _request(method: str, path: str, api_key: str, **kwargs: Any) -> requests.Response:
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.request(method, url, headers=_headers(api_key), timeout=30, **kwargs)
    except requests.RequestException as exc:  # pragma: no cover - network errors
        response = getattr(exc, "response", None)
        if response is not None:
            message = _format_http_error(response.status_code, getattr(response, "text", ""), response.reason)
            raise VultrAPIError(f"请求 {method} {url} 失败：{message}") from exc
        raise VultrAPIError(f"请求 {method} {url} 失败：{exc}") from exc
    if resp.status_code >= 400:
        message = _format_http_error(resp.status_code, resp.text, resp.reason)
        raise VultrAPIError(f"请求 {method} {url} 失败：{message}")
    return resp


def _paginate(endpoint: str, api_key: str) -> Iterable[Dict[str, Any]]:
    cursor: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"per_page": 200}
        if cursor:
            params["cursor"] = cursor
        resp = _request("GET", endpoint, api_key, params=params)
        payload = resp.json()
        items: List[Dict[str, Any]] = payload.get(endpoint.strip("/").replace("-", "_"), [])
        for item in items:
            yield item
        cursor = payload.get("meta", {}).get("links", {}).get("next")
        if not cursor:
            break


def ensure_ssh_key(api_key: str, pubkey_text: str, name: str) -> str:
    """Ensure ``pubkey_text`` exists on Vultr and return its id."""

    normalized = pubkey_text.strip()
    if not normalized:
        raise VultrAPIError("公钥内容为空，无法注册 SSH Key。")

    for item in _paginate("/ssh-keys", api_key):
        if item.get("ssh_key", "").strip() == normalized:
            print(f"✓ 复用已存在 SSH Key：{item.get('name', '') or item.get('id')}")
            return str(item["id"])

    payload = {"name": name, "ssh_key": normalized}
    resp = _request("POST", "/ssh-keys", api_key, json=payload)
    data = resp.json().get("ssh_key", {})
    ssh_id = data.get("id")
    if not ssh_id:
        raise VultrAPIError(f"创建 SSH Key 返回异常：{json.dumps(resp.json(), ensure_ascii=False)}")
    print(f"✓ 新建 SSH Key 成功：{name}")
    return str(ssh_id)


def _get_snapshot_info(api_key: str, snapshot_id: str) -> Dict[str, Any]:
    for snap in _paginate("/snapshots", api_key):
        if snap.get("id") == snapshot_id:
            return snap
    raise VultrAPIError(f"未找到指定快照：{snapshot_id}")


def pick_snapshot(api_key: str, snapshot_id_env: Optional[str]) -> Optional[str]:
    """Pick a snapshot ID based on ``snapshot_id_env`` or fall back to latest."""

    if snapshot_id_env:
        info = _get_snapshot_info(api_key, snapshot_id_env)
        desc = info.get("description") or info.get("id")
        print(f"✓ 使用环境变量快照 ID：{info['id']} ({desc})")
        return info["id"]

    snapshots = list(_paginate("/snapshots", api_key))
    if not snapshots:
        print("⚠️ 未找到任何快照，将使用官方镜像创建实例。")
        return None

    latest = max(snapshots, key=lambda item: item.get("date_created", ""))
    desc = latest.get("description") or latest.get("id")
    print(f"✓ 自动选择最新快照：{latest['id']} ({desc})")
    return latest["id"]


def _get_plan_info(api_key: str, plan: str) -> Dict[str, Any]:
    for item in _paginate("/plans", api_key):
        if item.get("id") == plan:
            return item
    raise VultrAPIError(f"未找到指定套餐计划：{plan}")


def _get_disk_size(item: Dict[str, Any]) -> Optional[int]:
    disk = item.get("disk") or item.get("disk_gb")
    if disk is None:
        return None
    try:
        return int(disk)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _check_snapshot_size(api_key: str, plan: str, snapshot_id: Optional[str]) -> None:
    if not snapshot_id:
        return

    snapshot = _get_snapshot_info(api_key, snapshot_id)
    snap_size = snapshot.get("size_gigabytes") or snapshot.get("size")
    try:
        snap_size_val = int(float(str(snap_size))) if snap_size is not None else None
    except ValueError:  # pragma: no cover - defensive
        snap_size_val = None

    plan_info = _get_plan_info(api_key, plan)
    plan_disk = _get_disk_size(plan_info)

    if snap_size_val and plan_disk and snap_size_val > plan_disk:
        raise VultrAPIError(
            f"选择的快照（约 {snap_size_val} GB）大于套餐 {plan} 的磁盘容量（{plan_disk} GB）。"
        )


def create_instance(
    api_key: str,
    region: str,
    plan: str,
    sshkey_ids: List[str],
    snapshot_id: Optional[str],
    *,
    label: str = "PrivateTunnel-Auto",
) -> Dict[str, Any]:
    """Create an instance with optional snapshot and SSH keys."""

    _check_snapshot_size(api_key, plan, snapshot_id)

    payload: Dict[str, Any] = {
        "region": region,
        "plan": plan,
        "label": label,
    }
    if sshkey_ids:
        unique_ids = list(dict.fromkeys(sshkey_ids))
        payload["sshkey_ids"] = unique_ids
        payload["sshkey_id"] = unique_ids
    if snapshot_id:
        payload["snapshot_id"] = snapshot_id
    else:
        payload["os_id"] = UBUNTU_22_04_OSID

    resp = _request("POST", "/instances", api_key, json=payload)
    data = resp.json().get("instance", {})
    if not data.get("id"):
        raise VultrAPIError(f"创建实例响应异常：{json.dumps(resp.json(), ensure_ascii=False)}")
    print(f"✓ 已创建实例：{data.get('id')}，等待启动中 ...")
    return data


def reinstall_instance(
    api_key: str,
    instance_id: str,
    sshkey_ids: Optional[List[str]] | None = None,
    *,
    user_data: Optional[str] = None,
) -> None:
    """Trigger ``Reinstall SSH Keys`` for an instance."""

    payload: Dict[str, Any] = {}
    if sshkey_ids:
        unique_ids = list(dict.fromkeys(sshkey_ids))
        payload["sshkey_ids"] = unique_ids
        payload["sshkey_id"] = unique_ids
    if user_data:
        payload["user_data"] = user_data
    _request("POST", f"/instances/{instance_id}/reinstall", api_key, json=payload)


def wait_instance_ready(api_key: str, instance_id: str, timeout: int = 600) -> Dict[str, Any]:
    """Poll the Vultr API until the instance becomes active and running."""

    deadline = time.time() + timeout
    last_payload: Dict[str, Any] = {}
    while time.time() < deadline:
        resp = _request("GET", f"/instances/{instance_id}", api_key)
        payload = resp.json().get("instance", {})
        last_payload = payload
        status = payload.get("status")
        power = payload.get("power_status")
        ip_addr = payload.get("main_ip")
        if status == "active" and power == "running" and ip_addr:
            print(f"✓ 实例已就绪：{ip_addr}")
            return payload
        time.sleep(5)

    raise VultrAPIError(
        "等待实例启动超时。最后状态："
        f"{json.dumps(last_payload, ensure_ascii=False)}"
    )


def auto_create(
    api_key: str,
    pubkey_path: str,
    key_name: str,
    snapshot_id: Optional[str] = None,
    region: str = "nrt",
    plan: str = "vc2-1c-1gb",
) -> Dict[str, Any]:
    """High-level helper that performs the whole creation flow."""

    with open(pubkey_path, "r", encoding="utf-8") as fh:
        pubkey_text = fh.read()

    ssh_id = ensure_ssh_key(api_key, pubkey_text, key_name)
    snap = pick_snapshot(api_key, snapshot_id)
    instance = create_instance(api_key, region, plan, [ssh_id], snap)
    info = wait_instance_ready(api_key, instance["id"])
    return info


__all__ = [
    "VultrAPIError",
    "ensure_ssh_key",
    "pick_snapshot",
    "create_instance",
    "reinstall_instance",
    "wait_instance_ready",
    "auto_create",
]
