"""OnePass Audio 统一命令行入口。"""
from __future__ import annotations

import argparse  # 解析命令行参数
import csv  # 写入规范化报表
import json  # 生成批处理 JSON 报告
import logging  # 控制台日志输出
import shlex  # 构建可复制的命令示例
import sys  # 访问解释器信息
import time  # 统计耗时
from concurrent.futures import ThreadPoolExecutor, as_completed  # 批处理并发执行
from pathlib import Path  # 跨平台路径处理
from typing import Optional, Sequence, Tuple

# 计算项目根目录，确保脚本可直接运行
ROOT_DIR = Path(__file__).resolve().parents[1]  # 项目根目录
if str(ROOT_DIR) not in sys.path:  # 若根目录未在 sys.path 中则插入
    sys.path.insert(0, str(ROOT_DIR))

from onepass.asr_loader import load_words  # 载入词级 JSON
from onepass.batch_utils import (  # 批处理通用工具
    find_text_for_stem,
    iter_files,
    safe_rel,
    stem_from_words_json,
    write_json,
)
from onepass.edl_renderer import (  # 音频渲染依赖
    load_edl,
    normalize_segments,
    probe_duration,
    render_audio,
    resolve_source_audio,
)
from onepass.retake_keep_last import (  # 保留最后一遍导出函数
    MAX_DUP_GAP_SEC as LINE_MAX_DUP_GAP_SEC,
    MAX_WINDOW_SEC,
    MIN_SENT_CHARS,
    MERGE_GAP_SEC,
    MIN_SEGMENT_SEC,
    PAD_AFTER,
    PAD_BEFORE,
    PAUSE_GAP_SEC,
    PAUSE_SNAP_LIMIT,
    compute_retake_keep_last,
    compute_sentence_review,
    export_audition_markers,
    export_edl_json,
    export_srt,
    export_txt,
    export_sentence_edl_json,
    export_sentence_markers,
    export_sentence_srt,
    export_sentence_txt,
)
from onepass.silence_probe import probe_silence_ffmpeg
from onepass.text_norm import (  # 规范化工具
    load_char_map,
    normalize_pipeline,
    prepare_alignment_text,
    run_opencc_if_available,
    scan_suspects,
)
from onepass.sent_align import (
    LOW_CONF as SENT_LOW_CONF,
    MAX_DUP_GAP_SEC as SENT_MAX_DUP_GAP_SEC,
    MERGE_ADJ_GAP_SEC,
)
from onepass.logging_utils import default_log_dir, setup_logger


DEFAULT_NORMALIZE_REPORT = ROOT_DIR / "out" / "normalize_report.csv"  # 规范化报表路径
DEFAULT_CHAR_MAP = ROOT_DIR / "config" / "default_char_map.json"  # 默认字符映射
LOGGER = logging.getLogger("onepass.cli")  # 模块级日志器


def _configure_logging() -> None:
    """初始化控制台日志格式。"""

    global LOGGER
    LOGGER = setup_logger("onepass.cli", default_log_dir())  # 使用统一滚动日志配置


def _build_cli_example(subcommand: str, parts: Sequence[str]) -> str:
    """构建便于复制的命令行示例。"""

    args = [sys.executable, str(Path(__file__).resolve()), subcommand, *parts]  # 拼接完整命令
    return shlex.join(args)  # 返回 shell 风格字符串


def _append_normalize_report(rows: list[dict]) -> None:
    """在 out/normalize_report.csv 末尾追加记录。"""

    if not rows:
        return
    DEFAULT_NORMALIZE_REPORT.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    file_exists = DEFAULT_NORMALIZE_REPORT.exists()  # 判断是否已存在
    with DEFAULT_NORMALIZE_REPORT.open("a", newline="", encoding="utf-8") as fh:  # 追加模式写入
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))  # 使用首行字段
        if not file_exists:  # 首次写入添加表头
            writer.writeheader()
        for row in rows:  # 写入每一行
            writer.writerow(row)


def _append_debug_rows(csv_path: Path, rows: Sequence[dict], mode: str) -> None:
    """将段调整的调试信息追加到 CSV 文件。"""

    if not rows:
        return
    csv_path = csv_path.expanduser().resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "item",
        "mode",
        "index",
        "orig_start",
        "orig_end",
        "snap_start",
        "snap_end",
        "pad_start",
        "pad_end",
        "final_start",
        "final_end",
        "snap_start_used",
        "snap_end_used",
        "merged_into",
        "dropped",
        "notes",
    ]
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            payload = dict(row)
            payload.setdefault("item", "")
            payload["mode"] = mode
            for key in fieldnames:
                if key not in payload:
                    payload[key] = ""
            writer.writerow(payload)


def _ensure_out_dir(out_dir: Path) -> Path:
    """校验输出目录必须位于 out/ 下。"""

    resolved = out_dir.expanduser().resolve()  # 解析为绝对路径
    out_root = (ROOT_DIR / "out").resolve()  # out 根目录
    try:
        resolved.relative_to(out_root)  # 确认在 out/ 内
    except ValueError as exc:  # 不在 out/ 范围内时报错
        raise ValueError(f"输出目录必须位于 {out_root} 内。当前: {resolved}") from exc
    resolved.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    return resolved


