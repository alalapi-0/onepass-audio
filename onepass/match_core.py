"""Anchor-based matching helpers for keep-last alignment."""
from __future__ import annotations

from dataclasses import dataclass
import bisect
import logging
import math
import time
from typing import Dict, List, Optional, Sequence, Tuple

from .canonicalize import CanonicalAliasMap, canonicalize
from .words_loader import Token

LOGGER = logging.getLogger(__name__)

@dataclass(slots=True)
class TokenStream:
    tokens: List[Token]
    canonical_text: str
    raw_text: str
    char_boundaries: List[int]


@dataclass(slots=True)
class MatchResult:
    tok_start: int
    tok_end: int
    time_start: float
    time_end: float
    score: float
    anchor_hits: int
    method: str


def build_token_stream(tokens: Sequence[Token], alias: Dict[str, str] | CanonicalAliasMap | None) -> TokenStream:
    raw = "".join(token.get("text", "") or "" for token in tokens)
    canonical = canonicalize(raw, alias)
    boundaries: List[int] = [0]
    cursor = 0
    for token in tokens:
        cursor += len(token.get("text", "") or "")
        boundaries.append(cursor)
    return TokenStream(tokens=list(tokens), canonical_text=canonical, raw_text=raw, char_boundaries=boundaries)


def _bounded_lev(left: str, right: str, max_ratio: float) -> float:
    if not left and not right:
        return 0.0
    if not left or not right:
        return 1.0
    n, m = len(left), len(right)
    limit = int(math.ceil(max(n, m) * max(0.0, max_ratio))) + 1
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        current = [i] + [0] * m
        min_row = current[0]
        li = left[i - 1]
        for j in range(1, m + 1):
            cost = 0 if li == right[j - 1] else 1
            current[j] = min(prev[j] + 1, current[j - 1] + 1, prev[j - 1] + cost)
            if current[j] < min_row:
                min_row = current[j]
        if limit > 0 and min_row > limit:
            return max_ratio + 1.0
        prev = current
    distance = prev[m]
    return distance / max(n, m)


def _char_to_token(char_boundaries: Sequence[int], pos: int, *, right: bool = False) -> int:
    if not char_boundaries:
        return 0
    if right:
        idx = bisect.bisect_left(char_boundaries, pos)
    else:
        idx = bisect.bisect_right(char_boundaries, pos) - 1
    idx = max(0, min(idx, len(char_boundaries) - 2))
    return idx


