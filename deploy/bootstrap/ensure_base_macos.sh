#!/usr/bin/env bash
# -*- coding: utf-8 -*-
#
# Ensure baseline CLI tools (OpenSSH, rsync) are installed on macOS.
# Requires Homebrew; exits with warning if brew is unavailable.

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

if ! command -v brew >/dev/null 2>&1; then
  echo "[WARN] Homebrew is required but not found. Run deploy/bootstrap/ensure_homebrew_macos.sh first." >&2
  exit 1
fi

if [[ "${CONFIRM}" == "ask" ]]; then
  read -r -p "Install/upgrade OpenSSH and rsync via Homebrew? [Y/n] " reply
  if [[ "$reply" =~ ^[Nn]$ ]]; then
    echo "[WARN] Installation skipped by user." >&2
    exit 1
  fi
elif [[ "${CONFIRM}" == "no" ]]; then
  echo "[WARN] Installation declined via --no." >&2
  exit 1
fi

if ! xcode-select -p >/dev/null 2>&1; then
  echo "[INFO] Command Line Tools not detected. Triggering xcode-select --install (requires GUI confirmation)."
  if ! xcode-select --install >/dev/null 2>&1; then
    echo "[WARN] xcode-select --install could not be completed automatically. Please finish the GUI installation and rerun." >&2
    exit 1
  fi
fi

for pkg in openssh rsync; do
  if brew list --versions "$pkg" >/dev/null 2>&1; then
    echo "[INFO] $pkg already installed via Homebrew."
  else
    echo "[INFO] Installing $pkg via Homebrew..."
    brew install "$pkg"
  fi
  if command -v "$pkg" >/dev/null 2>&1; then
    "$pkg" --version 2>&1 | head -n1
  fi
done

eval "$(ssh-agent -s)" >/dev/null 2>&1 || true
ssh-add -l >/dev/null 2>&1 || true

echo "[INFO] macOS base tools ensured."
exit 0
