#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin CLI wrapper around :mod:`onepass.text_normalizer`."""
from __future__ import annotations

import argparse
import csv
import glob
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

warnings.warn(
    "scripts/normalize_original.py 已弃用：默认参数与 all-in-one 不再一致，仅保留给旧流水线使用。",
    DeprecationWarning,
    stacklevel=2,
)

from onepass.text_normalizer import (
    TextNormConfig,
    load_normalize_char_map,
    normalize_text_for_export,
    split_sentences_with_rules,
)


def _bool_or_default(value: Optional[bool], default: bool) -> bool:
    return default if value is None else bool(value)


def _list_input_files(in_path: Path, pattern: Optional[str]) -> List[Path]:
    if in_path.is_file():
        return [in_path]
    if in_path.is_dir():
        if pattern:
            return [Path(p) for p in glob.glob(str(in_path / pattern), recursive=True)]
        return list(in_path.rglob("*.txt"))
    return []


def _ensure_out_path(out_dir: Path, in_path: Path, suffix: str = ".norm.txt") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = in_path.name
    stem = base[:-4] if base.endswith(".txt") else base
    return out_dir / f"{stem}{suffix}"


def _write_report(rows: List[Dict[str, object]], out_dir: Path) -> None:
    csv_path = out_dir / "normalize_report.csv"
    fieldnames = [
        "file",
        "bytes_in",
        "bytes_out",
        "merged_wraps",
        "merged_examples",
        "nfkc_applied",
        "whitespace_normalized",
        "char_map_replaced",
        "glyph_map_replaced",
        "opencc_mode",
        "opencc_applied",
        "suspects_compat_count",
        "suspects_fullwidth_count",
        "suspects_compat_chars",
        "suspects_fullwidth_chars",
        "profile",
        "norm_strip_newlines",
        "norm_collapse_space",
        "norm_ascii_gap",
        "norm_dash_policy",
        "norm_strip_punct",
        "asr_emitted",
        "asr_strip_newlines",
        "asr_collapse_space",
        "asr_ascii_gap",
        "asr_dash_policy",
        "asr_strip_punct",
        "bytes_norm",
        "bytes_asr",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_config(args: argparse.Namespace) -> TextNormConfig:
    collapse_lines = _bool_or_default(args.collapse_lines, True)
    return TextNormConfig(
        collapse_lines=collapse_lines,
        squash_mixed_english=_bool_or_default(args.ascii_gap, True),
        drop_ascii_parens=True,
    )


def process_file(
    path: Path,
    out_dir: Path,
    *,
    char_map: Dict[str, object],
    cfg: TextNormConfig,
    dry_run: bool,
    profile: str,
    strip_punct: str,
) -> Dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return {
            "file": str(path),
            "bytes_in": 0,
            "bytes_out": 0,
            "merged_wraps": 0,
            "merged_examples": "",
            "nfkc_applied": False,
            "whitespace_normalized": False,
            "char_map_replaced": 0,
            "glyph_map_replaced": 0,
            "opencc_mode": "none",
            "opencc_applied": False,
            "suspects_compat_count": 0,
            "suspects_fullwidth_count": 0,
            "suspects_compat_chars": "",
            "suspects_fullwidth_chars": "",
            "profile": profile,
            "norm_strip_newlines": False,
            "norm_collapse_space": True,
            "norm_ascii_gap": True,
            "norm_dash_policy": "normalize",
            "norm_strip_punct": strip_punct,
            "asr_emitted": False,
            "asr_strip_newlines": False,
            "asr_collapse_space": False,
            "asr_ascii_gap": False,
            "asr_dash_policy": "",
            "asr_strip_punct": "",
            "bytes_norm": 0,
            "bytes_asr": 0,
            "status": f"failed: {exc}",
        }

    normalized = normalize_text_for_export(raw, char_map, cfg)
    sentences = split_sentences_with_rules(normalized, cfg)
    payload = "\n".join(sentences)
    if not dry_run:
        out_path = _ensure_out_path(out_dir, path)
        out_path.write_text(payload, encoding="utf-8")
    encoded_in = raw.encode("utf-8", errors="ignore")
    encoded_out = payload.encode("utf-8", errors="ignore")
    return {
        "file": str(path),
        "bytes_in": len(encoded_in),
        "bytes_out": len(encoded_out),
        "merged_wraps": 0,
        "merged_examples": "",
        "nfkc_applied": True,
        "whitespace_normalized": True,
        "char_map_replaced": 0,
        "glyph_map_replaced": 0,
        "opencc_mode": "none",
        "opencc_applied": False,
        "suspects_compat_count": 0,
        "suspects_fullwidth_count": 0,
        "suspects_compat_chars": "",
        "suspects_fullwidth_chars": "",
            "profile": profile,
        "norm_strip_newlines": not cfg.collapse_lines,
        "norm_collapse_space": cfg.collapse_lines,
        "norm_ascii_gap": cfg.squash_mixed_english,
        "norm_dash_policy": "normalize",
        "norm_strip_punct": strip_punct,
        "asr_emitted": False,
        "asr_strip_newlines": False,
        "asr_collapse_space": False,
        "asr_ascii_gap": False,
        "asr_dash_policy": "",
        "asr_strip_punct": "",
        "bytes_norm": len(encoded_out),
        "bytes_asr": 0,
        "status": "ok",
        "sentences": len(sentences),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="OnePass-Audio 文本规范化（薄封装）")
    ap.add_argument("--in", dest="inp", required=True, help="输入：文件 或 目录")
    ap.add_argument("--out", dest="out_dir", required=True, help="输出目录（自动创建）")
    ap.add_argument("--glob", dest="glob_pat", default=None, help="目录输入时匹配通配符")
    ap.add_argument("--char-map", dest="char_map", default=None, help="自定义字符映射 JSON")
    ap.add_argument("--glyph-map", dest="glyph_map", default=None, help="危险字形白名单 JSON")
    ap.add_argument("--opencc", dest="opencc", default="none", help="兼容旧参数，占位")
    ap.add_argument("--nfkc", action="store_true", default=False, help="兼容旧参数，占位")
    ap.add_argument("--dry-run", action="store_true", default=False, help="只生成报表，不写出文本")
    ap.add_argument("--no-merge-wraps", dest="merge_wraps", action="store_false", default=True)
    ap.add_argument("--strip-punct", choices=["none", "keep-eos", "all"], default="none")
    ap.add_argument("--strip-newlines", dest="strip_newlines", action="store_true", default=None)
    ap.add_argument("--no-strip-newlines", dest="strip_newlines", action="store_false")
    ap.add_argument("--collapse-lines", dest="collapse_lines", action="store_true", default=None)
    ap.add_argument("--no-collapse-lines", dest="collapse_lines", action="store_false")
    ap.add_argument("--collapse-space", dest="collapse_space", action="store_true", default=None)
    ap.add_argument("--no-collapse-space", dest="collapse_space", action="store_false")
    ap.add_argument("--ascii-gap", dest="ascii_gap", action="store_true", default=None)
    ap.add_argument("--no-ascii-gap", dest="ascii_gap", action="store_false")
    ap.add_argument("--dash-policy", choices=["normalize", "remove"], default="normalize")
    ap.add_argument("--profile", choices=["default", "asr"], default="default")
    ap.add_argument("--strip-punct-mode", choices=["keep-eos", "all"], default="keep-eos")
    ap.add_argument("--emit-asr", dest="emit_asr", action="store_true", default=None)
    ap.add_argument("--no-emit-asr", dest="emit_asr", action="store_false")
    args = ap.parse_args()

    in_path = Path(args.inp)
    out_dir = Path(args.out_dir)
    files = _list_input_files(in_path, args.glob_pat)
    if not files:
        print(f"[ERR] 未找到输入文件：{in_path}（pattern={args.glob_pat}）", file=sys.stderr)
        sys.exit(2)

    char_map_path = Path(args.char_map) if args.char_map else None
    char_map = load_normalize_char_map(str(char_map_path) if char_map_path else None)
    cfg = _build_config(args)

    report_rows: List[Dict[str, object]] = []
    for fp in files:
        row = process_file(
            fp,
            out_dir,
            char_map=char_map,
            cfg=cfg,
            dry_run=args.dry_run,
            profile=args.profile,
            strip_punct=args.strip_punct,
        )
        report_rows.append(row)

    _write_report(report_rows, out_dir)
    print(f"[DONE] 文件数：{len(report_rows)}；输出目录：{out_dir}")


if __name__ == "__main__":
    main()
