"""词级 JSON + 原文 TXT → 保留最后一遍导出脚本。

用法示例：
    python scripts/retake_keep_last.py \
      --words-json materials/example/demo.words.json \
      --text materials/example/demo.txt \
      --out out
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from onepass.asr_loader import load_words
from onepass.retake_keep_last import (
    compute_retake_keep_last,
    export_audition_markers,
    export_edl_json,
    export_srt,
    export_txt,
)


def _derive_stem(words_path: Path, text_path: Path) -> str:
    """根据词级 JSON 与原文 TXT 推导输出前缀。"""

    # 默认使用词级 JSON 的文件名前缀
    stem = words_path.stem
    if stem.endswith(".words"):
        # 去掉常见的 .words 后缀，得到更短的名称
        stem = stem[:-6]
    if not stem:
        # 若 JSON 名称为空则退回 TXT 的前缀
        stem = text_path.stem
    return stem or "output"


def main(argv: list[str] | None = None) -> int:
    """命令行入口，执行保留最后一遍导出流程。"""

    parser = argparse.ArgumentParser(description="基于词级 JSON 与原文 TXT 导出保留最后一遍的字幕/文本/EDL")
    # 必填：词级 JSON 与原文 TXT
    parser.add_argument("--words-json", required=True, help="词级 ASR JSON 路径")
    parser.add_argument("--text", required=True, help="原文 TXT 路径 (一行一段)")
    # 输出目录默认使用 out
    parser.add_argument("--out", default="out", help="输出目录 (默认 out)")
    # 额外参数用于在 EDL 中记录音频元信息
    parser.add_argument("--source-audio", help="可选：EDL 中填充的源音频相对路径")
    parser.add_argument("--samplerate", type=int, help="可选：EDL 建议采样率")
    parser.add_argument("--channels", type=int, help="可选：EDL 建议声道数")
    args = parser.parse_args(argv)

    words_json = Path(args.words_json)
    text_path = Path(args.text)
    out_dir = Path(args.out)

    try:
        # 通过统一适配层读取词级 JSON
        doc = load_words(words_json)
    except Exception as exc:  # pragma: no cover - CLI 交互路径
        print(f"加载词级 JSON 失败: {exc}", file=sys.stderr)
        return 1

    try:
        # 调用核心策略，传入词序列与原文路径
        result = compute_retake_keep_last(list(doc), text_path)
    except Exception as exc:  # pragma: no cover - CLI 交互路径
        print(f"保留最后一遍计算失败: {exc}", file=sys.stderr)
        return 1

    # 统一输出文件名前缀
    stem = _derive_stem(words_json, text_path)
    # 确保输出目录存在
    out_dir.mkdir(parents=True, exist_ok=True)

    srt_path = out_dir / f"{stem}.keepLast.srt"
    txt_path = out_dir / f"{stem}.keepLast.txt"
    markers_path = out_dir / f"{stem}.audition_markers.csv"
    edl_path = out_dir / f"{stem}.keepLast.edl.json"

    # 导出四类产物
    export_srt(result.keeps, srt_path)
    export_txt(result.keeps, txt_path)
    export_audition_markers(result.keeps, markers_path)
    export_edl_json(
        result.edl_keep_segments,
        args.source_audio,
        edl_path,
        samplerate=args.samplerate,
        channels=args.channels,
    )

    stats = result.stats
    # 打印统计摘要，便于快速了解匹配质量
    print(
        "总词数 {total_words}，匹配行数 {matched_lines}，严格匹配 {strict_matches}，"
        "LCS 回退 {fallback_matches}，未匹配 {unmatched_lines}".format(**stats)
    )
    # 明确输出文件路径
    print(f"已生成: {srt_path}")
    print(f"已生成: {txt_path}")
    print(f"已生成: {markers_path}")
    print(f"已生成: {edl_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
