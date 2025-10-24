#!/usr/bin/env bash
# Generate a WireGuard client configuration QR code.
#
# Usage:
#   bash core/qr/gen_qr.sh /path/to/client.conf        # render ANSI QR in terminal
#   bash core/qr/gen_qr.sh /path/to/client.conf --png   # export PNG next to the config
#
# Requirements: qrencode (https://fukuchi.org/works/qrencode/)
# The script avoids printing sensitive configuration when qrencode is missing.

set -euo pipefail

print_help() {
  cat <<'USAGE'
Usage: gen_qr.sh CONFIG [--png]

Render a QR code for a WireGuard client configuration. By default an ANSI QR
code is printed to the terminal. Provide --png to emit CONFIG.png instead.
USAGE
}

if [[ ${#} -lt 1 || ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  print_help
  exit 0
fi

CONFIG_PATH=$1
MODE=${2:-ansi}

if ! command -v qrencode >/dev/null 2>&1; then
  echo "[ERROR] qrencode command not found. Install it via your package manager." >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "[ERROR] configuration file not found: $CONFIG_PATH" >&2
  exit 1
fi

if [[ "$MODE" == "--png" ]]; then
  OUTPUT="${CONFIG_PATH%.*}.png"
  qrencode -t PNG -o "$OUTPUT" < "$CONFIG_PATH"
  echo "[INFO] QR code PNG written to $OUTPUT"
else
  qrencode -t ANSIUTF8 < "$CONFIG_PATH"
fi
