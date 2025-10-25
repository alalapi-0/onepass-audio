# ==== BEGIN: OnePass Patch · R3 (sync_fetch_win) ====
"""Fetch ASR outputs and logs from the VPS (Windows Python version)."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

PROJ_ROOT = Path(__file__).resolve().parent.parent
SYNC_ENV = PROJ_ROOT / "deploy" / "sync" / "sync.env"
LOCAL_JSON_ROOT = PROJ_ROOT / "onepass" / "data" / "asr-json"
LOCAL_LOG_ROOT = PROJ_ROOT / "onepass" / "out" / "remote_mirror"


@dataclass
class RemoteFile:
    path: str
    size: int
    mtime: float


def _load_env_file() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not SYNC_ENV.exists():
        return env
    for raw in SYNC_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip().strip('"')
    return env


def _resolve_value(
    key: str,
    env_vars: Dict[str, str],
    file_vars: Dict[str, str],
    default: str | None = None,
) -> str | None:
    if key in env_vars and env_vars[key]:
        return env_vars[key]
    if key in file_vars and file_vars[key]:
        return file_vars[key]
    return default


def _ssh_base(user: str, host: str, key: str | None) -> List[str]:
    parts = ["ssh", "-o", "BatchMode=yes"]
    if key:
        parts.extend(["-i", key])
    parts.append(f"{user}@{host}")
    return parts


def _rsync_available() -> bool:
    return shutil.which("rsync") is not None


def _run_ssh_capture(cmd: Sequence[str], text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
    )


def _list_remote_json_files(
    base_cmd: Sequence[str],
    remote_dir: str,
    since_iso: str | None,
) -> Tuple[List[RemoteFile], bool]:
    remote_dir_q = shlex.quote(remote_dir.rstrip("/"))
    find_filter = "-name '*.json'"
    warn = False
    if since_iso:
        cmd_since = f"if [ -d {remote_dir_q} ]; then cd {remote_dir_q} && find . -type f {find_filter} -newermt {shlex.quote(since_iso)} -printf '%P\\t%T@\\t%s\\n'; fi"
        proc = _run_ssh_capture([*base_cmd, cmd_since])
        if proc.returncode != 0:
            warn = True
            cmd_all = f"if [ -d {remote_dir_q} ]; then cd {remote_dir_q} && find . -type f {find_filter} -printf '%P\\t%T@\\t%s\\n'; fi"
            proc = _run_ssh_capture([*base_cmd, cmd_all])
    else:
        cmd_all = f"if [ -d {remote_dir_q} ]; then cd {remote_dir_q} && find . -type f {find_filter} -printf '%P\\t%T@\\t%s\\n'; fi"
        proc = _run_ssh_capture([*base_cmd, cmd_all])
        if proc.returncode != 0:
            warn = True
    files: List[RemoteFile] = []
    if proc.stdout:
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            rel = parts[0].lstrip("./")
            try:
                mtime = float(parts[1])
            except ValueError:
                mtime = 0.0
            try:
                size = int(parts[2])
            except ValueError:
                size = 0
            files.append(RemoteFile(rel, size, mtime))
    files.sort(key=lambda item: item.path)
    return files, warn


def _build_rsync_cmd(
    remote_dir: str,
    local_dir: Path,
    base_cmd: Sequence[str],
    includes: Sequence[str] | None,
) -> List[str]:
    ssh_parts: List[str] = []
    for part in base_cmd[:-1]:
        if part == "ssh":
            ssh_parts.append(part)
        else:
            ssh_parts.append(shlex.quote(part))
    ssh = " ".join(ssh_parts)
    user_host = base_cmd[-1]
    cmd: List[str] = [
        "rsync",
        "-av",
        "--info=stats2,progress2",
        "--partial",
        "--inplace",
        "--stats",
        "-e",
        ssh,
    ]
    if includes:
        cmd.extend(["--include", "*/"])
        for item in includes:
            cmd.extend(["--include", item])
        cmd.extend(["--exclude", "*"])
    cmd.append(f"{user_host}:{remote_dir.rstrip('/')}/")
    cmd.append(str(local_dir) + "/")
    return cmd


def _run_rsync(cmd: Sequence[str]) -> Tuple[int, int, int, float]:
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    start = time.time()
    lines: List[str] = []
    for line in proc.stdout:
        print(line, end="")
        lines.append(line)
    rc = proc.wait()
    duration = max(0.001, time.time() - start)
    transferred = 0
    total_bytes = 0
    for line in lines:
        if "Number of regular files transferred" in line:
            parts = line.strip().split(":")
            if len(parts) >= 2:
                try:
                    transferred = int(parts[1].split()[0])
                except ValueError:
                    pass
        if "Total transferred file size" in line:
            parts = line.strip().split(":")
            if len(parts) >= 2:
                token = parts[1].split()[0]
                try:
                    total_bytes = int(float(token))
                except ValueError:
                    continue
    return rc, transferred, total_bytes, duration


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _backup_existing(path: Path) -> None:
    if not path.exists():
        return
    suffix = 1
    while True:
        candidate = path.with_suffix(path.suffix + f".part{suffix}")
        if not candidate.exists():
            path.rename(candidate)
            break
        suffix += 1


def _scp_fetch(
    files: Sequence[RemoteFile],
    base_cmd: Sequence[str],
    remote_dir: str,
    local_dir: Path,
    verbose: bool,
) -> Tuple[int, int, int, float]:
    if not files:
        return 0, 0, 0, 0.0
    count = 0
    total_bytes = 0
    start = time.time()
    for item in files:
        remote_path = f"{remote_dir.rstrip('/')}/{item.path}"
        dest = local_dir / item.path
        _ensure_dir(dest.parent)
        cmd = ["scp"] + list(base_cmd[1:-1])
        cmd.extend([f"{base_cmd[-1]}:{remote_path}", str(dest)])
        if verbose:
            print("[debug] scp cmd:", " ".join(cmd))
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            return 2, count, total_bytes, time.time() - start
        count += 1
        total_bytes += item.size
    return 0, count, total_bytes, time.time() - start


def _detect_run_id(base_cmd: Sequence[str], remote_log_dir: str) -> str:
    candidates = ["_status/state.json", "manifest.json"]
    for candidate in candidates:
        remote_path = f"{remote_log_dir.rstrip('/')}/{candidate}"
        cmd = [*base_cmd, f"if [ -f {shlex.quote(remote_path)} ]; then cat {shlex.quote(remote_path)}; fi"]
        proc = _run_ssh_capture(cmd)
        if proc.returncode == 0 and proc.stdout:
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                continue
            for key in ("run_id", "runId", "id"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return "latest"


def _fetch_logs(
    base_cmd: Sequence[str],
    remote_log_dir: str,
    local_dir: Path,
    use_rsync: bool,
    verbose: bool,
) -> Tuple[int, int, int, float]:
    targets = ["events.ndjson", "asr_job.log"]
    fetched = 0
    bytes_total = 0
    start = time.time()
    if use_rsync and _rsync_available():
        cmd = _build_rsync_cmd(remote_log_dir, local_dir, base_cmd, targets)
        if verbose:
            print("[debug] log rsync cmd:", " ".join(cmd))
        rc, count, total_bytes, duration = _run_rsync(cmd)
        if rc != 0:
            return 2, fetched, bytes_total, duration
        return 0, count, total_bytes, duration
    for name in targets:
        remote_path = f"{remote_log_dir.rstrip('/')}/{name}"
        dest = local_dir / name
        _ensure_dir(dest.parent)
        if dest.exists():
            _backup_existing(dest)
        cmd = ["scp"] + list(base_cmd[1:-1])
        cmd.extend([f"{base_cmd[-1]}:{remote_path}", str(dest)])
        if verbose:
            print("[debug] log scp cmd:", " ".join(cmd))
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            return 2, fetched, bytes_total, time.time() - start
        fetched += 1
        try:
            bytes_total += dest.stat().st_size
        except FileNotFoundError:
            pass
    return 0, fetched, bytes_total, time.time() - start


def _human_size(num: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="拉取远端 ASR 结果与日志（Windows Python 版）")
    parser.add_argument("--since", help="仅拉取指定 ISO 时间之后的文件")
    parser.add_argument("--only", choices=["JSON", "LOG", "ALL"], default="ALL", help="限制同步类型")
    parser.add_argument("--verbose", action="store_true", help="打印调试信息")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    file_env = _load_env_file()
    host = _resolve_value("VPS_HOST", env, file_env)
    user = _resolve_value("VPS_USER", env, file_env)
    key = _resolve_value("VPS_SSH_KEY", env, file_env)
    if key:
        key = str(Path(key).expanduser())
    remote_dir = _resolve_value("VPS_REMOTE_DIR", env, file_env)
    remote_json = _resolve_value("REMOTE_ASR_JSON", env, file_env)
    if remote_json is None and remote_dir:
        remote_json = f"{remote_dir.rstrip('/')}/data/asr-json"
    remote_logs = _resolve_value("REMOTE_LOG_DIR", env, file_env)
    if remote_logs is None and remote_dir:
        remote_logs = f"{remote_dir.rstrip('/')}/out"
    use_rsync = _resolve_value("USE_RSYNC_FIRST", env, file_env, "true").strip().lower() in {"1", "true", "yes"}

    if not host or not user:
        print("[FAIL] 未配置 VPS_HOST/VPS_USER。", file=sys.stderr)
        return 2
    if remote_json is None:
        print("[FAIL] 未配置 REMOTE_ASR_JSON。", file=sys.stderr)
        return 2
    only = args.only
    if only in {"LOG", "ALL"} and not remote_logs:
        print("[WARN] 未配置 REMOTE_LOG_DIR，跳过日志。")
        only = "JSON"

    base_cmd = _ssh_base(user, host, key)
    start_time = time.time()
    json_files: List[RemoteFile] = []
    warn_find = False
    if only in {"JSON", "ALL"}:
        json_files, warn_find = _list_remote_json_files(base_cmd, remote_json, args.since)
        if warn_find and args.since:
            print("[WARN] 远端 find -newermt 不可用，已回退为全量同步。")
        if args.since and not json_files and not warn_find:
            print("[INFO] 无符合时间条件的新 JSON 文件。")
    _ensure_dir(LOCAL_JSON_ROOT)

    run_id = "latest"
    if remote_logs:
        run_id = _detect_run_id(base_cmd, remote_logs)
    local_log_dir = LOCAL_LOG_ROOT / run_id
    _ensure_dir(local_log_dir)

    total_transferred = 0
    total_bytes = 0
    rc_json = 0
    if only in {"JSON", "ALL"}:
        if use_rsync and _rsync_available():
            includes = [item.path for item in json_files] if (args.since and json_files) else None
            if args.since and not json_files and not warn_find:
                print("[INFO] 无符合时间条件的新 JSON 文件。")
                includes = []
            if args.since and warn_find:
                includes = None
            if includes == []:
                pass
            else:
                cmd = _build_rsync_cmd(remote_json, LOCAL_JSON_ROOT, base_cmd, includes)
                if args.verbose:
                    print("[debug] rsync cmd:", " ".join(cmd))
                rc_json, count_json, bytes_json, duration = _run_rsync(cmd)
                if rc_json != 0:
                    print(f"[FAIL] rsync 拉取 JSON 失败 (rc={rc_json})")
                    return 2
                total_transferred += count_json
                total_bytes += bytes_json
                if count_json == 0:
                    print("[INFO] 未拉取新的 JSON 文件。")
        else:
            if not json_files and args.since and not warn_find:
                print("[INFO] 无新的 JSON 文件可通过 scp 下载。")
            else:
                rc_json, count_json, bytes_json, _ = _scp_fetch(json_files, base_cmd, remote_json, LOCAL_JSON_ROOT, args.verbose)
                if rc_json != 0:
                    print("[FAIL] scp 拉取 JSON 失败。")
                    return 2
                total_transferred += count_json
                total_bytes += bytes_json

    rc_logs = 0
    if only in {"LOG", "ALL"} and remote_logs:
        rc_logs, count_logs, bytes_logs, _ = _fetch_logs(base_cmd, remote_logs, local_log_dir, use_rsync, args.verbose)
        if rc_logs != 0:
            print("[FAIL] 拉取日志失败。")
            return 2
        total_transferred += count_logs
        total_bytes += bytes_logs

    if total_transferred == 0:
        print("[INFO] 未发现需要同步的文件。")
        return 1

    elapsed = time.time() - start_time
    print(f"[OK] 已同步 {total_transferred} 个文件，共 {_human_size(total_bytes)}，耗时 {elapsed:.1f}s。")
    print(f"日志已保存至：{local_log_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
# ==== END: OnePass Patch · R3 (sync_fetch_win) ====
