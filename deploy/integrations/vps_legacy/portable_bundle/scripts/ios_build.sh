#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=xcenv.sh
source "${SCRIPT_DIR}/xcenv.sh"

usage() {
  cat <<'HELP'
Usage: scripts/ios_build.sh [--no-sign]

Creates an Xcode archive for the PrivateTunnel iOS app.
By default the command respects the signing configuration in the project.
Pass --no-sign to disable code signing (equivalent to CODE_SIGNING_ALLOWED=NO).
HELP
}

CODE_SIGNING_FLAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      usage
      exit 0
      ;;
    --no-sign)
      CODE_SIGNING_FLAG="CODE_SIGNING_ALLOWED=NO"
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

mkdir -p "$(dirname "${ARCHIVE_PATH}")"

set -x
xcodebuild \
  -project "${PROJECT}" \
  -scheme "${SCHEME}" \
  -configuration Release \
  -sdk iphoneos \
  -archivePath "${ARCHIVE_PATH}" \
  clean archive ${CODE_SIGNING_FLAG}
set +x

echo "Archive available at: ${ARCHIVE_PATH}"
