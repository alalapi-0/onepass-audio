"""onepass.deploy_api
用途：提供统一的部署 Provider 抽象层，屏蔽 builtin 与 legacy 两类部署方式的差异。
依赖：Python 标准库 dataclasses、pathlib、re、shutil、subprocess、typing；内部模块 ``onepass.ux``。
示例：
  from onepass.deploy_api import get_provider
  provider = get_provider()
  provider.upload_audio(Path('data/audio'))
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Protocol, Sequence, runtime_checkable

from onepass.ux import format_cmd as _format_cmd
from onepass.ux import log_err, log_info, log_ok, log_warn, run_streamed as _run_streamed

PROJ_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJ_ROOT / "deploy" / "provider.yaml"
CONFIG_LOCAL_PATH = PROJ_ROOT / "deploy" / "provider.local.yaml"


@runtime_checkable
class DeployProvider(Protocol):
    """统一的部署 Provider 接口。"""

    def provision(self, dry_run: bool = False) -> int: ...

    def upload_audio(self, local_audio_dir: Path, dry_run: bool = False) -> int: ...

    def run_asr(
        self,
        audio_pattern: str,
        model: str,
        language: str,
        device: str,
        compute: str,
        workers: int,
        dry_run: bool = False,
    ) -> int: ...

    def fetch_outputs(
        self,
        local_asr_json_dir: Path,
        since_iso: str | None = None,
        dry_run: bool = False,
    ) -> int: ...

    def status(self) -> int: ...


def format_cmd(cmd: Sequence[str]) -> str:
    """暴露 ``onepass.ux.format_cmd`` 供外部调用。"""

    return _format_cmd(list(cmd))


def run_streamed(cmd: Sequence[str], cwd: Path | None = None) -> int:
    """对 ``onepass.ux.run_streamed`` 的轻量封装。"""

    return _run_streamed(list(cmd), cwd=cwd, heartbeat_s=30.0, show_cmd=False)


def _simple_yaml_load(text: str) -> Dict[str, Any]:
    """极简 YAML 解析，仅支持嵌套映射与字符串/数字。"""

    result: Dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, result)]
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        content = line.split("#", 1)[0].rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        stripped = content.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if ":" not in stripped:
            raise ValueError(f"Line {idx}: missing ':' in provider.yaml")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value == "":
            container: Any = {}
            if stripped.endswith(":") and idx < len(lines):
                # 预判下一行缩进决定容器类型
                next_line = None
                for candidate in lines[idx:]:
                    stripped_candidate = candidate.split("#", 1)[0].rstrip()
                    if stripped_candidate.strip():
                        next_line = stripped_candidate
                        break
                if next_line and next_line.lstrip().startswith("-"):
                    container = []
            if isinstance(parent, dict):
                parent[key] = container
            else:
                raise ValueError(f"Line {idx}: cannot attach key to non-mapping parent")
            stack.append((indent, container))
            continue
        if raw_value.startswith("[") and raw_value.endswith("]"):
            value = [item.strip() for item in raw_value[1:-1].split(",") if item.strip()]
        elif raw_value.lower() in {"true", "false"}:
            value = raw_value.lower() == "true"
        else:
            try:
                value = int(raw_value)
            except ValueError:
                value = raw_value.strip('"')
        if isinstance(parent, dict):
            parent[key] = value
        else:
            raise ValueError(f"Line {idx}: cannot attach value to non-mapping parent")
    return result


def _load_yaml_file(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if data is None:
            return {}
        return data
    except ModuleNotFoundError:
        return _simple_yaml_load(text)


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _load_env_file(path: Path) -> Dict[str, str]:
    """加载简单 ``KEY=VALUE`` 环境文件。"""

    if not path.exists():
        raise FileNotFoundError(f"缺少环境文件：{path}")
    result: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        result[key] = value
    return result


def load_provider_config() -> Dict[str, Any]:
    """读取 provider.yaml，并合并 provider.local.yaml（若存在）。"""

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"缺少 {CONFIG_PATH}")
    config = _load_yaml_file(CONFIG_PATH)
    if CONFIG_LOCAL_PATH.exists():
        local_config = _load_yaml_file(CONFIG_LOCAL_PATH)
        config = _merge_dict(config, local_config)
    return config


def get_current_provider_name(config: Dict[str, Any] | None = None) -> str:
    data = config or load_provider_config()
    current = str(data.get("current", "builtin")).strip()
    return current or "builtin"


def set_current_provider(name: str) -> None:
    if name not in {"builtin", "legacy", "sshfs"}:
        raise ValueError("provider 仅支持 builtin、legacy 或 sshfs")
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"缺少 {CONFIG_PATH}")
    text = CONFIG_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"^(current:\s*)(\w+)(.*)$", re.MULTILINE)
    if not pattern.search(text):
        raise RuntimeError("provider.yaml 缺少 current 字段")
    new_text = pattern.sub(lambda m: f"{m.group(1)}{name}{m.group(3)}", text, count=1)
    CONFIG_PATH.write_text(new_text, encoding="utf-8")


@dataclass
class _CommonDefaults:
    audio_pattern: str
    model: str
    language: str
    device: str
    compute: str
    workers: int
    remote_dir: str


def _read_common_defaults(config: Dict[str, Any]) -> _CommonDefaults:
    common = config.get("common", {})
    return _CommonDefaults(
        audio_pattern=str(common.get("audio_pattern", "*.m4a,*.wav,*.mp3,*.flac")),
        model=str(common.get("model", "medium")),
        language=str(common.get("language", "zh")),
        device=str(common.get("device", "auto")),
        compute=str(common.get("compute", "auto")),
        workers=int(common.get("workers", 1)),
        remote_dir=str(common.get("remote_dir", "/home/ubuntu/onepass")),
    )


class _BuiltinProvider:
    """官方 ssh/scp 流程的封装。"""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._pwsh = shutil.which("pwsh")
        self._bash = shutil.which("bash")
        self._defaults = _read_common_defaults(config)
        if self._pwsh is None:
            raise FileNotFoundError("未找到 PowerShell 7 (pwsh)，无法使用 builtin provider。")
        if self._bash is None:
            raise FileNotFoundError("未找到 bash，无法使用 builtin provider。")
        self._deploy_dir = PROJ_ROOT / "deploy"

    def _remote_path(self, *parts: str) -> str:
        base = self._defaults.remote_dir.rstrip("/")
        for part in parts:
            cleaned = part.strip("/")
            if cleaned:
                base = f"{base}/{cleaned}"
        return base

    def _run_pwsh(self, script: Path, args: Iterable[str], dry_run: bool = False) -> int:
        cmd = [self._pwsh or "pwsh", "-File", str(script)] + list(args)
        if dry_run:
            cmd.append("-DryRun")
        log_info(f"执行：{format_cmd(cmd)}")
        return run_streamed(cmd)

    def _run_bash(self, script: Path, args: Iterable[str], dry_run: bool = False) -> int:
        cmd = [self._bash or "bash", str(script)] + list(args)
        if dry_run:
            cmd.append("--dry-run")
        log_info(f"执行：{format_cmd(cmd)}")
        return run_streamed(cmd)

    def provision(self, dry_run: bool = False) -> int:
        script = self._deploy_dir / "remote_provision.sh"
        if not script.exists():
            log_err(f"缺少脚本：{script}")
            return 2
        return self._run_bash(script, [], dry_run=dry_run)

    def upload_audio(self, local_audio_dir: Path, dry_run: bool = False) -> int:
        script = self._deploy_dir / "deploy_to_vps.ps1"
        if not script.exists():
            log_err(f"缺少脚本：{script}")
            return 2
        remote_audio = self._remote_path("data", "audio")
        args = ["-AudioDir", str(local_audio_dir), "-RemoteDir", remote_audio]
        return self._run_pwsh(script, args, dry_run=dry_run)

    def run_asr(
        self,
        audio_pattern: str,
        model: str,
        language: str,
        device: str,
        compute: str,
        workers: int,
        dry_run: bool = False,
    ) -> int:
        script = self._deploy_dir / "remote_asr_job.sh"
        if not script.exists():
            log_err(f"缺少脚本：{script}")
            return 2
        args = [
            "--pattern",
            audio_pattern,
            "--model",
            model,
            "--language",
            language,
            "--device",
            device,
            "--compute",
            compute,
            "--workers",
            str(workers),
        ]
        return self._run_bash(script, args, dry_run=dry_run)

    def fetch_outputs(
        self,
        local_asr_json_dir: Path,
        since_iso: str | None = None,
        dry_run: bool = False,
    ) -> int:
        script = self._deploy_dir / "fetch_outputs.ps1"
        if not script.exists():
            log_err(f"缺少脚本：{script}")
            return 2
        remote_json = self._remote_path("data", "asr-json")
        args = ["-RemoteDir", remote_json, "-LocalDir", str(local_asr_json_dir)]
        if since_iso:
            args.extend(["-Since", since_iso])
        return self._run_pwsh(script, args, dry_run=dry_run)

    def status(self) -> int:
        script = self._deploy_dir / "remote_asr_job.sh"
        if not script.exists():
            log_err(f"缺少脚本：{script}")
            return 2
        return self._run_bash(script, ["--status"], dry_run=False)


class _LegacyProvider:
    """调用 PowerShell 适配层。"""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._pwsh = shutil.which("pwsh")
        if self._pwsh is None:
            raise FileNotFoundError("未找到 PowerShell 7 (pwsh)，无法使用 legacy provider。")
        self._adapter = PROJ_ROOT / "deploy" / "providers" / "legacy" / "legacy_adapter.ps1"
        if not self._adapter.exists():
            raise FileNotFoundError(f"缺少适配脚本：{self._adapter}")
        self._defaults = _read_common_defaults(config)

    def _invoke(self, subcommand: str, extra: Iterable[str], dry_run: bool = False) -> int:
        cmd = [self._pwsh or "pwsh", "-File", str(self._adapter), subcommand]
        cmd.extend(list(extra))
        if dry_run:
            cmd.append("-DryRun")
        log_info(f"执行：{format_cmd(cmd)}")
        return run_streamed(cmd)

    def provision(self, dry_run: bool = False) -> int:
        return self._invoke("provision", [], dry_run=dry_run)

    def upload_audio(self, local_audio_dir: Path, dry_run: bool = False) -> int:
        extra = ["-LocalAudio", str(local_audio_dir)]
        return self._invoke("upload_audio", extra, dry_run=dry_run)

    def run_asr(
        self,
        audio_pattern: str,
        model: str,
        language: str,
        device: str,
        compute: str,
        workers: int,
        dry_run: bool = False,
    ) -> int:
        extra = [
            "-AudioPattern",
            audio_pattern,
            "-Model",
            model,
            "-Language",
            language,
            "-Device",
            device,
            "-Compute",
            compute,
            "-Workers",
            str(workers),
        ]
        return self._invoke("run_asr", extra, dry_run=dry_run)

    def fetch_outputs(
        self,
        local_asr_json_dir: Path,
        since_iso: str | None = None,
        dry_run: bool = False,
    ) -> int:
        extra = ["-LocalAsrJson", str(local_asr_json_dir)]
        if since_iso:
            extra.extend(["-SinceIso", since_iso])
        return self._invoke("fetch_outputs", extra, dry_run=dry_run)

    def status(self) -> int:
        return self._invoke("status", [], dry_run=False)


class _SshfsProvider:
    """通过反向 sshfs 挂载本地音频目录。"""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._defaults = _read_common_defaults(config)
        section = config.get("sshfs", {})
        env_path_str = str(section.get("env_file", "deploy/sshfs/sshfs.env"))
        self._env_file = PROJ_ROOT / env_path_str
        self._env_vars = _load_env_file(self._env_file)
        self._remote_env_path = env_path_str
        mount_point = str(section.get("mount_point", "")).strip() or self._env_vars.get(
            "VPS_MOUNT_POINT", ""
        )
        if not mount_point:
            raise ValueError("sshfs.mount_point 未配置，且环境变量中缺少 VPS_MOUNT_POINT。")
        self._mount_point = mount_point
        self._ssh = shutil.which("ssh")
        self._scp = shutil.which("scp")
        if self._ssh is None:
            raise FileNotFoundError("未找到 ssh 命令，无法使用 sshfs provider。")
        if self._scp is None:
            raise FileNotFoundError("未找到 scp 命令，无法使用 sshfs provider。")
        self._local_script = PROJ_ROOT / "deploy" / "sshfs" / "local_reverse_tunnel.ps1"
        self._remote_dir = self._defaults.remote_dir
        required = ["VPS_HOST", "VPS_USER", "VPS_SSH_KEY", "REVERSE_SSHD_PORT"]
        for key in required:
            if not self._env_vars.get(key):
                raise ValueError(f"环境变量 {key} 未设置，无法使用 sshfs provider。")
        self._pwsh = shutil.which("pwsh")

    def _ssh_target(self) -> str:
        return f"{self._env_vars['VPS_USER']}@{self._env_vars['VPS_HOST']}"

    def _ssh_base_cmd(self) -> list[str]:
        cmd = [self._ssh or "ssh"]
        key = self._env_vars.get("VPS_SSH_KEY")
        if key:
            cmd.extend(["-i", key])
        return cmd

    def _build_remote_cmd(self, remote_command: str) -> list[str]:
        inner = f"cd {shlex.quote(self._remote_dir)} && {remote_command}"
        return self._ssh_base_cmd() + [self._ssh_target(), f"bash -lc {shlex.quote(inner)}"]

    def _run_remote_script(self, script_name: str) -> int:
        script_path = f"deploy/sshfs/{script_name}"
        remote_command = (
            f"SSHFS_ENV_FILE={shlex.quote(self._remote_env_path)} bash {shlex.quote(script_path)}"
        )
        cmd = self._build_remote_cmd(remote_command)
        log_info(f"执行：{format_cmd(cmd)}")
        return run_streamed(cmd)

    def _maybe_start_local_tunnel(self) -> None:
        cmd = ["pwsh", "-File", str(self._local_script)]
        if os.name != "nt":
            log_info("请在本地 Windows PowerShell 中运行以下命令以启动反向隧道：")
            log_info(f"  {format_cmd(cmd)}")
            return
        if not self._local_script.exists():
            log_warn(f"缺少本地脚本：{self._local_script}")
            return
        if self._pwsh is None:
            log_warn("未找到 PowerShell 7 (pwsh)，请手动运行本地脚本。")
            log_info(f"  {format_cmd(cmd)}")
            return
        env = os.environ.copy()
        env.update(self._env_vars)
        launch_cmd = [self._pwsh or "pwsh", "-File", str(self._local_script)]
        log_info(f"启动本地反向隧道脚本：{format_cmd(launch_cmd)}")
        try:
            process = subprocess.Popen(launch_cmd, env=env)
        except OSError as exc:
            log_warn(f"无法启动本地脚本：{exc}")
            return
        time.sleep(2.0)
        if process.poll() is not None:
            log_warn(f"本地脚本已退出，返回码 {process.returncode}。请检查其输出。")
        else:
            log_ok(f"本地隧道脚本正在运行（PID={process.pid}）。")

    def provision(self, dry_run: bool = False) -> int:
        log_info("sshfs provider 将使用反向隧道和远端挂载直读本地音频。")
        local_cmd = ["pwsh", "-File", str(self._local_script)]
        if dry_run:
            log_info(f"[DryRun] 本地命令：{format_cmd(local_cmd + ['-DryRun'])}")
            cmd = self._build_remote_cmd(
                f"SSHFS_ENV_FILE={shlex.quote(self._remote_env_path)} bash {shlex.quote('deploy/sshfs/remote_mount_local.sh')}"
            )
            log_info(f"[DryRun] 远端命令：{format_cmd(cmd)}")
            return 0
        self._maybe_start_local_tunnel()
        return self._run_remote_script("remote_mount_local.sh")

    def upload_audio(self, local_audio_dir: Path, dry_run: bool = False) -> int:
        pattern_src = self._env_vars.get("AUDIO_PATTERN", self._defaults.audio_pattern)
        pattern_list = [
            p.strip()
            for p in pattern_src.split(",")
            if p.strip()
        ]
        py_code = textwrap.dedent(
            f"""
            from pathlib import Path
            import json

            mount = Path({json.dumps(self._mount_point)})
            patterns = {json.dumps(pattern_list)}
            files = set()
            for pat in patterns:
                files.update(mount.glob(pat))
            count = 0
            total = 0
            for item in files:
                if item.is_file():
                    stat = item.stat()
                    count += 1
                    total += stat.st_size
            print(json.dumps({{'count': count, 'bytes': total}}, ensure_ascii=False))
            """
        ).strip()
        remote_command = f"python - <<'PY'\n{py_code}\nPY"
        cmd = self._build_remote_cmd(remote_command)
        if dry_run:
            log_info("sshfs provider 不上传文件，将在远端验证挂载可读性。")
            log_info(f"[DryRun] 远端命令：{format_cmd(cmd)}")
            return 0
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log_err(result.stderr.strip() or "远端校验失败")
            return 2
        output = result.stdout.strip().splitlines()
        if not output:
            log_err("远端未返回任何结果。")
            return 2
        try:
            summary = json.loads(output[-1])
        except json.JSONDecodeError as exc:
            log_err(f"解析远端输出失败：{exc}")
            return 2
        count = int(summary.get("count", 0))
        size_bytes = int(summary.get("bytes", 0))
        size_mb = size_bytes / 1_000_000 if size_bytes else 0
        log_ok(
            f"挂载 {self._mount_point} 可读，检测到 {count} 个音频文件，总计约 {size_mb:.2f} MB。"
        )
        return 0

    def run_asr(
        self,
        audio_pattern: str,
        model: str,
        language: str,
        device: str,
        compute: str,
        workers: int,
        dry_run: bool = False,
    ) -> int:
        audio_pattern = self._env_vars.get("AUDIO_PATTERN", audio_pattern)
        model = self._env_vars.get("ASR_MODEL", model)
        language = self._env_vars.get("ASR_LANGUAGE", language)
        device = self._env_vars.get("ASR_DEVICE", device)
        compute = self._env_vars.get("ASR_COMPUTE", compute)
        workers_env = self._env_vars.get("ASR_WORKERS")
        if workers_env:
            try:
                workers = int(workers_env)
            except ValueError:
                log_warn(f"ASR_WORKERS={workers_env} 不是有效整数，保持 {workers}。")
        cmd_parts = [
            "python",
            "scripts/asr_batch.py",
            "--audio-dir",
            self._mount_point,
            "--pattern",
            audio_pattern,
            "--model",
            model,
            "--language",
            language,
            "--device",
            device,
            "--compute",
            compute,
            "--workers",
            str(workers),
        ]
        cmd_str = " ".join(shlex.quote(part) for part in cmd_parts)
        verify_cmd = "python scripts/verify_asr_words.py"
        remote_command = f"{cmd_str} && {verify_cmd}"
        cmd = self._build_remote_cmd(remote_command)
        if dry_run:
            log_info(f"[DryRun] ASR 命令：{format_cmd(cmd)}")
            return 0
        return run_streamed(cmd)

    def fetch_outputs(
        self,
        local_asr_json_dir: Path,
        since_iso: str | None = None,
        dry_run: bool = False,
    ) -> int:
        local_asr_json_dir.mkdir(parents=True, exist_ok=True)
        if since_iso:
            log_warn("sshfs provider 暂不支持 --since 过滤，将完整同步 asr-json。")
        remote_path = self._remote_path("data", "asr-json")
        scp_cmd = self._scp_base_cmd()
        scp_cmd.extend(["-r", f"{self._ssh_target()}:{remote_path}/", str(local_asr_json_dir)])
        if dry_run:
            log_info(f"[DryRun] SCP 命令：{format_cmd(scp_cmd)}")
            return 0
        log_info(f"执行：{format_cmd(scp_cmd)}")
        return run_streamed(scp_cmd)

    def status(self) -> int:
        tunnel = f"{self._env_vars['VPS_HOST']}:{self._env_vars['REVERSE_SSHD_PORT']}"
        status_script = textwrap.dedent(
            f"""
            tunnel={json.dumps(tunnel)}
            mount_point={json.dumps(self._mount_point)}
            echo "隧道反向端口：${{tunnel}}"
            if mountpoint -q "${mount_point}"; then
                echo "挂载状态：已挂载 (${mount_point})"
            else
                echo "挂载状态：未挂载 (${mount_point})"
            fi
            df -h "${mount_point}" || true
            echo "远端日志目录：{self._remote_dir}/out"
            if [ -d data/asr-json ]; then
                echo "最近的 JSON："
                ls -1 data/asr-json | tail -n 5
            fi
            """
        ).strip()
        cmd = self._build_remote_cmd(status_script)
        log_info(f"执行：{format_cmd(cmd)}")
        return run_streamed(cmd)

    def _remote_path(self, *parts: str) -> str:
        base = self._defaults.remote_dir.rstrip("/")
        for part in parts:
            cleaned = part.strip("/")
            if cleaned:
                base = f"{base}/{cleaned}"
        return base

    def _scp_base_cmd(self) -> list[str]:
        cmd = [self._scp or "scp"]
        key = self._env_vars.get("VPS_SSH_KEY")
        if key:
            cmd.extend(["-i", key])
        return cmd

def get_provider(config: Dict[str, Any] | None = None) -> DeployProvider:
    data = config or load_provider_config()
    current = get_current_provider_name(data)
    if current == "legacy":
        log_info("使用 legacy provider")
        return _LegacyProvider(data)
    if current == "sshfs":
        log_info("使用 sshfs provider")
        return _SshfsProvider(data)
    log_info("使用 builtin provider")
    return _BuiltinProvider(data)
