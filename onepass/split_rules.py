from __future__ import annotations

import re
from typing import List

# 硬标点（不可跨越）：句号/问号/感叹号/全角变体/英文分号等，右侧可跟右引号/右括号
HARD_PUNCT_RE = re.compile(r'[。！？!?．.;；]+[」』”’）)]*')
# 软标点：逗号/顿号/冒号/分号/破折号/省略号等
SOFT_PUNCT_RE = re.compile(r'[，、,:：；;……—]')

RIGHT_CLOSERS = '」』”’）)]'


def _hard_cut(text: str) -> List[str]:
    """按硬标点先行断句，断点归右（标点落在本句末尾）。"""

    parts: List[str] = []
    last = 0
    for m in HARD_PUNCT_RE.finditer(text):
        end = m.end()
        seg = text[last:end].strip()
        if seg:
            parts.append(seg)
        last = end
    tail = text[last:].strip()
    if tail:
        parts.append(tail)
    return parts


def _soft_recut(seg: str, min_len: int, max_len: int) -> List[str]:
    """在单句内部，基于软标点与长度做二次切分；若无软点则等长切块。"""

    seg = seg.strip()
    if not seg:
        return []
    if len(seg) <= max_len:
        return [seg]

    chunks: List[str] = []
    start = 0
    last_soft = None
    for i, ch in enumerate(seg):
        if SOFT_PUNCT_RE.match(ch):
            last_soft = i
        if (i - start + 1) > max_len:
            cut = last_soft if last_soft is not None and last_soft >= start + min_len - 1 else i
            cut += 1  # 包含软标点本身
            chunks.append(seg[start:cut].strip())
            start = cut
            last_soft = None
    tail = seg[start:].strip()
    if tail:
        if chunks and len(tail) < min_len and len(chunks[-1]) < min_len:
            chunks[-1] = (chunks[-1] + tail).strip()
        else:
            chunks.append(tail)

    fixed: List[str] = []
    for c in chunks:
        if len(c) <= max_len:
            fixed.append(c)
        else:
            s = 0
            while s < len(c):
                fixed.append(c[s : s + max_len])
                s += max_len
    return fixed


def _quote_closer_guard(lines: List[str]) -> List[str]:
    """收尾安全网：多余的右引号/右括号尽量并回前句，让停顿自然。"""

    if not lines:
        return lines
    out: List[str] = []
    buf = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if buf:
            line = buf + line
            buf = ""
        out.append(line)
    merged: List[str] = []
    i = 0
    while i < len(out):
        cur = out[i]
        if i + 1 < len(out):
            nxt = out[i + 1]
            if nxt and all(ch in RIGHT_CLOSERS for ch in nxt):
                merged.append((cur + nxt).strip())
                i += 2
                continue
        merged.append(cur)
        i += 1
    return merged


def split_zh(
    text: str,
    min_len: int = 8,
    max_len: int = 24,
    attach: str = "right",
    *,
    soft_enabled: bool = True,
) -> List[str]:
    """
    主入口：硬标点先断，句内软标点+长度二次切分，最后做引号/括号安全网。
    attach: 目前仅支持 'right'（硬标点归右）；保留参数便于未来扩展。
    soft_enabled: False 时跳过软标点重切，仅做硬切。
    """

    text = (text or "").strip()
    if not text:
        return []
    hard_parts = _hard_cut(text)
    lines: List[str] = []
    for seg in hard_parts:
        if soft_enabled:
            lines.extend(_soft_recut(seg, min_len=min_len, max_len=max_len))
        else:
            cleaned = seg.strip()
            if cleaned:
                lines.append(cleaned)
    lines = _quote_closer_guard(lines)
    if attach != "right":
        # 若未来实现 attach=left，可在此调整标点归属
        pass
    return [ln.strip() for ln in lines if ln.strip()]
