#!/usr/bin/env bash
# Display WireGuard peer status in table or JSON form using "wg show" output.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
WG_IFNAME="wg0"
OUTPUT_JSON=false
DUMP_FILE=""

cleanup() {
  [[ -n "$DUMP_FILE" && -f "$DUMP_FILE" ]] && rm -f "$DUMP_FILE"
}

trap cleanup EXIT

log() { echo "[INFO] $*"; }
err() { echo "[ERROR] $*" >&2; }

die() {
  err "$*"
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: sudo bash wg-list-peers.sh [--ifname=wg0] [--json]

Options:
  --ifname <wg0>  Specify WireGuard interface (default wg0 or .env WG_IF)
  --json          Output machine-readable JSON array of peers
  --help          Show this help text

The script surfaces each peer's allowed IPs, handshake age, and byte counters
based on "wg show <ifname> dump".
USAGE
}

parse_env() {
  [[ -f "$ENV_FILE" ]] || return
  while IFS='=' read -r key value; do
    [[ -z "$key" || "${key:0:1}" == "#" ]] && continue
    case "$key" in
      WG_IF) WG_IFNAME="${value}" ;;
    esac
  done < <(grep -E '^[A-Za-z_]+=.*' "$ENV_FILE" || true)
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h)
        usage
        exit 0
        ;;
      --ifname)
        WG_IFNAME="$2"
        shift 2
        ;;
      --ifname=*)
        WG_IFNAME="${1#*=}"
        shift
        ;;
      --json)
        OUTPUT_JSON=true
        shift
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

collect_dump() {
  if ! command -v wg >/dev/null 2>&1; then
    die "wg command not available"
  fi
  DUMP_FILE=$(mktemp)
  if ! wg show "$WG_IFNAME" dump >"$DUMP_FILE" 2>/dev/null; then
    rm -f "$DUMP_FILE"
    die "Failed to query interface $WG_IFNAME. Is it up?"
  fi
}

render_table() {
  python3 - "$DUMP_FILE" <<'PY'
import pathlib, sys, time
path = pathlib.Path(sys.argv[1])
lines = path.read_text().strip().splitlines()
if not lines:
    print("No peers registered.")
    raise SystemExit
header = "%-45s %-18s %-19s %-12s %-12s" % ("PublicKey", "AllowedIPs", "Last Handshake", "Rx", "Tx")
print(header)
print("-" * len(header))
now = time.time()
for row in lines[1:]:
    fields = row.split('\t')
    public, _, endpoint, allowed, latest, rx, tx, keep = fields
    latest = int(latest)
    if latest == 0:
        last = "never"
    else:
        delta = int(now - latest)
        last = f"{delta}s ago"
    def human(bytes_value):
        value = int(bytes_value)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if value < 1024:
                return f"{value:.0f}{unit}"
            value /= 1024
        return f"{value:.0f}PiB"
    print("%-45s %-18s %-19s %-12s %-12s" % (public, allowed, last, human(rx), human(tx)))
PY
}

render_json() {
  python3 - "$DUMP_FILE" <<'PY'
import json, pathlib, sys, time
path = pathlib.Path(sys.argv[1])
lines = path.read_text().strip().splitlines()
if len(lines) <= 1:
    print('[]')
    raise SystemExit
now = int(time.time())
peers = []
for row in lines[1:]:
    public, _, endpoint, allowed, latest, rx, tx, keep = row.split('\t')
    peers.append({
        "public_key": public,
        "endpoint": endpoint if endpoint != '(none)' else None,
        "allowed_ips": allowed,
        "latest_handshake": int(latest),
        "handshake_age": None if int(latest) == 0 else now - int(latest),
        "transfer_rx": int(rx),
        "transfer_tx": int(tx),
        "persistent_keepalive": int(keep)
    })
print(json.dumps(peers, indent=2))
PY
}

main() {
  parse_env
  parse_args "$@"
  collect_dump
  if $OUTPUT_JSON; then
    render_json
  else
    render_table
  fi
}

main "$@"
