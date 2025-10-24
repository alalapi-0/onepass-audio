#!/usr/bin/env python3
"""Generate WireGuard client configuration from validated JSON input.

Typical usage::

    python3 core/tools/generate_wg_conf.py \
        --schema core/config-schema.json \
        --in core/examples/minimal.json \
        --out /tmp/iphone.conf --force

The script reuses the JSON schema validator to ensure the configuration is
sound before rendering a WireGuard client configuration.  Existing output files
are left untouched unless ``--force`` is passed.  Sensitive values such as the
client private key are only written to the target file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_config import (  # type: ignore  # pylint: disable=wrong-import-position
    load_json_file,
    validate_json,
)

JSON = Dict[str, Any]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Generate a WireGuard client configuration from PrivateTunnel JSON.",
    )
    parser.add_argument("--schema", required=True, type=Path, help="Path to the JSON schema.")
    parser.add_argument("--in", dest="input_path", required=True, type=Path, help="Input JSON file.")
    parser.add_argument(
        "--out",
        dest="output_path",
        required=True,
        type=Path,
        help="Destination for the rendered WireGuard configuration.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    return parser.parse_args(argv)


def ensure_output_path(path: Path, force: bool) -> None:
    """Abort if *path* exists and overwriting is not allowed."""

    if path.exists() and not force:
        raise FileExistsError(
            f"Refusing to overwrite existing file {path}. Use --force to replace it."
        )
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def render_interface_section(client: JSON) -> str:
    """Render the ``[Interface]`` section based on client parameters."""

    lines = ["[Interface]"]
    lines.append(f"PrivateKey = {client['private_key']}")
    lines.append(f"Address = {client['address']}")

    dns_values = ", ".join(client["dns"])
    lines.append(f"DNS = {dns_values}")

    if "mtu" in client:
        lines.append(f"MTU = {client['mtu']}")

    return "\n".join(lines)


def render_peer_section(endpoint: JSON, routing: JSON, keepalive_default: int = 25) -> str:
    """Render the ``[Peer]`` section using endpoint and routing details."""

    lines = ["[Peer]"]
    lines.append(f"PublicKey = {endpoint['public_key']}")
    lines.append(f"Endpoint = {endpoint['host']}:{endpoint['port']}")

    mode = routing["mode"]
    if mode == "global":
        allowed_ips = ", ".join(routing["allowed_ips"])
        lines.append(f"AllowedIPs = {allowed_ips}")
    else:
        lines.append("# whitelist mode requested; replace AllowedIPs after IP expansion.")
        lines.append("AllowedIPs = 0.0.0.0/0, ::/0")

    keepalive = keepalive_default
    if keepalive:
        lines.append(f"PersistentKeepalive = {keepalive}")

    return "\n".join(lines)


def render_configuration(document: JSON) -> str:
    """Convert the validated configuration dictionary into WireGuard text."""

    client = document["client"]
    routing = document["routing"]
    endpoint = document["endpoint"]

    sections = []
    if routing["mode"] == "whitelist":
        sections.append(
            "# WARNING: whitelist mode requested but IP expansion not applied in v1\n"
            "# AllowedIPs currently fallback to full-tunnel until Round 8 tooling lands."
        )

    interface_section = render_interface_section(client)
    sections.append(interface_section)

    keepalive_value = client.get("keepalive", 25)
    peer_section = render_peer_section(endpoint, routing, keepalive_value)
    sections.append(peer_section)

    return "\n\n".join(sections) + "\n"


def render_configuration_from_files(schema_path: Path, input_path: Path) -> str:
    """Load and validate JSON files before rendering a WireGuard configuration."""

    schema = load_json_file(schema_path)
    document = load_json_file(input_path)

    validate_json(document, schema)

    return render_configuration(document)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        ensure_output_path(args.output_path, args.force)
    except FileExistsError as exc:  # pragma: no cover - trivial branch
        raise SystemExit(str(exc)) from exc

    config_text = render_configuration_from_files(args.schema, args.input_path)
    args.output_path.write_text(config_text, encoding="utf-8")

    print(f"WireGuard configuration written to {args.output_path}.")
    print("Remember to protect the file permissions before distributing it to devices.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
