#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'HELP'
Usage: scripts/bump_version.sh --version <x.y.z> [--build <number>]

Synchronises the marketing version and build number across Info.plist files and
the README version badge.
HELP
}

VERSION=""
BUILD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      usage
      exit 0
      ;;
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --build)
      BUILD="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${VERSION}" ]]; then
  echo "--version is required" >&2
  usage >&2
  exit 1
fi

if [[ -z "${BUILD}" ]]; then
  BUILD="1"
fi

PLIST_BUDDY="/usr/libexec/PlistBuddy"
if [[ ! -x "${PLIST_BUDDY}" ]]; then
  echo "PlistBuddy not found at ${PLIST_BUDDY}. Run this script on macOS." >&2
  exit 1
fi

update_plist() {
  local plist="$1"
  "${PLIST_BUDDY}" -c "Set :CFBundleShortVersionString ${VERSION}" "${plist}" 2>/dev/null || \
    "${PLIST_BUDDY}" -c "Add :CFBundleShortVersionString string ${VERSION}" "${plist}"
  "${PLIST_BUDDY}" -c "Set :CFBundleVersion ${BUILD}" "${plist}" 2>/dev/null || \
    "${PLIST_BUDDY}" -c "Add :CFBundleVersion string ${BUILD}" "${plist}"
}

update_plist "${REPO_ROOT}/apps/ios/PrivateTunnelApp/Info.plist"
update_plist "${REPO_ROOT}/apps/ios/PacketTunnelProvider/Info.plist"

README_FILE="${REPO_ROOT}/README.md"
if [[ -f "${README_FILE}" ]]; then
  sed -i '' -E "s|(version-)[0-9]+(\\.[0-9]+)*(-blue)|\\1${VERSION}\\3|" "${README_FILE}"
  sed -i '' -E "s|(build-)[0-9]+(-blue)|\\1${BUILD}\\2|" "${README_FILE}"
fi

echo "Updated version to ${VERSION} (${BUILD})."
