#!/usr/bin/env bash
# Remove a WireGuard peer from the server configuration and optionally prune
# stored client files. A backup of wg0.conf is kept for safety.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
CONFIG_DIR="/etc/wireguard"
WG_IFNAME="wg0"
CLIENT_NAME=""
KEEP_FILES=true
AUTO_CONFIRM=false
CLIENT_PUBLIC_KEY=""
REMOVED_BACKUP=""

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

die() {
  err "$*"
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: sudo bash wg-revoke-peer.sh --name <client> [--keep-files=false] [--ifname=wg0] [--yes]

Options:
  --name <client>     Peer name (matches wg-add-peer.sh)
  --keep-files=false  Delete /etc/wireguard/clients/<client> instead of archiving
  --ifname <wg0>      WireGuard interface name
  --yes               Skip confirmation prompt
  --help              Display this help text

The script removes the peer from /etc/wireguard/<ifname>.conf, runs
"wg set <ifname> peer <publickey> remove" to hot-reload, and moves client
files to /etc/wireguard/clients-archive/ when keep-files=true.
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
      --name)
        CLIENT_NAME="$2"
        shift 2
        ;;
      --name=*)
        CLIENT_NAME="${1#*=}"
        shift
        ;;
      --keep-files=false)
        KEEP_FILES=false
        shift
        ;;
      --keep-files=true)
        KEEP_FILES=true
        shift
        ;;
      --ifname)
        WG_IFNAME="$2"
        shift 2
        ;;
      --ifname=*)
        WG_IFNAME="${1#*=}"
        shift
        ;;
      --yes)
        AUTO_CONFIRM=true
        shift
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "wg-revoke-peer.sh must be executed as root"
  fi
}

confirm() {
  local prompt="$1"
  if $AUTO_CONFIRM; then
    return 0
  fi
  read -r -p "$prompt [y/N]: " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

read_public_key() {
  local dir="$CONFIG_DIR/clients/$CLIENT_NAME"
  if [[ -f "$dir/public.key" ]]; then
    CLIENT_PUBLIC_KEY=$(<"$dir/public.key")
    return
  fi
  CLIENT_PUBLIC_KEY=""
  if [[ -f "$CONFIG_DIR/$WG_IFNAME.conf" ]]; then
    CLIENT_PUBLIC_KEY=$(grep -A4 -F "# Client $CLIENT_NAME" "$CONFIG_DIR/$WG_IFNAME.conf" | grep -E "^PublicKey" | head -n1 | awk -F'= ' '{print $2}' || true)
  fi
  if [[ -z "$CLIENT_PUBLIC_KEY" ]]; then
    warn "Unable to determine public key for $CLIENT_NAME. Removal will rely on config parsing only."
  fi
}

remove_peer_block() {
  local conf="$CONFIG_DIR/$WG_IFNAME.conf"
  [[ -f "$conf" ]] || die "Server configuration $conf not found"
  local backup="$conf.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$conf" "$backup"
  log "Backup stored at $backup"
  python3 - "$conf" "${CLIENT_NAME}" "${CLIENT_PUBLIC_KEY}" <<'PY'
import pathlib, sys
conf = pathlib.Path(sys.argv[1])
name = sys.argv[2]
public = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != '' else None
lines = conf.read_text().splitlines()
result = []
skip = False
skip_due_to_public = False
for line in lines:
    stripped = line.strip()
    if not skip and stripped == f"# Client {name}":
        skip = True
        continue
    if not skip and public and stripped == f"PublicKey = {public}":
        if result and result[-1].strip() == "[Peer]":
            result.pop()
        skip = True
        skip_due_to_public = True
        continue
    if skip:
        if stripped == "":
            skip = False
            skip_due_to_public = False
            continue
        if skip_due_to_public and stripped.startswith("# Client"):
            skip_due_to_public = False
        continue
    result.append(line)
conf.write_text("\n".join(result) + ("\n" if result else ""))
PY
  REMOVED_BACKUP="$backup"
  if cmp -s "$conf" "$backup"; then
    warn "Peer block for $CLIENT_NAME was not found in $conf"
  fi
}

remove_runtime_peer() {
  if [[ -n "$CLIENT_PUBLIC_KEY" ]]; then
    if wg set "$WG_IFNAME" peer "$CLIENT_PUBLIC_KEY" remove; then
      log "Removed peer from running interface"
    else
      warn "Failed to remove peer from runtime interface"
    fi
  else
    warn "Skipping runtime removal because public key is unknown"
  fi
}

handle_client_files() {
  local dir="$CONFIG_DIR/clients/$CLIENT_NAME"
  if [[ ! -d "$dir" ]]; then
    return
  fi
  if $KEEP_FILES; then
    local archive="$CONFIG_DIR/clients-archive"
    mkdir -p "$archive"
    local target="$archive/${CLIENT_NAME}-$(date +%Y%m%d-%H%M%S)"
    mv "$dir" "$target"
    log "Client files moved to $target"
  else
    rm -rf "$dir"
    log "Client directory $dir deleted"
  fi
}

main() {
  require_root
  parse_env
  parse_args "$@"
  [[ -n "$CLIENT_NAME" ]] || die "--name is required"

  if ! confirm "Revoke peer $CLIENT_NAME from $WG_IFNAME?"; then
    log "Aborted"
    exit 0
  fi

  read_public_key
  remove_peer_block
  remove_runtime_peer
  handle_client_files

  cat <<MSG
Peer $CLIENT_NAME removed from $WG_IFNAME.
Review $REMOVED_BACKUP if you need to undo the change.
Restart the interface with 'systemctl restart wg-quick@$WG_IFNAME' if required.
MSG
}

main "$@"
