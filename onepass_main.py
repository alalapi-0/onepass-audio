"""OnePass Audio 交互式助手的主入口。"""  # 模块说明：提供一键处理的交互式流程
from __future__ import annotations  # 启用未来注解特性，避免前置字符串引用报错

import json  # 处理 JSON 配置与结果文件
import subprocess  # 调用外部脚本与命令
import sys  # 访问命令行参数与退出函数
from dataclasses import asdict, dataclass  # 引入数据类工具与转换字典的方法
from pathlib import Path  # 使用 Path 进行跨平台路径操作
from typing import Dict, Iterable, List, Optional, Tuple  # 导入常用类型注解

from onepass import __version__  # 引入包版本信息用于展示
from onepass.align import align_sentences  # 引入句子对齐核心函数
from onepass.asr_loader import Word, load_words  # 引入词级别数据结构与加载函数
from onepass.edl import EDL, build_keep_last_edl  # 引入 EDL 数据结构与构建函数
from onepass.markers import write_audition_markers  # 引入写入 Audition 标记的工具
from onepass.pipeline import PreparedSentences, prepare_sentences  # 引入句子预处理逻辑
from onepass.textnorm import Sentence  # 引入规范化后的句子结构
from onepass.ux import (  # 引入命令行交互的工具函数
    print_error,  # 打印错误信息的工具
    print_header,  # 打印分组标题的工具
    print_info,  # 打印普通提示的工具
    print_success,  # 打印成功提示的工具
    print_warning,  # 打印警告提示的工具
    prompt_existing_directory,  # 询问并校验目录存在性的函数
    prompt_yes_no,  # 询问用户是否继续的布尔函数
)


ROOT_DIR = Path(__file__).resolve().parent  # 计算项目根目录，方便拼接相对路径
DEFAULT_MATERIALS_DIR = ROOT_DIR / "materials"  # 默认素材目录，存放 JSON/TXT/音频
DEFAULT_OUT_DIR = ROOT_DIR / "out"  # 默认输出目录，统一存放产出文件
DEFAULT_NORMALIZED_DIR = ROOT_DIR / "data" / "original_txt_norm"  # 默认的规范文本目录
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
    return prompt_existing_directory("输出文件夹 (会在其中生成字幕/EDL 等)", default=DEFAULT_OUT_DIR)  # 询问用户确认或修改输出目录


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

    message = (  # 组合交互提示文字
        "未检测到规范化文本，是否现在调用 scripts/normalize_original.py?\n"
        f"原稿: {chapter.original_txt}\n"
        f"输出: {norm_path}\n"
        "生成 CSV 报告: out/normalize_report.csv"
    )
    if not prompt_yes_no(message, default=True):  # 如果用户选择不执行
        return chapter.original_txt  # 直接回退使用原稿

    DEFAULT_NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)  # 确保规范化输出目录存在
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在以写入报告
    report_path = DEFAULT_NORMALIZE_REPORT  # 报告文件路径
    report_path.parent.mkdir(parents=True, exist_ok=True)  # 创建报告目录

    cmd = [  # 构建调用规范化脚本的命令行参数
        sys.executable,  # 使用当前 Python 解释器
        str(script_path),  # 规范化脚本路径
        "--in",  # 输入参数标志
        str(chapter.original_txt),  # 原始文本路径
        "--out",  # 输出参数标志
        str(norm_path),  # 规范化文本输出路径
        "--report",  # 报告参数标志
        str(report_path),  # 报告文件路径
        "--mode",  # 运行模式标志
        "align",  # 选择对齐模式
    ]

    print_info("正在规范化原稿，稍候…")  # 告知用户脚本正在执行
    try:
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))  # 在项目根目录运行脚本
    except FileNotFoundError as exc:  # 捕获解释器缺失或脚本不可执行
        print_error(f"无法调用规范化脚本: {exc}")  # 打印错误信息
        return chapter.original_txt  # 回退使用原稿

    if result.returncode == 0 and norm_path.exists():  # 如果脚本执行成功且输出存在
        print_success(f"已生成规范文本: {norm_path.name}")  # 提示生成成功
        return norm_path  # 返回新生成的规范文本

    print_warning("规范化脚本执行失败，将继续使用原始 TXT。")  # 未生成结果时提示
    return chapter.original_txt  # 回退使用原稿


