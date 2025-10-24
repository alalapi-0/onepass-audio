"""onepass.textnorm
用途: 规范化字幕文本并提供分句能力。
依赖: Python 标准库 dataclasses、re。
示例: ``from onepass.textnorm import norm_text, split_sentences, prepare_for_similarity``。
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

_ASCII_TO_CJK = {
    ",": "，",
    "?": "？",
    "!": "！",
    ";": "；",
    ":": "：",
    ".": "。",
    "~": "～",
    "\u2014": "—",  # em dash
    "-": "—",
    '"': "”",
    "'": "’",
}

_ELLIPSIS_RE = re.compile(r"\.{3,}")
_SPACE_RE = re.compile(r"\s+")
_MULTI_ELLIPSIS_RE = re.compile(r"…{2,}")
_NON_TEXT_RE = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff]+")

_TERMINATORS = set("。.!?！？；;…\n")


@dataclass(slots=True)
class SentencePiece:
    """原句及其规范化形式。"""

    norm: str
    raw: str


def _translate_ascii_punct(text: str) -> str:
    for src, dst in _ASCII_TO_CJK.items():
        text = text.replace(src, dst)
        if src.isalpha():
            text = text.replace(src.upper(), dst)
    return text


def _collapse_spaces(text: str) -> str:
    return _SPACE_RE.sub(" ", text.strip())


def norm_text(s: str) -> str:
    """对输入文本进行常见标点与空白规范化。"""

    if not s:
        return ""
    text = s.replace("\u3000", " ")  # 全角空格转半角
    text = text.replace("--", "—")
    text = _ELLIPSIS_RE.sub("…", text)
    text = text.replace("……", "…")
    text = _MULTI_ELLIPSIS_RE.sub("…", text)
    text = _translate_ascii_punct(text)
    text = text.replace("“", "“").replace("”", "”")
    text = text.replace("‘", "‘").replace("’", "’")
    text = _collapse_spaces(text)
    return text


def _iter_sentences(text: str) -> Iterable[str]:
    buffer: list[str] = []
    for ch in text:
        buffer.append(ch)
        if ch in _TERMINATORS:
            sentence = "".join(buffer)
            buffer.clear()
            if ch == "\n":
                sentence = sentence[:-1]
            yield sentence
    tail = "".join(buffer)
    if tail:
        yield tail


def split_sentences(original_text: str, cfg: dict) -> list[SentencePiece]:
    """以终止符拆分原文并返回规范化结果。"""

    text = original_text.replace("\r\n", "\n")
    min_len = int(cfg.get("sentence_min_chars", 6))
    pieces: list[SentencePiece] = []
    for raw_sentence in _iter_sentences(text):
        raw = raw_sentence.strip()
        if not raw:
            continue
        norm = norm_text(raw)
        if len(norm) < min_len:
            continue
        pieces.append(SentencePiece(norm=norm, raw=raw))
    return pieces


def prepare_for_similarity(text: str, cfg: dict) -> str:
    """根据配置生成用于相似度比较的文本。"""

    processed = norm_text(text)
    if cfg.get("punct_insensitive", False):
        processed = _NON_TEXT_RE.sub("", processed)
    if cfg.get("case_insensitive", False):
        processed = processed.lower()
    return processed
