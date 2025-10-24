#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=xcenv.sh
source "${SCRIPT_DIR}/xcenv.sh"

usage() {
  cat <<'HELP'
Usage: scripts/ios_export.sh --method <adhoc|appstore> --export-options <plist>

Exports an .ipa from an existing Xcode archive. The archive path and export
folder are controlled via scripts/xcenv.sh. Provide the ExportOptions plist that
matches your distribution method (templates live in apps/ios/PrivateTunnelApp).
HELP
}

METHOD=""
EXPORT_PLIST=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      usage
      exit 0
      ;;
    --method)
      METHOD="${2:-}"
      shift 2
      ;;
    --export-options)
      EXPORT_PLIST="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${METHOD}" ]]; then
  echo "--method is required" >&2
  usage >&2
  exit 1
fi

if [[ "${METHOD}" != "adhoc" && "${METHOD}" != "appstore" ]]; then
  echo "Unsupported method: ${METHOD}" >&2
  exit 1
fi

if [[ -z "${EXPORT_PLIST}" ]]; then
  echo "--export-options is required" >&2
  usage >&2
  exit 1
fi

if [[ ! -f "${EXPORT_PLIST}" ]]; then
  echo "Export options plist not found: ${EXPORT_PLIST}" >&2
  exit 1
fi

mkdir -p "${EXPORT_PATH}"

set -x
xcodebuild \
  -exportArchive \
  -archivePath "${ARCHIVE_PATH}" \
  -exportOptionsPlist "${EXPORT_PLIST}" \
  -exportPath "${EXPORT_PATH}"
set +x

IPA_PATH=$(find "${EXPORT_PATH}" -maxdepth 1 -name "*.ipa" -print | head -n 1 || true)

if [[ -n "${IPA_PATH}" ]]; then
  echo "Exported IPA: ${IPA_PATH}"
else
  echo "Export completed. Inspect ${EXPORT_PATH} for output." >&2
fi
