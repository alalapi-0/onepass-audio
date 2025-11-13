"""OnePass Audio 统一命令行入口。"""
from __future__ import annotations

import argparse  # 解析命令行参数
import csv  # 写入规范化报表
import json  # 生成批处理 JSON 报告
import logging  # 控制台日志输出
import re  # 处理 glob 参数拆分与上下文清理
import shlex  # 构建可复制的命令示例
import fnmatch  # 大小写无关的 glob 匹配
import sys  # 访问解释器信息
import threading
import time  # 统计耗时
from dataclasses import asdict, dataclass  # 复用数据类结构化统计
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed  # 并发执行
from pathlib import Path  # 跨平台路径处理
from typing import Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode

# 计算项目根目录，确保脚本可直接运行
ROOT_DIR = Path(__file__).resolve().parents[1]  # 项目根目录
if str(ROOT_DIR) not in sys.path:  # 若根目录未在 sys.path 中则插入
    sys.path.insert(0, str(ROOT_DIR))

from match_materials import match_materials, parse_glob_list
from scripts.ui_server import start_ui

from onepass.asr_loader import load_words  # 载入词级 JSON
from onepass.alignment.canonical import (
    CanonicalRules,
    concat_and_index,
)
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
from onepass.normalize import collapse_soft_linebreaks
from onepass.zh_segmenter import Segment as ZhSegment
from onepass.zh_segmenter import segment as segment_text
from onepass.retake_keep_last import (  # 保留最后一遍导出函数
    EDLWriteResult,
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
    load_alias_map,
    load_char_map,
    normalize_chinese_text,
    normalize_pipeline,
    run_opencc_if_available,
    scan_suspects,
    sentence_lines_from_text,
    validate_sentence_lines,
)
from onepass.text_normalizer import normalize_text_for_export
from onepass.sent_align import (
    LOW_CONF as SENT_LOW_CONF,
    MAX_DUP_GAP_SEC as SENT_MAX_DUP_GAP_SEC,
    MERGE_ADJ_GAP_SEC,
)
from onepass.logging_utils import default_log_dir, setup_logger
from onepass.ui_server import open_browser_later, start_static_server


DEFAULT_NORMALIZE_REPORT = ROOT_DIR / "out" / "normalize_report.csv"  # 规范化报表路径
DEFAULT_CHAR_MAP = ROOT_DIR / "config" / "default_char_map.json"  # 默认字符映射
DEFAULT_ALIAS_MAP = ROOT_DIR / "config" / "default_alias_map.json"
DEFAULT_ALIGN_SPLIT_MODE = "punct+len"
DEFAULT_ALIGN_MIN_LEN = 8
DEFAULT_ALIGN_MAX_LEN = 24
DEFAULT_ALIGN_HARD_MAX = 32
LOGGER = logging.getLogger("onepass.cli")  # 模块级日志器

_UI_PROCESSES: list[object] = []


@dataclass(slots=True)
class RetakeStats:
    """抽取保留最后一遍阶段的核心统计数据。"""

    stem: str
    total: int
    kept: int
    unaligned: int
    dedup_window: int
    cut_seconds: float


def _configure_logging() -> None:
    """初始化控制台日志格式。"""

    global LOGGER
    LOGGER = setup_logger("onepass.cli", default_log_dir())  # 使用统一滚动日志配置


def _build_cli_example(subcommand: str, parts: Sequence[str]) -> str:
    """构建便于复制的命令行示例。"""

    args = [sys.executable, str(Path(__file__).resolve()), subcommand, *parts]  # 拼接完整命令
    return shlex.join(args)  # 返回 shell 风格字符串


def _ensure_web_assets() -> Path:
    """确保 Web 前端静态资源目录存在。"""

    web_dir = ROOT_DIR / "web"
    if not web_dir.exists():
        raise FileNotFoundError("未找到 web/ 目录，无法启动本地 UI。")
    return web_dir


def _write_ui_manifest(
    out_dir: Path,
    *,
    audio_root: Path,
    items: Sequence[dict],
    render_summary: Mapping[str, Mapping[str, object]],
    stems: Sequence[str],
    report_path: Path,
) -> Path | None:
    """根据流水线输出生成 UI manifest.json。"""

    manifest_items: list[dict[str, object]] = []
    lower_render = {key.lower(): value for key, value in render_summary.items()}
    for item in items:
        if item.get("status") != "ok":
            continue
        stem = str(item.get("stem") or "").strip()
        outputs = item.get("outputs") or {}
        entry: dict[str, object] = {
            "stem": stem,
            "txt": outputs.get("txt", ""),
            "srt": outputs.get("srt", ""),
            "markers": outputs.get("markers", ""),
            "edl": outputs.get("edl", ""),
            "source_audio": item.get("source_audio_written") or "",
            "source_audio_resolved": item.get("source_audio_resolved_path") or "",
        }
        render_entry = lower_render.get(stem.lower()) if stem else None
        if render_entry:
            entry["rendered_audio"] = render_entry.get("output", "")
            entry["render_status"] = render_entry.get("status", "")
        manifest_items.append(entry)

    manifest = {
        "output_dir": str(out_dir),
        "audio_root": str(audio_root),
        "stems": [entry["stem"] for entry in manifest_items] if manifest_items else list(stems),
        "items": manifest_items,
        "batch_report": str(report_path),
    }
    try:
        manifest_path = out_dir / "manifest.json"
        write_json(manifest_path, manifest)
        return manifest_path
    except Exception:
        LOGGER.exception("写入 manifest.json 失败")
        return None


def _launch_static_ui(
    out_dir: Path,
    *,
    stems: Sequence[str],
    serve_host: str,
    serve_port: int,
    open_browser: bool,
    manifest_path: Path | None,
) -> tuple[threading.Thread | None, str | None]:
    """启动静态 Web UI，并在需要时打开浏览器。"""

    try:
        _ensure_web_assets()
    except FileNotFoundError as exc:
        LOGGER.error(str(exc))
        return None, None
    query: dict[str, str] = {"out": str(out_dir)}
    if stems:
        query["stem"] = ",".join(stems)
    if manifest_path and manifest_path.exists():
        query["manifest"] = str(manifest_path)
    base_url = f"http://{serve_host}:{serve_port}/"
    url = f"{base_url}?{urlencode(query)}" if query else base_url
    try:
        proc = start_ui(
            out_dir=out_dir,
            host=serve_host,
            port=serve_port,
            open_browser=open_browser,
            query=query,
        )
    except Exception:
        LOGGER.exception("自动启动 Web UI 失败")
        return None, None
    _UI_PROCESSES.append(proc)
    LOGGER.info("UI 服务已启动: %s", url)
    return proc, url


def _summarize_context(text: str, width: int = 20) -> str:
    """截取文本前后片段用于错误上下文展示。"""

    if not text:
        return ""
    sanitized = re.sub(r"\s+", " ", text.replace("\u3000", " ").replace("\t", " "))
    sanitized = sanitized.strip()
    if len(sanitized) <= width * 2:
        return sanitized
    return f"{sanitized[:width]}…{sanitized[-width:]}"


def _progress_tick(
    stage: str,
    processed: int,
    total: int,
    start_ts: float,
    last_ts: float,
    *,
    interval: float = 1.0,
) -> float:
    """根据进度打印日志并返回最近一次输出的时间戳。"""

    if total <= 0:
        return last_ts
    now = time.perf_counter()
    should_report = processed == total or processed % 200 == 0 or (now - last_ts) >= interval
    if not should_report or processed <= 0:
        return last_ts
    elapsed = now - start_ts
    remaining = max(total - processed, 0)
    eta = (elapsed / processed * remaining) if processed else 0.0
    LOGGER.info("[progress] %s %s/%s ETA=%.1fs", stage, processed, total, max(0.0, eta))
    return now


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


def _load_canonical_rules(char_map_path: Path) -> CanonicalRules:
    """Load canonical normalization rules from ``char_map_path``."""

    mapping: dict[str, str] = {}
    try:
        content = char_map_path.read_text(encoding="utf-8")
        payload = json.loads(content)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("WARNING: char_map load failed: %s ; use empty map {}", exc)
        return CanonicalRules(char_map=mapping)
    if isinstance(payload, dict):
        source = payload.get("map") if isinstance(payload.get("map"), dict) else payload
        if isinstance(source, dict):
            for key, value in source.items():
                if not isinstance(key, str):
                    key = str(key)
                if isinstance(value, str):
                    mapping[key] = value
                else:
                    mapping[key] = str(value)
    else:
        LOGGER.warning(
            "WARNING: char_map load failed: unexpected payload type %s ; use empty map {}",
            type(payload).__name__,
        )
        mapping = {}
    return CanonicalRules(char_map=mapping)


