"""生成规范化、分句、参数、对齐报告。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 计算项目根目录
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from onepass import text_normalizer
    from onepass.asr_loader import load_words, Word
except ImportError as e:
    print(f"ERROR: 无法导入模块: {e}", file=sys.stderr)
    sys.exit(1)


def analyze_normalization(orig_path: Path, norm_path: Path) -> Dict[str, Any]:
    """分析规范化差异。"""
    if not orig_path.exists() or not norm_path.exists():
        return {"error": "文件不存在"}
    
    orig_text = orig_path.read_text(encoding="utf-8")
    norm_text = norm_path.read_text(encoding="utf-8")
    
    # 统计差异
    orig_newlines = orig_text.count("\n")
    orig_tabs = orig_text.count("\t")
    orig_fullwidth_space = orig_text.count("\u3000")
    
    norm_newlines = norm_text.count("\n")
    norm_tabs = norm_text.count("\t")
    norm_fullwidth_space = norm_text.count("\u3000")
    
    # 字符映射统计（启发式：长度变化）
    orig_len = len(orig_text)
    norm_len = len(norm_text)
    
    return {
        "removed_newlines": max(0, orig_newlines - norm_newlines),
        "removed_tabs": max(0, orig_tabs - norm_tabs),
        "removed_fullwidth_space": max(0, orig_fullwidth_space - norm_fullwidth_space),
        "length_change": norm_len - orig_len,
        "char_map_applied": orig_len != norm_len,
    }


def analyze_splitting(norm_path: Path, align_path: Optional[Path] = None) -> Dict[str, Any]:
    """分析分句结果。"""
    if not norm_path.exists():
        return {"error": "规范化文件不存在"}
    
    norm_text = norm_path.read_text(encoding="utf-8")
    
    # 使用当前分句器重新切分
    cfg = text_normalizer.TextNormConfig(
        split_all_punct=True,
        split_mode="punct+len",
        min_len=8,
        max_len=24,
        hard_max=32,
    )
    sentences = text_normalizer.split_sentences_with_rules(norm_text, cfg)
    
    # 统计
    hard_puncts = text_normalizer.HARD_PUNCT
    soft_puncts = text_normalizer.SOFT_PUNCT
    all_puncts = hard_puncts | soft_puncts
    
    hard_punct_count = 0
    soft_punct_count = 0
    multi_hard_punct_sentences = []
    
    for i, sent in enumerate(sentences):
        stripped = sent.strip()
        if not stripped:
            continue
        # 检查硬标点
        if stripped[-1] in hard_puncts:
            hard_punct_count += 1
        # 检查软标点
        if stripped[-1] in soft_puncts:
            soft_punct_count += 1
        # 检查段内多句号
        hard_in_sent = sum(1 for ch in stripped if ch in hard_puncts)
        if hard_in_sent > 1:
            multi_hard_punct_sentences.append({
                "index": i,
                "text": stripped[:50] + "..." if len(stripped) > 50 else stripped,
                "hard_count": hard_in_sent,
            })
    
    total_sentences = len([s for s in sentences if s.strip()])
    hard_punct_rate = (hard_punct_count / total_sentences * 100) if total_sentences > 0 else 0
    soft_punct_rate = (soft_punct_count / total_sentences * 100) if total_sentences > 0 else 0
    
    # 可疑片段（前5个）
    suspicious = multi_hard_punct_sentences[:5]
    
    return {
        "total_sentences": total_sentences,
        "hard_punct_count": hard_punct_count,
        "soft_punct_count": soft_punct_count,
        "hard_punct_rate": round(hard_punct_rate, 2),
        "soft_punct_rate": round(soft_punct_rate, 2),
        "multi_hard_punct_count": len(multi_hard_punct_sentences),
        "suspicious_samples": suspicious,
    }


def analyze_alignment(norm_path: Path, words_path: Optional[Path] = None) -> Dict[str, Any]:
    """分析对齐结果。"""
    if not words_path or not words_path.exists():
        return {"status": "missing_words_json", "message": "词级数据缺失，跳过此项"}
    
    try:
        words = load_words(words_path)
    except Exception as e:
        return {"status": "error", "message": f"加载词级 JSON 失败: {e}"}
    
    if not norm_path.exists():
        return {"status": "error", "message": "规范化文件不存在"}
    
    norm_text = norm_path.read_text(encoding="utf-8")
    cfg = text_normalizer.TextNormConfig(split_all_punct=True)
    sentences = text_normalizer.split_sentences_with_rules(norm_text, cfg)
    
    # 简单匹配统计（启发式）
    norm_lines = [s.strip() for s in sentences if s.strip()]
    total_words = len(words)
    total_lines = len(norm_lines)
    
    # 检查未匹配行（启发式：行末是否为分句标点）
    unmatched_samples = []
    all_puncts = text_normalizer.HARD_PUNCT | text_normalizer.SOFT_PUNCT
    
    for i, line in enumerate(norm_lines[:10]):  # 前10条
        if line and line[-1] not in all_puncts:
            unmatched_samples.append({
                "index": i,
                "text": line[:50] + "..." if len(line) > 50 else line,
            })
    
    return {
        "status": "ok",
        "total_words": total_words,
        "total_norm_lines": total_lines,
        "match_ratio": round(total_lines / total_words * 100, 2) if total_words > 0 else 0,
        "unmatched_samples": unmatched_samples[:10],
    }


def main() -> int:
    """主函数。"""
    parser = argparse.ArgumentParser(description="生成规范化、分句、参数、对齐报告")
    parser.add_argument("--in", dest="input_dir", default="materials", help="输入目录（默认 materials）")
    parser.add_argument("--out", dest="output_dir", default="out/reports", help="输出目录（默认 out/reports）")
    
    args = parser.parse_args()
    
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    norm_dir = ROOT_DIR / "out" / "norm"
    
    # 查找所有文本文件
    text_files = list(input_dir.glob("*.txt"))
    
    norm_reports = []
    split_reports = []
    align_reports = []
    
    for text_file in text_files:
        stem = text_file.stem
        norm_file = norm_dir / f"{stem}.norm.txt"
        align_file = norm_dir / f"{stem}.align.txt"
        words_file = input_dir / f"{stem}.words.json"
        if not words_file.exists():
            words_file = input_dir / f"{stem}.json"
        
        # 规范化报告
        norm_report = analyze_normalization(text_file, norm_file)
        norm_report["stem"] = stem
        norm_reports.append(norm_report)
        
        # 分句报告
        split_report = analyze_splitting(norm_file, align_file)
        split_report["stem"] = stem
        split_reports.append(split_report)
        
        # 对齐报告
        align_report = analyze_alignment(norm_file, words_file)
        align_report["stem"] = stem
        align_reports.append(align_report)
    
    # 生成 JSON 报告
    reports = {
        "normalization": norm_reports,
        "splitting": split_reports,
        "alignment": align_reports,
    }
    
    json_path = output_dir / "norm_report.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump({"normalization": norm_reports}, f, ensure_ascii=False, indent=2)
    print(f"规范化报告 JSON: {json_path}")
    
    json_path = output_dir / "split_report.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump({"splitting": split_reports}, f, ensure_ascii=False, indent=2)
    print(f"分句报告 JSON: {json_path}")
    
    json_path = output_dir / "align_report.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump({"alignment": align_reports}, f, ensure_ascii=False, indent=2)
    print(f"对齐报告 JSON: {json_path}")
    
    # 生成 Markdown 报告
    md_path = output_dir / "norm_report.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# 规范化报告\n\n")
        for report in norm_reports:
            f.write(f"## {report.get('stem', 'unknown')}\n\n")
            if "error" in report:
                f.write(f"错误: {report['error']}\n\n")
            else:
                f.write(f"- 移除换行: {report.get('removed_newlines', 0)}\n")
                f.write(f"- 移除制表符: {report.get('removed_tabs', 0)}\n")
                f.write(f"- 移除全角空格: {report.get('removed_fullwidth_space', 0)}\n")
                f.write(f"- 长度变化: {report.get('length_change', 0)}\n")
            f.write("\n")
    print(f"规范化报告 Markdown: {md_path}")
    
    md_path = output_dir / "split_report.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# 分句报告\n\n")
        for report in split_reports:
            f.write(f"## {report.get('stem', 'unknown')}\n\n")
            if "error" in report:
                f.write(f"错误: {report['error']}\n\n")
            else:
                f.write(f"- 总句数: {report.get('total_sentences', 0)}\n")
                f.write(f"- 硬标点命中率: {report.get('hard_punct_rate', 0)}%\n")
                f.write(f"- 软标点命中率: {report.get('soft_punct_rate', 0)}%\n")
                f.write(f"- 段内多句号计数: {report.get('multi_hard_punct_count', 0)}\n")
                if report.get("suspicious_samples"):
                    f.write("\n可疑片段:\n")
                    for sample in report["suspicious_samples"]:
                        f.write(f"- [{sample['index']}] {sample['text']} (硬标点数: {sample['hard_count']})\n")
            f.write("\n")
    print(f"分句报告 Markdown: {md_path}")
    
    md_path = output_dir / "align_report.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# 对齐报告\n\n")
        for report in align_reports:
            f.write(f"## {report.get('stem', 'unknown')}\n\n")
            status = report.get("status", "unknown")
            if status == "missing_words_json":
                f.write("词级数据缺失，跳过此项\n\n")
            elif status == "error":
                f.write(f"错误: {report.get('message', 'unknown')}\n\n")
            else:
                f.write(f"- 总词数: {report.get('total_words', 0)}\n")
                f.write(f"- 规范化行数: {report.get('total_norm_lines', 0)}\n")
                f.write(f"- 匹配率: {report.get('match_ratio', 0)}%\n")
                if report.get("unmatched_samples"):
                    f.write("\n未匹配样例:\n")
                    for sample in report["unmatched_samples"]:
                        f.write(f"- [{sample['index']}] {sample['text']}\n")
            f.write("\n")
    print(f"对齐报告 Markdown: {md_path}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

