#!/usr/bin/env bash
#
# Prepare a Linux TUN interface and NAT rules for the toy UDP/TUN gateway.
# This script must be executed as root and is intended for short-lived
# development tests only — do not keep the interface or NAT rules enabled in
# production.

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "[setup_tun] This script must be run as root." >&2
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
MTU=${MTU:-1380}

export TOY_TUN TOY_TUN_ADDR WAN_IF MTU

if ! command -v ip >/dev/null 2>&1; then
  echo "[setup_tun] 'ip' command not found. Install iproute2." >&2
  exit 1
fi

if ! command -v iptables >/dev/null 2>&1; then
  echo "[setup_tun] 'iptables' command not found." >&2
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

echo "[setup_tun] Creating TUN interface ${TOY_TUN} with address ${TOY_TUN_ADDR}"
if ip link show "${TOY_TUN}" >/dev/null 2>&1; then
  echo "[setup_tun] Interface ${TOY_TUN} already exists, skipping creation."
else
  ip tuntap add dev "${TOY_TUN}" mode tun
fi

if ip addr show dev "${TOY_TUN}" | grep -q "${TOY_TUN_ADDR}"; then
  echo "[setup_tun] Address ${TOY_TUN_ADDR} already assigned."
else
  ip addr add "${TOY_TUN_ADDR}" dev "${TOY_TUN}"
fi

ip link set dev "${TOY_TUN}" mtu "${MTU}"
ip link set dev "${TOY_TUN}" up

echo "[setup_tun] Enabling net.ipv4.ip_forward"
sysctl -w net.ipv4.ip_forward=1 >/dev/null

if iptables -t nat -C POSTROUTING -s "${TOY_SUBNET}" -o "${WAN_IF}" -j MASQUERADE >/dev/null 2>&1; then
  echo "[setup_tun] NAT rule already present."
else
  iptables -t nat -A POSTROUTING -s "${TOY_SUBNET}" -o "${WAN_IF}" -j MASQUERADE
fi

echo "[setup_tun] Current TUN configuration:"
ip addr show dev "${TOY_TUN}"

echo "[setup_tun] Current NAT rules:"
iptables -t nat -S POSTROUTING | grep "${TOY_SUBNET}" || true

echo "[setup_tun] Done. Remember to run teardown_tun.sh after testing."
