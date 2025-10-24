#!/usr/bin/env python3
"""Utility helpers for provisioning a Vultr instance and preparing it for
PrivateTunnel's WireGuard automation.

The script intentionally keeps its dependencies limited to Python's standard
library so it can run in most environments without additional setup.  Vultr's
API token is read from the ``VULTR_API_TOKEN`` environment variable to avoid
committing secrets to disk.

Example usage:

    export VULTR_API_TOKEN="..."
    python scripts/vultr_provision.py \
        --region ams \
        --plan vc2-1c-1gb \
        --os-id 2136 \
        --label private-tunnel-demo \
        --ssh-key-id 01234567-89ab-cdef-0123-456789abcdef \
        --wait-ssh \
        --sync-provision

Refer to https://www.vultr.com/api/ for up-to-date values for ``region``,
``plan`` and ``os_id`` or ``image_id``.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


API_BASE = "https://api.vultr.com/v2"


class VultrAPIError(RuntimeError):
    """Raised when Vultr's API returns an error response."""

    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        message = f"Vultr API request failed with status {status}: {payload}"
        super().__init__(message)


def _api_request(
    method: str,
    path: str,
    token: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Perform a Vultr API call.

    Args:
        method: HTTP verb.
        path: API path starting with a slash.
        token: API token from the ``VULTR_API_TOKEN`` environment variable.
        params: Optional query parameters.
        body: Optional JSON body.

    Returns:
        The decoded JSON response as a dictionary.

    Raises:
        VultrAPIError: if the API returns a non-2xx response.
    """

    if not path.startswith("/"):
        raise ValueError("API path must start with '/'")

    url = API_BASE + path
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    data: Optional[bytes] = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)

    try:
        with urllib.request.urlopen(request) as response:
            content = response.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        error_payload: Any
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
        except Exception:  # pragma: no cover - defensive fallback
            error_payload = exc.reason
        raise VultrAPIError(exc.code, error_payload) from exc


def create_instance(token: str, args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "region": args.region,
        "plan": args.plan,
    }

    if args.os_id is not None and args.image_id is not None:
        raise ValueError("--os-id and --image-id are mutually exclusive")
    if args.os_id is not None:
        payload["os_id"] = args.os_id
    if args.image_id is not None:
        payload["image_id"] = args.image_id
    if args.app_id is not None:
        payload["app_id"] = args.app_id
    if args.label:
        payload["label"] = args.label
    if args.hostname:
        payload["hostname"] = args.hostname
    if args.tag:
        payload["tag"] = args.tag
    if args.firewall_group_id:
        payload["firewall_group_id"] = args.firewall_group_id
    if args.ssh_key_id:
        deduped = list(dict.fromkeys(args.ssh_key_id))
        payload["sshkey_ids"] = deduped
        payload["sshkey_id"] = deduped
    if args.enable_ipv6:
        payload["enable_ipv6"] = True
    if args.enable_private_network:
        payload["enable_private_network"] = True
    if args.backups:
        payload["backups"] = args.backups
    if args.user_data:
        payload["user_data"] = args.user_data
    if args.vpc_ids:
        payload["vpc_ids"] = list(dict.fromkeys(args.vpc_ids))

    response = _api_request("POST", "/instances", token, body=payload)
    return response["instance"]


def wait_for_instance_active(
    token: str,
    instance_id: str,
    *,
    poll_interval: int = 10,
    timeout: int = 600,
) -> Dict[str, Any]:
    """Poll the instance until it becomes ``active`` or ``timeout`` expires."""

    deadline = time.monotonic() + timeout
    while True:
        instance = _api_request("GET", f"/instances/{instance_id}", token)["instance"]
        status = instance.get("status")
        if status == "active":
            return instance
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Instance {instance_id} did not become active within {timeout} seconds"
            )
        time.sleep(poll_interval)


def wait_for_ssh(
    ip_address: str,
    port: int,
    *,
    timeout: int,
    poll_interval: int = 5,
) -> None:
    """Block until an SSH connection can be established."""

    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((ip_address, port), timeout=10):
                return
        except (OSError, socket.timeout):
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"SSH on {ip_address}:{port} was not reachable within {timeout} seconds"
                )
            time.sleep(poll_interval)


def sync_provision_directory(
    target_ip: str,
    *,
    ssh_user: str,
    ssh_port: int,
    local_path: Path,
    remote_path: Path,
    extra_rsync_args: Iterable[str],
) -> None:
    """Use ``rsync`` over SSH to copy ``server/provision`` to the instance."""

    if not local_path.exists():
        raise FileNotFoundError(f"Local provision directory {local_path} does not exist")

    remote = f"{ssh_user}@{target_ip}:{remote_path}"
    cmd = [
        "rsync",
        "-az",
        "-e",
        f"ssh -p {ssh_port}",
        *(extra_rsync_args or ()),
        str(local_path) + "/",
        remote,
    ]
    subprocess.run(cmd, check=True)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--region", required=True, help="Vultr region slug (e.g. ams)")
    parser.add_argument("--plan", required=True, help="Vultr plan ID (e.g. vc2-1c-1gb)")
    parser.add_argument("--os-id", type=int, help="Operating system ID")
    parser.add_argument("--image-id", help="Custom image ID")
    parser.add_argument("--app-id", type=int, help="Application ID")
    parser.add_argument("--label", help="Label displayed in the Vultr dashboard")
    parser.add_argument("--hostname", help="Hostname assigned to the instance")
    parser.add_argument("--tag", help="Tag applied to the instance")
    parser.add_argument("--firewall-group-id", help="Firewall group to attach")
    parser.add_argument(
        "--ssh-key-id",
        action="append",
        help="SSH key ID to embed. Repeat for multiple keys.",
    )
    parser.add_argument(
        "--enable-ipv6",
        action="store_true",
        help="Enable IPv6 for the instance",
    )
    parser.add_argument(
        "--enable-private-network",
        action="store_true",
        help="Attach to your private network",
    )
    parser.add_argument("--backups", choices=["enabled", "disabled"], help="Backups mode")
    parser.add_argument(
        "--user-data-file",
        type=Path,
        help="Path to a file containing cloud-init or user-data payload",
    )
    parser.add_argument(
        "--vpc-id",
        dest="vpc_ids",
        action="append",
        help="VPC ID to attach. Repeatable.",
    )

    parser.add_argument(
        "--wait-ssh",
        action="store_true",
        help="Wait for SSH to become reachable after the instance is active",
    )
    parser.add_argument("--ssh-port", type=int, default=22, help="SSH port to probe")
    parser.add_argument("--ssh-timeout", type=int, default=600, help="SSH wait timeout")

    parser.add_argument(
        "--sync-provision",
        action="store_true",
        help=(
            "After SSH is reachable, sync server/provision to the instance using rsync. "
            "Implies --wait-ssh."
        ),
    )
    parser.add_argument(
        "--remote-path",
        type=Path,
        default=Path("~/private-tunnel/server/provision"),
        help="Remote path to copy the provision scripts to",
    )
    parser.add_argument(
        "--local-path",
        type=Path,
        default=Path("server/provision"),
        help="Local provision directory to sync",
    )
    parser.add_argument(
        "--ssh-user",
        default="root",
        help="SSH username used for rsync when --sync-provision is set",
    )
    parser.add_argument(
        "--rsync-arg",
        dest="rsync_args",
        action="append",
        default=[],
        help="Additional rsync arguments (repeatable)",
    )
    parser.add_argument(
        "--instance-timeout",
        type=int,
        default=900,
        help="Maximum time to wait for the instance to become active",
    )
    parser.add_argument(
        "--instance-poll",
        type=int,
        default=10,
        help="Polling interval in seconds when waiting for the instance",
    )

    args = parser.parse_args(argv)

    if args.user_data_file:
        args.user_data = args.user_data_file.read_text()
    else:
        args.user_data = None

    if args.sync_provision:
        args.wait_ssh = True

    return args


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("VULTR_API_TOKEN")
    if not token:
        print("Error: VULTR_API_TOKEN environment variable is not set", file=sys.stderr)
        return 1

    try:
        instance = create_instance(token, args)
    except (VultrAPIError, ValueError) as exc:
        print(f"Failed to create instance: {exc}", file=sys.stderr)
        return 1

    instance_id = instance["id"]
    print(f"Created instance {instance_id} in status {instance.get('status')}")

    try:
        instance = wait_for_instance_active(
            token,
            instance_id,
            poll_interval=args.instance_poll,
            timeout=args.instance_timeout,
        )
    except TimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    ipv4 = instance.get("main_ip")
    print(f"Instance {instance_id} is active with IPv4 address {ipv4}")

    if args.wait_ssh and ipv4:
        try:
            wait_for_ssh(ipv4, args.ssh_port, timeout=args.ssh_timeout)
        except TimeoutError as exc:
            print(str(exc), file=sys.stderr)
            return 3
        print(f"SSH is reachable on {ipv4}:{args.ssh_port}")

        if args.sync_provision:
            remote_path = args.remote_path.expanduser()
            try:
                sync_provision_directory(
                    ipv4,
                    ssh_user=args.ssh_user,
                    ssh_port=args.ssh_port,
                    local_path=args.local_path,
                    remote_path=remote_path,
                    extra_rsync_args=args.rsync_args,
                )
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                print(f"Failed to sync provision directory: {exc}", file=sys.stderr)
                return 4
            print(
                "Provision directory synced. You can now SSH and run wg-install.sh, "
                "or trigger it via cloud-init/user-data."
            )

    print("All done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
