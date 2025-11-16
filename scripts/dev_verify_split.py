#!/usr/bin/env python3
"""轻量自检脚本：统计规范化文本的切句结果。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 计算项目根目录
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

HARD_PUNCT = set("。.!！?？…")


def count_hard_punct_ending(lines: list[str]) -> int:
    """统计以硬标点结尾的行数。"""
    count = 0
    for line in lines:
        stripped = line.strip()
        if stripped and stripped[-1] in HARD_PUNCT:
            count += 1
    return count


def count_unmatched(lines: list[str]) -> int:
    """统计包含 [unmatched] 标记的行数。"""
    count = 0
    for line in lines:
        if "[unmatched]" in line:
            count += 1
    return count


def analyze_file(norm_path: Path, align_path: Path | None = None, keep_last_path: Path | None = None) -> dict:
    """分析单个文件的切句统计。"""
    result = {
        "stem": norm_path.stem.replace(".norm", ""),
        "lines": 0,
        "avg_len": 0.0,
        "hard_punct_ending": 0,
        "hard_punct_ratio": 0.0,
        "unmatched": 0,
    }
    
    try:
        norm_lines = norm_path.read_text(encoding="utf-8-sig").strip().splitlines()
        norm_lines = [line.strip() for line in norm_lines if line.strip()]
        result["lines"] = len(norm_lines)
        if norm_lines:
            total_len = sum(len(line) for line in norm_lines)
            result["avg_len"] = total_len / len(norm_lines)
            result["hard_punct_ending"] = count_hard_punct_ending(norm_lines)
            result["hard_punct_ratio"] = result["hard_punct_ending"] / len(norm_lines) if norm_lines else 0.0
    except Exception as e:
        result["error"] = str(e)
        return result
    
    if align_path and align_path.exists():
        try:
            align_lines = align_path.read_text(encoding="utf-8-sig").strip().splitlines()
            align_lines = [line.strip() for line in align_lines if line.strip()]
            result["align_lines"] = len(align_lines)
        except Exception:
            pass
    
    if keep_last_path and keep_last_path.exists():
        try:
            keep_lines = keep_last_path.read_text(encoding="utf-8-sig").strip().splitlines()
            keep_lines = [line.strip() for line in keep_lines if line.strip()]
            result["unmatched"] = count_unmatched(keep_lines)
        except Exception:
            pass
    
    return result


def main() -> int:
    """主函数。"""
    if len(sys.argv) < 2:
        out_dir = ROOT_DIR / "out" / "norm"
    else:
        out_dir = Path(sys.argv[1]).expanduser().resolve()
    
    if not out_dir.exists():
        print(f"错误: 输出目录不存在: {out_dir}", file=sys.stderr)
        return 1
    
    norm_files = sorted(out_dir.glob("*.norm.txt"))
    if not norm_files:
        print(f"警告: 未找到 *.norm.txt 文件在 {out_dir}", file=sys.stderr)
        return 0
    
    results = []
    for norm_path in norm_files:
        stem = norm_path.stem.replace(".norm", "")
        align_path = norm_path.parent / f"{stem}.align.txt"
        keep_last_path = norm_path.parent / f"{stem}.keepLast.txt"
        
        result = analyze_file(norm_path, align_path if align_path.exists() else None, 
                             keep_last_path if keep_last_path.exists() else None)
        results.append(result)
        
        # 打印摘要
        print(
            f"stem={result['stem']} "
            f"lines={result['lines']} "
            f"hard={result['hard_punct_ending']} "
            f"soft={result['lines'] - result['hard_punct_ending']} "
            f"unmatched={result['unmatched']} "
            f"split_all_punct=True"
        )
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

