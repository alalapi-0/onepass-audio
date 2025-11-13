from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from onepass.dp_path import select_best_path
from onepass.repeat_detect import RepeatCandidate, cluster_candidates
from onepass.text_split import smart_split


def test_cluster_candidates_groups_and_ranks() -> None:
    matches = [
        {
            "candidate_id": 1,
            "line_idx": 0,
            "line_text": "这是第一句。",
            "t0": 0.0,
            "t1": 1.0,
            "score": 0.5,
            "length": 6,
        },
        {
            "candidate_id": 2,
            "line_idx": 0,
            "line_text": "这是第一句。",
            "t0": 5.0,
            "t1": 6.0,
            "score": 0.6,
            "length": 6,
        },
        {
            "candidate_id": 3,
            "line_idx": 1,
            "line_text": "这是第二句。",
            "t0": 12.0,
            "t1": 13.0,
            "score": 0.7,
            "length": 6,
        },
    ]
    clusters = cluster_candidates(matches, dedupe_window=10.0)
    assert len(clusters) == 2
    first = clusters[0]
    assert len(first.candidates) == 2
    assert [candidate.rank for candidate in first.candidates] == [1, 2]
    assert [candidate.is_last for candidate in first.candidates] == [False, True]
    assert clusters[1].candidates[0].candidate_id == 3


def _repeat_candidate(**kwargs) -> RepeatCandidate:
    defaults = dict(
        line_key="line",
        line_text="line",
        cluster_id=1,
        rank=1,
        is_last=True,
    )
    defaults.update(kwargs)
    return RepeatCandidate(**defaults)


def test_select_best_path_prefers_latest_versions() -> None:
    first = _repeat_candidate(
        candidate_id=10,
        line_idx=0,
        t0=0.0,
        t1=1.0,
        score=0.2,
        length=4,
        rank=1,
        is_last=False,
    )
    second = _repeat_candidate(
        candidate_id=11,
        line_idx=0,
        t0=4.0,
        t1=5.0,
        score=0.4,
        length=4,
        rank=2,
        is_last=True,
    )
    next_line = _repeat_candidate(
        candidate_id=12,
        line_idx=1,
        t0=6.0,
        t1=7.0,
        score=0.3,
        length=4,
        rank=1,
        is_last=True,
    )
    result = select_best_path(
        [first, second, next_line],
        epsilon=0.01,
        gap_threshold=10.0,
        bonus_late=0.5,
        penalty_pre=-2.0,
        penalty_gap=-0.2,
    )
    assert result.best_ids == [11, 12]
    assert all(row["candidate_id"] in result.best_ids for row in result.path_rows)


def test_smart_split_honors_hard_punct_left_attach() -> None:
    text = "甲。乙。丙，丁。"
    segments = smart_split(
        text,
        min_len=1,
        max_len=10,
        hard_max=12,
        hard_punct="。",
        soft_punct="，",
        punct_attach="left",
    )
    assert segments == ["甲。", "乙。", "丙，丁。"]


def test_smart_split_honors_hard_punct_right_attach() -> None:
    text = "甲。乙？丙。"
    segments = smart_split(
        text,
        min_len=1,
        max_len=10,
        hard_max=12,
        hard_punct="。？",
        soft_punct="，",
        punct_attach="right",
    )
    assert segments == ["甲", "。乙", "？丙。"]


def test_smart_split_right_attach_trailing_punct() -> None:
    text = "甲。"
    segments = smart_split(
        text,
        min_len=1,
        max_len=10,
        hard_max=12,
        hard_punct="。",
        soft_punct="，",
        punct_attach="right",
    )
    assert segments == ["甲。"]
