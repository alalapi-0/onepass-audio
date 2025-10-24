"""onepass.retake
用途: 对比原文句子与 ASR 词序列，识别重读句并保留最后一次朗读。
依赖: Python 标准库 dataclasses、logging、statistics；第三方 ``rapidfuzz`` 通过 ``onepass.align`` 使用。
示例: ``from onepass.retake import find_retake_keeps``。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .align import MatchTuple, find_sentence_matches, refine_with_dp_if_needed
from .textnorm import SentencePiece, split_sentences
from .types import KeepSpan, Word

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MatchInfo:
    """描述句子在词序列中的一次出现。"""

    i: int
    j: int
    score: float
    start: float
    end: float


def _interval_iou(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    if end <= start:
        return 0.0
    union = max(a[1], b[1]) - min(a[0], b[0])
    if union <= 0:
        return 0.0
    return (end - start) / union


def _merge_candidate_windows(matches: Sequence[MatchInfo], merge_gap: float) -> list[MatchInfo]:
    if not matches:
        return []
    clusters: list[list[MatchInfo]] = []
    for match in sorted(matches, key=lambda m: (m.start, m.end)):
        if not clusters:
            clusters.append([match])
            continue
        last_cluster = clusters[-1]
        cluster_end = max(item.end for item in last_cluster)
        overlap = max(_interval_iou((item.start, item.end), (match.start, match.end)) for item in last_cluster)
        gap = match.start - cluster_end
        if overlap > 0 or gap < merge_gap:
            last_cluster.append(match)
        else:
            clusters.append([match])
    merged: list[MatchInfo] = []
    for cluster in clusters:
        best = max(cluster, key=lambda m: (m.score, m.end))
        merged.append(best)
    return merged


def _make_match_info(match: MatchTuple) -> MatchInfo:
    return MatchInfo(i=match[0], j=match[1], score=float(match[2]), start=float(match[3]), end=float(match[4]))


def _select_keep(occurrences: Sequence[MatchInfo], keep_mode: str) -> MatchInfo:
    if keep_mode == "best":
        key_fn = lambda m: (m.score, m.end)
    else:
        key_fn = lambda m: (m.end, m.score)
    return max(occurrences, key=key_fn)


def _to_keep_span(match: MatchInfo) -> KeepSpan:
    return KeepSpan(i=match.i, j=match.j, score=match.score, start=match.start, end=match.end)


def _build_diff_item(
    piece: SentencePiece, kept: MatchInfo, deleted: Sequence[MatchInfo], keep_mode: str
) -> dict:
    return {
        "sent_raw": piece.raw,
        "sent_norm": piece.norm,
        "kept": {"start": kept.start, "end": kept.end, "score": kept.score},
        "deleted": [
            {"start": item.start, "end": item.end, "score": item.score}
            for item in sorted(deleted, key=lambda m: (m.start, m.end))
        ],
        "hit_count": 1 + len(deleted),
        "max_score": max([kept.score] + [item.score for item in deleted]) if kept or deleted else 0.0,
        "remark": f"overlap_keep={keep_mode}",
    }


def find_retake_keeps(
    words: List[Word], original_text: str, cfg: dict
) -> Tuple[List[KeepSpan], List[Tuple[float, float]], List[dict]]:
    """根据原文句子查找需保留的朗读窗口及重录剪切区间。"""

    sentences = split_sentences(original_text, cfg)
    keeps: list[KeepSpan] = []
    retake_cuts: list[tuple[float, float]] = []
    diff_items: list[dict] = []
    merge_gap = float(cfg.get("merge_gap_s", 0.25))
    keep_mode = str(cfg.get("overlap_keep", "last")).lower()
    if keep_mode not in {"last", "best"}:
        logger.warning("unknown overlap_keep=%s, fallback to 'last'", keep_mode)
        keep_mode = "last"

    for piece in sentences:
        raw_matches = find_sentence_matches(words, piece.norm, cfg)
        if not raw_matches:
            continue
        refined: list[MatchInfo] = []
        for match in raw_matches:
            refined_match = refine_with_dp_if_needed(match, piece.norm, words, cfg)
            refined.append(_make_match_info(refined_match))
        occurrences = _merge_candidate_windows(refined, merge_gap)
        if not occurrences:
            continue
        kept = _select_keep(occurrences, keep_mode)
        keeps.append(_to_keep_span(kept))
        deleted = [occ for occ in occurrences if occ is not kept]
        for occ in deleted:
            retake_cuts.append((occ.start, occ.end))
        diff_items.append(_build_diff_item(piece, kept, deleted, keep_mode))

    keeps.sort(key=lambda span: (span.start, span.end))
    retake_cuts.sort()
    return keeps, retake_cuts, diff_items
