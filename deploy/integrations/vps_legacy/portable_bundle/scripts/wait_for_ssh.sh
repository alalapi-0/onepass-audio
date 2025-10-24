#!/usr/bin/env bash
set -Eeuo pipefail
HOST="${1:?host required}"
TIMEOUT="${2:-600}"
DEADLINE=$((SECONDS + TIMEOUT))
while (( SECONDS < DEADLINE )); do
  if ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@"$HOST" "echo ok" >/dev/null 2>&1; then
    echo "[wait_for_ssh] ready"
    exit 0
  fi
  echo "[wait_for_ssh] waiting ssh on $HOST..."
  sleep 10
done
echo "[wait_for_ssh] timeout ${TIMEOUT}s" >&2
exit 1
