#!/usr/bin/env bash
#
# Run ASR batch inference on the remote host using scripts/asr_batch.py.
# Supports loading deploy/profiles/.env.active, emitting structured events,
# and producing run manifests for local mirroring.

set -euo pipefail

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

require_env() {
  local name="$1"
  local value="${!name-}"
  if [[ -z "$value" ]]; then
    echo "[remote_run_asr] Missing required environment variable: $name" >&2
    exit 2
  fi
}

emit_event() {
  local event_type="$1"
  shift
  python - <<'PY' "$events_file" "$event_type" "$@"
import json
import sys
from datetime import datetime, timezone

path = sys.argv[1]
event_type = sys.argv[2]
items = {}
for raw in sys.argv[3:]:
    if "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    items[key] = value
record = {
    "event": event_type,
    "timestamp": datetime.now(timezone.utc).isoformat(),
}
record.update(items)
with open(path, "a", encoding="utf-8") as fh:
    json.dump(record, fh, ensure_ascii=False)
    fh.write("\n")
PY
}

update_state() {
  python - <<'PY' "$state_file" "$top_state_file" "$@"
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

run_state = Path(sys.argv[1])
top_state = Path(sys.argv[2])
items = {}
for raw in sys.argv[3:]:
    if "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    items[key] = value
timestamp = datetime.now(timezone.utc).isoformat()
items.setdefault("updated_at", timestamp)
run_state.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
top_state.parent.mkdir(parents=True, exist_ok=True)
top_state.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

# Capture pre-existing overrides so CLI/env exports take precedence over profile.
declare -A OVERRIDES=()
remember_override() {
  local key="$1"
  local value="${!key-}"
  if [[ -n "$value" ]]; then
    OVERRIDES[$key]="$value"
  fi
}

for key in ASR_MODEL ASR_LANGUAGE ASR_DEVICE ASR_COMPUTE ASR_WORKERS ASR_VAD AUDIO_PATTERN ASR_STEMS ASR_OVERWRITE OVERWRITE STEMS RUN_MODE RUN_NOTES ENV_PROFILE ENV_SNAPSHOT_PATH ENV_RUN_ID; do
  remember_override "$key"
done

DEFAULT_MODEL="medium"
DEFAULT_LANGUAGE="zh"
DEFAULT_DEVICE="auto"
DEFAULT_COMPUTE="auto"
DEFAULT_WORKERS="1"
DEFAULT_VAD="true"
DEFAULT_PATTERN="*.m4a,*.wav,*.mp3,*.flac"

PATTERN_OVERRIDE=""
STEMS_OVERRIDE=""
OVERWRITE_FLAG="${ASR_OVERWRITE:-${OVERWRITE:-false}}"
DRY_RUN="false"

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
      OVERRIDES[ASR_WORKERS]="$2"
      shift 2
      ;;
    --model)
      OVERRIDES[ASR_MODEL]="$2"
      shift 2
      ;;
    --language)
      OVERRIDES[ASR_LANGUAGE]="$2"
      shift 2
      ;;
    --device)
      OVERRIDES[ASR_DEVICE]="$2"
      shift 2
      ;;
    --compute)
      OVERRIDES[ASR_COMPUTE]="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift 1
      ;;
    --help)
      cat <<USAGE
Usage: remote_run_asr.sh [options]
  --pattern "*.m4a"     Override audio glob pattern
  --stems "001,002"     Only process the listed stems
  --overwrite           Force regeneration of JSON outputs
  --workers N           Override worker count
  --model NAME          Override model name
  --language LANG       Override language
  --device DEVICE       Override device
  --compute TYPE        Override compute type
  --dry-run             Print commands without executing python
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

# Determine base directory before sourcing profile.
if [[ -z "${VPS_REMOTE_DIR-}" && -n "${REMOTE_DIR-}" ]]; then
  VPS_REMOTE_DIR="$REMOTE_DIR"
fi
require_env "VPS_REMOTE_DIR"
REMOTE_ROOT="$VPS_REMOTE_DIR"

cd "$REMOTE_ROOT"

