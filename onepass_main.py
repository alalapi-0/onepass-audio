"""OnePass Audio 交互式助手的主入口。"""  # 模块说明：提供一键处理的交互式流程
from __future__ import annotations  # 启用未来注解特性，避免前置字符串引用报错

import json  # 处理 JSON 配置与结果文件
import os  # 管理环境变量用于子进程
import re  # 正则提取服务端口
import shlex  # 构建 shell 风格命令展示
import subprocess  # 调用外部脚本与命令
import sys  # 访问命令行参数与退出函数
import threading  # 监听子进程输出
import webbrowser  # 自动打开浏览器显示可视化面板
from dataclasses import asdict, dataclass  # 引入数据类工具与转换字典的方法
from pathlib import Path  # 使用 Path 进行跨平台路径操作
from typing import Dict, Iterable, List, Optional, Tuple  # 导入常用类型注解

from onepass import __version__  # 引入包版本信息用于展示
from onepass.align import align_sentences  # 引入句子对齐核心函数
from onepass.asr_loader import Word, load_words  # 引入词级别数据结构与加载函数
from onepass.edl import EDL, build_keep_last_edl  # 引入 EDL 数据结构与构建函数
from onepass.edl_renderer import (  # 引入音频渲染相关工具
    load_edl as load_keep_edl,
    normalize_segments as normalize_keep_segments,
    probe_duration as probe_audio_duration,
    render_audio as render_clean_audio,
    resolve_source_audio as resolve_audio_path,
)
from onepass.markers import write_audition_markers  # 引入写入 Audition 标记的工具
from onepass.pipeline import PreparedSentences, prepare_sentences  # 引入句子预处理逻辑
from onepass.textnorm import Sentence  # 引入规范化后的句子结构
from onepass.retake_keep_last import (  # 引入“保留最后一遍”所需函数
    compute_retake_keep_last,
    export_audition_markers,
    export_edl_json,
    export_srt,
    export_txt,
)
from onepass.logging_utils import default_log_dir  # 引入统一日志目录工具
from onepass.ux import (  # 引入命令行交互的工具函数
    print_error,  # 打印错误信息的工具
    print_header,  # 打印分组标题的工具
    print_info,  # 打印普通提示的工具
    print_success,  # 打印成功提示的工具
    print_warning,  # 打印警告提示的工具
    prompt_existing_file,  # 询问并校验文件存在性的函数
    prompt_existing_directory,  # 询问并校验目录存在性的函数
    prompt_text,  # 自由输入文本
    prompt_yes_no,  # 询问用户是否继续的布尔函数
)


ROOT_DIR = Path(__file__).resolve().parent  # 计算项目根目录，方便拼接相对路径
DEFAULT_MATERIALS_DIR = ROOT_DIR / "materials"  # 默认素材目录，存放 JSON/TXT/音频
DEFAULT_OUT_DIR = ROOT_DIR / "out"  # 默认输出目录，统一存放产出文件
DEFAULT_NORMALIZED_DIR = ROOT_DIR / "out" / "norm"  # 默认的规范文本目录
DEFAULT_NORMALIZE_REPORT = ROOT_DIR / "out" / "normalize_report.csv"  # 规范化脚本的默认报告路径
DEFAULT_SCORE_THRESHOLD = 80  # 对齐得分的默认阈值，低于则提示人工确认
AUDIO_PRIORITY = {  # 音频格式优先级映射，数值越小优先级越高
    ".wav": 0,  # 无损 WAV 优先
    ".flac": 1,  # FLAC 次之
    ".m4a": 2,  # AAC 容器格式位于中段
    ".aac": 3,  # 原生 AAC 文件
    ".mp3": 4,  # 有损 MP3
    ".ogg": 5,  # OGG 容器
    ".wma": 6,  # WMA 最低优先级
}


@dataclass  # 使用数据类装饰器自动生成初始化等方法
class ChapterResources:
    """存放单章节所需素材路径的集合。"""  # 数据类用于描述章节的输入资源

    stem: str  # 章节文件名前缀，用于匹配同名资源
    asr_json: Path  # 指向 ASR 词级时间戳 JSON 的路径
    original_txt: Path  # 指向原始文本稿的路径
    audio_file: Path | None  # 对应音频文件的路径，可能缺失因此允许 None


@dataclass  # 使用数据类便于序列化与类型提示
class ChapterSummary:
    """记录单个章节产出的概要信息。"""  # 描述生成结果的容器

    stem: str  # 章节前缀，用于识别结果归属
    subtitle_path: Path  # 最终字幕文件的路径
    transcript_path: Path  # 清洗后的文本稿路径
    edl_path: Path  # 保留最后一遍生成的 EDL 路径
    markers_path: Path  # Adobe Audition 标记文件路径
    audio_path: Path | None  # 裁切后的音频路径（可选）
    kept_sentences: int  # 最终保留的句子数量
    duplicate_windows: int  # 识别出的重复窗口数量
    unaligned_sentences: int  # 未能对齐的句子数量
    cut_seconds: float  # 总共剪掉的时长（秒）


def _print_banner() -> None:  # 打印应用标题与版本信息
    print_header("OnePass Audio — 录完即净，一遍过")  # 输出应用名称作为欢迎语
    print_info(f"版本: {__version__}")  # 打印当前版本号
    print_info("本程序将自动匹配素材并批量生成字幕、EDL 等文件。\n")  # 提示主要功能并留空行


def _prompt_materials_directory() -> Path:  # 询问并校验素材目录
    print_header("素材目录")  # 显示当前步骤标题
    default_dir: Optional[Path] = DEFAULT_MATERIALS_DIR if DEFAULT_MATERIALS_DIR.exists() else None  # 若默认目录存在则提示
    return prompt_existing_directory(  # 调用交互函数获取素材目录
        "包含 JSON/TXT/音频 的素材文件夹路径",  # 向用户说明目录需要包含的素材类型
        default=default_dir,  # 如果有默认值则作为建议输入
    )


def _ensure_output_directory() -> Path:  # 询问输出目录并保证存在
    print_header("输出目录")  # 显示输出路径的标题
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)  # 确保默认输出目录存在
    return prompt_existing_directory(
        "输出文件夹 (会在其中生成字幕/EDL 等)",
        default=DEFAULT_OUT_DIR,
    )  # 询问用户确认或修改输出目录


def _clean_input_path(raw: str) -> str:  # 清理用户输入的路径字符串
    cleaned = raw.strip().strip('"').strip("'")  # 去掉首尾空白与引号
    return cleaned or raw.strip()  # 若清理后为空则返回去除空白的原值


def _prompt_optional_int(prompt: str) -> int | None:  # 允许留空的正整数输入
    while True:  # 持续提示直到获得合法值
        raw = _clean_input_path(prompt_text(prompt, allow_empty=True))  # 读取用户输入
        if not raw:  # 留空表示沿用默认
            return None
        try:
            value = int(raw)  # 尝试解析为整数
        except ValueError:  # 解析失败
            print_warning("请输入正整数或直接回车留空。")
            continue
        if value <= 0:  # 非正数需要重新输入
            print_warning("数值必须大于 0。")
            continue
        return value


def _prompt_processing_mode() -> str:
    """询问批处理模式。"""

    print_header("选择处理模式")
    print_info("[1] 一键流水线：规范化 → 保留最后一遍 → 自动渲染（有音频才渲染）")
    print_info("[2] 仅保留最后一遍（跳过规范化）")
    print_info("[3] 仅执行规范化")
    print_info("[4] 仅渲染音频（需要已有 EDL 与音频）")
    while True:
        mode = _clean_input_path(prompt_text("请选择处理模式", default="1"))
        if mode in {"1", "2", "3", "4"}:
            return mode
        print_warning("请输入 1/2/3/4 中的一个选项。")


