from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ._legacy_text_norm import apply_alias_map, normalize_for_align

try:  # pragma: no cover - optional dependency
    from pypinyin import Style, lazy_pinyin
except Exception:  # pragma: no cover - fallback when pypinyin missing
    Style = None  # type: ignore

    def lazy_pinyin(text: str, style: object | None = None, strict: bool = False) -> list[str]:
        return [char for char in text]

LOGGER = logging.getLogger(__name__)

_CJK_CHAR = re.compile(r"[\u3400-\u9fff]")


@dataclass(slots=True)
class RepeatCandidate:
    candidate_id: int
    line_idx: int
    line_key: str
    line_text: str
    t0: float
    t1: float
    score: float
    length: int
    cluster_id: int = -1
    rank: int = 0
    is_last: bool = False


@dataclass(slots=True)
class RepeatCluster:
    line_key: str
    cluster_id: int
    candidates: list[RepeatCandidate] = field(default_factory=list)


def supports_pinyin() -> bool:
    return Style is not None and lazy_pinyin is not None


def _normalize_line(text: str, alias_map: Mapping[str, Sequence[str]] | None) -> str:
    normalized = normalize_for_align(text or "")
    if alias_map:
        normalized = apply_alias_map(normalized, alias_map)
    return normalized


def _pinyin_key(text: str) -> str:
    if not text:
        return ""
    tokens: list[str] = []
    for char in text:
        if _CJK_CHAR.match(char):
            try:
                letters = lazy_pinyin(char, style=Style.FIRST_LETTER if Style else None, strict=False)
            except Exception:  # pragma: no cover - third party errors
                letters = []
            if letters and letters[0]:
                tokens.append(letters[0][0])
                continue
        normalized = unicodedata.normalize("NFKC", char).lower()
        if normalized:
            tokens.append(normalized[0])
    return "".join(tokens)


def _normalized_distance(left: str, right: str) -> float:
    if left == right:
        return 0.0
    if not left or not right:
        return 1.0
    n, m = len(left), len(right)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        current = [i] + [0] * m
        li = left[i - 1]
        for j in range(1, m + 1):
            cost = 0 if li == right[j - 1] else 1
            current[j] = min(prev[j] + 1, current[j - 1] + 1, prev[j - 1] + cost)
        prev = current
    distance = prev[m]
    return distance / max(n, m, 1)


def _assign_line_key(
    normalized_text: str,
    *,
    alias_map: Mapping[str, Sequence[str]] | None,
    eq_mode: str,
    existing: list[tuple[str, str]],
    dist_max: float,
) -> str:
    base = normalized_text
    if eq_mode == "pinyin":
        base = _pinyin_key(base) or base
    if not base:
        base = "_"
    for key, reference in existing:
        if base == key:
            return key
        if _normalized_distance(base, reference) <= dist_max:
            return key
    existing.append((base, base))
    return base


def cluster_candidates(
    matches: Iterable[dict],
    *,
    alias_map: Mapping[str, Sequence[str]] | None = None,
    eq_mode: str = "char",
    dist_max: float = 0.15,
    dedupe_window: float = 12.0,
) -> list[RepeatCluster]:
    eq_value = (eq_mode or "char").lower()
    if eq_value not in {"char", "pinyin"}:
        eq_value = "char"
    dist_gate = max(0.0, float(dist_max))
    window = max(0.0, float(dedupe_window))
    key_refs: list[tuple[str, str]] = []
    grouped: dict[str, list[RepeatCandidate]] = {}
    for match in matches:
        line_text = str(match.get("line_text", "") or "")
        normalized = _normalize_line(line_text, alias_map)
        key = _assign_line_key(normalized, alias_map=alias_map, eq_mode=eq_value, existing=key_refs, dist_max=dist_gate)
        candidate = RepeatCandidate(
            candidate_id=int(match.get("candidate_id", -1)),
            line_idx=int(match.get("line_idx", 0)),
            line_key=key,
            line_text=line_text,
            t0=float(match.get("t0", 0.0) or 0.0),
            t1=float(match.get("t1", 0.0) or 0.0),
            score=float(match.get("score", 0.0) or 0.0),
            length=int(match.get("length", 0) or 0),
        )
        grouped.setdefault(key, []).append(candidate)
    clusters: list[RepeatCluster] = []
    cluster_id = 0
    for key, candidates in grouped.items():
        ordered = sorted(candidates, key=lambda c: (c.t0, c.t1, c.candidate_id))
        current: list[RepeatCandidate] = []
        last_t0 = None
        for candidate in ordered:
            if not current:
                current.append(candidate)
                last_t0 = candidate.t0
                continue
            gap = None if last_t0 is None else candidate.t0 - last_t0
            should_split = window <= 0.0
            if not should_split and gap is not None and gap > window:
                should_split = True
            if should_split:
                cluster_id += 1
                _finalize_cluster(current, clusters, key, cluster_id)
                current = [candidate]
            else:
                current.append(candidate)
            last_t0 = candidate.t0
        if current:
            cluster_id += 1
            _finalize_cluster(current, clusters, key, cluster_id)
    return clusters


def _finalize_cluster(
    bucket: list[RepeatCandidate],
    clusters: list[RepeatCluster],
    key: str,
    cluster_id: int,
) -> None:
    for idx, candidate in enumerate(bucket, start=1):
        candidate.cluster_id = cluster_id
        candidate.rank = idx
        candidate.is_last = idx == len(bucket)
    clusters.append(RepeatCluster(line_key=key, cluster_id=cluster_id, candidates=list(bucket)))


__all__ = [
    "RepeatCandidate",
    "RepeatCluster",
    "cluster_candidates",
    "supports_pinyin",
]
