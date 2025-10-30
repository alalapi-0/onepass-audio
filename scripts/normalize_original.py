#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 该脚本用于将原始 TXT 文本规范化为适合词级对齐的格式

# 导入 argparse 以解析命令行参数
import argparse
# 导入 csv 以生成规范化统计报告
import csv
# 导入 sys 以访问解释器路径与退出函数
import sys
# 导入 unicodedata 以执行 Unicode NFKC 归一化
import unicodedata
# 导入 re 以使用正则表达式处理空白与句子切分
import re
# 导入 dataclass 装饰器以简化统计数据结构
from dataclasses import dataclass
# 导入 Path 类以处理文件与目录路径
from pathlib import Path
# 导入类型注解以提高可读性
from typing import Dict, List, Tuple

# 定义兼容部首的二次替换映射
RADICAL_MAP: Dict[str, str] = {
    "⻓": "长",
    "⺠": "民",
    "⻅": "见",
    "⻛": "风",
    "⻢": "马",
    "⻙": "韦",
    "⾔": "言",
    "⾕": "谷",
    "⼈": "人",
    "⼤": "大",
}

# 定义标点替换序列，长字符串需放在前方以防部分重复统计
PUNCT_SUBSTITUTIONS: List[Tuple[str, str]] = [
    ("——", "-"),
    ("—", "-"),
    ("–", "-"),
    ("…", "..."),
    ("⋯", "..."),
    ("，", ","),
    ("。", "."),
    ("：", ":"),
    ("；", ";"),
    ("！", "!"),
    ("、", ","),
    ("（", "("),
    ("）", ")"),
    ("“", '"'),
    ("”", '"'),
    ("‘", "'"),
    ("’", "'"),
    ("《", ""),
    ("》", ""),
]

# 定义中文字符范围的正则，以识别中文内部空格
ZH_CHAR_PATTERN = r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]"

# 定义统计数据的数据类
@dataclass  # 定义统计结果的数据结构
class NormalizationStats:
    # 原始文本长度
    length_before: int
    # 规范化后文本长度
    length_after: int
    # 兼容部首替换计数
    radical_fixes: int
    # 标点替换计数
    punct_fixes: int
    # 软换行合并计数
    soft_wraps_merged: int
    # 中文内部空格移除计数
    zh_intra_spaces_removed: int

# 定义规范化核心函数供外部模块复用

def normalize_for_align(text: str) -> str:  # 对外暴露的规范化函数
    # 调用内部实现并仅返回规范化后的文本
    normalized_text, _ = _normalize_text(text)
    # 返回规范化结果
    return normalized_text

# 定义内部函数执行完整规范化并返回统计

def _normalize_text(text: str) -> Tuple[str, NormalizationStats]:  # 内部规范化实现
    # 记录原始长度用于报告
    length_before = len(text)
    # 统一换行符为 \n 以便后续处理
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # 执行 NFKC 归一化
    normalized = unicodedata.normalize("NFKC", normalized)
    # 应用兼容部首映射并统计替换数量
    normalized, radical_count = _apply_char_map(normalized, RADICAL_MAP)
    # 执行标点替换并统计数量
    normalized, punct_count = _apply_substitutions(normalized, PUNCT_SUBSTITUTIONS)
    # 合并软换行并获取合并次数
    normalized, wrap_count = _merge_soft_wraps(normalized)
    # 移除中文内部空格并统计次数
    normalized, zh_space_count = _remove_zh_inner_spaces(normalized)
    # 折叠连续空格并修剪首尾空白
    normalized = _collapse_spaces(normalized)
    # 组装统计数据对象
    stats = NormalizationStats(
        length_before=length_before,
        length_after=len(normalized),
        radical_fixes=radical_count,
        punct_fixes=punct_count,
        soft_wraps_merged=wrap_count,
        zh_intra_spaces_removed=zh_space_count,
    )
    # 返回规范化文本与统计
    return normalized, stats

# 定义辅助函数：按字符映射替换并返回新文本与计数

def _apply_char_map(text: str, mapping: Dict[str, str]) -> Tuple[str, int]:  # 字符映射替换
    # 初始化列表以逐字符拼接结果
    buffer: List[str] = []
    # 初始化计数器
    count = 0
    # 遍历原始文本中的每一个字符
    for char in text:
        # 查找字符是否存在于映射表
        replacement = mapping.get(char)
        # 如果存在映射则替换并累计计数
        if replacement is not None:
            buffer.append(replacement)
            count += 1
        else:
            # 否则保持原样
            buffer.append(char)
    # 将列表合并成字符串
    return "".join(buffer), count

# 定义辅助函数：执行子串替换并累计替换次数

