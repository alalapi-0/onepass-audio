from onepass.retake.matcher import MatchRequest, StableMatcher


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
