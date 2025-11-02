#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OnePass-Audio 文本规范化脚本（可直接覆盖使用）

功能：
1) 可选 NFKC（全/半角等兼容形归一）
2) 统一空白、行末空白清理
3) 自定义字符映射（JSON）
4) 兼容部件 → 标准字 修复（⺠⻅⻓⻛⻢⻆⻙⻄ 等）
5) 可选 OpenCC 简繁/地区转换（t2s/s2t/s2tw/s2twp/s2hk 等）
6) 支持对单文件 / 目录 / 通配符批处理
7) 生成可疑字符扫描报表（CSV/JSON），方便人工二次校对

用法示例：
  python scripts/normalize_original.py \
    --in data/original_txt \
    --out out/norm_fixed \
    --glob "*.txt" \
    --char-map config/default_char_map.json \
    --opencc t2s \
    --fix-compat \
    --nfkc

若你已有旧参数/调用方式，也可以最简：
  python scripts/normalize_original.py --in path/to/file.txt --out out
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import csv
import glob
import unicodedata
from pathlib import Path
from typing import Dict, Tuple, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from onepass.text_norm import merge_hard_wraps

# ========== 可选依赖：OpenCC ==========
_OPENCC = None
def _get_opencc(mode: str):
    global _OPENCC
    if _OPENCC is None:
        try:
            from opencc import OpenCC
            _OPENCC = OpenCC
        except Exception:
            _OPENCC = False
    if _OPENCC is False:
        return None
    try:
        return _OPENCC(mode)
    except Exception:
        return None

# ========== 兼容部件修复表（可按需扩充） ==========
EXTRA_COMPAT_MAP: Dict[str, str] = {
    "⺠": "民",  # U+2EE0 -> U+6C11
    "⻅": "见",  # U+2F45 -> U+89C1
    "⻓": "长",  # U+2F93 -> U+957F
    "⻛": "风",  # U+2F9B -> U+98CE
    "⻢": "马",  # U+2FA2 -> U+9A6C
    "⻆": "角",  # U+2F86 -> U+89D2
    "⻙": "韦",  # U+2ED9 -> U+97E6
    "⻄": "西",  # U+2F7D -> U+897F

    "⻜": "飞",  # Kangxi 183（飞/飛部件） -> 飞
    "⻉": "贝",  # 贝/貝 部件 -> 贝
    "⻨": "麦",  # 麦/麥 部件 -> 麦
    "⻰": "龙",  # 龙/龍 部件 -> 龙
    "⻥": "鱼",  # 鱼/魚 部件 -> 鱼
    "⻝": "食",  # “掠⻝者”等应为“食”字，不用饣
    "⻔": "门",  # 门/門 部件 -> 门
    "⻋": "车",  # 车/車 部件 -> 车
    "⻦": "鸟",  # 鸟/鳥 部件 -> 鸟
    "⻮": "齿",  # 齿/齒 部件 -> 齿
    "⻁": "虎",  # 虎 部件 -> 虎
    "⻤": "鬼",  # 鬼 部件 -> 鬼

    "⻣": "骨",  # e.g. 毛⻣悚然
    "⻘": "青",  # e.g. ⻘睐
    "⻚": "页",  # e.g. 专⻚ / 第601⻚
    "⻬": "齐",  # e.g. 对⻬问题
    "⻩": "黄",  # e.g. ⻩金法则
}

# CJK 兼容部件区间（用于扫描）：U+2E80..U+2EFF（部首补充） & U+2F00..U+2FDF（康熙部首）
COMPAT_BLOCKS = [
    (0x2E80, 0x2EFF),
    (0x2F00, 0x2FDF),
]

FULLWIDTH_PUNCT = set("（）［］【】｛｝：；？！（）“”‘’，。、．《》〈〉—…·　")


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
        "map": {}
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
    except Exception as e:
        print(f"[WARN] 读取 char-map 失败：{e}", file=sys.stderr)
    return cfg


def apply_nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def apply_whitespace(text: str) -> str:
    # 统一行尾空白、将多空白压为单空格（不破坏换行）
    # 先去行尾空白
    text = re.sub(r"[ \t]+\r?\n", "\n", text)
    # 将连续空白（不含换行）压缩为一个空格
    text = re.sub(r"[ \t]{2,}", " ", text)
    # 统一换行为 \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def apply_char_map(text: str, mp: Dict[str, str]) -> Tuple[str, int]:
    cnt = 0
    for k, v in mp.items():
        if k in text:
            occurrences = text.count(k)
            text = text.replace(k, v)
            cnt += occurrences
    return text, cnt


def apply_compat_fixes(text: str) -> Tuple[str, int, Dict[str, int]]:
    cnt = 0
    detail = {}
    for k, v in EXTRA_COMPAT_MAP.items():
        if k in text:
            n = text.count(k)
            text = text.replace(k, v)
            cnt += n
            detail[k] = detail.get(k, 0) + n
    return text, cnt, detail


def run_opencc(text: str, mode: str) -> Tuple[str, bool]:
    if not mode or mode.lower() == "none":
        return text, False
    cc = _get_opencc(mode)
    if cc is None:
        print(f"[WARN] OpenCC 不可用或模式无效：{mode}，跳过简繁转换", file=sys.stderr)
        return text, False
    try:
        return cc.convert(text), True
    except Exception as e:
        print(f"[WARN] OpenCC 转换失败：{e}", file=sys.stderr)
        return text, False