def _run_all_in_one_cli(materials_dir: Path, out_dir: Path) -> None:
    """调用统一 CLI 执行一键流水线。"""

    cli_script = ROOT_DIR / "scripts" / "onepass_cli.py"
    if not cli_script.exists():
        print_warning("未找到 scripts/onepass_cli.py，无法执行一键流水线。")
        return
    char_map = ROOT_DIR / "config" / "default_char_map.json"
    cmd = [
        sys.executable,
        str(cli_script),
        "all-in-one",
        "--in",
        str(materials_dir),
        "--out",
        str(out_dir),
        "--emit-align",
        "--opencc",
        "none",
        "--glob-text",
        "*.txt",
        "--glob-words",
        "*.words.json",
        "--render",
        "auto",
        "--glob-audio",
        "*.wav;*.m4a;*.mp3;*.flac",
        "--no-interaction",
    ]
    if char_map.exists():
        cmd.extend(["--char-map", str(char_map)])
    else:
        print_warning("未找到默认字符映射，将在流水线中跳过字符映射阶段。")
    print_info("等价 CLI：")
    print_info(shlex.join(cmd))
    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))
    except FileNotFoundError as exc:
        print_error(f"无法调用 Python 解释器执行 all-in-one: {exc}")
        return
    if result.returncode != 0:
        print_warning("all-in-one CLI 返回非零状态，请查看上方日志排查。")
    else:
        print_success(f"流水线已完成。输出目录: {out_dir}")


def _run_norm_only_cli(materials_dir: Path, out_dir: Path) -> None:
    """仅执行规范化阶段。"""

    cli_script = ROOT_DIR / "scripts" / "onepass_cli.py"
    if not cli_script.exists():
        print_warning("未找到 scripts/onepass_cli.py，无法执行规范化。")
        return
    norm_out = out_dir / "norm"
    try:
        norm_out.resolve().relative_to((ROOT_DIR / "out").resolve())
    except ValueError:
        print_warning("规范化输出必须位于 out/ 目录下，已回退到默认 out/norm。")
        norm_out = DEFAULT_NORMALIZED_DIR
    norm_out.mkdir(parents=True, exist_ok=True)
    char_map = ROOT_DIR / "config" / "default_char_map.json"
    cmd = [
        sys.executable,
        str(cli_script),
        "prep-norm",
        "--in",
        str(materials_dir),
        "--out",
        str(norm_out),
        "--glob",
        "*.txt",
        "--emit-align",
        "--opencc",
        "none",
    ]
    if char_map.exists():
        cmd.extend(["--char-map", str(char_map)])
    else:
        print_warning("未找到默认字符映射，将跳过字符映射配置。")
    print_info("等价 CLI：")
    print_info(shlex.join(cmd))
    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))
    except FileNotFoundError as exc:
        print_error(f"无法调用 Python 解释器执行 prep-norm: {exc}")
        return
    if result.returncode != 0:
        print_warning("prep-norm CLI 返回非零状态，请检查终端输出。")
    else:
        print_success(f"规范化完成，输出目录: {norm_out}")


def _run_render_only_cli(materials_dir: Path, out_dir: Path) -> None:
    """仅执行渲染阶段。"""

    cli_script = ROOT_DIR / "scripts" / "onepass_cli.py"
    if not cli_script.exists():
        print_warning("未找到 scripts/onepass_cli.py，无法执行渲染。")
        return
    cmd = [
        sys.executable,
        str(cli_script),
        "render-audio",
        "--materials",
        str(out_dir),
        "--audio-root",
        str(materials_dir),
        "--out",
        str(out_dir),
    ]
    print_info("等价 CLI：")
    print_info(shlex.join(cmd))
    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))
    except FileNotFoundError as exc:
        print_error(f"无法调用 Python 解释器执行 render-audio: {exc}")
        return
    if result.returncode != 0:
        print_warning("render-audio CLI 返回非零状态，请检查终端输出。")
    else:
        print_success(f"渲染阶段完成，输出目录: {out_dir}")


def _run_normalize_original_menu() -> None:  # 交互式调用原文规范化脚本
    print_header("预处理：原文规范化")  # 显示步骤标题

    target_raw = _clean_input_path(prompt_text("输入文件或目录路径", allow_empty=False))  # 获取输入路径
    target_path = Path(target_raw).expanduser().resolve()  # 解析为绝对路径
    if not target_path.exists():  # 路径不存在直接报错
        print_error("指定的文件或目录不存在，请检查后重试。")
        return

    glob_pattern = ""
    if target_path.is_dir():  # 目录模式可配置
        glob_pattern = _clean_input_path(
            prompt_text("匹配模式 (默认 *.txt)", default="*.txt", allow_empty=True)
        )
        if not glob_pattern:
            glob_pattern = "*.txt"

    DEFAULT_NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)  # 确保默认输出存在
    out_raw = _clean_input_path(
        prompt_text(
            "输出目录 (必须位于 out/ 下)",
            default=str(DEFAULT_NORMALIZED_DIR),
            allow_empty=False,
        )
    )
    out_dir = Path(out_raw).expanduser()
    if not out_dir.is_absolute():  # 相对路径基于项目根目录
        out_dir = (ROOT_DIR / out_dir).resolve()
    else:
        out_dir = out_dir.resolve()

    out_root = (ROOT_DIR / "out").resolve()  # out 根目录
    try:
        out_dir.relative_to(out_root)  # 校验输出目录位置
    except ValueError:
        print_error(f"输出目录必须位于 {out_root} 内。")
        return

    opencc_mode = _clean_input_path(
        prompt_text("opencc 模式 (none/t2s/s2t)", default="none", allow_empty=False)
    ).lower()
    if opencc_mode not in {"none", "t2s", "s2t"}:  # 校验 opencc 模式
        print_error("opencc 模式仅支持 none/t2s/s2t。")
        return

    char_map_path = ROOT_DIR / "config" / "default_char_map.json"  # 默认字符映射
    if not char_map_path.exists():  # 缺少配置文件
        print_error("未找到 config/default_char_map.json，请先拉取仓库最新配置。")
        return

    script_path = ROOT_DIR / "scripts" / "normalize_original.py"  # 脚本路径
    if not script_path.exists():  # 缺少脚本
        print_error("未找到 scripts/normalize_original.py，请确认仓库已更新。")
        return

    print_info("请选择输出模式：")
    print_info("1) 仅 .norm（保留原标点，不生成 .asr）")
    print_info("2) .norm + .asr（去换行 + 去标点［保留句末］）")
    print_info("3) .norm + .asr（去换行 + 去全部标点）")
    while True:
        mode_choice = _clean_input_path(prompt_text("输出模式", default="2", allow_empty=False))
        if mode_choice in {"1", "2", "3"}:
            break
        print_warning("请输入 1/2/3。")

    cmd = [
        sys.executable,
        str(script_path),
        "--in",
        str(target_path),
        "--out",
        str(out_dir),
        "--char-map",
        str(char_map_path),
        "--opencc",
        opencc_mode,
    ]
    if glob_pattern:
        cmd.extend(["--glob", glob_pattern])

    extra_args: list[str] = []
    emit_asr_selected = True
    if mode_choice == "1":
        extra_args.append("--no-emit-asr")
        emit_asr_selected = False
    elif mode_choice == "2":
        extra_args.extend(["--profile", "asr", "--strip-punct-mode", "keep-eos"])
    elif mode_choice == "3":
        extra_args.extend(["--profile", "asr", "--strip-punct-mode", "all"])
    cmd.extend(extra_args)

    dry_run = prompt_yes_no("是否仅生成报表 (Dry-Run)?", default=False)
    if dry_run:
        cmd.append("--dry-run")

    cli_script = ROOT_DIR / "scripts" / "onepass_cli.py"
    if cli_script.exists():
        cli_cmd = [
            sys.executable,
            str(cli_script),
            "prep-norm",
            "--in",
            str(target_path),
            "--out",
            str(out_dir),
            "--char-map",
            str(char_map_path),
            "--opencc",
            opencc_mode,
        ]
        if glob_pattern:
            cli_cmd.extend(["--glob", glob_pattern])
        if dry_run:
            cli_cmd.append("--dry-run")
        print_info("统一 CLI 等价命令（当前未包含 ASR 预设参数）:")
        print_info(shlex.join(cli_cmd))
        if extra_args:
            print_warning("如需 --profile asr 或关闭 .asr 输出，请直接执行 normalize_original.py。")
    else:
        print_warning("未找到 scripts/onepass_cli.py，暂无法展示统一 CLI 命令。")

    print_info("内部将执行旧版脚本命令:")
    print_info(shlex.join(cmd))
    if not prompt_yes_no("确认执行上述命令?", default=True):
        print_warning("已取消原文规范化。")
        return

    out_dir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    before_norm = set(out_dir.rglob("*.norm.txt"))  # 记录执行前的 norm 文件
    before_asr: set[Path] = set()
    if emit_asr_selected and not dry_run:
        before_asr = set(out_dir.rglob("*.asr.txt"))

    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))  # 执行脚本
    except FileNotFoundError as exc:
        print_error(f"无法调用规范化脚本: {exc}")
        return

    if result.returncode != 0:
        print_error("规范化脚本执行失败，请根据上方输出排查问题。")
        return

    report_path = DEFAULT_NORMALIZE_REPORT
    if report_path.exists():
        print_info(f"报表: {report_path}")
    else:
        print_warning("未找到 normalize_report.csv，请确认脚本输出。")

    if dry_run:
        print_success("Dry-Run 已完成，可根据上方命令实际执行。")
        return

    after_norm = set(out_dir.rglob("*.norm.txt"))  # 执行后的 norm 文件
    new_norm = [p for p in after_norm - before_norm if p.is_file()]
    print_success(f"本次共生成 {len(new_norm)} 个规范化文本。输出目录: {out_dir}")
    if not new_norm:
        print_warning("未检测到新增 .norm.txt，可能输入为空或文件已存在。")
    if emit_asr_selected:
        after_asr = set(out_dir.rglob("*.asr.txt"))
        new_asr = [p for p in after_asr - before_asr if p.is_file()]
        print_info(f"新增 .asr.txt 数量：{len(new_asr)}")


