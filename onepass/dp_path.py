from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from .repeat_detect import RepeatCandidate


@dataclass(slots=True)
class DPPathResult:
    best_ids: list[int]
    path_rows: list[dict[str, object]]


def _candidate_value(candidate: RepeatCandidate, bonus_late: float, penalty_pre: float) -> tuple[float, float, float, float]:
    len_bonus = min(0.2, 0.01 * max(candidate.length, 0))
    late_bonus = bonus_late * max(1, candidate.rank)
    pre_penalty = 0.0
    if not candidate.is_last:
        pre_penalty = penalty_pre
    base = candidate.score + len_bonus + late_bonus + pre_penalty
    return base, late_bonus, pre_penalty, len_bonus


def _gap_penalty(gap: float, threshold: float, epsilon: float, penalty_gap: float) -> float:
    if gap < -epsilon:
        return penalty_gap
    if threshold > 0.0 and gap > threshold:
        return penalty_gap
    return 0.0


def select_best_path(
    candidates: Iterable[RepeatCandidate],
    *,
    epsilon: float,
    gap_threshold: float,
    bonus_late: float,
    penalty_pre: float,
    penalty_gap: float,
) -> DPPathResult:
    ordered = sorted(candidates, key=lambda c: (c.t0, c.t1, c.candidate_id))
    if not ordered:
        return DPPathResult(best_ids=[], path_rows=[])
    n = len(ordered)
    scores: List[float] = [0.0] * n
    prev: List[int] = [-1] * n
    gap_used: List[float] = [0.0] * n
    late_values: List[float] = [0.0] * n
    pre_penalties: List[float] = [0.0] * n
    epsilon = max(0.0, float(epsilon))
    threshold = max(0.0, float(gap_threshold))
    for idx, candidate in enumerate(ordered):
        base, late_bonus, pre_penalty, _ = _candidate_value(candidate, bonus_late, penalty_pre)
        late_values[idx] = late_bonus
        pre_penalties[idx] = pre_penalty
        scores[idx] = base
        prev[idx] = -1
        gap_used[idx] = 0.0
        for j in range(idx):
            prev_candidate = ordered[j]
            if prev_candidate.t1 > candidate.t0 + epsilon:
                continue
            gap = candidate.t0 - prev_candidate.t1
            penalty = _gap_penalty(gap, threshold, epsilon, penalty_gap)
            value = scores[j] + base + penalty
            if value > scores[idx]:
                scores[idx] = value
                prev[idx] = j
                gap_used[idx] = penalty
    best_idx = max(range(n), key=lambda i: (scores[i], ordered[i].t1))
    best_ids: list[int] = []
    path_rows: list[dict[str, object]] = []
    order = 0
    cursor = best_idx
    while cursor >= 0:
        candidate = ordered[cursor]
        order += 1
        penalty_details = []
        if pre_penalties[cursor]:
            penalty_details.append(f"pre={pre_penalties[cursor]:.2f}")
        if gap_used[cursor]:
            penalty_details.append(f"gap={gap_used[cursor]:.2f}")
        path_rows.append(
            {
                "order": order,
                "line_idx": candidate.line_idx,
                "t0": candidate.t0,
                "t1": candidate.t1,
                "value": scores[cursor],
                "late_bonus": late_values[cursor],
                "penalties": ",".join(penalty_details) if penalty_details else "-",
                "candidate_id": candidate.candidate_id,
            }
        )
        best_ids.append(candidate.candidate_id)
        cursor = prev[cursor]
    best_ids.reverse()
    path_rows.reverse()
    for idx, row in enumerate(path_rows, start=1):
        row["order"] = idx
    return DPPathResult(best_ids=best_ids, path_rows=path_rows)


__all__ = ["DPPathResult", "select_best_path"]
