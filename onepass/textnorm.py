"""onepass.textnorm
用途: 规范化字幕文本并提供分句能力。
依赖: Python 标准库 re。
示例: ``from onepass.textnorm import norm_text, split_sentences``。
"""
from __future__ import annotations

import re

_PUNCT_MAP = {
    ",": "，",
    "?": "？",
    "!": "！",
    ";": "；",
    ":": "：",
    ".": "。",
    "~": "～",
}


def norm_text(s: str) -> str:
    """对输入文本进行常见标点与空白规范化。"""

    text = re.sub(r"\.{3,}", "…", s)
    text = re.sub(r"\s+", " ", text.strip())
    for src, dst in _PUNCT_MAP.items():
        text = text.replace(src, dst)
        text = text.replace(src.upper(), dst)
    text = text.replace("……", "…")
    text = re.sub(r"…{2,}", "…", text)
    return text


_TERMINATORS = "。！？!?；;…"


def split_sentences(original_text: str) -> list[str]:
    """按终止符与换行将文本拆分为句子。"""

    text = original_text.replace("\r\n", "\n")
    sentences: list[str] = []
    buffer: list[str] = []
    for ch in text:
        buffer.append(ch)
        if ch in _TERMINATORS or ch == "\n":
            sentence = "".join(buffer).strip()
            buffer.clear()
            if sentence.endswith("\n"):
                sentence = sentence[:-1].rstrip()
            if len(sentence) >= 6:
                sentences.append(sentence)
    tail = "".join(buffer).strip()
    if len(tail) >= 6:
        sentences.append(tail)
    return sentences