def scan_suspects(text: str) -> Dict[str, List[str]]:
    """扫描文本里的可疑字符片段（兼容部件/全角标点）。"""
    suspects_compat = sorted({ch for ch in text if is_compat_char(ch)})
    suspects_fullwidth = sorted({ch for ch in text if ch in FULLWIDTH_PUNCT})
    return {
        "compat_chars": suspects_compat,
        "fullwidth_punct": suspects_fullwidth
    }


def ensure_out_path(out_dir: Path, in_path: Path, suffix: str = ".norm.txt") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = in_path.name
    # 去掉已有扩展，标准化为 .norm.txt
    stem = base
    if base.endswith(".txt"):
        stem = base[:-4]
    return out_dir / f"{stem}{suffix}"


def process_text(text: str, args, cmap_cfg: Dict) -> Tuple[str, Dict]:
    stats = {
        "nfkc_applied": False,
        "whitespace_normalized": False,
        "char_map_replaced": 0,
        "compat_fixed": 0,
        "compat_detail": {},
        "opencc_mode": args.opencc,
        "opencc_applied": False,
    }

    # 0) 可选 NFKC（命令行）或 char-map 中声明 normalize_width
    if args.nfkc or cmap_cfg.get("normalize_width", False):
        text = apply_nfkc(text)
        stats["nfkc_applied"] = True

    # 1) 可选空白归一
    if cmap_cfg.get("normalize_whitespace", True):
        text = apply_whitespace(text)
        stats["whitespace_normalized"] = True

    # 2) 自定义 map
    mp = cmap_cfg.get("map", {}) or {}
    if mp:
        text, c = apply_char_map(text, mp)
        stats["char_map_replaced"] = c

    # 3) 兼容部件修复
    if args.fix_compat:
        text, c, detail = apply_compat_fixes(text)
        stats["compat_fixed"] = c
        stats["compat_detail"] = detail

    # 4) OpenCC
    if args.opencc and args.opencc.lower() != "none":
        text, ok = run_opencc(text, args.opencc)
        stats["opencc_applied"] = ok

    return text, stats


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


def write_reports(report_rows: List[Dict], out_dir: Path):
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
        "compat_fixed",
        "compat_detail",
        "opencc_mode",
        "opencc_applied",
        "suspects_compat_count",
        "suspects_fullwidth_count",
        "suspects_compat_chars",
        "suspects_fullwidth_chars",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fieldnames)
        wr.writeheader()
        for row in report_rows:
            wr.writerow(row)

    # JSON
    json_path = out_dir / "normalize_report.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report_rows, f, ensure_ascii=False, indent=2)

    print(f"[OK] 报表已生成：\n  - {csv_path}\n  - {json_path}")


def main():
    ap = argparse.ArgumentParser(description="OnePass-Audio 文本规范化（覆盖版）")
    ap.add_argument("--in", dest="inp", required=True,
                    help="输入：文件 或 目录")
    ap.add_argument("--out", dest="out_dir", required=True,
                    help="输出目录（自动创建）")
    ap.add_argument("--glob", dest="glob_pat", default=None,
                    help="（当 --in 是目录时）匹配的通配符，如 *.txt")
    ap.add_argument("--char-map", dest="char_map", default=None,
                    help="自定义字符映射 JSON 文件路径")
    ap.add_argument("--opencc", dest="opencc", default="none",
                    help="OpenCC 模式（如 t2s / s2t / s2tw / s2twp / s2hk；默认 none）")
    ap.add_argument("--fix-compat", dest="fix_compat", action="store_true", default=True,
                    help="修复 CJK 兼容部件为标准字（默认开启）")
    ap.add_argument("--no-fix-compat", dest="fix_compat", action="store_false",
                    help="关闭兼容部件修复")
    ap.add_argument("--nfkc", action="store_true", default=False,
                    help="对输入做 unicodedata.normalize('NFKC')（默认关闭）")
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="只生成报表，不写出 .norm.txt")
    ap.add_argument("--no-merge-wraps", dest="merge_wraps", action="store_false", default=True,
                    help="跳过硬换行合并，仅在原文内容需要严格保持行结构时使用")
    args = ap.parse_args()

    in_path = Path(args.inp)
    out_dir = Path(args.out_dir)
    char_map_path = Path(args.char_map) if args.char_map else None

    cmap_cfg = load_char_map(char_map_path)
    files = list_input_files(in_path, args.glob_pat)
    if not files:
        print(f"[ERR] 未找到输入文件：{in_path}（pattern={args.glob_pat}）", file=sys.stderr)
        sys.exit(2)

    out_dir.mkdir(parents=True, exist_ok=True)
    report_rows: List[Dict] = []

    for fp in files:
        try:
            raw = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[WARN] 读取失败：{fp} -> {e}", file=sys.stderr)
            continue

        merged_text = raw
        merged_count = 0
        merged_examples: list[str] = []
        if args.merge_wraps:
            merged_text = merge_hard_wraps(raw)
            info = getattr(merge_hard_wraps, "last_stats", {})
            merged_count = int(info.get("merged_count", 0))
            merged_examples = list(info.get("examples", []))

        norm, stats = process_text(merged_text, args, cmap_cfg)
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
        }
        report_rows.append(row)

        if not args.dry_run:
            out_fp = ensure_out_path(out_dir, fp, ".norm.txt")
            try:
                out_fp.write_text(norm, encoding="utf-8")
            except Exception as e:
                print(f"[WARN] 写入失败：{out_fp} -> {e}", file=sys.stderr)

    write_reports(report_rows, out_dir)
    print(f"[DONE] 文件数：{len(report_rows)}；输出目录：{out_dir}")


if __name__ == "__main__":
    main()