def _process_single_text(
    path: Path,
    base_dir: Path,
    out_dir: Path,
    cmap: dict,
    opencc_mode: str,
    dry_run: bool,
    emit_align: bool,
) -> dict:
    """对单个文本执行规范化处理并返回报表行。"""

    LOGGER.debug("Normalize %s", path)  # 调试输出当前文件
    try:
        raw_text = path.read_text(encoding="utf-8")  # 按 UTF-8 读取原文
        decode_note = ""  # 初始化编码提示
    except UnicodeDecodeError:
        raw_text = path.read_text(encoding="utf-8", errors="replace")  # 若解码失败则替换异常字符
        decode_note = "原文包含无法解码的字符，已用替换符号保留。"  # 记录提示
    except OSError as exc:
        return {
            "file": str(path),
            "orig_len": 0,
            "norm_len": 0,
            "deleted_count": 0,
            "mapped_count": 0,
            "width_normalized_count": 0,
            "space_normalized_count": 0,
            "opencc_mode": opencc_mode,
            "opencc_applied": "false",
            "suspects_found": "false",
            "suspects_examples": "",
            "status": "failed",
            "message": f"读取失败: {exc}",
        }

    try:
        normalized_text, stats = normalize_pipeline(
            raw_text,  # 原始文本
            cmap,  # 字符映射
            use_width=bool(cmap.get("normalize_width", False)),  # 是否执行宽度归一
            use_space=bool(cmap.get("normalize_space", False)),  # 是否执行空白归一
            preserve_cjk_punct=bool(cmap.get("preserve_cjk_punct", False)),  # 是否保留中日韩标点
        )
    except Exception as exc:
        LOGGER.exception("规范化流程失败: %s", path)
        return {
            "file": str(path),
            "orig_len": len(raw_text),
            "norm_len": 0,
            "deleted_count": 0,
            "mapped_count": 0,
            "width_normalized_count": 0,
            "space_normalized_count": 0,
            "opencc_mode": opencc_mode,
            "opencc_applied": "false",
            "suspects_found": "false",
            "suspects_examples": "",
            "status": "failed",
            "message": f"规范化失败: {exc}",
        }

    converted_text, opencc_applied = run_opencc_if_available(normalized_text, opencc_mode)  # 运行 OpenCC
    suspects = scan_suspects(converted_text)  # 扫描可疑字符
    suspects_found = any(value.get("count", 0) for value in suspects.values())  # 是否发现异常
    suspects_examples = "; ".join(  # 汇总示例
        f"{key}:{','.join(str(item) for item in info.get('examples', []))}"
        for key, info in suspects.items()
        if info.get("count", 0)
    )

    relative = path.relative_to(base_dir) if path.is_relative_to(base_dir) else Path(path.name)  # 计算相对路径
    out_path = out_dir / relative.parent / f"{relative.stem}.norm.txt"  # 生成输出路径

    message_parts = []  # 准备提示信息
    if decode_note:
        message_parts.append(decode_note)  # 追加编码提示
    if opencc_mode != "none" and not opencc_applied:
        message_parts.append("OpenCC 未安装或执行失败，已跳过繁简转换。")  # 提醒 OpenCC 状态
    if not message_parts:
        message_parts.append("处理成功。")  # 默认成功提示

    align_written = False
    align_path: Path | None = None

    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)  # 创建输出目录
        payload = converted_text if converted_text.endswith("\n") else converted_text + "\n"  # 确保换行
        try:
            out_path.write_text(payload, encoding="utf-8")  # 写入规范化文本
        except OSError as exc:
            return {
                "file": str(path),
                "orig_len": len(raw_text),
                "norm_len": len(converted_text),
                "deleted_count": stats.get("deleted_count", 0),
                "mapped_count": stats.get("mapped_count", 0),
                "width_normalized_count": stats.get("width_normalized_count", 0),
                "space_normalized_count": stats.get("space_normalized_count", 0),
                "opencc_mode": opencc_mode,
                "opencc_applied": str(opencc_applied).lower(),
                "suspects_found": str(bool(suspects_found)).lower(),
                "suspects_examples": suspects_examples,
                "status": "failed",
                "message": f"写入失败: {exc}",
            }

        if emit_align:
            align_payload = prepare_alignment_text(payload)
            align_path = out_dir / relative.parent / f"{relative.stem}.align.txt"
            try:
                align_path.write_text(align_payload, encoding="utf-8")
                align_written = True
            except OSError as exc:
                LOGGER.warning("写入对齐文本失败: %s", exc)

    return {
        "file": str(path),
        "orig_len": len(raw_text),
        "norm_len": len(converted_text),
        "deleted_count": stats.get("deleted_count", 0),
        "mapped_count": stats.get("mapped_count", 0),
        "width_normalized_count": stats.get("width_normalized_count", 0),
        "space_normalized_count": stats.get("space_normalized_count", 0),
        "opencc_mode": opencc_mode,
        "opencc_applied": str(opencc_applied).lower(),
        "suspects_found": str(bool(suspects_found)).lower(),
        "suspects_examples": suspects_examples,
        "align_written": str(align_written).lower(),
        "align_path": str(align_path) if align_written and align_path else "",
        "status": "ok",
        "message": "；".join(message_parts),
    }


def run_prep_norm(
    input_path: Path,
    output_dir: Path,
    char_map_path: Path,
    opencc_mode: str,
    glob_pattern: str,
    dry_run: bool,
    emit_align: bool,
) -> dict:
    """执行规范化批处理并返回统计结果。"""

    if opencc_mode not in {"none", "t2s", "s2t"}:  # 校验 opencc 取值
        raise ValueError("--opencc 仅支持 none/t2s/s2t。")
    cmap = load_char_map(char_map_path)  # 加载字符映射
    out_dir = _ensure_out_dir(output_dir)  # 校验并创建输出目录
    input_path = input_path.expanduser().resolve()  # 解析输入路径
    if input_path.is_file():  # 单文件模式
        files = [input_path]
        base_dir = input_path.parent
    elif input_path.is_dir():  # 目录模式
        files = iter_files(input_path, [glob_pattern])  # 递归匹配
        base_dir = input_path
    else:  # 输入不存在
        raise FileNotFoundError(f"输入路径不存在: {input_path}")

    if not files:  # 无待处理文件
        return {"items": [], "summary": {"total": 0, "ok": 0, "failed": 0, "elapsed_seconds": 0.0}}

    rows: list[dict] = []  # 收集报表行
    failed = 0  # 统计失败数量
    start = time.perf_counter()  # 记录起始时间
    for path in files:  # 遍历每个文件
        row = _process_single_text(
            path,
            base_dir,
            out_dir,
            cmap,
            opencc_mode,
            dry_run,
            emit_align,
        )  # 处理单个文件
        rows.append(row)
        if row.get("status") != "ok":  # 判断成功与否
            failed += 1
            LOGGER.warning("[failed] %s %s", path, row.get("message"))  # 打印失败信息
        else:
            LOGGER.info("[ok] %s", path)  # 打印成功信息
    elapsed = time.perf_counter() - start  # 计算耗时
    if rows:
        _append_normalize_report(rows)  # 写入报表
    summary = {"total": len(rows), "ok": len(rows) - failed, "failed": failed, "elapsed_seconds": elapsed}  # 汇总
    if rows:
        summary["aggregated_stats"] = {
            "orig_len": sum(int(row.get("orig_len", 0)) for row in rows if row.get("status") == "ok"),
            "norm_len": sum(int(row.get("norm_len", 0)) for row in rows if row.get("status") == "ok"),
            "deleted_count": sum(int(row.get("deleted_count", 0)) for row in rows if row.get("status") == "ok"),
            "mapped_count": sum(int(row.get("mapped_count", 0)) for row in rows if row.get("status") == "ok"),
        }
    return {"items": rows, "summary": summary}


def handle_prep_norm(args: argparse.Namespace) -> int:
    """处理 prep-norm 子命令。"""

    cmd = _build_cli_example(
        "prep-norm",
        [
            "--in",
            str(Path(args.input)),
            "--out",
            str(Path(args.output)),
            "--char-map",
            str(Path(args.char_map)),
            "--opencc",
            args.opencc,
            "--glob",
            args.glob,
        ]
        + (["--emit-align"] if args.emit_align else [])
        + (["--dry-run"] if args.dry_run else []),
    )
    LOGGER.info("开始规范化任务: 输入=%s 输出=%s", args.input, args.output)
    LOGGER.info("等价命令: %s", cmd)
    try:
        result = run_prep_norm(
            Path(args.input),
            Path(args.output),
            Path(args.char_map),
            args.opencc,
            args.glob,
            args.dry_run,
            args.emit_align,
        )
    except Exception as exc:
        LOGGER.exception("处理 prep-norm 失败")
        print(f"处理失败: {exc}", file=sys.stderr)
        return 1
    summary = result["summary"]
    LOGGER.info(
        "完成规范化 %s 个，成功 %s，失败 %s，耗时 %.2fs",
        summary.get("total", 0),
        summary.get("ok", 0),
        summary.get("failed", 0),
        summary.get("elapsed_seconds", 0.0),
    )
    if summary.get("failed"):
        LOGGER.warning("存在 %s 个失败条目，已写入报表以便排查。", summary.get("failed"))
    return 0


