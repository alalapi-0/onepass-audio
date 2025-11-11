#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OnePass-Audio 文本规范化脚本（可直接覆盖使用）

功能：
1) 可选 NFKC（全/半角等兼容形归一）
2) 统一空白、行末空白清理
3) 自定义字符映射（JSON）
4) 可选 OpenCC 简繁/地区转换（t2s/s2t/s2tw/s2twp/s2hk 等）
5) 支持对单文件 / 目录 / 通配符批处理
6) 生成可疑字符扫描报表（CSV/JSON），方便人工二次校对
7) 额外输出 `<stem>.asr.txt`，面向语音对齐的极简文本

用法示例：
  python scripts/normalize_original.py \
    --in data/original_txt \
    --out out/norm_fixed \
    --glob "*.txt" \
    --char-map config/default_char_map.json \
    --opencc t2s \
    --profile asr \
    --strip-punct-mode keep-eos

若你已有旧参数/调用方式，也可以最简：
  python scripts/normalize_original.py --in path/to/file.txt --out out
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from onepass.text_norm import (
    merge_hard_wraps,
    normalize_chinese_text,
    sentence_lines_from_text,
    validate_sentence_lines,
)

# ========== 可选依赖：OpenCC ==========
_OPENCC = None


def _get_opencc(mode: str):
    global _OPENCC
    if _OPENCC is None:
        try:
            from opencc import OpenCC  # type: ignore

            _OPENCC = OpenCC
        except Exception:
            _OPENCC = False
    if _OPENCC is False:
        return None
    try:
        return _OPENCC(mode)
    except Exception:
        return None


# CJK 兼容部件区间（用于扫描）：U+2E80..U+2EFF（部首补充） & U+2F00..U+2FDF（康熙部首）
COMPAT_BLOCKS = [
    (0x2E80, 0x2EFF),
    (0x2F00, 0x2FDF),
]

