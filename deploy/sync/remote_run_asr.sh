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
ASR_OVERWRITE="${ASR_OVERWRITE:-false}"
ASR_STEMS="${ASR_STEMS:-}"

# Parse CLI overrides
PATTERN_OVERRIDE=""
STEMS_OVERRIDE=""
OVERWRITE_FLAG="$ASR_OVERWRITE"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pattern)
      PATTERN_OVERRIDE="$2"
      shift 2
      ;;
    --stems)
      STEMS_OVERRIDE="$2"
      shift 2
      ;;
    --overwrite)
      OVERWRITE_FLAG="true"
      shift 1
      ;;
    --workers)
      ASR_WORKERS="$2"
      shift 2
      ;;
    --model)
      ASR_MODEL="$2"
      shift 2
      ;;
    --language)
      ASR_LANGUAGE="$2"
      shift 2
      ;;
    --device)
      ASR_DEVICE="$2"
      shift 2
      ;;
    --compute)
      ASR_COMPUTE="$2"
      shift 2
      ;;
    --help)
      cat <<USAGE
Usage: remote_run_asr.sh [--stems csv|--pattern pattern] [--overwrite] [options]
  --stems "001,002"     Only process the listed stems (comma separated)
  --pattern "*.m4a"     Override audio glob pattern
  --overwrite           Force regeneration of existing JSON outputs
  --workers N           Override worker count
  --model NAME          Override model name
  --language LANG       Override language
  --device DEVICE       Override device
  --compute TYPE        Override compute type
USAGE
      exit 0
      ;;
    *)
      echo "[remote_run_asr] Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -n "$PATTERN_OVERRIDE" && -n "$STEMS_OVERRIDE" ]]; then
  echo "[remote_run_asr] --pattern 与 --stems 互斥，请二选一。" >&2
  exit 2
fi

if [[ -n "$STEMS_OVERRIDE" ]]; then
  ASR_STEMS="$STEMS_OVERRIDE"
fi
if [[ -n "$PATTERN_OVERRIDE" ]]; then
  AUDIO_PATTERN="$PATTERN_OVERRIDE"
fi

cd "$VPS_REMOTE_DIR"

if [[ ! -d .venv ]]; then
  echo "[remote_run_asr] Python virtual environment (.venv) is missing. Run deploy/remote_provision.sh first." >&2
  exit 2
fi

mkdir -p "$REMOTE_AUDIO" "$REMOTE_ASR_JSON" "$REMOTE_LOG_DIR"

log_file="$REMOTE_LOG_DIR/asr_job.log"
start_ts=$(date +%s)

source .venv/bin/activate

selected_patterns=""
if [[ -n "$ASR_STEMS" ]]; then
  IFS=',' read -r -a stems_array <<<"$ASR_STEMS"
  declare -a collected=()
  for raw in "${stems_array[@]}"; do
    stem="${raw//[[:space:]]/}"
    [[ -z "$stem" ]] && continue
    mapfile -t matches < <(find "$REMOTE_AUDIO" -maxdepth 1 -type f -name "$stem.*" -printf '%f\n' | sort)
    if [[ ${#matches[@]} -eq 0 ]]; then
      echo "[remote_run_asr] [$stem] 未找到匹配音频，跳过。" | tee -a "$log_file"
      continue
    fi
    out_json="$REMOTE_ASR_JSON/$stem.json"
    if [[ -f "$out_json" && "$OVERWRITE_FLAG" != "true" ]]; then
      echo "[remote_run_asr] [$stem] 已存在 JSON，将在转写阶段跳过（使用 --overwrite 可强制重跑）。" | tee -a "$log_file"
    else
      echo "[remote_run_asr] [$stem] 计划处理：${matches[*]} → ${stem}.json" | tee -a "$log_file"
    fi
    for item in "${matches[@]}"; do
      collected+=("$item")
    done
  done
  if [[ ${#collected[@]} -eq 0 ]]; then
    echo "[remote_run_asr] 未有可处理的音频，任务结束。" | tee -a "$log_file"
    exit 1
  fi
  selected_patterns=$(IFS=','; echo "${collected[*]}")
  AUDIO_PATTERN="$selected_patterns"
fi

cmd=(python -u scripts/asr_batch.py \
  --audio-dir "$REMOTE_AUDIO" \
  --out-dir "$REMOTE_ASR_JSON" \
  --pattern "$AUDIO_PATTERN" \
  --model "$ASR_MODEL" \
  --language "$ASR_LANGUAGE" \
  --device "$ASR_DEVICE" \
  --compute-type "$ASR_COMPUTE" \
  --workers "$ASR_WORKERS")
if [[ "$OVERWRITE_FLAG" == "true" ]]; then
  cmd+=(--overwrite)
fi

export ONEPASS_VERBOSE=1

echo "[remote_run_asr] Running: ${cmd[*]}" | tee -a "$log_file"

if "${cmd[@]}" 2>&1 | tee -a "$log_file"; then
  echo "[remote_run_asr] ASR job completed successfully." | tee -a "$log_file"
  exit_code=0
else
  exit_code=$?
  echo "[remote_run_asr] ASR job failed with exit code $exit_code." | tee -a "$log_file"
  echo "[remote_run_asr] Last 50 log lines:" >&2
  tail -n 50 "$log_file" >&2 || true
fi

success_count=$(grep -a "^成功：" "$log_file" | tail -n 1 | sed 's/[^0-9]//g')
skip_count=$(grep -a "^跳过：" "$log_file" | tail -n 1 | sed 's/[^0-9]//g')
failure_count=$(grep -a "^失败：" "$log_file" | tail -n 1 | sed 's/[^0-9]//g')
end_ts=$(date +%s)
elapsed=$((end_ts - start_ts))
if [[ -z "$success_count" ]]; then success_count=0; fi
if [[ -z "$skip_count" ]]; then skip_count=0; fi
if [[ -z "$failure_count" ]]; then failure_count=0; fi

echo "[remote_run_asr] 汇总：成功 $success_count · 跳过 $skip_count · 失败 $failure_count · 耗时 ${elapsed}s" | tee -a "$log_file"

exit "$exit_code"