def _run_edl_render_menu() -> None:  # 交互式调用 EDL 渲染脚本
    print_header("按 EDL 渲染干净音频")  # 输出步骤标题

    edl_path = prompt_existing_file("拖拽或输入 EDL JSON 路径")  # 获取 EDL 文件
    default_audio_root = (
        DEFAULT_MATERIALS_DIR if DEFAULT_MATERIALS_DIR.exists() else edl_path.parent
    )  # 默认音频目录
    audio_root = prompt_existing_directory(
        "源音频所在目录 (用于解析 EDL 中的相对路径)",
        default=default_audio_root,
    )  # 询问音频根目录

    out_default = DEFAULT_OUT_DIR if DEFAULT_OUT_DIR.exists() else edl_path.parent
    out_raw = _clean_input_path(
        prompt_text(
            "输出目录 (不存在会自动创建)",
            default=str(out_default),
            allow_empty=False,
        )
    )
    out_dir = Path(out_raw).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在

    samplerate = _prompt_optional_int("输出采样率 (Hz，可留空沿用原始设置)")
    channels = _prompt_optional_int("输出声道数 (可留空沿用原始设置)")

    try:
        edl = load_keep_edl(edl_path)  # 加载 EDL 内容
        source_audio = resolve_audio_path(edl, edl_path, audio_root)  # 解析源音频
        duration = probe_audio_duration(source_audio)  # 获取音频总时长
        keeps = normalize_keep_segments(edl.segments, duration)  # 计算保留片段
    except Exception as exc:
        print_error(f"解析 EDL 失败: {exc}")
        return

    keep_duration = sum(segment.end - segment.start for segment in keeps)  # 汇总保留时长
    if keep_duration <= 0:
        print_error("有效保留时长为 0，无法执行渲染。")
        return

    effective_samplerate = samplerate or edl.samplerate
    effective_channels = channels or edl.channels
    out_path = out_dir / f"{source_audio.stem}.clean.wav"

    print_info(f"源音频: {source_audio}")
    print_info(f"输出文件: {out_path}")
    print_info(f"保留片段 {len(keeps)} 段，总计 {keep_duration:.3f}s")
    if samplerate is not None:
        print_info(f"目标采样率: {effective_samplerate} Hz（用户输入）")
    elif effective_samplerate:
        print_info(f"目标采样率: {effective_samplerate} Hz（来自 EDL 建议）")
    if channels is not None:
        print_info(f"目标声道数: {effective_channels}（用户输入）")
    elif effective_channels:
        print_info(f"目标声道数: {effective_channels}（来自 EDL 建议）")

    script_path = ROOT_DIR / "scripts" / "edl_render.py"
    if not script_path.exists():
        print_error("未找到 scripts/edl_render.py，请确认仓库已更新。")
        return

    cmd = [
        sys.executable,
        str(script_path),
        "--edl",
        str(edl_path),
        "--audio-root",
        str(audio_root),
        "--out",
        str(out_dir),
    ]
    if samplerate is not None:
        cmd.extend(["--samplerate", str(samplerate)])
    if channels is not None:
        cmd.extend(["--channels", str(channels)])

    cli_script = ROOT_DIR / "scripts" / "onepass_cli.py"
    if cli_script.exists():
        cli_cmd = [
            sys.executable,
            str(cli_script),
            "render-audio",
            "--edl",
            str(edl_path),
            "--audio-root",
            str(audio_root),
            "--out",
            str(out_dir),
        ]
        if samplerate is not None:
            cli_cmd.extend(["--samplerate", str(samplerate)])
        if channels is not None:
            cli_cmd.extend(["--channels", str(channels)])
        print_info("统一 CLI 等价命令:")
        print_info(shlex.join(cli_cmd))
    else:
        print_warning("未找到 scripts/onepass_cli.py，暂无法展示统一 CLI 命令。")

    dry_run = prompt_yes_no("是否仅预览渲染命令 (Dry-Run)?", default=False)
    if dry_run:
        cmd.append("--dry-run")

    print_info("内部将执行旧版脚本命令:")
    print_info(shlex.join(cmd))

    if not prompt_yes_no("确认执行上述命令?", default=True):
        print_warning("已取消音频渲染。")
        return

    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))
    except FileNotFoundError as exc:
        print_error(f"无法调用 Python 解释器执行脚本: {exc}")
        return

    if result.returncode != 0:
        print_error("渲染脚本执行失败，请根据上方输出排查问题。")
        return

    if dry_run:
        print_success("Dry-Run 已完成，可根据上方命令实际执行。")
        return

    print_success(f"已完成干净音频渲染: {out_path}")
    print_info(f"保留片段 {len(keeps)} 段，总计 {keep_duration:.3f}s")


