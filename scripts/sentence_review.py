"""句子级审阅模式的独立调试入口。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from onepass.asr_loader import load_words
from onepass.batch_utils import stem_from_words_json
from onepass.retake_keep_last import (
    compute_sentence_review,
    export_sentence_edl_json,
    export_sentence_markers,
    export_sentence_srt,
    export_sentence_txt,
)
from onepass.sent_align import (
    LOW_CONF,
    MAX_DUP_GAP_SEC,
    MERGE_ADJ_GAP_SEC,
    MIN_SENT_CHARS,
)


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="句子级审阅模式调试脚本")
    parser.add_argument("--words-json", required=True, help="词级 JSON 文件路径")
    parser.add_argument("--text", required=True, help="原文 TXT 路径")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--sentence-strict", action="store_true", help="启用句子级审阅匹配")
    parser.add_argument(
        "--review-only",
        action="store_true",
        help="仅打点不裁剪，EDL 生成整段 keep",
    )
    parser.add_argument(
        "--min-sent-chars",
        type=int,
        default=MIN_SENT_CHARS,
        help=f"重复去重的句长下限（默认 {MIN_SENT_CHARS}）",
    )
    parser.add_argument(
        "--max-dup-gap-sec",
        type=float,
        default=None,
        help=f"判定重录的最大近邻间隔（默认 {MAX_DUP_GAP_SEC:g} 秒）",
    )
    parser.add_argument(
        "--merge-adj-gap-sec",
        type=float,
        default=None,
        help=f"命中合并的最大间隙（默认 {MERGE_ADJ_GAP_SEC:g} 秒）",
    )
    parser.add_argument(
        "--low-conf",
        type=float,
        default=None,
        help=f"低置信判定阈值（默认 {LOW_CONF:.2f}）",
    )
    parser.add_argument(
        "--low-conf-threshold",
        dest="low_conf",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--source-audio", help="可选：EDL 中记录的源音频相对路径")
    parser.add_argument("--samplerate", type=int, help="可选：EDL 建议采样率")
    parser.add_argument("--channels", type=int, help="可选：EDL 建议声道数")
    return parser


def main(argv: list[str] | None = None) -> int:
    """脚本入口，执行句子级审阅导出。"""

    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.sentence_strict:
        print("请使用 --sentence-strict 开启句子级审阅模式。", file=sys.stderr)
        return 2
    if args.review_only and not args.sentence_strict:
        print("--review-only 仅能在启用 --sentence-strict 时使用。", file=sys.stderr)
        return 2
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    words_path = Path(args.words_json).expanduser()
    text_path = Path(args.text).expanduser()
    try:
        doc = load_words(words_path)
    except Exception as exc:  # pragma: no cover - I/O 依赖于外部环境
        print(f"读取词级 JSON 失败: {exc}", file=sys.stderr)
        return 1
    max_dup_gap = args.max_dup_gap_sec if args.max_dup_gap_sec is not None else MAX_DUP_GAP_SEC
    merge_gap = args.merge_adj_gap_sec if args.merge_adj_gap_sec is not None else MERGE_ADJ_GAP_SEC
    low_conf = args.low_conf if args.low_conf is not None else LOW_CONF
    try:
        result = compute_sentence_review(
            list(doc),
            text_path,
            min_sent_chars=args.min_sent_chars,
            max_dup_gap_sec=max_dup_gap,
            merge_gap_sec=merge_gap,
            low_conf=low_conf,
        )
    except Exception as exc:  # pragma: no cover - 依赖外部文件
        print(f"句子级审阅匹配失败: {exc}", file=sys.stderr)
        return 1
    stem = stem_from_words_json(words_path)
    srt_path = out_dir / f"{stem}.sentence.keep.srt"
    txt_path = out_dir / f"{stem}.sentence.keep.txt"
    markers_path = out_dir / f"{stem}.sentence.audition_markers.csv"
    edl_path = out_dir / f"{stem}.sentence.edl.json"
    export_sentence_srt(result.hits, srt_path)
    export_sentence_txt(result.hits, txt_path)
    export_sentence_markers(result.hits, result.review_points, markers_path)
    source_audio_name = Path(args.source_audio).name if args.source_audio else None
    export_sentence_edl_json(
        result.edl_keep_segments,
        result.audio_start,
        result.audio_end,
        edl_path,
        review_only=args.review_only,
        source_audio_rel=source_audio_name,
        samplerate=args.samplerate,
        channels=args.channels,
        stem=stem,
        source_samplerate=args.samplerate,
    )
    stats = dict(result.stats)
    matched = stats.get("matched_sentences", 0)
    total = stats.get("total_sentences", 0)
    low_conf = stats.get("low_conf_sentences", 0)
    unmatched = stats.get("unmatched_sentences", 0)
    longest_span = stats.get("longest_keep_span", 0.0)
    print("句子级审阅完成。")
    print(f"总句数: {total}，命中: {matched}，低置信: {low_conf}，未匹配: {unmatched}")
    print(f"合并后 keep 段数量: {stats.get('keep_span_count', 0)}，最长 keep 段时长: {longest_span:.2f}s")
    review_count = len(result.review_points)
    print(f"审阅标记数量: {review_count}，输出目录: {out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