PROFILE_ENV_PATH="$REMOTE_ROOT/deploy/profiles/.env.active"
if [[ -f "$PROFILE_ENV_PATH" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$PROFILE_ENV_PATH"
  set +a
fi

# Reapply overrides to honour CLI/env precedence.
for key in "${!OVERRIDES[@]}"; do
  printf -v "$key" '%s' "${OVERRIDES[$key]}"
  export "$key"
done

ASR_MODEL="${ASR_MODEL:-$DEFAULT_MODEL}"
ASR_LANGUAGE="${ASR_LANGUAGE:-$DEFAULT_LANGUAGE}"
ASR_DEVICE="${ASR_DEVICE:-$DEFAULT_DEVICE}"
ASR_COMPUTE="${ASR_COMPUTE:-$DEFAULT_COMPUTE}"
ASR_WORKERS="${ASR_WORKERS:-$DEFAULT_WORKERS}"
ASR_VAD="${ASR_VAD:-$DEFAULT_VAD}"
AUDIO_PATTERN="${PATTERN_OVERRIDE:-${AUDIO_PATTERN:-$DEFAULT_PATTERN}}"
ASR_STEMS="${STEMS_OVERRIDE:-${ASR_STEMS:-${STEMS:-}}}"
REMOTE_AUDIO="${REMOTE_AUDIO:-$REMOTE_ROOT/data/audio}"
REMOTE_ASR_JSON="${REMOTE_ASR_JSON:-$REMOTE_ROOT/data/asr-json}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-$REMOTE_ROOT/out}"
RUN_MODE="${RUN_MODE:-}"; export RUN_MODE
RUN_NOTES="${RUN_NOTES:-}"; export RUN_NOTES
ENV_PROFILE="${ENV_PROFILE:-}"; export ENV_PROFILE
ENV_SNAPSHOT_PATH="${ENV_SNAPSHOT_PATH:-}"; export ENV_SNAPSHOT_PATH
ENV_RUN_ID="${ENV_RUN_ID:-}"; export ENV_RUN_ID

mkdir -p "$REMOTE_AUDIO" "$REMOTE_ASR_JSON" "$REMOTE_LOG_DIR"
RUNS_DIR="$REMOTE_LOG_DIR/_runs"
mkdir -p "$RUNS_DIR"

if [[ -n "$ENV_RUN_ID" ]]; then
  RUN_ID="$ENV_RUN_ID"
else
  RUN_ID=$(python - <<'PY'
import datetime, secrets
now = datetime.datetime.utcnow()
print(now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3))
PY
)
fi
RUN_DIR="$RUNS_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"

log_file="$RUN_DIR/asr_job.log"
: >"$log_file"
ln -sf "$log_file" "$REMOTE_LOG_DIR/asr_job.log"

events_file="$RUN_DIR/events.ndjson"
: >"$events_file"
ln -sf "$events_file" "$REMOTE_LOG_DIR/events.ndjson"

state_file="$RUN_DIR/state.json"
top_state_file="$REMOTE_LOG_DIR/state.json"
: >"$state_file"
ln -sf "$state_file" "$top_state_file"

manifest_file="$RUN_DIR/manifest.json"

log_info() {
  echo "[remote_run_asr] $1" | tee -a "$log_file"
}
log_warn() {
  echo "[remote_run_asr][WARN] $1" | tee -a "$log_file" >&2
}
log_err() {
  echo "[remote_run_asr][ERROR] $1" | tee -a "$log_file" >&2
}

SUMMARY_WRITTEN="false"
finish_summary() {
  SUMMARY_WRITTEN="true"
}

on_exit() {
  local code="$1"
  [[ -n "${manifest_tmp-}" ]] && rm -f "$manifest_tmp"
  if [[ "$code" -ne 0 && "$SUMMARY_WRITTEN" != "true" ]]; then
    emit_event job_summary run_id="$RUN_ID" status="failed" exit_code="$code"
    update_state run_id="$RUN_ID" status="failed" exit_code="$code" profile="$ENV_PROFILE" run_mode="$RUN_MODE"
  fi
}
trap 'on_exit $?' EXIT

log_info "启动 ASR 作业：run_id=$RUN_ID profile=${ENV_PROFILE:-<none>}"

GPU_INFO=$(command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv,noheader || true)
STARTED_AT=$(timestamp)
update_state run_id="$RUN_ID" status="running" profile="$ENV_PROFILE" run_mode="$RUN_MODE" started_at="$STARTED_AT" log_file="$log_file" events="$events_file" manifest="$manifest_file" env_snapshot="$ENV_SNAPSHOT_PATH"
emit_event job_start run_id="$RUN_ID" profile="$ENV_PROFILE" run_mode="$RUN_MODE" note="$RUN_NOTES" audio_pattern="$AUDIO_PATTERN" stems="$ASR_STEMS" overwrite="$OVERWRITE_FLAG" workers="$ASR_WORKERS"

if [[ -n "$ASR_STEMS" && -n "$AUDIO_PATTERN" && "$AUDIO_PATTERN" != "$DEFAULT_PATTERN" && -z "$STEMS_OVERRIDE" ]]; then
  log_warn "Profile 同时定义了 STEMS 与 pattern，将优先使用 STEMS。"
fi

manifest_tmp=$(mktemp)

processed_stems=()
total_candidates=0

if [[ -n "$ASR_STEMS" ]]; then
  IFS=',' read -r -a stems_array <<<"$ASR_STEMS"
  if [[ ${#stems_array[@]} -eq 0 ]]; then
    log_err "STEMS 列表为空，结束。"
    exit 2
  fi
  AUDIO_PATTERN=""
  for raw in "${stems_array[@]}"; do
    stem="${raw//[[:space:]]/}"
    [[ -z "$stem" ]] && continue
    mapfile -t matches < <(find "$REMOTE_AUDIO" -maxdepth 1 -type f -name "$stem.*" -printf '%f\n' | sort)
    if [[ ${#matches[@]} -eq 0 ]]; then
      log_warn "[$stem] 未找到匹配音频，跳过。"
      continue
    fi
    processed_stems+=("$stem")
    total_candidates=$((total_candidates + ${#matches[@]}))
    joined_patterns=$(IFS=','; echo "${matches[*]}")
    if [[ -z "$AUDIO_PATTERN" ]]; then
      AUDIO_PATTERN="$joined_patterns"
    else
      AUDIO_PATTERN="$AUDIO_PATTERN,$joined_patterns"
    fi
    out_json="$REMOTE_ASR_JSON/$stem.json"
    if [[ -f "$out_json" && "$OVERWRITE_FLAG" != "true" ]]; then
      log_info "[$stem] 已存在 JSON，将跳过生成（使用 --overwrite 可重跑）。"
    else
      log_info "[$stem] 计划处理：${matches[*]} → ${stem}.json"
    fi
    python - <<'PY' "$manifest_tmp" "$stem" "${matches[@]}"
import json
import sys
path = sys.argv[1]
stem = sys.argv[2]
files = sys.argv[3:]
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"stem": stem, "files": files}, ensure_ascii=False) + "\n")
PY
  done
  if [[ ${#processed_stems[@]} -eq 0 ]]; then
    log_err "未有可处理的音频，任务结束。"
    exit 1
  fi
else
  pattern_json=$(python - <<'PY' "$REMOTE_AUDIO" "$AUDIO_PATTERN"
import json
import sys
from pathlib import Path

audio_dir = Path(sys.argv[1])
patterns = [p.strip() for p in sys.argv[2].split(',') if p.strip()]
if not patterns:
    patterns = ['*.m4a', '*.wav', '*.mp3', '*.flac']
result = []
seen = {}
for pattern in patterns:
    for path in sorted(audio_dir.glob(pattern)):
        if not path.is_file():
            continue
        stem = path.stem
        seen.setdefault(stem, set()).add(path.name)
for stem, files in sorted(seen.items()):
    result.append({"stem": stem, "files": sorted(files)})
print(json.dumps(result, ensure_ascii=False))
PY
)
  pattern_json_trimmed="$(echo "$pattern_json" | tr -d '\n')"
  if [[ "$pattern_json_trimmed" == "[]" ]]; then
    log_err "匹配模式 $AUDIO_PATTERN 未找到任何音频。"
    exit 1
  fi
  python - <<'PY' "$manifest_tmp"
import json
import sys
path = sys.argv[1]
data = json.loads(sys.stdin.read())
with open(path, "a", encoding="utf-8") as fh:
    for item in data:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")
PY <<<"$pattern_json_trimmed"
  mapfile -t processed_stems < <(python - <<'PY' <<<"$pattern_json_trimmed"
import json, sys
data = json.loads(sys.stdin.read())
for item in data:
    print(item["stem"])
PY
)
  total_candidates=$(python - <<'PY' <<<"$pattern_json_trimmed"
import json, sys
data = json.loads(sys.stdin.read())
print(sum(len(item["files"]) for item in data))
PY
)
fi

manifest_inputs=$(python - <<'PY' "$manifest_tmp"
import json
import sys
path = sys.argv[1]
items = []
with open(path, "r", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        items.append(json.loads(line))
print(json.dumps(items, ensure_ascii=False))
PY
)

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
vad_lc="${ASR_VAD,,}"
if [[ "$vad_lc" == "false" || "$vad_lc" == "0" || "$vad_lc" == "no" ]]; then
  cmd+=(--no-vad)
else
  cmd+=(--vad)
fi

log_info "最终匹配 stems：${processed_stems[*]} (共 $total_candidates 个音频候选)"
log_info "执行命令：${cmd[*]}"

EXIT_CODE=0
if [[ "$DRY_RUN" == "true" ]]; then
  log_warn "dry-run 模式：未实际执行 asr_batch。"
else
  if "${cmd[@]}" 2>&1 | tee -a "$log_file"; then
    log_info "ASR job completed successfully."
    EXIT_CODE=0
  else
    EXIT_CODE=$?
    log_err "ASR job failed with exit code $EXIT_CODE."
    tail -n 50 "$log_file" >&2 || true
  fi
fi

success_count=$(grep -a "^成功：" "$log_file" | tail -n 1 | sed 's/[^0-9]//g')
skip_count=$(grep -a "^跳过：" "$log_file" | tail -n 1 | sed 's/[^0-9]//g')
failure_count=$(grep -a "^失败：" "$log_file" | tail -n 1 | sed 's/[^0-9]//g')
if [[ -z "$success_count" ]]; then success_count=0; fi
if [[ -z "$skip_count" ]]; then skip_count=0; fi
if [[ -z "$failure_count" ]]; then failure_count=0; fi

ENDED_AT=$(timestamp)
elapsed=$(( $(date -d "$ENDED_AT" +%s) - $(date -d "$STARTED_AT" +%s) )) || elapsed=0

STATUS="succeeded"
if [[ "$EXIT_CODE" -ne 0 ]]; then
  STATUS="failed"
fi

emit_event job_summary run_id="$RUN_ID" status="$STATUS" exit_code="$EXIT_CODE" success="$success_count" skip="$skip_count" failure="$failure_count" elapsed="$elapsed"
update_state run_id="$RUN_ID" status="$STATUS" exit_code="$EXIT_CODE" profile="$ENV_PROFILE" run_mode="$RUN_MODE" finished_at="$ENDED_AT" success="$success_count" skip="$skip_count" failure="$failure_count" manifest="$manifest_file" env_snapshot="$ENV_SNAPSHOT_PATH"
finish_summary

python - <<'PY' "$manifest_file" "$RUN_ID" "$STARTED_AT" "$ENDED_AT" "$ASR_MODEL" "$ASR_DEVICE" "$ASR_COMPUTE" "$ASR_WORKERS" "$vad_lc" "$AUDIO_PATTERN" "$ASR_STEMS" "$OVERWRITE_FLAG" "$GPU_INFO" "$manifest_inputs" "$ENV_PROFILE" "$RUN_MODE" "$RUN_NOTES" "$ENV_SNAPSHOT_PATH"
import json
import sys
from pathlib import Path

(
    manifest_path,
    run_id,
    started_at,
    ended_at,
    model,
    device,
    compute,
    workers,
    vad,
    pattern,
    stems,
    overwrite,
    gpu_info,
    inputs_json,
    profile,
    run_mode,
    run_notes,
    env_snapshot,
) = sys.argv[1:19]
inputs = json.loads(inputs_json)
manifest = {
    "run_id": run_id,
    "started_at": started_at,
    "finished_at": ended_at,
    "provider": "sync",
    "profile": profile,
    "run_mode": run_mode,
    "run_notes": run_notes,
    "params": {
        "model": model,
        "device": device,
        "compute": compute,
        "workers": workers,
        "vad": vad,
        "pattern": pattern,
        "stems": stems,
        "overwrite": overwrite,
    },
    "inputs": inputs,
    "gpu": gpu_info.splitlines() if gpu_info else [],
    "env_snapshot_path": env_snapshot,
}
Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

log_info "运行清单已写入：$manifest_file"
ln -sf "$RUN_ID" "$REMOTE_LOG_DIR/latest_run"

if [[ "$STATUS" == "succeeded" ]]; then
  log_info "汇总：成功 $success_count · 跳过 $skip_count · 失败 $failure_count · 耗时 ${elapsed}s"
else
  log_warn "作业状态：$STATUS（成功 $success_count · 跳过 $skip_count · 失败 $failure_count）"
fi

echo "产物: manifest => $manifest_file"
exit "$EXIT_CODE"
