from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import argparse
import json
import re

from onepass.alignment.canonical import CanonicalRules, concat_and_index
from onepass.asr_loader import Word, load_words
from onepass.retake_keep_last import compute_retake_keep_last
from onepass.text_norm import collapse_and_resplit
from scripts.onepass_cli import DEFAULT_CHAR_MAP, run_all_in_one, run_prep_norm


def _default_canonical_rules() -> CanonicalRules:
    payload = json.loads(DEFAULT_CHAR_MAP.read_text(encoding="utf-8"))
    mapping = payload.get("map", {}) if isinstance(payload, dict) else {}
    mapping = {str(key): str(value) for key, value in mapping.items()}
    return CanonicalRules(char_map=mapping)


def _allowed_boundary_chars() -> set[str]:
    return set("。！？!?；;：:…．.」』”’》）】\"")


def _assert_sentence_lines(text: str, *, enforce_boundary: bool = True) -> None:
    boundary = _allowed_boundary_chars()
    for line in text.splitlines():
        assert line == line.strip(), "sentence line should not have leading/trailing spaces"
    assert "\t" not in text, "sentence text should not contain tabs"
    if enforce_boundary:
        pattern = re.compile(r"(\S)\n(?=\S)")
        for match in pattern.finditer(text):
            assert (
                match.group(1) in boundary
            ), f"unexpected newline boundary after {match.group(1)!r}"


