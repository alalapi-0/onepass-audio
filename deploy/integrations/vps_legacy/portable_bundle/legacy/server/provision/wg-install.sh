#!/usr/bin/env bash
# WireGuard server provisioning helper for PrivateTunnel.
#
# This script is designed to be idempotent: you can run it multiple times to
# reconfigure the server safely. It reads defaults from env.example/.env, allows
# command-line overrides, applies kernel/network tunings, manages firewall NAT,
# and renders /etc/wireguard/wg0.conf from a template while keeping backups.
#
# Usage: sudo bash wg-install.sh [--dry-run] [--yes]
#        [--ipv6=true|false] [--firewall=auto|ufw|iptables|nftables]
#        [--port=51820] [--ifname=wg0] [--wan-if=eth0]
#        [--subnet=10.6.0.0/24] [--mtu=1420] [--dns=1.1.1.1]
#        [--endpoint=vpn.example.com:51820]
#
# Important: The script prints a summary including the public key and endpoint
# details. Store secrets securely and audit backups stored in
# /etc/wireguard/wg0.conf.bak-YYYYmmdd-HHMMSS when changes occur.
#
# Round 8 note: when enabling domain-based split routing, export `WAN_IF` and
# `WG_CLIENT_CIDR` to match this server before running the helper scripts in
# `server/split/`. See docs/SPLIT-IPSET.md for the full workflow.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR/templates"
ENV_FILE="$SCRIPT_DIR/.env"
CONFIG_PATH="/etc/wireguard"
SERVER_CONFIG="$CONFIG_PATH/wg0.conf"
SYSCTL_DROP_IN="/etc/sysctl.d/99-privatetunnel.conf"
NFT_RULES_FILE="$CONFIG_PATH/privatetunnel-nat.nft"
DRY_RUN=false
AUTO_CONFIRM=false

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
error() { echo "[ERROR] $*" >&2; }

die() {
  error "$*"
  exit 1
}

cleanup() {
  if [[ -n "${RESTORE_CONFIG:-}" && -f "${RESTORE_CONFIG}" ]]; then
    warn "Restoring previous WireGuard configuration from ${RESTORE_CONFIG}"
    cp "${RESTORE_CONFIG}" "$SERVER_CONFIG"
    rm -f "${RESTORE_CONFIG}"
  fi
}

trap cleanup ERR

usage() {
  cat <<'USAGE'
Usage: sudo bash wg-install.sh [options]

Options:
  --help                 Show this help message
  --dry-run              Print actions without modifying the system
  --yes                  Assume "yes" for interactive confirmations
  --ipv6=true|false      Enable IPv6 forwarding (default false)
  --firewall=<mode>      auto|ufw|iptables|nftables (default auto)
  --port=<port>          UDP port for WireGuard (default 51820)
  --ifname=<name>        WireGuard interface name (default wg0)
  --wan-if=<iface>       External interface for NAT (default eth0)
  --subnet=<cidr>        VPN IPv4 subnet (default 10.6.0.0/24)
  --subnet6=<cidr>       Optional IPv6 subnet (e.g. fd86:ea04:1115::/64)
  --mtu=<value>          Recommended MTU (default 1420)
  --dns=<list>           Comma separated DNS servers for clients
  --endpoint=<host:port> Public endpoint for generated client configs

Priority: CLI options > server/provision/.env > built-in defaults.
Run with --dry-run first on new hosts to review the resulting configuration.
USAGE
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "This script must be run as root via sudo."
  fi
}

parse_kv_line() {
  local line="$1"
  [[ -z "$line" || "${line:0:1}" == "#" ]] && return 1
  if [[ "$line" == *"="* ]]; then
    local key="${line%%=*}"
    local value="${line#*=}"
    key="${key// /}"  # strip spaces
    value="${value#"\""}"
    value="${value%"\""}"
    echo "$key=$value"
  fi
}

