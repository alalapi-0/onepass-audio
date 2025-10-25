# ==== BEGIN: OnePass Patch · R4.5 (deprecated header) ====
# DEPRECATED (kept for fallback)
# This script/path is retained for macOS/PowerShell fallback. Default Windows path no longer uses it.
# To re-enable cross-platform prompts, set environment variable: WIN_ONLY=false
# ==== END: OnePass Patch · R4.5 (deprecated header) ====
#!/usr/bin/env bash
# -*- coding: utf-8 -*-
#
# Ensure Homebrew is installed on macOS systems.
# This script is idempotent and exits with a warning (1) if installation is skipped.

set -euo pipefail

CONFIRM="ask"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)
      CONFIRM="yes"
      shift
      ;;
    --no|-n)
      CONFIRM="no"
      shift
      ;;
    *)
      echo "[WARN] Unknown argument: $1" >&2
      shift
      ;;
  esac
done

if command -v brew >/dev/null 2>&1; then
  echo "[INFO] Homebrew already installed: $(brew --version | head -n1)"
  exit 0
fi

if [[ "${CONFIRM}" == "no" ]]; then
  echo "[WARN] Homebrew installation declined. Please install manually from https://brew.sh" >&2
  exit 1
fi

if [[ "${CONFIRM}" == "ask" ]]; then
  read -r -p "Homebrew is required. Install now? [y/N] " reply
  if [[ ! "$reply" =~ ^[Yy]$ ]]; then
    echo "[WARN] Homebrew installation skipped by user." >&2
    exit 1
  fi
fi

echo "[INFO] Installing Homebrew..."
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

if ! command -v brew >/dev/null 2>&1; then
  echo "[WARN] Homebrew installer completed but brew command not found. Follow brew.sh instructions to finish setup." >&2
  exit 1
fi

echo "[INFO] Homebrew installed: $(brew --version | head -n1)"
exit 0