def _export_retake_outputs(
    result,
    stem: str,
    out_dir: Path,
    source_audio: Optional[str],
    samplerate: Optional[int],
    channels: Optional[int],
    *,
    sentence_mode: bool,
    review_only: bool,
) -> dict:
    """根据处理结果导出字幕、标记与 EDL。"""

    out_dir.mkdir(parents=True, exist_ok=True)
    if sentence_mode:
        srt_path = out_dir / f"{stem}.sentence.keep.srt"
        txt_path = out_dir / f"{stem}.sentence.keep.txt"
        markers_path = out_dir / f"{stem}.sentence.audition_markers.csv"
        edl_path = out_dir / f"{stem}.sentence.edl.json"
        export_sentence_srt(result.hits, srt_path)
        export_sentence_txt(result.hits, txt_path)
        export_sentence_markers(result.hits, result.review_points, markers_path)
        export_sentence_edl_json(
            result.edl_keep_segments,
            result.audio_start,
            result.audio_end,
            edl_path,
            review_only=review_only,
            source_audio_rel=source_audio,
            samplerate=samplerate,
            channels=channels,
        )
    else:
        srt_path = out_dir / f"{stem}.keepLast.srt"
        txt_path = out_dir / f"{stem}.keepLast.txt"
        markers_path = out_dir / f"{stem}.audition_markers.csv"
        edl_path = out_dir / f"{stem}.keepLast.edl.json"
        export_srt(result.keeps, srt_path)
        export_txt(result.keeps, txt_path)
        export_audition_markers(result.keeps, markers_path)
        export_edl_json(
            result.edl_keep_segments,
            source_audio,
            edl_path,
            samplerate=samplerate,
            channels=channels,
        )
    return {
        "srt": srt_path,
        "txt": txt_path,
        "markers": markers_path,
        "edl": edl_path,
    }


def _process_retake_item(
    words_path: Path,
    text_path: Path,
    out_dir: Path,
    source_audio: Optional[str],
    samplerate: Optional[int],
    channels: Optional[int],
    materials_base: Optional[Path],
    out_base: Path,
    min_sent_chars: int,
    line_max_dup_gap_sec: float,
    sentence_max_dup_gap_sec: float,
    max_window_sec: float,
    sentence_strict: bool,
    review_only: bool,
    merge_adj_gap_sec: float,
    low_conf: float,
    *,
    pause_align: bool,
    pause_gap_sec: float,
    pause_snap_limit: float,
    pad_before: float,
    pad_after: float,
    min_segment_sec: float,
    merge_gap_sec: float,
    silence_probe_enabled: bool,
    noise_db: int,
    silence_min_d: float,
    overcut_guard: bool,
    overcut_mode: str,
    overcut_threshold: float,
    debug_csv: Path | None,
) -> Tuple[str, dict]:
    """处理单个词级 JSON + 文本的组合。"""

    stem = stem_from_words_json(words_path)  # 解析输出前缀
    try:
        doc = load_words(words_path)  # 读取词级 JSON
        words = list(doc)
        audio_path: Path | None = None
        if source_audio:
            candidate = Path(source_audio)
            if not candidate.is_absolute():
                candidate = (words_path.parent / candidate).resolve()
            audio_path = candidate
        silence_ranges: list[tuple[float, float]] | None = None
        if pause_align and silence_probe_enabled and audio_path is not None:
            silence_ranges = probe_silence_ffmpeg(audio_path, noise_db=noise_db, min_d=silence_min_d)
        effective_silence = silence_ranges if pause_align else None

        current_min_sent = min_sent_chars
        current_line_gap = line_max_dup_gap_sec
        current_sentence_gap = sentence_max_dup_gap_sec
        current_pause_gap = pause_gap_sec

        def _run_compute(
            min_sent: int,
            line_gap: float,
            sent_gap: float,
            pause_gap: float,
        ):
            if sentence_strict:
                return compute_sentence_review(
                    words,
                    text_path,
                    min_sent_chars=min_sent,
                    max_dup_gap_sec=sent_gap,
                    merge_gap_sec=merge_adj_gap_sec,
                    low_conf=low_conf,
                    pad_before=pad_before,
                    pad_after=pad_after,
                    pause_align=pause_align,
                    pause_gap_sec=pause_gap,
                    pause_snap_limit=pause_snap_limit,
                    min_segment_sec=min_segment_sec,
                    segment_merge_gap_sec=merge_gap_sec,
                    silence_ranges=effective_silence,
                    audio_path=audio_path,
                    debug_label=stem,
                )
            return compute_retake_keep_last(
                words,
                text_path,
                min_sent_chars=min_sent,
                max_dup_gap_sec=line_gap,
                max_window_sec=max_window_sec,
                pad_before=pad_before,
                pad_after=pad_after,
                pause_align=pause_align,
                pause_gap_sec=pause_gap,
                pause_snap_limit=pause_snap_limit,
                min_segment_sec=min_segment_sec,
                merge_gap_sec=merge_gap_sec,
                silence_ranges=effective_silence,
                audio_path=audio_path,
                debug_label=stem,
            )

        result = _run_compute(
            current_min_sent,
            current_line_gap,
            current_sentence_gap,
            current_pause_gap,
        )
        stats = dict(result.stats)
        cut_ratio = float(stats.get("cut_ratio", 0.0))
        action = "none"
        if overcut_guard and stats.get("audio_duration", 0.0) > 0 and cut_ratio > overcut_threshold:
            if overcut_mode == "auto":
                action = "auto"
            elif overcut_mode == "abort":
                action = "abort"
            else:
                if not sys.stdin.isatty():
                    LOGGER.warning(
                        "剪切比例 %.2f 超过阈值 %.2f，非交互环境自动选择 auto。",
                        cut_ratio,
                        overcut_threshold,
                    )
                    action = "auto"
                else:
                    prompt = (
                        f"剪切比例 {cut_ratio:.2%} 超过阈值 {overcut_threshold:.2%}，"
                        "选择 auto/continue/abort: "
                    )
                    while True:
                        choice = input(prompt).strip().lower() or "auto"
                        if choice in {"auto", "continue", "abort"}:
                            action = choice
                            break
                        print("请输入 auto、continue 或 abort。")
            if action == "auto":
                LOGGER.warning(
                    "触发过裁剪保护，自动调整参数后重算 (min_sent+4, max_dup_gap=15, pause_gap=0.55)。"
                )
                current_min_sent = min_sent_chars + 4
                current_line_gap = 15.0
                current_sentence_gap = 15.0
                current_pause_gap = 0.55
                result = _run_compute(
                    current_min_sent,
                    current_line_gap,
                    current_sentence_gap,
                    current_pause_gap,
                )
                stats = dict(result.stats)
                cut_ratio = float(stats.get("cut_ratio", 0.0))
            elif action == "abort":
                raise RuntimeError(
                    f"剪切比例 {cut_ratio:.2%} 超过阈值 {overcut_threshold:.2%}，已根据设置中止。"
                )
        stats["overcut_guard_action"] = action
        stats["review_only"] = bool(review_only)
        stats["sentence_strict"] = bool(sentence_strict)
        stats["pause_gap_final"] = current_pause_gap

        if sentence_strict:
            outputs = _export_retake_outputs(
                result,
                stem,
                out_dir,
                source_audio,
                samplerate,
                channels,
                sentence_mode=True,
                review_only=review_only,
            )
        else:
            outputs = _export_retake_outputs(
                result,
                stem,
                out_dir,
                source_audio,
                samplerate,
                channels,
                sentence_mode=False,
                review_only=False,
            )

        LOGGER.info(
            "停顿吸附=%s 吸附次数=%s 自动合并=%s 丢弃碎片=%s 剪切比例=%.3f",
            stats.get("pause_used"),
            stats.get("pause_snaps", 0),
            stats.get("auto_merged", 0),
            stats.get("too_short_dropped", 0),
            cut_ratio,
        )

        if debug_csv is not None and result.debug_rows:
            mode = "sentence" if sentence_strict else "line"
            _append_debug_rows(debug_csv, result.debug_rows, mode)

        stats["text_variant"] = text_path.name  # 记录使用的文本文件
        item = {
            "stem": stem,
            "words_json": safe_rel(materials_base or words_path.parent, words_path),
            "text": safe_rel(materials_base or text_path.parent, text_path),
            "outputs": {key: safe_rel(out_base, value) for key, value in outputs.items()},
            "stats": stats,
            "status": "ok",
            "message": "处理成功",
        }
    except Exception as exc:
        LOGGER.exception("保留最后一遍处理失败: %s", words_path)
        item = {
            "stem": stem,
            "words_json": safe_rel(materials_base or words_path.parent, words_path),
            "text": safe_rel(materials_base or text_path.parent, text_path),
            "outputs": {},
            "stats": {},
            "status": "failed",
            "message": str(exc),
        }
    return stem, item  # 返回 stem 及结果条目


