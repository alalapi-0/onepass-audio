"""onepass.asr_loader
用途: 从 faster-whisper 导出的词级 JSON 读取词时间戳。
依赖: Python 标准库 json、pathlib；自定义 ``onepass.types.Word``。
示例: ``from onepass.asr_loader import load_words``。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import Word


def _iter_words(data: Any) -> list[Word]:
    words: list[Word] = []
    segments = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segments, list):
        return words
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_words = seg.get("words")
        if not isinstance(seg_words, list):
            continue
        for w in seg_words:
            if not isinstance(w, dict):
                continue
            text = (w.get("word") or "").strip()
            if not text:
                continue
            try:
                start = float(w.get("start"))
                end = float(w.get("end"))
            except (TypeError, ValueError):
                continue
            if end < start:
                start, end = end, start
            words.append(Word(text=text, start=start, end=end))
    words.sort(key=lambda x: (x.start, x.end))
    return words


def load_words(json_path: Path) -> list[Word]:
    """读取 faster-whisper 词级 JSON 并返回词列表。"""

    data = json.loads(json_path.read_text("utf-8"))
    words = _iter_words(data)
    return words