def _run_all_in_one_menu() -> None:  # 一键流水线入口
    print_header("一键流水线：规范化 → 保留最后一遍 → 渲染音频")

    materials_dir = _prompt_materials_directory()  # 素材目录
    default_audio_root = materials_dir  # 默认音频根目录
    audio_root = prompt_existing_directory(
        "音频搜索根目录",
        default=default_audio_root,
    )

    suggested_out = DEFAULT_OUT_DIR / materials_dir.name  # 建议的输出目录
    out_raw = _clean_input_path(
        prompt_text(
            "输出目录 (默认 out/<素材名>)",
            default=str(suggested_out),
            allow_empty=True,
        )
    )
    if not out_raw:
        out_raw = str(suggested_out)
    out_dir = Path(out_raw).expanduser()
    if not out_dir.is_absolute():
        out_dir = (ROOT_DIR / out_dir).resolve()
    else:
        out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    do_norm = prompt_yes_no("是否执行文本规范化阶段?", default=True)
    norm_glob = "*.txt"
    opencc_mode = "none"
    norm_dry_run = False
    char_map_path = ROOT_DIR / "config" / "default_char_map.json"
    if do_norm:
        opencc_mode = _clean_input_path(
            prompt_text("opencc 模式 (none/t2s/s2t)", default="none", allow_empty=False)
        ).lower()
        if opencc_mode not in {"none", "t2s", "s2t"}:
            print_error("opencc 模式仅支持 none/t2s/s2t。")
            return
        norm_glob_raw = _clean_input_path(
            prompt_text("规范化匹配模式 (默认 *.txt)", default="*.txt", allow_empty=True)
        )
        if norm_glob_raw:
            norm_glob = norm_glob_raw
        norm_dry_run = prompt_yes_no("规范化阶段是否仅生成报表 (Dry-Run)?", default=False)
        if not char_map_path.exists():
            print_error("未找到 config/default_char_map.json，请先准备字符映射配置。")
            return

    glob_words_raw = _clean_input_path(
        prompt_text("词级 JSON 匹配模式 (默认 *.words.json)", default="*.words.json", allow_empty=True)
    )
    glob_words = glob_words_raw or "*.words.json"

    glob_text_raw = _clean_input_path(
        prompt_text(
            "文本匹配模式，多个以空格或分号分隔 (默认 *.norm.txt *.txt)",
            default="*.norm.txt *.txt",
            allow_empty=True,
        )
    )
    glob_text_patterns = [part for part in glob_text_raw.replace(";", " ").split() if part] if glob_text_raw else ["*.norm.txt", "*.txt"]

    run_render = prompt_yes_no("是否在结尾执行音频渲染?", default=True)
    glob_edl_patterns = ["*.keepLast.edl.json"]
    samplerate = None
    channels = None
    if run_render:
        glob_edl_raw = _clean_input_path(
            prompt_text(
                "EDL 匹配模式 (默认 *.keepLast.edl.json)",
                default="*.keepLast.edl.json",
                allow_empty=True,
            )
        )
        if glob_edl_raw:
            glob_edl_patterns = [part for part in glob_edl_raw.replace(";", " ").split() if part]
        samplerate = _prompt_optional_int("渲染采样率 (Hz，可留空)")
        channels = _prompt_optional_int("渲染声道数 (可留空)")

    workers = _prompt_optional_int("并发线程数 (可留空)")

    cli_script = ROOT_DIR / "scripts" / "onepass_cli.py"
    if not cli_script.exists():
        print_error("未找到 scripts/onepass_cli.py，无法执行统一流水线。")
        return

    cli_cmd = [
        sys.executable,
        str(cli_script),
        "all-in-one",
        "--materials",
        str(materials_dir),
        "--audio-root",
        str(audio_root),
        "--out",
        str(out_dir),
        "--glob-words",
        glob_words,
    ]
    for pattern in glob_text_patterns:
        cli_cmd.extend(["--glob-text", pattern])
    if do_norm:
        cli_cmd.extend(["--do-norm", "--opencc", opencc_mode, "--norm-glob", norm_glob, "--char-map", str(char_map_path)])
        if norm_dry_run:
            cli_cmd.append("--dry-run")
    if run_render:
        cli_cmd.append("--render")
        for pattern in glob_edl_patterns:
            cli_cmd.extend(["--glob-edl", pattern])
    if samplerate:
        cli_cmd.extend(["--samplerate", str(samplerate)])
    if channels:
        cli_cmd.extend(["--channels", str(channels)])
    if workers:
        cli_cmd.extend(["--workers", str(workers)])

    print_info("统一 CLI 等价命令:")
    print_info(shlex.join(cli_cmd))
    if not prompt_yes_no("确认执行上述命令?", default=True):
        print_warning("已取消一键流水线。")
        return

    try:
        result = subprocess.run(cli_cmd, check=False, cwd=str(ROOT_DIR))
    except FileNotFoundError as exc:
        print_error(f"无法调用统一 CLI: {exc}")
        return

    if result.returncode != 0:
        print_error("统一流水线执行失败，请根据上方输出排查问题。")
        return

    report_path = out_dir / "batch_report.json"
    if report_path.exists():
        print_success(f"流水线已完成，报告位置: {report_path}")
    else:
        print_success("流水线已完成。")