load_env_defaults() {
  if [[ -f "$ENV_FILE" ]]; then
    while IFS= read -r line; do
      local kv
      if kv=$(parse_kv_line "$line"); then
        local key="${kv%%=*}" value="${kv#*=}"
        case "$key" in
          WAN_IF) ENV_WAN_IF="$value" ;;
          WG_IF) ENV_IFNAME="$value" ;;
          WG_PORT) ENV_PORT="$value" ;;
          WG_SUBNET) ENV_SUBNET="$value" ;;
          WG_SUBNET_V6) ENV_SUBNET_V6="$value" ;;
          WG_MTU) ENV_MTU="$value" ;;
          WG_DNS) ENV_DNS="$value" ;;
          WG_FIREWALL) ENV_FIREWALL="$value" ;;
          WG_IPV6) ENV_IPV6="$value" ;;
          WG_ENDPOINT) ENV_ENDPOINT="$value" ;;
          WG_KEEPALIVE) ENV_KEEPALIVE="$value" ;;
          WG_ALLOWED_IPS) ENV_ALLOWED_IPS="$value" ;;
        esac
      fi
    done < "$ENV_FILE"
  fi
}

set_defaults() {
  FIREWALL_MODE="${ENV_FIREWALL:-auto}"
  LISTEN_PORT="${ENV_PORT:-51820}"
  WG_IFNAME="${ENV_IFNAME:-wg0}"
  WAN_IF="${ENV_WAN_IF:-eth0}"
  VPN_SUBNET="${ENV_SUBNET:-10.6.0.0/24}"
  VPN_SUBNET_V6="${ENV_SUBNET_V6:-}"
  RECOMMENDED_MTU="${ENV_MTU:-1420}"
  CLIENT_DNS="${ENV_DNS:-1.1.1.1}"
  ENABLE_IPV6="${ENV_IPV6:-false}"
  ENDPOINT_VALUE="${ENV_ENDPOINT:-}"
  DEFAULT_KEEPALIVE="${ENV_KEEPALIVE:-25}"
  DEFAULT_ALLOWED_IPS="${ENV_ALLOWED_IPS:-0.0.0.0/0, ::/0}"
}

parse_cli() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h)
        usage
        exit 0
        ;;
      --dry-run)
        DRY_RUN=true
        shift
        ;;
      --yes)
        AUTO_CONFIRM=true
        shift
        ;;
      --ipv6=*)
        ENABLE_IPV6="${1#*=}"
        shift
        ;;
      --ipv6)
        ENABLE_IPV6="$2"
        shift 2
        ;;
      --firewall=*)
        FIREWALL_MODE="${1#*=}"
        shift
        ;;
      --firewall)
        FIREWALL_MODE="$2"
        shift 2
        ;;
      --port=*)
        LISTEN_PORT="${1#*=}"
        shift
        ;;
      --port)
        LISTEN_PORT="$2"
        shift 2
        ;;
      --ifname=*)
        WG_IFNAME="${1#*=}"
        shift
        ;;
      --ifname)
        WG_IFNAME="$2"
        shift 2
        ;;
      --wan-if=*)
        WAN_IF="${1#*=}"
        shift
        ;;
      --wan-if)
        WAN_IF="$2"
        shift 2
        ;;
      --subnet=*)
        VPN_SUBNET="${1#*=}"
        shift
        ;;
      --subnet)
        VPN_SUBNET="$2"
        shift 2
        ;;
      --subnet6=*)
        VPN_SUBNET_V6="${1#*=}"
        shift
        ;;
      --subnet6)
        VPN_SUBNET_V6="$2"
        shift 2
        ;;
      --mtu=*)
        RECOMMENDED_MTU="${1#*=}"
        shift
        ;;
      --mtu)
        RECOMMENDED_MTU="$2"
        shift 2
        ;;
      --dns=*)
        CLIENT_DNS="${1#*=}"
        shift
        ;;
      --dns)
        CLIENT_DNS="$2"
        shift 2
        ;;
      --endpoint=*)
        ENDPOINT_VALUE="${1#*=}"
        shift
        ;;
      --endpoint)
        ENDPOINT_VALUE="$2"
        shift 2
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

