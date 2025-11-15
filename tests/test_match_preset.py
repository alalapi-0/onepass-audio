import sys
from argparse import Namespace
from pathlib import Path

import pytest
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from onepass.retake.matcher import MatchRequest, StableMatcher
from scripts.onepass_cli import (
    DEFAULT_CHAR_MAP,
    DEFAULT_MATCH_FALLBACK_POLICY,
    DEFAULT_MATCH_MAX_DISTANCE_RATIO,
    DEFAULT_MATCH_MIN_ANCHOR_NGRAM,
    PAUSE_GAP_SEC,
    STRICT_PRESET_CHAR_MAP,
    _apply_text_preset,
    _resolve_match_parameters,
)


def test_tolerant_preset_matches_ascii_mixture():
    matcher = StableMatcher("甲乙丙丁戊己庚辛")
    target = "甲乙丙丁戊己庚辛2024A"
    strict_request = MatchRequest(
        target_text=target,
        max_distance_ratio=0.30,
        min_anchor_ngram=6,
        max_windows=32,
        deadline=None,
    )
    tolerant_request = MatchRequest(
        target_text=target,
        max_distance_ratio=0.45,
        min_anchor_ngram=4,
        max_windows=32,
        deadline=None,
    )
    assert not matcher.match(strict_request).success
    assert matcher.match(tolerant_request).success


def test_strict_preset_maps_to_defaults():
    ratio, anchor, fallback, pause_gap = _resolve_match_parameters(
        "strict_zh_punct",
        0.1,
        2,
        "keep-all",
        0.2,
        ratio_explicit=False,
        anchor_explicit=False,
        fallback_explicit=False,
        pause_gap_explicit=False,
    )
    assert ratio == pytest.approx(0.45)
    assert anchor == 4
    assert fallback == "greedy+expand"
    assert pause_gap == pytest.approx(PAUSE_GAP_SEC)


def test_match_preset_respects_explicit_overrides():
    ratio, anchor, fallback, pause_gap = _resolve_match_parameters(
        "strict_zh_punct",
        0.3,
        DEFAULT_MATCH_MIN_ANCHOR_NGRAM,
        "keep-all",
        0.42,
        ratio_explicit=True,
        anchor_explicit=False,
        fallback_explicit=True,
        pause_gap_explicit=True,
    )
    assert ratio == pytest.approx(0.3)
    assert anchor == 4
    assert fallback == "keep-all"
    assert pause_gap == pytest.approx(0.42)


def test_apply_text_preset_sets_sentence_strategy():
    args = Namespace(
        match_preset="strict_zh_punct",
        collapse_lines=False,
        char_map=str(DEFAULT_CHAR_MAP),
        split_mode="punct+len",
        weak_punct_enable=True,
        prosody_split=True,
        split_attach="left",
        min_len=32,
        max_len=120,
        hard_max=140,
        max_distance_ratio=None,
        min_anchor_ngram=None,
        fallback_policy="",
    )
    _apply_text_preset(args)
    assert args.collapse_lines
    assert args.split_mode == "punct"
    assert args.weak_punct_enable is False
    assert args.prosody_split is False
    assert args.split_attach == "right"
    assert args.min_len <= 8
    assert args.max_len >= 2000
    assert args.hard_max >= 4000
    assert args.max_distance_ratio == pytest.approx(0.45)
    assert args.min_anchor_ngram == 4
    assert args.fallback_policy == "greedy+expand"
    assert Path(args.char_map).name == STRICT_PRESET_CHAR_MAP.name
