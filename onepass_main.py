"""Entry point for the OnePass Audio interactive helper."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from onepass import __version__
from onepass.ux import (
    print_error,
    print_header,
    print_info,
    print_success,
    prompt_choice,
    prompt_existing_directory,
    prompt_existing_file,
    prompt_yes_no,
)


@dataclass
class ChapterResources:
    """Collection of paths pointing to the required resources for a chapter."""

    asr_json: Path
    original_txt: Path
    audio_file: Path | None


def _print_banner() -> None:
    print_header("OnePass Audio — 录完即净，一遍过")
    print_info(f"版本: {__version__}")
    print_info("本向导将帮助你收集运行脚本所需的文件路径，并给出建议命令。\n")


def _collect_chapter_resources() -> ChapterResources:
    print_header("素材路径")
    asr_json = prompt_existing_file("ASR 词级时间戳 JSON 文件路径")
    original_txt = prompt_existing_file("原稿 TXT 文件路径")

    has_audio = prompt_yes_no("是否同时准备好了原始音频文件?", default=True)
    audio_file: Path | None = None
    if has_audio:
        audio_file = prompt_existing_file("原始音频文件路径")

    return ChapterResources(asr_json=asr_json, original_txt=original_txt, audio_file=audio_file)


def _collect_output_directory() -> Path:
    print_header("输出目录")
    outdir = prompt_existing_directory("输出文件夹 (会在其中生成字幕/EDL 等)")
    return outdir


def _build_summary(resources: ChapterResources, outdir: Path) -> str:
    commands = [
        dedent(
            f"""
            生成去口癖字幕 + 保留最后一遍 + EDL + Audition 标记:
                python scripts/retake_keep_last.py \
                    --json {resources.asr_json} \
                    --original {resources.original_txt} \
                    --outdir {outdir}
            """
        ).strip()
    ]

    if resources.audio_file:
        commands.append(
            dedent(
                f"""
                按 EDL 导出干净音频:
                    python scripts/edl_to_ffmpeg.py \
                        --audio {resources.audio_file} \
                        --edl {outdir / (resources.asr_json.stem + '.keepLast.edl.json')} \
                        --out {outdir / (resources.asr_json.stem + '.clean.wav')}
                """
            ).strip()
        )

    return "\n\n".join(commands)


def _show_summary(resources: ChapterResources, outdir: Path) -> None:
    print_header("建议命令")
    print_info(_build_summary(resources, outdir))
    print_success("复制命令后即可在终端中直接运行。祝创作顺利！")


def _extra_utilities() -> None:
    print_header("额外工具")
    choice = prompt_choice(
        "选择要查看的脚本说明",
        (
            "验证素材完整性 (scripts/validate_assets.py)",
            "仅生成 Audition 标记 CSV (scripts/make_markers.py)",
            "返回主菜单",
        ),
        default=0,
    )

    if choice.startswith("验证素材"):
        print_info(
            dedent(
                """
                用法示例:
                    python scripts/validate_assets.py --root data/chapters/001
                该脚本会检查指定章节目录下是否存在所需的 JSON/TXT/音频文件。
                """
            ).strip()
        )
    elif choice.startswith("仅生成 Audition"):
        print_info(
            dedent(
                """
                用法示例:
                    python scripts/make_markers.py --json data/asr-json/001.json --out out/001.markers.csv
                该脚本会读取 ASR JSON 并导出 Adobe Audition 标记文件。
                """
            ).strip()
        )
    else:
        print_info("返回主菜单。")


def main() -> None:
    _print_banner()
    resources = _collect_chapter_resources()
    outdir = _collect_output_directory()
    _show_summary(resources, outdir)

    if prompt_yes_no("需要查看其他可用脚本吗?", default=False):
        _extra_utilities()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_error("操作已取消。")
