#!/usr/bin/env bash
# ipset_apply.sh — atomically refresh the pt_split_v4 ipset and helper iptables rules.
#
# Usage:
#   sudo bash ipset_apply.sh --dry-run
#   sudo bash ipset_apply.sh --apply [--wan-if eth0] [--wg-cidr 10.6.0.0/24]
#   sudo bash ipset_apply.sh --rollback
#
# Environment overrides:
#   WAN_IF         External interface for NAT (defaults to env or blank for any interface)
#   WG_CLIENT_CIDR IPv4 CIDR of the WireGuard clients for MASQUERADE scoping
#
# The script keeps a snapshot of the existing pt_split_v4 set in state/ipset.snapshot
# before applying changes. Rollback restores that snapshot.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${SCRIPT_DIR}/state"
CIDR_FILE="${STATE_DIR}/cidr.txt"
SNAPSHOT_FILE="${STATE_DIR}/ipset.snapshot"
IPSET_NAME="pt_split_v4"
TMP_SET="${IPSET_NAME}_tmp"
ACTION=""
WAN_IF_ENV="${WAN_IF:-}"
WG_CIDR_ENV="${WG_CLIENT_CIDR:-}"

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
error() { echo "[ERROR] $*" >&2; }

die() {
  error "$*"
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: sudo bash ipset_apply.sh [--dry-run|--apply|--rollback] [--wan-if <iface>] [--wg-cidr <cidr>]

Options:
  --dry-run       Print the CIDR entries that would be loaded without touching ipset
  --apply         Update pt_split_v4 and ensure iptables NAT rules exist
  --rollback      Restore the last saved snapshot from state/ipset.snapshot
  --wan-if        Override WAN interface (fallback to WAN_IF env or leave blank)
  --wg-cidr       Override WireGuard client CIDR (fallback to WG_CLIENT_CIDR env)
  --help          Show this help message

Example:
  sudo bash ipset_apply.sh --apply --wan-if eth0 --wg-cidr 10.6.0.0/24
USAGE
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "This script must be run as root (use sudo)."
  fi
}

ensure_state_dir() {
  mkdir -p "${STATE_DIR}"
}

ensure_command() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || die "Required command '$cmd' not found"
}

read_cidr_entries() {
  [[ -f "${CIDR_FILE}" ]] || die "Missing ${CIDR_FILE}. Run resolve_domains.py first."
  mapfile -t CIDR_ENTRIES < <(grep -Ev '^\s*(#|$)' "${CIDR_FILE}" | tr -d '\r')
}

save_snapshot() {
  if ipset list "${IPSET_NAME}" >/dev/null 2>&1; then
    ipset save "${IPSET_NAME}" >"${SNAPSHOT_FILE}" || warn "Failed to write snapshot ${SNAPSHOT_FILE}"
    log "Saved snapshot to ${SNAPSHOT_FILE}"
  else
    warn "ipset ${IPSET_NAME} does not exist yet; snapshot skipped"
  fi
}

apply_ipset() {
  ensure_command ipset
  read_cidr_entries

  log "Updating ${IPSET_NAME} with ${#CIDR_ENTRIES[@]} CIDR entries"
  save_snapshot

  ipset create "${IPSET_NAME}" hash:net family inet hashsize 2048 maxelem 131072 -exist
  ipset create "${TMP_SET}" hash:net family inet hashsize 2048 maxelem 131072 -exist
  ipset flush "${TMP_SET}" 2>/dev/null || true

  for cidr in "${CIDR_ENTRIES[@]}"; do
    ipset add "${TMP_SET}" "$cidr" -exist
  done

  ipset swap "${TMP_SET}" "${IPSET_NAME}"
  ipset destroy "${TMP_SET}" || true
  log "ipset ${IPSET_NAME} refreshed"
}

compose_iptables_rule() {
  local -n ref="$1"
  ref=()
  if [[ -n "${WG_CIDR_ENV}" ]]; then
    ref+=("-s" "${WG_CIDR_ENV}")
  fi
  if [[ -n "${WAN_IF_ENV}" ]]; then
    ref+=("-o" "${WAN_IF_ENV}")
  fi
  ref+=("-m" "set" "--match-set" "${IPSET_NAME}" "dst" "-j" "MASQUERADE")
}

ensure_iptables() {
  if ! command -v iptables >/dev/null 2>&1; then
    warn "iptables not found; skipping NAT rule automation. Configure manually."
    return 1
  fi
  local rule
  compose_iptables_rule rule
  if iptables -t nat -C POSTROUTING "${rule[@]}" >/dev/null 2>&1; then
    log "iptables MASQUERADE rule already present"
    return 0
  fi
  iptables -t nat -A POSTROUTING "${rule[@]}"
  log "Inserted iptables rule in nat/POSTROUTING"
}

rollback_ipset() {
  ensure_command ipset
  if [[ ! -f "${SNAPSHOT_FILE}" ]]; then
    die "Snapshot ${SNAPSHOT_FILE} not found."
  fi
  ipset restore <"${SNAPSHOT_FILE}"
  log "Restored ipset snapshot from ${SNAPSHOT_FILE}"
}

print_dry_run() {
  read_cidr_entries
  local rule
  compose_iptables_rule rule
  echo "[DRY-RUN] Would load ${#CIDR_ENTRIES[@]} CIDR entries into ${IPSET_NAME}"
  echo "  Source file : ${CIDR_FILE}"
  echo "  Snapshot    : ${SNAPSHOT_FILE}"
  echo "  WAN_IF      : ${WAN_IF_ENV:-<any>}"
  echo "  WG_CLIENT   : ${WG_CIDR_ENV:-<any>}"
  printf "  Example rule: iptables -t nat -A POSTROUTING"
  for arg in "${rule[@]}"; do
    printf " %q" "$arg"
  done
  printf "\n"
}

# -----------------------------------------------------------------------------
# CLI parsing
# -----------------------------------------------------------------------------

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      usage
      exit 0
      ;;
    --dry-run)
      ACTION="dry-run"
      shift
      ;;
    --apply)
      ACTION="apply"
      shift
      ;;
    --rollback)
      ACTION="rollback"
      shift
      ;;
    --wan-if)
      WAN_IF_ENV="$2"
      shift 2
      ;;
    --wg-cidr)
      WG_CIDR_ENV="$2"
      shift 2
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

ensure_state_dir

case "${ACTION}" in
  dry-run)
    print_dry_run
    ;;
  apply)
    require_root
    apply_ipset
    ensure_iptables || warn "Ensure your firewall routes pt_split_v4 traffic manually."
    log "Apply completed."
    ;;
  rollback)
    require_root
    rollback_ipset
    log "Rollback completed."
    ;;
  *)
    die "No action specified. Use --dry-run, --apply, or --rollback."
    ;;
esac