def _process_single_text(
    path: Path,
    base_dir: Path,
    out_dir: Path,
    cmap: dict,
    opencc_mode: str,
    dry_run: bool,
    collapse_lines: bool,
    emit_align: bool,
    split_mode: str,
    canonical_rules: CanonicalRules | None,
    *,
    align_min_len: int,
    align_max_len: int,
    align_hard_max: int,
    align_weak_punct: bool,
    align_keep_quotes: bool,
) -> dict:
    """对单个文本执行规范化处理并返回报表行。"""

    LOGGER.debug("Normalize %s", path)  # 调试输出当前文件
    raw_text = ""
    try:
        raw_text = path.read_text(encoding="utf-8-sig")  # 按 UTF-8-sig 读取原文
        decode_note = ""  # 初始化编码提示
    except UnicodeDecodeError:
        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")  # 若解码失败则替换异常字符
        decode_note = "原文包含无法解码的字符，已用替换符号保留。"  # 记录提示
    except OSError as exc:
        LOGGER.error("[error] 读取失败 %s: %s", path, exc)
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
        context_preview = _summarize_context(raw_text)
        LOGGER.exception("规范化流程失败: %s 上下文=%s", path, context_preview)
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
            "message": f"规范化失败: {exc}; ctx={context_preview}",
        }

    converted_text, opencc_applied = run_opencc_if_available(normalized_text, opencc_mode)  # 运行 OpenCC
    sentence_lines = sentence_lines_from_text(converted_text, collapse_lines=collapse_lines)
    if collapse_lines and sentence_lines:
        validate_sentence_lines(sentence_lines)
    converted_text = "\n".join(sentence_lines)
    converted_text = normalize_chinese_text(converted_text, collapse_lines=collapse_lines)
    payload_text = converted_text.replace("\r\n", "\n").replace("\r", "\n")
    if collapse_lines:
        payload_text = collapse_soft_linebreaks(payload_text)
    else:
        payload_text = payload_text.replace("\t", " ")
    suspects = scan_suspects(payload_text)  # 扫描可疑字符
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
    align_debug_path: Path | None = None
    align_lines: list[str] | None = None
    align_guard_attempted = False
    align_guard_failed = False
    effective_split_mode = split_mode
    effective_min_len = align_min_len
    effective_max_len = align_max_len
    effective_hard_max = align_hard_max
    effective_weak = align_weak_punct
    canonical_path: Path | None = None

    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)  # 创建输出目录
        payload_text = normalize_text_for_export(
            payload_text,
            char_map=cmap,
            preserve_newlines=not collapse_lines,
        )
        payload = payload_text
        if payload and not payload.endswith("\n"):
            payload = payload + "\n"
        try:
            with out_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
        except OSError as exc:
            return {
                "file": str(path),
                "orig_len": len(raw_text),
                "norm_len": len(payload_text),
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
            align_source = normalize_text_for_export(
                payload_text,
                char_map=cmap,
                preserve_newlines=True,
            )
            align_segments: list[ZhSegment] = segment_text(
                align_source,
                split_mode=split_mode,
                min_len=align_min_len,
                max_len=align_max_len,
                hard_max=align_hard_max,
                weak_punct_enable=align_weak_punct,
                keep_quotes=align_keep_quotes,
            )
            if len(align_segments) <= 1:
                align_guard_attempted = True
                guard_segments = segment_text(
                    align_source,
                    split_mode="punct+len",
                    min_len=8,
                    max_len=20,
                    hard_max=28,
                    weak_punct_enable=True,
                    keep_quotes=align_keep_quotes,
                )
                effective_split_mode = "punct+len"
                effective_min_len = 8
                effective_max_len = 20
                effective_hard_max = 28
                effective_weak = True
                if guard_segments:
                    align_segments = guard_segments
                if len(align_segments) <= 1:
                    align_guard_failed = True
            align_lines = [segment.text for segment in align_segments]
            align_payload = "\n".join(align_lines)
            if align_payload:
                if "\t" in align_payload:
                    raise ValueError("对齐文本包含制表符，请检查规范化结果。")
                for part in align_payload.splitlines():
                    if part != part.strip():
                        raise ValueError("对齐文本存在首尾空格，请检查规范化结果。")
            align_path = out_dir / relative.parent / f"{relative.stem}.align.txt"
            align_debug_path = align_path.with_name(f"{align_path.stem}.debug.tsv")
            try:
                with align_path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(align_payload)
                align_written = True
            except OSError as exc:
                LOGGER.warning("写入对齐文本失败: %s", exc)
            if align_written:
                try:
                    _write_align_debug(align_debug_path, align_segments)
                except OSError as exc:
                    LOGGER.warning("写入对齐调试文件失败: %s", exc)
            align_total_lines = len(align_lines)
            LOGGER.info(
                "[align] stem=%s lines=%s split_mode=%s min=%s max=%s hard=%s",
                relative.stem,
                align_total_lines,
                effective_split_mode,
                effective_min_len,
                effective_max_len,
                effective_hard_max,
            )
            if align_guard_attempted and align_guard_failed:
                LOGGER.warning(
                    "[align-guard] stem=%s fallback未能拆分，多句对齐将在 R3 兜底路径处理。",
                    relative.stem,
                )

            if align_written and canonical_rules and align_lines is not None:
                canonical_path = out_dir / relative.parent / f"{relative.stem}.canonical.txt"
                canonical_text, index_map, line_spans = concat_and_index(align_lines, canonical_rules)
                canonical_len = len(canonical_text)
                LOGGER.info(
                    "[canonical] stem=%s canonical_len=%s first50=%s",
                    relative.stem,
                    canonical_len,
                    canonical_text[:50],
                )
                if canonical_len == 0:
                    LOGGER.warning("WARNING: empty canonical for %s", relative.stem)
                try:
                    with canonical_path.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write(canonical_text)
                except OSError as exc:
                    LOGGER.warning("写入 canonical 文本失败: %s", exc)
                # line_spans and index_map are computed for future alignment steps.

    return {
        "stem": relative.stem,
        "file": str(path),
        "orig_len": len(raw_text),
        "norm_len": len(payload_text),
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
        "align_debug_path": str(align_debug_path) if align_written and align_debug_path else "",
        "canonical_path": str(canonical_path) if canonical_path else "",
        "align_total_lines": len(align_lines) if align_lines is not None else 0,
        "split_mode": effective_split_mode,
        "min_len": effective_min_len,
        "max_len": effective_max_len,
        "hard_max": effective_hard_max,
        "weak_punct_enable": bool(effective_weak),
        "keep_quotes": bool(align_keep_quotes),
        "align_guard_triggered": align_guard_attempted,
        "align_guard_failed": align_guard_failed,
        "status": "ok",
        "message": "；".join(message_parts),
    }


def _write_align_debug(path: Path, segments: Sequence[ZhSegment]) -> None:
    """Write a TSV with segmentation spans for quick inspection."""

    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("idx\tstart_char\tend_char\ttext_preview\n")
        for idx, segment in enumerate(segments, 1):
            preview = segment.text.strip()
            if len(preview) > 30:
                preview = preview[:30]
            preview = preview.replace("\t", " ").replace("\n", " ")
            handle.write(f"{idx}\t{segment.start}\t{segment.end}\t{preview}\n")


def run_prep_norm(
    input_path: Path,
    output_dir: Path,
    char_map_path: Path,
    opencc_mode: str,
    glob_pattern: str,
    dry_run: bool,
    collapse_lines: bool,
    emit_align: bool,
    *,
    allow_missing_char_map: bool = False,
    split_mode: str = DEFAULT_ALIGN_SPLIT_MODE,
    align_min_len: int = DEFAULT_ALIGN_MIN_LEN,
    align_max_len: int = DEFAULT_ALIGN_MAX_LEN,
    align_hard_max: int = DEFAULT_ALIGN_HARD_MAX,
    align_weak_punct: bool = True,
    align_keep_quotes: bool = True,
) -> dict:
    """执行规范化批处理并返回统计结果。"""

    if opencc_mode not in {"none", "t2s", "s2t"}:  # 校验 opencc 取值
        raise ValueError("--opencc 仅支持 none/t2s/s2t。")
    try:
        cmap = load_char_map(char_map_path)  # 加载字符映射
    except FileNotFoundError:
        if not allow_missing_char_map:
            raise
        LOGGER.warning("未找到字符映射 %s，继续执行但不做字符映射。", char_map_path)
        cmap = {
            "delete": [],
            "map": {},
            "normalize_width": False,
            "normalize_space": False,
            "preserve_cjk_punct": True,
        }
    canonical_rules = _load_canonical_rules(char_map_path)
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
    total = len(files)
    LOGGER.info("[stage] norm start total=%s", total)
    if collapse_lines:
        LOGGER.info("[normalize] collapse-lines=on (ascii-ascii -> space, others -> join)")
    else:
        LOGGER.info("[normalize] collapse-lines=off (preserve original line breaks)")
    start = time.perf_counter()  # 记录起始时间
    last_progress = start
    processed = 0
    for path in files:  # 遍历每个文件
        row = _process_single_text(
            path,
            base_dir,
            out_dir,
            cmap,
            opencc_mode,
            dry_run,
            collapse_lines,
            emit_align,
            split_mode,
            canonical_rules if emit_align else None,
            align_min_len=align_min_len,
            align_max_len=align_max_len,
            align_hard_max=align_hard_max,
            align_weak_punct=align_weak_punct,
            align_keep_quotes=align_keep_quotes,
        )  # 处理单个文件
        rows.append(row)
        if row.get("status") != "ok":  # 判断成功与否
            failed += 1
            LOGGER.warning("[failed] %s %s", path, row.get("message"))  # 打印失败信息
        else:
            LOGGER.info("[ok] %s", path)  # 打印成功信息
        processed += 1
        last_progress = _progress_tick("norm", processed, total, start, last_progress)
    elapsed = time.perf_counter() - start  # 计算耗时
    LOGGER.info("[stage] norm done elapsed=%.2fs", elapsed)
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
        + (["--collapse-lines"] if args.collapse_lines else ["--no-collapse-lines"])
        + (["--emit-align"] if args.emit_align else [])
        + (["--split-mode", args.split_mode] if args.split_mode != DEFAULT_ALIGN_SPLIT_MODE else [])
        + (["--min-len", str(args.min_len)] if args.min_len != DEFAULT_ALIGN_MIN_LEN else [])
        + (["--max-len", str(args.max_len)] if args.max_len != DEFAULT_ALIGN_MAX_LEN else [])
        + (["--hard-max", str(args.hard_max)] if args.hard_max != DEFAULT_ALIGN_HARD_MAX else [])
        + (["--no-weak-punct-enable"] if not args.weak_punct_enable else [])
        + (["--no-keep-quotes"] if not args.keep_quotes else [])
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
            args.collapse_lines,
            args.emit_align,
            split_mode=args.split_mode,
            align_min_len=args.min_len,
            align_max_len=args.max_len,
            align_hard_max=args.hard_max,
            align_weak_punct=args.weak_punct_enable,
            align_keep_quotes=args.keep_quotes,
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
    source_audio_abs: Optional[str],
    samplerate: Optional[int],
    channels: Optional[int],
    audio_root: Optional[Path],
    *,
    sentence_mode: bool,
    review_only: bool,
    prefer_relative_audio: bool,
    path_style: str,
) -> tuple[dict, EDLWriteResult]:
    """根据处理结果导出字幕、标记与 EDL，并返回写出结果。"""

    out_dir.mkdir(parents=True, exist_ok=True)
    audio_root_str = str(audio_root) if audio_root else None
    edl_result: EDLWriteResult
    if sentence_mode:
        srt_path = out_dir / f"{stem}.sentence.keep.srt"
        txt_path = out_dir / f"{stem}.sentence.keep.txt"
        markers_path = out_dir / f"{stem}.sentence.audition_markers.csv"
        edl_path = out_dir / f"{stem}.sentence.edl.json"
        export_sentence_srt(result.hits, srt_path)
        export_sentence_txt(result.hits, txt_path)
        export_sentence_markers(result.hits, result.review_points, markers_path)
        edl_result = export_sentence_edl_json(
            result.edl_keep_segments,
            result.audio_start,
            result.audio_end,
            edl_path,
            review_only=review_only,
            source_audio_abs=source_audio_abs,
            samplerate=samplerate,
            channels=channels,
            stem=stem,
            source_samplerate=samplerate,
            audio_root=audio_root_str,
            prefer_relative_audio=prefer_relative_audio,
            path_style=path_style,
        )
    else:
        srt_path = out_dir / f"{stem}.keepLast.srt"
        txt_path = out_dir / f"{stem}.keepLast.txt"
        markers_path = out_dir / f"{stem}.keepLast.audition_markers.csv"
        edl_path = out_dir / f"{stem}.keepLast.edl.json"
        export_srt(result.keeps, srt_path)
        export_txt(result.keeps, txt_path)
        export_audition_markers(
            result.keeps,
            markers_path,
            note=result.fallback_marker_note if result.fallback_used else None,
        )
        edl_result = export_edl_json(
            result.edl_keep_segments,
            result.edl_segment_metadata,
            source_audio_abs,
            edl_path,
            stem=stem,
            samplerate=samplerate,
            channels=channels,
            source_samplerate=samplerate,
            audio_root=audio_root_str,
            prefer_relative_audio=prefer_relative_audio,
            path_style=path_style,
            fallback_reason=result.edl_fallback_reason,
            fallback_used=result.edl_fallback,
        )
    outputs = {
        "srt": srt_path,
        "txt": txt_path,
        "markers": markers_path,
        "edl": edl_result.edl_path,
    }
    return outputs, edl_result


