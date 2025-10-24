#!/usr/bin/env bash
set -Eeuo pipefail

show_help() {
  cat <<'USAGE'
Usage: source scripts/xcenv.sh

Exports shared environment variables for Xcode build and export scripts.
Override TEAM_ID or PROFILE_NAME by exporting them before sourcing.
USAGE
}

if [[ "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

# Project-wide defaults
export SCHEME="PrivateTunnelApp"
export PROJECT="apps/ios/PrivateTunnelApp/PrivateTunnelApp.xcodeproj"
export ARCHIVE_PATH="${ARCHIVE_PATH:-./build/PrivateTunnel.xcarchive}"
export EXPORT_PATH="${EXPORT_PATH:-./build/export}"
export TEAM_ID="${TEAM_ID:-}"
export PROFILE_NAME="${PROFILE_NAME:-}"
