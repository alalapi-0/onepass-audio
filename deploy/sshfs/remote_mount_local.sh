#!/usr/bin/env bash
# shellcheck disable=SC1090
set -euo pipefail

log() {
    printf '[%(%H:%M:%S)T] [信息] %s\n' -1 "$1"
}

ok() {
    printf '[%(%H:%M:%S)T] [完成] %s\n' -1 "$1"
}

warn() {
    printf '[%(%H:%M:%S)T] [警告] %s\n' -1 "$1" >&2
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
: "${REVERSE_SSHD_PORT:?未设置 REVERSE_SSHD_PORT}"
: "${LOCAL_USER:?未设置 LOCAL_USER}"
: "${LOCAL_JUNCTION_NAME:?未设置 LOCAL_JUNCTION_NAME}"

if ! command -v sshfs >/dev/null 2>&1; then
    log "安装 sshfs..."
    sudo apt-get update -y
    sudo apt-get install -y sshfs
    ok "sshfs 已安装。"
else
    log "sshfs 已存在。"
fi

mkdir -p "$VPS_MOUNT_POINT"

if mountpoint -q "$VPS_MOUNT_POINT"; then
    ok "挂载点 $VPS_MOUNT_POINT 已挂载。"
    exit 0
fi

start_ts=$SECONDS

SSHFS_ARGS=(
    -p "$REVERSE_SSHD_PORT"
    "$LOCAL_USER@127.0.0.1:/$LOCAL_JUNCTION_NAME"
    "$VPS_MOUNT_POINT"
    -o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,allow_other
)

log "执行：sshfs ${SSHFS_ARGS[*]}"
if ! sshfs "${SSHFS_ARGS[@]}"; then
    abort "sshfs 挂载失败。"
fi

if [[ ! -d "$VPS_MOUNT_POINT" ]]; then
    abort "挂载目录不可用：$VPS_MOUNT_POINT"
fi

if ! ls -la "$VPS_MOUNT_POINT" | head; then
    abort "无法读取挂载点内容。"
fi

elapsed=$((SECONDS - start_ts))
ok "已挂载到 $VPS_MOUNT_POINT（用时 ${elapsed}s）。"
exit 0