def _run_env_check_menu() -> None:  # 环境自检流程
    print_header("环境自检与日志位置")  # 显示步骤标题

    script_path = ROOT_DIR / "scripts" / "env_check.py"  # 定位环境自检脚本
    if not script_path.exists():  # 缺少脚本时直接提示
        print_error("未找到 scripts/env_check.py，请先更新仓库。")
        return

    default_out_text = "out"  # 默认报告输出目录
    out_raw = _clean_input_path(  # 获取用户输入
        prompt_text("报告输出目录 (默认 out)", default=default_out_text, allow_empty=True)
    )
    if not out_raw:  # 用户直接回车沿用默认值
        out_raw = default_out_text

    out_path = Path(out_raw).expanduser()  # 支持 ~ 展开
    if not out_path.is_absolute():  # 相对路径基于项目根目录
        out_path = (ROOT_DIR / out_path).resolve()
    else:
        out_path = out_path.resolve()

    try:
        display_script = script_path.relative_to(ROOT_DIR)  # 优先展示相对路径
    except ValueError:
        display_script = script_path
    command_preview = shlex.join(
        [sys.executable, str(display_script), "--out", out_raw, "--auto-fix"]
    )  # 构建展示命令
    print_info("将执行命令:")
    print_info(command_preview)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.Popen(  # 调用环境自检脚本
            [
                sys.executable,
                str(script_path),
                "--out",
                str(out_path),
                "--auto-fix",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except Exception as exc:
        print_error(f"执行环境自检失败: {exc}")
        return

    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
    proc.wait()

    if proc.returncode != 0:  # 非零退出码时提醒用户检查终端输出
        print_warning("环境自检返回非零状态，请结合上方输出排查。")

    report_path = out_path / "env_report.json"  # 默认报告文件
    report_data: dict | None = None
    if report_path.exists():
        try:
            report_data = json.loads(report_path.read_text(encoding="utf-8"))  # 读取 JSON 报告
        except Exception as exc:
            print_error(f"读取报告失败: {exc}")
    else:
        print_warning("未找到 env_report.json，请确认输出目录是否可写。")

    try:
        report_rel = report_path.relative_to(ROOT_DIR)
    except ValueError:
        report_rel = report_path
    if report_path.exists():
        print_success(f"环境检查报告位置: {report_rel}")

    log_dir_path = (ROOT_DIR / default_log_dir()).resolve()  # 统一日志目录
    try:
        log_rel = log_dir_path.relative_to(ROOT_DIR)
    except ValueError:
        log_rel = log_dir_path
    print_info(f"统一日志目录: {log_rel}")

    if not report_data:  # 无可用报告时结束
        return

    problematic = [  # 汇总需要关注的检查项
        item for item in report_data.get("checks", []) if item.get("status") in {"warn", "fail"}
    ]
    if problematic:
        print_warning("检测到需要关注的项目：")
        for item in problematic:
            name = item.get("name", "未知检查")
            status = item.get("status", "warn")
            detail = item.get("detail", "无详细信息")
            advice = item.get("advice")
            print_warning(f"- {name} ({status}) {detail}")
            if advice:
                print_info(f"  建议: {advice}")
    else:
        print_success("所有检查项均通过。")

    notes = report_data.get("summary", {}).get("notes", [])  # 输出额外提示
    for note in notes:
        print_info(f"提示: {note}")


def _index_files_by_stem(paths: Iterable[Path]) -> Dict[str, Path]:  # 按文件名前缀建立索引
    index: Dict[str, Path] = {}  # 初始化映射字典
    for path in sorted(paths):  # 按名称排序遍历路径
        if not path.is_file():  # 忽略子目录
            continue  # 非文件则跳过
        index.setdefault(path.stem.lower(), path.resolve())  # 使用文件名（小写）作为键记录绝对路径
    return index  # 返回构建好的索引


def _discover_chapters(materials_dir: Path) -> List[ChapterResources]:  # 基于素材目录匹配章节资源
    files = list(materials_dir.iterdir())  # 列举素材目录下的所有条目
    json_map = _index_files_by_stem(p for p in files if p.suffix.lower() == ".json")  # 收集 JSON 文件并按前缀映射
    txt_map = _index_files_by_stem(p for p in files if p.suffix.lower() == ".txt")  # 收集 TXT 文件并按前缀映射

    audio_map: Dict[str, Tuple[int, Path]] = {}  # 初始化音频索引，包含优先级与路径
    for path in files:  # 遍历所有条目寻找音频
        if not path.is_file():  # 跳过文件夹
            continue  # 非文件则忽略
        suffix = path.suffix.lower()  # 获取拓展名并统一小写
        if suffix not in AUDIO_PRIORITY:  # 不在支持的音频列表
            continue  # 直接跳过
        priority = AUDIO_PRIORITY[suffix]  # 查找格式优先级
        key = path.stem.lower()  # 获取文件名前缀
        existing = audio_map.get(key)  # 查询是否已有相同前缀
        if existing is None or priority < existing[0]:  # 若未记录或新文件更优
            audio_map[key] = (priority, path.resolve())  # 更新为更高优先级的音频

    missing_txt = sorted(set(json_map) - set(txt_map))  # 找出缺少 TXT 的 JSON 前缀
    for stem in missing_txt:  # 遍历缺失的条目
        print_warning(f"找到 JSON 但缺少同名 TXT: {json_map[stem].name}")  # 提醒用户补齐文本

    missing_json = sorted(set(txt_map) - set(json_map))  # 找出缺少 JSON 的 TXT 前缀
    for stem in missing_json:  # 遍历缺失的条目
        print_warning(f"找到 TXT 但缺少同名 JSON: {txt_map[stem].name}")  # 提醒用户补齐识别结果

    chapters: List[ChapterResources] = []  # 初始化章节资源列表
    for key in sorted(set(json_map) & set(txt_map)):  # 遍历同时存在 JSON/TXT 的前缀
        json_path = json_map[key]  # 获取 JSON 路径
        txt_path = txt_map[key]  # 获取 TXT 路径
        audio_entry = audio_map.get(key)  # 尝试获取音频信息
        audio_path = audio_entry[1] if audio_entry else None  # 若存在则取路径否则为 None
        chapters.append(  # 新增章节资源条目
            ChapterResources(
                stem=json_path.stem,  # 保存基础文件名前缀
                asr_json=json_path,  # 记录 ASR JSON 路径
                original_txt=txt_path,  # 记录原文 TXT 路径
                audio_file=audio_path,  # 附带可能存在的音频
            )
        )

    return chapters  # 返回整理好的章节列表


def _ensure_normalized_text_path(chapter: ChapterResources) -> Path:  # 确保规范文本存在
    """确认规范化文本存在并返回可用路径。"""  # 若没有规范文本则引导用户生成

    norm_path = DEFAULT_NORMALIZED_DIR / f"{chapter.stem}.norm.txt"  # 预期的规范化文本路径
    if norm_path.exists():  # 如果文件已经存在
        print_info(f"使用已规范文本: {norm_path}")  # 提示复用现成文件
        return norm_path  # 直接返回规范化文本

    script_path = ROOT_DIR / "scripts" / "normalize_original.py"  # 规范化脚本的路径
    if not script_path.exists():  # 当脚本缺失时
        print_warning("未找到 scripts/normalize_original.py，将继续使用原始 TXT。")  # 给出警告
        return chapter.original_txt  # 回退使用原稿

    char_map = ROOT_DIR / "config" / "default_char_map.json"  # 默认字符映射路径
    if not char_map.exists():  # 缺少配置时给出提示
        print_warning("未找到 config/default_char_map.json，将继续使用原始 TXT。")
        return chapter.original_txt

    message = (  # 组合交互提示文字
        "未检测到规范化文本，是否现在调用 scripts/normalize_original.py?\n"
        f"原稿: {chapter.original_txt}\n"
        f"输出目录: {DEFAULT_NORMALIZED_DIR}\n"
        "生成报表: out/normalize_report.csv"
    )
    if not prompt_yes_no(message, default=True):  # 如果用户选择不执行
        return chapter.original_txt  # 直接回退使用原稿

    DEFAULT_NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)  # 确保规范化输出目录存在
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在以写入报告
    DEFAULT_NORMALIZE_REPORT.parent.mkdir(parents=True, exist_ok=True)  # 创建报告目录

    before_files = set(DEFAULT_NORMALIZED_DIR.glob("*.norm.txt"))  # 记录执行前已有文件

    cmd = [  # 构建调用规范化脚本的命令行参数
        sys.executable,  # 使用当前 Python 解释器
        str(script_path),  # 规范化脚本路径
        "--in",  # 输入参数标志
        str(chapter.original_txt),  # 原始文本路径
        "--out",  # 输出目录参数
        str(DEFAULT_NORMALIZED_DIR),  # 输出目录
        "--char-map",  # 指定字符映射
        str(char_map),  # 映射文件路径
        "--opencc",  # 繁简转换模式
        "none",  # 默认不调用 opencc
    ]

    print_info("正在规范化原稿，稍候…")  # 告知用户脚本正在执行
    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))  # 在项目根目录运行脚本
    except FileNotFoundError as exc:  # 捕获解释器缺失或脚本不可执行
        print_error(f"无法调用规范化脚本: {exc}")  # 打印错误信息
        return chapter.original_txt  # 回退使用原稿

    if result.returncode != 0:  # 脚本执行失败
        print_warning("规范化脚本执行失败，将继续使用原始 TXT。")
        return chapter.original_txt

    after_files = set(DEFAULT_NORMALIZED_DIR.glob("*.norm.txt"))  # 统计执行后的文件
    if norm_path in after_files and norm_path not in before_files:  # 成功生成目标文件
        print_success(f"已生成规范文本: {norm_path.name}")  # 提示生成成功
        return norm_path  # 返回新生成的规范文本

    if norm_path.exists():  # 已存在同名文件
        print_info(f"复用已有规范文本: {norm_path.name}")
        return norm_path

    print_warning("规范化脚本未生成目标文件，将继续使用原始 TXT。")
    return chapter.original_txt  # 回退使用原稿


def _warn_mismatch(words: List[Word], sentences: List[Sentence]) -> None:  # 检查词句数量是否匹配
    if not words or not sentences:  # 若任一列表为空则无需提示
        return  # 直接跳过
    if len(sentences) > len(words) * 1.5:  # 句子数量明显超出词数量时
        print_warning("原稿句子数量明显多于 ASR 词数量，可能存在内容不匹配。")  # 提示用户检查输入


