#!/usr/bin/env bash
# Gracefully stop and roll back the PrivateTunnel WireGuard deployment.
# The script disables wg-quick@<ifname>, archives configuration files, and
# optionally purges client profiles. Use --yes to skip interactive confirmation.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
CONFIG_DIR="/etc/wireguard"
SYSCTL_DROP_IN="/etc/sysctl.d/99-privatetunnel.conf"
NFT_RULES_FILE="$CONFIG_DIR/privatetunnel-nat.nft"
AUTO_CONFIRM=false
PURGE=false
WG_IFNAME="wg0"

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

die() {
  err "$*"
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: sudo bash wg-uninstall.sh [--ifname=wg0] [--yes] [--purge]

Options:
  --help          Show this message
  --ifname=name   WireGuard interface to disable (default wg0)
  --yes           Assume "yes" to all prompts
  --purge         Delete /etc/wireguard/clients after creating a tar archive

The script keeps safety backups in /etc/wireguard/archive/ unless --purge is
specified, in which case the directory is removed after archiving. To restore,
re-run wg-install.sh and copy the archived configuration back.
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
      --yes)
        AUTO_CONFIRM=true
        shift
        ;;
      --purge)
        PURGE=true
        shift
        ;;
      --ifname=*)
        WG_IFNAME="${1#*=}"
        shift
        ;;
      --ifname)
        WG_IFNAME="$2"
        shift 2
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "wg-uninstall.sh must be run as root"
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

stop_service() {
  if systemctl list-unit-files | grep -q "wg-quick@$WG_IFNAME.service"; then
    if systemctl is-enabled "wg-quick@$WG_IFNAME" >/dev/null 2>&1; then
      log "Disabling wg-quick@$WG_IFNAME"
      systemctl disable "wg-quick@$WG_IFNAME" || warn "Failed to disable unit"
    fi
    if systemctl is-active "wg-quick@$WG_IFNAME" >/dev/null 2>&1; then
      log "Stopping wg-quick@$WG_IFNAME"
      systemctl stop "wg-quick@$WG_IFNAME" || warn "Failed to stop interface"
    fi
  else
    warn "wg-quick@$WG_IFNAME unit not found; skipping stop"
  fi
}

backup_path() {
  local ts
  ts="$(date +%Y%m%d-%H%M%S)"
  echo "$CONFIG_DIR/archive/$ts"
}

archive_configs() {
  local backup
  backup=$(backup_path)
  mkdir -p "$backup"
  if [[ -f "$CONFIG_DIR/$WG_IFNAME.conf" ]]; then
    log "Backing up $CONFIG_DIR/$WG_IFNAME.conf to $backup"
    cp "$CONFIG_DIR/$WG_IFNAME.conf" "$backup/"
  fi
  if [[ -d "$CONFIG_DIR/clients" ]]; then
    log "Archiving client profiles to $backup/clients"
    cp -a "$CONFIG_DIR/clients" "$backup/"
  fi
  if [[ -f "$NFT_RULES_FILE" ]]; then
    cp "$NFT_RULES_FILE" "$backup/"
  fi
  if [[ -f "$SYSCTL_DROP_IN" ]]; then
    cp "$SYSCTL_DROP_IN" "$backup/"
  fi
  log "Backup stored at $backup"
  echo "$backup"
}

remove_sysctl() {
  if [[ -f "$SYSCTL_DROP_IN" ]]; then
    log "Removing $SYSCTL_DROP_IN"
    rm -f "$SYSCTL_DROP_IN"
    sysctl -w net.ipv4.ip_forward=0 >/dev/null 2>&1 || true
    sysctl -w net.ipv6.conf.all.forwarding=0 >/dev/null 2>&1 || true
    sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1 || true
    sysctl -w net.ipv4.tcp_congestion_control=cubic >/dev/null 2>&1 || true
    sysctl -w net.ipv4.tcp_mtu_probing=0 >/dev/null 2>&1 || true
  fi
}

purge_clients() {
  if [[ ! -d "$CONFIG_DIR/clients" ]]; then
    return
  fi
  local archive_file="$CONFIG_DIR/archive/clients-$(date +%Y%m%d-%H%M%S).tar.gz"
  log "Creating $archive_file before purge"
  tar -czf "$archive_file" -C "$CONFIG_DIR" clients
  rm -rf "$CONFIG_DIR/clients"
  log "Client directory removed. Archive stored at $archive_file"
}

main() {
  require_root
  parse_env
  parse_args "$@"

  if ! confirm "This will stop wg-quick@$WG_IFNAME and disable WireGuard"; then
    log "Aborted by user"
    exit 0
  fi

  stop_service
  local backup
  backup=$(archive_configs)
  if [[ -f "$CONFIG_DIR/$WG_IFNAME.conf" ]]; then
    log "Removing active configuration $CONFIG_DIR/$WG_IFNAME.conf"
    rm -f "$CONFIG_DIR/$WG_IFNAME.conf"
  fi
  rm -f "$NFT_RULES_FILE"
  remove_sysctl

  if $PURGE; then
    if confirm "Delete all client profiles under $CONFIG_DIR/clients?"; then
      purge_clients
    else
      log "Skipping purge per user request"
    fi
  fi

  cat <<MSG
WireGuard service wg-quick@$WG_IFNAME has been disabled.
Backups are stored at: $backup
To restore later, re-run wg-install.sh and copy the archived configuration
back to /etc/wireguard, then start the service.
Check 'journalctl -u wg-quick@$WG_IFNAME' for historical logs if needed.
MSG
}

main "$@"
