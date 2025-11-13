from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

DEFAULT_HARD_PUNCT = "。！？!?．.;；"
DEFAULT_SOFT_PUNCT = "，、,:：；;……—"
JOIN_WITH_NEXT_PREFIXES = (
    "例如",
    "比如",
    "举例来说",
    "相较之下",
    "这种观点认为",
    "因此",
    "所以",
    "然而",
    "但是",
    "首先",
    "其次",
    "总之",
    "也就是说",
)


def _char_set(value: str | Sequence[str] | None, fallback: str) -> set[str]:
    source = value if value is not None else fallback
    chars: set[str] = set()
    if isinstance(source, str):
        chars.update(ch for ch in source if ch and not ch.isspace())
        return chars
    for item in source:
        if not item:
            continue
        if isinstance(item, str):
            chars.update(ch for ch in item if ch and not ch.isspace())
    if not chars and fallback:
        chars.update(ch for ch in fallback if ch and not ch.isspace())
    return chars


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    return text


def _flush(
    buf: List[str],
    out: List[str],
    reason: str,
    debug: Optional[List[Tuple[str, int, str]]] = None,
) -> None:
    if not buf:
        return
    seg = "".join(buf).strip()
    if seg:
        out.append(seg)
        if debug is not None:
            debug.append((seg, len(seg), reason))
    buf.clear()


def _split_buffer(
    buf: List[str],
    cut_at: int,
    out: List[str],
    reason: str,
    debug: Optional[List[Tuple[str, int, str]]],
) -> None:
    if cut_at <= 0 or cut_at >= len(buf):
        _flush(buf, out, reason, debug)
        return
    left = buf[:cut_at]
    right = buf[cut_at:]
    buf[:] = right
    _flush(left, out, reason, debug)


def smart_split(
    text: str,
    min_len: int = 8,
    max_len: int = 24,
    hard_max: int = 32,
    *,
    hard_punct: str | Sequence[str] | None = None,
    soft_punct: str | Sequence[str] | None = None,
    punct_attach: str = "left",
    return_debug: bool = False,
) -> List[str] | Tuple[List[str], List[Tuple[str, int, str]]]:
    text = _normalize(text)
    hard_set = _char_set(hard_punct, DEFAULT_HARD_PUNCT)
    soft_set = _char_set(soft_punct, DEFAULT_SOFT_PUNCT)
    if soft_set:
        soft_set.difference_update(hard_set)
    attach_left = (punct_attach or "left").lower() != "right"
    out: List[str] = []
    debug_rows: Optional[List[Tuple[str, int, str]]] = [] if return_debug else None
    buf: List[str] = []
    last_soft_idx: Optional[int] = None

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in hard_set:
            if attach_left:
                buf.append(ch)
                _flush(buf, out, f"HARD:{ch}", debug_rows)
            else:
                _flush(buf, out, f"HARD:{ch}", debug_rows)
                buf.append(ch)
            last_soft_idx = None
            i += 1
            continue
        buf.append(ch)
        if ch in soft_set:
            last_soft_idx = len(buf)
        cur_len = len(buf)
        if cur_len >= hard_max:
            cut_at = last_soft_idx or hard_max
            _split_buffer(buf, cut_at, out, "HARD_MAX/SOFT_OR_FORCED", debug_rows)
            last_soft_idx = None
        elif cur_len >= max_len:
            if last_soft_idx:
                cut_at = last_soft_idx
            else:
                cut_at = cur_len
            if cut_at != cur_len:
                _split_buffer(buf, cut_at, out, "MAX_LEN/SOFT", debug_rows)
                last_soft_idx = None
        i += 1

    _flush(buf, out, "TAIL", debug_rows)

    j = 0
    while j < len(out) - 1:
        seg = out[j].lstrip()
        short_gate = max(6, min_len)
        if seg.startswith(JOIN_WITH_NEXT_PREFIXES) and len(seg) <= short_gate:
            out[j] = out[j] + out[j + 1]
            del out[j + 1]
            if debug_rows is not None:
                debug_rows[j] = (out[j], len(out[j]), "JOIN_WITH_NEXT")
                del debug_rows[j + 1]
            continue
        j += 1

    k = 0
    while k < len(out):
        if len(out[k]) < min_len:
            if k + 1 < len(out) and len(out[k]) + len(out[k + 1]) <= hard_max:
                out[k] = out[k] + out[k + 1]
                del out[k + 1]
                if debug_rows is not None:
                    debug_rows[k] = (out[k], len(out[k]), "FUSE_RIGHT_MIN_LEN")
                    del debug_rows[k + 1]
                continue
            if k - 1 >= 0 and len(out[k - 1]) + len(out[k]) <= hard_max:
                out[k - 1] = out[k - 1] + out[k]
                del out[k]
                if debug_rows is not None:
                    debug_rows[k - 1] = (out[k - 1], len(out[k - 1]), "FUSE_LEFT_MIN_LEN")
                    del debug_rows[k]
                continue
        k += 1

    final_out: List[str] = []
    final_dbg: List[Tuple[str, int, str]] = []
    hard_regex = ""
    if hard_set:
        escaped = re.escape("".join(sorted(hard_set)))
        hard_regex = rf"([{escaped}])"
    for idx, seg in enumerate(out):
        base_reason = ""
        if debug_rows is not None and idx < len(debug_rows):
            base_reason = debug_rows[idx][2]
        if not hard_regex:
            cleaned = seg.strip()
            if cleaned:
                final_out.append(cleaned)
                if debug_rows is not None:
                    final_dbg.append((cleaned, len(cleaned), base_reason or "OK"))
            continue
        parts = re.split(hard_regex, seg)
        if len(parts) == 1:
            cleaned = seg.strip()
            if cleaned:
                final_out.append(cleaned)
                if debug_rows is not None:
                    final_dbg.append((cleaned, len(cleaned), base_reason or "OK"))
            continue
        cur = ""
        for part in parts:
            if not part:
                continue
            cur += part
            if part[-1] in hard_set:
                token = cur.strip()
                if token:
                    final_out.append(token)
                    if debug_rows is not None:
                        final_dbg.append((token, len(token), "POST_SPLIT_HARD"))
                cur = ""
        remainder = cur.strip()
        if remainder:
            final_out.append(remainder)
            if debug_rows is not None:
                final_dbg.append((remainder, len(remainder), "POST_TAIL"))

    if return_debug:
        return final_out, final_dbg
    return final_out
