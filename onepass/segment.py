"""onepass.segment
用途: 将词级结果切分为字幕段落。
依赖: Python 标准库；内部类型 ``onepass.types.Word``、``Segment``。
示例: ``from onepass.segment import to_segments``。
"""
from __future__ import annotations

from typing import List

from .types import Segment, Word


def _join_words(words: List[Word]) -> str:
    pieces: list[str] = []
    for word in words:
        token = word.text
        if not pieces:
            pieces.append(token)
            continue
        prev = pieces[-1]
        if prev and prev[-1].isascii() and token and token[0].isascii():
            pieces.append(" " + token)
        else:
            pieces.append(token)
    return "".join(pieces)


def to_segments(words: list[Word], cfg: dict) -> list[Segment]:
    """根据配置将词序列划分为字幕段。"""

    if not words:
        return []
    gap_newline = float(cfg.get("gap_newline_s", 0.6))
    max_dur = float(cfg.get("max_seg_dur_s", 5.0))
    max_chars = int(cfg.get("max_seg_chars", 32))

    segments: list[Segment] = []
    current_words: list[Word] = []
    seg_start = words[0].start

    for idx, word in enumerate(words):
        if not current_words:
            current_words.append(word)
            seg_start = word.start
        else:
            potential_words = current_words + [word]
            potential_text = _join_words(potential_words)
            potential_duration = word.end - seg_start
            if potential_duration > max_dur or len(potential_text) > max_chars:
                segment_text = _join_words(current_words)
                segments.append(Segment(text=segment_text, start=seg_start, end=current_words[-1].end))
                current_words = [word]
                seg_start = word.start
            else:
                current_words.append(word)
        # 检查与下一词的间隙
        is_last_word = idx == len(words) - 1
        if not is_last_word:
            next_gap = words[idx + 1].start - word.end
            if next_gap > gap_newline:
                segment_text = _join_words(current_words)
                segments.append(Segment(text=segment_text, start=seg_start, end=current_words[-1].end))
                current_words = []
        else:
            segment_text = _join_words(current_words)
            segments.append(Segment(text=segment_text, start=seg_start, end=current_words[-1].end))
    return segments
