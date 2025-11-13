"""Robust utilities to extract token sequences from ASR JSON outputs."""
from __future__ import annotations

from typing import Any, List, TypedDict


class Token(TypedDict):
    text: str
    start: float
    end: float


def _coerce_word(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("text", "word", "char", "token"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _coerce_time(payload: Any, keys=("start", "ts", "begin"), default: float = 0.0) -> float:
    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                try:
                    return float(payload[key])
                except Exception:
                    continue
    return float(default)


def load_tokens(json_obj: Any) -> List[Token]:
    tokens: List[Token] = []

    def _push(entry: Any) -> None:
        text = _coerce_word(entry)
        if not text:
            return
        start = _coerce_time(entry, ("start", "ts", "begin"))
        end = _coerce_time(entry, ("end", "te", "finish"), start)
        tokens.append({"text": text, "start": start, "end": end})

    if isinstance(json_obj, dict):
        if isinstance(json_obj.get("words"), list):
            for item in json_obj["words"]:
                _push(item)
        elif isinstance(json_obj.get("segments"), list):
            for segment in json_obj["segments"]:
                if isinstance(segment, dict) and isinstance(segment.get("words"), list):
                    for item in segment["words"]:
                        _push(item)
        elif isinstance(json_obj.get("items"), list):
            for item in json_obj["items"]:
                _push(item)
    elif isinstance(json_obj, list):
        for item in json_obj:
            _push(item)
    return tokens


__all__ = ["Token", "load_tokens"]
