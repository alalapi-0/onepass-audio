#!/usr/bin/env bash
# cron-install.sh — install a systemd timer that refreshes split-tunnel ipsets.
#
# Usage:
#   sudo bash cron-install.sh --install [--backend ipset|nft]
#   sudo bash cron-install.sh --remove
#   sudo bash cron-install.sh --status
#
# The timer runs resolve_domains.py followed by ipset_apply.sh/nft_apply.sh
# every 10 minutes. Environment overrides (WAN_IF, WG_CLIENT_CIDR, etc.) can be
# placed in /etc/privatetunnel/split.env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="pt-split-resolver.service"
TIMER_NAME="pt-split-resolver.timer"
SYSTEMD_DIR="/etc/systemd/system"
ENV_FILE="/etc/privatetunnel/split.env"
BACKEND="ipset"
ACTION=""

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
error() { echo "[ERROR] $*" >&2; }

die() {
  error "$*"
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: sudo bash cron-install.sh --install [--backend ipset|nft]
       sudo bash cron-install.sh --remove
       sudo bash cron-install.sh --status
USAGE
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "This script must be run as root."
  fi
}

write_unit_files() {
  local resolver_exec="${SCRIPT_DIR}/resolve_domains.py"
  local apply_script
  case "${BACKEND}" in
    ipset) apply_script="${SCRIPT_DIR}/ipset_apply.sh --apply" ;;
    nft) apply_script="${SCRIPT_DIR}/nft_apply.sh --apply" ;;
    *) die "Unsupported backend: ${BACKEND}" ;;
  esac

  cat >"${SYSTEMD_DIR}/${SERVICE_NAME}" <<UNIT
[Unit]
Description=PrivateTunnel split resolver (${BACKEND})
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=/usr/bin/env python3 ${resolver_exec}
ExecStart=/usr/bin/env bash ${apply_script}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

  cat >"${SYSTEMD_DIR}/${TIMER_NAME}" <<UNIT
[Unit]
Description=Run PrivateTunnel split resolver every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Unit=${SERVICE_NAME}
Persistent=true

[Install]
WantedBy=timers.target
UNIT
}

install_timer() {
  require_root
  mkdir -p "$(dirname "${ENV_FILE}")"
  write_unit_files
  systemctl daemon-reload
  systemctl enable --now "${TIMER_NAME}"
  log "Installed and started ${TIMER_NAME}. Use --status to inspect." 
}

remove_timer() {
  require_root
  systemctl disable --now "${TIMER_NAME}" >/dev/null 2>&1 || true
  rm -f "${SYSTEMD_DIR}/${SERVICE_NAME}" "${SYSTEMD_DIR}/${TIMER_NAME}"
  systemctl daemon-reload
  log "Removed ${TIMER_NAME} and ${SERVICE_NAME}."
}

status_timer() {
  systemctl list-timers "${TIMER_NAME}" || true
  systemctl status "${TIMER_NAME}" --no-pager || true
  systemctl status "${SERVICE_NAME}" --no-pager || true
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      usage; exit 0 ;;
    --install)
      ACTION="install"; shift ;;
    --remove)
      ACTION="remove"; shift ;;
    --status)
      ACTION="status"; shift ;;
    --backend)
      BACKEND="$2"; shift 2 ;;
    *)
      die "Unknown option: $1" ;;
  esac
done

case "${ACTION}" in
  install)
    install_timer
    ;;
  remove)
    remove_timer
    ;;
  status)
    status_timer
    ;;
  *)
    usage
    die "Specify --install, --remove, or --status"
    ;;
esac