def _process_retake_item(
    words_path: Path,
    text_path: Path,
    out_dir: Path,
    source_audio_path: Optional[Path],
    samplerate: Optional[int],
    channels: Optional[int],
    audio_root: Optional[Path],
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
    fast_match: bool,
    max_windows: int,
    match_timeout: float,
    max_distance_ratio: float,
    min_anchor_ngram: int,
    fallback_policy: str,
    compute_timeout_sec: float,
    coarse_threshold: float,
    coarse_len_tolerance: float,
    prefer_relative_audio: bool,
    path_style: str,
    alias_map: Mapping[str, Sequence[str]] | None,
    no_collapse_align: bool,
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
    no_interaction: bool,
    debug_csv: Path | None,
) -> Tuple[str, dict]:
    """处理单个词级 JSON + 文本的组合。"""

    stem = stem_from_words_json(words_path)  # 解析输出前缀
    try:
        doc = load_words(words_path)  # 读取词级 JSON
        words = list(doc)
        audio_path: Path | None = None
        source_audio_name: str | None = None
        source_audio_abs: Path | None = None
        if source_audio_path is not None:
            candidate = source_audio_path
            if not candidate.is_absolute():
                base_dir = materials_base or words_path.parent
                candidate = (base_dir / candidate).expanduser()
            else:
                candidate = candidate.expanduser()
            try:
                resolved_candidate = candidate.resolve()
            except OSError:
                resolved_candidate = candidate
            source_audio_abs = resolved_candidate
            source_audio_name = resolved_candidate.name
            if resolved_candidate.exists():
                audio_path = resolved_candidate
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
                    no_collapse_align=no_collapse_align,
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
                alias_map=alias_map,
                debug_label=stem,
                fast_match=fast_match,
                max_windows=max_windows,
                match_timeout=match_timeout,
                max_distance_ratio=max_distance_ratio,
                min_anchor_ngram=min_anchor_ngram,
                fallback_policy=fallback_policy,
                compute_timeout_sec=compute_timeout_sec,
                coarse_threshold=coarse_threshold,
                coarse_len_tolerance=coarse_len_tolerance,
                no_collapse_align=no_collapse_align,
            )

        def _load_context_preview() -> str:
            try:
                raw = text_path.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                return ""
            return _summarize_context(raw)

        try:
            result = _run_compute(
                current_min_sent,
                current_line_gap,
                current_sentence_gap,
                current_pause_gap,
            )
        except Exception as exc:
            context_preview = _load_context_preview()
            LOGGER.exception(
                "保留最后一遍处理失败: stem=%s text=%s 上下文=%s",
                stem,
                text_path,
                context_preview,
            )
            base_dir = materials_base or text_path.parent
            item = {
                "stem": stem,
                "words_json": safe_rel(base_dir, words_path) if base_dir else str(words_path),
                "text": safe_rel(base_dir, text_path) if base_dir else str(text_path),
                "outputs": {},
                "stats": {},
                "status": "failed",
                "message": f"处理失败: {exc}; ctx={context_preview}",
            }
            return stem, item
        stats = dict(result.stats)
        cut_ratio = float(stats.get("cut_ratio", 0.0))
        action = "none"
        if overcut_guard and stats.get("audio_duration", 0.0) > 0 and cut_ratio > overcut_threshold:
            if overcut_mode == "auto":
                action = "auto"
            elif overcut_mode == "abort":
                action = "abort"
            else:
                if no_interaction or not sys.stdin.isatty():
                    reason = "--no-interaction 模式下" if no_interaction else "非交互环境下"
                    LOGGER.warning(
                        "剪切比例 %.2f 超过阈值 %.2f，%s自动选择 auto。",
                        cut_ratio,
                        overcut_threshold,
                        reason,
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
                LOGGER.info(
                    "自动调参后参数: min_sent=%s line_gap=%.2f sentence_gap=%.2f pause_gap=%.2f",
                    current_min_sent,
                    current_line_gap,
                    current_sentence_gap,
                    current_pause_gap,
                )
            elif action == "abort":
                raise RuntimeError(
                    f"剪切比例 {cut_ratio:.2%} 超过阈值 {overcut_threshold:.2%}，已根据设置中止。"
                )
        stats["overcut_guard_action"] = action
        stats["review_only"] = bool(review_only)
        stats["sentence_strict"] = bool(sentence_strict)
        stats["pause_gap_final"] = current_pause_gap
        stats["min_sent_final"] = current_min_sent
        stats["line_gap_final"] = current_line_gap
        stats["sentence_gap_final"] = current_sentence_gap

        source_audio_abs_str = str(source_audio_abs) if source_audio_abs else None
        outputs, edl_result = _export_retake_outputs(
            result,
            stem,
            out_dir,
            source_audio_abs_str,
            samplerate,
            channels,
            audio_root,
            sentence_mode=sentence_strict,
            review_only=review_only if sentence_strict else False,
            prefer_relative_audio=prefer_relative_audio,
            path_style=path_style,
        )

        LOGGER.info(
            "停顿吸附=%s 吸附次数=%s 自动合并=%s 丢弃碎片=%s 剪切比例=%.3f",
            stats.get("pause_used"),
            stats.get("pause_snaps", 0),
            stats.get("auto_merged", 0),
            stats.get("too_short_dropped", 0),
            cut_ratio,
        )

        resolved_audio_path = edl_result.source_audio_abs or source_audio_abs_str
        if resolved_audio_path:
            try:
                resolved_path = Path(resolved_audio_path)
                exists_flag = resolved_path.exists()
            except OSError:
                exists_flag = False
            if exists_flag:
                LOGGER.info("source_audio 指向：%s（已验证存在）", resolved_audio_path)
            else:
                LOGGER.warning("source_audio 指向：%s（未找到）", resolved_audio_path)
        else:
            LOGGER.warning("source_audio 指向：无（未提供 source_audio 字段）")

        if debug_csv is not None and result.debug_rows:
            mode = "sentence" if sentence_strict else "line"
            _append_debug_rows(debug_csv, result.debug_rows, mode)

        stats["text_variant"] = text_path.name  # 记录使用的文本文件
        stats["alias_map_used"] = bool(alias_map)
        if result.fallback_used:
            stats["fallback_message"] = result.fallback_reason or "keep_full_audio"
        if result.unmatched_samples:
            stats.setdefault("unmatched_samples", result.unmatched_samples)
        stats["source_audio_resolved_path"] = resolved_audio_path or ""
        if source_audio_name:
            stats["source_audio_name"] = source_audio_name
        if edl_result.source_audio is not None:
            stats["source_audio_written"] = edl_result.source_audio
        item = {
            "stem": stem,
            "words_json": safe_rel(materials_base or words_path.parent, words_path),
            "text": safe_rel(materials_base or text_path.parent, text_path),
            "outputs": {key: safe_rel(out_base, value) for key, value in outputs.items()},
            "source_audio_written": edl_result.source_audio,
            "source_audio_abs": edl_result.source_audio_abs or source_audio_abs_str,
            "audio_root": str(audio_root) if audio_root else "",
            "stats": stats,
            "status": "ok",
            "message": (
                f"fallback:{result.fallback_reason or 'keep_full_audio'}"
                if result.fallback_used
                else "处理成功"
            ),
            "edl_segments_count": int(stats.get("edl_segments_count", len(result.edl_keep_segments))),
            "edl_fallback": bool(result.edl_fallback),
            "source_audio_resolved_path": resolved_audio_path or "",
            "unmatched_samples": list(
                (result.unmatched_samples or stats.get("unmatched_samples", []))[:5]
            ),
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
            "audio_root": str(audio_root) if audio_root else "",
            "source_audio_written": None,
            "source_audio_abs": None,
        }
    return stem, item  # 返回 stem 及结果条目


def _build_retake_stats_from_item(item: dict) -> RetakeStats:
    """提炼单条 retake 结果中的关键统计。"""

    stats = item.get("stats") or {}
    total = int(
        stats.get("total_lines")
        or stats.get("total_sentences")
        or stats.get("keep_span_count")
        or 0
    )
    kept = int(
        stats.get("matched_lines")
        or stats.get("matched_sentences")
        or stats.get("keep_span_count")
        or 0
    )
    unaligned = int(stats.get("unmatched_lines") or stats.get("unmatched_sentences") or 0)
    dedup_window = int(stats.get("len_gate_skipped", 0)) + int(stats.get("neighbor_gap_skipped", 0))
    dedup_window += int(stats.get("max_window_splits", 0))
    audio_duration = float(stats.get("audio_duration", 0.0))
    keep_duration = float(stats.get("keep_duration", 0.0))
    cut_seconds = max(0.0, audio_duration - keep_duration)
    return RetakeStats(
        stem=str(item.get("stem", "")),
        total=total,
        kept=kept,
        unaligned=unaligned,
        dedup_window=dedup_window,
        cut_seconds=cut_seconds,
    )


def _run_retake_batch(
    materials_dir: Path,
    out_dir: Path,
    glob_words: list[str],
    text_patterns: list[str],
    audio_root: Optional[Path],
    workers: Optional[int],
    min_sent_chars: int,
    line_max_dup_gap_sec: float,
    sentence_max_dup_gap_sec: float,
    max_window_sec: float,
    sentence_strict: bool,
    review_only: bool,
    merge_adj_gap_sec: float,
    low_conf: float,
    fast_match: bool,
    max_windows: int,
    match_timeout: float,
    max_distance_ratio: float,
    min_anchor_ngram: int,
    fallback_policy: str,
    compute_timeout_sec: float,
    coarse_threshold: float,
    coarse_len_tolerance: float,
    prefer_relative_audio: bool,
    path_style: str,
    alias_map: Mapping[str, Sequence[str]] | None,
    no_collapse_align: bool,
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
    no_interaction: bool,
    debug_csv: Path | None,
) -> dict:
    """执行目录批处理的配对与导出。"""

    words_files = iter_files(materials_dir, glob_words)  # 收集所有 JSON
    if not words_files:  # 未找到文件
        return {"items": [], "summary": {"total": 0, "ok": 0, "failed": 0, "elapsed_seconds": 0.0}}
    total = len(words_files)  # 总任务数
    LOGGER.info("[stage] retake start total=%s", total)
    start = time.perf_counter()  # 记录耗时
    items: list[dict] = []  # 存储处理结果
    failed = 0  # 统计失败数
    executor: ProcessPoolExecutor | None = None  # 进程池引用
    futures = []  # 并发任务列表
    processed = 0
    last_progress = start
    audio_root_effective = audio_root or materials_dir
    try:
        if workers and workers > 1:  # 并发模式
            executor = ProcessPoolExecutor(max_workers=workers)  # 构建进程池
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
                        "audio_root": str(audio_root_effective),
                        "source_audio_written": None,
                        "source_audio_abs": None,
                    }
                    failed += 1
                    items.append(item)
                    processed += 1
                    last_progress = _progress_tick("retake", processed, total, start, last_progress)
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
                        audio_root_effective,
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
                        fast_match,
                        max_windows,
                        match_timeout,
                        max_distance_ratio,
                        min_anchor_ngram,
                        fallback_policy,
                        compute_timeout_sec,
                        coarse_threshold,
                        coarse_len_tolerance,
                        prefer_relative_audio,
                        path_style,
                        alias_map,
                        no_collapse_align,
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
                        no_interaction=no_interaction,
                        debug_csv=debug_csv,
                    )
                )  # 提交任务
            for future in as_completed(futures):  # 收集结果
                _, item = future.result()
                items.append(item)
                if item["status"] != "ok":  # 统计失败
                    failed += 1
                processed += 1
                last_progress = _progress_tick("retake", processed, total, start, last_progress)
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
                            "audio_root": str(audio_root_effective),
                            "source_audio_written": None,
                            "source_audio_abs": None,
                        }
                    )
                    failed += 1
                    processed += 1
                    last_progress = _progress_tick("retake", processed, total, start, last_progress)
                    continue
                _, item = _process_retake_item(
                    words_path,
                    text_path,
                    out_dir,
                    None,
                    None,
                    None,
                    audio_root_effective,
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
                    fast_match,
                    max_windows,
                    match_timeout,
                    max_distance_ratio,
                    min_anchor_ngram,
                    fallback_policy,
                    compute_timeout_sec,
                    coarse_threshold,
                    coarse_len_tolerance,
                    prefer_relative_audio,
                    path_style,
                    alias_map,
                    no_collapse_align,
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
                    no_interaction=no_interaction,
                    debug_csv=debug_csv,
                )  # 直接处理
                items.append(item)
                if item["status"] != "ok":  # 更新失败计数
                    failed += 1
                processed += 1
                last_progress = _progress_tick("retake", processed, total, start, last_progress)
    finally:
        if executor:  # 清理线程池
            executor.shutdown()
    elapsed = time.perf_counter() - start  # 计算耗时
    LOGGER.info("[stage] retake done elapsed=%.2fs", elapsed)
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
    fast_match = bool(args.fast_match)
    max_windows = int(args.max_windows)
    match_timeout = float(args.match_timeout)
    compute_timeout_sec = float(args.compute_timeout_sec)
    max_distance_ratio = float(args.max_distance_ratio)
    min_anchor_ngram = int(args.min_anchor_ngram)
    coarse_threshold = float(args.coarse_threshold)
    coarse_len_tolerance = float(args.coarse_len_tol)
    fallback_policy = args.fallback_policy
    debug_csv = Path(args.debug_csv).expanduser() if args.debug_csv else None
    if debug_csv and args.workers and args.workers > 1:
        LOGGER.warning("检测到并发执行，已禁用 debug CSV 以避免文件竞争。")
        debug_csv = None
    no_interaction = bool(getattr(args, "no_interaction", False))
    prefer_relative_audio = bool(getattr(args, "prefer_relative_audio", True))
    path_style = str(getattr(args, "path_style", "auto")).lower()
    if path_style not in {"auto", "posix", "windows"}:
        raise ValueError("--path-style 仅支持 auto/posix/windows")
    alias_map = load_alias_map(Path(getattr(args, "alias_map", DEFAULT_ALIAS_MAP)))
    no_collapse_align = bool(getattr(args, "no_collapse_align", True))
    if args.audio_root:
        audio_root_path = Path(args.audio_root).expanduser().resolve()
    elif args.words_json:
        audio_root_path = Path(args.words_json).expanduser().resolve().parent
    else:
        audio_root_path = Path(args.materials).expanduser().resolve()
    if args.words_json:  # 单文件模式
        if not args.text:
            raise ValueError("单文件模式需要同时提供 --text")
        LOGGER.info("[stage] retake start total=1")
        single_start = time.perf_counter()
        source_audio_path = Path(args.source_audio).expanduser() if args.source_audio else None
        _, item = _process_retake_item(
            Path(args.words_json),
            Path(args.text),
            out_dir,
            source_audio_path,
            args.samplerate,
            args.channels,
            audio_root_path,
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
            fast_match,
            max_windows,
            match_timeout,
            max_distance_ratio,
            min_anchor_ngram,
            fallback_policy,
            compute_timeout_sec,
            coarse_threshold,
            coarse_len_tolerance,
            prefer_relative_audio,
            path_style,
            alias_map,
            no_collapse_align,
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
            no_interaction=no_interaction,
            debug_csv=debug_csv,
        )
        items = [item]
        failed = 0 if item["status"] == "ok" else 1
        _progress_tick("retake", 1, 1, single_start, single_start)
        elapsed_single = time.perf_counter() - single_start
        LOGGER.info("[stage] retake done elapsed=%.2fs", elapsed_single)
        summary = {"total": 1, "ok": 1 - failed, "failed": failed, "elapsed_seconds": elapsed_single}
    elif args.text:  # 仅给出文本但缺少 JSON
        raise ValueError("单文件模式必须提供 --words-json")
    else:  # 目录批处理
        text_patterns = [_casefold_pattern(pattern) for pattern in args.glob_text]
        glob_words_pattern = parse_glob_list(args.glob_words)
        materials_dir = Path(args.materials).expanduser().resolve()
        result = _run_retake_batch(
            materials_dir,
            out_dir,
            glob_words_pattern,
            text_patterns,
            audio_root_path,
            args.workers,
            args.min_sent_chars,
            line_max_dup_gap_sec,
            sentence_max_dup_gap_sec,
            args.max_window_sec,
            args.sentence_strict,
            args.review_only,
            merge_adj_gap_sec,
            low_conf,
            fast_match,
            max_windows,
            match_timeout,
            max_distance_ratio,
            min_anchor_ngram,
            fallback_policy,
            compute_timeout_sec,
            coarse_threshold,
            coarse_len_tolerance,
            prefer_relative_audio,
            path_style,
            alias_map,
            no_collapse_align,
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
            no_interaction,
            debug_csv,
        )
        items = result["items"]
        summary = result["summary"]
        failed = summary.get("failed", 0)
    payload = {"items": items, "summary": summary}
    payload["audio_root"] = str(audio_root_path)
    payload["prefer_relative_audio"] = prefer_relative_audio
    payload["path_style"] = path_style
    retake_stats = [_build_retake_stats_from_item(item) for item in items]
    payload["retake_stats"] = [asdict(stat) for stat in retake_stats]
    stat_keys = [
        "total_words",
        "total_lines",
        "align_line_count_read",
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
        "kept_count",
        "deleted_count",
        "cut_seconds",
        "keep_duration",
        "coarse_total",
        "coarse_passed",
        "fine_evaluated",
        "pruned_candidates",
        "search_elapsed_sec",
        "segment_count",
        "fallback_used",
        "timeout_fallback",
        "elapsed_sec",
    ]  # 汇总字段
    float_keys = {"cut_seconds", "search_elapsed_sec", "elapsed_sec", "keep_duration"}
    aggregated = {}
    for key in stat_keys:
        if key in float_keys:
            aggregated[key] = sum(
                float(item.get("stats", {}).get(key, 0.0))
                for item in items
                if item.get("status") == "ok"
            )
        else:
            aggregated[key] = sum(
                int(item.get("stats", {}).get(key, 0))
                for item in items
                if item.get("status") == "ok"
            )
    summary["aggregated_stats"] = aggregated
    if write_report:  # 写入批处理报告
        existing = {}
        if report_path.exists():
            existing = json.loads(report_path.read_text(encoding="utf-8-sig", errors="replace"))
        params_section = existing.setdefault("params", {})
        params_section["no_collapse_align"] = bool(args.no_collapse_align)
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
    parts.extend(["--alias-map", str(Path(args.alias_map))])
    if args.audio_root:
        parts.extend(["--audio-root", str(Path(args.audio_root))])
    if not args.prefer_relative_audio:
        parts.append("--no-prefer-relative-audio")
    if args.path_style != "auto":
        parts.extend(["--path-style", args.path_style])
    parts.append("--collapse-lines" if args.collapse_lines else "--no-collapse-lines")
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
    if getattr(args, "no_interaction", False):
        parts.append("--no-interaction")
    parts.append("--no-collapse-align" if args.no_collapse_align else "--collapse-align")
    parts.append("--fast-match" if args.fast_match else "--no-fast-match")
    parts.extend(["--max-windows", str(args.max_windows)])
    parts.extend(["--match-timeout", str(args.match_timeout)])
    parts.extend(["--compute-timeout-sec", str(args.compute_timeout_sec)])
    parts.extend(["--max-distance-ratio", str(args.max_distance_ratio)])
    parts.extend(["--min-anchor-ngram", str(args.min_anchor_ngram)])
    parts.extend(["--coarse-threshold", str(args.coarse_threshold)])
    parts.extend(["--coarse-len-tol", str(args.coarse_len_tol)])
    parts.extend(["--fallback-policy", args.fallback_policy])
    LOGGER.info("开始保留最后一遍任务: 输入=%s 文本=%s 输出=%s", args.words_json or args.materials, args.text, args.out)
    LOGGER.info("等价命令: %s", _build_cli_example("retake-keep-last", parts))

    if args.audio_root:
        snapshot_audio_root = str(Path(args.audio_root))
    elif args.words_json:
        snapshot_audio_root = str(Path(args.words_json).expanduser().resolve().parent)
    else:
        snapshot_audio_root = str(Path(args.materials).expanduser().resolve())
    snapshot = {
        "mode": "single" if args.words_json else "batch",
        "audio_root": snapshot_audio_root,
        "prefer_relative_audio": args.prefer_relative_audio,
        "path_style": args.path_style,
        "alias_map": str(args.alias_map),
        "no_collapse_align": args.no_collapse_align,
        "fast_match": args.fast_match,
        "workers": args.workers,
        "max_windows": args.max_windows,
        "match_timeout": args.match_timeout,
        "compute_timeout_sec": args.compute_timeout_sec,
        "fallback_policy": args.fallback_policy,
        "coarse_threshold": args.coarse_threshold,
        "coarse_len_tol": args.coarse_len_tol,
    }
    LOGGER.info(_safe_text(f"参数快照: {json.dumps(snapshot, ensure_ascii=False)}"))

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
        stem_hint = edl.stem or _stem_from_edl_path(str(edl_path))
        source_hint = edl.source_audio or ""
        LOGGER.info(
            "[render] resolve audio: stem=%s source_audio=%s audio_root=%s",
            stem_hint,
            source_hint,
            audio_root,
        )
        source_audio = resolve_source_audio(edl, edl_path, audio_root, strict=False)  # 定位源音频
        if source_audio is None:
            LOGGER.warning(
                "未定位到源音频，跳过渲染: stem=%s source_audio=%s audio_root=%s",
                stem_hint,
                source_hint,
                audio_root,
            )
            return {
                "edl": safe_rel(edl_path.parent, edl_path),
                "source_audio": source_hint,
                "output": "",
                "stats": {},
                "status": "skipped",
                "render_skipped_reason": "audio_not_found",
                "message": "未定位到源音频，已跳过渲染",
                "source_audio_written": edl.source_audio,
            }
        LOGGER.info(
            "[render] resolve audio: stem=%s source_audio=%s audio_root=%s resolved=%s",
            stem_hint,
            source_hint,
            audio_root,
            source_audio,
        )
        if not edl.segments:
            LOGGER.warning("EDL 无有效片段，已跳过渲染: %s", edl_path)
            return {
                "edl": safe_rel(edl_path.parent, edl_path),
                "source_audio": safe_rel(audio_root, source_audio),
                "output": "",
                "stats": {},
                "status": "skipped",
                "render_skipped_reason": "no_segments",
                "message": "EDL 无有效片段，已跳过渲染",
                "source_audio_written": edl.source_audio,
            }
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
            edl_doc=edl,
            source_audio_path=source_audio,
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
            "source_audio_written": edl.source_audio,
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
            "render_skipped_reason": "error",
            "source_audio_written": edl.source_audio if 'edl' in locals() else None,
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
        LOGGER.info("[stage] render start total=1")
        single_start = time.perf_counter()
        items = [
            _process_render_item(Path(args.edl), audio_root, out_dir, samplerate, channels)
        ]
        summary = {
            "total": 1,
            "ok": 1 if items[0]["status"] == "ok" else 0,
            "failed": 0 if items[0]["status"] == "ok" else 1,
            "elapsed_seconds": time.perf_counter() - single_start,
        }
        _progress_tick("render", 1, 1, single_start, single_start)
        LOGGER.info("[stage] render done elapsed=%.2fs", summary["elapsed_seconds"])
    else:  # 批处理模式
        edl_files = iter_files(Path(args.materials), list(args.glob_edl))  # 搜索 EDL
        if not edl_files:
            return {"items": [], "summary": {"total": 0, "ok": 0, "failed": 0, "elapsed_seconds": 0.0}}
        total = len(edl_files)
        LOGGER.info("[stage] render start total=%s", total)
        start = time.perf_counter()  # 记录耗时
        items = []
        failed = 0
        workers = args.workers or 1
        processed = 0
        last_progress = start
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
                    processed += 1
                    last_progress = _progress_tick("render", processed, total, start, last_progress)
        else:  # 串行执行
            for edl_path in edl_files:
                item = _process_render_item(edl_path, audio_root, out_dir, samplerate, channels)
                items.append(item)
                if item["status"] != "ok":
                    failed += 1
                processed += 1
                last_progress = _progress_tick("render", processed, total, start, last_progress)
        elapsed = time.perf_counter() - start  # 计算耗时
        LOGGER.info("[stage] render done elapsed=%.2fs", elapsed)
        items.sort(key=lambda item: item.get("edl", ""))  # 按 EDL 名称排序
        summary = {"total": total, "ok": total - failed, "failed": failed, "elapsed_seconds": elapsed}
    payload = {"items": items, "summary": summary}
    payload["audio_root"] = str(audio_root)
    summary["aggregated_stats"] = {
        "segments": sum(int(item.get("stats", {}).get("segments", 0)) for item in items if item.get("status") == "ok"),
        "keep_duration": sum(float(item.get("stats", {}).get("keep_duration", 0.0)) for item in items if item.get("status") == "ok"),
    }
    if write_report:
        existing = {}
        if report_path.exists():
            existing = json.loads(report_path.read_text(encoding="utf-8-sig", errors="replace"))
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


