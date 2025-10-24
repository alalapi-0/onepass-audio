"""scripts.validate_assets
用途：扫描 data/ 目录，检查 ASR JSON、原文 TXT 与音频素材是否对齐，输出报告并打印实时进度。
依赖：Python 标准库 argparse、csv、json、os、pathlib、subprocess；内部模块 ``onepass.ux``。
示例：
  python scripts/validate_assets.py --audio-required
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from onepass.ux import enable_ansi, log_err, log_info, log_ok, log_warn, section

PROJ_ROOT = Path(__file__).resolve().parent.parent
ASR_DIR = PROJ_ROOT / "data" / "asr-json"
TXT_DIR = PROJ_ROOT / "data" / "original_txt"
AUDIO_DIR = PROJ_ROOT / "data" / "audio"
OUT_DIR = PROJ_ROOT / "out"

JSON_EXT = {".json"}
TXT_EXT = {".txt"}
AUDIO_EXT = {".m4a", ".wav", ".mp3", ".flac"}


@dataclass
class FileRecord:
    path: Path
    ext: str

    def stat(self) -> Tuple[int, str]:
        file_stat = self.path.stat()
        return file_stat.st_size, datetime.fromtimestamp(file_stat.st_mtime).isoformat(timespec="seconds")


@dataclass
class DirectoryScan:
    files: dict[str, List[FileRecord]]
    extras: List[Path]


@dataclass
class ValidationOutcome:
    report: dict
    warnings: List[str]
    errors: List[str]


def determine_verbose(args: argparse.Namespace) -> bool:
    env_verbose = os.environ.get("ONEPASS_VERBOSE", "1") != "0"
    if getattr(args, "quiet", False):
        return False
    if getattr(args, "verbose", False):
        return True
    return env_verbose


def to_posix(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(PROJ_ROOT)
        return rel.as_posix()
    except ValueError:
        return path.resolve().as_posix()


def detect_ffprobe() -> Optional[str]:
    from shutil import which

    return which("ffprobe")


def probe_audio_duration(executable: Optional[str], path: Path) -> Optional[float]:
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    duration_str = completed.stdout.strip()
    if not duration_str:
        return None
    try:
        return round(float(duration_str), 3)
    except ValueError:
        return None


def scan_directory(directory: Path, allowed_exts: Sequence[str]) -> DirectoryScan:
    files: dict[str, List[FileRecord]] = {}
    extras: List[Path] = []
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return DirectoryScan(files={}, extras=[])
    except OSError as exc:
        raise RuntimeError(f"无法读取目录 {to_posix(directory)}：{exc}") from exc

    for entry in entries:
        if not entry.is_file() or entry.name == ".gitkeep":
            continue
        ext = entry.suffix.lower()
        if ext in allowed_exts:
            files.setdefault(entry.stem, []).append(FileRecord(path=entry, ext=ext))
        else:
            extras.append(entry)
    return DirectoryScan(files=files, extras=extras)


def build_file_meta(record: FileRecord, duration: Optional[float] = None) -> dict:
    size, mtime = record.stat()
    data = {
        "path": to_posix(record.path),
        "size": size,
        "mtime": mtime,
    }
    if record.ext:
        data["ext"] = record.ext
    if duration is not None:
        data["duration_s"] = duration
    return data


def collect_stems(*maps: Iterable[dict[str, List[FileRecord]]]) -> List[str]:
    stems: set[str] = set()
    for mapping in maps:
        for stem in mapping.keys():
            stems.add(stem)
    return sorted(stems)


def write_json_report(report: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "validate_report.json"
    with target.open("w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def write_csv_summary(rows: List[List[str]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "validate_summary.csv"
    with target.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "stem",
                "has_json",
                "has_txt",
                "has_audio",
                "audio_ext",
                "json_size",
                "txt_size",
                "audio_size",
                "json_mtime",
                "txt_mtime",
                "audio_mtime",
                "audio_duration_s",
            ]
        )
        writer.writerows(rows)


def write_markdown(report: dict, general_warnings: List[str], general_errors: List[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "validate_report.md"
    lines: List[str] = []
    lines.append("# 素材验证报告")
    lines.append("")
    lines.append(f"- 生成时间：{report['checked_at']}")
    lines.append(f"- 项目根目录：{report['root']}")
    lines.append("- 目录配置：")
    lines.append(f"  - ASR JSON：{report['dirs']['asr_json']}")
    lines.append(f"  - 原文 TXT：{report['dirs']['original_txt']}")
    lines.append(f"  - 音频：{report['dirs']['audio']}")
    if general_errors:
        lines.append("- ❌ 目录/系统错误：")
        for err in general_errors:
            lines.append(f"  - {err}")
    if general_warnings:
        lines.append("- ⚠️ 目录/系统警告：")
        for warn in general_warnings:
            lines.append(f"  - {warn}")
    lines.append("")
    lines.append("| stem | JSON | TXT | Audio | Errors | Warnings |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for item in report["items"]:
        json_cell = item_display_cell(item.get("json"))
        txt_cell = item_display_cell(item.get("txt"))
        audio_cell = item_audio_cell(item.get("audio"))
        errors = "<br>".join(item["status"]["errors"]) if item["status"]["errors"] else ""
        warnings = "<br>".join(item["status"]["warnings"]) if item["status"]["warnings"] else ""
        lines.append(f"| {item['stem']} | {json_cell} | {txt_cell} | {audio_cell} | {errors} | {warnings} |")
    summary = report["summary"]
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(
        f"- 总计 {summary['total']} 个 stem：✅ {summary['ok']} · ⚠️ {summary['warn']} · ❌ {summary['error']}"
    )
    if summary["missing_txt"]:
        lines.append(f"- 缺少 TXT：{', '.join(summary['missing_txt'])}")
    if summary["missing_json"]:
        lines.append(f"- 缺少 JSON：{', '.join(summary['missing_json'])}")
    if summary["orphan_txt"]:
        lines.append(f"- 多余 TXT：{', '.join(summary['orphan_txt'])}")
    if summary["orphan_json"]:
        lines.append(f"- 多余 JSON：{', '.join(summary['orphan_json'])}")
    if summary["orphan_audio"]:
        lines.append(f"- 孤立音频：{', '.join(summary['orphan_audio'])}")
    lines.append("")
    lines.append("## 操作建议")
    lines.append("")
    suggestions = [
        "确保文件名按 stem 完全一致，例如 001.json ↔ 001.txt ↔ 001.m4a",
        "缺失的 TXT 请放置到 data/original_txt/<stem>.txt",
        "缺失的 JSON 请放置到 data/asr-json/<stem>.json",
        "若仅生成字幕/标记，可忽略音频缺失；需要渲染音频时请补齐 data/audio/<stem>.<ext>",
        "当前支持的音频扩展名：.m4a/.wav/.mp3/.flac",
    ]
    for suggestion in suggestions:
        lines.append(f"- {suggestion}")
    with target.open("w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))


def item_display_cell(meta: Optional[dict]) -> str:
    if not meta:
        return "❌ 缺失"
    return f"✅ `{meta['path']}`"


def item_audio_cell(meta: Optional[dict]) -> str:
    if not meta:
        return "⚠️ 未找到"
    details = [f"`{meta['path']}`"]
    if "ext" in meta:
        details.append(meta["ext"])
    if "duration_s" in meta:
        details.append(f"{meta['duration_s']} s")
    return "✅ " + " / ".join(details)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 data/ 下素材是否对齐并生成报告")
    parser.add_argument("--audio-required", action="store_true", help="同时要求音频存在，缺失则视为错误")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", action="store_true", help="强制开启详细日志")
    verbosity.add_argument("--quiet", action="store_true", help="关闭大部分日志")
    return parser.parse_args(argv)


def validate_assets(audio_required: bool) -> ValidationOutcome:
    ffprobe = detect_ffprobe()
    general_warnings: List[str] = []
    general_errors: List[str] = []
    scans: dict[str, DirectoryScan] = {}

    required_dirs = [
        (ASR_DIR, "data/asr-json", True, JSON_EXT),
        (TXT_DIR, "data/original_txt", True, TXT_EXT),
        (AUDIO_DIR, "data/audio", audio_required, AUDIO_EXT),
    ]
    for directory, label, required, exts in required_dirs:
        if not directory.exists():
            message = f"缺少目录 {label}，请创建后放入对应素材"
            if required:
                general_errors.append(message)
            else:
                general_warnings.append(message)
            scans[label] = DirectoryScan(files={}, extras=[])
            continue
        if not directory.is_dir():
            message = f"路径 {label} 不是目录，请检查"
            general_errors.append(message)
            scans[label] = DirectoryScan(files={}, extras=[])
            continue
        try:
            scans[label] = scan_directory(directory, exts)
        except RuntimeError as exc:
            general_errors.append(str(exc))
            scans[label] = DirectoryScan(files={}, extras=[])

    json_scan = scans.get("data/asr-json", DirectoryScan(files={}, extras=[]))
    txt_scan = scans.get("data/original_txt", DirectoryScan(files={}, extras=[]))
    audio_scan = scans.get("data/audio", DirectoryScan(files={}, extras=[]))

    if json_scan.extras:
        general_warnings.append("检测到非 JSON 文件：" + ", ".join(sorted(to_posix(p) for p in json_scan.extras)))
    if txt_scan.extras:
        general_warnings.append("检测到非 TXT 文件：" + ", ".join(sorted(to_posix(p) for p in txt_scan.extras)))
    if audio_scan.extras:
        general_warnings.append("检测到不受支持的音频文件：" + ", ".join(sorted(to_posix(p) for p in audio_scan.extras)))

    stems = collect_stems(json_scan.files, txt_scan.files, audio_scan.files)
    items: List[dict] = []
    summary = {
        "total": len(stems),
        "ok": 0,
        "warn": 0,
        "error": 0,
        "missing_txt": [],
        "missing_json": [],
        "orphan_txt": [],
        "orphan_json": [],
        "orphan_audio": [],
    }
    csv_rows: List[List[str]] = []

    for idx, stem in enumerate(stems, start=1):
        if idx % 200 == 0:
            log_info(f"扫描进度：{idx}/{len(stems)} 个 stem")
        json_records = json_scan.files.get(stem, [])
        txt_records = txt_scan.files.get(stem, [])
        audio_records = audio_scan.files.get(stem, [])

        item_json = build_list_meta(json_records)
        item_txt = build_list_meta(txt_records)
        item_audio, audio_duration = build_audio_meta(audio_records, ffprobe)

        status_errors: List[str] = []
        status_warnings: List[str] = []

        if not json_records:
            status_errors.append("缺少 JSON")
            summary["missing_json"].append(stem)
        elif len(json_records) > 1:
            status_warnings.append("存在多个 JSON，仅使用首个")

        if not txt_records:
            status_errors.append("缺少 TXT")
            summary["missing_txt"].append(stem)
        elif len(txt_records) > 1:
            status_warnings.append("存在多个 TXT，仅使用首个")

        if not audio_records:
            if audio_required:
                status_errors.append("缺少音频")
                summary["orphan_audio"].append(stem)
            else:
                status_warnings.append("未找到音频")
        elif len(audio_records) > 1:
            status_warnings.append("存在多个音频，仅使用首个")

        if not txt_records and json_records:
            summary["orphan_json"].append(stem)
        if not json_records and txt_records:
            summary["orphan_txt"].append(stem)
        if audio_records and (not json_records or not txt_records):
            summary["orphan_audio"].append(stem)

        if status_errors:
            summary["error"] += 1
        elif status_warnings:
            summary["warn"] += 1
        else:
            summary["ok"] += 1

        csv_rows.append(
            [
                stem,
                str(bool(json_records)),
                str(bool(txt_records)),
                str(bool(audio_records)),
                audio_records[0].ext if audio_records else "",
                str(item_json[0].get("size") if item_json else ""),
                str(item_txt[0].get("size") if item_txt else ""),
                str(item_audio[0].get("size") if item_audio else ""),
                item_json[0].get("mtime") if item_json else "",
                item_txt[0].get("mtime") if item_txt else "",
                item_audio[0].get("mtime") if item_audio else "",
                str(audio_duration or ""),
            ]
        )

        items.append(
            {
                "stem": stem,
                "json": item_json,
                "txt": item_txt,
                "audio": item_audio,
                "status": {"errors": status_errors, "warnings": status_warnings},
            }
        )

    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "root": to_posix(PROJ_ROOT),
        "dirs": {
            "asr_json": to_posix(ASR_DIR),
            "original_txt": to_posix(TXT_DIR),
            "audio": to_posix(AUDIO_DIR),
        },
        "items": items,
        "summary": summary,
    }

    write_json_report(report)
    write_csv_summary(csv_rows)
    write_markdown(report, general_warnings, general_errors)

    return ValidationOutcome(report=report, warnings=general_warnings, errors=general_errors)


def build_list_meta(records: List[FileRecord]) -> List[dict]:
    return [build_file_meta(record) for record in records]


def build_audio_meta(records: List[FileRecord], ffprobe: Optional[str]) -> Tuple[List[dict], Optional[float]]:
    if not records:
        return [], None
    first = records[0]
    duration = probe_audio_duration(ffprobe, first.path)
    return [build_file_meta(first, duration=duration)], duration


def main(argv: Optional[Sequence[str]] = None) -> int:
    enable_ansi()
    args = parse_args(argv)
    verbose_flag = determine_verbose(args)

    section("目录扫描")
    outcome = validate_assets(audio_required=args.audio_required)

    section("统计结果")
    summary = outcome.report["summary"]
    log_info(
        f"总计 {summary['total']} 个 stem：完成 {summary['ok']} · 警告 {summary['warn']} · 错误 {summary['error']}"
    )
    if outcome.errors:
        if verbose_flag:
            log_err(f"目录级错误 {len(outcome.errors)} 条：")
            for err in outcome.errors:
                log_err(f"  - {err}")
        else:
            log_err(f"目录级错误 {len(outcome.errors)} 条（使用 --verbose 查看详情）")
    if outcome.warnings:
        if verbose_flag:
            log_warn(f"目录级警告 {len(outcome.warnings)} 条：")
            for warn in outcome.warnings:
                log_warn(f"  - {warn}")
        else:
            log_warn(f"目录级警告 {len(outcome.warnings)} 条（使用 --verbose 查看详情）")

    log_info(f"错误示例 {summary['error']} 条 / 其余略")
    log_ok("验证完成，报告已写入 out/ 目录。")
    return 0 if summary["error"] == 0 and not outcome.errors else 2


if __name__ == "__main__":
    sys.exit(main())