def _run_retake_batch(
    materials_dir: Path,
    out_dir: Path,
    glob_words: str,
    text_patterns: list[str],
    workers: Optional[int],
    min_sent_chars: int,
    line_max_dup_gap_sec: float,
    sentence_max_dup_gap_sec: float,
    max_window_sec: float,
    sentence_strict: bool,
    review_only: bool,
    merge_adj_gap_sec: float,
    low_conf: float,
    pause_align: bool,
    pause_gap_sec: float,
    pause_snap_limit: float,
    pad_before: float,
    pad_after: float,
    min_segment_sec: float,
    merge_gap_sec: float,
    silence_probe_enabled: bool,
    noise_db: int,
    silence_min_d: float,
    overcut_guard: bool,
    overcut_mode: str,
    overcut_threshold: float,
    debug_csv: Path | None,
) -> dict:
    """执行目录批处理的配对与导出。"""

    words_files = iter_files(materials_dir, [glob_words])  # 收集所有 JSON
    if not words_files:  # 未找到文件
        return {"items": [], "summary": {"total": 0, "ok": 0, "failed": 0, "elapsed_seconds": 0.0}}
    start = time.perf_counter()  # 记录耗时
    items: list[dict] = []  # 存储处理结果
    failed = 0  # 统计失败数
    total = len(words_files)  # 总任务数
    executor: ThreadPoolExecutor | None = None  # 线程池引用
    futures = []  # 并发任务列表
    try:
        if workers and workers > 1:  # 并发模式
            executor = ThreadPoolExecutor(max_workers=workers)  # 构建线程池
            for words_path in words_files:  # 遍历每个 JSON
                text_path = find_text_for_stem(materials_dir, stem_from_words_json(words_path), text_patterns)  # 查找文本
                if text_path is None:  # 未匹配到文本
                    item = {
                        "stem": stem_from_words_json(words_path),
                        "words_json": safe_rel(materials_dir, words_path),
                        "text": "",
                        "outputs": {},
                        "stats": {},
                        "status": "failed",
                        "message": "未找到匹配的 TXT 或 .norm.txt",
                    }
                    failed += 1
                    items.append(item)
                    continue
                futures.append(
                    executor.submit(
                        _process_retake_item,
                        words_path,
                        text_path,
                        out_dir,
                        None,
                        None,
                        None,
                        materials_dir,
                        out_dir,
                        min_sent_chars,
                        line_max_dup_gap_sec,
                        sentence_max_dup_gap_sec,
                        max_window_sec,
                        sentence_strict,
                        review_only,
                        merge_adj_gap_sec,
                        low_conf,
                        pause_align=pause_align,
                        pause_gap_sec=pause_gap_sec,
                        pause_snap_limit=pause_snap_limit,
                        pad_before=pad_before,
                        pad_after=pad_after,
                        min_segment_sec=min_segment_sec,
                        merge_gap_sec=merge_gap_sec,
                        silence_probe_enabled=silence_probe_enabled,
                        noise_db=noise_db,
                        silence_min_d=silence_min_d,
                        overcut_guard=overcut_guard,
                        overcut_mode=overcut_mode,
                        overcut_threshold=overcut_threshold,
                        debug_csv=debug_csv,
                    )
                )  # 提交任务
            for future in as_completed(futures):  # 收集结果
                _, item = future.result()
                items.append(item)
                if item["status"] != "ok":  # 统计失败
                    failed += 1
        else:  # 串行模式
            for words_path in words_files:
                text_path = find_text_for_stem(materials_dir, stem_from_words_json(words_path), text_patterns)  # 配对文本
                if text_path is None:
                    items.append(
                        {
                            "stem": stem_from_words_json(words_path),
                            "words_json": safe_rel(materials_dir, words_path),
                            "text": "",
                            "outputs": {},
                            "stats": {},
                            "status": "failed",
                            "message": "未找到匹配的 TXT 或 .norm.txt",
                        }
                    )
                    failed += 1
                    continue
                _, item = _process_retake_item(
                    words_path,
                    text_path,
                    out_dir,
                    None,
                    None,
                    None,
                    materials_dir,
                    out_dir,
                    min_sent_chars,
                    line_max_dup_gap_sec,
                    sentence_max_dup_gap_sec,
                    max_window_sec,
                    sentence_strict,
                    review_only,
                    merge_adj_gap_sec,
                    low_conf,
                    pause_align=pause_align,
                    pause_gap_sec=pause_gap_sec,
                    pause_snap_limit=pause_snap_limit,
                    pad_before=pad_before,
                    pad_after=pad_after,
                    min_segment_sec=min_segment_sec,
                    merge_gap_sec=merge_gap_sec,
                    silence_probe_enabled=silence_probe_enabled,
                    noise_db=noise_db,
                    silence_min_d=silence_min_d,
                    overcut_guard=overcut_guard,
                    overcut_mode=overcut_mode,
                    overcut_threshold=overcut_threshold,
                    debug_csv=debug_csv,
                )  # 直接处理
                items.append(item)
                if item["status"] != "ok":  # 更新失败计数
                    failed += 1
    finally:
        if executor:  # 清理线程池
            executor.shutdown()
    elapsed = time.perf_counter() - start  # 计算耗时
    items.sort(key=lambda item: item.get("stem", ""))  # 按 stem 排序
    summary = {"total": total, "ok": total - failed, "failed": failed, "elapsed_seconds": elapsed}  # 汇总
    return {"items": items, "summary": summary}


