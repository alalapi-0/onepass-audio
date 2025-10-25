# ==== BEGIN: OnePass Patch · R3 (sync_upload_win) ====
"""Incrementally upload audio files to the VPS on Windows systems."""
from __future__ import annotations

import argparse
import fnmatch
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

DEFAULT_AUDIO_DIRS = [
    PROJ_ROOT / "onepass" / "data" / "audio",
    PROJ_ROOT / "data" / "audio",
]


@dataclass
class FileEntry:
    path: Path
    relative: Path
    size: int


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
    cli_value: str | None,
    env_vars: Dict[str, str],
    file_vars: Dict[str, str],
    default: str | None = None,
) -> str | None:
    if cli_value:
        return cli_value
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    if key in env_vars and env_vars[key]:
        return env_vars[key]
    if key in file_vars and file_vars[key]:
        return file_vars[key]
    return default


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_patterns(patterns: str | None) -> List[str]:
    if not patterns:
        return []
    result = []
    for item in patterns.split(","):
        item = item.strip()
        if item:
            result.append(item)
    return result


def _default_local_audio() -> Path:
    for candidate in DEFAULT_AUDIO_DIRS:
        if candidate.exists():
            return candidate
    return DEFAULT_AUDIO_DIRS[0]


def _collect_files(root: Path, patterns: Sequence[str]) -> List[FileEntry]:
    entries: List[FileEntry] = []
    for dirpath, _, filenames in os.walk(root):
        dir_path = Path(dirpath)
        for filename in filenames:
            full_path = dir_path / filename
            rel = full_path.relative_to(root)
            rel_posix = rel.as_posix()
            if patterns and not any(
                fnmatch.fnmatch(filename, pat) or fnmatch.fnmatch(rel_posix, pat)
                for pat in patterns
            ):
                continue
            try:
                size = full_path.stat().st_size
            except FileNotFoundError:
                continue
            entries.append(FileEntry(full_path, rel, size))
    entries.sort(key=lambda e: e.relative.as_posix())
    return entries


