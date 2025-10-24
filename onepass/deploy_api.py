"""onepass.deploy_api
用途：提供统一的部署 Provider 抽象层，屏蔽 builtin 与 legacy 两类部署方式的差异。
依赖：Python 标准库 dataclasses、pathlib、re、shutil、subprocess、typing；内部模块 ``onepass.ux``。
示例：
  from onepass.deploy_api import get_provider
  provider = get_provider()
  provider.upload_audio(Path('data/audio'))
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Protocol, Sequence, runtime_checkable

from onepass.ux import format_cmd as _format_cmd
from onepass.ux import log_err, log_info, run_streamed as _run_streamed

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
    if name not in {"builtin", "legacy"}:
        raise ValueError("provider 仅支持 builtin 或 legacy")
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


def get_provider(config: Dict[str, Any] | None = None) -> DeployProvider:
    data = config or load_provider_config()
    current = get_current_provider_name(data)
    if current == "legacy":
        log_info("使用 legacy provider")
        return _LegacyProvider(data)
    log_info("使用 builtin provider")
    return _BuiltinProvider(data)
