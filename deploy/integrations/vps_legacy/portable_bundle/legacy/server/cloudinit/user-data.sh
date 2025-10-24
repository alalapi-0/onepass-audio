#!/bin/bash
# open to overrides by the workflow prelude:
# WG_PORT, CLIENT_NAME, CLIENT_ADDR
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

WG_IFNAME="${WG_IFNAME:-wg0}"
WG_PORT="${WG_PORT:-51820}"
WG_SUBNET="${WG_SUBNET:-10.6.0.0/24}"
WG_SVR_ADDR="${WG_SVR_ADDR:-10.6.0.1/24}"
CLIENT_NAME="${CLIENT_NAME:-iphone}"
CLIENT_ADDR="${CLIENT_ADDR:-10.6.0.2/32}"
CLIENT_DNS="${CLIENT_DNS:-1.1.1.1}"
KEEPALIVE="${KEEPALIVE:-25}"

log(){ echo "[OC] $*"; }

log "apt update/upgrade & packages"
apt-get update -y
apt-get install -y wireguard wireguard-tools qrencode iptables-persistent curl git >/dev/null

log "detect WAN_IF"
WAN_IF="$(ip -o -4 route show to default | awk '{print $5}' | head -n1)"
[ -n "$WAN_IF" ] || { echo "No WAN_IF"; exit 1; }
log "WAN_IF=$WAN_IF"

log "server keys"
install -d -m 700 /etc/wireguard
umask 077
if [ ! -f /etc/wireguard/${WG_IFNAME}.private ]; then
  wg genkey | tee /etc/wireguard/${WG_IFNAME}.private | wg pubkey > /etc/wireguard/${WG_IFNAME}.public
fi
SVR_PRIV="$(cat /etc/wireguard/${WG_IFNAME}.private)"
SVR_PUB="$(cat /etc/wireguard/${WG_IFNAME}.public)"

log "write wg conf"
cat >/etc/wireguard/${WG_IFNAME}.conf <<EOF
[Interface]
Address = ${WG_SVR_ADDR}
ListenPort = ${WG_PORT}
PrivateKey = ${SVR_PRIV}
SaveConfig = true
EOF

log "sysctl & NAT"
cat >/etc/sysctl.d/99-privatetunnel.conf <<EOF
net.ipv4.ip_forward=1
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.ipv4.tcp_mtu_probing=1
EOF
sysctl -p /etc/sysctl.d/99-privatetunnel.conf || true

iptables -t nat -C POSTROUTING -s "${WG_SUBNET}" -o "${WAN_IF}" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s "${WG_SUBNET}" -o "${WAN_IF}" -j MASQUERADE
iptables -C FORWARD -i "${WAN_IF}" -o "${WG_IFNAME}" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -i "${WAN_IF}" -o "${WG_IFNAME}" -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -C FORWARD -i "${WG_IFNAME}" -o "${WAN_IF}" -j ACCEPT 2>/dev/null || iptables -A FORWARD -i "${WG_IFNAME}" -o "${WAN_IF}" -j ACCEPT
netfilter-persistent save || true

log "enable wg"
systemctl enable "wg-quick@${WG_IFNAME}" >/dev/null 2>&1 || true
systemctl restart "wg-quick@${WG_IFNAME}"

log "first client: ${CLIENT_NAME}"
install -d -m 700 "/etc/wireguard/clients/${CLIENT_NAME}"
pushd "/etc/wireguard/clients/${CLIENT_NAME}" >/dev/null
  umask 077
  wg genkey | tee ${CLIENT_NAME}.private | wg pubkey > ${CLIENT_NAME}.public
  CLI_PRIV="$(cat ${CLIENT_NAME}.private)"
  CLI_PUB="$(cat ${CLIENT_NAME}.public)"
  wg set "${WG_IFNAME}" peer "${CLI_PUB}" allowed-ips "${CLIENT_ADDR}"
  wg-quick save "${WG_IFNAME}"
  ENDPOINT="$(curl -4 -s ifconfig.me):${WG_PORT}"
  cat > ${CLIENT_NAME}.conf <<EOC
[Interface]
PrivateKey = ${CLI_PRIV}
Address = ${CLIENT_ADDR}
DNS = ${CLIENT_DNS}

[Peer]
PublicKey = ${SVR_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = ${ENDPOINT}
PersistentKeepalive = ${KEEPALIVE}
EOC
  qrencode -o /root/${CLIENT_NAME}.png -s 8 -m 2 < ${CLIENT_NAME}.conf
  cp ${CLIENT_NAME}.conf /root/${CLIENT_NAME}.conf
popd >/dev/null

log "DONE. QR at /root/${CLIENT_NAME}.png"
