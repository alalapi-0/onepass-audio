"""统一的 EDL JSON 写出工具。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, MutableMapping, Sequence


@dataclass(slots=True)
class EDLWriteResult:
    """封装写出 EDL 后的关键元数据。"""

    edl_path: Path
    source_audio: str | None
    source_audio_abs: str | None
    path_style: str


def _ensure_segment_payload(segment: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "start": float(segment.get("start", 0.0) or 0.0),
        "end": float(segment.get("end", segment.get("stop", segment.get("t1", 0.0)) or 0.0) or 0.0),
        "action": segment.get("action", "keep") or "keep",
    }
    if "text" in segment:
        payload["text"] = str(segment.get("text") or "")
    if "conf" in segment:
        payload["conf"] = float(segment.get("conf") or 0.0)
    if "metadata" in segment:
        payload["metadata"] = segment["metadata"]
    return payload


def _normalise_source_audio(
    source_audio: str | None,
    *,
    audio_root: str | None,
    prefer_relative_audio: bool,
    path_style: str,
) -> tuple[str | None, str | None, str]:
    """根据配置规范化 source_audio 字段并确定写出风格。"""

    if not source_audio:
        return None, None, "posix"

    candidate = Path(source_audio).expanduser()
    try:
        abs_path = candidate.resolve(strict=False)
    except OSError:
        abs_path = candidate

    audio_root_path: Path | None = None
    if audio_root:
        root_candidate = Path(audio_root).expanduser()
        try:
            audio_root_path = root_candidate.resolve(strict=False)
        except OSError:
            audio_root_path = root_candidate

    style_option = (path_style or "auto").lower()
    if style_option not in {"auto", "posix", "windows"}:
        style_option = "auto"

    posix_value: str
    use_relative = False
    if prefer_relative_audio and audio_root_path is not None:
        try:
            rel = abs_path.relative_to(audio_root_path)
        except ValueError:
            use_relative = False
        else:
            use_relative = True
            posix_value = PurePosixPath(rel).as_posix()
    if not use_relative:
        posix_value = abs_path.as_posix()

    style_value = "windows" if style_option == "windows" else "posix"
    return posix_value, str(abs_path), style_value


def write_edl(
    edl_path: Path,
    *,
    source_audio: str | None,
    segments: Sequence[Mapping[str, object]] | None,
    schema_version: int = 1,
    sample_rate: int | None = None,
    channels: int | None = None,
    source_samplerate: int | None = None,
    stats: Mapping[str, object] | None = None,
    ensure_non_empty: bool = True,
    fallback_note: str | None = None,
    stem: str | None = None,
    version: int = 1,
    audio_root: str | None = None,
    prefer_relative_audio: bool = True,
    path_style: str = "auto",
) -> EDLWriteResult:
    """写出符合规范的 EDL JSON，并保证 segments 非空。"""

    edl_path = edl_path.expanduser().resolve()
    edl_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_segments: list[dict[str, object]] = []
    for segment in segments or []:
        payload = _ensure_segment_payload(segment)
        if payload["end"] < payload["start"]:
            payload["end"] = payload["start"]
        normalized_segments.append(payload)

    stats_payload: MutableMapping[str, object] = dict(stats or {})
    if ensure_non_empty and not normalized_segments:
        normalized_segments.append(
            {
                "start": 0.0,
                "end": 0.0,
                "action": "keep",
                "text": fallback_note or "FALLBACK",  # 标记占位段
                "conf": 0.0,
            }
        )
        stats_payload.setdefault("segments_fallback", True)

    written_source, abs_path, style_value = _normalise_source_audio(
        source_audio,
        audio_root=audio_root,
        prefer_relative_audio=prefer_relative_audio,
        path_style=path_style,
    )

    payload: MutableMapping[str, object] = {
        "schema_version": schema_version,
        "source_audio": written_source,
        "source_audio_basename": Path(abs_path).name if abs_path else None,
        "path_style": style_value,
        "segments": normalized_segments,
        "version": version,
        "stem": stem,
        "stats": stats_payload,
    }
    if sample_rate is not None:
        payload["sample_rate"] = int(sample_rate)
    if channels is not None:
        payload["channels"] = int(channels)
    if source_samplerate is not None:
        payload["source_samplerate"] = int(source_samplerate)

    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    edl_path.write_text(content, encoding="utf-8")
    return EDLWriteResult(
        edl_path=edl_path,
        source_audio=written_source,
        source_audio_abs=abs_path,
        path_style=style_value,
    )


__all__ = ["EDLWriteResult", "write_edl"]