def run_retake_keep_last(args: argparse.Namespace, *, report_path: Path, write_report: bool = True) -> dict:
    """执行 retake-keep-last 子命令逻辑。"""

    out_dir = Path(args.out).expanduser().resolve()  # 解析输出目录
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保存在
    if args.max_dup_gap_sec is None:
        line_max_dup_gap_sec = LINE_MAX_DUP_GAP_SEC
        sentence_max_dup_gap_sec = SENT_MAX_DUP_GAP_SEC
    else:
        value = float(args.max_dup_gap_sec)
        line_max_dup_gap_sec = value
        sentence_max_dup_gap_sec = value
    merge_adj_gap_sec = (
        float(args.merge_adj_gap_sec) if args.merge_adj_gap_sec is not None else MERGE_ADJ_GAP_SEC
    )
    low_conf = float(args.low_conf) if args.low_conf is not None else SENT_LOW_CONF
    pause_align = not args.no_pause_align
    pause_gap_sec = float(args.pause_gap_sec)
    pause_snap_limit = float(args.pause_snap_limit)
    pad_before = float(args.pad_before)
    pad_after = float(args.pad_after)
    min_segment_sec = float(args.min_segment_sec)
    merge_gap_sec = float(args.merge_gap_sec)
    silence_probe_enabled = not args.no_silence_probe
    noise_db = int(args.noise_db)
    silence_min_d = float(args.silence_min_d)
    overcut_guard = not args.no_overcut_guard
    overcut_mode = args.overcut_mode
    overcut_threshold = float(args.overcut_threshold)
    debug_csv = Path(args.debug_csv).expanduser() if args.debug_csv else None
    if debug_csv and args.workers and args.workers > 1:
        LOGGER.warning("检测到并发执行，已禁用 debug CSV 以避免文件竞争。")
        debug_csv = None
    if args.words_json:  # 单文件模式
        if not args.text:
            raise ValueError("单文件模式需要同时提供 --text")
        _, item = _process_retake_item(
            Path(args.words_json),
            Path(args.text),
            out_dir,
            args.source_audio,
            args.samplerate,
            args.channels,
            None,
            out_dir,
            args.min_sent_chars,
            line_max_dup_gap_sec,
            sentence_max_dup_gap_sec,
            args.max_window_sec,
            args.sentence_strict,
            args.review_only,
            merge_adj_gap_sec,
            low_conf,
            pause_align=pause_align,
            pause_gap_sec=pause_gap_sec,
            pause_snap_limit=pause_snap_limit,
            pad_before=pad_before,
            pad_after=pad_after,
            min_segment_sec=min_segment_sec,
            merge_gap_sec=merge_gap_sec,
            silence_probe_enabled=silence_probe_enabled,
            noise_db=noise_db,
            silence_min_d=silence_min_d,
            overcut_guard=overcut_guard,
            overcut_mode=overcut_mode,
            overcut_threshold=overcut_threshold,
            debug_csv=debug_csv,
        )
        items = [item]
        failed = 0 if item["status"] == "ok" else 1
        summary = {"total": 1, "ok": 1 - failed, "failed": failed, "elapsed_seconds": 0.0}
    elif args.text:  # 仅给出文本但缺少 JSON
        raise ValueError("单文件模式必须提供 --words-json")
    else:  # 目录批处理
        text_patterns = list(args.glob_text)
        result = _run_retake_batch(
            Path(args.materials),
            out_dir,
            args.glob_words,
            text_patterns,
            args.workers,
            args.min_sent_chars,
            line_max_dup_gap_sec,
            sentence_max_dup_gap_sec,
            args.max_window_sec,
            args.sentence_strict,
            args.review_only,
            merge_adj_gap_sec,
            low_conf,
            pause_align,
            pause_gap_sec,
            pause_snap_limit,
            pad_before,
            pad_after,
            min_segment_sec,
            merge_gap_sec,
            silence_probe_enabled,
            noise_db,
            silence_min_d,
            overcut_guard,
            overcut_mode,
            overcut_threshold,
            debug_csv,
        )
        items = result["items"]
        summary = result["summary"]
        failed = summary.get("failed", 0)
    payload = {"items": items, "summary": summary}
    stat_keys = [
        "total_words",
        "total_lines",
        "matched_lines",
        "strict_matches",
        "fallback_matches",
        "unmatched_lines",
        "len_gate_skipped",
        "neighbor_gap_skipped",
        "max_window_splits",
        "total_sentences",
        "matched_sentences",
        "low_conf_sentences",
        "unmatched_sentences",
        "strict_hit_sentences",
        "fuzzy_hit_sentences",
        "keep_span_count",
        "pause_snaps",
        "auto_merged",
        "too_short_dropped",
        "silence_regions",
    ]  # 汇总字段
    aggregated = {
        key: sum(int(item.get("stats", {}).get(key, 0)) for item in items if item.get("status") == "ok")
        for key in stat_keys
    }  # 聚合统计
    summary["aggregated_stats"] = aggregated
    if write_report:  # 写入批处理报告
        existing = {}
        if report_path.exists():
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        existing["retake_keep_last"] = payload
        write_json(report_path, existing)
    return payload


def handle_retake_keep_last(args: argparse.Namespace) -> int:
    """处理 retake-keep-last 子命令。"""

    if args.review_only and not args.sentence_strict:
        raise ValueError("--review-only 仅在启用 --sentence-strict 时可用。")
    parts: list[str] = []
    if args.words_json:
        parts.extend([
            "--words-json",
            str(Path(args.words_json)),
            "--text",
            str(Path(args.text)),
        ])
    else:
        parts.extend([
            "--materials",
            str(Path(args.materials)),
            "--glob-words",
            args.glob_words,
        ])
        for pattern in args.glob_text:
            parts.append("--glob-text")
            parts.append(pattern)
        if args.workers:
            parts.extend(["--workers", str(args.workers)])
    parts.extend(["--out", str(Path(args.out))])
    if args.source_audio:
        parts.extend(["--source-audio", args.source_audio])
    if args.samplerate:
        parts.extend(["--samplerate", str(args.samplerate)])
    if args.channels:
        parts.extend(["--channels", str(args.channels)])
    if args.min_sent_chars != MIN_SENT_CHARS:
        parts.extend(["--min-sent-chars", str(args.min_sent_chars)])
    if args.max_dup_gap_sec is not None:
        parts.extend(["--max-dup-gap-sec", str(args.max_dup_gap_sec)])
    if float(args.max_window_sec) != float(MAX_WINDOW_SEC):
        parts.extend(["--max-window-sec", str(args.max_window_sec)])
    if args.merge_adj_gap_sec is not None:
        parts.extend(["--merge-adj-gap-sec", str(args.merge_adj_gap_sec)])
    if args.low_conf is not None:
        parts.extend(["--low-conf", str(args.low_conf)])
    if args.no_pause_align:
        parts.append("--no-pause-align")
    if float(args.pause_gap_sec) != PAUSE_GAP_SEC:
        parts.extend(["--pause-gap-sec", str(args.pause_gap_sec)])
    if float(args.pause_snap_limit) != PAUSE_SNAP_LIMIT:
        parts.extend(["--pause-snap-limit", str(args.pause_snap_limit)])
    if float(args.pad_before) != PAD_BEFORE:
        parts.extend(["--pad-before", str(args.pad_before)])
    if float(args.pad_after) != PAD_AFTER:
        parts.extend(["--pad-after", str(args.pad_after)])
    if float(args.min_segment_sec) != MIN_SEGMENT_SEC:
        parts.extend(["--min-segment-sec", str(args.min_segment_sec)])
    if float(args.merge_gap_sec) != MERGE_GAP_SEC:
        parts.extend(["--merge-gap-sec", str(args.merge_gap_sec)])
    if args.no_silence_probe:
        parts.append("--no-silence-probe")
    if int(args.noise_db) != -35:
        parts.extend(["--noise-db", str(args.noise_db)])
    if float(args.silence_min_d) != 0.28:
        parts.extend(["--silence-min-d", str(args.silence_min_d)])
    if args.no_overcut_guard:
        parts.append("--no-overcut-guard")
    if args.overcut_mode != "ask":
        parts.extend(["--overcut-mode", args.overcut_mode])
    if float(args.overcut_threshold) != 0.60:
        parts.extend(["--overcut-threshold", str(args.overcut_threshold)])
    if args.debug_csv:
        parts.extend(["--debug-csv", str(Path(args.debug_csv))])
    if args.sentence_strict:
        parts.append("--sentence-strict")
    if args.review_only:
        parts.append("--review-only")
    LOGGER.info("开始保留最后一遍任务: 输入=%s 文本=%s 输出=%s", args.words_json or args.materials, args.text, args.out)
    LOGGER.info("等价命令: %s", _build_cli_example("retake-keep-last", parts))

    try:
        payload = run_retake_keep_last(args, report_path=Path(args.out) / "batch_report.json")
    except Exception as exc:
        LOGGER.exception("处理 retake-keep-last 失败")
        print(f"处理失败: {exc}", file=sys.stderr)
        return 1
    summary = payload["summary"]
    LOGGER.info(
        "完成保留最后一遍 %s 个，成功 %s，失败 %s，耗时 %.2fs",
        summary.get("total", 0),
        summary.get("ok", 0),
        summary.get("failed", 0),
        summary.get("elapsed_seconds", 0.0),
    )
    if summary.get("failed"):
        LOGGER.warning("存在 %s 个失败条目，请查看 batch_report.json。", summary.get("failed"))
    return 0


