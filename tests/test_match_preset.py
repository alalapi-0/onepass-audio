import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from onepass.retake.matcher import MatchRequest, StableMatcher
from scripts.onepass_cli import (
    DEFAULT_MATCH_FALLBACK_POLICY,
    DEFAULT_MATCH_MAX_DISTANCE_RATIO,
    DEFAULT_MATCH_MIN_ANCHOR_NGRAM,
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
    assert ratio == pytest.approx(DEFAULT_MATCH_MAX_DISTANCE_RATIO)
    assert anchor == DEFAULT_MATCH_MIN_ANCHOR_NGRAM
    assert fallback == DEFAULT_MATCH_FALLBACK_POLICY
    assert pause_gap == pytest.approx(0.45)


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
    assert anchor == DEFAULT_MATCH_MIN_ANCHOR_NGRAM
    assert fallback == "keep-all"
    assert pause_gap == pytest.approx(0.42)
