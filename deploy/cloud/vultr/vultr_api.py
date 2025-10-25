"""Vultr API 客户端（标准库实现）。

用途：封装 Vultr v2 API 的常用操作，供 cloud_vultr_cli.py 调用。
环境变量：需要有效的 ``VULTR_API_KEY``。
示例：
    >>> from deploy.cloud.vultr.vultr_api import list_instances
    >>> list_instances("your-api-key")
"""
from __future__ import annotations

import json
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

API_BASE = "https://api.vultr.com/v2"

GPU_KEYWORDS: tuple[str, ...] = ("GPU", "A40", "A16", "L40S", "A100", "L4")


class VultrError(RuntimeError):
    """HTTP 调用失败时抛出的异常。"""


def _format_error_snippet(body: str, *, min_lines: int = 3, max_lines: int = 5, width: int = 120) -> str:
    """将 HTTP 错误响应裁剪为 3~5 行便于调试的片段。"""

    text = body.strip()
    if not text:
        return ""
    raw_lines = text.splitlines()
    snippet_lines: list[str] = []
    for raw in raw_lines:
        wrapped = textwrap.wrap(raw, width=width) or [raw[:width]]
        for part in wrapped:
            snippet_lines.append(part)
            if len(snippet_lines) >= max_lines:
                break
        if len(snippet_lines) >= max_lines:
            break
    if len(snippet_lines) < min_lines:
        expanded = textwrap.wrap(text, width=width) or [text[:width]]
        for part in expanded:
            if part not in snippet_lines:
                snippet_lines.append(part)
            if len(snippet_lines) >= min_lines:
                break
    return "\n".join(snippet_lines[:max_lines])


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
        body = exc.read().decode("utf-8", "replace")
        snippet = _format_error_snippet(body)
        message = f"HTTP {exc.code} {exc.reason}: {_build_url(path)}"
        if snippet:
            message = f"{message}\n{snippet}"
        raise VultrError(message) from exc


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


def _plan_matches_gpu_keywords(plan: dict) -> bool:
    name = str(plan.get("name", ""))
    description = str(plan.get("description") or "")
    haystack = f"{name} {description}".upper()
    return any(keyword in haystack for keyword in GPU_KEYWORDS)


def extract_region_availability(plan: dict) -> Dict[str, bool]:
    availability: Dict[str, bool] = {}
    candidates = [
        plan.get("available_in"),
        plan.get("available_locations"),
        plan.get("locations"),
        plan.get("regions"),
        plan.get("region_availability"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if isinstance(candidate, dict):
            for region, info in candidate.items():
                if isinstance(info, dict):
                    available = info.get("available")
                    if available is None:
                        available = info.get("is_available")
                    if available is None and "stock" in info:
                        available = str(info.get("stock")).lower() not in {"0", "soldout", "false"}
                elif isinstance(info, bool):
                    available = info
                else:
                    available = bool(info)
                availability[str(region)] = bool(available)
        elif isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, str):
                    availability[item] = True
                elif isinstance(item, dict):
                    region = item.get("id") or item.get("region") or item.get("code") or item.get("name")
                    if region:
                        available = item.get("available")
                        if available is None:
                            available = item.get("is_available")
                        if available is None and "stock" in item:
                            available = str(item.get("stock")).lower() not in {"0", "soldout", "false"}
                        availability[str(region)] = bool(True if available is None else available)
        elif isinstance(candidate, str):
            for token in candidate.split(","):
                token = token.strip()
                if token:
                    availability[token] = True
    return availability


def _region_available(availability: Dict[str, bool], region: str) -> bool:
    region_lower = region.lower()
    for code, available in availability.items():
        if str(code).lower() == region_lower:
            return bool(available)
    return False


def _filter_plans_by_region(plans: List[dict], region: str) -> List[dict]:
    filtered: List[dict] = []
    for plan in plans:
        availability = extract_region_availability(plan)
        if not availability:
            continue
        if _region_available(availability, region):
            filtered.append(plan)
    if filtered:
        return filtered
    # 如果所有 plan 都缺少该 region，可返回空列表以便上层给出提示。
    return []


def list_gpu_plans(region: str | None = None, *, api_key: str) -> List[dict]:
    """列出 Vultr GPU 计划，必要时回退到通用 plans 列表。"""

    params = {"region": region} if region else None
    try:
        plans = paginate("/plans/gpu", params, api_key)
    except VultrError as exc:
        message = str(exc).lower()
        if params and ("http 400" in message or "invalid" in message):
            try:
                plans = paginate("/plans/gpu", None, api_key)
            except VultrError:
                raise
            else:
                if region:
                    filtered = _filter_plans_by_region(plans, region)
                    if filtered:
                        return filtered
                return plans
        if "http 404" not in message and "not found" not in message and "invalid" not in message:
            raise
        plans = list_plans(api_key)
        plans = [plan for plan in plans if _plan_matches_gpu_keywords(plan)]
        if region:
            plans = [
                plan
                for plan in plans
                if _region_available(extract_region_availability(plan), region)
            ]
        return plans
    if region and params:
        filtered = _filter_plans_by_region(plans, region)
        if filtered:
            return filtered
    return plans


def resolve_os_id(slug: str, *, api_key: str) -> str:
    """将操作系统 slug/name 映射到 Vultr OS ID。"""

    normalized = slug.strip()
    if not normalized:
        raise VultrError("操作系统标识不能为空。")
    if normalized.isdigit():
        return normalized
    target = normalized.lower()
    os_list = list_os(api_key)
    for item in os_list:
        candidates = [
            item.get("slug"),
            item.get("name"),
            item.get("description"),
            item.get("family"),
        ]
        for value in candidates:
            if value and str(value).lower() == target:
                return str(item.get("id"))
    raise VultrError(f"未找到匹配的操作系统：{slug}")


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


def delete_instance(instance_id: str, api_key: str) -> dict:
    """删除指定实例。"""

    return _request("DELETE", f"/instances/{instance_id}", api_key)


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