run_cmd() {
  if $DRY_RUN; then
    log "[DRY-RUN] $*"
  else
    "$@"
  fi
}

ensure_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    ensure_package python3
  fi
}

OS_ID=""
OS_VERSION_ID=""

load_os_release() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    OS_ID="$ID"
    OS_VERSION_ID="$VERSION_ID"
  else
    die "Unable to detect distribution (missing /etc/os-release)."
  fi
}

APT_UPDATED=0
ensure_package() {
  local pkg="$1"
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    return
  fi
  if $DRY_RUN; then
    log "[DRY-RUN] Would install package: $pkg"
    return
  fi
  if [[ $APT_UPDATED -eq 0 ]]; then
    log "Updating apt cache"
    apt-get update -y
    APT_UPDATED=1
  fi
  log "Installing package: $pkg"
  DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg"
}

ensure_dependencies() {
  case "$OS_ID" in
    ubuntu|debian)
      ensure_package qrencode
      ensure_package wireguard
      ensure_package wireguard-tools || true
      ;;
    *)
      warn "Unsupported distribution $OS_ID. Attempting best-effort install."
      ensure_package wireguard || true
      ensure_package qrencode || true
      ;;
  esac

  if [[ "$FIREWALL_MODE" == "ufw" || "$FIREWALL_MODE" == "auto" ]]; then
    if command -v ufw >/dev/null 2>&1; then
      true
    else
      ensure_package ufw
    fi
  fi

  if [[ "$FIREWALL_MODE" == "nftables" ]]; then
    ensure_package nftables
  fi

  if ! command -v wg >/dev/null 2>&1; then
    ensure_package wireguard-tools
  fi

  ensure_python
}