def _human_size(num_bytes: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _ssh_base(user: str, host: str, key: str | None) -> List[str]:
    parts = ["ssh", "-o", "BatchMode=yes"]
    if key:
        parts.extend(["-i", key])
    parts.append(f"{user}@{host}")
    return parts


def _rsync_available() -> bool:
    exe = shutil.which("rsync")
    return exe is not None


def _run_streamed(cmd: Sequence[str]) -> Tuple[int, List[str]]:
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    lines: List[str] = []
    for line in proc.stdout:
        print(line, end="")
        lines.append(line)
    return proc.wait(), lines


def _build_rsync_command(
    local_dir: Path,
    remote_audio: str,
    user: str,
    host: str,
    key: str | None,
    patterns: Sequence[str],
    no_delete: bool,
    dry_run: bool,
    bwlimit_mbps: float,
    checksum: bool,
) -> List[str]:
    cmd: List[str] = [
        "rsync",
        "-av",
        "--info=stats2,progress2",
        "--partial",
        "--inplace",
        "--stats",
        "--chmod=Du=rwx,Dg=rx,Do=rx,Fu=rw,Fg=r,Fo=r",
    ]
    if not no_delete:
        cmd.append("--delete-after")
    if dry_run:
        cmd.append("--dry-run")
    if checksum:
        cmd.append("--checksum")
    if bwlimit_mbps > 0:
        bwlimit = max(1, int(bwlimit_mbps * 125000))
        cmd.append(f"--bwlimit={bwlimit}")
    if patterns:
        cmd.extend(["--include", "*/"])
        for pattern in patterns:
            cmd.extend(["--include", pattern])
        cmd.extend(["--exclude", "*"])
    ssh_parts = ["ssh", "-o", "BatchMode=yes"]
    if key:
        ssh_parts.extend(["-i", key])
    ssh = " ".join(shlex.quote(part) for part in ssh_parts)
    cmd.extend(["-e", ssh])
    src = str(local_dir) + ("/" if not str(local_dir).endswith(("/", "\\")) else "")
    cmd.append(src)
    cmd.append(f"{user}@{host}:{remote_audio.rstrip('/')}/")
    return cmd


def _run_rsync(cmd: Sequence[str]) -> Tuple[int, int, int, float]:
    start = time.time()
    rc, lines = _run_streamed(cmd)
    duration = max(0.001, time.time() - start)
    transferred = 0
    total_size = 0
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
                    total_size = int(float(token))
                except ValueError:
                    continue
    return rc, transferred, total_size, duration


def _ensure_remote_dirs(user: str, host: str, key: str | None, remote_dirs: Iterable[str]) -> bool:
    base = _ssh_base(user, host, key)
    for directory in remote_dirs:
        remote_cmd = [*base, f"mkdir -p {shlex.quote(directory)}"]
        result = subprocess.run(remote_cmd, check=False)
        if result.returncode != 0:
            return False
    return True


def _scp_transfer(
    entries: Sequence[FileEntry],
    local_dir: Path,
    remote_audio: str,
    user: str,
    host: str,
    key: str | None,
    dry_run: bool,
) -> Tuple[int, int, float]:
    if not entries:
        return 1, 0, 0.0
    start = time.time()
    transferred = 0
    total_bytes = 0
    base_cmd = ["scp", "-C", "-p"]
    if key:
        base_cmd.extend(["-i", key])
    if dry_run:
        print("[dry-run] 以下文件将会上传：")
        for entry in entries:
            print(f"  {entry.relative.as_posix()} ({_human_size(entry.size)})")
        return 0, 0, 0.0
    for entry in entries:
        remote_path = f"{remote_audio.rstrip('/')}/{entry.relative.as_posix()}"
        remote_dir = os.path.dirname(remote_path)
        if remote_dir:
            ok = _ensure_remote_dirs(user, host, key, [remote_dir])
            if not ok:
                print(f"[FAIL] 无法创建远端目录：{remote_dir}")
                return 2, transferred, time.time() - start
        cmd = [*base_cmd, str(entry.path), f"{user}@{host}:{remote_path}"]
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(f"[FAIL] scp 失败：{entry.relative.as_posix()}")
            return 2, transferred, time.time() - start
        transferred += 1
        total_bytes += entry.size
    duration = max(0.001, time.time() - start)
    throughput = total_bytes / duration
    print(
        f"[OK] 已通过 scp 上传 {transferred} 个文件，共 {_human_size(total_bytes)}，平均 {throughput/1024/1024:.2f} MiB/s。"
    )
    return 0, transferred, duration


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="增量上传音频（Windows Python 版）")
    parser.add_argument("--host", help="远端主机名")
    parser.add_argument("--user", help="远端用户名")
    parser.add_argument("--key", help="SSH 私钥路径")
    parser.add_argument("--remote-dir", help="远端基础目录")
    parser.add_argument("--local-audio", help="本地音频目录")
    parser.add_argument("--remote-audio", help="远端音频目录")
    parser.add_argument("--pattern", help="音频匹配模式，逗号分隔")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不上传")
    parser.add_argument("--no-delete", action="store_true", help="不删除远端冗余文件")
    parser.add_argument("--bwlimit-mbps", type=float, default=None, help="带宽限制 (Mbps)")
    parser.add_argument("--checksum", action="store_true", help="强制使用 checksum 模式")
    parser.add_argument("--verbose", action="store_true", help="打印更多日志")
    args = parser.parse_args(argv)

    env_file = _load_env_file()
    host = _resolve_value("VPS_HOST", args.host, os.environ, env_file)
    user = _resolve_value("VPS_USER", args.user, os.environ, env_file)
    key = _resolve_value("VPS_SSH_KEY", args.key, os.environ, env_file)
    if key:
        key = str(Path(key).expanduser())
    remote_dir = _resolve_value("VPS_REMOTE_DIR", args.remote_dir, os.environ, env_file)
    remote_audio = _resolve_value("REMOTE_AUDIO", args.remote_audio, os.environ, env_file)
    if remote_audio is None and remote_dir:
        remote_audio = f"{remote_dir.rstrip('/')}/data/audio"
    local_audio = _resolve_value("LOCAL_AUDIO", args.local_audio, os.environ, env_file)
    patterns = _parse_patterns(_resolve_value("AUDIO_PATTERN", args.pattern, os.environ, env_file))
    bwlimit_value = args.bwlimit_mbps
    if bwlimit_value is None:
        bwlimit_raw = _resolve_value("BWLIMIT_Mbps", None, os.environ, env_file, "0")
        try:
            bwlimit_value = float(bwlimit_raw)
        except (TypeError, ValueError):
            bwlimit_value = 0.0
    use_rsync = _parse_bool(_resolve_value("USE_RSYNC_FIRST", None, os.environ, env_file, "true"), True)
    checksum = args.checksum or _parse_bool(_resolve_value("CHECKSUM", None, os.environ, env_file, "false"))

    if not host or not user:
        print("[FAIL] 未配置 VPS_HOST/VPS_USER。", file=sys.stderr)
        return 2
    if not remote_audio:
        print("[FAIL] 未配置远端音频目录。", file=sys.stderr)
        return 2

    if local_audio:
        local_dir = Path(local_audio).expanduser()
    else:
        local_dir = _default_local_audio()
    if not local_dir.exists():
        print(f"[FAIL] 本地目录不存在：{local_dir}", file=sys.stderr)
        return 2

    entries = _collect_files(local_dir, patterns)
    if not entries:
        print("[WARN] 无匹配文件，结束。")
        return 1

    total_bytes = sum(entry.size for entry in entries)
    print(f"[INFO] 准备同步 {len(entries)} 个文件，共 {_human_size(total_bytes)}。")
    if args.dry_run:
        for entry in entries:
            print(f"  {entry.relative.as_posix()} ({_human_size(entry.size)})")
        print("[INFO] dry-run 模式未执行上传。")
        return 0

    if use_rsync and _rsync_available():
        cmd = _build_rsync_command(
            local_dir=local_dir,
            remote_audio=remote_audio,
            user=user,
            host=host,
            key=key,
            patterns=patterns,
            no_delete=args.no_delete,
            dry_run=args.dry_run,
            bwlimit_mbps=bwlimit_value or 0.0,
            checksum=checksum,
        )
        if args.verbose:
            print("[debug] rsync cmd:", " ".join(cmd))
        rc, transferred, transferred_bytes, duration = _run_rsync(cmd)
        if rc != 0:
            print(f"[FAIL] rsync 失败，返回码 {rc}")
            return 2 if rc != 1 else 1
        if transferred == 0 or transferred_bytes == 0:
            print("[INFO] rsync 未传输新文件。")
            return 1
        throughput = transferred_bytes / duration
        print(
            f"[OK] rsync 完成：传输 {transferred} 个文件，共 {_human_size(transferred_bytes)}，平均 {throughput/1024/1024:.2f} MiB/s。"
        )
        return 0

    print("[WARN] rsync 不可用或被禁用，回退至 scp。")
    rc, transferred, _ = _scp_transfer(entries, local_dir, remote_audio, user, host, key, args.dry_run)
    if rc == 0 and transferred == 0:
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
# ==== END: OnePass Patch · R3 (sync_upload_win) ====
