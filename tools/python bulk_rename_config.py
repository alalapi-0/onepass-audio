#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bulk_rename_config.py

批量重命名（仅在“参数区”填写的规则才会运行）：
- 删除固定字段 / 批量替换 / 正则替换
- 增加前缀/后缀（含自动编号、父目录名、日期、扩展保留等）
- 大小写转换（lower/upper/title/capwords）
- 空格与分隔符规范化（空格→下划线/短横，连字符去重，左右裁剪）
- 统一半角（NFKC），可选将中文/假名转拉丁（需安装 unidecode）
- 日期戳（文件修改时间），可选 EXIF 日期（需安装 Pillow）
- 仅限某些扩展名、排除某些扩展名、只处理含指定子串的文件
- 递归子目录，重名冲突自动递增 (1)(2)...
- 预演（dry run）与映射 CSV 导出

注意：
1) 默认 DRY_RUN=True，仅打印预演结果，不真正改名。要执行请在参数区改为 False。
2) 只有你在参数区填写了值/开启了布尔开关的规则才会应用；其余全部跳过。
3) 规则按 RULE_ORDER 指定的顺序执行（可在参数区自定义）。
"""

from __future__ import annotations
import csv
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# -----------------------
# 可选依赖（按需自动探测）
# -----------------------
try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

try:
    from unidecode import unidecode
    UNIDECODE_AVAILABLE = True
except Exception:
    UNIDECODE_AVAILABLE = False


# =========================
#        核心逻辑
# =========================

@dataclass
class FileInfo:
    path: Path
    parent: str
    stem: str
    ext: str          # 含点，如 ".txt"
    mtime: datetime


def list_files(root: Path, include_subdirs: bool) -> List[Path]:
    if include_subdirs:
        return [p for p in root.rglob("*") if p.is_file()]
    else:
        return [p for p in root.iterdir() if p.is_file()]


def to_fileinfo(p: Path, keep_full_suffix: bool) -> FileInfo:
    stat = p.stat()
    if keep_full_suffix and len(p.suffixes) >= 2:
        # 保留多后缀，如 .tar.gz
        ext = "".join(p.suffixes)
        stem = p.name[: -len(ext)]
    else:
        ext = p.suffix
        stem = p.stem
    return FileInfo(
        path=p,
        parent=p.parent.name,
        stem=stem,
        ext=ext,
        mtime=datetime.fromtimestamp(stat.st_mtime),
    )


# ---------- 可选规则实现（仅启用被填写的） ----------

def rule_delete_substrings(stem: str, values: List[str]) -> str:
    for v in values:
        if v:
            stem = stem.replace(v, "")
    return stem

def rule_replace_map(stem: str, mapping: Dict[str, str]) -> str:
    for k, v in mapping.items():
        if k is not None:
            stem = stem.replace(k, v if v is not None else "")
    return stem

def rule_regex_replace(stem: str, patterns: List[Tuple[str, str, bool]]) -> str:
    # patterns: [(pattern, repl, ignore_case)]
    for pat, repl, ic in patterns:
        if pat:
            flags = re.IGNORECASE if ic else 0
            stem = re.sub(pat, repl or "", stem, flags=flags)
    return stem

def rule_strip(stem: str, left: Optional[str], right: Optional[str], strip_spaces: bool) -> str:
    if strip_spaces:
        stem = stem.strip()
    if left:
        while stem.startswith(left):
            stem = stem[len(left):]
    if right:
        while stem.endswith(right):
            stem = stem[:-len(right)]
    return stem

def rule_case(stem: str, mode: Optional[str]) -> str:
    if not mode:
        return stem
    m = mode.lower()
    if m == "lower":
        return stem.lower()
    if m == "upper":
        return stem.upper()
    if m == "title":
        return stem.title()
    if m == "capwords":
        return " ".join(s.capitalize() for s in stem.split())
    return stem

def rule_spaces_and_separators(stem: str, space_to: Optional[str], dedupe: bool, trim_sep: Optional[str]) -> str:
    if space_to in {"_", "-"}:
        stem = re.sub(r"\s+", space_to, stem)
    elif space_to == "remove":
        stem = re.sub(r"\s+", "", stem)
    if dedupe:
        # 连续的 _ 或 - 合并
        stem = re.sub(r"_+", "_", stem)
        stem = re.sub(r"-+", "-", stem)
    if trim_sep in {"_", "-"}:
        stem = stem.strip(trim_sep)
    return stem

def rule_nfkc(stem: str, enable: bool) -> str:
    return unicodedata.normalize("NFKC", stem) if enable else stem

def rule_unidecode(stem: str, enable: bool) -> str:
    if enable and UNIDECODE_AVAILABLE:
        return unidecode(stem)
    return stem

def rule_prefix_suffix(stem: str, prefix: Optional[str], suffix: Optional[str]) -> str:
    if prefix:
        stem = f"{prefix}{stem}"
    if suffix:
        stem = f"{stem}{suffix}"
    return stem

def format_dt(dt: datetime, fmt: str) -> str:
    return dt.strftime(fmt)

def get_exif_datetime(path: Path) -> Optional[datetime]:
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            data = {TAGS.get(k, k): v for k, v in exif.items()}
            dt_str = data.get("DateTimeOriginal") or data.get("DateTime")
            if not dt_str:
                return None
            # 常见格式 "YYYY:MM:DD HH:MM:SS"
            dt_str = dt_str.replace(":", "-", 2)
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def rule_date_stamp(stem: str, info: FileInfo, date_fmt: Optional[str],
                    position: str, use_exif: bool) -> str:
    if not date_fmt:
        return stem
    dt = None
    if use_exif:
        dt = get_exif_datetime(info.path)
    if not dt:
        dt = info.mtime
    tag = format_dt(dt, date_fmt)
    if position == "prefix":
        return f"{tag}_{stem}"
    elif position == "suffix":
        return f"{stem}_{tag}"
    return stem

def rule_parent_prefix(stem: str, parent: Optional[str], enable: bool) -> str:
    if enable and parent:
        return f"{parent}_{stem}"
    return stem

def rule_numbering(stem: str, num: Optional[int], fmt: str, position: str) -> str:
    if num is None:
        return stem
    tag = fmt.format(num)
    return f"{tag}_{stem}" if position == "prefix" else f"{stem}_{tag}"

def rule_extension_change(ext: str, new_ext: Optional[str], to_lower: bool) -> str:
    e = new_ext if new_ext else ext
    if to_lower:
        e = e.lower()
    if e and not e.startswith("."):
        e = "." + e
    return e

def compress_separators(stem: str) -> str:
    # 清理多余的分隔符与空格
    stem = re.sub(r"[ \t]+", " ", stem)
    stem = re.sub(r"_+", "_", stem)
    stem = re.sub(r"-+", "-", stem)
    stem = stem.strip(" -_")
    return stem

# ---------- 碰撞处理 ----------

def resolve_collision(dst: Path, policy: str) -> Path:
    """
    policy: "skip" | "overwrite" | "auto_increment"
    """
    if not dst.exists() or policy == "overwrite":
        return dst
    if policy == "skip":
        return dst  # 上层看到已存在可选择跳过
    # auto_increment
    stem = dst.stem
    ext = dst.suffix
    i = 1
    while True:
        cand = dst.with_name(f"{stem}({i}){ext}")
        if not cand.exists():
            return cand
        i += 1


# =========================
#        主流程
# =========================

def main(
    ROOT_DIR: str,
    INCLUDE_SUBDIRS: bool,
    ONLY_EXTS: List[str],
    EXCLUDE_EXTS: List[str],
    ONLY_IF_NAME_CONTAINS: Optional[str],
    KEEP_FULL_SUFFIX: bool,

    # 规则顺序
    RULE_ORDER: List[str],

    # 具体规则（仅填写的会生效）
    DELETE_SUBSTRINGS: List[str],
    REPLACE_MAP: Dict[str, str],
    REGEX_REPLACE: List[Tuple[str, str, bool]],

    STRIP_LEFT: Optional[str],
    STRIP_RIGHT: Optional[str],
    STRIP_SPACES: bool,

    CASE_MODE: Optional[str],  # lower/upper/title/capwords

    SPACE_TO: Optional[str],   # "_" / "-" / "remove" / None
    DEDUPE_SEP: bool,
    TRIM_SEP: Optional[str],   # "_" / "-" / None

    USE_NFKC: bool,
    USE_UNIDECODE: bool,

    ADD_PREFIX: Optional[str],
    ADD_SUFFIX: Optional[str],

    DATE_FMT: Optional[str],   # 例如 "%Y%m%d" 或 "%Y-%m-%d"
    DATE_POSITION: str,        # "prefix"/"suffix"
    USE_EXIF_DATE: bool,

    PARENT_AS_PREFIX: bool,

    ENABLE_NUMBERING: bool,
    NUMBER_START: int,
    NUMBER_STEP: int,
    NUMBER_FMT: str,           # 例如 "{:03d}"
    NUMBER_POSITION: str,      # "prefix"/"suffix"
    NUMBER_GROUP_BY_DIR: bool,

    NEW_EXTENSION: Optional[str],
    EXT_TO_LOWER: bool,

    DRY_RUN: bool,
    COLLISION_POLICY: str,     # "skip" | "overwrite" | "auto_increment"
    EXPORT_CSV: Optional[str],
):
    root = Path(ROOT_DIR).resolve()
    if not root.is_dir():
        print(f"[错误] 目录不存在：{root}", file=sys.stderr)
        sys.exit(1)

    only_exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in ONLY_EXTS}
    exclude_exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in EXCLUDE_EXTS}

    # 收集文件
    candidates = []
    for p in list_files(root, INCLUDE_SUBDIRS):
        finfo = to_fileinfo(p, KEEP_FULL_SUFFIX)
        ext_low = finfo.ext.lower()
        if only_exts and ext_low not in only_exts:
            continue
        if ext_low in exclude_exts:
            continue
        if ONLY_IF_NAME_CONTAINS and ONLY_IF_NAME_CONTAINS not in finfo.stem:
            continue
        candidates.append(finfo)

    # 准备编号（如启用）
    numbering_map: Dict[Path, int] = {}
    if ENABLE_NUMBERING:
        if NUMBER_GROUP_BY_DIR:
            # 目录内分别计数
            groups: Dict[Path, List[FileInfo]] = {}
            for f in candidates:
                groups.setdefault(f.path.parent, []).append(f)
            for _dir, items in groups.items():
                items.sort(key=lambda x: x.path.name)
                n = NUMBER_START
                for f in items:
                    numbering_map[f.path] = n
                    n += NUMBER_STEP
        else:
            # 全局计数
            candidates.sort(key=lambda x: x.path.name)
            n = NUMBER_START
            for f in candidates:
                numbering_map[f.path] = n
                n += NUMBER_STEP

    rows = []  # 导出 CSV
    changed = 0

    # 执行
    for info in candidates:
        stem = info.stem
        ext = info.ext

        # 动态执行规则（只有被填写/开启的才生效）
        for rule in RULE_ORDER:
            if rule == "delete_substrings" and DELETE_SUBSTRINGS:
                stem = rule_delete_substrings(stem, DELETE_SUBSTRINGS)

            elif rule == "replace_map" and REPLACE_MAP:
                stem = rule_replace_map(stem, REPLACE_MAP)

            elif rule == "regex_replace" and REGEX_REPLACE:
                stem = rule_regex_replace(stem, REGEX_REPLACE)

            elif rule == "strip" and (STRIP_LEFT or STRIP_RIGHT or STRIP_SPACES):
                stem = rule_strip(stem, STRIP_LEFT, STRIP_RIGHT, STRIP_SPACES)

            elif rule == "case" and CASE_MODE:
                stem = rule_case(stem, CASE_MODE)

            elif rule == "spaces" and (SPACE_TO or DEDUPE_SEP or TRIM_SEP):
                stem = rule_spaces_and_separators(stem, SPACE_TO, DEDUPE_SEP, TRIM_SEP)

            elif rule == "nfkc" and USE_NFKC:
                stem = rule_nfkc(stem, True)

            elif rule == "unidecode" and USE_UNIDECODE:
                stem = rule_unidecode(stem, True)

            elif rule == "parent_prefix" and PARENT_AS_PREFIX:
                stem = rule_parent_prefix(stem, info.parent, True)

            elif rule == "date" and DATE_FMT:
                stem = rule_date_stamp(stem, info, DATE_FMT, DATE_POSITION, USE_EXIF_DATE)

            elif rule == "prefix_suffix" and (ADD_PREFIX or ADD_SUFFIX):
                stem = rule_prefix_suffix(stem, ADD_PREFIX, ADD_SUFFIX)

            elif rule == "numbering" and ENABLE_NUMBERING:
                num = numbering_map.get(info.path)
                stem = rule_numbering(stem, num, NUMBER_FMT, NUMBER_POSITION)

            elif rule == "compress" :
                stem = compress_separators(stem)

            elif rule == "extension":
                # 延后统一处理
                pass

        # 扩展处理
        new_ext = rule_extension_change(ext, NEW_EXTENSION, EXT_TO_LOWER)
        new_name = f"{stem}{new_ext}"

        # 不变则跳过
        if new_name == info.path.name:
            continue

        dst = info.path.with_name(new_name)
        dst_final = resolve_collision(dst, COLLISION_POLICY)

        # 如果 policy=skip 且目标已存在，就跳过
        if COLLISION_POLICY == "skip" and dst.exists():
            print(f"[跳过-已存在] {info.path.name}  ->  {dst.name}")
            continue

        print(f"{'[预演]' if DRY_RUN else '[重命名]'} {info.path.name}  ->  {dst_final.name}")
        rows.append([str(info.path), str(dst_final)])
        changed += 1

        if not DRY_RUN:
            try:
                info.path.rename(dst_final)
            except Exception as e:
                print(f"[失败] {info.path.name} -> {dst_final.name} : {e}", file=sys.stderr)

    print(f"\n完成：拟重命名 {changed} 个文件。{'（预演，未实际修改）' if DRY_RUN else ''}")

    if EXPORT_CSV and rows:
        outp = Path(EXPORT_CSV).resolve()
        outp.parent.mkdir(parents=True, exist_ok=True)
        with outp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["old_path", "new_path"])
            writer.writerows(rows)
        print(f"[已导出映射] {outp}")


# =========================
#        参数区（可编辑）
# =========================
if __name__ == "__main__":
    # —— 基本范围 ——
    ROOT_DIR = r"E:\nexus\word_json"    # 目标文件夹
    INCLUDE_SUBDIRS = True               # 是否递归子目录
    ONLY_EXTS = []                       # 仅处理这些扩展名（如[".jpg",".png",".txt"]），空列表=不限
    EXCLUDE_EXTS = []                    # 排除这些扩展名
    ONLY_IF_NAME_CONTAINS = ".words"         # 仅当文件名包含此子串时才处理，如 "副本"
    KEEP_FULL_SUFFIX = False              # True: 保持多后缀（.tar.gz），False: 只保留最后一个

    # —— 规则执行顺序（可调整顺序或删改）——
    RULE_ORDER = [
        "nfkc",            # 半角/全角标准化（先做，便于后续匹配）
        "delete_substrings",
        "replace_map",
        "regex_replace",
        "strip",
        "spaces",
        "case",
        "unidecode",
        "parent_prefix",
        "date",
        "prefix_suffix",
        "numbering",
        "compress",        # 最后再压缩分隔符
        "extension",       # 扩展名在最后统一处理
    ]

    # —— 具体规则：仅填写/开启的会生效 ——
    # 1) 删除固定字段（示例：["副本", "(1)", " - 快照"]）
    DELETE_SUBSTRINGS: List[str] = [".words"]  # ← 填写生效

    # 2) 批量替换（示例：{" ": "_", "（": "(", "）": ")"}）
    REPLACE_MAP: Dict[str, str] = {}   # ← 填写生效

    # 3) 正则替换：列表项为 (pattern, repl, ignore_case)
    #    示例：[(r"\s+\-\s*副本$", "", True), (r"版本(\d+)", r"v\1", True)]
    REGEX_REPLACE: List[Tuple[str, str, bool]] = []  # ← 填写生效

    # 4) 去头去尾与空白裁剪
    STRIP_LEFT: Optional[str] = None   # 例："_"  会循环去除开头所有 "_"
    STRIP_RIGHT: Optional[str] = None  # 例:"-"   会循环去除结尾所有 "-"
    STRIP_SPACES: bool = False         # 去除首尾空白

    # 5) 大小写模式：None/lower/upper/title/capwords
    CASE_MODE: Optional[str] = None

    # 6) 空格/分隔符处理
    SPACE_TO: Optional[str] = None     # "_"/"-"/"remove"/None
    DEDUPE_SEP: bool = False           # 多个 _ 或 - 压缩成一个
    TRIM_SEP: Optional[str] = None     # 去除首尾 "_" 或 "-"

    # 7) 标准化与拉丁化
    USE_NFKC: bool = False             # 全角→半角、符号标准化
    USE_UNIDECODE: bool = False        # 需要 pip install unidecode

    # 8) 前后缀（在日期/父目录等之后再叠加）
    ADD_PREFIX: Optional[str] = None   # 例："IMG_"
    ADD_SUFFIX: Optional[str] = None   # 例："_bak"

    # 9) 日期标签（文件mtime或EXIF）
    DATE_FMT: Optional[str] = None     # 例:"%Y%m%d" 或 "%Y-%m-%d_%H%M%S"
    DATE_POSITION: str = "prefix"      # "prefix"/"suffix"
    USE_EXIF_DATE: bool = False        # 需要 Pillow；失败则退回 mtime

    # 10) 父目录名前缀
    PARENT_AS_PREFIX: bool = False     # True 在前面加 "<父目录名>_"

    # 11) 自动编号
    ENABLE_NUMBERING: bool = False
    NUMBER_START: int = 1
    NUMBER_STEP: int = 1
    NUMBER_FMT: str = "{:03d}"         # 位数控制
    NUMBER_POSITION: str = "prefix"    # "prefix"/"suffix"
    NUMBER_GROUP_BY_DIR: bool = False  # True: 每个目录单独计数

    # 12) 扩展名处理
    NEW_EXTENSION: Optional[str] = None  # None=不改；如 "txt" 或 ".txt" 改成固定扩展
    EXT_TO_LOWER: bool = True            # 统一小写扩展

    # —— 执行控制 ——
    DRY_RUN: bool = False                 # 预演；要真正改名改为 False
    COLLISION_POLICY: str = "auto_increment"  # "skip"|"overwrite"|"auto_increment"
    EXPORT_CSV: Optional[str] = r""      # 映射表路径；空字符串=不导出

    # 建议示例（按需取消注释）：
    # DELETE_SUBSTRINGS = ["副本"]
    # REPLACE_MAP = {"（": "(", "）": ")", " ": "_"}
    # REGEX_REPLACE = [(r"\(\d+\)$", "", True)]
    # CASE_MODE = "lower"
    # SPACE_TO = "_"; DEDUPE_SEP = True; TRIM_SEP = "_"
    # USE_NFKC = True
    # DATE_FMT = "%Y%m%d"; DATE_POSITION = "prefix"
    # PARENT_AS_PREFIX = True
    # ENABLE_NUMBERING = True; NUMBER_FMT = "{:02d}"
    # NEW_EXTENSION = ".txt"
    # DRY_RUN = False
    # EXPORT_CSV = r"E:\test\rename_map.csv"

    EXPORT_CSV = EXPORT_CSV.strip() or None

    main(
        ROOT_DIR=ROOT_DIR,
        INCLUDE_SUBDIRS=INCLUDE_SUBDIRS,
        ONLY_EXTS=ONLY_EXTS,
        EXCLUDE_EXTS=EXCLUDE_EXTS,
        ONLY_IF_NAME_CONTAINS=ONLY_IF_NAME_CONTAINS,
        KEEP_FULL_SUFFIX=KEEP_FULL_SUFFIX,
        RULE_ORDER=RULE_ORDER,
        DELETE_SUBSTRINGS=DELETE_SUBSTRINGS,
        REPLACE_MAP=REPLACE_MAP,
        REGEX_REPLACE=REGEX_REPLACE,
        STRIP_LEFT=STRIP_LEFT,
        STRIP_RIGHT=STRIP_RIGHT,
        STRIP_SPACES=STRIP_SPACES,
        CASE_MODE=CASE_MODE,
        SPACE_TO=SPACE_TO,
        DEDUPE_SEP=DEDUPE_SEP,
        TRIM_SEP=TRIM_SEP,
        USE_NFKC=USE_NFKC,
        USE_UNIDECODE=USE_UNIDECODE,
        ADD_PREFIX=ADD_PREFIX,
        ADD_SUFFIX=ADD_SUFFIX,
        DATE_FMT=DATE_FMT,
        DATE_POSITION=DATE_POSITION,
        USE_EXIF_DATE=USE_EXIF_DATE,
        PARENT_AS_PREFIX=PARENT_AS_PREFIX,
        ENABLE_NUMBERING=ENABLE_NUMBERING,
        NUMBER_START=NUMBER_START,
        NUMBER_STEP=NUMBER_STEP,
        NUMBER_FMT=NUMBER_FMT,
        NUMBER_POSITION=NUMBER_POSITION,
        NUMBER_GROUP_BY_DIR=NUMBER_GROUP_BY_DIR,
        NEW_EXTENSION=NEW_EXTENSION,
        EXT_TO_LOWER=EXT_TO_LOWER,
        DRY_RUN=DRY_RUN,
        COLLISION_POLICY=COLLISION_POLICY,
        EXPORT_CSV=EXPORT_CSV,
    )