def _apply_substitutions(text: str, substitutions: List[Tuple[str, str]]) -> Tuple[str, int]:  # 子串替换
    # 初始化计数器
    total = 0
    # 逐条替换映射执行替换
    for old, new in substitutions:
        # 统计当前子串在文本中的出现次数
        occurrences = text.count(old)
        # 若存在则执行替换并累计
        if occurrences:
            text = text.replace(old, new)
            total += occurrences
    # 返回替换后的文本与总计数
    return text, total

# 定义辅助函数：合并软换行

def _merge_soft_wraps(text: str) -> Tuple[str, int]:  # 合并软换行
    # 按换行符拆分为行列表
    lines = text.split("\n")
    # 存放合并后的段落列表
    paragraphs: List[str] = []
    # 当前段落缓存
    current_segment: List[str] = []
    # 统计合并次数
    merged = 0
    # 遍历每一行
    for line in lines:
        # 去除首尾空白以避免空格干扰
        stripped = line.strip()
        # 如果该行为空，则视为段落分隔
        if stripped == "":
            if current_segment:
                paragraphs.append(" ".join(current_segment))
                merged += len(current_segment) - 1
                current_segment = []
            paragraphs.append("")
        else:
            # 非空行加入当前段落
            current_segment.append(stripped)
    # 处理末尾残留段落
    if current_segment:
        paragraphs.append(" ".join(current_segment))
        merged += len(current_segment) - 1
    # 构建最终行列表，避免连续空行
    result_lines: List[str] = []
    for entry in paragraphs:
        if entry == "":
            if result_lines and result_lines[-1] == "":
                continue
            result_lines.append("")
        else:
            result_lines.append(entry)
    # 使用换行符重新拼接文本
    return "\n".join(result_lines).strip("\n"), merged

# 定义辅助函数：移除中文内部空格

def _remove_zh_inner_spaces(text: str) -> Tuple[str, int]:  # 移除中文内部空格
    # 构造正则以匹配中文字符之间的空格
    pattern = re.compile(rf"(?<={ZH_CHAR_PATTERN})\s+(?={ZH_CHAR_PATTERN})")
    # 查找所有匹配以统计次数
    matches = list(pattern.finditer(text))
    # 执行替换将匹配到的空白移除
    text = pattern.sub("", text)
    # 返回新文本与匹配数量
    return text, len(matches)

# 定义辅助函数：折叠多余空格并修剪首尾

def _collapse_spaces(text: str) -> str:  # 折叠多余空格
    # 按行处理以保留段落换行
    lines = text.split("\n")
    # 处理后的行列表
    collapsed: List[str] = []
    # 遍历每一行
    for line in lines:
        # 将制表符统一为空格
        line = line.replace("\t", " ")
        # 折叠连续空格为单个空格
        line = re.sub(r" {2,}", " ", line)
        # 去除每行首尾空白
        collapsed.append(line.strip())
    # 重新组合并去除整体首尾空白
    result = "\n".join(collapsed).strip()
    # 返回折叠后的文本
    return result

# 定义辅助函数：基于终止符号切分句子

def _split_sentences(text: str) -> List[str]:  # 简单句子切分
    # 将换行替换为空格以避免切分受限
    unified = text.replace("\n", " ")
    # 使用正则按句号、感叹号、问号与分号进行切分
    parts = re.split(r"(?<=[。.!?;；])\s+", unified)
    # 过滤空白片段并返回
    return [segment.strip() for segment in parts if segment.strip()]

# 定义函数：收集输入文件列表

def _collect_input_files(input_path: Path) -> List[Path]:  # 收集待处理文件
    # 如果输入是目录则遍历其中的 .txt 文件
    if input_path.is_dir():
        return sorted(p for p in input_path.iterdir() if p.suffix.lower() == ".txt" and p.is_file())
    # 如果输入是单个文件则直接返回
    if input_path.is_file() and input_path.suffix.lower() == ".txt":
        return [input_path]
    # 否则抛出错误提示
    raise FileNotFoundError(f"未找到可处理的 TXT 文件: {input_path}")

# 定义函数：根据输入路径与输出基准计算输出路径

def _resolve_output_path(src: Path, out_base: Path) -> Path:  # 推导输出路径
    # 若输出基准是目录则在其中创建同名 .norm.txt
    if out_base.exists() and out_base.is_dir():
        return out_base / f"{src.stem}.norm.txt"
    # 若输出基准以 .txt 结尾则直接使用该路径
    if out_base.suffix.lower() == ".txt":
        return out_base
    # 其他情况视为目录需要创建
    if not out_base.exists():
        return out_base / f"{src.stem}.norm.txt"
    # 若路径存在但不是目录亦不是 TXT 文件则抛出错误
    raise ValueError(f"无法确定输出路径: {out_base}")