def _serialise_edl(edl: EDL) -> dict:  # 将 EDL 对象转换成字典
    payload = {  # 构造可写入 JSON 的字典
        "audio_stem": edl.audio_stem,  # 音频基名
        "sample_rate": edl.sample_rate,  # 音频采样率
        "samplerate": edl.sample_rate,  # 兼容新式字段名
        "actions": [asdict(action) for action in edl.actions],  # 将所有剪辑动作转为字典
        "stats": edl.stats,  # 附带统计信息
        "created_at": edl.created_at,  # 记录生成时间
    }
    source_audio = getattr(edl, "source_audio", None)
    if source_audio:
        payload["source_audio"] = source_audio  # 写入源音频路径
    return payload


def _format_srt_timestamp(seconds: float) -> str:  # 将秒转换为 SRT 时间戳
    milliseconds = max(0, int(round(seconds * 1000)))  # 将秒转换为毫秒并确保非负
    hours, remainder = divmod(milliseconds, 3_600_000)  # 计算小时与剩余毫秒
    minutes, remainder = divmod(remainder, 60_000)  # 计算分钟与剩余毫秒
    secs, millis = divmod(remainder, 1_000)  # 计算秒与毫秒
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"  # 返回 SRT 格式时间戳


def _write_srt(entries: List[Tuple[float, float, str]], out_path: Path) -> None:  # 输出 SRT 字幕文件
    lines: List[str] = []  # 用于收集输出行
    for index, (start, end, text) in enumerate(entries, start=1):  # 遍历每条字幕
        lines.append(str(index))  # 写入序号
        lines.append(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}")  # 写入起止时间
        payload = text.splitlines() or [""]  # 支持多行文本
        lines.extend(payload)  # 写入字幕内容
        lines.append("")  # 每条字幕之间插入空行
    out_path.write_text("\n".join(lines).strip() + "\n" if lines else "", encoding="utf-8")  # 写入文件并保证结尾换行


def _write_plain_transcript(entries: List[Tuple[float, float, str]], out_path: Path) -> None:  # 输出纯文本稿
    text = "\n".join(content for _, _, content in entries)  # 拼接所有字幕文本
    out_path.write_text((text + "\n") if text else "", encoding="utf-8")  # 写入纯文本稿件


def _render_audio(audio: Path, edl_path: Path, out_dir: Path) -> Path | None:  # 调用新模块渲染音频
    try:
        output = render_clean_audio(  # 直接调用库函数
            edl_path,
            audio.parent,
            out_dir,
            None,
            None,
            dry_run=False,
        )
    except Exception as exc:
        print_error(f"音频导出失败: {exc}")  # 提示失败原因
        return None

    print_success(f"已导出干净音频: {output.name}")  # 输出成功提示
    return output


def _process_chapter(  # 处理单章素材并汇总结果
    chapter: ChapterResources,
    outdir: Path,
    *,
    score_threshold: int,
    render_audio: bool,
) -> ChapterSummary | None:
    try:  # 尝试读取词级 JSON
        words = load_words(chapter.asr_json)  # 读取词级时间戳 JSON
    except Exception as exc:  # 捕获解析异常
        print_error(f"读取 ASR JSON 失败: {exc}")  # 提示错误原因
        return None  # 终止该章节处理

    text_path = _ensure_normalized_text_path(chapter)  # 获取规范化文本路径（必要时触发生成）
    try:  # 尝试读取规范化文本
        raw_text = text_path.read_text(encoding="utf-8")  # 读取文本内容
    except Exception as exc:  # 读取失败时
        print_error(f"读取原稿 TXT 失败: {exc}")  # 提示错误
        return None  # 停止处理

    prepared: PreparedSentences = prepare_sentences(raw_text)  # 对文本进行句子拆分与规范化
    sentences = prepared.alignment  # 对齐用句子列表
    display_texts = prepared.display  # 展示用原句列表

    if not sentences:  # 如果没有有效句子
        print_warning("原稿中没有有效的句子，跳过该文件。")  # 提示用户检查文本
        return None  # 结束处理

    _warn_mismatch(words, sentences)  # 检查句子与词数量是否显著不匹配

    align = align_sentences(words, sentences, score_threshold=score_threshold)  # 执行句子对齐
    edl = build_keep_last_edl(words, align)  # 根据对齐结果构建保留最后一遍的 EDL
    edl.audio_stem = chapter.stem  # 将章节前缀写入 EDL

    subtitle_entries: List[Tuple[float, float, str]] = []  # 准备字幕条目列表
    for idx, match in sorted(align.kept.items()):  # 遍历所有保留的句子
        if match is None:  # 未对齐则跳过
            continue
        if idx >= len(display_texts):  # 安全防护避免越界
            continue
        subtitle_entries.append((match.start, match.end, display_texts[idx]))  # 收集时间范围与原句

    outdir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    srt_path = outdir / f"{chapter.stem}.keepLast.srt"  # 字幕输出路径
    txt_path = outdir / f"{chapter.stem}.keepLast.txt"  # 纯文本输出路径
    edl_path = outdir / f"{chapter.stem}.keepLast.edl.json"  # EDL 输出路径
    markers_path = outdir / f"{chapter.stem}.keepLast.audition_markers.csv"  # Audition 标记输出路径

    if chapter.audio_file is not None:  # 若存在对应音频
        try:
            relative = chapter.audio_file.relative_to(ROOT_DIR)
            setattr(edl, "source_audio", relative.as_posix())  # 存储相对路径
        except ValueError:
            setattr(edl, "source_audio", chapter.audio_file.as_posix())  # 回退为绝对路径

    try:
        with edl_path.open("w", encoding="utf-8") as fh:  # 打开 EDL 文件
            json.dump(_serialise_edl(edl), fh, ensure_ascii=False, indent=2)  # 写入 EDL 数据
        _write_srt(subtitle_entries, srt_path)  # 写入字幕文件
        _write_plain_transcript(subtitle_entries, txt_path)  # 写入纯文本稿
        write_audition_markers(edl, markers_path)  # 写入 Audition 标记
    except Exception as exc:  # 捕获任意写入异常
        print_error(f"写入输出文件失败: {exc}")  # 提示错误
        return None  # 停止该章节处理

    kept_count = sum(1 for m in align.kept.values() if m is not None)  # 统计保留句子数量
    duplicate_windows = sum(len(windows) for windows in align.dups.values())  # 重复窗口总数
    unaligned_count = len(align.unaligned)  # 未对齐句子数量
    cut_seconds = float(edl.stats.get("total_cut_sec", 0.0)) if isinstance(edl.stats, dict) else 0.0  # 剪切总时长

    if align.unaligned:  # 若存在未对齐句子
        samples: List[str] = []  # 用于收集示例
        for idx in align.unaligned[:3]:  # 仅展示前三个样例
            if 0 <= idx < len(display_texts):  # 确保索引有效
                sample = display_texts[idx]  # 获取句子文本
                samples.append(sample if len(sample) <= 20 else sample[:20] + "…")  # 过长时截断
        if samples:  # 若收集到样例
            print_warning("未对齐的句子示例: " + "; ".join(samples))  # 输出提示

    audio_output: Path | None = None  # 初始化音频输出路径
    if render_audio:  # 如果开启音频导出
        if chapter.audio_file is None:  # 没有匹配音频
            print_warning("未找到同名音频文件，跳过音频导出。")  # 提示缺失
        else:  # 找到音频时执行导出
            audio_output = _render_audio(chapter.audio_file, edl_path, outdir)  # 调用导出流程

    print_info(  # 打印统计摘要
        "句子总数 {total}，保留 {kept}，重复窗口 {dup}，未对齐 {unaligned}，去除重复 {cut:.3f}s".format(
            total=len(sentences),  # 总句子数
            kept=kept_count,  # 保留句子数
            dup=duplicate_windows,  # 重复窗口数
            unaligned=unaligned_count,  # 未对齐句子数
            cut=cut_seconds,  # 剪掉的时长
        )
    )
    print_success(f"已生成字幕: {srt_path.name}")  # 通知字幕生成成功
    print_success(f"已生成精简文本: {txt_path.name}")  # 通知文本稿生成成功
    print_success(f"已生成 EDL: {edl_path.name}")  # 通知 EDL 文件生成成功
    print_success(f"已生成 Audition 标记: {markers_path.name}")  # 通知标记文件生成成功

    return ChapterSummary(  # 返回章节处理摘要
        stem=chapter.stem,  # 章节标识
        subtitle_path=srt_path,  # 字幕路径
        transcript_path=txt_path,  # 文本稿路径
        edl_path=edl_path,  # EDL 路径
        markers_path=markers_path,  # 标记路径
        audio_path=audio_output,  # 导出音频路径
        kept_sentences=kept_count,  # 保留句子数
        duplicate_windows=duplicate_windows,  # 重复窗口数
        unaligned_sentences=unaligned_count,  # 未对齐句子数
        cut_seconds=cut_seconds,  # 剪掉的总时长
    )