def test_norm_outputs_are_sentence_lines(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    input_txt = repo_root / "materials" / "001序言01.txt"
    out_dir = repo_root / "out" / "tests" / tmp_path.name / "norm"
    run_prep_norm(
        input_txt,
        out_dir,
        DEFAULT_CHAR_MAP,
        "none",
        "*.txt",
        dry_run=False,
        collapse_lines=True,
        emit_align=True,
        allow_missing_char_map=False,
    )
    norm_path = out_dir / "001序言01.norm.txt"
    align_path = out_dir / "001序言01.align.txt"
    canonical_path = out_dir / "001序言01.canonical.txt"
    norm_text = norm_path.read_text(encoding="utf-8")
    align_text = align_path.read_text(encoding="utf-8")
    canonical_text = canonical_path.read_text(encoding="utf-8")
    assert norm_text.strip(), "normalized text should not be empty"
    assert "\r" not in norm_text
    assert "\t" not in norm_text
    assert norm_text.count("\n") >= 1
    _assert_sentence_lines(norm_text)
    assert "\r" not in align_text
    align_lines = align_text.splitlines()
    assert align_lines, "align text should contain sentence lines"
    for line in align_lines:
        assert line == line.strip(), "align sentence should not have surrounding spaces"
        assert "\t" not in line
    _assert_sentence_lines(align_text, enforce_boundary=False)
    rules = _default_canonical_rules()
    expected_canonical, _, line_spans = concat_and_index(align_lines, rules)
    assert canonical_text == expected_canonical, "canonical text should match normalized align lines"
    assert len(line_spans) == len(align_lines)
    prev_end = 0
    for start, end in line_spans:
        assert start <= end
        assert start >= prev_end
        prev_end = end


def test_all_in_one_without_audio_reports_records(tmp_path: Path) -> None:
    materials_dir = tmp_path / "materials"
    materials_dir.mkdir()
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "out" / "tests" / tmp_path.name
    out_dir.mkdir(parents=True, exist_ok=True)
    text_path = materials_dir / "sample.txt"
    text_path.write_text("第一句內容。\n第二句變了。", encoding="utf-8")
    words_path = materials_dir / "sample.words.json"
    words_payload = {
        "segments": [
            {
                "start": 0.0,
                "end": 2.2,
                "words": [
                    {"word": "第一", "start": 0.0, "end": 0.3},
                    {"word": "句", "start": 0.3, "end": 0.6},
                    {"word": "內容", "start": 0.6, "end": 0.9},
                    {"word": "第二", "start": 1.0, "end": 1.3},
                    {"word": "句", "start": 1.3, "end": 1.6},
                    {"word": "變", "start": 1.6, "end": 1.9},
                    {"word": "更", "start": 1.9, "end": 2.2},
                ],
            }
        ]
    }
    words_path.write_text(json.dumps(words_payload, ensure_ascii=False), encoding="utf-8")
    args = argparse.Namespace(
        input_dir=str(materials_dir),
        output_dir=str(out_dir),
        emit_align=True,
        collapse_lines=True,
        char_map=str(DEFAULT_CHAR_MAP),
        opencc="none",
        norm_glob="*.txt",
        glob_words="*.words.json",
        glob_audio="*.wav;*.m4a;*.mp3;*.flac",
        render_mode="auto",
        workers=None,
        no_interaction=True,
        verbose=False,
        quiet=False,
    )
    report = run_all_in_one(args)
    records = report["summary"]["records"]
    assert records, "expected pipeline records in summary"
    stems = {record["stem"] for record in records}
    assert stems, "expected stems in pipeline records"
    for record in records:
        assert record["render"] == "skipped(no-audio)"
    stats = report["summary"].get("retake_stats", [])
    assert len(stats) == len(records)
    for record in records:
        if record["status"] != "ok":
            continue
        stem = record["stem"]
        assert (out_dir / f"{stem}.keepLast.srt").exists()
        assert (out_dir / f"{stem}.keepLast.txt").exists()
        assert (out_dir / f"{stem}.keepLast.edl.json").exists()
        assert (out_dir / f"{stem}.keepLast.audition_markers.csv").exists()


def test_collapse_and_resplit_handles_cjk_spacing() -> None:
    sample = (
        "第一段「測試」包含\t空白……第二段緊接。"
        "第三段混合 English. Next Sentence!\n第四段：結束。"
    )
    lines = collapse_and_resplit(sample)
    assert len(lines) > 1
    for line in lines:
        assert "\t" not in line
        assert "\r" not in line
        assert not re.search(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", line)


def test_compute_retake_keep_last_uses_fuzzy_match(tmp_path: Path) -> None:
    text_path = tmp_path / "source.txt"
    text_path.write_text("第一句內容。\n第二句變了。", encoding="utf-8")
    words = [
        Word(text="第一", start=0.0, end=0.3),
        Word(text="句", start=0.3, end=0.6),
        Word(text="內容", start=0.6, end=0.9),
        Word(text="第二", start=1.0, end=1.3),
        Word(text="句", start=1.3, end=1.6),
        Word(text="變", start=1.6, end=1.9),
        Word(text="更", start=1.9, end=2.2),
    ]
    result = compute_retake_keep_last(
        words,
        text_path,
        pause_align=False,
        pad_before=0.0,
        pad_after=0.0,
        merge_gap_sec=0.0,
    )
    assert result.stats["matched_lines"] >= 1
    assert result.stats["fallback_matches"] >= 1
    assert not result.fallback_used
    assert result.keeps, "expected keep spans from fuzzy alignment"


def test_compute_retake_keep_last_triggers_fallback(tmp_path: Path) -> None:
    text_path = tmp_path / "mismatch.txt"
    text_path.write_text("完全不同的句子。\n另一句也不同。", encoding="utf-8")
    words = [
        Word(text="你好", start=0.0, end=0.4),
        Word(text="世界", start=0.4, end=0.8),
    ]
    result = compute_retake_keep_last(
        words,
        text_path,
        pause_align=False,
        pad_before=0.0,
        pad_after=0.0,
        merge_gap_sec=0.0,
    )
    assert result.fallback_used
    assert result.stats["fallback_used"] is True
    assert result.stats["fallback_reason"] == "no-match"
    assert result.stats["matched_lines"] == 0
    assert result.edl_keep_segments == [(0.0, words[-1].end)]
    assert result.fallback_marker_note and "NO_MATCH_FALLBACK" in result.fallback_marker_note
    history = result.stats.get("degrade_history")
    assert isinstance(history, list) and history, "expected degrade history entries"
    fallback_entries = [entry for entry in history if entry.get("reason") == "fallback"]
    assert fallback_entries, "fallback entry should be recorded in degrade history"
    assert fallback_entries[0].get("policy")
    assert "no-match" in fallback_entries[0].get("fallback_reasons", [])


def test_retake_align_line_count_preserved(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    input_txt = repo_root / "materials" / "001序言01.txt"
    words_path = repo_root / "materials" / "001序言01.json"
    out_dir = repo_root / "out" / "tests" / tmp_path.name / "align"
    run_prep_norm(
        input_txt,
        out_dir,
        DEFAULT_CHAR_MAP,
        "none",
        "*.txt",
        dry_run=False,
        collapse_lines=True,
        emit_align=True,
        allow_missing_char_map=False,
    )
    align_path = out_dir / "001序言01.align.txt"
    assert align_path.exists(), "align file should be generated"
    words = list(load_words(words_path))
    result = compute_retake_keep_last(words, align_path, no_collapse_align=True)
    count = result.stats.get("align_line_count_read")
    assert isinstance(count, int)
    assert count >= 80
