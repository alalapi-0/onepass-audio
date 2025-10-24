#!/usr/bin/env bash
set -euo pipefail

log() {
    printf '[%(%H:%M:%S)T] [信息] %s\n' -1 "$1"
}

ok() {
    printf '[%(%H:%M:%S)T] [完成] %s\n' -1 "$1"
}

err() {
    printf '[%(%H:%M:%S)T] [错误] %s\n' -1 "$1" >&2
}

abort() {
    err "$1"
    exit 2
}

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE=${SSHFS_ENV_FILE:-"$SCRIPT_DIR/sshfs.env"}
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
else
    abort "缺少环境文件：$ENV_FILE"
fi

: "${VPS_MOUNT_POINT:?未设置 VPS_MOUNT_POINT}"

if ! mountpoint -q "$VPS_MOUNT_POINT"; then
    log "挂载点 $VPS_MOUNT_POINT 当前未挂载。"
    exit 0
fi

if fusermount -u "$VPS_MOUNT_POINT" 2>/dev/null; then
    ok "已卸载 $VPS_MOUNT_POINT。"
    exit 0
fi

if sudo umount "$VPS_MOUNT_POINT" 2>/dev/null; then
    ok "已通过 umount 卸载 $VPS_MOUNT_POINT。"
    exit 0
fi

abort "卸载失败，请手动检查挂载状态。"