def _warn_mismatch(words: List[Word], sentences: List[Sentence]) -> None:  # 检查词句数量是否匹配
    if not words or not sentences:  # 若任一列表为空则无需提示
        return  # 直接跳过
    if len(sentences) > len(words) * 1.5:  # 句子数量明显超出词数量时
        print_warning("原稿句子数量明显多于 ASR 词数量，可能存在内容不匹配。")  # 提示用户检查输入


def _serialise_edl(edl: EDL) -> dict:  # 将 EDL 对象转换成字典
    return {  # 构造可写入 JSON 的字典
        "audio_stem": edl.audio_stem,  # 音频基名
        "sample_rate": edl.sample_rate,  # 音频采样率
        "actions": [asdict(action) for action in edl.actions],  # 将所有剪辑动作转为字典
        "stats": edl.stats,  # 附带统计信息
        "created_at": edl.created_at,  # 记录生成时间
    }


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


def _render_audio(audio: Path, edl_path: Path, output: Path) -> bool:  # 按 EDL 调用脚本导出音频
    script = ROOT_DIR / "scripts" / "edl_to_ffmpeg.py"  # 定位音频导出脚本
    if not script.exists():  # 若脚本缺失
        print_warning("未找到 edl_to_ffmpeg.py，跳过音频导出。")  # 给出提示
        return False  # 返回失败

    cmd = [  # 构建执行音频导出脚本的命令行
        sys.executable,  # 使用当前 Python 解释器
        str(script),  # 脚本路径
        "--audio",  # 音频路径参数
        str(audio),  # 原始音频文件
        "--edl",  # EDL 路径参数
        str(edl_path),  # EDL 文件路径
        "--out",  # 输出文件参数
        str(output),  # 目标音频输出路径
    ]
    try:  # 调用外部脚本尝试导出
        result = subprocess.run(cmd, check=False, cwd=str(ROOT_DIR))  # 在项目根执行导出脚本
    except FileNotFoundError as exc:  # 捕获解释器或脚本不可用的情况
        print_error(f"无法调用 Python 解释器导出音频: {exc}")  # 提示错误
        return False  # 导出失败

    if result.returncode != 0:  # 如果脚本返回非零
        print_error("音频导出失败，请确认已安装 ffmpeg 并可在命令行中使用。")  # 告知可能原因
        return False  # 视为导出失败

    print_success(f"已导出干净音频: {output.name}")  # 成功提示
    return True  # 返回成功状态


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
            audio_output = outdir / f"{chapter.stem}.clean.wav"  # 目标音频路径
            if not _render_audio(chapter.audio_file, edl_path, audio_output):  # 调用导出流程
                audio_output = None  # 导出失败时重置

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


def main() -> None:  # CLI 主入口
    _print_banner()  # 展示欢迎信息
    materials_dir = _prompt_materials_directory()  # 询问素材目录

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

    outdir = _ensure_output_directory()  # 询问输出目录

    render_audio = with_audio > 0 and prompt_yes_no("检测到音频文件，是否按 EDL 自动导出干净音频?", default=True)  # 判断是否导出音频

    print_header("批量处理")  # 提示进入批量处理阶段
    summaries: List[ChapterSummary] = []  # 收集每章摘要
    total = len(chapters)  # 章节总数
    for index, chapter in enumerate(chapters, start=1):  # 逐章处理
        print_header(f"[{index}/{total}] {chapter.stem}")  # 显示当前进度
        summary = _process_chapter(
            chapter,  # 当前章节资源
            outdir,  # 输出目录
            score_threshold=DEFAULT_SCORE_THRESHOLD,  # 对齐得分阈值
            render_audio=render_audio,  # 是否导出音频
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
