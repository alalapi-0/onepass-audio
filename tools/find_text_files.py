#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
find_text_files_config.py

功能：
- 在指定目录递归查找包含某段文字（或正则）的所有文本文件
- 支持忽略大小写、按扩展名过滤、上下文行展示、尝试多编码读取
- 可选导出CSV、可选把命中文件复制到新目录（保持相对路径）

使用方法：
- 不需要传命令行参数，直接在文件最底部“参数区”填写你的参数，然后运行：
  python find_text_files_config.py
"""

import csv
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# 默认尝试的文本编码列表（从常见到不常见）
DEFAULT_ENCODINGS = [
    "utf-8", "utf-8-sig",
    "gb18030", "gbk", "gb2312",
    "shift_jis", "cp932", "euc_jp",
    "big5",
    "latin-1"  # 兜底
]

# 常见二进制文件扩展名（在未指定扩展过滤时用于跳过）
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
    ".zip", ".rar", ".7z", ".gz", ".tar", ".xz",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib",
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".wma", ".ogg",
    ".mp4", ".mkv", ".avi", ".mov", ".webm",
    ".ttf", ".otf", ".woff", ".woff2",
}


def should_skip_by_ext(path: Path, allowed_exts: List[str]) -> bool:
    if allowed_exts:
        return path.suffix.lower() not in {e.lower() for e in allowed_exts}
    return path.suffix.lower() in BINARY_EXTS


def file_too_large(path: Path, max_size_mb: float) -> bool:
    try:
        size = path.stat().st_size
    except Exception:
        return True
    return size > max_size_mb * 1024 * 1024


def read_lines_try_encodings(path: Path, encodings: List[str]) -> Iterable[str]:
    """
    逐个编码尝试读取。若均失败，使用 utf-8(errors='ignore') 兜底。
    """
    for enc in encodings:
        try:
            with path.open("r", encoding=enc, errors="strict") as f:
                for line in f:
                    yield line
            return
        except UnicodeDecodeError:
            continue
        except Exception:
            break
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                yield line
    except Exception:
        return


def find_matches_in_file(
    path: Path,
    pattern: str,
    use_regex: bool = False,
    ignore_case: bool = False,
    encodings: List[str] = None,
    context: int = 0,
) -> Tuple[int, List[Tuple[int, str]]]:
    """
    返回 (匹配总次数, [(行号, 展示文本)...])
    展示文本为匹配行与上下文（若 context>0，则包含上下文；否则仅匹配行）。
    """
    encodings = encodings or DEFAULT_ENCODINGS
    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(pattern, flags) if use_regex else None

    total_hits = 0
    displays: List[Tuple[int, str]] = []

    lines = list(read_lines_try_encodings(path, encodings))
    if not lines:
        return 0, []

    for idx, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        hit = False
        if use_regex:
            if regex.search(line):
                hit = True
        else:
            if ignore_case:
                hit = pattern.casefold() in line.casefold()
            else:
                hit = pattern in line

        if hit:
            total_hits += 1
            if context > 0:
                start = max(0, idx - context)
                end = min(len(lines), idx + context + 1)
                block = "".join(lines[start:end])
                displays.append((idx + 1, block.strip()))
            else:
                displays.append((idx + 1, line.strip()))

    return total_hits, displays


def copy_preserve_relpath(src_file: Path, root_dir: Path, dst_root: Path, follow_symlinks: bool):
    rel = src_file.relative_to(root_dir)
    dst_path = dst_root / rel
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, dst_path, follow_symlinks=follow_symlinks)


def run_search(
    root_dir: Path,
    pattern: str,
    ignore_case: bool,
    use_regex: bool,
    extensions: List[str],
    output_csv: Path | None,
    copy_to: Path | None,
    max_size_mb: float,
    extra_encodings: List[str],
    context: int,
    follow_symlinks: bool,
    print_preview_limit: int = 10,
):
    root = Path(root_dir).resolve()
    if not root.is_dir():
        print(f"[错误] 目录不存在：{root}", file=sys.stderr)
        sys.exit(1)

    encodings = list(dict.fromkeys((extra_encodings or []) + DEFAULT_ENCODINGS))

    results = []  # {"file","hits","line_no","preview"}
    files_scanned = 0
    files_matched = 0

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        files_scanned += 1

        if should_skip_by_ext(p, extensions):
            continue
        if file_too_large(p, max_size_mb):
            continue

        try:
            hits, displays = find_matches_in_file(
                p, pattern, use_regex, ignore_case, encodings, context
            )
        except Exception as e:
            print(f"[跳过] 读取失败：{p} ({e})", file=sys.stderr)
            continue

        if hits > 0:
            files_matched += 1
            print(f"\n=== 命中：{p} （{hits} 次）===")
            for line_no, snippet in displays[:print_preview_limit]:
                one_line = snippet.replace("\r", " ").replace("\n", " \\n ")
                print(f"{line_no:>6}: {one_line[:200]}{'...' if len(one_line) > 200 else ''}")

            for line_no, snippet in displays:
                results.append({
                    "file": str(p),
                    "hits": hits,
                    "line_no": line_no,
                    "preview": snippet.replace("\r", " ").replace("\n", "\\n"),
                })

            if copy_to:
                dst_root = Path(copy_to).resolve()
                copy_preserve_relpath(p, root, dst_root, follow_symlinks)

    if output_csv:
        out_path = Path(output_csv).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["file", "hits", "line_no", "preview"])
            writer.writeheader()
            writer.writerows(results)
        print(f"\n[已导出] 结果CSV：{out_path}")

    print(f"\n扫描完成：共处理文件 {files_scanned} 个；命中文件 {files_matched} 个。")


# =========================
#        参数区（可编辑）
# =========================
if __name__ == "__main__":
    # 根目录
    ROOT_DIR = r"E:\nexus\e8e1c84d-4b46-4bd0-857f-f8cb9fa5ff52\20251029-205427"           # 例：r"E:\docs" 或 "/path/to/dir"

    # 匹配内容
    PATTERN = ".words"               # 当 USE_REGEX=False 时为普通文本；True 时为正则表达式
    USE_REGEX = False               # True 启用正则
    IGNORE_CASE = True              # True 忽略大小写

    # 扩展名过滤（为空列表表示不过滤；推荐明确指定以避免扫描二进制）
    EXTENSIONS = [".txt", ".md", ".log", ".py",".json"]  # 例：[] 或 [".txt", ".md"]

    # 结果导出与复制
    OUTPUT_CSV = r""                # 例：r"E:\out\result.csv"；留空表示不导出
    COPY_TO = r"E:\nexus\word_json"                   # 例：r"E:\out\matched"；留空表示不复制

    # 其他
    MAX_SIZE_MB = 50.0              # 跳过大文件阈值
    EXTRA_ENCODINGS = []            # 追加尝试的编码，如 ["utf-16"]
    CONTEXT = 0                     # 匹配行上下文行数
    FOLLOW_SYMLINKS = False         # 复制时是否跟随符号链接
    PRINT_PREVIEW_LIMIT = 10        # 控制台预览前多少条命中片段

    # 路径空字符串转换为 None
    OUTPUT_CSV = Path(OUTPUT_CSV) if OUTPUT_CSV.strip() else None
    COPY_TO = Path(COPY_TO) if COPY_TO.strip() else None

    run_search(
        root_dir=Path(ROOT_DIR),
        pattern=PATTERN,
        ignore_case=IGNORE_CASE,
        use_regex=USE_REGEX,
        extensions=EXTENSIONS,
        output_csv=OUTPUT_CSV,
        copy_to=COPY_TO,
        max_size_mb=MAX_SIZE_MB,
        extra_encodings=EXTRA_ENCODINGS,
        context=CONTEXT,
        follow_symlinks=FOLLOW_SYMLINKS,
        print_preview_limit=PRINT_PREVIEW_LIMIT,
    )
