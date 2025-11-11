from __future__ import annotations

import argparse
import re
from pathlib import Path

from scripts.onepass_cli import DEFAULT_CHAR_MAP, run_all_in_one, run_prep_norm


def _allowed_boundary_chars() -> set[str]:
    return set("。！？!?；;…．.」』”’》）】")


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
    norm_text = norm_path.read_text(encoding="utf-8")
    align_text = align_path.read_text(encoding="utf-8")
    assert norm_text.strip(), "normalized text should not be empty"
    _assert_sentence_lines(norm_text)
    _assert_sentence_lines(align_text, enforce_boundary=False)


def test_all_in_one_without_audio_reports_records(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = repo_root / "out" / "tests" / tmp_path.name / "batch"
    args = argparse.Namespace(
        input_dir=str(repo_root / "materials"),
        output_dir=str(output_dir),
        emit_align=True,
        collapse_lines=True,
        char_map=str(DEFAULT_CHAR_MAP),
        opencc="none",
        norm_glob="*.txt",
        glob_words="*.json",
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
        assert (output_dir / f"{stem}.keepLast.srt").exists()
        assert (output_dir / f"{stem}.keepLast.txt").exists()
        assert (output_dir / f"{stem}.keepLast.edl.json").exists()
        assert (output_dir / f"{stem}.keepLast.audition_markers.csv").exists()
