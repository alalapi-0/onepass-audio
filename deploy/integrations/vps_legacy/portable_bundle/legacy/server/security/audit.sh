#!/usr/bin/env bash
#
# audit.sh - Perform baseline security audit for PrivateTunnel servers.
#
# The script inspects sysctl parameters, time synchronisation services,
# firewall state, key directory permissions and logging rotation readiness.
#

set -euo pipefail

OUTPUT_FORMAT="human"

print_help() {
  cat <<'USAGE'
Usage: audit.sh [--json]

Options:
  --json    Emit machine-readable JSON instead of a human summary.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)
      OUTPUT_FORMAT="json"
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

RESULT_KEYS=()
declare -A RESULT_STATUS
declare -A RESULT_NOTES

add_result() {
  local key="$1"
  local status="$2"
  local note="$3"
  RESULT_KEYS+=("$key")
  RESULT_STATUS["$key"]="$status"
  RESULT_NOTES["$key"]="$note"
}

check_sysctl() {
  local key="$1"
  local expected="$2"
  local current
  if current=$(sysctl -n "$key" 2>/dev/null); then
    if [[ "$current" == "$expected" ]]; then
      add_result "sysctl:$key" "pass" "${key}=${current}"
    else
      add_result "sysctl:$key" "warn" "${key}=${current} (expected ${expected})"
    fi
  else
    add_result "sysctl:$key" "fail" "${key} unavailable"
  fi
}

check_time_sync() {
  if systemctl is-active --quiet chronyd 2>/dev/null; then
    add_result "timesync" "pass" "chronyd active"
  elif systemctl is-active --quiet systemd-timesyncd 2>/dev/null; then
    add_result "timesync" "pass" "systemd-timesyncd active"
  else
    add_result "timesync" "warn" "No active NTP service detected"
  fi
}

check_firewall() {
  if command -v ufw >/dev/null 2>&1; then
    if ufw status | grep -qi "active"; then
      add_result "firewall" "pass" "ufw active"
    else
      add_result "firewall" "warn" "ufw installed but disabled"
    fi
  elif command -v nft >/dev/null 2>&1; then
    if nft list ruleset >/dev/null 2>&1; then
      add_result "firewall" "pass" "nftables ruleset present"
    else
      add_result "firewall" "warn" "nftables command available but empty ruleset"
    fi
  else
    add_result "firewall" "warn" "No firewall tooling (ufw/nftables) detected"
  fi
}

check_directory_permissions() {
  local path="$1"
  local expected="$2"
  if [[ -e "$path" ]]; then
    local mode
    mode=$(stat -c %a "$path")
    if [[ "$mode" == "$expected" ]]; then
      add_result "perm:$path" "pass" "${path} permissions ${mode}"
    else
      add_result "perm:$path" "warn" "${path} permissions ${mode} (expected ${expected})"
    fi
  else
    add_result "perm:$path" "fail" "${path} missing"
  fi
}

check_logrotate() {
  if command -v logrotate >/dev/null 2>&1; then
    add_result "logrotate" "pass" "logrotate $(logrotate --version | head -n1)"
  else
    add_result "logrotate" "warn" "logrotate not installed"
  fi
}

check_log_directory() {
  local path="/var/log/private-tunnel"
  if [[ -d "$path" ]]; then
    if [[ -w "$path" ]]; then
      add_result "logdir" "pass" "${path} present and writable"
    else
      add_result "logdir" "warn" "${path} present but not writable"
    fi
  else
    add_result "logdir" "warn" "${path} missing"
  fi
}

# Perform checks
check_sysctl "net.ipv4.ip_forward" "1"
check_sysctl "net.ipv4.conf.all.rp_filter" "1"
check_sysctl "net.ipv4.tcp_congestion_control" "bbr"
check_sysctl "net.core.default_qdisc" "fq"
check_time_sync
check_firewall
check_directory_permissions "/etc/wireguard" "700"
check_directory_permissions "/etc/wireguard/server_private.key" "600"
check_directory_permissions "/etc/wireguard/clients" "700"
check_log_directory
check_logrotate

compute_grade() {
  local fails=0
  local warns=0
  for key in "${RESULT_KEYS[@]}"; do
    case "${RESULT_STATUS[$key]}" in
      fail) ((fails++)) ;;
      warn) ((warns++)) ;;
    esac
  done
  if (( fails == 0 && warns <= 1 )); then
    echo "A"
  elif (( fails <= 1 && warns <= 3 )); then
    echo "B"
  else
    echo "C"
  fi
}

GRADE=$(compute_grade)

if [[ "$OUTPUT_FORMAT" == "json" ]]; then
  echo -n '{"grade":"'$GRADE'","checks":['
  first=1
  for key in "${RESULT_KEYS[@]}"; do
    if [[ $first -eq 0 ]]; then
      echo -n ','
    fi
    first=0
    printf '{"id":"%s","status":"%s","note":%s}' \
      "$key" "${RESULT_STATUS[$key]}" \
      "$(python3 -c 'import json,sys;print(json.dumps(sys.stdin.read().strip()))' <<<"${RESULT_NOTES[$key]}")"
  done
  echo ']}'
else
  echo "=== PrivateTunnel Security Audit ==="
  printf "%-35s %-6s %s\n" "Check" "State" "Notes"
  for key in "${RESULT_KEYS[@]}"; do
    printf "%-35s %-6s %s\n" "$key" "${RESULT_STATUS[$key]}" "${RESULT_NOTES[$key]}"
  done
  echo "\nOverall grade: $GRADE"
  if [[ "$GRADE" != "A" ]]; then
    echo "Refer to docs/SECURITY-HARDENING.md for remediation guidance."
  fi
fi