def handle_serve_web(args: argparse.Namespace) -> int:
    """处理 serve-web 子命令，启动本地可视化控制台。"""

    out_dir = Path(args.out).expanduser().resolve()
    manifest_path = out_dir / "manifest.json"
    stems = []
    proc, url = _launch_static_ui(
        out_dir,
        stems=stems,
        serve_host=args.host,
        serve_port=args.port,
        open_browser=args.open_browser,
        manifest_path=manifest_path if manifest_path.exists() else None,
    )
    if proc is None or url is None:
        return 1
    LOGGER.info("按 Ctrl+C 停止服务。日志将打印在子进程中。")
    try:
        proc.wait()
    except KeyboardInterrupt:
        LOGGER.info("收到中断信号，正在停止 Web UI。")
    finally:
        if proc.poll() is None:
            proc.terminate()
    return 0


def run_all_in_one(args: argparse.Namespace) -> dict:
    """执行 all-in-one 流水线。"""

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "batch_report.json"
    no_collapse_align = bool(getattr(args, "no_collapse_align", True))
    report: dict[str, dict] = {"params": {"no_collapse_align": no_collapse_align}}
    stage_summary: dict[str, dict] = {}
    pipeline_records: list[dict] = []
    start = time.perf_counter()

    audio_root_arg = getattr(args, "audio_root", None)
    if audio_root_arg:
        audio_root_path = Path(audio_root_arg).expanduser().resolve()
    else:
        audio_root_path = input_dir
    prefer_relative_audio = bool(getattr(args, "prefer_relative_audio", True))
    path_style = str(getattr(args, "path_style", "auto")).lower()
    if path_style not in {"auto", "posix", "windows"}:
        raise ValueError("--path-style 仅支持 auto/posix/windows")

    alias_map_path = Path(getattr(args, "alias_map", DEFAULT_ALIAS_MAP)).expanduser()
    alias_map = load_alias_map(alias_map_path)
    report["alias_map"] = str(alias_map_path)

    norm_pattern = _casefold_pattern(args.norm_glob)
    norm_out_dir = out_dir / "norm"
    split_mode = getattr(args, "split_mode", DEFAULT_ALIGN_SPLIT_MODE)
    align_min_len = int(getattr(args, "min_len", DEFAULT_ALIGN_MIN_LEN))
    align_max_len = int(getattr(args, "max_len", DEFAULT_ALIGN_MAX_LEN))
    align_hard_max = int(getattr(args, "hard_max", DEFAULT_ALIGN_HARD_MAX))
    align_weak = bool(getattr(args, "weak_punct_enable", True))
    align_keep = bool(getattr(args, "keep_quotes", True))
    LOGGER.info(_safe_text(f"开始规范化文本 → {norm_out_dir}"))
    norm_result = run_prep_norm(
        input_dir,
        norm_out_dir,
        Path(args.char_map),
        args.opencc,
        norm_pattern,
        dry_run=False,
        collapse_lines=args.collapse_lines,
        emit_align=args.emit_align,
        allow_missing_char_map=True,
        split_mode=split_mode,
        align_min_len=align_min_len,
        align_max_len=align_max_len,
        align_hard_max=align_hard_max,
        align_weak_punct=align_weak,
        align_keep_quotes=align_keep,
    )
    report["prep_norm"] = norm_result
    stage_summary["prep_norm"] = norm_result.get("summary", {})

    parsed_words = parse_glob_list(args.glob_words)
    parsed_audio = parse_glob_list(args.glob_audio)
    LOGGER.info("解析后的词级匹配模式: %s", parsed_words or [args.glob_words])
    LOGGER.info("解析后的音频匹配模式: %s", parsed_audio or [args.glob_audio])

    retake_text_patterns = _derive_retake_text_patterns([args.norm_glob])
    kits = match_materials(
        input_dir,
        norm_out_dir,
        retake_text_patterns,
        args.glob_words,
        args.glob_audio,
    )
    kit_count = len(kits)
    audio_kits = sum(1 for kit in kits if kit.audio)
    LOGGER.info("素材扫描完成: 共 %s 套，其中 %s 套包含音频。", kit_count, audio_kits)

    def _flag(value: object) -> str:
        return "Y" if value else "-"

    for kit in kits:
        LOGGER.info(
            "[kit] %s | text=%s words=%s audio=%s",
            kit.stem,
            _flag(kit.best_text()),
            _flag(kit.words),
            _flag(kit.audio),
        )

    if not kits:
        LOGGER.warning("未匹配到任何素材套件，请检查目录结构或 glob 配置。")

    missing_words = [kit for kit in kits if not kit.words]
    if missing_words:
        LOGGER.warning(
            "检测到 %s 套素材缺少词级 JSON（支持 *.words.json;*.json），请确认文件名。",
            len(missing_words),
        )

    fast_match = bool(getattr(args, "fast_match", True))
    max_windows = int(getattr(args, "max_windows", 50))
    match_timeout = float(getattr(args, "match_timeout", 20.0))
    max_distance_ratio = float(getattr(args, "max_distance_ratio", 0.25))
    min_anchor_ngram = int(getattr(args, "min_anchor_ngram", 8))
    fallback_policy = getattr(args, "fallback_policy", "greedy")
    compute_timeout_sec = float(getattr(args, "compute_timeout_sec", 300.0))
    coarse_threshold = float(getattr(args, "coarse_threshold", 0.30))
    coarse_len_tolerance = float(
        getattr(args, "coarse_len_tol", getattr(args, "coarse_len_tolerance", 0.35))
    )

    retake_items: list[dict] = []
    failed = 0
    retake_start = time.perf_counter()
    for kit in kits:
        stem = kit.stem
        words_path = kit.words
        text_path = kit.best_text()
        if words_path is None:
            retake_items.append(
                {
                    "stem": stem,
                    "words_json": "",
                    "text": safe_rel(norm_out_dir, text_path) if text_path else "",
                    "outputs": {},
                    "stats": {},
                    "status": "failed",
                    "message": "未找到匹配的词级 JSON",
                    "audio_root": str(audio_root_path),
                    "source_audio_written": None,
                    "source_audio_abs": None,
                }
            )
            failed += 1
            continue
        if text_path is None:
            retake_items.append(
                {
                    "stem": stem,
                    "words_json": safe_rel(input_dir, words_path),
                    "text": "",
                    "outputs": {},
                    "stats": {},
                    "status": "failed",
                    "message": "未找到匹配的 TXT/.norm/.align 文件",
                    "audio_root": str(audio_root_path),
                    "source_audio_written": None,
                    "source_audio_abs": None,
                }
            )
            failed += 1
            continue
        _, item = _process_retake_item(
            words_path,
            text_path,
            out_dir,
            kit.audio,
            None,
            None,
            audio_root_path,
            input_dir,
            out_dir,
            MIN_SENT_CHARS,
            LINE_MAX_DUP_GAP_SEC,
            SENT_MAX_DUP_GAP_SEC,
            MAX_WINDOW_SEC,
            False,
            False,
            MERGE_ADJ_GAP_SEC,
            SENT_LOW_CONF,
            fast_match,
            max_windows,
            match_timeout,
            max_distance_ratio,
            min_anchor_ngram,
            fallback_policy,
            compute_timeout_sec,
            coarse_threshold,
            coarse_len_tolerance,
            prefer_relative_audio,
            path_style,
            alias_map,
            no_collapse_align,
            pause_align=True,
            pause_gap_sec=PAUSE_GAP_SEC,
            pause_snap_limit=PAUSE_SNAP_LIMIT,
            pad_before=PAD_BEFORE,
            pad_after=PAD_AFTER,
            min_segment_sec=MIN_SEGMENT_SEC,
            merge_gap_sec=MERGE_GAP_SEC,
            silence_probe_enabled=True,
            noise_db=-35,
            silence_min_d=0.28,
            overcut_guard=True,
            overcut_mode="ask",
            overcut_threshold=0.60,
            no_interaction=args.no_interaction,
            debug_csv=None,
        )
        retake_items.append(item)
        if item.get("status") != "ok":
            failed += 1
    retake_elapsed = time.perf_counter() - retake_start
    retake_items.sort(key=lambda entry: entry.get("stem", ""))
    total_items = len(retake_items)
    success_items = total_items - failed
    retake_summary = {
        "total": total_items,
        "ok": success_items,
        "failed": failed,
        "elapsed_seconds": retake_elapsed,
    }
    retake_result = {"items": retake_items, "summary": retake_summary, "retake_stats": []}
    retake_result["audio_root"] = str(audio_root_path)
    retake_result["prefer_relative_audio"] = prefer_relative_audio
    retake_result["path_style"] = path_style
    report["retake_keep_last"] = retake_result
    stage_summary["retake_keep_last"] = retake_summary
    LOGGER.info("[stage] retake done elapsed=%.2fs", retake_elapsed)

    items = retake_result.get("items", [])
    total_items = len(items)
    success_items = sum(1 for item in items if item.get("status") == "ok")
    stems = [item.get("stem", "") for item in items]
    retake_stats_entries: list[RetakeStats] = []
    for entry in retake_result.get("retake_stats", []):
        try:
            retake_stats_entries.append(RetakeStats(**entry))
        except TypeError:
            LOGGER.debug("跳过无法解析的 retake 统计: %s", entry)
    stats_by_stem: dict[str, RetakeStats] = {
        stat.stem: stat for stat in retake_stats_entries if stat.stem
    }
    for item in items:
        stem = item.get("stem", "")
        if stem and stem not in stats_by_stem:
            stats_by_stem[stem] = _build_retake_stats_from_item(item)
    ordered_stats: list[RetakeStats] = []
    seen_stems: set[str] = set()
    for stat in retake_stats_entries:
        if stat.stem and stat.stem not in seen_stems:
            ordered_stats.append(stat)
            seen_stems.add(stat.stem)
    for item in items:
        stem = item.get("stem", "")
        if stem and stem not in seen_stems:
            fallback = stats_by_stem.get(stem)
            if fallback:
                ordered_stats.append(fallback)
                seen_stems.add(stem)

    audio_flags = {kit.stem.lower(): bool(kit.audio) for kit in kits}
    seen_no_audio: set[str] = set()
    for stem in stems:
        if not stem:
            continue
        stem_key = stem.lower()
        if not audio_flags.get(stem_key, False) and stem_key not in seen_no_audio:
            LOGGER.info("[no-audio] stem=%s", stem)
            seen_no_audio.add(stem_key)

    render_mode = args.render_mode
    if render_mode == "yes":
        render_mode = "always"
    elif render_mode == "no":
        render_mode = "never"
    render_payload: dict | None = None
    render_summary: dict[str, dict] = {}
    render_skipped_no_audio = False
    render_error = ""
    if render_mode == "never":
        LOGGER.info("渲染阶段已禁用，跳过。")
        render_payload = {
            "items": [],
            "summary": {"total": 0, "ok": 0, "failed": 0, "elapsed_seconds": 0.0},
            "status": "skipped(manual)",
            "audio_root": str(audio_root_path),
            "render_skipped_reason": "render_disabled",
        }
        report["render_audio"] = render_payload
        stage_summary["render_audio"] = render_payload["summary"]
    else:
        any_audio = any(audio_flags.get(stem.lower(), False) for stem in stems)
        if render_mode == "auto" and not any_audio:
            render_skipped_no_audio = True
            LOGGER.info("未发现音频文件，已跳过渲染，仅导出标记/字幕产物。")
            render_payload = {
                "items": [],
                "summary": {"total": 0, "ok": 0, "failed": 0, "elapsed_seconds": 0.0},
                "status": "skipped(no-audio)",
                "audio_root": str(audio_root_path),
                "render_skipped_reason": "no_audio_detected",
            }
            report["render_audio"] = render_payload
            stage_summary["render_audio"] = render_payload["summary"]
        else:
            LOGGER.info(_safe_text(f"开始渲染音频 → {out_dir}"))
            render_namespace = argparse.Namespace(
                edl=None,
                materials=str(out_dir),
                glob_edl=_expand_case_insensitive_patterns(
                    ["*.keepLast.edl.json", "*.sentence.edl.json"]
                ),
                workers=args.workers,
                audio_root=str(audio_root_path),
                out=str(out_dir),
                samplerate=None,
                channels=None,
            )
            try:
                render_payload = run_render_audio(render_namespace, report_path=report_path, write_report=False)
            except Exception as exc:  # pragma: no cover - 渲染异常需要容错
                LOGGER.exception("渲染阶段出现异常")
                render_payload = {
                    "items": [],
                    "summary": {"total": 0, "ok": 0, "failed": 0, "elapsed_seconds": 0.0},
                    "error": str(exc),
                    "audio_root": str(audio_root_path),
                    "render_skipped_reason": "error",
                }
            report["render_audio"] = render_payload
            stage_summary["render_audio"] = render_payload.get("summary", {})
            render_error = str(render_payload.get("error", ""))
            for entry in render_payload.get("items", []):
                stem = _stem_from_edl_path(entry.get("edl", "")).lower()
                render_summary[stem] = entry

    pipeline_records: list[dict] = []
    for item in items:
        stem = item.get("stem", "")
        stats = stats_by_stem.get(stem) or _build_retake_stats_from_item(item)
        has_audio = audio_flags.get(stem.lower(), False)
        stats_map = item.get("stats", {}) or {}
        render_note = _resolve_render_status(
            stem,
            render_mode=render_mode,
            has_audio=has_audio,
            render_skipped_no_audio=render_skipped_no_audio,
            render_summary=render_summary,
            global_error=render_error,
        )
        render_entry = render_summary.get(stem.lower())
        render_reason = render_entry.get("render_skipped_reason") if render_entry else None
        if render_reason is None:
            if render_mode == "never":
                render_reason = "render_disabled"
            elif render_skipped_no_audio:
                render_reason = "no_audio_detected"
        record = {
            "stem": stem,
            "status": item.get("status"),
            "message": item.get("message", ""),
            "total": stats.total,
            "kept": stats.kept,
            "unaligned": stats.unaligned,
            "window": stats.dedup_window,
            "cut": stats.cut_seconds,
            "render": render_note,
            "source_audio_written": item.get("source_audio_written"),
            "audio_root": item.get("audio_root") or str(audio_root_path),
            "render_skipped_reason": render_reason,
            "match_engine": stats_map.get("match_engine"),
            "timed_out": stats_map.get("timed_out"),
        }
        pipeline_records.append(record)

    elapsed = time.perf_counter() - start
    report["summary"] = {
        "total_items": total_items,
        "success_items": success_items,
        "elapsed_seconds": elapsed,
        "stages": stage_summary,
        "records": pipeline_records,
    }
    report["summary"]["audio_root"] = str(audio_root_path)
    report["audio_root"] = str(audio_root_path)
    report["prefer_relative_audio"] = prefer_relative_audio
    report["path_style"] = path_style
    if ordered_stats:
        retake_result["retake_stats"] = [asdict(stat) for stat in ordered_stats]
        report["summary"]["retake_stats"] = [asdict(stat) for stat in ordered_stats]
    manifest_path = _write_ui_manifest(
        out_dir,
        audio_root=audio_root_path,
        items=items,
        render_summary=render_summary,
        stems=stems,
        report_path=report_path,
    )
    if manifest_path:
        report["ui_manifest"] = str(manifest_path)
        report["summary"]["ui_manifest"] = str(manifest_path)
    report["summary"]["stems"] = stems
    write_json(report_path, report)

    LOGGER.info(_safe_text(f"处理结果：成功 {success_items}/{total_items}"))
    for record in pipeline_records:
        if record["status"] == "ok":
            LOGGER.info(
                _safe_text(
                    f"{record['stem']}: kept={record['kept']}, window={record['window']}, unaligned={record['unaligned']}, "
                    f"cut={record['cut']:.3f}s, render={record['render']}"
                )
            )
        else:
            LOGGER.warning(
                _safe_text(
                    f"{record['stem']}: 处理失败（{record['message'] or '未知原因'}）"
                )
            )
    LOGGER.info(_safe_text(f"输出目录: {out_dir}"))

    return report


