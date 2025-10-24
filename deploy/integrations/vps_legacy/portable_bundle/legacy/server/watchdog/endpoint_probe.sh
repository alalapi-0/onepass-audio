#!/usr/bin/env bash
#
# endpoint_probe.sh
# -------------------
# Lightweight watchdog script that validates upstream connectivity from the VPN
# gateway. It performs an ICMP ping to Cloudflare (1.1.1.1) and an HTTPS HEAD
# request to Apple (https://www.apple.com). Failures are logged to stderr and an
# optional restart command can be executed to revive toy_tun_gateway.py or
# wg-quick.
#
# Usage:
#   TARGET_ICMP=8.8.8.8 TARGET_HTTP=https://example.com RESTART_COMMAND="systemctl restart toy-gateway" ./endpoint_probe.sh
#
set -euo pipefail

TARGET_ICMP=${TARGET_ICMP:-"1.1.1.1"}
TARGET_HTTP=${TARGET_HTTP:-"https://www.apple.com"}
PING_COUNT=${PING_COUNT:-3}
PING_TIMEOUT=${PING_TIMEOUT:-3}
HTTP_TIMEOUT=${HTTP_TIMEOUT:-10}
RESTART_COMMAND=${RESTART_COMMAND:-""}
LOG_PREFIX=${LOG_PREFIX:-"[endpoint_probe]"}

log() {
    echo "${LOG_PREFIX} $1" >&2
}

log "Probing ICMP target ${TARGET_ICMP}"
if ping -c "${PING_COUNT}" -W "${PING_TIMEOUT}" "${TARGET_ICMP}" >/dev/null 2>&1; then
    log "ICMP reachability OK"
else
    log "ICMP probe FAILED"
    FAILURE=1
fi

log "Probing HTTPS target ${TARGET_HTTP}"
if curl -fsS --max-time "${HTTP_TIMEOUT}" -o /dev/null -I "${TARGET_HTTP}"; then
    log "HTTPS reachability OK"
else
    log "HTTPS probe FAILED"
    FAILURE=1
fi

if [[ -n "${FAILURE:-}" ]]; then
    if [[ -n "${RESTART_COMMAND}" ]]; then
        log "Triggering restart command: ${RESTART_COMMAND}"
        if bash -c "${RESTART_COMMAND}"; then
            log "Restart command executed successfully"
        else
            log "Restart command failed"
        fi
    else
        log "No restart command configured. Inspect connectivity manually."
    fi
else
    log "All probes successful"
fi
