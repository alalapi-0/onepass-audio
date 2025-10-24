#!/usr/bin/env bash
# Create a new WireGuard peer profile with automatic IP assignment and optional
# QR code output. The script is idempotent: it refuses to overwrite an existing
# peer unless --force is provided.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
CONFIG_DIR="/etc/wireguard"
DEFAULT_SUBNET="10.6.0.0/24"
DEFAULT_DNS="1.1.1.1"
DEFAULT_KEEPALIVE=25
DEFAULT_ALLOWED_IPS="0.0.0.0/0, ::/0"
WG_IFNAME="wg0"
DEFAULT_ENDPOINT=""
CLIENT_NAME=""
CLIENT_IP_OVERRIDE=""
CLIENT_MTU=""
CLIENT_DNS=""
CLIENT_KEEPALIVE=""
CLIENT_ALLOWED_IPS=""
CLIENT_CIDR=""
REQUEST_QR=false
FORCE=false
AUTO_CONFIRM=false
CURRENT_BACKUP=""
CLIENT_DIR=""
CLIENT_CONF_PATH=""
CLIENT_PRIVATE_KEY=""
CLIENT_PUBLIC_KEY=""

cleanup() {
  if [[ -n "$CURRENT_BACKUP" && -f "$CURRENT_BACKUP" ]]; then
    warn "Restoring configuration from $CURRENT_BACKUP due to error"
    cp "$CURRENT_BACKUP" "$CONFIG_DIR/$WG_IFNAME.conf"
  fi
}

trap cleanup ERR

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

die() {
  err "$*"
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: sudo bash wg-add-peer.sh --name <client> [options]

Required:
  --name <client>        Unique identifier for the peer (letters, numbers, -,_)

Optional:
  --ip <address>         Assign specific IPv4 address within the VPN subnet
  --mtu <value>          MTU override for the client profile
  --dns <servers>        Comma-separated DNS servers (defaults from .env or 1.1.1.1)
  --keepalive <seconds>  PersistentKeepalive override (default 25)
  --allowed-ips <cidrs>  AllowedIPs override (default 0.0.0.0/0, ::/0)
  --qrcode               Print a QR code for the generated configuration
  --force                Replace existing peer with the same name (prompts)
  --yes                  Assume "yes" for confirmation prompts
  --ifname <wg0>         Target interface (default wg0 or .env WG_IF)

The script stores client artifacts under /etc/wireguard/clients/<name>/ and
appends the peer block to /etc/wireguard/<ifname>.conf with a timestamped
backup. To revoke a peer later run wg-revoke-peer.sh.
USAGE
}

validate_name() {
  [[ "$CLIENT_NAME" =~ ^[A-Za-z0-9_-]+$ ]] || die "Client name must match [A-Za-z0-9_-]+"
}

