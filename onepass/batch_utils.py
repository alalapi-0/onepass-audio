"""批处理与文件配对的通用工具函数。"""
from __future__ import annotations

import json  # 写入 JSON 报告
from pathlib import Path  # 统一路径处理
from typing import Dict


def iter_files(root: Path, patterns: list[str]) -> list[Path]:
    """递归匹配多个 glob 模式，返回去重且稳定排序的文件列表。"""

    root = root.expanduser().resolve()  # 展开用户目录并转换为绝对路径
    if not root.exists():  # 若根目录不存在则直接返回空列表
        return []
    seen: Dict[Path, None] = {}  # 使用字典保持插入顺序并去重
    for pattern in patterns:  # 遍历所有模式
        for path in root.rglob(pattern):  # 递归匹配模式
            if path.is_file() and path not in seen:  # 仅保留文件并去重
                seen[path] = None  # 记录文件路径
    return sorted(seen.keys())  # 按路径排序以获得稳定输出


def stem_from_words_json(p: Path) -> str:
    """根据 *.words.json 文件求出基础 stem。"""

    name = p.name  # 获取文件名
    if name.endswith(".words.json"):  # 标准后缀
        return name[: -len(".words.json")]  # 去掉后缀得到 stem
    if name.endswith(".json"):  # 兼容非标准命名
        return name[: -len(".json")]  # 去掉 .json
    return p.stem  # 回退到 pathlib 的 stem 逻辑


def find_text_for_stem(root: Path, stem: str, text_patterns: list[str]) -> Path | None:
    """根据 stem 优先匹配 .norm.txt，再回退到 .txt。"""

    root = root.expanduser().resolve()  # 解析根目录
    norm_name = f"{stem}.norm.txt"  # 期望的规范化文件名
    txt_name = f"{stem}.txt"  # 原始文本文件名
    candidates = iter_files(root, text_patterns)  # 先收集所有候选文件
    for path in candidates:  # 遍历候选文件
        if path.name == norm_name:  # 优先返回规范化文本
            return path
    for path in candidates:  # 再次遍历寻找原始文本
        if path.name == txt_name:  # 匹配原始文本
            return path
    return None  # 找不到匹配项返回 None


def safe_rel(base: Path, target: Path) -> str:
    """生成用于报告的相对路径字符串。"""

    try:
        return str(target.resolve().relative_to(base.resolve()))  # 优先返回相对路径
    except Exception:
        return str(target.resolve())  # 失败时返回绝对路径字符串


def write_json(path: Path, data: dict) -> None:
    """写入 UTF-8 编码且带缩进的 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"  # 生成 JSON 字符串
    path.write_text(payload, encoding="utf-8")  # 写入文件