verify_kernel_module() {
  if $DRY_RUN; then
    log "[DRY-RUN] Skipping kernel module probe"
    return
  fi
  if modprobe wireguard >/dev/null 2>&1; then
    log "WireGuard kernel module is available"
    return
  fi
  warn "WireGuard kernel module missing. Installing headers and DKMS fallback."
  ensure_package "linux-headers-$(uname -r)"
  ensure_package wireguard-dkms
  if $DRY_RUN; then
    log "[DRY-RUN] Would attempt to load wireguard module after DKMS install"
  else
    modprobe wireguard || warn "Failed to load wireguard module automatically."
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

ensure_directories() {
  if $DRY_RUN; then
    log "[DRY-RUN] Would create $CONFIG_PATH"
  else
    mkdir -p "$CONFIG_PATH"
    chmod 700 "$CONFIG_PATH"
  fi
}

ensure_server_keys() {
  local priv="$CONFIG_PATH/server_private.key"
  local pub="$CONFIG_PATH/server_public.key"
  if [[ -f "$priv" && -f "$pub" ]]; then
    SERVER_PRIVATE_KEY=$(<"$priv")
    SERVER_PUBLIC_KEY=$(<"$pub")
    return
  fi
  if $DRY_RUN; then
    log "[DRY-RUN] Would generate server key pair at $CONFIG_PATH"
    SERVER_PRIVATE_KEY="<generated-on-apply>"
    SERVER_PUBLIC_KEY="<generated-on-apply>"
    return
  fi
  umask 077
  log "Generating WireGuard server key pair"
  local key
  key=$(wg genkey)
  SERVER_PRIVATE_KEY="$key"
  printf '%s\n' "$SERVER_PRIVATE_KEY" > "$priv"
  chmod 600 "$priv"
  wg pubkey < "$priv" > "$pub"
  chmod 600 "$pub"
  SERVER_PUBLIC_KEY=$(<"$pub")
}

first_address_in_subnet() {
  local subnet="$1"
  python3 - "$subnet" <<'PY'
import ipaddress, sys
net = ipaddress.ip_network(sys.argv[1], strict=False)
if net.num_addresses < 2:
    print(str(net[0]))
else:
    print(f"{net[1]}/{net.prefixlen}")
PY
}

render_template() {
  local template="$1"
  local output="$2"
  shift 2
  local args=()
  for kv in "$@"; do
    local key="${kv%%=*}"
    local value="${kv#*=}"
    value="${value//$'\n'/\\n}"
    args+=("${key}=${value}")
  done
  if $DRY_RUN; then
    log "[DRY-RUN] Rendering $template"
    python3 - "$template" "-" "${args[@]}" <<'PY'
import pathlib, sys


def render(path, out_path, pairs):
    text = pathlib.Path(path).read_text()
    for raw in pairs:
        key, value = raw.split('=', 1)
        value = value.encode().decode('unicode_escape')
        text = text.replace('{{' + key + '}}', value)
    if out_path == '-':
        print(text, end='')
    else:
        pathlib.Path(out_path).write_text(text)


render(sys.argv[1], sys.argv[2], sys.argv[3:])
PY
  else
    python3 - "$template" "$output" "${args[@]}" <<'PY'
import pathlib, sys


def render(path, out_path, pairs):
    text = pathlib.Path(path).read_text()
    for raw in pairs:
        key, value = raw.split('=', 1)
        value = value.encode().decode('unicode_escape')
        text = text.replace('{{' + key + '}}', value)
    pathlib.Path(out_path).write_text(text)


render(sys.argv[1], sys.argv[2], sys.argv[3:])
PY
  fi
}

render_server_config() {
  local server_addr_ipv4
  server_addr_ipv4=$(first_address_in_subnet "$VPN_SUBNET")
  local ipv6_line="# IPv6 disabled"
  if [[ "$ENABLE_IPV6" =~ ^([Tt]rue|1|yes)$ ]]; then
    if [[ -n "$VPN_SUBNET_V6" ]]; then
      local addr
      if addr=$(first_address_in_subnet "$VPN_SUBNET_V6" 2>/dev/null); then
        ipv6_line="Address = $addr"
      else
        warn "Failed to derive IPv6 host address from $VPN_SUBNET_V6"
      fi
    else
      warn "IPv6 forwarding enabled but WG_SUBNET_V6 not set. Add it via --subnet6."
    fi
  elif [[ -n "$VPN_SUBNET_V6" ]]; then
    warn "WG_SUBNET_V6 provided but --ipv6=false; skipping IPv6 address configuration."
  fi

  local postup=$'PostUp = echo "No firewall rules configured"'
  local postdown=$'PostDown = echo "No firewall rules configured"'

  case "$FIREWALL_MODE" in
    auto)
      if command -v ufw >/dev/null 2>&1; then
        FIREWALL_MODE_RESOLVED="ufw"
      else
        FIREWALL_MODE_RESOLVED="iptables"
      fi
      ;;
    ufw|iptables|nftables)
      FIREWALL_MODE_RESOLVED="$FIREWALL_MODE"
      ;;
    *)
      warn "Unknown firewall mode $FIREWALL_MODE, defaulting to auto"
      if command -v ufw >/dev/null 2>&1; then
        FIREWALL_MODE_RESOLVED="ufw"
      else
        FIREWALL_MODE_RESOLVED="iptables"
      fi
      ;;
  esac

  FIREWALL_MODE="$FIREWALL_MODE_RESOLVED"

  case "$FIREWALL_MODE" in
    ufw|iptables)
      postup=$'PostUp = iptables -A INPUT -p udp --dport $LISTEN_PORT -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -s $VPN_SUBNET -o $WAN_IF -j MASQUERADE
