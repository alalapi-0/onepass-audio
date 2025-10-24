#!/usr/bin/env bash
#
# Tear down the toy TUN interface and NAT rules created by setup_tun.sh.
# Only run this after finishing local testing.

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "[teardown_tun] This script must be run as root." >&2
  exit 1
fi

ENV_FILE=${1:-.env}
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

TOY_TUN=${TOY_TUN:-toy0}
TOY_TUN_ADDR=${TOY_TUN_ADDR:-10.66.0.1/24}
WAN_IF=${WAN_IF:-eth0}

export TOY_TUN TOY_TUN_ADDR WAN_IF

if ! command -v ip >/dev/null 2>&1; then
  echo "[teardown_tun] 'ip' command not found." >&2
  exit 1
fi

if ! command -v iptables >/dev/null 2>&1; then
  echo "[teardown_tun] 'iptables' command not found." >&2
  exit 1
fi

TOY_SUBNET=$(python3 - <<'PY'
import ipaddress
import os
addr = os.environ.get('TOY_TUN_ADDR', '10.66.0.1/24')
try:
    iface = ipaddress.ip_interface(addr)
except ValueError as exc:
    raise SystemExit(f"Invalid TOY_TUN_ADDR '{addr}': {exc}")
print(iface.network)
PY
)

echo "[teardown_tun] Removing NAT rule for ${TOY_SUBNET} via ${WAN_IF}"
if iptables -t nat -C POSTROUTING -s "${TOY_SUBNET}" -o "${WAN_IF}" -j MASQUERADE >/dev/null 2>&1; then
  iptables -t nat -D POSTROUTING -s "${TOY_SUBNET}" -o "${WAN_IF}" -j MASQUERADE
else
  echo "[teardown_tun] NAT rule already absent."
fi

echo "[teardown_tun] Bringing interface ${TOY_TUN} down"
ip link set dev "${TOY_TUN}" down >/dev/null 2>&1 || true

if ip link show "${TOY_TUN}" >/dev/null 2>&1; then
  ip link delete "${TOY_TUN}"
  echo "[teardown_tun] Interface ${TOY_TUN} deleted."
else
  echo "[teardown_tun] Interface ${TOY_TUN} not present."
fi

echo "[teardown_tun] Remaining addresses on ${TOY_TUN}:"
ip addr show dev "${TOY_TUN}" || true

echo "[teardown_tun] NAT POSTROUTING table:"
iptables -t nat -S POSTROUTING | grep "${TOY_SUBNET}" || true

echo "[teardown_tun] Done. Consider resetting net.ipv4.ip_forward if you enabled it manually."
