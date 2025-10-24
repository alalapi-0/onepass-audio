#!/usr/bin/env bash
# nft_apply.sh — update the pt_split_v4 nftables set and NAT chain.
#
# Usage is identical to ipset_apply.sh but targets nftables deployments.
#   sudo bash nft_apply.sh --dry-run
#   sudo bash nft_apply.sh --apply [--wan-if eth0] [--wg-cidr 10.6.0.0/24]
#   sudo bash nft_apply.sh --rollback
#
# The script maintains state/nft.snapshot for rollbacks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${SCRIPT_DIR}/state"
CIDR_FILE="${STATE_DIR}/cidr.txt"
SNAPSHOT_FILE="${STATE_DIR}/nft.snapshot"
TABLE_NAME="inet pt_split"
SET_NAME="pt_split_v4"
CHAIN_NAME="postrouting"
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
Usage: sudo bash nft_apply.sh [--dry-run|--apply|--rollback] [--wan-if <iface>] [--wg-cidr <cidr>]
USAGE
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "This script must be run as root (use sudo)."
  fi
}

ensure_state_dir() { mkdir -p "${STATE_DIR}"; }

read_cidr_entries() {
  [[ -f "${CIDR_FILE}" ]] || die "Missing ${CIDR_FILE}. Run resolve_domains.py first."
  mapfile -t CIDR_ENTRIES < <(grep -Ev '^\s*(#|$)' "${CIDR_FILE}" | tr -d '\r')
}

save_snapshot() {
  if nft list table ${TABLE_NAME} >/dev/null 2>&1; then
    nft list table ${TABLE_NAME} >"${SNAPSHOT_FILE}" || warn "Failed to snapshot ${TABLE_NAME}"
    log "Saved snapshot to ${SNAPSHOT_FILE}"
  else
    warn "Table ${TABLE_NAME} missing; snapshot skipped"
  fi
}

apply_nft() {
  command -v nft >/dev/null 2>&1 || die "nftables command 'nft' not found"
  read_cidr_entries
  log "Updating ${TABLE_NAME} with ${#CIDR_ENTRIES[@]} CIDR entries"
  save_snapshot

  nft add table ${TABLE_NAME} >/dev/null 2>&1 || true
  nft add set ${TABLE_NAME} ${SET_NAME} '{ type ipv4_addr; flags interval; }' >/dev/null 2>&1 || true
  nft flush set ${TABLE_NAME} ${SET_NAME}

  if [[ ${#CIDR_ENTRIES[@]} -gt 0 ]]; then
    local elements="{ ${CIDR_ENTRIES[*]} }"
    nft add element ${TABLE_NAME} ${SET_NAME} "${elements}"
  fi

  nft add chain ${TABLE_NAME} ${CHAIN_NAME} '{ type nat hook postrouting priority srcnat; policy accept; }' >/dev/null 2>&1 || true
  nft flush chain ${TABLE_NAME} ${CHAIN_NAME}

  local rule_args=()
  if [[ -n "${WAN_IF_ENV}" ]]; then
    rule_args+=("oifname" "${WAN_IF_ENV}")
  fi
  if [[ -n "${WG_CIDR_ENV}" ]]; then
    rule_args+=("ip" "saddr" "${WG_CIDR_ENV}")
  fi
  rule_args+=("ip" "daddr" "@${SET_NAME}" "masquerade")
  nft add rule ${TABLE_NAME} ${CHAIN_NAME} "${rule_args[@]}"
  log "nftables set and NAT rule refreshed"
}

print_dry_run() {
  read_cidr_entries
  echo "[DRY-RUN] Would load ${#CIDR_ENTRIES[@]} CIDR entries into ${SET_NAME}"
  echo "  Table    : ${TABLE_NAME}"
  echo "  Snapshot : ${SNAPSHOT_FILE}"
  echo "  WAN_IF   : ${WAN_IF_ENV:-<any>}"
  echo "  WG_CIDR  : ${WG_CIDR_ENV:-<any>}"
}

rollback_nft() {
  command -v nft >/dev/null 2>&1 || die "nft command not found"
  [[ -f "${SNAPSHOT_FILE}" ]] || die "Snapshot ${SNAPSHOT_FILE} not found"
  nft -f "${SNAPSHOT_FILE}"
  log "Restored nftables table from ${SNAPSHOT_FILE}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      usage; exit 0 ;;
    --dry-run)
      ACTION="dry-run"; shift ;;
    --apply)
      ACTION="apply"; shift ;;
    --rollback)
      ACTION="rollback"; shift ;;
    --wan-if)
      WAN_IF_ENV="$2"; shift 2 ;;
    --wg-cidr)
      WG_CIDR_ENV="$2"; shift 2 ;;
    *)
      die "Unknown option: $1" ;;
  esac
done

ensure_state_dir

case "${ACTION}" in
  dry-run)
    print_dry_run
    ;;
  apply)
    require_root
    apply_nft
    ;;
  rollback)
    require_root
    rollback_nft
    ;;
  *)
    usage
    die "Specify --dry-run, --apply, or --rollback"
    ;;
esac