PostUp = iptables -A FORWARD -i $WAN_IF -o $WG_IFNAME -m state --state RELATED,ESTABLISHED -j ACCEPT
PostUp = iptables -A FORWARD -i $WG_IFNAME -o $WAN_IF -j ACCEPT'
      postdown=$'PostDown = iptables -D INPUT -p udp --dport $LISTEN_PORT -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -s $VPN_SUBNET -o $WAN_IF -j MASQUERADE
PostDown = iptables -D FORWARD -i $WAN_IF -o $WG_IFNAME -m state --state RELATED,ESTABLISHED -j ACCEPT
PostDown = iptables -D FORWARD -i $WG_IFNAME -o $WAN_IF -j ACCEPT'
      ;;
    nftables)
      postup=$'PostUp = nft -f $NFT_RULES_FILE'
      postdown=$'PostDown = nft delete table inet privatetunnel || true'
      ;;
  esac

  local template="$TEMPLATE_DIR/wg0.conf.template"
  local render_args=(
    "SERVER_ADDR=$server_addr_ipv4"
    "SERVER_ADDR_V6=$ipv6_line"
    "PORT=$LISTEN_PORT"
    "SERVER_PRIVATE_KEY=$SERVER_PRIVATE_KEY"
    "POST_UP_BLOCK=$postup"
    "POST_DOWN_BLOCK=$postdown"
  )

  if $DRY_RUN; then
    render_template "$template" /dev/null "${render_args[@]}"
  else
    local tmp="$SERVER_CONFIG.new"
    render_template "$template" "$tmp" "${render_args[@]}"
    install_config "$tmp"
  fi
}

install_config() {
  local tmp_file="$1"
  if [[ -f "$SERVER_CONFIG" ]]; then
    if cmp -s "$tmp_file" "$SERVER_CONFIG"; then
      log "Server configuration unchanged"
      rm -f "$tmp_file"
      return
    fi
    local backup="$SERVER_CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
    log "Backing up existing configuration to $backup"
    if $DRY_RUN; then
      log "[DRY-RUN] Would copy $SERVER_CONFIG to $backup"
      RESTORE_CONFIG=""
      rm -f "$tmp_file"
      return
    fi
    cp "$SERVER_CONFIG" "$backup"
    RESTORE_CONFIG="$backup"
  fi
  if $DRY_RUN; then
    log "[DRY-RUN] Would move $tmp_file to $SERVER_CONFIG"
    rm -f "$tmp_file"
  else
    mv "$tmp_file" "$SERVER_CONFIG"
    chmod 600 "$SERVER_CONFIG"
    RESTORE_CONFIG=""
  fi
}

apply_sysctl_tuning() {
  local ipv6_forward="0"
  if [[ "$ENABLE_IPV6" =~ ^([Tt]rue|1|yes)$ ]]; then
    ipv6_forward="1"
  fi
  local content="net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=$ipv6_forward
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.ipv4.tcp_mtu_probing=1
"
  if $DRY_RUN; then
    log "[DRY-RUN] Would write sysctl settings to $SYSCTL_DROP_IN"
    printf '%s' "$content"
  else
    local changed=1
    if [[ -f "$SYSCTL_DROP_IN" ]]; then
      if [[ "$(<"$SYSCTL_DROP_IN")" == "$content" ]]; then
        changed=0
      fi
    fi
    if [[ $changed -eq 1 ]]; then
      log "Updating sysctl tunings in $SYSCTL_DROP_IN"
      printf '%s' "$content" > "$SYSCTL_DROP_IN"
      run_cmd sysctl -p "$SYSCTL_DROP_IN"
    else
      log "Sysctl tunings unchanged"
    fi
  fi
}

