"""Vultr API 客户端（标准库实现）。

用途：封装 Vultr v2 API 的常用操作，供 cloud_vultr_cli.py 调用。
环境变量：需要有效的 ``VULTR_API_KEY``。
示例：
    >>> from deploy.cloud.vultr.vultr_api import list_instances
    >>> list_instances("your-api-key")
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

API_BASE = "https://api.vultr.com/v2"


class VultrError(RuntimeError):
    """HTTP 调用失败时抛出的异常。"""


def _build_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return API_BASE + path


def _request(method: str, path: str, api_key: str, data: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    """发起 HTTP 请求并解析 JSON 响应。"""

    url = _build_url(path)
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if query:
            url = f"{url}?{query}"
    body: bytes | None = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method.upper())
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read().decode("utf-8")
            if not payload:
                return {}
            return json.loads(payload)
    except urllib.error.HTTPError as exc:  # pragma: no cover - 运行时错误路径
        snippet = exc.read().decode("utf-8", "replace")[:300]
        raise VultrError(f"HTTP {exc.code} {exc.reason}: {snippet}") from exc


def api_get(path: str, params: Optional[dict], api_key: str) -> dict:
    """执行 GET 请求。"""

    return _request("GET", path, api_key, params=params)


def api_post(path: str, payload: dict, api_key: str) -> dict:
    """执行 POST 请求。"""

    return _request("POST", path, api_key, data=payload)


def paginate(path: str, params: Optional[dict], api_key: str) -> List[dict]:
    """获取分页资源的完整列表。"""

    results: List[dict] = []
    next_path: Optional[str] = path
    while next_path:
        resp = api_get(next_path, params if next_path == path else None, api_key)
        data = resp.get("data") or resp.get(path.strip("/").replace("/", "_"))
        if isinstance(data, list):
            results.extend(data)
        meta = resp.get("meta") or {}
        next_path = meta.get("next")
    return results


def list_regions(api_key: str) -> List[dict]:
    """列出所有 region。"""

    return paginate("/regions", None, api_key)


def list_plans(api_key: str) -> List[dict]:
    """列出所有 plan。"""

    return paginate("/plans", None, api_key)


def list_os(api_key: str) -> List[dict]:
    """列出所有操作系统模板。"""

    return paginate("/os", None, api_key)


def list_instances(api_key: str) -> List[dict]:
    """列出账户中的所有实例。"""

    return paginate("/instances", None, api_key)


def get_instance(instance_id: str, api_key: str) -> dict:
    """获取单个实例信息。"""

    return api_get(f"/instances/{instance_id}", None, api_key)


def list_ssh_keys(api_key: str) -> List[dict]:
    """列出所有 SSH 公钥。"""

    return paginate("/ssh-keys", None, api_key)


def create_ssh_key(name: str, public_key: str, api_key: str) -> dict:
    """上传新的 SSH 公钥。"""

    payload = {"name": name, "ssh_key": public_key}
    return api_post("/ssh-keys", payload, api_key)


def create_instance(
    region: str,
    plan: str,
    os_id: int,
    label: str,
    sshkey_ids: List[str],
    api_key: str,
    tag: Optional[str] = None,
) -> dict:
    """创建 VPS 实例。"""

    payload: Dict[str, Any] = {
        "region": region,
        "plan": plan,
        "os_id": os_id,
        "label": label,
        "sshkey_ids": sshkey_ids,
    }
    if tag:
        payload["tag"] = tag
    return api_post("/instances", payload, api_key)


def wait_for_instance_active(
    instance_id: str,
    timeout_s: int,
    poll_s: int,
    api_key: str,
) -> dict:
    """轮询等待实例进入 active 状态。"""

    deadline = time.monotonic() + max(timeout_s, 1)
    while True:
        info = get_instance(instance_id, api_key)
        status = info.get("instance", {}).get("status")
        if status == "active":
            return info
        if time.monotonic() >= deadline:
            raise VultrError(f"实例 {instance_id} 在 {timeout_s} 秒内未就绪，当前状态：{status}")
        time.sleep(max(poll_s, 1))

