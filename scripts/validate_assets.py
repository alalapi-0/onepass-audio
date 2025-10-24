# 用途：校验 data/ 目录下的 ASR JSON、原文 TXT 与可选音频素材是否按文件名对齐，生成报告。
# 依赖：Python 3.10+（标准库），可选系统命令 ffprobe（若存在则用于探测音频时长）。
# 用法示例：
#   python scripts/validate_assets.py
#   python scripts/validate_assets.py --audio-required
"""素材验证器：核对 data/ 下的 JSON/TXT/音频素材是否对齐并生成报告。"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


PROJ_ROOT = Path(__file__).resolve().parent.parent
ASR_DIR = PROJ_ROOT / "data" / "asr-json"
TXT_DIR = PROJ_ROOT / "data" / "original_txt"
AUDIO_DIR = PROJ_ROOT / "data" / "audio"
OUT_DIR = PROJ_ROOT / "out"

JSON_EXT = {".json"}
TXT_EXT = {".txt"}
AUDIO_EXT = {".m4a", ".wav", ".mp3", ".flac"}

FFPROBE = None


@dataclass
class FileRecord:
    """Represent a collected file and basic metadata."""

    path: Path
    ext: str

    def stat(self) -> Tuple[int, str]:
        """Return file size and ISO formatted mtime."""

        file_stat = self.path.stat()
        return file_stat.st_size, datetime.fromtimestamp(file_stat.st_mtime).isoformat(timespec="seconds")


@dataclass
class DirectoryScan:
    """Hold scan results for a directory."""

    files: dict[str, List[FileRecord]]
    extras: List[Path]


@dataclass
class ValidationOutcome:
    """Collect validation results with messages for exit code calculation."""

    report: dict
    warnings: List[str]
    errors: List[str]


def to_posix(path: Path) -> str:
    """Convert a path to project-root-relative POSIX string when possible."""

    try:
        rel = path.resolve().relative_to(PROJ_ROOT)
        return rel.as_posix()
    except ValueError:
        return path.resolve().as_posix()


def detect_ffprobe() -> Optional[str]:
    """Return the ffprobe executable if available."""

    from shutil import which

    return which("ffprobe")


def probe_audio_duration(executable: Optional[str], path: Path) -> Optional[float]:
    """Probe audio duration via ffprobe when available."""

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
    """Scan a directory collecting files with allowed extensions and extras."""

    files: dict[str, List[FileRecord]] = {}
    extras: List[Path] = []
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return DirectoryScan(files={}, extras=[])
    except OSError as exc:
        raise RuntimeError(f"无法读取目录 {to_posix(directory)}：{exc}") from exc

    for entry in entries:
        if not entry.is_file():
            continue
        if entry.name == ".gitkeep":
            continue
        ext = entry.suffix.lower()
        if ext in allowed_exts:
            files.setdefault(entry.stem, []).append(FileRecord(path=entry, ext=ext))
        else:
            extras.append(entry)
    return DirectoryScan(files=files, extras=extras)


def build_file_meta(record: FileRecord, duration: Optional[float] = None) -> dict:
    """Convert a FileRecord to serializable metadata dictionary."""

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
    """Return sorted list of stems present in any map."""

    stems: set[str] = set()
    for mapping in maps:
        for stem in mapping.keys():
            stems.add(stem)
    return sorted(stems)


def write_json_report(report: dict) -> None:
    """Write the JSON report to out/validate_report.json."""

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "validate_report.json"
    with target.open("w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def write_csv_summary(rows: List[List[str]]) -> None:
    """Write the CSV summary file."""

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


def write_markdown_report(report: dict, general_warnings: Sequence[str], general_errors: Sequence[str]) -> None:
    """Write the human-readable Markdown report."""

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
        lines.append(
            f"| {item['stem']} | {json_cell} | {txt_cell} | {audio_cell} | {errors} | {warnings} |"
        )

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
    """Format JSON/TXT cells for Markdown output."""

    if not meta:
        return "❌ 缺失"
    return f"✅ `{meta['path']}`"


def item_audio_cell(meta: Optional[dict]) -> str:
    """Format audio cell for Markdown output."""

    if not meta:
        return "⚠️ 未找到"
    details = [f"`{meta['path']}`"]
    if "ext" in meta:
        details.append(meta["ext"])
    if "duration_s" in meta:
        details.append(f"{meta['duration_s']} s")
    return "✅ " + " / ".join(details)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="验证 data/ 下素材是否对齐并生成报告")
    parser.add_argument(
        "--audio-required",
        action="store_true",
        help="同时要求音频存在，缺失则视为错误",
    )
    return parser.parse_args(argv)


def validate_assets(audio_required: bool) -> ValidationOutcome:
    """Perform validation and collect a structured report."""

    global FFPROBE
    FFPROBE = detect_ffprobe()
    general_warnings: List[str] = []
    general_errors: List[str] = []

    scans: dict[str, DirectoryScan] = {}

    # Check directories
    required_dirs = [
        (ASR_DIR, "data/asr-json", True),
        (TXT_DIR, "data/original_txt", True),
        (AUDIO_DIR, "data/audio", audio_required),
    ]
    for directory, label, required in required_dirs:
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
            if label == "data/asr-json":
                scans[label] = scan_directory(directory, JSON_EXT)
            elif label == "data/original_txt":
                scans[label] = scan_directory(directory, TXT_EXT)
            else:
                scans[label] = scan_directory(directory, AUDIO_EXT)
        except RuntimeError as exc:
            general_errors.append(str(exc))
            scans[label] = DirectoryScan(files={}, extras=[])

    json_scan = scans.get("data/asr-json", DirectoryScan(files={}, extras=[]))
    txt_scan = scans.get("data/original_txt", DirectoryScan(files={}, extras=[]))
    audio_scan = scans.get("data/audio", DirectoryScan(files={}, extras=[]))

    if json_scan.extras:
        general_warnings.append(
            "检测到非 JSON 文件：" + ", ".join(sorted(to_posix(p) for p in json_scan.extras))
        )
    if txt_scan.extras:
        general_warnings.append(
            "检测到非 TXT 文件：" + ", ".join(sorted(to_posix(p) for p in txt_scan.extras))
        )
    if audio_scan.extras:
        general_warnings.append(
            "检测到不受支持的音频文件：" + ", ".join(sorted(to_posix(p) for p in audio_scan.extras))
        )

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

    for stem in stems:
        json_records = json_scan.files.get(stem, [])
        txt_records = txt_scan.files.get(stem, [])
        audio_records = audio_scan.files.get(stem, [])

        item_json = build_list_meta(json_records)
        item_txt = build_list_meta(txt_records)
        item_audio, audio_duration = build_audio_meta(audio_records)

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

        if audio_required and not audio_records:
            status_errors.append("缺少音频（已启用强制要求）")
        elif not audio_records:
            status_warnings.append("未找到音频")
        elif len(audio_records) > 1:
            status_warnings.append("存在多个音频，仅使用首个")

        if not json_records and txt_records:
            summary["orphan_txt"].append(stem)
        if not txt_records and json_records:
            summary["orphan_json"].append(stem)
        if audio_records and not (json_records and txt_records):
            summary["orphan_audio"].append(stem)

        item_status = {
            "ok": not status_errors and not status_warnings,
            "warnings": status_warnings,
            "errors": status_errors,
        }

        if status_errors:
            summary["error"] += 1
        elif status_warnings:
            summary["warn"] += 1
        else:
            summary["ok"] += 1

        items.append(
            {
                "stem": stem,
                "json": item_json,
                "txt": item_txt,
                "audio": item_audio,
                "status": item_status,
            }
        )

        csv_rows.append(
            [
                stem,
                "1" if json_records else "0",
                "1" if txt_records else "0",
                "1" if audio_records else "0",
                item_audio.get("ext", "") if item_audio else "",
                str(item_json["size"]) if item_json else "",
                str(item_txt["size"]) if item_txt else "",
                str(item_audio["size"]) if item_audio else "",
                item_json["mtime"] if item_json else "",
                item_txt["mtime"] if item_txt else "",
                item_audio["mtime"] if item_audio else "",
                str(audio_duration) if audio_duration is not None else "",
            ]
        )

    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "root": ".",
        "dirs": {
            "asr_json": to_posix(ASR_DIR),
            "original_txt": to_posix(TXT_DIR),
            "audio": to_posix(AUDIO_DIR),
        },
        "items": items,
        "summary": {
            **summary,
            "general_warnings": general_warnings,
            "general_errors": general_errors,
        },
    }

    write_json_report(report)
    write_csv_summary(csv_rows)
    write_markdown_report(report, general_warnings, general_errors)

    return ValidationOutcome(report=report, warnings=list(general_warnings), errors=list(general_errors))


def build_list_meta(records: List[FileRecord]) -> Optional[dict]:
    """Return metadata for the first record in a list."""

    if not records:
        return None
    record = sorted(records, key=lambda r: r.path.name)[0]
    return build_file_meta(record)


def build_audio_meta(records: List[FileRecord]) -> Tuple[Optional[dict], Optional[float]]:
    """Return metadata and duration for audio records."""

    if not records:
        return None, None
    record = sorted(records, key=lambda r: r.path.name)[0]
    duration = probe_audio_duration(FFPROBE, record.path)
    meta = build_file_meta(record, duration=duration)
    return meta, duration


def summarize_console(outcome: ValidationOutcome) -> Tuple[bool, bool]:
    """Print console summary and return (has_warnings, has_errors)."""

    report = outcome.report
    summary = report["summary"]
    general_warnings = outcome.warnings
    general_errors = outcome.errors

    print("[信息] 素材验证完成。")
    print(
        f"[统计] 总计 {summary['total']} 个 stem：OK={summary['ok']} WARN={summary['warn']} ERROR={summary['error']}"
    )

    for warning in general_warnings:
        print(f"[WARN] {warning}")
    for error in general_errors:
        print(f"[FAIL] {error}")

    if summary["missing_txt"]:
        show_examples("缺少 TXT", summary["missing_txt"], "将缺失的 {stem}.txt 放入 data/original_txt/{stem}.txt")
    if summary["missing_json"]:
        show_examples("缺少 JSON", summary["missing_json"], "补充 data/asr-json/{stem}.json 文件")
    if summary["orphan_json"]:
        show_examples("多余 JSON", summary["orphan_json"], "若不需要，请移除 data/asr-json/{stem}.json 或补充对应 TXT")
    if summary["orphan_txt"]:
        show_examples("多余 TXT", summary["orphan_txt"], "若不需要，请移除 data/original_txt/{stem}.txt 或补充对应 JSON")
    if summary["orphan_audio"]:
        show_examples("孤立音频", summary["orphan_audio"], "补齐 data/asr-json/{stem}.json 与 data/original_txt/{stem}.txt")

    print("[信息] 报告已写入 out/validate_report.json | out/validate_report.md | out/validate_summary.csv")

    has_item_warnings = summary["warn"] > 0 or bool(summary["orphan_json"] or summary["orphan_txt"] or summary["orphan_audio"])
    has_item_errors = summary["error"] > 0

    has_warnings = bool(general_warnings) or has_item_warnings
    has_errors = bool(general_errors) or has_item_errors
    return has_warnings, has_errors


def show_examples(title: str, stems: Sequence[str], template: str) -> None:
    """Print up to 10 remediation suggestions based on stems."""

    if not stems:
        return
    print(f"[提示] {title}：")
    for stem in stems[:10]:
        print(f"  - {template.format(stem=stem)}")
    if len(stems) > 10:
        print("  - 其余略 …")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Script entry point."""

    args = parse_args(argv)
    try:
        outcome = validate_assets(audio_required=args.audio_required)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[错误] 验证过程中出现未预期异常：{exc}")
        return 2

    has_warnings, has_errors = summarize_console(outcome)
    if has_errors:
        return 2
    if has_warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