def handle_all_in_one(args: argparse.Namespace) -> int:
    """处理 all-in-one 子命令。"""

    if args.verbose and args.quiet:
        raise ValueError("--verbose 与 --quiet 不能同时使用。")
    if args.verbose:
        LOGGER.setLevel(logging.DEBUG)
    elif args.quiet:
        LOGGER.setLevel(logging.WARNING)

    canonical_render = args.render_mode
    if canonical_render == "yes":
        canonical_render = "always"
    elif canonical_render == "no":
        canonical_render = "never"
    args.render_mode = canonical_render

    parts: list[str] = [
        "--in",
        str(Path(args.input_dir)),
        "--out",
        str(Path(args.output_dir)),
        "--char-map",
        str(Path(args.char_map)),
        "--alias-map",
        str(Path(args.alias_map)),
        "--opencc",
        args.opencc,
        "--glob-text",
        args.norm_glob,
        "--glob-words",
        args.glob_words,
        "--render",
        canonical_render,
        "--glob-audio",
        args.glob_audio,
    ]
    if args.split_mode != DEFAULT_ALIGN_SPLIT_MODE:
        parts.extend(["--split-mode", args.split_mode])
    if args.min_len != DEFAULT_ALIGN_MIN_LEN:
        parts.extend(["--min-len", str(args.min_len)])
    if args.max_len != DEFAULT_ALIGN_MAX_LEN:
        parts.extend(["--max-len", str(args.max_len)])
    if args.hard_max != DEFAULT_ALIGN_HARD_MAX:
        parts.extend(["--hard-max", str(args.hard_max)])
    if not args.weak_punct_enable:
        parts.append("--no-weak-punct-enable")
    if not args.keep_quotes:
        parts.append("--no-keep-quotes")
    if args.audio_root:
        parts.extend(["--audio-root", str(Path(args.audio_root))])
    if not args.prefer_relative_audio:
        parts.append("--no-prefer-relative-audio")
    if args.path_style != "auto":
        parts.extend(["--path-style", args.path_style])
    parts.append("--collapse-lines" if args.collapse_lines else "--no-collapse-lines")
    if not args.emit_align:
        parts.append("--no-emit-align")
    if args.workers:
        parts.extend(["--workers", str(args.workers)])
    if args.no_interaction:
        parts.append("--no-interaction")
    if args.verbose:
        parts.append("--verbose")
    if args.quiet:
        parts.append("--quiet")
    if args.serve:
        parts.append("--serve")
    serve_host = getattr(args, "serve_host", "127.0.0.1")
    serve_port = getattr(args, "serve_port", 8765)
    if serve_host != "127.0.0.1":
        parts.extend(["--serve-host", serve_host])
    if serve_port != 8765:
        parts.extend(["--serve-port", str(serve_port)])
    if args.open_ui:
        parts.append("--open-ui")
    if args.open_browser:
        parts.append("--open-browser")
    parts.append("--no-collapse-align" if args.no_collapse_align else "--collapse-align")
    parts.append("--fast-match" if args.fast_match else "--no-fast-match")
    parts.extend(["--max-windows", str(args.max_windows)])
    parts.extend(["--match-timeout", str(args.match_timeout)])
    parts.extend(["--max-distance-ratio", str(args.max_distance_ratio)])
    parts.extend(["--min-anchor-ngram", str(args.min_anchor_ngram)])
    parts.extend(["--fallback-policy", args.fallback_policy])

    LOGGER.info(
        _safe_text(
            f"开始流水线任务: 输入={args.input_dir} 输出={args.output_dir} 渲染模式={args.render_mode}"
        )
    )
    LOGGER.info("等价命令: %s", _build_cli_example("all-in-one", parts))
    snapshot = {
        "emit_align": args.emit_align,
        "char_map": str(args.char_map),
        "alias_map": str(args.alias_map),
        "opencc": args.opencc,
        "norm_glob": args.norm_glob,
        "glob_words": args.glob_words,
        "glob_words_list": parse_glob_list(args.glob_words),
        "glob_audio": args.glob_audio,
        "glob_audio_list": parse_glob_list(args.glob_audio),
        "collapse_lines": args.collapse_lines,
        "no_collapse_align": args.no_collapse_align,
        "split_mode": args.split_mode,
        "render": canonical_render,
        "audio_root": str(args.audio_root) if args.audio_root else str(Path(args.input_dir)),
        "prefer_relative_audio": args.prefer_relative_audio,
        "path_style": args.path_style,
        "workers": args.workers,
        "no_interaction": args.no_interaction,
        "fast_match": args.fast_match,
        "max_windows": args.max_windows,
        "match_timeout": args.match_timeout,
        "compute_timeout_sec": getattr(args, "compute_timeout_sec", 120.0),
        "max_distance_ratio": args.max_distance_ratio,
        "min_anchor_ngram": args.min_anchor_ngram,
        "coarse_threshold": getattr(args, "coarse_threshold", 0.30),
        "coarse_len_tol": getattr(args, "coarse_len_tol", 0.35),
        "fallback_policy": args.fallback_policy,
        "serve": args.serve,
        "open_browser": args.open_browser,
        "open_ui": args.open_ui,
        "serve_host": serve_host,
        "serve_port": serve_port,
    }
    LOGGER.info(_safe_text(f"参数快照: {json.dumps(snapshot, ensure_ascii=False)}"))

    try:
        report = run_all_in_one(args)
    except Exception as exc:
        LOGGER.exception("处理 all-in-one 流水线失败")
        print("流水线执行失败: {}".format(exc), file=sys.stderr)
        return 1
    summary = report.get("summary", {})
    if summary.get("success_items", 0) != summary.get("total_items", 0):
        LOGGER.warning(
            _safe_text(
                f"部分条目失败，请检查 {Path(args.output_dir) / 'batch_report.json'}"
            )
        )
    records = summary.get("records") or []
    success_stems = [
        str(item.get("stem"))
        for item in records
        if item.get("status") == "ok" and item.get("stem")
    ]
    manifest_value = report.get("ui_manifest") or summary.get("ui_manifest")
    manifest_path = Path(manifest_value).expanduser().resolve() if manifest_value else None
    out_dir = Path(args.output_dir).expanduser().resolve()
    stems_for_ui = success_stems or list(summary.get("stems", []))
    if args.serve or args.open_ui:
        proc, url = _launch_static_ui(
            out_dir,
            stems=stems_for_ui,
            serve_host=serve_host,
            serve_port=serve_port,
            open_browser=bool(args.open_ui or args.open_browser),
            manifest_path=manifest_path if manifest_path and manifest_path.exists() else None,
        )
        if proc is None or url is None:
            LOGGER.error("静态 UI 服务启动失败，可稍后通过 serve-web 子命令重试。")
        else:
            LOGGER.info("流水线结束后 UI 服务已在后台运行。")
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
        "--collapse-lines",
        dest="collapse_lines",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="规范化时折叠换行并按句分行 (默认开启)",
    )
    prep.add_argument(
        "--emit-align",
        action="store_true",
        help="额外生成去标点的 .align.txt 供词级对齐与可视化使用",
    )
    prep.add_argument(
        "--split-mode",
        choices=["punct", "all-punct", "punct+len"],
        default=DEFAULT_ALIGN_SPLIT_MODE,
        help="对齐文本切句策略 (默认 punct+len)",
    )
    prep.add_argument(
        "--min-len",
        type=int,
        default=DEFAULT_ALIGN_MIN_LEN,
        help=f"punct+len 模式短句合并阈值（默认 {DEFAULT_ALIGN_MIN_LEN}）",
    )
    prep.add_argument(
        "--max-len",
        type=int,
        default=DEFAULT_ALIGN_MAX_LEN,
        help=f"punct+len 模式首轮长度上限（默认 {DEFAULT_ALIGN_MAX_LEN}）",
    )
    prep.add_argument(
        "--hard-max",
        type=int,
        default=DEFAULT_ALIGN_HARD_MAX,
        help=f"punct+len 强制断句上限（默认 {DEFAULT_ALIGN_HARD_MAX}）",
    )
    prep.add_argument(
        "--weak-punct-enable",
        dest="weak_punct_enable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="弱标点可作为断句点（默认开启）",
    )
    prep.add_argument(
        "--keep-quotes",
        dest="keep_quotes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="括号/引号内默认不在弱标点处断句（默认开启）",
    )
    prep.add_argument("--dry-run", action="store_true", help="仅生成报表，不写规范化文本")
    prep.set_defaults(func=handle_prep_norm)

    retake = subparsers.add_parser("retake-keep-last", help="词级 JSON + 原文 → SRT/TXT/EDL/Markers")
    group = retake.add_mutually_exclusive_group(required=True)
    group.add_argument("--words-json", help="单文件模式：词级 JSON")
    group.add_argument("--materials", help="目录批处理模式：素材根目录")
    retake.add_argument("--text", help="单文件模式：原文 TXT")
    retake.add_argument("--out", required=True, help="输出目录")
    retake.add_argument("--audio-root", help="源音频搜索根目录（默认=素材根或词级 JSON 所在目录）")
    retake.add_argument("--alias-map", default=str(DEFAULT_ALIAS_MAP), help="词别名映射 JSON (默认 config/default_alias_map.json)")
    retake.add_argument(
        "--prefer-relative-audio",
        dest="prefer_relative_audio",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="若音频位于 audio-root 下，EDL 中优先写相对路径（默认开启）",
    )
    retake.add_argument(
        "--path-style",
        choices=["auto", "posix", "windows"],
        default="auto",
        help="EDL 中 source_audio 的分隔符风格（默认 auto）",
    )
    retake.add_argument(
        "--glob-words",
        default="*.words.json;*.json",
        help="批处理模式：JSON 匹配模式 (分号或空格分隔)",
    )
    retake.add_argument(
        "--glob-text",
        nargs="+",
        default=["*.align.txt", "*.norm.txt", "*.txt"],
        help="批处理模式：文本匹配模式 (可多次指定)",
    )
    retake.add_argument(
        "--collapse-lines",
        dest="collapse_lines",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="规范化文本是否已折叠换行（默认开启，用于诊断时可关闭）",
    )
    align_group = retake.add_mutually_exclusive_group()
    align_group.add_argument(
        "--no-collapse-align",
        dest="no_collapse_align",
        action="store_true",
        default=True,
        help="读取 *.align.txt 时保留原始换行与空白（默认开启）",
    )
    align_group.add_argument(
        "--collapse-align",
        dest="no_collapse_align",
        action="store_false",
        help="兼容旧版行为：读取 *.align.txt 时继续折行/空白规范化",
    )
    retake.add_argument(
        "--no-interaction",
        action="store_true",
        help="无交互模式，不等待用户确认",
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
    retake.add_argument(
        "--fast-match",
        dest="fast_match",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启用快速候选预筛与带宽受限匹配（默认开启）",
    )
    retake.add_argument(
        "--max-windows",
        type=int,
        default=200,
        help="每行最多尝试的候选窗口数（默认 200）",
    )
    retake.add_argument(
        "--match-timeout",
        type=float,
        default=60.0,
        help="每个条目的匹配时间预算（秒，默认 60.0）",
    )
    retake.add_argument(
        "--compute-timeout-sec",
        type=float,
        default=300.0,
        help="单个条目的整体对齐超时，秒（默认 300）",
    )
    retake.add_argument(
        "--max-distance-ratio",
        type=float,
        default=0.35,
        help="允许的编辑距离占比上限（默认 0.35）",
    )
    retake.add_argument(
        "--min-anchor-ngram",
        type=int,
        default=6,
        help="候选预筛锚点 n-gram 长度（默认 6）",
    )
    retake.add_argument(
        "--coarse-threshold",
        type=float,
        default=0.20,
        help="粗筛相似度通过阈值（0-1，默认 0.20）",
    )
    retake.add_argument(
        "--coarse-len-tol",
        type=float,
        default=0.45,
        help="粗筛允许的窗口长度差占比（默认 0.45）",
    )
    retake.add_argument(
        "--fallback-policy",
        choices=["safe", "keep-all", "align-greedy", "greedy"],
        default="greedy",
        help="对齐失败时的回退策略（默认 greedy，对应 align-greedy）",
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

    serve = subparsers.add_parser("serve-web", help="启动本地可视化控制台")
    serve.add_argument("--out", required=True, help="输出目录 (扫描成果与导出文件)")
    serve.add_argument("--audio-root", help="原始音频根目录 (可选)")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    serve.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765，若占用会自动顺延")
    serve.add_argument("--open-browser", action="store_true", help="启动后自动打开浏览器")
    serve.add_argument(
        "--cors",
        action="store_true",
        help="允许来自 localhost 的跨域访问 (调试其它前端时启用)",
    )
    serve.set_defaults(func=handle_serve_web)

    pipeline = subparsers.add_parser(
        "all-in-one",
        help="一键流水线：规范化（含去换行与空格修复） → 保留最后一遍 → 生成对齐标记（有音频时自动渲染）",
    )
    pipeline.add_argument("--in", dest="input_dir", required=True, help="输入目录 (含 *.txt/*.json/音频)")
    pipeline.add_argument("--out", dest="output_dir", required=True, help="输出目录")
    pipeline.add_argument(
        "--audio-root",
        help="渲染阶段音频搜索根目录（默认=输入目录）",
    )
    pipeline.add_argument(
        "--emit-align",
        dest="emit_align",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="规范化阶段是否产出 .align.txt (默认开启)",
    )
    pipeline.add_argument(
        "--prefer-relative-audio",
        dest="prefer_relative_audio",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="若音频位于 audio-root 下，EDL 中优先写相对路径（默认开启）",
    )
    pipeline.add_argument(
        "--path-style",
        choices=["auto", "posix", "windows"],
        default="auto",
        help="EDL 中 source_audio 的分隔符风格（默认 auto）",
    )
    pipeline.add_argument(
        "--collapse-lines",
        dest="collapse_lines",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="规范化阶段是否折叠换行并按句输出 (默认开启)",
    )
    pipeline.add_argument(
        "--char-map",
        dest="char_map",
        default=str(DEFAULT_CHAR_MAP),
        help="字符映射配置 JSON (默认 config/default_char_map.json)",
    )
    pipeline.add_argument(
        "--alias-map",
        dest="alias_map",
        default=str(DEFAULT_ALIAS_MAP),
        help="词别名映射 JSON (默认 config/default_alias_map.json)",
    )
    pipeline.add_argument(
        "--opencc",
        choices=["none", "t2s", "s2t"],
        default="none",
        help="opencc 模式 (默认 none)",
    )
    pipeline.add_argument(
        "--glob-text",
        dest="norm_glob",
        default="*.txt",
        help="规范化阶段的文本匹配模式",
    )
    pipeline_align_group = pipeline.add_mutually_exclusive_group()
    pipeline_align_group.add_argument(
        "--no-collapse-align",
        dest="no_collapse_align",
        action="store_true",
        default=True,
        help="读取 *.align.txt 时保留原样，不再额外折叠（默认开启）",
    )
    pipeline_align_group.add_argument(
        "--collapse-align",
        dest="no_collapse_align",
        action="store_false",
        help="兼容旧版行为：读取 *.align.txt 时继续折行/空白规范化",
    )
    pipeline.add_argument(
        "--split-mode",
        dest="split_mode",
        choices=["punct", "all-punct", "punct+len"],
        default=DEFAULT_ALIGN_SPLIT_MODE,
        help="规范化阶段输出 .align.txt 时的切句策略 (默认 punct+len)",
    )
    pipeline.add_argument(
        "--min-len",
        type=int,
        default=DEFAULT_ALIGN_MIN_LEN,
        help=f"punct+len 模式短句合并阈值（默认 {DEFAULT_ALIGN_MIN_LEN}）",
    )
    pipeline.add_argument(
        "--max-len",
        type=int,
        default=DEFAULT_ALIGN_MAX_LEN,
        help=f"punct+len 模式首轮长度上限（默认 {DEFAULT_ALIGN_MAX_LEN}）",
    )
    pipeline.add_argument(
        "--hard-max",
        type=int,
        default=DEFAULT_ALIGN_HARD_MAX,
        help=f"punct+len 强制断句上限（默认 {DEFAULT_ALIGN_HARD_MAX}）",
    )
    pipeline.add_argument(
        "--weak-punct-enable",
        dest="weak_punct_enable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="规范化阶段弱标点是否可断句（默认开启）",
    )
    pipeline.add_argument(
        "--keep-quotes",
        dest="keep_quotes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="括号/引号内默认不在弱标点处断句（默认开启）",
    )
    pipeline.add_argument(
        "--glob-words",
        dest="glob_words",
        default="*.words.json;*.json",
        help="词级 JSON 匹配模式 (分号或空格分隔)",
    )
    pipeline.add_argument(
        "--glob-audio",
        dest="glob_audio",
        default="*.wav;*.m4a;*.mp3;*.flac",
        help="音频匹配模式 (分号分隔，默认 *.wav;*.m4a;*.mp3;*.flac)",
    )
    pipeline.add_argument(
        "--render",
        dest="render_mode",
        choices=["auto", "always", "never", "yes", "no"],
        default="auto",
        help="渲染策略：auto=有音频才渲染，always=总是渲染，never=跳过",
    )
    pipeline.add_argument("--workers", type=int, help="批处理并发度")
    pipeline.add_argument("--no-interaction", action="store_true", help="无交互模式，直接执行")
    pipeline.add_argument("--verbose", action="store_true", help="输出调试日志")
    pipeline.add_argument("--quiet", action="store_true", help="仅输出警告及以上")
    pipeline.add_argument(
        "--serve",
        action="store_true",
        help="流水线结束后在后台启动本地控制台",
    )
    pipeline.add_argument(
        "--serve-host",
        default="127.0.0.1",
        help="Web UI 服务绑定地址（默认 127.0.0.1）",
    )
    pipeline.add_argument(
        "--serve-port",
        type=int,
        default=8765,
        help="Web UI 服务起始端口（默认 8765）",
    )
    pipeline.add_argument(
        "--open-ui",
        action="store_true",
        help="流水线结束后启动 Web UI 并自动打开浏览器",
    )
    pipeline.add_argument(
        "--open-browser",
        action="store_true",
        help="配合 --serve 使用，启动后自动打开浏览器",
    )
    pipeline.add_argument(
        "--fast-match",
        dest="fast_match",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启用快速候选预筛与带宽受限匹配（默认开启）",
    )
    pipeline.add_argument(
        "--max-windows",
        type=int,
        default=200,
        help="每行最多尝试的候选窗口数（默认 200）",
    )
    pipeline.add_argument(
        "--match-timeout",
        type=float,
        default=60.0,
        help="每个条目的匹配时间预算（秒，默认 60.0）",
    )
    pipeline.add_argument(
        "--max-distance-ratio",
        type=float,
        default=0.35,
        help="允许的编辑距离占比上限（默认 0.35）",
    )
    pipeline.add_argument(
        "--min-anchor-ngram",
        type=int,
        default=6,
        help="候选预筛锚点 n-gram 长度（默认 6）",
    )
    pipeline.add_argument(
        "--fallback-policy",
        choices=["safe", "keep-all", "align-greedy", "greedy"],
        default="greedy",
        help="对齐失败时的回退策略（默认 greedy，对应 align-greedy）",
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


def _safe_text(payload: str) -> str:
    """确保字符串在 UTF-8 环境下安全输出。"""

    return payload.encode("utf-8", errors="replace").decode("utf-8")


def _casefold_pattern(pattern: str) -> str:
    """将 glob 模式转换为大小写不敏感的形式。"""

    result: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "[":
            # 保留原有字符集，直到遇到 ']'
            end = pattern.find("]", i + 1)
            if end == -1:
                result.append(ch)
                i += 1
                continue
            result.append(pattern[i : end + 1])
            i = end + 1
            continue
        if ch.isalpha():
            lower = ch.lower()
            upper = ch.upper()
            if lower == upper:
                result.append(ch)
            else:
                result.append(f"[{lower}{upper}]")
        else:
            result.append(ch)
        i += 1
    return "".join(result)


def _expand_case_insensitive_patterns(patterns: Iterable[str]) -> list[str]:
    """针对每个模式返回大小写无关匹配的等价形式。"""

    expanded: list[str] = []
    for pattern in patterns:
        if not pattern:
            continue
        converted = _casefold_pattern(pattern)
        if converted not in expanded:
            expanded.append(converted)
    return expanded


def _derive_retake_text_patterns(patterns: Iterable[str]) -> list[str]:
    """根据规范化模式生成保留阶段的文本匹配顺序。"""

    align_patterns: list[str] = []
    norm_patterns: list[str] = []
    others: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        lower = pattern.lower()
        base = _casefold_pattern(pattern)
        if lower.endswith(".align.txt"):
            target = align_patterns
        elif lower.endswith(".norm.txt"):
            target = norm_patterns
        else:
            target = others
            if lower.endswith(".txt"):
                align_variant = _casefold_pattern(pattern[:-4] + ".align.txt")
                if align_variant not in seen:
                    align_patterns.append(align_variant)
                    seen.add(align_variant)
                norm_variant = _casefold_pattern(pattern[:-4] + ".norm.txt")
                if norm_variant not in seen:
                    norm_patterns.append(norm_variant)
                    seen.add(norm_variant)
        if base not in seen:
            target.append(base)
            seen.add(base)
    if not (align_patterns or norm_patterns or others):
        return _expand_case_insensitive_patterns(["*.align.txt", "*.norm.txt", "*.txt"])
    return align_patterns + norm_patterns + others


def _iter_casefold_files(root: Path, patterns: Iterable[str]) -> list[Path]:
    """在目录下执行大小写无关的模式匹配。"""

    root = root.expanduser().resolve()
    if not root.exists():
        return []
    matched: dict[Path, None] = {}
    compiled = [pattern.lower() for pattern in patterns if pattern]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        for pattern in compiled:
            if fnmatch.fnmatch(name, pattern):
                matched[path] = None
                break
    return sorted(matched.keys())


def _resolve_render_status(
    stem: str,
    *,
    render_mode: str,
    has_audio: bool,
    render_skipped_no_audio: bool,
    render_summary: dict[str, dict],
    global_error: str,
) -> str:
    """根据渲染配置与执行结果推导最终状态字符串。"""

    if render_mode in {"no", "never"}:
        return "skipped(manual)"
    stem_key = stem.lower()
    if render_mode == "auto" and render_skipped_no_audio:
        return "skipped(no-audio)"
    entry = render_summary.get(stem_key)
    if entry:
        if entry.get("status") == "ok":
            return "ok"
        reason = entry.get("message") or entry.get("error") or "failed"
        cleaned = " ".join(str(reason).split())
        return f"failed:{cleaned}" if cleaned else "failed"
    if global_error:
        cleaned = " ".join(str(global_error).split())
        return f"failed:{cleaned}" if cleaned else "failed"
    if render_mode == "auto" and not has_audio:
        return "skipped(no-audio)"
    if not has_audio:
        return "missing-audio"
    return "skipped(no-edl)"


def _collect_audio_map(base_dir: Path, patterns: Iterable[str]) -> dict[str, list[Path]]:
    """按 stem 收集可用音频文件。"""

    audio_map: dict[str, list[Path]] = {}
    for audio_path in _iter_casefold_files(base_dir, patterns):
        stem = audio_path.stem.lower()
        LOGGER.info("[audio] stem=%s path=%s", stem, safe_rel(base_dir, audio_path))
        audio_map.setdefault(stem, []).append(audio_path)
    return audio_map


def _stem_from_edl_path(edl_path: str) -> str:
    """根据 EDL 文件名推导素材 stem。"""

    name = Path(edl_path).name
    for suffix in (".keepLast.edl.json", ".sentence.edl.json", ".edl.json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


