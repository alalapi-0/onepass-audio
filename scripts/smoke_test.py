"""OnePass Audio 最小演示脚本。

用法：
    python scripts/smoke_test.py

若本机未安装 ffmpeg/ffprobe，脚本会跳过演示音频渲染步骤，但字幕、EDL、标记等文本产物仍会照常生成。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from onepass.logging_utils import default_log_dir, setup_logger


def _run_command(cmd: Iterable[str], logger, description: str) -> None:
    """执行子进程命令，统一日志与错误处理。"""

    cmd_list = [str(part) for part in cmd]
    logger.info("开始执行步骤: %s", description)
    logger.debug("命令: %s", " ".join(cmd_list))
    print(f"[+] {description}")
    try:
        subprocess.run(cmd_list, check=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - CLI 交互
        logger.exception("命令执行失败: %s", description)
        raise RuntimeError(f"步骤失败：{description} (exit={exc.returncode})") from exc


def _check_file(path: Path, description: str) -> None:
    """确认示例文件存在，若缺失则抛出异常。"""

    if not path.is_file():
        raise FileNotFoundError(f"未找到 {description}：{path}")


def main(argv: list[str] | None = None) -> int:
    """命令行入口，串联示例数据的完整跑通流程。"""

    parser = argparse.ArgumentParser(description="运行 OnePass Audio 最小可复现实例")
    parser.add_argument("--out", default="out", help="输出目录（默认 out）")
    args = parser.parse_args(argv)

    logger = setup_logger(__name__, default_log_dir())
    example_dir = ROOT / "materials" / "example"
    words_json = example_dir / "demo.words.json"
    text_path = example_dir / "demo.txt"
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== OnePass Audio · 5 分钟跑通 ===")

    try:
        _check_file(words_json, "词级 JSON 示例")
        _check_file(text_path, "示例文本")
    except FileNotFoundError as exc:
        logger.error("缺少示例素材: %s", exc)
        print(f"[!] {exc}")
        return 1

    opencc_path = shutil.which("opencc")
    if opencc_path:
        print(f"[✓] 检测到 opencc：{opencc_path}")
    else:
        print("[i] 未检测到 opencc，若无需繁简转换可忽略。")

    retake_cmd = [
        sys.executable,
        ROOT / "scripts" / "retake_keep_last.py",
        "--words-json",
        words_json,
        "--text",
        text_path,
        "--out",
        out_dir,
    ]
    try:
        _run_command(retake_cmd, logger, "执行保留最后一遍导出")
    except RuntimeError as exc:
        print(f"[!] {exc}")
        return 1

    stem = words_json.stem.replace(".words", "")
    if not stem:
        stem = text_path.stem
    stem = stem or "demo"
    srt_path = out_dir / f"{stem}.keepLast.srt"
    txt_path = out_dir / f"{stem}.keepLast.txt"
    markers_path = out_dir / f"{stem}.audition_markers.csv"
    edl_path = out_dir / f"{stem}.keepLast.edl.json"

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if ffmpeg_path and ffprobe_path:
        print(f"[✓] 检测到 ffmpeg：{ffmpeg_path}")
        print(f"[✓] 检测到 ffprobe：{ffprobe_path}")
        audio_path = example_dir / "demo.wav"
        ffmpeg_cmd = [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=10",
            "-ac",
            "1",
            "-ar",
            "48000",
            audio_path,
        ]
        try:
            _run_command(ffmpeg_cmd, logger, "生成示例正弦音频")
        except RuntimeError as exc:
            print(f"[!] {exc}")
            return 1

        set_source_cmd = [
            sys.executable,
            ROOT / "scripts" / "edl_set_source.py",
            "--edl",
            edl_path,
            "--source",
            audio_path,
        ]
        try:
            _run_command(set_source_cmd, logger, "为 EDL 写入 source_audio")
        except RuntimeError as exc:
            print(f"[!] {exc}")
            return 1

        render_cmd = [
            sys.executable,
            ROOT / "scripts" / "edl_render.py",
            "--edl",
            edl_path,
            "--audio-root",
            example_dir,
            "--out",
            out_dir,
            "--samplerate",
            "48000",
            "--channels",
            "1",
        ]
        try:
            _run_command(render_cmd, logger, "按 EDL 渲染干净音频")
        except RuntimeError as exc:
            print(f"[!] {exc}")
            return 1
        clean_audio = out_dir / f"{stem}.clean.wav"
    else:
        print("[i] 未检测到 ffmpeg/ffprobe，跳过示例音频生成与渲染步骤。")
        clean_audio = None

    print("=== 输出产物 ===")
    print(f"字幕：{srt_path}")
    print(f"文本：{txt_path}")
    print(f"Audition 标记：{markers_path}")
    print(f"EDL：{edl_path}")
    if clean_audio:
        print(f"干净音频：{clean_audio}")
    else:
        print("干净音频：跳过（缺少 ffmpeg/ffprobe）")

    print("=== 演示完成 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
