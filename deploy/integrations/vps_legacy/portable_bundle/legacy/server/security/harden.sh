#!/usr/bin/env bash
#
# harden.sh - Apply baseline hardening for PrivateTunnel servers.
#
# The script can operate interactively or with --yes/--dry-run flags. It writes
# sysctl overrides, ensures time synchronisation, prepares log directories and
# installs logrotate examples.
#

set -euo pipefail

DRY_RUN=0
ASSUME_YES=0

print_help() {
  cat <<'USAGE'
Usage: harden.sh [--dry-run] [--yes]

Options:
  --dry-run   Show the planned changes without applying them.
  --yes       Run non-interactively, accepting default actions.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --yes)
      ASSUME_YES=1
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      print_help
      exit 1
      ;;
  esac
done

run_cmd() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] $*"
  else
    echo "[exec] $*"
    eval "$@"
  fi
}

confirm() {
  local prompt="$1"
  if [[ $ASSUME_YES -eq 1 ]]; then
    return 0
  fi
  read -r -p "$prompt [y/N] " response
  case "$response" in
    [yY][eE][sS]|[yY]) return 0 ;;
    *) return 1 ;;
  esac
}

apply_sysctl() {
  local sysctl_file="/etc/sysctl.d/90-privatetunnel.conf"
  local backup="${sysctl_file}.bak.$(date +%s)"
  if [[ $DRY_RUN -eq 0 && -f "$sysctl_file" ]]; then
    cp "$sysctl_file" "$backup"
    echo "Backup written to $backup"
  fi
  local contents="net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=1
net.ipv4.tcp_congestion_control=bbr
net.core.default_qdisc=fq"
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '[dry-run] write %s with:\n%s\n' "$sysctl_file" "$contents"
  else
    printf '%s\n' "$contents" > "$sysctl_file"
    sysctl --system >/dev/null
  fi
}

ensure_chrony() {
  if systemctl is-active --quiet chronyd 2>/dev/null; then
    echo "chronyd already active"
    return
  fi
  if command -v chronyd >/dev/null 2>&1; then
    run_cmd "systemctl enable --now chronyd"
    return
  fi
  if confirm "Install chrony NTP service?"; then
    if command -v apt-get >/dev/null 2>&1; then
      run_cmd "apt-get update"
      run_cmd "apt-get install -y chrony"
    elif command -v yum >/dev/null 2>&1; then
      run_cmd "yum install -y chrony"
    else
      echo "Package manager not detected. Install chrony manually." >&2
    fi
    run_cmd "systemctl enable --now chronyd"
  else
    echo "Skipping chrony installation."
  fi
}

prepare_logs() {
  local dir="/var/log/private-tunnel"
  if [[ ! -d "$dir" ]]; then
    run_cmd "mkdir -p $dir"
  fi
  run_cmd "chmod 750 $dir"
  local rotate_dir="/etc/logrotate.d"
  if [[ -d "$rotate_dir" ]]; then
    local script_dir
    script_dir=$(cd "$(dirname "$0")" && pwd)
    run_cmd "ln -sf $script_dir/logrotate/toy-gateway $rotate_dir/privatetunnel-toy"
    run_cmd "ln -sf $script_dir/logrotate/wireguard $rotate_dir/privatetunnel-wireguard"
  else
    echo "logrotate directory not found; create manually at /etc/logrotate.d" >&2
  fi
}

print_firewall_hint() {
  cat <<'FW'
[info] Firewall not modified automatically. Recommended ufw rules:
    ufw allow 51820/udp  # WireGuard
    ufw allow 35000/udp  # Toy gateway (if used)

nftables template located at server/security/firewall/ (placeholder).
FW
}

main() {
  echo "=== PrivateTunnel Hardening ==="
  apply_sysctl
  ensure_chrony
  prepare_logs
  print_firewall_hint
  echo "Done. Review docs/SECURITY-HARDENING.md for additional steps."
}

main
