"""Chinese sentence segmentation utilities with configurable strategies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

__all__ = ["Segment", "segment"]


@dataclass(slots=True)
class Segment:
    """Represents a segmented sentence span."""

    text: str
    start: int
    end: int

    @property
    def length(self) -> int:
        return len(self.text)


_STRONG_CHARS = set("。！？!?；;…")
_WEAK_CHARS = set("，,、：:—-﹣–")
_ELLIPSIS_TOKENS = ("……", "...")
_WEAK_TOKENS = ("——",)
_FORCED_BREAK_CHARS = {" ", "\t", "\n", "\r", "\u3000", "，", ","}
_OPEN_TO_CLOSE = {
    "（": "）",
    "(": ")",
    "【": "】",
    "[": "]",
    "《": "》",
    "〈": "〉",
    "『": "』",
    "「": "」",
    "〔": "〕",
    "{": "}",
    "｛": "｝",
}
_SYMMETRIC_QUOTES = {"\"", "'", "“", "”", "‘", "’", "＂", "＇"}


def segment(
    text: str,
    *,
    split_mode: str = "punct+len",
    min_len: int = 8,
    max_len: int = 24,
    hard_max: int = 32,
    weak_punct_enable: bool = True,
    keep_quotes: bool = True,
) -> List[Segment]:
    """Split *text* into sentence segments based on punctuation and length limits."""

    normalized = _normalize_text(text)
    if not normalized:
        return []
    mode = (split_mode or "punct+len").strip().lower()
    if mode not in {"punct", "all-punct", "punct+len"}:
        raise ValueError("split_mode must be punct, all-punct, or punct+len")
    if min_len < 1:
        raise ValueError("min_len must be positive")
    if max_len < min_len:
        raise ValueError("max_len must be >= min_len")
    if hard_max < max_len:
        raise ValueError("hard_max must be >= max_len")

    if mode == "punct+len":
        base_segments = _split_by_punct(
            normalized,
            use_weak=False,
            keep_quotes=keep_quotes,
            weak_enabled=weak_punct_enable,
        )
        adjusted = _apply_length_rules(
            normalized,
            base_segments,
            min_len=min_len,
            max_len=max_len,
            hard_max=hard_max,
            weak_enabled=weak_punct_enable,
            keep_quotes=keep_quotes,
        )
        return adjusted
    use_weak = mode == "all-punct"
    base_segments = _split_by_punct(
        normalized,
        use_weak=use_weak,
        keep_quotes=keep_quotes,
        weak_enabled=weak_punct_enable,
    )
    merged = _merge_short_segments(base_segments, min_len=min_len)
    return merged


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized


def _split_by_punct(
    text: str,
    *,
    use_weak: bool,
    keep_quotes: bool,
    weak_enabled: bool,
) -> List[Segment]:
    segments: List[Segment] = []
    length = len(text)
    i = 0
    seg_start = 0
    stack: list[str] = []
    while i < length:
        token, token_len, token_type = _next_token(text, i)
        for offset in range(token_len):
            ch = text[i + offset]
            if keep_quotes:
                _update_stack(stack, ch)
        i += token_len
        inside = keep_quotes and bool(stack)
        should_break = False
        if token_type == "strong":
            should_break = True
        elif token == "\n" and i - seg_start > 0:
            if i < length and text[i : i + 1] == "\n":
                should_break = True
        elif use_weak and weak_enabled and token_type == "weak" and not inside:
            should_break = True
        if should_break:
            seg = _extract_segment(text, seg_start, i)
            if seg:
                segments.append(seg)
            seg_start = i
    tail = _extract_segment(text, seg_start, length)
    if tail:
        segments.append(tail)
    return segments


def _apply_length_rules(
    text: str,
    segments: Iterable[Segment],
    *,
    min_len: int,
    max_len: int,
    hard_max: int,
    weak_enabled: bool,
    keep_quotes: bool,
) -> List[Segment]:
    expanded: List[Segment] = []
    for seg in segments:
        seg_len = seg.end - seg.start
        if seg_len <= max_len:
            expanded.append(seg)
            continue
        expanded.extend(
            _split_segment_by_length(
                text,
                seg.start,
                seg.end,
                max_len=max_len,
                hard_max=hard_max,
                weak_enabled=weak_enabled,
                keep_quotes=keep_quotes,
            )
        )
    merged = _merge_short_segments(expanded, min_len=min_len)
    return merged


def _split_segment_by_length(
    source: str,
    start: int,
    end: int,
    *,
    max_len: int,
    hard_max: int,
    weak_enabled: bool,
    keep_quotes: bool,
) -> List[Segment]:
    text = source[start:end]
    segs: List[Segment] = []
    piece_start = 0
    length = len(text)
    i = 0
    last_weak_break = -1
    stack: list[str] = []
    while i < length:
        token, token_len, token_type = _next_token(text, i)
        for offset in range(token_len):
            ch = text[i + offset]
            if keep_quotes:
                _update_stack(stack, ch)
        i += token_len
        inside = keep_quotes and bool(stack)
        if weak_enabled and not inside and token_type == "weak":
            last_weak_break = i
        current_len = i - piece_start
        flush_at = None
        if current_len > hard_max:
            forced = _find_forced_break(text, piece_start, i, hard_max)
            flush_at = forced
        elif current_len >= max_len and last_weak_break > piece_start:
            flush_at = last_weak_break
        if flush_at is not None and flush_at > piece_start:
            seg = _extract_segment(source, start + piece_start, start + flush_at)
            if seg:
                segs.append(seg)
            piece_start = flush_at
            last_weak_break = -1
    tail = _extract_segment(source, start + piece_start, start + length)
    if tail:
        segs.append(tail)
    return segs


def _find_forced_break(text: str, start: int, current: int, hard_max: int) -> int:
    for idx in range(current - 1, start, -1):
        if text[idx] in _FORCED_BREAK_CHARS:
            return idx + 1
    return start + hard_max


def _merge_short_segments(segments: Iterable[Segment], *, min_len: int) -> List[Segment]:
    merged: List[Segment] = []
    carry: Segment | None = None
    for seg in segments:
        current = seg
        if carry is not None:
            current = Segment(
                text=(carry.text + current.text).strip(),
                start=carry.start,
                end=current.end,
            )
            carry = None
        if not current.text:
            continue
        if len(current.text) < min_len:
            if merged:
                prev = merged.pop()
                merged.append(
                    Segment(
                        text=(prev.text + current.text).strip(),
                        start=prev.start,
                        end=current.end,
                    )
                )
            else:
                carry = current
            continue
        merged.append(current)
    if carry is not None:
        if merged:
            prev = merged.pop()
            merged.append(
                Segment(
                    text=(prev.text + carry.text).strip(),
                    start=prev.start,
                    end=carry.end,
                )
            )
        else:
            merged.append(carry)
    return merged


def _next_token(text: str, index: int) -> tuple[str, int, str | None]:
    for token in _ELLIPSIS_TOKENS:
        if text.startswith(token, index):
            return token, len(token), "strong"
    for token in _WEAK_TOKENS:
        if text.startswith(token, index):
            return token, len(token), "weak"
    ch = text[index]
    if ch in _STRONG_CHARS:
        return ch, 1, "strong"
    if ch in _WEAK_CHARS:
        return ch, 1, "weak"
    return ch, 1, None


def _update_stack(stack: list[str], ch: str) -> None:
    if ch in _OPEN_TO_CLOSE:
        stack.append(_OPEN_TO_CLOSE[ch])
        return
    if stack and ch == stack[-1]:
        stack.pop()
        return
    if ch in _SYMMETRIC_QUOTES:
        if stack and stack[-1] == ch:
            stack.pop()
        else:
            stack.append(ch)


def _extract_segment(text: str, start: int, end: int) -> Segment | None:
    if start >= end:
        return None
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    return Segment(text=text[start:end], start=start, end=end)