parse_env() {
  [[ -f "$ENV_FILE" ]] || return
  while IFS='=' read -r key value; do
    [[ -z "$key" || "${key:0:1}" == "#" ]] && continue
    case "$key" in
      WG_IF) WG_IFNAME="${value}" ;;
      WG_SUBNET) DEFAULT_SUBNET="${value}" ;;
      WG_DNS) DEFAULT_DNS="${value}" ;;
      WG_KEEPALIVE) DEFAULT_KEEPALIVE="${value}" ;;
      WG_ALLOWED_IPS) DEFAULT_ALLOWED_IPS="${value}" ;;
      WG_ENDPOINT) DEFAULT_ENDPOINT="${value}" ;;
      WG_MTU) CLIENT_MTU="${CLIENT_MTU:-${value}}" ;;
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
      --name)
        CLIENT_NAME="$2"
        shift 2
        ;;
      --name=*)
        CLIENT_NAME="${1#*=}"
        shift
        ;;
      --ip)
        CLIENT_IP_OVERRIDE="$2"
        shift 2
        ;;
      --ip=*)
        CLIENT_IP_OVERRIDE="${1#*=}"
        shift
        ;;
      --mtu)
        CLIENT_MTU="$2"
        shift 2
        ;;
      --mtu=*)
        CLIENT_MTU="${1#*=}"
        shift
        ;;
      --dns)
        CLIENT_DNS="$2"
        shift 2
        ;;
      --dns=*)
        CLIENT_DNS="${1#*=}"
        shift
        ;;
      --keepalive)
        CLIENT_KEEPALIVE="$2"
        shift 2
        ;;
      --keepalive=*)
        CLIENT_KEEPALIVE="${1#*=}"
        shift
        ;;
      --allowed-ips)
        CLIENT_ALLOWED_IPS="$2"
        shift 2
        ;;
      --allowed-ips=*)
        CLIENT_ALLOWED_IPS="${1#*=}"
        shift
        ;;
      --qrcode)
        REQUEST_QR=true
        shift
        ;;
      --force)
        FORCE=true
        shift
        ;;
      --yes)
        AUTO_CONFIRM=true
        shift
        ;;
      --ifname)
        WG_IFNAME="$2"
        shift 2
        ;;
      --ifname=*)
        WG_IFNAME="${1#*=}"
        shift
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "wg-add-peer.sh must run as root"
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
}

collect_used_ips() {
  python3 - "$CONFIG_DIR" "$WG_IFNAME" "$DEFAULT_SUBNET" <<'PY'
import ipaddress, pathlib, sys
config_dir = pathlib.Path(sys.argv[1])
ifname = sys.argv[2]
subnet = ipaddress.ip_network(sys.argv[3], strict=False)
used = set()
config_file = config_dir / f"{ifname}.conf"
if config_file.exists():
    for line in config_file.read_text().splitlines():
        line = line.strip()
        if line.startswith('Address ='):
            for token in line.split('=', 1)[1].split(','):
                token = token.strip()
                if '/' in token:
                    try:
                        addr = ipaddress.ip_interface(token)
                        if addr.version == 4:
                            used.add(addr.ip)
                    except ValueError:
                        pass
        if line.startswith('AllowedIPs ='):
            for token in line.split('=', 1)[1].split(','):
                token = token.strip()
                if '/' in token:
                    try:
                        network = ipaddress.ip_network(token, strict=False)
                    except ValueError:
                        continue
                    if network.version == 4:
                        if network.prefixlen >= 32:
                            used.add(network.network_address)
                        elif network.subnet_of(subnet):
                            used.update(host for host in network.hosts())
clients_dir = config_dir / 'clients'
if clients_dir.exists():
    for conf in clients_dir.glob('*/*.conf'):
        for line in conf.read_text().splitlines():
            if line.startswith('Address ='):
                addr = line.split('=', 1)[1].strip().split(',')[0]
                try:
                    iface = ipaddress.ip_interface(addr)
                    if iface.version == 4:
                        used.add(iface.ip)
                except ValueError:
                    pass
                break
print('\n'.join(str(ip) for ip in sorted(used, key=lambda x: int(x))))
PY
}

allocate_ip() {
  local requested="$1"
  local used_ips
  mapfile -t used_ips < <(collect_used_ips)
  python3 - "$DEFAULT_SUBNET" "$requested" "$FORCE" "${used_ips[@]}" <<'PY'
import ipaddress, sys
subnet = ipaddress.ip_network(sys.argv[1], strict=False)
requested = sys.argv[2]
force = sys.argv[3].lower() == 'true'
used = {ipaddress.ip_address(v) for v in sys.argv[4:]}
if requested:
    try:
        ip = ipaddress.ip_address(requested)
    except ValueError as exc:
        raise SystemExit(f"Invalid IP: {exc}")
    if ip not in subnet:
        raise SystemExit(f"Requested IP {ip} is outside {subnet}")
    if ip in used and not force:
        raise SystemExit(f"Requested IP {ip} already in use")
    print(ip)
else:
    for host in subnet.hosts():
        if host not in used:
            print(host)
            break
    else:
        raise SystemExit("No free IPs available in subnet")
PY
}