FULLWIDTH_PUNCT = set("（）［］【】｛｝：；？！（）“”‘’，。、．《》〈〉—…·　")
CJK_PUNCTS = "，。、：；！？“”‘’（）《》〈〉【】…—"
ASCII_PUNCTS = r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~"""
_NL_CHARS = {"\r", "\n", "\u2028", "\u2029"}
_WS_RE = re.compile(r"[ \t\u00A0]+")


def is_compat_char(ch: str) -> bool:
    cp = ord(ch)
    for a, b in COMPAT_BLOCKS:
        if a <= cp <= b:
            return True
    return False


def load_char_map(path: Optional[Path]) -> Dict:
    """加载自定义字符映射 JSON。支持结构：
    {
      "normalize_width": true/false,
      "normalize_whitespace": true/false,
      "map": {"Ａ":"A","Ｂ":"B",...}
    }
    """
    cfg = {
        "normalize_width": False,
        "normalize_whitespace": True,
        "map": {},
    }
    if not path:
        return cfg
    if not path.exists():
        print(f"[WARN] char-map 文件不存在：{path}", file=sys.stderr)
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update({k: data.get(k, cfg[k]) for k in cfg.keys()})
    except Exception as exc:
        print(f"[WARN] 读取 char-map 失败：{exc}", file=sys.stderr)
    return cfg


def load_glyph_map(path: Optional[Path]) -> Dict[str, str]:
    """加载危险字形白名单映射。键值均需为单字符。"""
    if not path:
        return {}
    if not path.exists():
        print(f"[WARN] glyph-map 文件不存在：{path}", file=sys.stderr)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[WARN] 读取 glyph-map 失败：{exc}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"[WARN] glyph-map 需为对象映射，实际类型：{type(data).__name__}", file=sys.stderr)
        return {}
    result: Dict[str, str] = {}
    for k, v in data.items():
        if not (isinstance(k, str) and isinstance(v, str) and len(k) == 1 and len(v) == 1):
            print(f"[WARN] glyph-map 条目无效：{k!r}->{v!r}（需单字符映射）", file=sys.stderr)
            continue
        result[k] = v
    return result


def apply_nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def apply_whitespace(text: str) -> str:
    # 统一行尾空白、将多空白压为单空格（不破坏换行）
    text = re.sub(r"[ \t]+\r?\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")
    return text


def apply_char_map(text: str, mp: Dict[str, str]) -> Tuple[str, int]:
    cnt = 0
    for k, v in mp.items():
        if not k:
            continue
        if k in text:
            occurrences = text.count(k)
            text = text.replace(k, v)
            cnt += occurrences
    return text, cnt


def run_opencc(text: str, mode: str) -> Tuple[str, bool]:
    if not mode or mode.lower() == "none":
        return text, False
    cc = _get_opencc(mode)
    if cc is None:
        print(f"[WARN] OpenCC 不可用或模式无效：{mode}，跳过简繁转换", file=sys.stderr)
        return text, False
    try:
        return cc.convert(text), True
    except Exception as exc:
        print(f"[WARN] OpenCC 转换失败：{exc}", file=sys.stderr)
        return text, False


def scan_suspects(text: str) -> Dict[str, List[str]]:
    """扫描文本里的可疑字符片段（兼容部件/全角标点）。"""
    suspects_compat = sorted({ch for ch in text if is_compat_char(ch)})
    suspects_fullwidth = sorted({ch for ch in text if ch in FULLWIDTH_PUNCT})
    return {
        "compat_chars": suspects_compat,
        "fullwidth_punct": suspects_fullwidth,
    }


def ensure_out_path(out_dir: Path, in_path: Path, suffix: str = ".norm.txt") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = in_path.name
    stem = base[:-4] if base.endswith(".txt") else base
    return out_dir / f"{stem}{suffix}"


def norm_dash(text: str, policy: str = "normalize") -> str:
    if policy == "remove":
        return text.replace("——", "").replace("—", "").replace("--", "")
    normalized = text.replace("--", "——").replace("—", "——")
    while "———" in normalized:
        normalized = normalized.replace("———", "——")
    return normalized


def merge_soft_wraps(text: str, *, ascii_gap: bool = True, collapse_space: bool = True) -> str:
    parts: List[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch in _NL_CHARS:
            j = i
            while j < length and text[j] in _NL_CHARS:
                j += 1
            prev = parts[-1][-1] if parts else ""
            nxt = text[j] if j < length else ""
            if j - i >= 2:
                if not (parts and parts[-1].endswith("\n")):
                    parts.append("\n")
            else:
                if (
                    ascii_gap
                    and prev
                    and nxt
                    and prev.isascii()
                    and nxt.isascii()
                    and (prev.isalnum() or prev == "_")
                    and (nxt.isalnum() or nxt == "_")
                ):
                    parts.append(" ")
            i = j
            continue
        parts.append(" " if ch == "\u00A0" else ch)
        i += 1
    result = "".join(parts)
    if collapse_space:
        result = _WS_RE.sub(" ", result)
    return result.strip()


def strip_punct_all(text: str) -> str:
    tbl = {ord(c): None for c in (CJK_PUNCTS + ASCII_PUNCTS)}
    return text.translate(tbl)


def strip_punct_keep_eos(text: str) -> str:
    keep = {"。", "！", "？"}
    tbl = {ord(c): (c if c in keep else None) for c in (CJK_PUNCTS + ASCII_PUNCTS)}
    return text.translate(tbl)


def apply_output_policies(text: str, *, options: Dict[str, object]) -> str:
    dash_policy = str(options.get("dash_policy", "normalize"))
    strip_newlines = bool(options.get("strip_newlines", False))
    collapse_space = bool(options.get("collapse_space", True))
    ascii_gap = bool(options.get("ascii_gap", True))
    strip_punct = str(options.get("strip_punct", "none"))
    collapse_lines_opt = bool(options.get("collapse_lines", True))

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")
    text = text.replace("\u00A0", " ")
    text = norm_dash(text, dash_policy)

    if strip_newlines:
        processed = merge_soft_wraps(text, ascii_gap=ascii_gap, collapse_space=collapse_space)
    else:
        processed = text
        if collapse_space:
            processed = _WS_RE.sub(" ", processed)

    if strip_punct == "all":
        processed = strip_punct_all(processed)
    elif strip_punct == "keep-eos":
        processed = strip_punct_keep_eos(processed)

    if collapse_space:
        processed = _WS_RE.sub(" ", processed)

    if collapse_lines_opt:
        lines = sentence_lines_from_text(processed, collapse_lines=True)
        if lines:
            validate_sentence_lines(lines)
        processed = "\n".join(lines)
    else:
        processed = "\n".join(sentence_lines_from_text(processed, collapse_lines=False))

    return processed.strip()


def process_text(
    text: str,
    args: argparse.Namespace,
    cmap_cfg: Dict,
    glyph_map: Dict[str, str],
    norm_options: Dict[str, object],
    asr_options: Optional[Dict[str, object]],
) -> Tuple[str, Optional[str], Dict[str, object]]:
    stats: Dict[str, object] = {
        "nfkc_applied": False,
        "whitespace_normalized": False,
        "char_map_replaced": 0,
        "glyph_map_replaced": 0,
        "opencc_mode": args.opencc,
        "opencc_applied": False,
    }

    if args.nfkc or cmap_cfg.get("normalize_width", False):
        text = apply_nfkc(text)
        stats["nfkc_applied"] = True

    if cmap_cfg.get("normalize_whitespace", True):
        text = apply_whitespace(text)
        stats["whitespace_normalized"] = True

    mp = cmap_cfg.get("map", {}) or {}
    if mp:
        text, replaced = apply_char_map(text, mp)
        stats["char_map_replaced"] = replaced

    if glyph_map:
        text, replaced = apply_char_map(text, glyph_map)
        stats["glyph_map_replaced"] = replaced

    if args.opencc and args.opencc.lower() != "none":
        text, ok = run_opencc(text, args.opencc)
        stats["opencc_applied"] = ok

    collapse_lines_flag = bool(norm_options.get("collapse_lines", True))
    norm_text = apply_output_policies(text, options=norm_options)
    norm_text = normalize_chinese_text(norm_text, collapse_lines=collapse_lines_flag)
    asr_text: Optional[str] = None
    if asr_options is not None:
        asr_text = apply_output_policies(text, options=asr_options)
        asr_collapse_flag = bool(asr_options.get("collapse_lines", True))
        asr_text = normalize_chinese_text(asr_text, collapse_lines=asr_collapse_flag)

    return norm_text, asr_text, stats


def list_input_files(in_path: Path, pattern: Optional[str]) -> List[Path]:
    if in_path.is_file():
        return [in_path]
    files: List[Path] = []
    if in_path.is_dir():
        if pattern:
            files = [Path(p) for p in glob.glob(str(in_path / pattern), recursive=True)]
        else:
            files = list(in_path.rglob("*.txt"))
    return [p for p in files if p.is_file()]


def write_reports(report_rows: List[Dict[str, object]], out_dir: Path) -> None:
    # CSV
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
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in report_rows:
            writer.writerow(row)

    # JSON
    json_path = out_dir / "normalize_report.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report_rows, f, ensure_ascii=False, indent=2)

    print(f"[OK] 报表已生成：\n  - {csv_path}\n  - {json_path}")


def _bool_or_default(value: Optional[bool], default: bool) -> bool:
    return default if value is None else bool(value)


def main() -> None:
    ap = argparse.ArgumentParser(description="OnePass-Audio 文本规范化（覆盖版）")
    ap.add_argument("--in", dest="inp", required=True, help="输入：文件 或 目录")
    ap.add_argument("--out", dest="out_dir", required=True, help="输出目录（自动创建）")
    ap.add_argument("--glob", dest="glob_pat", default=None, help="（当 --in 是目录时）匹配的通配符，如 *.txt")
    ap.add_argument("--char-map", dest="char_map", default=None, help="自定义字符映射 JSON 文件路径")
    ap.add_argument("--glyph-map", dest="glyph_map", default=None, help="危险字形白名单 JSON（需手动指定）")
    ap.add_argument("--opencc", dest="opencc", default="none", help="OpenCC 模式（如 t2s/s2t，默认 none）")
    ap.add_argument("--nfkc", action="store_true", default=False, help="对输入做 unicodedata.normalize('NFKC')")
    ap.add_argument("--dry-run", action="store_true", default=False, help="只生成报表，不写出文本")
    ap.add_argument("--no-merge-wraps", dest="merge_wraps", action="store_false", default=True,
                    help="跳过硬换行合并，仅在原文内容需要严格保持行结构时使用")
    ap.add_argument("--strip-punct", choices=["none", "keep-eos", "all"], default="none",
                    help="控制 .norm.txt 的标点处理（默认 none）")
    ap.add_argument("--strip-newlines", dest="strip_newlines", action="store_true", default=None,
                    help="去掉所有换行（默认开启，可用 --no-strip-newlines 覆盖）")
    ap.add_argument("--no-strip-newlines", dest="strip_newlines", action="store_false",
                    help="保留换行（覆盖 --strip-newlines）")
    ap.add_argument("--collapse-lines", dest="collapse_lines", action="store_true", default=None,
                    help="合并换行/制表符为单空格后再按句分行（默认开启，可用 --no-collapse-lines 覆盖）")
    ap.add_argument("--no-collapse-lines", dest="collapse_lines", action="store_false",
                    help="保留原始换行结构（覆盖 --collapse-lines）")
    ap.add_argument("--collapse-space", dest="collapse_space", action="store_true", default=None,
                    help="合并重复空白为单空格（默认开启，可用 --no-collapse-space 覆盖）")
    ap.add_argument("--no-collapse-space", dest="collapse_space", action="store_false",
                    help="保留重复空白")
    ap.add_argument("--ascii-gap", dest="ascii_gap", action="store_true", default=None,
                    help="软换行合并时在 ASCII/数字之间插空格（默认开启，可用 --no-ascii-gap 覆盖）")
    ap.add_argument("--no-ascii-gap", dest="ascii_gap", action="store_false",
                    help="禁用 ASCII 连写自动插空格")
    ap.add_argument("--dash-policy", choices=["normalize", "remove"], default="normalize",
                    help="破折号策略：统一或移除（默认 normalize）")
    ap.add_argument("--profile", choices=["default", "asr"], default="default",
                    help="预设：default 保持旧流程，asr 生成极简对齐文本")
    ap.add_argument("--strip-punct-mode", choices=["keep-eos", "all"], default="keep-eos",
                    help="仅 --profile asr 时生效，控制 .asr.txt 保留句末标点与否")
    ap.add_argument("--emit-asr", dest="emit_asr", action="store_true", default=None,
                    help="生成 <stem>.asr.txt（默认开启，可用 --no-emit-asr 关闭）")
    ap.add_argument("--no-emit-asr", dest="emit_asr", action="store_false", help="禁用 .asr.txt 输出")

    args = ap.parse_args()

    in_path = Path(args.inp)
    out_dir = Path(args.out_dir)
    char_map_path = Path(args.char_map) if args.char_map else None
    glyph_map_path = Path(args.glyph_map) if args.glyph_map else None

    cmap_cfg = load_char_map(char_map_path)
    glyph_map = load_glyph_map(glyph_map_path)

    files = list_input_files(in_path, args.glob_pat)
    if not files:
        print(f"[ERR] 未找到输入文件：{in_path}（pattern={args.glob_pat}）", file=sys.stderr)
        sys.exit(2)

    out_dir.mkdir(parents=True, exist_ok=True)

    emit_asr = True if args.emit_asr is None else bool(args.emit_asr)

    norm_options = {
        "strip_newlines": _bool_or_default(args.strip_newlines, False),
        "collapse_space": _bool_or_default(args.collapse_space, True),
        "ascii_gap": _bool_or_default(args.ascii_gap, True),
        "dash_policy": args.dash_policy,
        "strip_punct": args.strip_punct,
        "collapse_lines": _bool_or_default(args.collapse_lines, True),
    }
    if args.profile == "asr":
        asr_options = {
            "strip_newlines": True,
            "collapse_space": True,
            "ascii_gap": True,
            "dash_policy": "remove",
            "strip_punct": args.strip_punct_mode,
            "collapse_lines": True,
        } if emit_asr else None
    else:
        asr_options = norm_options.copy() if emit_asr else None

    report_rows: List[Dict[str, object]] = []

    for fp in files:
        try:
            raw = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            print(f"[WARN] 读取失败：{fp} -> {exc}", file=sys.stderr)
            continue

        merged_text = raw
        merged_count = 0
        merged_examples: List[str] = []
        if args.merge_wraps:
            merged_text = merge_hard_wraps(raw)
            info = getattr(merge_hard_wraps, "last_stats", {})
            merged_count = int(info.get("merged_count", 0))
            merged_examples = list(info.get("examples", []))

        norm, asr_text, stats = process_text(merged_text, args, cmap_cfg, glyph_map, norm_options, asr_options)
        suspects = scan_suspects(norm)

        row = {
            "file": str(fp),
            "bytes_in": len(raw.encode("utf-8", errors="ignore")),
            "bytes_out": len(norm.encode("utf-8", errors="ignore")),
            "merged_wraps": merged_count,
            "merged_examples": ", ".join(merged_examples),
            **stats,
            "suspects_compat_count": len(suspects["compat_chars"]),
            "suspects_fullwidth_count": len(suspects["fullwidth_punct"]),
            "suspects_compat_chars": "".join(suspects["compat_chars"]),
            "suspects_fullwidth_chars": "".join(suspects["fullwidth_punct"]),
            "profile": args.profile,
            "norm_strip_newlines": bool(norm_options.get("strip_newlines")),
            "norm_collapse_space": bool(norm_options.get("collapse_space")),
            "norm_ascii_gap": bool(norm_options.get("ascii_gap")),
            "norm_dash_policy": norm_options.get("dash_policy"),
            "norm_strip_punct": norm_options.get("strip_punct"),
            "asr_emitted": bool(asr_options is not None),
            "asr_strip_newlines": bool(asr_options.get("strip_newlines")) if asr_options else False,
            "asr_collapse_space": bool(asr_options.get("collapse_space")) if asr_options else False,
            "asr_ascii_gap": bool(asr_options.get("ascii_gap")) if asr_options else False,
            "asr_dash_policy": asr_options.get("dash_policy") if asr_options else "",
            "asr_strip_punct": asr_options.get("strip_punct") if asr_options else "",
            "bytes_norm": len(norm.encode("utf-8", errors="ignore")),
            "bytes_asr": len(asr_text.encode("utf-8", errors="ignore")) if asr_text else 0,
        }
        report_rows.append(row)

        if args.dry_run:
            continue

        out_fp = ensure_out_path(out_dir, fp, ".norm.txt")
        try:
            out_fp.write_text(norm, encoding="utf-8")
        except Exception as exc:
            print(f"[WARN] 写入失败：{out_fp} -> {exc}", file=sys.stderr)

        if asr_text:
            asr_fp = ensure_out_path(out_dir, fp, ".asr.txt")
            try:
                asr_fp.write_text(asr_text, encoding="utf-8")
            except Exception as exc:
                print(f"[WARN] 写入失败：{asr_fp} -> {exc}", file=sys.stderr)

    write_reports(report_rows, out_dir)
    print(f"[DONE] 文件数：{len(report_rows)}；输出目录：{out_dir}")


if __name__ == "__main__":
    main()
