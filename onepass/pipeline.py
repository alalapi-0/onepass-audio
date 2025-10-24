"""onepass.pipeline
用途: 串联 ASR 词级数据、重录检测、字幕生成与剪辑导出流程。
依赖: Python 标准库 json、dataclasses、pathlib；内部模块 ``onepass.*``。
示例: ``from onepass.pipeline import run_once``。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from .aggr import map_aggr
from .asr_loader import load_words
from .clean import remove_fillers
from .config import load_config
from .diffreport import write_diff_markdown
from .edl import build_edl, edl_to_json
from .markers import write_audition_markers
from .retake import find_retake_keeps
from .segment import to_segments
from .types import Paths, Stats, ensure_outdir
from .writers import write_plain, write_srt, write_vtt


def run_once(
    stem: str,
    paths: Paths,
    aggr: int = 50,
    config_path: Path | None = None,
    cfg_overrides: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """执行单次处理流程并返回输出信息。"""

    cfg = load_config(config_path)
    if cfg_overrides:
        for key, value in cfg_overrides.items():
            cfg[key] = value
    cfg = map_aggr(aggr, cfg)

    ensure_outdir(paths.outdir)

    words = load_words(paths.json)
    original_text = paths.original.read_text("utf-8")

    keeps, retake_cuts, diff_items = find_retake_keeps(words, original_text, cfg)
    words_for_subs = remove_fillers(words, cfg, strict=cfg.get("filler_strict", False))
    segs = to_segments(words_for_subs, cfg)
    edl_actions = build_edl(words, retake_cuts, keeps, cfg)

    stats = Stats()
    stats.total_words = len(words)
    stats.filler_removed = max(0, len(words) - len(words_for_subs))
    stats.retake_cuts = sum(1 for action in edl_actions if action.type == "cut")
    stats.long_pauses = sum(1 for action in edl_actions if action.type == "tighten_pause")
    stats.duplicated_sentences = sum(len(item.get("deleted", [])) for item in diff_items)
    for action in edl_actions:
        duration_ms = int(round(max(0.0, (action.end - action.start) * 1000)))
        if action.type == "cut":
            stats.shortened_ms += duration_ms
        elif action.type == "tighten_pause":
            target = action.target_ms or 0
            stats.shortened_ms += max(0, duration_ms - target)

    srt_path = paths.outdir / f"{stem}.keepLast.clean.srt"
    vtt_path = paths.outdir / f"{stem}.keepLast.clean.vtt"
    txt_path = paths.outdir / f"{stem}.keepLast.clean.txt"
    edl_path = paths.outdir / f"{stem}.keepLast.edl.json"
    marker_path = paths.outdir / f"{stem}.keepLast.audition_markers.csv"
    log_path = paths.outdir / f"{stem}.keepLast.log"
    diff_path = write_diff_markdown(stem, diff_items, paths.outdir)

    write_srt(segs, srt_path)
    write_vtt(segs, vtt_path)
    write_plain(segs, txt_path)
    edl_payload = edl_to_json(edl_actions, cfg)
    edl_path.write_text(json.dumps(edl_payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    write_audition_markers(edl_actions, marker_path)

    log_lines = [
        f"stem: {stem}",
        f"total_words: {stats.total_words}",
        f"filler_removed: {stats.filler_removed}",
        f"retake_cuts: {stats.retake_cuts}",
        f"long_pauses: {stats.long_pauses}",
        f"shortened_ms: {stats.shortened_ms}",
        f"keeps: {len(keeps)}",
        f"duplicated_sentences: {stats.duplicated_sentences}",
        (
            "overlap_keep = "
            f"{cfg.get('overlap_keep', 'last')}, align_strategy = {cfg.get('align_strategy', 'hybrid')}, "
            f"align_min_sim = {cfg.get('align_min_sim', 0.84)}"
        ),
    ]
    log_path.write_text("\n".join(log_lines) + "\n", "utf-8")

    outputs = {
        "srt": str(srt_path),
        "vtt": str(vtt_path),
        "txt": str(txt_path),
        "edl": str(edl_path),
        "markers": str(marker_path),
        "log": str(log_path),
        "diff": str(diff_path),
    }
    return {"outputs": outputs, "stats": asdict(stats), "keeps": [asdict(k) for k in keeps]}


if __name__ == "__main__":
    demo_json = Path("examples/demo.json")
    demo_txt = Path("examples/demo.txt")
    if demo_json.exists() and demo_txt.exists():
        try:
            paths = Paths(json=demo_json, original=demo_txt, outdir=Path("out"))
            result = run_once("demo", paths)
        except Exception as exc:  # pragma: no cover - demonstration only
            print(f"演示失败: {exc}")
        else:
            print("演示成功")
            for name, value in result["outputs"].items():
                print(f"  {name}: {value}")
    else:
        print("演示失败: 缺少 examples/demo.json 或 examples/demo.txt")
