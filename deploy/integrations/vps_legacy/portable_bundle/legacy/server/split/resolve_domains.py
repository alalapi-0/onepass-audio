#!/usr/bin/env python3
"""Resolve domain groups into aggregated CIDR blocks for PrivateTunnel split routing.

This script parses ``domains.yaml`` and generates two state files:

``state/resolved.json``
    Verbose resolution results including timestamps, TTL hints, resolver
    metadata, and per-domain diagnostics.

``state/cidr.txt``
    Collapsed IPv4 CIDR ranges suitable for ipset/nftables.

The script favours the Python standard library. If ``dig`` is available it is
used for richer TTL data. Missing domains are reported but do not abort the
entire run so existing ipset state can continue serving traffic.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import logging
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "domains.yaml"
DEFAULT_STATE_DIR = SCRIPT_DIR / "state"
RESOLVED_JSON = DEFAULT_STATE_DIR / "resolved.json"
CIDR_FILE = DEFAULT_STATE_DIR / "cidr.txt"

DIG_PATH = shutil.which("dig")


class ConfigError(RuntimeError):
    """Raised when the YAML configuration cannot be parsed."""


@dataclass
class DomainResult:
    domain: str
    ipv4: Set[str] = field(default_factory=set)
    ipv6: Set[str] = field(default_factory=set)
    ttl: Optional[int] = None
    errors: List[str] = field(default_factory=list)
    resolvers: Set[str] = field(default_factory=set)

    def merge(self, other: "DomainResult") -> None:
        self.ipv4 |= other.ipv4
        self.ipv6 |= other.ipv6
        self.resolvers |= other.resolvers
        if other.ttl is not None:
            self.ttl = min(self.ttl, other.ttl) if self.ttl is not None else other.ttl
        self.errors.extend(other.errors)


# ---------------------------------------------------------------------------
# YAML parsing helpers (minimal subset to avoid non-standard dependencies)
# ---------------------------------------------------------------------------

def parse_scalar(value: str) -> object:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", ""}:
        return None
    if (value.startswith("\"") and value.endswith("\"")) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        if value.startswith("0") and len(value) > 1 and value.isdigit():
            return value
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value

def determine_container(lines: Sequence[str], start: int, current_indent: int) -> object:
    idx = start
    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.split("#", 1)[0].strip()
        if not stripped:
            idx += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= current_indent:
            return {}
        return [] if stripped.startswith("- ") else {}
    return {}

def simple_yaml_load(text: str) -> Dict[str, object]:
    result: Dict[str, object] = {}
    stack: List[Tuple[int, object]] = [(-1, result)]
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped_comment = line.split("#", 1)[0].rstrip("\n")
        if not stripped_comment.strip():
            continue
        indent = len(stripped_comment) - len(stripped_comment.lstrip(" "))
        content = stripped_comment.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if content.startswith("- "):
            if not isinstance(parent, list):
                raise ConfigError(
                    f"Line {idx + 1}: unexpected list item without a list parent"
                )
            parent.append(parse_scalar(content[2:].strip()))
            continue
        if ":" not in content:
            raise ConfigError(f"Line {idx + 1}: expected key: value pair")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value == "":
            container = determine_container(lines, idx + 1, indent)
            if isinstance(parent, dict):
                parent[key] = container
            else:
                raise ConfigError(
                    f"Line {idx + 1}: cannot attach key '{key}' to non-dict parent"
                )
            stack.append((indent, container))
        else:
            value = parse_scalar(raw_value)
            if isinstance(parent, dict):
                parent[key] = value
            else:
                raise ConfigError(
                    f"Line {idx + 1}: cannot attach key '{key}' to non-dict parent"
                )
    return result

def load_config(path: Path) -> Dict[str, object]:
    text = path.read_text(encoding="utf-8")
    try:  # Prefer PyYAML when available for better compatibility
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if data is None:
            return {}
        return data
    except ModuleNotFoundError:
        logging.info("PyYAML not installed; using simplified parser for %s", path)
        return simple_yaml_load(text)


# ---------------------------------------------------------------------------
# DNS resolution helpers
# ---------------------------------------------------------------------------

def resolve_with_dig(domain: str, record_type: str, resolver: Optional[str]) -> DomainResult:
    if DIG_PATH is None:
        raise FileNotFoundError("dig not available")
    cmd: List[str] = [DIG_PATH]
    if resolver:
        cmd.append(f"@{resolver}")
    cmd.extend([domain, record_type, "+nocmd", "+noall", "+answer"])
    logging.debug("Running command: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=8,
        check=False,
    )
    result = DomainResult(domain=domain)
    if proc.returncode != 0:
        result.errors.append(
            f"dig failed with code {proc.returncode}: {proc.stderr.strip()}"
        )
        return result
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        # Expected format: name ttl class type value
        _, ttl, _, rtype, value = parts[:5]
        if rtype.upper() != record_type.upper():
            continue
        if record_type.upper() == "A":
            result.ipv4.add(value)
        elif record_type.upper() == "AAAA":
            result.ipv6.add(value)
        try:
            ttl_int = int(ttl)
        except ValueError:
            ttl_int = None
        if ttl_int is not None:
            result.ttl = (
                min(result.ttl, ttl_int) if result.ttl is not None else ttl_int
            )
    if result.ipv4 or result.ipv6:
        result.resolvers.add(resolver or "system")
    return result

def resolve_with_socket(domain: str, family: socket.AddressFamily) -> DomainResult:
    result = DomainResult(domain=domain)
    try:
        infos = socket.getaddrinfo(domain, None, family, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        result.errors.append(f"getaddrinfo: {exc}")
        return result
    for info in infos:
        address = info[4][0]
        if family == socket.AF_INET:
            result.ipv4.add(address)
        else:
            result.ipv6.add(address)
    if result.ipv4 or result.ipv6:
        result.resolvers.add("system")
    return result

def resolve_domain(
    domain: str,
    resolvers: Sequence[str],
    include_ipv6: bool,
) -> DomainResult:
    aggregated = DomainResult(domain=domain)
    record_types = ["A"] + (["AAAA"] if include_ipv6 else [])
    for record_type in record_types:
        record_result = DomainResult(domain=domain)
        used_at_least_one = False
        for resolver in resolvers or [None]:
            try:
                partial = resolve_with_dig(domain, record_type, resolver)
            except (FileNotFoundError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                partial = resolve_with_socket(
                    domain,
                    socket.AF_INET if record_type == "A" else socket.AF_INET6,
                )
            if partial.ipv4 or partial.ipv6:
                record_result.merge(partial)
                used_at_least_one = True
        if not used_at_least_one and not (record_result.ipv4 or record_result.ipv6):
            record_result.errors.append(
                f"No {record_type} records resolved via provided resolvers"
            )
        aggregated.merge(record_result)
    return aggregated


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def ensure_state_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def collapse_ipv4(addresses: Iterable[str]) -> List[str]:
    networks = [ipaddress.IPv4Network(f"{ip}/32") for ip in sorted(set(addresses))]
    collapsed = ipaddress.collapse_addresses(networks)
    return [str(network) for network in collapsed]

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to domains.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--state-dir",
        default=str(DEFAULT_STATE_DIR),
        help="Directory to write state outputs (default: %(default)s)",
    )
    parser.add_argument(
        "--groups",
        help="Comma separated list of groups to resolve (default: all)",
    )
    parser.add_argument(
        "--resolve-ipv6",
        action="store_true",
        help="Force enable IPv6 resolution even if disabled in options",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser

def extract_domains(config: Dict[str, object], groups_filter: Optional[Set[str]]) -> Tuple[Dict[str, List[str]], Dict[str, object]]:
    groups_raw = config.get("groups")
    if not isinstance(groups_raw, dict):
        raise ConfigError("domains.yaml must define a 'groups' mapping")
    groups: Dict[str, List[str]] = {}
    for name, values in groups_raw.items():
        if groups_filter and name not in groups_filter:
            continue
        if values is None:
            continue
        if isinstance(values, list):
            domains = []
            for value in values:
                if isinstance(value, str) and value.strip():
                    domains.append(value.strip())
            if domains:
                groups[name] = domains
        else:
            raise ConfigError(f"Group '{name}' must be a list of domains")
    options = config.get("options")
    if options is None:
        options_dict: Dict[str, object] = {}
    elif isinstance(options, dict):
        options_dict = options
    else:
        raise ConfigError("options must be a mapping if present")
    return groups, options_dict

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="[%(levelname)s] %(message)s")

    config_path = Path(args.config).resolve()
    state_dir = Path(args.state_dir).resolve()
    ensure_state_dir(state_dir)

    if not config_path.exists():
        logging.error("Configuration file %s does not exist", config_path)
        return 2

    try:
        config = load_config(config_path)
    except (ConfigError, OSError) as exc:
        logging.error("Failed to load configuration: %s", exc)
        return 2

    groups_filter = None
    if args.groups:
        groups_filter = {name.strip() for name in args.groups.split(",") if name.strip()}
    groups, options = extract_domains(config, groups_filter)

    if not groups:
        logging.error("No domains selected. Check groups filter or configuration contents.")
        return 3

    resolvers = []
    if isinstance(options.get("resolvers"), list):
        resolvers = [str(item) for item in options["resolvers"] if isinstance(item, (str, int))]
    if not resolvers:
        resolvers = []  # fall back to system resolver

    resolve_ipv6 = bool(options.get("resolve_ipv6", False)) or args.resolve_ipv6
    min_ttl = int(options.get("min_ttl_sec", 300)) if isinstance(options.get("min_ttl_sec"), int) else 300
    max_workers_opt = options.get("max_workers")
    if isinstance(max_workers_opt, int) and max_workers_opt > 0:
        max_workers = max_workers_opt
    else:
        max_workers = min(8, max(1, len(groups) * 2))

    all_domains: List[str] = sorted({domain for domains in groups.values() for domain in domains})
    logging.info("Resolving %d domains across %d groups", len(all_domains), len(groups))

    results: Dict[str, DomainResult] = {}
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(resolve_domain, domain, resolvers, resolve_ipv6): domain
            for domain in all_domains
        }
        for future in concurrent.futures.as_completed(future_map):
            domain = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - defensive guard
                logging.error("Resolution error for %s: %s", domain, exc)
                result = DomainResult(domain=domain, errors=[str(exc)])
            results[domain] = result
            if result.errors:
                logging.warning("%s: %s", domain, "; ".join(result.errors))
            if result.ipv4 or result.ipv6:
                logging.debug(
                    "%s => IPv4=%s IPv6=%s", domain, sorted(result.ipv4), sorted(result.ipv6)
                )

    elapsed = time.time() - start_time
    logging.info("Resolution finished in %.2fs", elapsed)

    aggregated_v4: Set[str] = set()
    aggregated_v6: Set[str] = set()
    for result in results.values():
        aggregated_v4.update(result.ipv4)
        aggregated_v6.update(result.ipv6)

    collapsed_v4 = collapse_ipv4(aggregated_v4) if aggregated_v4 else []

    resolved_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(elapsed, 3),
        "options": {
            "resolvers": resolvers or ["system"],
            "resolve_ipv6": resolve_ipv6,
            "min_ttl_sec": min_ttl,
        },
        "groups": groups,
        "domains": {
            name: {
                "ipv4": sorted(result.ipv4),
                "ipv6": sorted(result.ipv6),
                "ttl": result.ttl,
                "resolvers": sorted(result.resolvers),
                "errors": result.errors,
            }
            for name, result in sorted(results.items())
        },
        "summary": {
            "total_ipv4": len(aggregated_v4),
            "total_ipv6": len(aggregated_v6),
            "collapsed_ipv4": len(collapsed_v4),
            "min_ttl_sec": min_ttl,
        },
    }

    resolved_path = state_dir / RESOLVED_JSON.name
    cidr_path = state_dir / CIDR_FILE.name

    resolved_path.write_text(json.dumps(resolved_payload, indent=2, sort_keys=True), encoding="utf-8")
    logging.info("Wrote %s", resolved_path)

    lines = [
        "# Generated by resolve_domains.py",
        f"# Timestamp: {resolved_payload['generated_at']}",
        f"# Groups: {', '.join(sorted(groups.keys()))}",
        f"# Source file: {config_path}",
    ]
    if not collapsed_v4:
        lines.append("# No IPv4 addresses resolved.")
    else:
        lines.extend(collapsed_v4)
    cidr_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logging.info("Wrote %s (%d CIDR entries)", cidr_path, len(collapsed_v4))

    if aggregated_v6:
        logging.info("IPv6 addresses resolved (%d) — stored in resolved.json only", len(aggregated_v6))

    failures = [name for name, res in results.items() if not res.ipv4 and not res.ipv6]
    if failures:
        logging.warning("Domains without records: %s", ", ".join(sorted(failures)))

    if resolved_payload["summary"]["collapsed_ipv4"] == 0:
        logging.warning("No IPv4 CIDRs produced; ipset update will keep previous snapshot")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