read_server_public_key() {
  local key_file="$CONFIG_DIR/server_public.key"
  if [[ -f "$key_file" ]]; then
    SERVER_PUBLIC_KEY=$(<"$key_file")
  else
    SERVER_PUBLIC_KEY=$(wg show "$WG_IFNAME" public-key || true)
    if [[ -z "$SERVER_PUBLIC_KEY" ]]; then
      die "Cannot determine server public key. Ensure wg-install.sh was run."
    fi
  fi
}

read_listen_port() {
  local conf="$CONFIG_DIR/$WG_IFNAME.conf"
  [[ -f "$conf" ]] || die "Server configuration $conf not found"
  LISTEN_PORT=$(grep -E '^ListenPort' "$conf" | awk -F'=' '{gsub(/ /,""); print $2}' | tail -n1)
  if [[ -z "$LISTEN_PORT" ]]; then
    warn "ListenPort not found in $conf; defaulting to 51820"
    LISTEN_PORT=51820
  fi
}

detect_endpoint() {
  if [[ -n "${DEFAULT_ENDPOINT:-}" ]]; then
    if [[ "$DEFAULT_ENDPOINT" == *:* ]]; then
      ENDPOINT="$DEFAULT_ENDPOINT"
    else
      ENDPOINT="$DEFAULT_ENDPOINT:$LISTEN_PORT"
    fi
    return
  fi
  local ip
  if command -v curl >/dev/null 2>&1; then
    ip=$(curl -4 -s https://ifconfig.co || true)
  fi
  if [[ -z "$ip" ]]; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [[ -n "$ip" ]]; then
      warn "Using detected local IP $ip as endpoint placeholder; override via WG_ENDPOINT in .env"
    fi
  fi
  ENDPOINT="${ip:-<public-ip-required>}:$LISTEN_PORT"
}

prepare_client_directory() {
  CLIENT_DIR="$CONFIG_DIR/clients/$CLIENT_NAME"
  if [[ -d "$CLIENT_DIR" ]]; then
    if ! $FORCE; then
      die "Client $CLIENT_NAME already exists. Use --force to overwrite."
    fi
    if ! confirm "Client $CLIENT_NAME exists. Replace it?"; then
      log "Aborted"
      exit 0
    fi
    if [[ -x "$SCRIPT_DIR/wg-revoke-peer.sh" ]]; then
      bash "$SCRIPT_DIR/wg-revoke-peer.sh" --name "$CLIENT_NAME" --keep-files=true --yes --ifname "$WG_IFNAME" || warn "Failed to prune existing peer via wg-revoke-peer.sh"
    fi
    rm -rf "$CLIENT_DIR"
  fi
  mkdir -p "$CLIENT_DIR"
  chmod 700 "$CLIENT_DIR"
}

generate_keys() {
  umask 077
  CLIENT_PRIVATE_KEY=$(wg genkey)
  printf '%s\n' "$CLIENT_PRIVATE_KEY" > "$CLIENT_DIR/private.key"
  chmod 600 "$CLIENT_DIR/private.key"
  CLIENT_PUBLIC_KEY=$(wg pubkey < "$CLIENT_DIR/private.key")
  printf '%s\n' "$CLIENT_PUBLIC_KEY" > "$CLIENT_DIR/public.key"
  chmod 600 "$CLIENT_DIR/public.key"
}

append_peer_config() {
  local conf="$CONFIG_DIR/$WG_IFNAME.conf"
  local backup="$conf.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$conf" "$backup"
  log "Backup stored at $backup"
  CURRENT_BACKUP="$backup"
  local peer_block="# Client $CLIENT_NAME\n# Added $(date --iso-8601=seconds)\n[Peer]\nPublicKey = $CLIENT_PUBLIC_KEY\nAllowedIPs = $CLIENT_CIDR\nPersistentKeepalive = $CLIENT_KEEPALIVE\n"
  printf '\n%b\n' "$peer_block" >> "$conf"
  TMP_PEER=$(mktemp)
  printf '[Peer]\nPublicKey = %s\nAllowedIPs = %s\nPersistentKeepalive = %s\n' "$CLIENT_PUBLIC_KEY" "$CLIENT_CIDR" "$CLIENT_KEEPALIVE" > "$TMP_PEER"
  if ! wg addconf "$WG_IFNAME" "$TMP_PEER"; then
    cp "$backup" "$conf"
    rm -f "$TMP_PEER"
    die "Failed to apply configuration via wg addconf"
  fi
  rm -f "$TMP_PEER"
  CURRENT_BACKUP=""
  log "Peer appended to $conf"
}

write_client_profile() {
  local tmpl="$SCRIPT_DIR/templates/client.conf.template"
  local conf_path="$CLIENT_DIR/$CLIENT_NAME.conf"
  local mtu_line="# MTU not set"
  if [[ -n "$CLIENT_MTU" ]]; then
    mtu_line="MTU = $CLIENT_MTU"
  fi
  render_template "$tmpl" "$conf_path" \
    "CLIENT_PRIVATE_KEY=$CLIENT_PRIVATE_KEY" \
    "CLIENT_ADDR=$CLIENT_IP/32" \
    "CLIENT_DNS=${CLIENT_DNS}" \
    "CLIENT_MTU_LINE=$mtu_line" \
    "SERVER_PUBLIC_KEY=$SERVER_PUBLIC_KEY" \
    "ENDPOINT=$ENDPOINT" \
    "ALLOWED_IPS=$CLIENT_ALLOWED_IPS" \
    "KEEPALIVE=$CLIENT_KEEPALIVE"
  chmod 600 "$conf_path"
  CLIENT_CONF_PATH="$conf_path"
}

print_summary() {
  cat <<INFO
Client created successfully.
  Name: $CLIENT_NAME
  Assigned IP: $CLIENT_IP/32
  DNS: $CLIENT_DNS
  AllowedIPs: $CLIENT_ALLOWED_IPS
  PersistentKeepalive: $CLIENT_KEEPALIVE
  Endpoint: $ENDPOINT
  Public key: $CLIENT_PUBLIC_KEY
  Private key stored at: $CLIENT_DIR/private.key  (KEEP SECRET!)
  Client profile: $CLIENT_CONF_PATH

Import the configuration into your device. On iOS open the WireGuard app and
scan the QR code if requested. Verify connectivity with 'wg show $WG_IFNAME'.
INFO
}

main() {
  require_root
  parse_env
  parse_args "$@"
  [[ -n "$CLIENT_NAME" ]] || die "--name is required"
  validate_name

  [[ -n "$CLIENT_DNS" ]] || CLIENT_DNS="$DEFAULT_DNS"
  [[ -n "$CLIENT_KEEPALIVE" ]] || CLIENT_KEEPALIVE="$DEFAULT_KEEPALIVE"
  [[ -n "$CLIENT_ALLOWED_IPS" ]] || CLIENT_ALLOWED_IPS="$DEFAULT_ALLOWED_IPS"

  read_listen_port
  detect_endpoint
  read_server_public_key

  if [[ "$CLIENT_IP_OVERRIDE" == */* ]]; then
    CLIENT_IP_OVERRIDE="${CLIENT_IP_OVERRIDE%%/*}"
  fi
  CLIENT_IP=$(allocate_ip "$CLIENT_IP_OVERRIDE")
  CLIENT_CIDR="$CLIENT_IP/32"
  prepare_client_directory
  generate_keys
  write_client_profile
  append_peer_config

  if $REQUEST_QR; then
    bash "$SCRIPT_DIR/wg-qrcode.sh" "$CLIENT_CONF_PATH" || warn "Failed to render QR code"
  fi

  print_summary
}

main "$@"
