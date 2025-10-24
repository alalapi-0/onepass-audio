#!/usr/bin/env bash
# remote_provision.sh
# 用途：通过 ssh 在远端 VPS 上初始化 onepass 运行目录。
# 依赖：bash、ssh；配置文件 deploy/vps.env。
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE="$SCRIPT_DIR/vps.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[deploy] 未找到 $ENV_FILE，请复制 vps.env.example 并填写连接信息。" >&2
  exit 2
fi

declare -A CFG
while IFS='=' read -r key value; do
  [[ -z "$key" || "$key" =~ ^# ]] && continue
  CFG[$key]="$value"
done < "$ENV_FILE"

: "${CFG[VPS_HOST]:?missing VPS_HOST}"
: "${CFG[VPS_USER]:?missing VPS_USER}"
REMOTE_ROOT="${CFG[VPS_REMOTE_ROOT]:-/home/ubuntu/onepass}"
SSH_PORT="${CFG[VPS_SSH_PORT]:-}"
SSH_KEY="${CFG[VPS_SSH_KEY]:-}"
DRY_RUN=0
if [[ ${1:-} == "--dry-run" ]]; then
  DRY_RUN=1
fi

SSH_OPTS=()
[[ -n "$SSH_PORT" ]] && SSH_OPTS+=("-p" "$SSH_PORT")
[[ -n "$SSH_KEY" ]] && SSH_OPTS+=("-i" "$SSH_KEY")
TARGET="${CFG[VPS_USER]}@${CFG[VPS_HOST]}"

REMOTE_CMD="mkdir -p '$REMOTE_ROOT/data/audio' '$REMOTE_ROOT/data/asr-json' && echo 'REMOTE PATH: $REMOTE_ROOT'"

echo "[deploy] 远端初始化：ssh ${SSH_OPTS[*]} $TARGET -- $REMOTE_CMD"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "[deploy] dry-run 模式，未执行。"
  exit 0
fi
ssh "${SSH_OPTS[@]}" "$TARGET" "$REMOTE_CMD"