configure_firewall_backend() {
  case "$FIREWALL_MODE" in
    ufw)
      if command -v ufw >/dev/null 2>&1; then
        if $DRY_RUN; then
          log "[DRY-RUN] Would run ufw allow $LISTEN_PORT/udp"
        else
          ufw allow "$LISTEN_PORT/udp" || warn "Failed to add ufw rule"
          if ! ufw status | grep -q "Status: active"; then
            warn "ufw is not enabled. Enable with 'ufw enable' when ready."
          fi
        fi
      fi
      ;;
    nftables)
      local nft_content="table inet privatetunnel {
  chain postrouting {
    type nat hook postrouting priority srcnat;
    oifname \"$WAN_IF\" ip saddr $VPN_SUBNET counter masquerade
  }
}"
      if $DRY_RUN; then
        log "[DRY-RUN] Would write nftables rules to $NFT_RULES_FILE"
        printf '%s\n' "$nft_content"
      else
        printf '%s\n' "$nft_content" > "$NFT_RULES_FILE"
      fi
      ;;
  esac
}

ensure_service_enabled() {
  if $DRY_RUN; then
    log "[DRY-RUN] Would enable and start wg-quick@$WG_IFNAME"
    return
  fi
  if ! systemctl enable "wg-quick@$WG_IFNAME"; then
    die "Failed to enable systemd unit wg-quick@$WG_IFNAME"
  fi
  if ! systemctl restart "wg-quick@$WG_IFNAME"; then
    journalctl -u "wg-quick@$WG_IFNAME" --no-pager | tail -n 50 >&2
    die "Failed to start WireGuard interface. Previous configuration restored if possible."
  fi
  log "wg-quick@$WG_IFNAME is active"
}

check_port_availability() {
  if ss -lun | awk '{print $5}' | grep -q ":$LISTEN_PORT$"; then
    warn "UDP port $LISTEN_PORT appears to be in use. Review or change with --port."
  fi
}

detect_public_ip() {
  local ip=""
  if [[ -n "$ENDPOINT_VALUE" ]]; then
    echo "$ENDPOINT_VALUE"
    return
  fi
  if command -v curl >/dev/null 2>&1 && ! $DRY_RUN; then
    ip=$(curl -4 -s https://ifconfig.co || true)
    if [[ -n "$ip" ]]; then
      echo "$ip:$LISTEN_PORT"
      return
    fi
  fi
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  if [[ -n "$ip" ]]; then
    warn "Using detected local IP $ip as endpoint placeholder. Override with --endpoint."
    echo "$ip:$LISTEN_PORT"
  else
    echo "<set-endpoint-manually>:${LISTEN_PORT}"
  fi
}

validate_interface() {
  if ! ip -o link show "$WAN_IF" >/dev/null 2>&1; then
    warn "WAN interface $WAN_IF not detected. Pass --wan-if to specify the correct interface."
  fi
}

print_summary() {
  local endpoint
  endpoint=$(detect_public_ip)
  cat <<SUMMARY

===== WireGuard server ready =====
Interface : $WG_IFNAME
Config    : $SERVER_CONFIG
Endpoint  : $endpoint
Subnet    : $VPN_SUBNET
Subnet v6 : ${VPN_SUBNET_V6:-disabled}
Listen    : UDP/$LISTEN_PORT on $WAN_IF
PublicKey : $SERVER_PUBLIC_KEY
MTU       : $RECOMMENDED_MTU
Firewall  : $FIREWALL_MODE

Use server/provision/wg-add-peer.sh to create client profiles.
Check cloud firewall rules to ensure UDP $LISTEN_PORT is allowed.
If your provider blocks UDP, re-run with --port 443.
SUMMARY
}

main() {
  require_root
  load_os_release
  load_env_defaults
  set_defaults
  parse_cli "$@"
  validate_interface
  ensure_directories
  ensure_dependencies
  verify_kernel_module
  check_port_availability
  ensure_server_keys
  render_server_config
  configure_firewall_backend
  apply_sysctl_tuning
  ensure_service_enabled
  print_summary
}

main "$@"