def _process_render_item(
    edl_path: Path,
    audio_root: Path,
    out_dir: Path,
    samplerate: Optional[int],
    channels: Optional[int],
) -> dict:
    """处理单个 EDL 渲染任务。"""

    try:
        edl = load_edl(edl_path)  # 读取 EDL JSON
        source_audio = resolve_source_audio(edl, edl_path, audio_root)  # 定位源音频
        duration = probe_duration(source_audio)  # 探测音频时长
        keeps = normalize_segments(edl.segments, duration)  # 归一化保留片段
        actual_samplerate = samplerate or edl.samplerate  # 采样率优先使用命令行
        actual_channels = channels or edl.channels  # 声道数优先使用命令行
        output_path = render_audio(
            edl_path,
            audio_root,
            out_dir,
            actual_samplerate,
            actual_channels,
            dry_run=False,
        )  # 调用渲染
        keep_duration = sum(seg.end - seg.start for seg in keeps)  # 统计保留时长
        return {
            "edl": safe_rel(edl_path.parent, edl_path),
            "source_audio": safe_rel(audio_root, source_audio),
            "output": safe_rel(out_dir, output_path),
            "stats": {
                "segments": len(keeps),
                "keep_duration": keep_duration,
                "samplerate": actual_samplerate,
                "channels": actual_channels,
            },
            "status": "ok",
            "message": "渲染成功",
        }
    except Exception as exc:
        LOGGER.exception("渲染任务失败: %s", edl_path)
        return {
            "edl": safe_rel(edl_path.parent, edl_path),
            "source_audio": "",
            "output": "",
            "stats": {},
            "status": "failed",
            "message": str(exc),
        }


def run_render_audio(args: argparse.Namespace, *, report_path: Path, write_report: bool = True) -> dict:
    """执行 render-audio 子命令核心逻辑。"""

    audio_root = Path(args.audio_root).expanduser().resolve()  # 源音频根目录
    out_dir = Path(args.out).expanduser().resolve()  # 输出目录
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保存在
    samplerate = args.samplerate  # 命令行采样率
    channels = args.channels  # 命令行声道数
    if samplerate is not None and samplerate <= 0:  # 校验采样率
        raise ValueError("--samplerate 必须为正整数")
    if channels is not None and channels <= 0:  # 校验声道数
        raise ValueError("--channels 必须为正整数")

    if args.edl:  # 单文件模式
        items = [
            _process_render_item(Path(args.edl), audio_root, out_dir, samplerate, channels)
        ]
        summary = {
            "total": 1,
            "ok": 1 if items[0]["status"] == "ok" else 0,
            "failed": 0 if items[0]["status"] == "ok" else 1,
            "elapsed_seconds": 0.0,
        }
    else:  # 批处理模式
        edl_files = iter_files(Path(args.materials), list(args.glob_edl))  # 搜索 EDL
        if not edl_files:
            return {"items": [], "summary": {"total": 0, "ok": 0, "failed": 0, "elapsed_seconds": 0.0}}
        start = time.perf_counter()  # 记录耗时
        items = []
        failed = 0
        workers = args.workers or 1
        if workers > 1:  # 并发执行
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        _process_render_item,
                        edl_path,
                        audio_root,
                        out_dir,
                        samplerate,
                        channels,
                    )
                    for edl_path in edl_files
                ]
                for future in as_completed(futures):  # 收集结果
                    item = future.result()
                    items.append(item)
                    if item["status"] != "ok":
                        failed += 1
        else:  # 串行执行
            for edl_path in edl_files:
                item = _process_render_item(edl_path, audio_root, out_dir, samplerate, channels)
                items.append(item)
                if item["status"] != "ok":
                    failed += 1
        elapsed = time.perf_counter() - start  # 计算耗时
        items.sort(key=lambda item: item.get("edl", ""))  # 按 EDL 名称排序
        summary = {"total": len(edl_files), "ok": len(edl_files) - failed, "failed": failed, "elapsed_seconds": elapsed}
    payload = {"items": items, "summary": summary}
    summary["aggregated_stats"] = {
        "segments": sum(int(item.get("stats", {}).get("segments", 0)) for item in items if item.get("status") == "ok"),
        "keep_duration": sum(float(item.get("stats", {}).get("keep_duration", 0.0)) for item in items if item.get("status") == "ok"),
    }
    if write_report:
        existing = {}
        if report_path.exists():
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        existing.setdefault("retake_keep_last", {})
        existing["render_audio"] = payload
        write_json(report_path, existing)
    return payload


def handle_render_audio(args: argparse.Namespace) -> int:
    """处理 render-audio 子命令。"""

    parts: list[str] = []
    if args.edl:
        parts.extend(["--edl", str(Path(args.edl))])
    else:
        parts.extend(["--materials", str(Path(args.materials))])
        for pattern in args.glob_edl:
            parts.extend(["--glob-edl", pattern])
        if args.workers:
            parts.extend(["--workers", str(args.workers)])
    parts.extend(["--audio-root", str(Path(args.audio_root)), "--out", str(Path(args.out))])
    if args.samplerate:
        parts.extend(["--samplerate", str(args.samplerate)])
    if args.channels:
        parts.extend(["--channels", str(args.channels)])
    LOGGER.info(
        "开始渲染任务: 模式=%s 源音频=%s 输出=%s",
        "单文件" if args.edl else "批处理",
        args.audio_root,
        args.out,
    )
    LOGGER.info("等价命令: %s", _build_cli_example("render-audio", parts))
    try:
        payload = run_render_audio(args, report_path=Path(args.out) / "batch_report.json")
    except Exception as exc:
        LOGGER.exception("处理 render-audio 失败")
        print(f"处理失败: {exc}", file=sys.stderr)
        return 1
    summary = payload["summary"]
    LOGGER.info(
        "完成渲染 %s 个，成功 %s，失败 %s，耗时 %.2fs",
        summary.get("total", 0),
        summary.get("ok", 0),
        summary.get("failed", 0),
        summary.get("elapsed_seconds", 0.0),
    )
    if summary.get("failed"):
        LOGGER.warning("存在 %s 个失败条目，请查看 batch_report.json。", summary.get("failed"))
    return 0


