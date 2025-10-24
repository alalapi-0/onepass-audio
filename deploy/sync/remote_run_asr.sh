#!/usr/bin/env bash
#
# Run ASR batch inference on the remote host using scripts/asr_batch.py.
# Requires environment variables to be set (REMOTE_AUDIO, REMOTE_ASR_JSON, etc.).

set -euo pipefail

require_env() {
  local name="$1"
  local value="${!name-}"
  if [[ -z "$value" ]]; then
    echo "[remote_run_asr] Missing required environment variable: $name" >&2
    exit 2
  fi
}

require_env "VPS_REMOTE_DIR"
require_env "REMOTE_AUDIO"
require_env "REMOTE_ASR_JSON"
require_env "REMOTE_LOG_DIR"

# Optional parameters (may be empty -> use defaults)
ASR_MODEL="${ASR_MODEL:-medium}"
ASR_LANGUAGE="${ASR_LANGUAGE:-zh}"
ASR_DEVICE="${ASR_DEVICE:-auto}"
ASR_COMPUTE="${ASR_COMPUTE:-auto}"
ASR_WORKERS="${ASR_WORKERS:-1}"
AUDIO_PATTERN="${AUDIO_PATTERN:-*.m4a,*.wav,*.mp3,*.flac}"

cd "$VPS_REMOTE_DIR"

if [[ ! -d .venv ]]; then
  echo "[remote_run_asr] Python virtual environment (.venv) is missing. Run deploy/remote_provision.sh first." >&2
  exit 2
fi

mkdir -p "$REMOTE_AUDIO" "$REMOTE_ASR_JSON" "$REMOTE_LOG_DIR"

source .venv/bin/activate

log_file="$REMOTE_LOG_DIR/asr_job.log"
cmd=(python scripts/asr_batch.py \
  --audio-dir "$REMOTE_AUDIO" \
  --out-dir "$REMOTE_ASR_JSON" \
  --pattern "$AUDIO_PATTERN" \
  --model "$ASR_MODEL" \
  --language "$ASR_LANGUAGE" \
  --device "$ASR_DEVICE" \
  --compute-type "$ASR_COMPUTE" \
  --workers "$ASR_WORKERS")

echo "[remote_run_asr] Running: ${cmd[*]}" | tee -a "$log_file"

if "${cmd[@]}" 2>&1 | tee -a "$log_file"; then
  echo "[remote_run_asr] ASR job completed successfully." | tee -a "$log_file"
else
  status=$?
  echo "[remote_run_asr] ASR job failed with exit code $status." | tee -a "$log_file"
  echo "[remote_run_asr] Last 50 log lines:" >&2
  tail -n 50 "$log_file" >&2 || true
  exit $status
fi
