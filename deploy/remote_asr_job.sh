#!/usr/bin/env bash
# remote_asr_job.sh
# 用途：通过 ssh 在远端执行 scripts/asr_batch.py 并确保生成 words 信息。
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
PYTHON_BIN="${CFG[VPS_PYTHON]:-python3}"
VENV_ACTIVATE="${CFG[VPS_VENV_ACTIVATE]:-}"
SSH_PORT="${CFG[VPS_SSH_PORT]:-}"
SSH_KEY="${CFG[VPS_SSH_KEY]:-}"

PATTERN="*.m4a,*.wav,*.mp3,*.flac"
MODEL="medium"
LANGUAGE="zh"
DEVICE="auto"
COMPUTE="auto"
WORKERS=1
STATUS_ONLY=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pattern) PATTERN="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --language) LANGUAGE="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --compute) COMPUTE="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --status) STATUS_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "[deploy] 未知参数：$1" >&2; exit 2 ;;
  esac
done

SSH_OPTS=()
[[ -n "$SSH_PORT" ]] && SSH_OPTS+=("-p" "$SSH_PORT")
[[ -n "$SSH_KEY" ]] && SSH_OPTS+=("-i" "$SSH_KEY")
TARGET="${CFG[VPS_USER]}@${CFG[VPS_HOST]}"

if [[ $STATUS_ONLY -eq 1 ]]; then
  REMOTE_CMD="cd '$REMOTE_ROOT' && ls -lt data/asr-json | head -n 5 && echo '' && nvidia-smi || true"
  echo "[deploy] 查询状态：ssh ${SSH_OPTS[*]} $TARGET -- $REMOTE_CMD"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[deploy] dry-run 模式，未执行。"
    exit 0
  fi
  ssh "${SSH_OPTS[@]}" "$TARGET" "$REMOTE_CMD"
  exit 0
fi

REMOTE_CMD="cd '$REMOTE_ROOT' && "
if [[ -n "$VENV_ACTIVATE" ]]; then
  REMOTE_CMD+="source '$VENV_ACTIVATE' && "
fi
REMOTE_CMD+="$PYTHON_BIN scripts/asr_batch.py --audio-dir data/audio --out-dir data/asr-json"
REMOTE_CMD+=" --pattern '$PATTERN' --model $MODEL --language $LANGUAGE"
REMOTE_CMD+=" --device $DEVICE --compute-type $COMPUTE --workers $WORKERS"

echo "[deploy] 远端 ASR：ssh ${SSH_OPTS[*]} $TARGET -- $REMOTE_CMD"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "[deploy] dry-run 模式，未执行。"
  exit 0
fi
ssh "${SSH_OPTS[@]}" "$TARGET" "$REMOTE_CMD"
