"""统一的 EDL JSON 写出工具。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, MutableMapping, Sequence


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
) -> Path:
    """写出符合规范的 EDL JSON，并保证 segments 非空。"""

    edl_path = edl_path.expanduser().resolve()
    edl_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_segments: list[dict[str, object]] = []
    for segment in segments or []:
        payload = _ensure_segment_payload(segment)
        if payload["end"] < payload["start"]:
            payload["end"] = payload["start"]
        normalized_segments.append(payload)
    if ensure_non_empty and not normalized_segments:
        normalized_segments.append(
            {
                "start": 0.0,
                "end": 1e-6,
                "action": "keep",
                "text": fallback_note or "FALLBACK",
                "conf": 0.0,
            }
        )
    payload: MutableMapping[str, object] = {
        "schema_version": schema_version,
        "source_audio": source_audio,
        "source_audio_basename": Path(source_audio).name if source_audio else None,
        "segments": normalized_segments,
        "version": version,
        "stem": stem,
        "stats": dict(stats or {}),
    }
    if sample_rate is not None:
        payload["sample_rate"] = int(sample_rate)
    if channels is not None:
        payload["channels"] = int(channels)
    if source_samplerate is not None:
        payload["source_samplerate"] = int(source_samplerate)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    edl_path.write_text(content, encoding="utf-8")
    return edl_path


__all__ = ["write_edl"]