def _run_singlefile_menu():
    """
    单文件：词级 JSON + 原文（txt/norm.txt） → SRT/TXT/EDL/Markers
    这里用 scripts/onepass_cli.py 的 'single' 子命令兜底：
    python scripts/onepass_cli.py single --json <json> --text <txt> --out <dir>
    若你的 CLI 子命令名字不同，请把下面的 'single' 改成实际的子命令。
    """
    import os, sys, subprocess

    print("\n[单文件处理] 词级 JSON + 原文 → SRT/TXT/EDL/Markers")
    json_path = input("词级 JSON 路径：").strip().strip('"')
    text_path = input("原文 TXT路径：").strip().strip('"')
    out_dir  = input("输出目录（默认 ./out/single）：").strip() or "./out/single"

    os.makedirs(out_dir, exist_ok=True)

    cli = os.path.join('scripts', 'onepass_cli.py')
    if not os.path.exists(cli):
        print("[错误] 未找到 scripts/onepass_cli.py，请确认路径。")
        return

    # 如果你的 CLI 子命令不是 'single'，这里改成实际的（例如 'mk-edl' 等）
    cmd = [sys.executable, cli, 'single', '--json', json_path, '--text', text_path, '--out', out_dir]
    print(">>>", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
        print("[OK] 单文件已处理完成。输出目录：", out_dir)
    except subprocess.CalledProcessError as e:
        print("[失败] 子进程返回非零：", e.returncode)




def _launch_web_panel() -> None:
    """启动本地可视化控制台的 Flask 服务。"""

    print_header("可视化控制台")
    script = ROOT_DIR / "scripts" / "web_panel_server.py"
    if not script.exists():
        print_error("未找到 scripts/web_panel_server.py，请先更新仓库。")
        return

    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    cmd = [sys.executable, str(script), "--port", "8088"]
    print_info(f"启动命令: {shlex.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print_error(f"无法启动服务器: {exc}")
        return

    assert proc.stdout is not None
    port_event = threading.Event()
    detected = {"port": None}

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            match = re.search(r"http://127\\.0\\.0\\.1:(\\d+)", line)
            if match and detected["port"] is None:
                detected["port"] = int(match.group(1))
                port_event.set()
        proc.stdout.close()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        while not port_event.wait(timeout=0.1):
            if proc.poll() is not None:
                return_code = proc.returncode
                print_error(
                    f"可视化控制台启动失败 (exit={return_code})，请检查上方输出。"
                )
                return

        port = detected["port"] or 8088
        base_url = f"http://127.0.0.1:{port}"
        panel_url = f"{base_url}/web/index.html?api={base_url}"
        print_success(f"可视化控制台已运行: {panel_url}")
        try:
            webbrowser.open(panel_url)
        except Exception as exc:  # pragma: no cover - 浏览器未配置
            print_warning(f"自动打开浏览器失败: {exc}")
        print_info(f"如浏览器未自动打开，请手动访问 {panel_url}")
        print_info("按 Ctrl+C 停止服务器。")

        try:
            proc.wait()
        except KeyboardInterrupt:
            print_info("正在关闭服务器...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            reader_thread.join(timeout=1)
        except Exception:  # pragma: no cover - 容错
            pass

def main() -> None:  # CLI 主入口
    _print_banner()  # 展示欢迎信息

    print_header("主菜单")  # 显示主菜单标题
    print_info("[1] 批量处理素材（可选规范化与自动渲染）")
    print_info("[K] 单文件：词级 JSON + 原文 → SRT/TXT/EDL/Markers")
    print_info("[R] 按 EDL 渲染干净音频")
    print_info("[P] 预处理：原文规范化（输出 .norm.txt 与 normalize_report.csv）")
    print_info("[E] 环境自检与日志位置")
    print_info("[W] 启动可视化控制台（本地网页）")
    print_info("[Q] 退出程序")

    choice = _clean_input_path(prompt_text("请选择操作", default="1"))  # 读取选择
    choice_lower = choice.lower()
    if choice_lower == "k":  # 进入单文件保留最后一遍流程
        _run_singlefile_menu()  # 单文件：JSON+TXT → SRT/TXT/EDL/Markers
        return
    if choice_lower == "r":  # 仅执行音频渲染
        _run_edl_render_menu()
        return
    if choice_lower == "p":  # 调用原文规范化流程
        _run_normalize_original_menu()
        return
    if choice_lower == "e":
        _run_env_check_menu()
        return
    if choice_lower == "w":
        _launch_web_panel()
        return
    if choice_lower == "q":  # 用户选择退出
        print_info("已退出。")
        return

    materials_dir = _prompt_materials_directory()  # 询问素材目录
    outdir = _ensure_output_directory()  # 询问输出目录
    mode_choice = _prompt_processing_mode()

    if mode_choice == "1":
        _run_all_in_one_cli(materials_dir, outdir)
        return
    if mode_choice == "3":
        _run_norm_only_cli(materials_dir, outdir)
        return
    if mode_choice == "4":
        _run_render_only_cli(materials_dir, outdir)
        return

    cli_script = ROOT_DIR / "scripts" / "onepass_cli.py"
    if mode_choice == "2":
        print_info("已选择“仅保留最后一遍”，跳过规范化阶段。")
        do_norm_first = False
    else:
        do_norm_first = prompt_yes_no("是否先对原文做规范化?", default=True)
    if do_norm_first:
        if not cli_script.exists():
            print_warning("未找到 scripts/onepass_cli.py，暂无法调用统一 CLI 进行规范化。")
        else:
            norm_out_dir = outdir / "norm"
            try:
                norm_out_dir.resolve().relative_to((ROOT_DIR / "out").resolve())
            except ValueError:
                print_warning("规范化输出必须位于 out/ 目录下，已回退到默认 out/norm。")
                norm_out_dir = DEFAULT_NORMALIZED_DIR
            norm_out_dir.mkdir(parents=True, exist_ok=True)
            char_map = ROOT_DIR / "config" / "default_char_map.json"
            prep_cmd = [
                sys.executable,
                str(cli_script),
                "prep-norm",
                "--in",
                str(materials_dir),
                "--out",
                str(norm_out_dir),
                "--emit-align",
            ]
            if char_map.exists():
                prep_cmd.extend(["--char-map", str(char_map)])
            else:
                print_warning("未找到默认字符映射，将跳过字符映射配置。")
            print_info("规范化阶段等价 CLI:")
            print_info(shlex.join(prep_cmd))
            try:
                result = subprocess.run(prep_cmd, check=False, cwd=str(ROOT_DIR))
            except FileNotFoundError as exc:
                print_error(f"无法调用 Python 解释器执行规范化 CLI: {exc}")
            else:
                if result.returncode != 0:
                    print_warning("规范化 CLI 返回非零状态，请参考终端输出。")
                else:
                    print_success(f"规范化完成，输出目录: {norm_out_dir}")

    print_header("素材匹配")  # 提示开始匹配素材
    chapters = _discover_chapters(materials_dir)  # 根据素材目录构建章节列表
    if not chapters:  # 若未找到任何有效组合
        print_error("未找到任何同时包含 JSON 与 TXT 的素材文件。")  # 提示错误
        return  # 结束程序

    with_audio = sum(1 for chapter in chapters if chapter.audio_file is not None)  # 统计含音频的章节数量
    preview = ", ".join(ch.stem for ch in chapters[:5])  # 截取前五个章节名用于预览
    if len(chapters) > 5:  # 超过五个时添加省略号
        preview += " …"
    print_info(  # 输出匹配结果摘要
        f"共匹配到 {len(chapters)} 套素材，其中 {with_audio} 套包含音频。" +
        (f" 示例: {preview}" if preview else "")
    )

    if cli_script.exists():  # 展示等价 CLI 便于复现
        retake_cmd = [
            sys.executable,
            str(cli_script),
            "retake-keep-last",
            "--materials",
            str(materials_dir),
            "--out",
            str(outdir),
            "--glob-words",
            "*.words.json",
            "--glob-text",
            "*.norm.txt",
            "--glob-text",
            "*.txt",
        ]
        print_info("保留最后一遍等价 CLI:")
        print_info(shlex.join(retake_cmd))

    auto_render = False  # 默认不自动渲染
    if with_audio > 0:  # 仅在存在音频时才询问
        auto_render = prompt_yes_no("是否在生成 EDL 后立即渲染干净音频?", default=True)
        if auto_render and cli_script.exists():
            render_cmd = [
                sys.executable,
                str(cli_script),
                "render-audio",
                "--materials",
                str(outdir),
                "--audio-root",
                str(materials_dir),
                "--out",
                str(outdir),
            ]
            print_info("音频渲染等价 CLI:")
            print_info(shlex.join(render_cmd))

    print_header("批量处理")  # 提示进入批量处理阶段
    summaries: List[ChapterSummary] = []  # 收集每章摘要
    total = len(chapters)  # 章节总数
    for index, chapter in enumerate(chapters, start=1):  # 逐章处理
        print_header(f"[{index}/{total}] {chapter.stem}")  # 显示当前进度
        summary = _process_chapter(
            chapter,  # 当前章节资源
            outdir,  # 输出目录
            score_threshold=DEFAULT_SCORE_THRESHOLD,  # 对齐得分阈值
            render_audio=auto_render,  # 是否导出音频
        )
        if summary:  # 若处理成功
            summaries.append(summary)  # 收集结果

    print_header("处理结果")  # 输出结果汇总标题
    print_success(f"成功处理 {len(summaries)}/{total} 套素材。输出目录: {outdir}")  # 打印成功率与输出目录
    if summaries:  # 如果存在成功条目
        for summary in summaries:  # 遍历摘要
            info = [
                f"保留{summary.kept_sentences}",  # 保留句子数量
                f"重复{summary.duplicate_windows}",  # 重复窗口数量
                f"未对齐{summary.unaligned_sentences}",  # 未对齐句子数量
                f"cut={summary.cut_seconds:.3f}s",  # 剪切时长
            ]
            if summary.audio_path:  # 若有导出音频
                info.append(f"音频→{summary.audio_path.name}")  # 添加音频文件信息
            print_info(f"{summary.stem}: " + ", ".join(info))  # 输出每章统计


if __name__ == "__main__":  # 作为脚本运行时执行主流程
    try:
        main()  # 运行主函数
    except KeyboardInterrupt:  # 捕获 Ctrl+C 中断
        print_error("操作已取消。")  # 提示用户操作已终止
def _derive_single_stem(words_path: Path, text_path: Path) -> str:  # 计算单文件流程的输出前缀
    stem = words_path.stem  # 默认采用词级 JSON 的文件名前缀
    if stem.endswith(".words"):  # 移除常见的 .words 后缀
        stem = stem[:-6]
    if not stem:  # 如果 JSON 名称为空则退回原文 TXT 的前缀
        stem = text_path.stem
    return stem or "output"  # 兜底使用 output


def _run_retake_keep_last_menu() -> None:  # 单文件“保留最后一遍”处理流程
    print_header("词级 JSON + 原文 → SRT/TXT/EDL/Markers（保留最后一遍）")  # 显示步骤标题

    json_path = prompt_existing_file("拖拽或输入词级 ASR JSON 路径")  # 获取词级 JSON
    txt_path = prompt_existing_file("拖拽或输入原文 TXT 路径")  # 获取原文 TXT

    out_default = DEFAULT_OUT_DIR if DEFAULT_OUT_DIR.exists() else ROOT_DIR / "out"  # 输出目录默认值
    out_raw = _clean_input_path(  # 询问输出目录
        prompt_text(
            "输出目录 (不存在会自动创建)",
            default=str(out_default),
            allow_empty=False,
        )
    )
    out_dir = Path(out_raw).expanduser().resolve()  # 解析成绝对路径
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在

    cli_script = ROOT_DIR / "scripts" / "onepass_cli.py"
    if cli_script.exists():
        cli_cmd = [
            sys.executable,
            str(cli_script),
            "retake-keep-last",
            "--words-json",
            str(json_path),
            "--text",
            str(txt_path),
            "--out",
            str(out_dir),
        ]
        print_info("统一 CLI 等价命令:")
        print_info(shlex.join(cli_cmd))
    else:
        print_warning("未找到 scripts/onepass_cli.py，暂无法展示统一 CLI 命令。")

    try:
        doc = load_words(json_path)  # 加载词级 JSON
    except Exception as exc:  # pragma: no cover - 交互流程
        print_error(f"加载词级 JSON 失败: {exc}")
        return

    try:
        result = compute_retake_keep_last(list(doc), txt_path)  # 计算保留最后一遍
    except Exception as exc:  # pragma: no cover - 交互流程
        print_error(f"计算保留最后一遍失败: {exc}")
        return

    stem = _derive_single_stem(json_path, txt_path)  # 推导输出文件前缀

    srt_path = out_dir / f"{stem}.keepLast.srt"  # 字幕路径
    txt_out_path = out_dir / f"{stem}.keepLast.txt"  # 文本路径
    markers_path = out_dir / f"{stem}.audition_markers.csv"  # 标记路径
    edl_path = out_dir / f"{stem}.keepLast.edl.json"  # EDL 路径

    export_srt(result.keeps, srt_path)  # 导出字幕
    export_txt(result.keeps, txt_out_path)  # 导出文本
    export_audition_markers(result.keeps, markers_path)  # 导出 Audition 标记
    export_edl_json(result.edl_keep_segments, None, edl_path)  # 导出 EDL，源音频留空待后续指定

    stats = result.stats  # 读取统计信息
    print_info(
        "总词数 {total_words}，匹配行数 {matched_lines}，严格匹配 {strict_matches}，"
        "LCS 回退 {fallback_matches}，未匹配 {unmatched_lines}".format(**stats)
    )  # 打印统计摘要

    print_success(f"已生成字幕: {srt_path}")  # 回显各产物路径
    print_success(f"已生成文本: {txt_out_path}")
    print_success(f"已生成 Audition 标记: {markers_path}")
    print_success(f"已生成 EDL: {edl_path}")