def run_all_in_one(args: argparse.Namespace) -> dict:
    """执行 all-in-one 流水线。"""

    out_dir = Path(args.out).expanduser().resolve()  # 解析输出根目录
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    report_path = out_dir / "batch_report.json"  # 报告路径
    report: dict = {}  # 汇总结构
    total_failed = 0  # 总失败数
    total_ok = 0  # 总成功数
    start = time.perf_counter()  # 记录耗时
    stage_summary: dict[str, dict] = {}  # 各阶段摘要

    if args.do_norm:  # 可选规范化阶段
        norm_out = out_dir / "norm"
        LOGGER.info("开始规范化文本 → %s", norm_out)
        norm_result = run_prep_norm(
            Path(args.materials),
            norm_out,
            Path(args.char_map),
            args.opencc,
            args.norm_glob,
            args.dry_run,
            args.emit_align,
        )
        report["prep_norm"] = norm_result
        total_failed += norm_result["summary"].get("failed", 0)
        total_ok += norm_result["summary"].get("ok", 0)
        stage_summary["prep_norm"] = norm_result["summary"]

    LOGGER.info("开始保留最后一遍生成字幕/EDL → %s", out_dir)
    retake_namespace = argparse.Namespace(
        words_json=None,
        text=None,
        materials=args.materials,
        glob_words=args.glob_words,
        glob_text=args.glob_text,
        workers=args.workers,
        out=str(out_dir),
        source_audio=None,
        samplerate=args.samplerate,
        channels=args.channels,
        min_sent_chars=args.min_sent_chars,
        max_dup_gap_sec=args.max_dup_gap_sec,
        max_window_sec=args.max_window_sec,
        merge_adj_gap_sec=args.merge_adj_gap_sec,
        low_conf=args.low_conf,
        sentence_strict=args.sentence_strict,
        review_only=args.review_only,
    )
    retake_result = run_retake_keep_last(retake_namespace, report_path=report_path, write_report=False)
    report["retake_keep_last"] = retake_result
    total_failed += retake_result["summary"].get("failed", 0)
    total_ok += retake_result["summary"].get("ok", 0)
    stage_summary["retake_keep_last"] = retake_result["summary"]

    if args.render:  # 可选渲染阶段
        LOGGER.info("开始渲染音频 → %s", out_dir)
        render_namespace = argparse.Namespace(
            edl=None,
            materials=str(out_dir),
            glob_edl=args.glob_edl,
            workers=args.workers,
            audio_root=args.audio_root,
            out=str(out_dir),
            samplerate=args.samplerate,
            channels=args.channels,
        )
        render_result = run_render_audio(render_namespace, report_path=report_path, write_report=False)
        report["render_audio"] = render_result
        total_failed += render_result["summary"].get("failed", 0)
        total_ok += render_result["summary"].get("ok", 0)
        stage_summary["render_audio"] = render_result["summary"]

    elapsed = time.perf_counter() - start  # 流水线耗时
    report["summary"] = {
        "total_ok": total_ok,
        "total_failed": total_failed,
        "elapsed_seconds": elapsed,
        "stages": stage_summary,
    }
    write_json(report_path, report)
    return report


