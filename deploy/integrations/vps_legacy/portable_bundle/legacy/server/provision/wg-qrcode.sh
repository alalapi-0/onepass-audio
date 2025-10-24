#!/usr/bin/env bash
# Render a WireGuard client configuration as a QR code (ASCII or PNG).

set -Eeuo pipefail

PNG=false
CONFIG_PATH=""

usage() {
  cat <<'USAGE'
Usage: sudo bash wg-qrcode.sh /path/to/client.conf [--png]

Without --png the QR code is printed to the terminal using UTF-8 art. With
--png a PNG file named after the client is generated in the same directory.
USAGE
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h)
        usage
        exit 0
        ;;
      --png)
        PNG=true
        shift
        ;;
      *)
        if [[ -z "$CONFIG_PATH" ]]; then
          CONFIG_PATH="$1"
          shift
        else
          echo "[ERROR] Unexpected argument: $1" >&2
          exit 1
        fi
        ;;
    esac
  done
}

check_requirements() {
  if [[ -z "$CONFIG_PATH" ]]; then
    usage
    exit 1
  fi
  if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "[ERROR] Config file $CONFIG_PATH not found" >&2
    exit 1
  fi
  if ! command -v qrencode >/dev/null 2>&1; then
    echo "[ERROR] qrencode command not available. Install via apt install qrencode." >&2
    exit 1
  fi
}

render_ascii() {
  qrencode -t ANSIUTF8 < "$CONFIG_PATH"
}

render_png() {
  local dir name output
  dir="$(dirname "$CONFIG_PATH")"
  name="$(basename "${CONFIG_PATH%.conf}")"
  output="$dir/${name}.png"
  qrencode -o "$output" < "$CONFIG_PATH"
  echo "[INFO] PNG QR code saved to $output"
}

main() {
  parse_args "$@"
  check_requirements
  if $PNG; then
    render_png
  else
    render_ascii
  fi
}

main "$@"
