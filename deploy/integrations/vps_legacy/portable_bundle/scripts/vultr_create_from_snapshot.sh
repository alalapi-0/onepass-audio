#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage: VULTR_API_KEY=... SNAPSHOT_ID=... $0 [--region nrt] [--plan vc2-1c-1gb] [--client-name iphone] [--client-addr 10.6.0.2/32] [--wg-port 51820]
USAGE
}

REGION="nrt"
PLAN="vc2-1c-1gb"
CLIENT_NAME="iphone"
CLIENT_ADDR="10.6.0.2/32"
WG_PORT="443"

if [ -n "${PRIVATETUNNEL_WG_PORT:-}" ]; then
  WG_PORT="${PRIVATETUNNEL_WG_PORT}"
elif [ -n "${PT_WG_PORT:-}" ]; then
  WG_PORT="${PT_WG_PORT}"
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --region)
      REGION="$2"; shift 2 ;;
    --plan)
      PLAN="$2"; shift 2 ;;
    --client-name)
      CLIENT_NAME="$2"; shift 2 ;;
    --client-addr)
      CLIENT_ADDR="$2"; shift 2 ;;
    --wg-port)
      WG_PORT="$2"; shift 2 ;;
    -h|--help)
      usage
      exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

case "$WG_PORT" in
  ''|*[!0-9]*)
    echo "WireGuard 端口必须是 1-65535 的数字，当前为: $WG_PORT" >&2
    exit 1
    ;;
  *)
    if [ "$WG_PORT" -lt 1 ] || [ "$WG_PORT" -gt 65535 ]; then
      echo "WireGuard 端口必须位于 1-65535 之间，当前为: $WG_PORT" >&2
      exit 1
    fi
    ;;
esac

: "${VULTR_API_KEY:?Environment variable VULTR_API_KEY is required}"
SNAPSHOT_ID="${SNAPSHOT_ID:-}"
if [ -z "$SNAPSHOT_ID" ]; then
  echo "SNAPSHOT_ID environment variable is required" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
USER_DATA_FINAL="$(mktemp)"
HEADER="$(mktemp)"
PAYLOAD="$(mktemp)"
RESPONSE="$(mktemp)"
trap 'rm -f "$USER_DATA_FINAL" "$HEADER" "$PAYLOAD" "$RESPONSE"' EXIT

{
  echo "#!/bin/bash"
  printf 'export WG_PORT=%q\n' "$WG_PORT"
  printf 'export CLIENT_NAME=%q\n' "$CLIENT_NAME"
  printf 'export CLIENT_ADDR=%q\n' "$CLIENT_ADDR"
  if [ -n "${SSH_PUBLIC_KEY:-}" ]; then
    printf 'export AUTHORIZED_SSH_PUBKEY=%q\n' "$SSH_PUBLIC_KEY"
  fi
} > "$HEADER"

tail -n +2 "$REPO_ROOT/server/cloudinit/user-data.sh" >> "$HEADER"
mv "$HEADER" "$USER_DATA_FINAL"

LABEL="oneclick-$(date +%Y%m%d-%H%M%S)"

jq -n --arg region "$REGION" --arg plan "$PLAN" --arg snapshot_id "$SNAPSHOT_ID" --rawfile user_data "$USER_DATA_FINAL" --arg label "$LABEL" '{region:$region, plan:$plan, snapshot_id:$snapshot_id, label:$label, enable_ipv6:false, backups:"disabled", user_data:$user_data}' > "$PAYLOAD"

curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer $VULTR_API_KEY" \
  -H "Content-Type: application/json" \
  -d @"$PAYLOAD" \
  https://api.vultr.com/v2/instances > "$RESPONSE"

INSTANCE_ID="$(jq -r '.instance.id' "$RESPONSE")"
if [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" = "null" ]; then
  echo "Failed to create instance" >&2
  cat "$RESPONSE" >&2
  exit 1
fi

echo "Instance created: $INSTANCE_ID" >&2

for attempt in $(seq 1 60); do
  sleep 10
  INFO="$(mktemp)"
  curl --silent --show-error --fail-with-body \
    -H "Authorization: Bearer $VULTR_API_KEY" \
    https://api.vultr.com/v2/instances/$INSTANCE_ID > "$INFO"
  STATUS="$(jq -r '.instance.status' "$INFO")"
  IP="$(jq -r '.instance.main_ip' "$INFO")"
  if [ "$STATUS" = "active" ] && [ -n "$IP" ] && [ "$IP" != "null" ]; then
    echo "Instance ready: $IP" >&2
    rm -f "$INFO"
    echo "$IP"
    exit 0
  fi
  rm -f "$INFO"
done

echo "Timed out waiting for instance to become active" >&2
exit 2