# 定义函数：写入规范化结果与句子列表

def _write_outputs(norm_text: str, norm_path: Path) -> None:  # 写入规范化文本与句子
    # 确保输出目录存在
    norm_path.parent.mkdir(parents=True, exist_ok=True)
    # 写入规范化文本
    norm_path.write_text(norm_text, encoding="utf-8")
    # 计算句子拆分结果
    sentences = _split_sentences(norm_text)
    # 构造句子文件路径
    sentences_path = norm_path.with_suffix(".sentences.txt")
    # 写入句子文件，每行一句
    sentences_path.write_text("\n".join(sentences), encoding="utf-8")

# 定义函数：打印控制台摘要

def _print_summary(path: Path, stats: NormalizationStats) -> None:  # 打印摘要
    # 构建摘要字符串
    summary = (
        f"len {stats.length_before}->{stats.length_after}, "
        f"radical {stats.radical_fixes}, punct {stats.punct_fixes}, "
        f"wraps {stats.soft_wraps_merged}, zh-space {stats.zh_intra_spaces_removed}"
    )
    # 输出进度信息
    print(f"[OK] {path.name}: {summary}")

# 定义函数：处理单个文件并返回统计

def _process_file(src: Path, out_path: Path) -> NormalizationStats:  # 处理单个文件
    # 读取原始文本
    original = src.read_text(encoding="utf-8")
    # 执行规范化并获取统计
    normalized_text, stats = _normalize_text(original)
    # 写入结果文件与句子拆分
    _write_outputs(normalized_text, out_path)
    # 打印摘要
    _print_summary(src, stats)
    # 返回统计数据
    return stats

# 定义函数：写入 CSV 报告

def _write_report(report_path: Path, rows: List[Tuple[str, NormalizationStats]]) -> None:  # 写入报告
    # 确保报告目录存在
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # 打开 CSV 文件准备写入
    with report_path.open("w", encoding="utf-8", newline="") as fh:
        # 创建 CSV 写入器
        writer = csv.writer(fh)
        # 写入表头
        writer.writerow([
            "file",
            "length_before",
            "length_after",
            "radical_fixes",
            "punct_fixes",
            "soft_wraps_merged",
            "zh_intra_spaces_removed",
            "notes",
        ])
        # 写入每个文件的统计数据
        for file_name, stats in rows:
            writer.writerow([
                file_name,
                stats.length_before,
                stats.length_after,
                stats.radical_fixes,
                stats.punct_fixes,
                stats.soft_wraps_merged,
                stats.zh_intra_spaces_removed,
                "",
            ])

# 定义函数：解析命令行参数

def _parse_args(argv: List[str]) -> argparse.Namespace:  # 解析命令行参数
    # 创建参数解析器
    parser = argparse.ArgumentParser(description="规范化原始 TXT 以提升词级对齐效果")
    # 添加输入参数
    parser.add_argument("--in", dest="input", required=True, help="输入文件或目录")
    # 添加输出参数
    parser.add_argument("--out", dest="output", required=True, help="输出文件或目录")
    # 添加报告参数
    parser.add_argument("--report", required=True, help="CSV 报告路径")
    # 添加模式参数
    parser.add_argument("--mode", default="align", choices=["align"], help="处理模式")
    # 解析并返回结果
    return parser.parse_args(argv)

# 定义主执行函数

def _run(argv: List[str]) -> int:  # 主执行入口
    # 解析命令行参数
    args = _parse_args(argv)
    # 解析输入与输出路径对象
    input_path = Path(args.input)
    output_base = Path(args.output)
    report_path = Path(args.report)
    # 收集待处理文件
    try:
        sources = _collect_input_files(input_path)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        # 返回错误状态码
        return 1
    # 若未找到文件则提示并退出
    if not sources:
        print("[WARN] 未发现任何 TXT 文件，任务结束。")
        # 返回成功状态码
        return 0
    # 处理结果行列表
    report_rows: List[Tuple[str, NormalizationStats]] = []
    # 遍历每个文件执行规范化
    for src in sources:
        # 解析对应的输出路径
        out_path = _resolve_output_path(src, output_base)
        # 执行规范化并收集统计
        stats = _process_file(src, out_path)
        # 记录报告行
        report_rows.append((src.name, stats))
    # 写入汇总报告
    _write_report(report_path, report_rows)
    # 返回成功状态码
    return 0

# 当脚本作为主程序执行时运行入口
if __name__ == "__main__":
    # 调用主执行函数并以返回值作为退出码
    sys.exit(_run(sys.argv[1:]))
