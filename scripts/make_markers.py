"""CLI for producing "keep last take" EDL JSON and Adobe Audition markers.

Example
-------
python scripts/make_markers.py --json data/asr-json/001.json \
    --original data/original_txt/001.txt --outdir out
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from onepass.align import align_sentences
from onepass.asr_loader import Word, load_words
from onepass.edl import EDL, build_keep_last_edl
from onepass.markers import write_audition_markers
from onepass.textnorm import Sentence, normalize_sentence, split_sentences, tokenize_for_match


def _prepare_sentences(raw_text: str) -> List[Sentence]:
    sentences: List[Sentence] = []
    for raw_sentence in split_sentences(raw_text):
        normalised = normalize_sentence(raw_sentence)
        if not normalised:
            continue
        tokens = tokenize_for_match(normalised)
        if not tokens:
            continue
        sentences.append(Sentence(text=normalised, tokens=tokens))
    return sentences


def _warn_mismatch(words: List[Word], sentences: List[Sentence]) -> None:
    if not words or not sentences:
        return
    if len(sentences) > len(words) * 1.5:
        print("[warning] sentence count much larger than ASR words; check transcript", file=sys.stderr)


def _edl_to_dict(edl: EDL) -> dict:
    return {
        "audio_stem": edl.audio_stem,
        "sample_rate": edl.sample_rate,
        "actions": [asdict(action) for action in edl.actions],
        "stats": edl.stats,
        "created_at": edl.created_at,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate keep-last markers from ASR output")
    parser.add_argument("--json", required=True, help="Path to ASR word-level JSON")
    parser.add_argument("--original", required=True, help="Path to original transcript TXT")
    parser.add_argument("--outdir", default="out", help="Output directory")
    parser.add_argument("--score", type=int, default=80, help="Alignment score threshold")
    args = parser.parse_args(argv)

    json_path = Path(args.json)
    original_path = Path(args.original)
    outdir = Path(args.outdir)

    try:
        words = load_words(json_path)
    except Exception as exc:  # pragma: no cover - CLI surface
        print(f"failed to load words: {exc}", file=sys.stderr)
        return 1

    try:
        raw_text = original_path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - CLI surface
        print(f"failed to read transcript: {exc}", file=sys.stderr)
        return 1

    sentences = _prepare_sentences(raw_text)
    _warn_mismatch(words, sentences)

    align = align_sentences(words, sentences, score_threshold=args.score)
    edl = build_keep_last_edl(words, align)

    stem = json_path.stem or original_path.stem
    edl.audio_stem = stem

    outdir.mkdir(parents=True, exist_ok=True)
    edl_path = outdir / f"{stem}.keepLast.edl.json"
    markers_path = outdir / f"{stem}.keepLast.audition_markers.csv"

    with edl_path.open("w", encoding="utf-8") as f:
        json.dump(_edl_to_dict(edl), f, ensure_ascii=False, indent=2)

    write_audition_markers(edl, markers_path)

    total_hits = sum(1 for m in align.kept.values() if m is not None) + sum(
        len(windows) for windows in align.dups.values()
    )

    print(
        f"sentences={len(sentences)} unaligned={len(align.unaligned)} "
        f"hits={total_hits} cut_sec={edl.stats['total_cut_sec']:.3f}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