def handle_all_in_one(args: argparse.Namespace) -> int:
    """处理 all-in-one 子命令。"""

    if args.review_only and not args.sentence_strict:
        raise ValueError("--review-only 仅能与 --sentence-strict 搭配使用。")
    parts: list[str] = [
        "--materials",
        str(Path(args.materials)),
        "--audio-root",
        str(Path(args.audio_root)),
        "--out",
        str(Path(args.out)),
    ]
    if args.do_norm:
        parts.extend(["--do-norm", "--opencc", args.opencc, "--norm-glob", args.norm_glob])
        parts.extend(["--char-map", str(Path(args.char_map))])
        if args.emit_align:
            parts.append("--emit-align")
        if args.dry_run:
            parts.append("--dry-run")
    parts.extend(["--glob-words", args.glob_words])
    for pattern in args.glob_text:
        parts.extend(["--glob-text", pattern])
    if args.render:
        parts.append("--render")
        for pattern in args.glob_edl:
            parts.extend(["--glob-edl", pattern])
    if args.samplerate:
        parts.extend(["--samplerate", str(args.samplerate)])
    if args.channels:
        parts.extend(["--channels", str(args.channels)])
    if args.workers:
        parts.extend(["--workers", str(args.workers)])
    if args.min_sent_chars != MIN_SENT_CHARS:
        parts.extend(["--min-sent-chars", str(args.min_sent_chars)])
    if args.max_dup_gap_sec is not None:
        parts.extend(["--max-dup-gap-sec", str(args.max_dup_gap_sec)])
    if float(args.max_window_sec) != float(MAX_WINDOW_SEC):
        parts.extend(["--max-window-sec", str(args.max_window_sec)])
    if args.merge_adj_gap_sec is not None:
        parts.extend(["--merge-adj-gap-sec", str(args.merge_adj_gap_sec)])
    if args.low_conf is not None:
        parts.extend(["--low-conf", str(args.low_conf)])
    if args.sentence_strict:
        parts.append("--sentence-strict")
    if args.review_only:
        parts.append("--review-only")
    LOGGER.info(
        "开始流水线任务: 素材=%s 音频=%s 输出=%s 渲染=%s",
        args.materials,
        args.audio_root,
        args.out,
        bool(args.render),
    )
    LOGGER.info("等价命令: %s", _build_cli_example("all-in-one", parts))

    try:
        report = run_all_in_one(args)
    except Exception as exc:
        LOGGER.exception("处理 all-in-one 流水线失败")
        print(f"流水线执行失败: {exc}", file=sys.stderr)
        return 1
    summary = report.get("summary", {})
    LOGGER.info(
        "流水线结束，总成功 %s，总失败 %s，耗时 %.2fs",
        summary.get("total_ok", 0),
        summary.get("total_failed", 0),
        summary.get("elapsed_seconds", 0.0),
    )
    if summary.get("total_failed"):
        LOGGER.warning("存在失败条目，请检查 %s", Path(args.out) / "batch_report.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建顶层解析器与子命令。"""

    parser = argparse.ArgumentParser(description="OnePass Audio 统一命令行工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prep = subparsers.add_parser("prep-norm", help="批量规范化原文 TXT")
    prep.add_argument("--in", dest="input", required=True, help="输入文件或目录")
    prep.add_argument("--out", dest="output", required=True, help="规范化输出目录 (需位于 out/ 下)")
    prep.add_argument("--char-map", dest="char_map", default=str(DEFAULT_CHAR_MAP), help="字符映射配置 JSON")
    prep.add_argument("--opencc", choices=["none", "t2s", "s2t"], default="none", help="opencc 模式")
    prep.add_argument("--glob", default="*.txt", help="目录模式匹配 (默认 *.txt)")
    prep.add_argument(
        "--emit-align",
        action="store_true",
        help="额外生成去标点的 .align.txt 供词级对齐与可视化使用",
    )
    prep.add_argument("--dry-run", action="store_true", help="仅生成报表，不写规范化文本")
    prep.set_defaults(func=handle_prep_norm)

    retake = subparsers.add_parser("retake-keep-last", help="词级 JSON + 原文 → SRT/TXT/EDL/Markers")
    group = retake.add_mutually_exclusive_group(required=True)
    group.add_argument("--words-json", help="单文件模式：词级 JSON")
    group.add_argument("--materials", help="目录批处理模式：素材根目录")
    retake.add_argument("--text", help="单文件模式：原文 TXT")
    retake.add_argument("--out", required=True, help="输出目录")
    retake.add_argument("--glob-words", default="*.words.json", help="批处理模式：JSON 匹配模式")
    retake.add_argument(
        "--glob-text",
        nargs="+",
        default=["*.norm.txt", "*.txt"],
        help="批处理模式：文本匹配模式 (可多次指定)",
    )
    retake.add_argument("--workers", type=int, help="批处理并发度")
    retake.add_argument("--source-audio", help="单文件模式：EDL 中记录的源音频相对路径")
    retake.add_argument("--samplerate", type=int, help="可选：EDL 建议采样率")
    retake.add_argument("--channels", type=int, help="可选：EDL 建议声道数")
    retake.add_argument(
        "--min-sent-chars",
        type=int,
        default=MIN_SENT_CHARS,
        help=f"重复去重的句长下限（默认 {MIN_SENT_CHARS}）",
    )
    retake.add_argument(
        "--max-dup-gap-sec",
        type=float,
        default=None,
        help=(
            "判定重录的最大近邻间隔，单位秒（行级默认"
            f" {LINE_MAX_DUP_GAP_SEC:g}，句子模式默认 {SENT_MAX_DUP_GAP_SEC:g}）"
        ),
    )
    retake.add_argument(
        "--max-window-sec",
        type=float,
        default=MAX_WINDOW_SEC,
        help=f"单个 drop 窗口的最长持续时间，单位秒（默认 {MAX_WINDOW_SEC:g}）",
    )
    retake.add_argument(
        "--merge-adj-gap-sec",
        type=float,
        default=None,
        help=f"句子级命中合并间隔阈值（默认 {MERGE_ADJ_GAP_SEC:g} 秒）",
    )
    retake.add_argument(
        "--low-conf",
        type=float,
        default=None,
        help=f"句子命中置信度阈值（默认 {SENT_LOW_CONF:.2f}）",
    )
    retake.add_argument(
        "--low-conf-threshold",
        dest="low_conf",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    retake.add_argument("--no-pause-align", action="store_true", help="关闭停顿吸附调优")
    retake.add_argument(
        "--pause-gap-sec",
        type=float,
        default=PAUSE_GAP_SEC,
        help=f"判定停顿的词间间隔阈值，秒（默认 {PAUSE_GAP_SEC:.2f}）",
    )
    retake.add_argument(
        "--pause-snap-limit",
        type=float,
        default=PAUSE_SNAP_LIMIT,
        help=f"段首尾向停顿吸附时允许的最大偏移，秒（默认 {PAUSE_SNAP_LIMIT:.2f}）",
    )
    retake.add_argument(
        "--pad-before",
        type=float,
        default=PAD_BEFORE,
        help=f"段首额外留白，秒（默认 {PAD_BEFORE:.2f}）",
    )
    retake.add_argument(
        "--pad-after",
        type=float,
        default=PAD_AFTER,
        help=f"段尾额外留白，秒（默认 {PAD_AFTER:.2f}）",
    )
    retake.add_argument(
        "--min-segment-sec",
        type=float,
        default=MIN_SEGMENT_SEC,
        help=f"最短保留段长度，低于则尝试合并，秒（默认 {MIN_SEGMENT_SEC:.2f}）",
    )
    retake.add_argument(
        "--merge-gap-sec",
        type=float,
        default=MERGE_GAP_SEC,
        help=f"补偿后相邻片段自动合并的最大间隙，秒（默认 {MERGE_GAP_SEC:.2f}）",
    )
    retake.add_argument("--no-silence-probe", action="store_true", help="跳过 ffmpeg 静音探测")
    retake.add_argument(
        "--noise-db",
        type=int,
        default=-35,
        help="静音检测噪声阈值，单位 dB（默认 -35）",
    )
    retake.add_argument(
        "--silence-min-d",
        type=float,
        default=0.28,
        help="静音检测的最小时长，秒（默认 0.28）",
    )
    retake.add_argument("--no-overcut-guard", action="store_true", help="关闭过裁剪保护")
    retake.add_argument(
        "--overcut-mode",
        choices=["ask", "auto", "abort"],
        default="ask",
        help="过裁剪保护触发时的策略（默认 ask）",
    )
    retake.add_argument(
        "--overcut-threshold",
        type=float,
        default=0.60,
        help="过裁剪保护的剪切比例阈值（默认 0.60）",
    )
    retake.add_argument("--debug-csv", help="将段吸附/合并调试信息写入指定 CSV")
    retake.add_argument("--sentence-strict", action="store_true", help="启用句子级审阅模式")
    retake.add_argument(
        "--review-only",
        action="store_true",
        help="句子级模式下仅打点不裁剪，EDL 保留整段",
    )
    retake.set_defaults(func=handle_retake_keep_last)

    render = subparsers.add_parser("render-audio", help="按 EDL 渲染干净音频")
    group_r = render.add_mutually_exclusive_group(required=True)
    group_r.add_argument("--edl", help="单文件模式：EDL JSON")
    group_r.add_argument("--materials", help="目录批处理模式：EDL 搜索根目录")
    render.add_argument(
        "--glob-edl",
        nargs="+",
        default=["*.keepLast.edl.json", "*.sentence.edl.json"],
        help="批处理模式：EDL 匹配模式",
    )
    render.add_argument("--workers", type=int, help="批处理并发度")
    render.add_argument("--audio-root", required=True, help="源音频搜索根目录")
    render.add_argument("--out", required=True, help="输出目录")
    render.add_argument("--samplerate", type=int, help="渲染采样率 (可选)")
    render.add_argument("--channels", type=int, help="渲染声道数 (可选)")
    render.set_defaults(func=handle_render_audio)

    pipeline = subparsers.add_parser("all-in-one", help="一键流水线：规范化 → 保留最后一遍 → 渲染音频")
    pipeline.add_argument("--materials", required=True, help="素材根目录")
    pipeline.add_argument("--audio-root", required=True, help="音频搜索根目录")
    pipeline.add_argument("--out", required=True, help="输出根目录")
    pipeline.add_argument("--do-norm", action="store_true", help="启用文本规范化阶段")
    pipeline.add_argument("--dry-run", action="store_true", help="规范化阶段仅生成报表")
    pipeline.add_argument("--opencc", default="none", choices=["none", "t2s", "s2t"], help="opencc 模式")
    pipeline.add_argument("--char-map", default=str(DEFAULT_CHAR_MAP), help="字符映射配置 JSON")
    pipeline.add_argument("--norm-glob", default="*.txt", help="规范化阶段的匹配模式")
    pipeline.add_argument(
        "--emit-align",
        action="store_true",
        help="规范化阶段同步产出无标点的 .align.txt",
    )
    pipeline.add_argument("--glob-words", default="*.words.json", help="保留最后一遍：JSON 匹配模式")
    pipeline.add_argument(
        "--glob-text",
        nargs="+",
        default=["*.norm.txt", "*.txt"],
        help="保留最后一遍：文本匹配模式",
    )
    pipeline.add_argument("--render", action="store_true", help="执行渲染阶段")
    pipeline.add_argument(
        "--glob-edl",
        nargs="+",
        default=["*.keepLast.edl.json", "*.sentence.edl.json"],
        help="渲染阶段 EDL 匹配模式",
    )
    pipeline.add_argument("--samplerate", type=int, help="渲染采样率")
    pipeline.add_argument("--channels", type=int, help="渲染声道数")
    pipeline.add_argument("--workers", type=int, help="批处理并发度 (Windows 需入口保护)")
    pipeline.add_argument(
        "--min-sent-chars",
        type=int,
        default=MIN_SENT_CHARS,
        help=f"重复去重的句长下限（默认 {MIN_SENT_CHARS}）",
    )
    pipeline.add_argument(
        "--max-dup-gap-sec",
        type=float,
        default=None,
        help=(
            "判定重录的最大近邻间隔，单位秒（行级默认"
            f" {LINE_MAX_DUP_GAP_SEC:g}，句子模式默认 {SENT_MAX_DUP_GAP_SEC:g}）"
        ),
    )
    pipeline.add_argument(
        "--max-window-sec",
        type=float,
        default=MAX_WINDOW_SEC,
        help=f"单个 drop 窗口的最长持续时间，单位秒（默认 {MAX_WINDOW_SEC:g}）",
    )
    pipeline.add_argument(
        "--merge-adj-gap-sec",
        type=float,
        default=None,
        help=f"句子级命中合并间隔阈值（默认 {MERGE_ADJ_GAP_SEC:g} 秒）",
    )
    pipeline.add_argument(
        "--low-conf",
        type=float,
        default=None,
        help=f"句子命中置信度阈值（默认 {SENT_LOW_CONF:.2f}）",
    )
    pipeline.add_argument(
        "--low-conf-threshold",
        dest="low_conf",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    pipeline.add_argument("--sentence-strict", action="store_true", help="流水线中启用句子级审阅模式")
    pipeline.add_argument(
        "--review-only",
        action="store_true",
        help="句子级模式下仅打点不裁剪，EDL 保留整段",
    )
    pipeline.set_defaults(func=handle_all_in_one)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。"""

    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    LOGGER.info("启动 onepass_cli，子命令=%s", args.command)
    try:
        return args.func(args)
    except Exception as exc:
        LOGGER.exception("命令执行过程中出现未捕获异常")
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