def match_line_to_tokens(
    line: str,
    stream: TokenStream,
    alias: Dict[str, str] | CanonicalAliasMap | None,
    *,
    min_anchor_ngram: int = 6,
    max_windows: int = 200,
    max_distance_ratio: float = 0.35,
    match_timeout: float | None = 60.0,
    prefer_latest: bool = True,
) -> Optional[MatchResult]:
    if not stream.tokens or not stream.canonical_text:
        return None
    normalized_line = canonicalize(line, alias)
    if not normalized_line:
        return None
    max_windows = max(1, int(max_windows))
    anchor_len = max(1, min(min_anchor_ngram, len(normalized_line)))
    deadline = None
    if match_timeout and match_timeout > 0:
        deadline = time.time() + match_timeout
    windows: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    anchor_hits = 0
    text = stream.canonical_text
    total_len = len(text)
    start_idx = 0
    while start_idx + anchor_len <= len(normalized_line):
        anchor = normalized_line[start_idx : start_idx + anchor_len]
        search_from = len(text) if prefer_latest else 0
        found = -1
        while True:
            if deadline and time.time() > deadline:
                break
            if prefer_latest:
                found = text.rfind(anchor, 0, search_from)
                if found == -1:
                    break
                search_from = max(0, found)
            else:
                found = text.find(anchor, search_from)
                if found == -1:
                    break
                search_from = found + 1
            anchor_hits += 1
            left = max(0, found - len(normalized_line))
            right = min(total_len, found + len(normalized_line))
            key = (left, right)
            if key not in seen:
                seen.add(key)
                windows.append(key)
            if len(windows) >= max_windows:
                break
            if not prefer_latest:
                continue
            search_from = found
        if len(windows) >= max_windows or (deadline and time.time() > deadline):
            break
        start_idx += 1
    if prefer_latest:
        windows.sort(reverse=True)
    else:
        windows.sort()
    best: MatchResult | None = None

    def _update(tok_lo: int, tok_hi: int, score: float, method: str) -> None:
        nonlocal best
        tok_lo = max(0, min(tok_lo, len(stream.tokens) - 1))
        tok_hi = max(tok_lo + 1, min(tok_hi, len(stream.tokens)))
        t_start = stream.tokens[tok_lo]["start"]
        t_end = stream.tokens[tok_hi - 1]["end"]
        candidate = MatchResult(
            tok_start=tok_lo,
            tok_end=tok_hi,
            time_start=t_start,
            time_end=t_end,
            score=score,
            anchor_hits=anchor_hits,
            method=method,
        )
        if best is None:
            best = candidate
            return
        if score < best.score - 1e-6:
            best = candidate
            return
        if abs(score - best.score) <= 1e-6 and prefer_latest and t_end > best.time_end:
            best = candidate

    for left, right in windows:
        if deadline and time.time() > deadline:
            break
        if right <= left:
            continue
        tok_lo = _char_to_token(stream.char_boundaries, left)
        tok_hi = _char_to_token(stream.char_boundaries, right, right=True)
        if tok_hi <= tok_lo:
            tok_hi = tok_lo + 1
        candidate_text = text[left:right]
        ratio = _bounded_lev(normalized_line, candidate_text, max_distance_ratio)
        if ratio <= max_distance_ratio:
            _update(tok_lo, tok_hi, ratio, "anchor+lev")
            continue
    if best is None:
        window = max(len(normalized_line), anchor_len * 2)
        cursor = total_len
        while cursor > 0:
            if deadline and time.time() > deadline:
                break
            left = max(0, cursor - window)
            if cursor - left <= 0:
                cursor -= max(1, window // 2)
                continue
            tok_lo = _char_to_token(stream.char_boundaries, left)
            tok_hi = _char_to_token(stream.char_boundaries, cursor, right=True)
            if tok_hi <= tok_lo:
                tok_hi = tok_lo + 1
            candidate_text = text[left:cursor]
            ratio = _bounded_lev(normalized_line, candidate_text, max_distance_ratio)
            if ratio <= max_distance_ratio:
                _update(tok_lo, tok_hi, ratio, "greedy-back")
                break
            cursor -= max(1, window // 2)
    return best


def align_text(
    lines: Sequence[str],
    tokens: Sequence[Token],
    *,
    alias_map: Dict[str, str] | CanonicalAliasMap | None = None,
    min_anchor_ngram: int = 6,
    max_distance_ratio: float = 0.35,
    fallback_policy: str = "greedy",
    max_windows: int = 200,
    match_timeout: float | None = 60.0,
    prefer_latest: bool = True,
    dedupe_policy: str | None = None,
) -> tuple[list[MatchResult | None], dict[str, object]]:
    """Align *lines* against *tokens*, returning matches and config metadata."""

    LOGGER.info(
        "[align_text] alias_map_size=%s dedupe_policy=%s min_anchor_ngram=%s max_distance_ratio=%.2f fallback_policy=%s",
        len(alias_map or {}),
        dedupe_policy or "none",
        min_anchor_ngram,
        max_distance_ratio,
        fallback_policy,
    )
    stream = build_token_stream(tokens, alias_map)
    matches: list[MatchResult | None] = []
    for line in lines:
        match = match_line_to_tokens(
            line,
            stream,
            alias_map,
            min_anchor_ngram=min_anchor_ngram,
            max_windows=max_windows,
            max_distance_ratio=max_distance_ratio,
            match_timeout=match_timeout,
            prefer_latest=prefer_latest,
        )
        matches.append(match)
    metadata = {
        "alias_map_size": len(alias_map or {}),
        "dedupe_policy": dedupe_policy or "none",
        "min_anchor_ngram": int(min_anchor_ngram),
        "max_distance_ratio": float(max_distance_ratio),
        "fallback_policy": fallback_policy,
    }
    return matches, metadata


__all__ = [
    "MatchResult",
    "TokenStream",
    "align_text",
    "build_token_stream",
    "match_line_to_tokens",
]
