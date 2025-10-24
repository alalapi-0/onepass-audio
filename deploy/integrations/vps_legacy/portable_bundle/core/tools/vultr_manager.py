from __future__ import annotations

import json
import os
import socket
import time
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager

API = "https://api.vultr.com/v2"
UBUNTU_22_04_OSID = 1743  # 如有差异，请在 Vultr 控制台或官方文档中查询实际 OSID


class VultrError(RuntimeError):
    """Custom exception for Vultr API operations."""


class IPv4HTTPAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = HTTPAdapter()._socket_options
        self.poolmanager = PoolManager(*args, **kwargs, num_pools=10)

    def get_connection(self, url, proxies=None):
        return super().get_connection(url, proxies=proxies)

    def _get_conn(self, timeout=None, pool=None, url=None):
        return super()._get_conn(timeout=timeout, pool=pool, url=url)

    def proxy_manager_for(self, *args, **kwargs):
        return super().proxy_manager_for(*args, **kwargs)

    def _prepare_conn(self, conn):
        # 强制 AF_INET
        conn.pool_kwargs["socket_options"] = [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]
        return super()._prepare_conn(conn)


def _hdr(api_key: str) -> Dict[str, str]:
    """Build request headers for Vultr API calls."""
    if not api_key:
        raise VultrError("VULTR_API_KEY is empty")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _session(api_key: str, force_ipv4: bool = False) -> requests.Session:
    s = requests.Session()
    if force_ipv4 or os.environ.get("FORCE_VULTR_IPV4") == "1":
        adapter = IPv4HTTPAdapter()
        s.mount("https://", adapter)
        s.mount("http://", adapter)
    s.headers.update(_hdr(api_key))
    return s


def _friendly_error_message(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        status = response.status_code
        text = (getattr(response, "text", "") or "").strip() or getattr(response, "reason", "")
        if status == 401:
            hint = "请检查 Vultr API Access Control 是否放行当前公网 IPv4/IPv6。"
            base = f"HTTP 401 {text}" if text else "HTTP 401"
            return f"{base}。{hint}"
        if text:
            return f"HTTP {status} {text}"
        return f"HTTP {status}"
    return str(exc)


def list_ssh_keys(api_key: str) -> list[Dict[str, Any]]:
    """Return all SSH keys associated with the account."""

    session = _session(api_key)
    keys: list[Dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            response = session.get(f"{API}/ssh-keys", params=params, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:  # pragma: no cover - network
            message = _friendly_error_message(exc)
            raise VultrError(f"List SSH keys failed: {message}") from exc

        payload = response.json()
        keys.extend(payload.get("ssh_keys", []))
        cursor = payload.get("meta", {}).get("links", {}).get("next")
        if not cursor:
            break
    return keys


def list_instances(api_key: str) -> list[Dict[str, Any]]:
    """Return all VPS instances associated with the account."""

    session = _session(api_key)
    instances: list[Dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            response = session.get(f"{API}/instances", params=params, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:  # pragma: no cover - network
            message = _friendly_error_message(exc)
            raise VultrError(f"List instances failed: {message}") from exc

        payload = response.json()
        instances.extend(payload.get("instances", []))
        cursor = payload.get("meta", {}).get("links", {}).get("next")
        if not cursor:
            break
    return instances


def create_ssh_key(api_key: str, name: str, key_text: str) -> Dict[str, Any]:
    """Create a new SSH key in Vultr."""

    session = _session(api_key)
    body = {"name": name, "ssh_key": key_text}
    try:
        response = session.post(f"{API}/ssh-keys", json=body, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - network
        message = _friendly_error_message(exc)
        raise VultrError(f"Create SSH key failed: {message}") from exc
    return response.json().get("ssh_key", {})


def create_instance(
    api_key: str,
    region: str = "nrt",
    plan: str = "vc2-1c-1gb",
    snapshot_id: str | None = None,
    label: str = "privatetunnel-oc",
    *,
    sshkey_ids: Optional[list[str]] = None,
    user_data: str | None = None,
) -> Dict[str, Any]:
    """Create a Vultr instance and return the raw instance payload."""

    body: Dict[str, Any] = {
        "region": region,
        "plan": plan,
        "label": label,
        "backups": "disabled",
        "enable_ipv6": True,
    }
    if snapshot_id:
        body["snapshot_id"] = snapshot_id
    else:
        body["os_id"] = UBUNTU_22_04_OSID
    if sshkey_ids:
        deduped_ids = [str(item).strip() for item in sshkey_ids if str(item).strip()]
        deduped_ids = list(dict.fromkeys(deduped_ids))
        if deduped_ids:
            body["sshkey_ids"] = deduped_ids
            body["sshkey_id"] = deduped_ids
    if user_data:
        body["user_data"] = user_data

    session = _session(api_key)

    try:
        response = session.post(
            f"{API}/instances",
            json=body,
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        message = _friendly_error_message(exc)
        raise VultrError(f"Create instance failed: {message}") from exc

    data = response.json()
    instance = data.get("instance") or {}
    instance_id = instance.get("id")
    if not instance_id:
        raise VultrError(f"Unexpected create response: {json.dumps(data, ensure_ascii=False)}")
    return instance


def wait_instance_active(
    api_key: str,
    instance_id: str,
    timeout: int = 600,
    interval: int = 10,
) -> Dict[str, str]:
    """Poll instance status until it becomes active and returns its IP."""

    start = time.time()
    last_state: Dict[str, Any] = {}
    session = _session(api_key)

    while time.time() - start < timeout:
        try:
            response = session.get(
                f"{API}/instances/{instance_id}",
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            last_state = {"error": _friendly_error_message(exc)}
            time.sleep(interval)
            continue

        payload = response.json().get("instance", {})
        status = payload.get("status")
        ip = payload.get("main_ip")
        if status == "active" and ip:
            return {"id": instance_id, "ip": ip, "status": status}
        last_state = {"status": status, "ip": ip}
        time.sleep(interval)

    raise VultrError(f"Wait active timeout. Last state={json.dumps(last_state, ensure_ascii=False)}")


def destroy_instance(api_key: str, instance_id: str) -> None:
    """Destroy a Vultr instance."""

    session = _session(api_key)

    try:
        response = session.delete(
            f"{API}/instances/{instance_id}",
            timeout=30,
        )
        if response.status_code not in (200, 204):
            response.raise_for_status()
    except requests.RequestException as exc:
        message = _friendly_error_message(exc)
        raise VultrError(f"Destroy failed: {message}") from exc


def reinstall_with_ssh_keys(
    api_key: str,
    instance_id: str,
    sshkey_ids: Optional[list[str]] = None,
    user_data: str | None = None,
) -> None:
    """Trigger ``Reinstall SSH Keys`` for an instance.

    The operation wipes the instance disk.  ``sshkey_ids`` must contain at least
    one SSH key id to inject during reinstall.
    """

    body: Dict[str, Any] = {}
    if sshkey_ids:
        deduped_ids = [str(item).strip() for item in sshkey_ids if str(item).strip()]
        deduped_ids = list(dict.fromkeys(deduped_ids))
        if deduped_ids:
            body["sshkey_ids"] = deduped_ids
            body["sshkey_id"] = deduped_ids
    if user_data:
        body["user_data"] = user_data

    session = _session(api_key)
    try:
        response = session.post(
            f"{API}/instances/{instance_id}/reinstall",
            json=body,
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - network
        message = _friendly_error_message(exc)
        raise VultrError(f"Reinstall failed: {message}") from exc
