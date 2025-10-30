"""Entry point for the OnePass Audio interactive helper."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from onepass import __version__
from onepass.align import align_sentences
from onepass.asr_loader import Word, load_words
from onepass.edl import EDL, build_keep_last_edl
from onepass.markers import write_audition_markers
from onepass.pipeline import PreparedSentences, prepare_sentences
from onepass.textnorm import Sentence
from onepass.ux import (
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
    prompt_existing_directory,
    prompt_yes_no,
)


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_MATERIALS_DIR = ROOT_DIR / "materials"
DEFAULT_OUT_DIR = ROOT_DIR / "out"
DEFAULT_NORMALIZED_DIR = ROOT_DIR / "data" / "original_txt_norm"
DEFAULT_NORMALIZE_REPORT = ROOT_DIR / "out" / "normalize_report.csv"
DEFAULT_SCORE_THRESHOLD = 80
AUDIO_PRIORITY = {
    ".wav": 0,
    ".flac": 1,
    ".m4a": 2,
    ".aac": 3,
    ".mp3": 4,
    ".ogg": 5,
    ".wma": 6,
}


@dataclass
class ChapterResources:
    """Collection of paths pointing to the required resources for a chapter."""

    stem: str
    asr_json: Path
    original_txt: Path
    audio_file: Path | None


@dataclass
class ChapterSummary:
    """Information about generated artefacts for a single chapter."""

    stem: str
    subtitle_path: Path
    transcript_path: Path
    edl_path: Path
    markers_path: Path
    audio_path: Path | None
    kept_sentences: int
    duplicate_windows: int
    unaligned_sentences: int
    cut_seconds: float


def _print_banner() -> None:
    print_header("OnePass Audio — 录完即净，一遍过")
    print_info(f"版本: {__version__}")
    print_info("本程序将自动匹配素材并批量生成字幕、EDL 等文件。\n")


def _prompt_materials_directory() -> Path:
    print_header("素材目录")
    default_dir: Optional[Path] = DEFAULT_MATERIALS_DIR if DEFAULT_MATERIALS_DIR.exists() else None
    return prompt_existing_directory(
        "包含 JSON/TXT/音频 的素材文件夹路径",
        default=default_dir,
    )


def _ensure_output_directory() -> Path:
    print_header("输出目录")
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    return prompt_existing_directory("输出文件夹 (会在其中生成字幕/EDL 等)", default=DEFAULT_OUT_DIR)


def _index_files_by_stem(paths: Iterable[Path]) -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    for path in sorted(paths):
        if not path.is_file():
            continue
        index.setdefault(path.stem.lower(), path.resolve())
    return index


def _discover_chapters(materials_dir: Path) -> List[ChapterResources]:
    files = list(materials_dir.iterdir())
    json_map = _index_files_by_stem(p for p in files if p.suffix.lower() == ".json")
    txt_map = _index_files_by_stem(p for p in files if p.suffix.lower() == ".txt")

    audio_map: Dict[str, Tuple[int, Path]] = {}
    for path in files:
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in AUDIO_PRIORITY:
            continue
        priority = AUDIO_PRIORITY[suffix]
        key = path.stem.lower()
        existing = audio_map.get(key)
        if existing is None or priority < existing[0]:
            audio_map[key] = (priority, path.resolve())

    missing_txt = sorted(set(json_map) - set(txt_map))
    for stem in missing_txt:
        print_warning(f"找到 JSON 但缺少同名 TXT: {json_map[stem].name}")

    missing_json = sorted(set(txt_map) - set(json_map))
    for stem in missing_json:
        print_warning(f"找到 TXT 但缺少同名 JSON: {txt_map[stem].name}")

    chapters: List[ChapterResources] = []
    for key in sorted(set(json_map) & set(txt_map)):
        json_path = json_map[key]
        txt_path = txt_map[key]
        audio_entry = audio_map.get(key)
        audio_path = audio_entry[1] if audio_entry else None
        chapters.append(
            ChapterResources(
                stem=json_path.stem,
                asr_json=json_path,
                original_txt=txt_path,
                audio_file=audio_path,
            )
        )

    return chapters


def _ensure_normalized_text_path(chapter: ChapterResources) -> Path:
    """Ensure the normalised transcript exists and return the path to use."""

    norm_path = DEFAULT_NORMALIZED_DIR / f"{chapter.stem}.norm.txt"
    if norm_path.exists():
        print_info(f"使用已规范文本: {norm_path}")
        return norm_path

    script_path = ROOT_DIR / "scripts" / "normalize_original.py"
    if not script_path.exists():
        print_warning("未找到 scripts/normalize_original.py，将继续使用原始 TXT。")
        return chapter.original_txt

    message = (
        "未检测到规范化文本，是否现在调用 scripts/normalize_original.py?\n"
        f"原稿: {chapter.original_txt}\n"
        f"输出: {norm_path}\n"
        "生成 CSV 报告: out/normalize_report.csv"
    )
    if not prompt_yes_no(message, default=True):
        return chapter.original_txt

    DEFAULT_NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = DEFAULT_NORMALIZE_REPORT
    report_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(script_path),
        "--in",
        str(chapter.original_txt),
        "--out",
        str(norm_path),
        "--report",
        str(report_path),
        "--mode",
        "align",
    ]

    print_info("正在规范化原稿，稍候…")
    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))
    except FileNotFoundError as exc:
        print_error(f"无法调用规范化脚本: {exc}")
        return chapter.original_txt

    if result.returncode == 0 and norm_path.exists():
        print_success(f"已生成规范文本: {norm_path.name}")
        return norm_path

    print_warning("规范化脚本执行失败，将继续使用原始 TXT。")
    return chapter.original_txt


def _warn_mismatch(words: List[Word], sentences: List[Sentence]) -> None:
    if not words or not sentences:
        return
    if len(sentences) > len(words) * 1.5:
        print_warning("原稿句子数量明显多于 ASR 词数量，可能存在内容不匹配。")


def _serialise_edl(edl: EDL) -> dict:
    return {
        "audio_stem": edl.audio_stem,
        "sample_rate": edl.sample_rate,
        "actions": [asdict(action) for action in edl.actions],
        "stats": edl.stats,
        "created_at": edl.created_at,
    }


def _format_srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(entries: List[Tuple[float, float, str]], out_path: Path) -> None:
    lines: List[str] = []
    for index, (start, end, text) in enumerate(entries, start=1):
        lines.append(str(index))
        lines.append(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}")
        payload = text.splitlines() or [""]
        lines.extend(payload)
        lines.append("")
    out_path.write_text("\n".join(lines).strip() + "\n" if lines else "", encoding="utf-8")


def _write_plain_transcript(entries: List[Tuple[float, float, str]], out_path: Path) -> None:
    text = "\n".join(content for _, _, content in entries)
    out_path.write_text((text + "\n") if text else "", encoding="utf-8")


def _render_audio(audio: Path, edl_path: Path, output: Path) -> bool:
    script = ROOT_DIR / "scripts" / "edl_to_ffmpeg.py"
    if not script.exists():
        print_warning("未找到 edl_to_ffmpeg.py，跳过音频导出。")
        return False

    cmd = [
        sys.executable,
        str(script),
        "--audio",
        str(audio),
        "--edl",
        str(edl_path),
        "--out",
        str(output),
    ]
    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))
    except FileNotFoundError as exc:
        print_error(f"无法调用 Python 解释器导出音频: {exc}")
        return False

    if result.returncode != 0:
        print_error("音频导出失败，请确认已安装 ffmpeg 并可在命令行中使用。")
        return False

    print_success(f"已导出干净音频: {output.name}")
    return True


def _process_chapter(
    chapter: ChapterResources,
    outdir: Path,
    *,
    score_threshold: int,
    render_audio: bool,
) -> ChapterSummary | None:
    try:
        words = load_words(chapter.asr_json)
    except Exception as exc:
        print_error(f"读取 ASR JSON 失败: {exc}")
        return None

    text_path = _ensure_normalized_text_path(chapter)
    try:
        raw_text = text_path.read_text(encoding="utf-8")
    except Exception as exc:
        print_error(f"读取原稿 TXT 失败: {exc}")
        return None

    prepared: PreparedSentences = prepare_sentences(raw_text)
    sentences = prepared.alignment
    display_texts = prepared.display

    if not sentences:
        print_warning("原稿中没有有效的句子，跳过该文件。")
        return None

    _warn_mismatch(words, sentences)

    align = align_sentences(words, sentences, score_threshold=score_threshold)
    edl = build_keep_last_edl(words, align)
    edl.audio_stem = chapter.stem

    subtitle_entries: List[Tuple[float, float, str]] = []
    for idx, match in sorted(align.kept.items()):
        if match is None:
            continue
        if idx >= len(display_texts):
            continue
        subtitle_entries.append((match.start, match.end, display_texts[idx]))

    outdir.mkdir(parents=True, exist_ok=True)
    srt_path = outdir / f"{chapter.stem}.keepLast.srt"
    txt_path = outdir / f"{chapter.stem}.keepLast.txt"
    edl_path = outdir / f"{chapter.stem}.keepLast.edl.json"
    markers_path = outdir / f"{chapter.stem}.keepLast.audition_markers.csv"

    try:
        with edl_path.open("w", encoding="utf-8") as fh:
            json.dump(_serialise_edl(edl), fh, ensure_ascii=False, indent=2)
        _write_srt(subtitle_entries, srt_path)
        _write_plain_transcript(subtitle_entries, txt_path)
        write_audition_markers(edl, markers_path)
    except Exception as exc:
        print_error(f"写入输出文件失败: {exc}")
        return None

    kept_count = sum(1 for m in align.kept.values() if m is not None)
    duplicate_windows = sum(len(windows) for windows in align.dups.values())
    unaligned_count = len(align.unaligned)
    cut_seconds = float(edl.stats.get("total_cut_sec", 0.0)) if isinstance(edl.stats, dict) else 0.0

    if align.unaligned:
        samples: List[str] = []
        for idx in align.unaligned[:3]:
            if 0 <= idx < len(display_texts):
                sample = display_texts[idx]
                samples.append(sample if len(sample) <= 20 else sample[:20] + "…")
        if samples:
            print_warning("未对齐的句子示例: " + "; ".join(samples))

    audio_output: Path | None = None
    if render_audio:
        if chapter.audio_file is None:
            print_warning("未找到同名音频文件，跳过音频导出。")
        else:
            audio_output = outdir / f"{chapter.stem}.clean.wav"
            if not _render_audio(chapter.audio_file, edl_path, audio_output):
                audio_output = None

    print_info(
        "句子总数 {total}，保留 {kept}，重复窗口 {dup}，未对齐 {unaligned}，去除重复 {cut:.3f}s".format(
            total=len(sentences),
            kept=kept_count,
            dup=duplicate_windows,
            unaligned=unaligned_count,
            cut=cut_seconds,
        )
    )
    print_success(f"已生成字幕: {srt_path.name}")
    print_success(f"已生成精简文本: {txt_path.name}")
    print_success(f"已生成 EDL: {edl_path.name}")
    print_success(f"已生成 Audition 标记: {markers_path.name}")

    return ChapterSummary(
        stem=chapter.stem,
        subtitle_path=srt_path,
        transcript_path=txt_path,
        edl_path=edl_path,
        markers_path=markers_path,
        audio_path=audio_output,
        kept_sentences=kept_count,
        duplicate_windows=duplicate_windows,
        unaligned_sentences=unaligned_count,
        cut_seconds=cut_seconds,
    )


def main() -> None:
    _print_banner()
    materials_dir = _prompt_materials_directory()

    print_header("素材匹配")
    chapters = _discover_chapters(materials_dir)
    if not chapters:
        print_error("未找到任何同时包含 JSON 与 TXT 的素材文件。")
        return

    with_audio = sum(1 for chapter in chapters if chapter.audio_file is not None)
    preview = ", ".join(ch.stem for ch in chapters[:5])
    if len(chapters) > 5:
        preview += " …"
    print_info(
        f"共匹配到 {len(chapters)} 套素材，其中 {with_audio} 套包含音频。" +
        (f" 示例: {preview}" if preview else "")
    )

    outdir = _ensure_output_directory()

    render_audio = with_audio > 0 and prompt_yes_no("检测到音频文件，是否按 EDL 自动导出干净音频?", default=True)

    print_header("批量处理")
    summaries: List[ChapterSummary] = []
    total = len(chapters)
    for index, chapter in enumerate(chapters, start=1):
        print_header(f"[{index}/{total}] {chapter.stem}")
        summary = _process_chapter(
            chapter,
            outdir,
            score_threshold=DEFAULT_SCORE_THRESHOLD,
            render_audio=render_audio,
        )
        if summary:
            summaries.append(summary)

    print_header("处理结果")
    print_success(f"成功处理 {len(summaries)}/{total} 套素材。输出目录: {outdir}")
    if summaries:
        for summary in summaries:
            info = [
                f"保留{summary.kept_sentences}",
                f"重复{summary.duplicate_windows}",
                f"未对齐{summary.unaligned_sentences}",
                f"cut={summary.cut_seconds:.3f}s",
            ]
            if summary.audio_path:
                info.append(f"音频→{summary.audio_path.name}")
            print_info(f"{summary.stem}: " + ", ".join(info))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_error("操作已取消。")
