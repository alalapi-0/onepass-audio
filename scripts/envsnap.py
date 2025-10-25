#!/usr/bin/env python3
"""Environment snapshot and profile helper for OnePass Audio.

This utility manages deploy profiles under ``deploy/profiles`` and keeps
per-run snapshots under ``out/_runs``.  Only the Python standard library is
used, ensuring the tool works in constrained environments (Windows PowerShell
and Linux shells alike).

Supported commands::

    python scripts/envsnap.py list
    python scripts/envsnap.py apply --profile NAME
    python scripts/envsnap.py snapshot [--note "..."]
    python scripts/envsnap.py diff --a PATH --b PATH
    python scripts/envsnap.py export-remote
    python scripts/envsnap.py show-active

"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

PROJ_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = PROJ_ROOT / "deploy" / "profiles"
ACTIVE_ENV = PROFILES_DIR / ".env.active"
RUNS_DIR = PROJ_ROOT / "out" / "_runs"
SYNC_ENV_FILE = PROJ_ROOT / "deploy" / "sync" / "sync.env"
REQUIRED_KEYS = [
    "ASR_MODEL",
    "ASR_LANGUAGE",
    "ASR_DEVICE",
    "ASR_COMPUTE",
    "ASR_WORKERS",
    "REMOTE_DIR",
    "REMOTE_AUDIO",
    "REMOTE_ASR_JSON",
    "REMOTE_LOG_DIR",
]
SENSITIVE_KEYWORDS = {"KEY", "SECRET", "TOKEN", "PASSWORD"}
PATH_KEYWORDS = {"PATH", "DIR"}


def _ensure_profiles_dir() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _parse_env_lines(lines: Iterable[str]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        env[key] = value
    return env


def _load_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"未找到环境文件：{path}")
    return _parse_env_lines(path.read_text(encoding="utf-8").splitlines())


def _load_optional_env(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        return _load_env_file(path)
    except FileNotFoundError:
        return {}


def _write_env_file(path: Path, data: Dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in data.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_snapshot_id() -> str:
    try:
        from onepass.snapshot import _make_snapshot_id as _snap_id

        return _snap_id()
    except Exception:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y%m%d-%H%M%S")


def _sanitise_value(key: str, value: str) -> str:
    upper = key.upper()
    if any(token in upper for token in SENSITIVE_KEYWORDS):
        return "<hidden>"
    if any(token in upper for token in PATH_KEYWORDS):
        home = str(Path.home())
        if value.startswith(home):
            return "~" + value[len(home) :]
    return value


def _print_env_table(env: Dict[str, str]) -> None:
    if not env:
        print("<无配置>")
        return
    width = max(len(key) for key in env)
    for key in sorted(env):
        value = _sanitise_value(key, env[key])
        print(f"{key.ljust(width)} : {value}")


def _diff_envs(a: Dict[str, str], b: Dict[str, str]) -> Tuple[List[str], List[str], List[Tuple[str, str, str]]]:
    added = sorted(key for key in b if key not in a)
    removed = sorted(key for key in a if key not in b)
    changed: List[Tuple[str, str, str]] = []
    for key in sorted(set(a) & set(b)):
        if a[key] != b[key]:
            changed.append((key, a[key], b[key]))
    return added, removed, changed


def _print_diff(a: Dict[str, str], b: Dict[str, str]) -> None:
    added, removed, changed = _diff_envs(a, b)
    if not (added or removed or changed):
        print("无差异。")
        return
    if added:
        print("新增变量：")
        for key in added:
            print(f"  + {key} = {b[key]}")
    if removed:
        print("删除变量：")
        for key in removed:
            print(f"  - {key} (原值 {a[key]})")
    if changed:
        print("修改变量：")
        for key, old, new in changed:
            print(f"  * {key}: {old} -> {new}")


def cmd_list(_: argparse.Namespace) -> int:
    _ensure_profiles_dir()
    print("=== 可用 Profiles ===")
    profiles = sorted(
        path for path in PROFILES_DIR.glob("*.env") if path.name != ".env.active"
    )
    if not profiles:
        print("暂无预置配置。")
    for path in profiles:
        env = _load_optional_env(path)
        run_mode = env.get("RUN_MODE", "-")
        notes = env.get("RUN_NOTES", "")
        print(f"- {path.stem}: RUN_MODE={run_mode} {notes}")
    print("\n=== 当前激活配置 ===")
    active = _load_optional_env(ACTIVE_ENV)
    if active:
        profile = active.get("ENV_PROFILE", "<未知>")
        print(f"ENV_PROFILE={profile}")
    else:
        print("尚未应用任何配置。")
    print("\n=== 最近快照 ===")
    if not RUNS_DIR.exists():
        print("暂无快照。")
        return 0
    entries = []
    for run_path in RUNS_DIR.glob("*"):
        snap = run_path / "env.snapshot.json"
        if snap.exists():
            entries.append((snap.stat().st_mtime, run_path.name, snap))
    entries.sort(reverse=True)
    for _, run_id, snap in entries[:10]:
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        created = data.get("created_at", "")
        note = data.get("note", "")
        profile = data.get("env", {}).get("ENV_PROFILE", "")
        print(f"- {run_id} · {profile} · {created} {note}")
    return 0


def _validate_profile(name: str, env: Dict[str, str]) -> None:
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    if missing:
        raise ValueError(f"Profile {name} 缺少必要字段：{', '.join(missing)}")
    if not env.get("AUDIO_PATTERN") and not env.get("STEMS"):
        raise ValueError("需提供 AUDIO_PATTERN 或 STEMS 至少一项。")


def cmd_apply(args: argparse.Namespace) -> int:
    _ensure_profiles_dir()
    profile_path = PROFILES_DIR / f"{args.profile}.env"
    if not profile_path.exists():
        print(f"未找到 profile：{profile_path}", file=sys.stderr)
        return 2
    try:
        profile_env = _load_env_file(profile_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        _validate_profile(args.profile, profile_env)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    active_env = _load_optional_env(ACTIVE_ENV)
    new_env = dict(profile_env)
    new_env["ENV_PROFILE"] = args.profile
    new_env["APPLIED_AT"] = datetime.now(timezone.utc).isoformat()
    _write_env_file(ACTIVE_ENV, new_env)
    print(f"已应用 profile: {args.profile}")
    _print_diff(active_env, new_env)
    return 0


def _git_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJ_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJ_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def cmd_snapshot(args: argparse.Namespace) -> int:
    if not ACTIVE_ENV.exists():
        print("尚未应用任何配置，请先运行 apply。", file=sys.stderr)
        return 2
    env = _load_env_file(ACTIVE_ENV)
    run_id = _make_snapshot_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = run_dir / "env.snapshot.json"
    data = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": args.note or "",
        "git": {
            "head": _git_head(),
            "dirty": _git_dirty(),
        },
        "env": env,
        "run_mode": env.get("RUN_MODE", ""),
        "run_notes": env.get("RUN_NOTES", ""),
    }
    snapshot_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"已创建快照：{snapshot_path.relative_to(PROJ_ROOT)}")
    print(f"RUN_ID={run_id}")
    print(f"SNAPSHOT_PATH={snapshot_path}")
    return 0


def _load_generic(path: Path) -> Dict[str, str]:
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"无法解析 JSON：{path}: {exc}") from exc
        if isinstance(data, dict):
            if "env" in data and isinstance(data["env"], dict):
                return {str(k): str(v) for k, v in data["env"].items()}
            return {str(k): str(v) for k, v in data.items() if isinstance(k, str)}
        raise ValueError(f"JSON 文件 {path} 不是对象。")
    return _load_env_file(path)


def cmd_diff(args: argparse.Namespace) -> int:
    path_a = Path(args.a)
    path_b = Path(args.b)
    try:
        env_a = _load_generic(path_a)
        env_b = _load_generic(path_b)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"比较 {path_a} ↔ {path_b}")
    _print_diff(env_a, env_b)
    return 0


def _run_subprocess(cmd: List[str]) -> int:
    try:
        return subprocess.call(cmd)
    except OSError as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        return 2


def cmd_export_remote(_: argparse.Namespace) -> int:
    if not ACTIVE_ENV.exists():
        print("尚未应用任何配置，请先运行 apply。", file=sys.stderr)
        return 2
    env = _load_optional_env(SYNC_ENV_FILE)
    if not env:
        print("缺少 deploy/sync/sync.env，请先运行 write-sync-env。", file=sys.stderr)
        return 2
    required = ["VPS_HOST", "VPS_USER", "VPS_SSH_KEY"]
    missing = [key for key in required if not env.get(key)]
    if missing:
        print(f"sync.env 缺少字段：{', '.join(missing)}", file=sys.stderr)
        return 2
    remote_dir = _load_env_file(ACTIVE_ENV).get("REMOTE_DIR") or env.get("VPS_REMOTE_DIR")
    if not remote_dir:
        print("配置缺少 REMOTE_DIR。", file=sys.stderr)
        return 2
    remote_profiles = f"{remote_dir.rstrip('/')}/deploy/profiles"
    host = env["VPS_HOST"].strip()
    user = env["VPS_USER"].strip() or "ubuntu"
    key_path = Path(env["VPS_SSH_KEY"]).expanduser()
    if not key_path.exists():
        print(f"SSH 私钥不存在：{key_path}", file=sys.stderr)
        return 2
    mkdir_cmd = [
        "ssh",
        "-i",
        str(key_path),
        f"{user}@{host}",
        f"mkdir -p {shlex.quote(remote_profiles)}",
    ]
    if _run_subprocess(mkdir_cmd) != 0:
        print("远端目录创建失败。", file=sys.stderr)
        return 2
    scp_cmd = [
        "scp",
        "-i",
        str(key_path),
        str(ACTIVE_ENV),
        f"{user}@{host}:{remote_profiles}/.env.active",
    ]
    rc = _run_subprocess(scp_cmd)
    if rc == 0:
        print(f"已上传：{remote_profiles}/.env.active")
    return rc


def cmd_show_active(_: argparse.Namespace) -> int:
    if not ACTIVE_ENV.exists():
        print("尚未应用任何配置。")
        return 0
    env = _load_env_file(ACTIVE_ENV)
    print(f"ENV_PROFILE={env.get('ENV_PROFILE', '<未知>')}")
    _print_env_table(env)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="环境快照与配置管理工具")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    list_parser = sub.add_parser("list", help="列出 profiles 与快照")
    list_parser.set_defaults(func=cmd_list)

    apply_parser = sub.add_parser("apply", help="应用指定 profile")
    apply_parser.add_argument("--profile", required=True, help="Profile 名称")
    apply_parser.set_defaults(func=cmd_apply)

    snapshot_parser = sub.add_parser("snapshot", help="根据当前激活配置生成快照")
    snapshot_parser.add_argument("--note", help="快照备注", default="")
    snapshot_parser.set_defaults(func=cmd_snapshot)

    diff_parser = sub.add_parser("diff", help="比较两个环境文件或快照")
    diff_parser.add_argument("--a", required=True, help="文件 A")
    diff_parser.add_argument("--b", required=True, help="文件 B")
    diff_parser.set_defaults(func=cmd_diff)

    export_parser = sub.add_parser("export-remote", help="将 .env.active 上传到远端")
    export_parser.set_defaults(func=cmd_export_remote)

    show_parser = sub.add_parser("show-active", help="显示当前激活配置")
    show_parser.set_defaults(func=cmd_show_active)

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("用户取消。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
