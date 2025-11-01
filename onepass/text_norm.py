"""最小化的文本规范化与对齐辅助工具。"""
from __future__ import annotations

import json  # 读取字符映射配置
import re  # 处理空白归一
import shutil  # 检测可执行文件是否存在
import subprocess  # 调用外部 opencc
import unicodedata  # 进行 Unicode 归一化
from pathlib import Path  # 使用 Path 处理路径
from typing import Any, Dict

__all__ = [
    "load_char_map",
    "fullwidth_halfwidth_normalize",
    "apply_char_map",
    "normalize_spaces",
    "normalize_pipeline",
    "run_opencc_if_available",
    "scan_suspects",
    "normalize_for_align",
    "cjk_or_latin_seq",
    "build_char_index_map",
]

_ZERO_WIDTH_AND_CONTROL = {
    ord(ch)
    for ch in (
        "\u200b",
        "\u200c",
        "\u200d",
        "\ufeff",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
    )
}
_ZERO_WIDTH_AND_CONTROL.update({code for code in range(0x00, 0x20)})
_ZERO_WIDTH_AND_CONTROL.add(0x7F)

# 需要保留的中英文常见句读符号映射为普通空格，以保留停顿感的“影子”
_PUNCT_TO_SPACE = {
    ord(ch): " "
    for ch in "，。！？；：,.!?;:"
}

# 其他标点统一删除
_OTHER_PUNCT = {
    ord(ch)
    for ch in "`~!@#$%^&*()-_=+[]{}\\|;:'\",<.>/?"
}
_OTHER_PUNCT.update({ord(ch) for ch in "·、—…【】（）〈〉《》「」『』“”’‘`"})

_REMOVE_ZERO_WIDTH = {code: None for code in _ZERO_WIDTH_AND_CONTROL}
_REMOVE_OTHER_PUNCT = {code: None for code in _OTHER_PUNCT}

_ASCII_UPPER = {ord(ch): ch.lower() for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}

# 需要回写的中式标点映射（NFKC 会转为半角，需要恢复）
_ASCII_TO_CJK_PUNCT = {
    ",": "，",
    ".": "。",
    "?": "？",
    "!": "！",
    ":": "：",
    ";": "；",
    "(": "（",
    ")": "）",
    "[": "【",
    "]": "】",
}


def load_char_map(path: Path) -> dict:
    """加载字符映射配置并校验格式。"""

    if not path.exists():  # 若文件不存在直接提示
        raise FileNotFoundError(
            f"未找到字符映射文件: {path}。请确认已同步仓库或指定 --char-map。"
        )

    try:
        content = path.read_text(encoding="utf-8")  # 读取 JSON 文本
    except UnicodeDecodeError as exc:  # 捕获编码错误
        raise ValueError(
            f"读取 {path} 失败: {exc}. 请将文件重新保存为 UTF-8 编码。"
        ) from exc

    try:
        data: Dict[str, Any] = json.loads(content)  # 解析 JSON 字符串
    except json.JSONDecodeError as exc:  # pragma: no cover - 配置文件错误时提示
        raise ValueError(
            f"解析 {path} 失败: {exc}. 请检查 JSON 语法是否正确。"
        ) from exc

    required_keys = {"delete", "map", "normalize_width", "normalize_space", "preserve_cjk_punct"}  # 必需字段集合
    missing = required_keys - set(data)  # 计算缺失字段
    if missing:  # 若缺失任何字段
        raise ValueError(
            f"字符映射缺少字段: {', '.join(sorted(missing))}。请参考 default_char_map.json 模板。"
        )

    delete = data.get("delete")  # 读取 delete 列表
    mapping = data.get("map")  # 读取映射字典
    if not isinstance(delete, list) or not all(isinstance(item, str) for item in delete):  # 校验列表类型
        raise TypeError("delete 字段必须为字符串列表。请确认配置格式。")
    if not isinstance(mapping, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()
    ):  # 校验映射类型
        raise TypeError("map 字段必须为字符串到字符串的映射。")

    for flag in ("normalize_width", "normalize_space", "preserve_cjk_punct"):  # 逐个检查布尔开关
        value = data.get(flag)
        if not isinstance(value, bool):  # 必须为布尔值
            raise TypeError(f"{flag} 字段必须为布尔值 true/false。")

    return {
        "delete": delete,  # 清除字符列表
        "map": mapping,  # 替换映射字典
        "normalize_width": data["normalize_width"],  # 宽度归一开关
        "normalize_space": data["normalize_space"],  # 空白归一开关
        "preserve_cjk_punct": data["preserve_cjk_punct"],  # 保留中式标点开关
    }


def fullwidth_halfwidth_normalize(s: str, preserve_cjk_punct: bool) -> str:
    """执行 NFKC 宽度归一并在需要时恢复中式标点。"""

    normalized = unicodedata.normalize("NFKC", s)  # 使用 NFKC 统一全半角
    if not preserve_cjk_punct:  # 若无需恢复标点则直接返回
        return normalized

    restored: list[str] = []  # 初始化回写后的字符列表
    for ch in normalized:  # 遍历归一化后的每个字符
        if ch in _ASCII_TO_CJK_PUNCT:  # 若命中需恢复的 ASCII 标点
            restored.append(_ASCII_TO_CJK_PUNCT[ch])  # 回写为全角标点
        else:
            restored.append(ch)  # 其他字符保持不变
    return "".join(restored)  # 拼接回写后的字符串


def apply_char_map(s: str, cmap: dict) -> tuple[str, dict]:
    """按映射表删除字符并执行替换，返回结果与统计。"""

    delete_set = set(cmap.get("delete", []))  # 转换为集合便于查找
    mapping = cmap.get("map", {})  # 读取替换映射
    stats = {
        "deleted_count": 0,  # 记录删除次数
        "mapped_count": 0,  # 记录替换次数
        "width_normalized_count": 0,  # 占位统计（由外层填充）
        "space_normalized_count": 0,  # 占位统计（由外层填充）
    }

    result: list[str] = []  # 使用列表累积结果字符
    for ch in s:  # 遍历输入字符
        if ch in delete_set:  # 命中删除列表
            stats["deleted_count"] += 1  # 累加删除次数
            continue  # 跳过该字符
        if ch in mapping:  # 命中映射表
            mapped = mapping[ch]  # 查找替换结果
            if mapped != ch:  # 仅当结果不同才计数
                stats["mapped_count"] += 1
            result.append(mapped)  # 写入替换字符
        else:
            result.append(ch)  # 未命中映射则保持原字符

    return "".join(result), stats  # 返回拼接结果与统计


_SPACE_PATTERN = re.compile(r"[^\S\n]+")


def normalize_spaces(s: str) -> str:
    """归一空白字符为单空格，同时折叠空行。"""

    collapsed = _SPACE_PATTERN.sub(" ", s)  # 将连续空白折叠为单个空格
    lines = collapsed.splitlines()  # 按行处理以保留换行
    normalized_lines: list[str] = []  # 存放处理后的每行
    blank_pending = False  # 标记是否刚刚写入空行
    for line in lines:  # 遍历每一行
        trimmed = line.strip()  # 去除行首尾空白
        if trimmed:  # 非空行直接写入
            normalized_lines.append(trimmed)
            blank_pending = False  # 重置空行标记
        else:  # 空行需要折叠
            if not blank_pending:  # 连续空行只保留一个
                normalized_lines.append("")
                blank_pending = True
    result = "\n".join(normalized_lines).strip("\n")  # 去除首尾多余空行
    return result


def normalize_pipeline(
    s: str,
    cmap: dict,
    use_width: bool,
    use_space: bool,
    preserve_cjk_punct: bool,
) -> tuple[str, dict]:
    """串联执行宽度归一、字符映射与空白归一。"""

    stats = {
        "deleted_count": 0,
        "mapped_count": 0,
        "width_normalized_count": 0,
        "space_normalized_count": 0,
    }

    text = s
    if use_width:  # 根据配置决定是否执行宽度归一
        width_changes = sum(
            1 for ch in text if unicodedata.normalize("NFKC", ch) != ch
        )  # 统计可能受影响的字符数量
        text = fullwidth_halfwidth_normalize(text, preserve_cjk_punct)  # 执行归一化
        stats["width_normalized_count"] = width_changes  # 记录统计

    text, cmap_stats = apply_char_map(text, cmap)  # 应用字符映射规则
    for key in ("deleted_count", "mapped_count"):
        stats[key] += cmap_stats.get(key, 0)  # 合并映射阶段统计

    if use_space:  # 根据配置决定是否归一空白
        before = text  # 保存处理前文本用于统计
        text = normalize_spaces(text)  # 执行空白归一
        space_changes = 0  # 统计空白调整次数
        for match in _SPACE_PATTERN.finditer(before):  # 统计多空白折叠
            space_changes += max(0, len(match.group()) - 1)
        for line in before.splitlines():  # 统计行首尾空白
            space_changes += len(line) - len(line.lstrip())
            space_changes += len(line) - len(line.rstrip())
        blank_run = 0  # 统计连续空行被折叠次数
        for line in before.splitlines():
            if not line.strip():
                blank_run += 1
                if blank_run > 1:
                    space_changes += 1
            else:
                blank_run = 0
        stats["space_normalized_count"] = space_changes  # 写入空白统计

    return text, stats


def run_opencc_if_available(s: str, mode: str) -> tuple[str, bool]:
    """按需调用 opencc，实现繁简转换。"""

    if mode not in {"none", "t2s", "s2t"}:  # 校验模式合法性
        raise ValueError("opencc 模式仅支持 none/t2s/s2t，请重新选择。")
    if mode == "none":  # 未开启则直接返回原文
        return s, False

    executable = shutil.which("opencc")  # 检查 opencc 是否可用
    if not executable:  # 未安装时直接返回
        return s, False

    cmd = [executable, "-c", mode]  # 构建命令行参数
    try:
        completed = subprocess.run(
            cmd,  # 调用 opencc
            input=s.encode("utf-8"),  # 通过 stdin 传入文本
            capture_output=True,  # 捕获 stdout/stderr
            check=False,  # 允许非零退出码自行处理
        )
    except OSError:  # 调用失败时回退
        return s, False

    if completed.returncode != 0:  # opencc 执行失败
        return s, False

    try:
        converted = completed.stdout.decode("utf-8")  # 解码转换后的文本
    except UnicodeDecodeError:  # 输出非 UTF-8 时回退
        return s, False

    return converted, True  # 返回转换结果与标记


def scan_suspects(s: str, max_examples: int = 8) -> dict:
    """扫描文本中可能导致对齐问题的可疑字符。"""

    control_examples: list[str] = []  # 收集控制字符示例
    rare_examples: list[str] = []  # 收集符号示例
    mixed_examples: list[str] = []  # 收集混合脚本示例
    control_count = 0  # 统计控制字符数量
    rare_count = 0  # 统计罕见符号数量
    mixed_count = 0  # 统计混合脚本数量

    for ch in s:  # 遍历所有字符
        if ch in {"\n", "\t", " "}:  # 忽略常见空白
            continue
        category = unicodedata.category(ch)  # 获取 Unicode 类别
        if category in {"Cc", "Cf"}:  # 控制字符或格式控制
            control_count += 1
            if len(control_examples) < max_examples:  # 收集有限数量示例
                control_examples.append(f"U+{ord(ch):04X}")
            continue
        name = unicodedata.name(ch, "")  # 获取字符名称
        if "PRIVATE USE" in name or category.startswith("S"):  # 罕见符号或私有区
            rare_count += 1
            if len(rare_examples) < max_examples:
                rare_examples.append(ch)

    tokens = re.split(r"\s+", s)  # 按空白切分文本
    for token in tokens:
        if not token:  # 跳过空串
            continue
        has_cjk = any("CJK" in unicodedata.name(ch, "") for ch in token)  # 检测是否包含 CJK 字符
        has_latin = any("LATIN" in unicodedata.name(ch, "") for ch in token)  # 检测是否包含拉丁字符
        if has_cjk and has_latin:  # 同一词内混合脚本
            mixed_count += 1
            if len(mixed_examples) < max_examples:
                mixed_examples.append(token[:12] + ("…" if len(token) > 12 else ""))

    return {
        "control_chars": {"count": control_count, "examples": control_examples},
        "rare_symbols": {"count": rare_count, "examples": rare_examples},
        "mixed_scripts": {"count": mixed_count, "examples": mixed_examples},
    }


def normalize_for_align(text: str) -> str:
    """规范化文本以便做粗对齐。"""

    # 去掉零宽字符与控制字符，避免隐形噪声干扰匹配
    text = text.translate(_REMOVE_ZERO_WIDTH)
    # 使用 NFKC 统一全半角形态
    text = unicodedata.normalize("NFKC", text)
    # 仅对 ASCII 字母做小写化处理，避免影响 CJK 字符
    text = text.translate(_ASCII_UPPER)
    # 常见句读符转为空格，保留停顿的影子效果
    text = text.translate(_PUNCT_TO_SPACE)
    # 其他标点全部删除
    text = text.translate(_REMOVE_OTHER_PUNCT)
    # 合并多余空白为单个空格
    text = " ".join(text.split())
    # 返回去除首尾空白的结果
    return text.strip()


def _remove_spaces(text: str) -> str:
    """去除文本中的所有空白字符。"""

    return "".join(ch for ch in text if not ch.isspace())


def cjk_or_latin_seq(words: list[str]) -> str:
    """将词序列拼接为对齐用字符串。"""

    joined: list[str] = []
    for word in words:
        # 每个词去掉空白后拼接，中文自然按字符粒度保留
        joined.append(_remove_spaces(word))
    # 直接拼接得到用于匹配的字符串
    return "".join(joined)


def build_char_index_map(word_texts: list[str]) -> list[tuple[int, int]]:
    """构建词到字符的索引映射。"""

    mapping: list[tuple[int, int]] = []
    cursor = 0
    for text in word_texts:
        # 去掉空白后得到用于匹配的字符序列
        cleaned = _remove_spaces(text)
        # 当前词在拼接字符串中的起始位置
        start = cursor
        # 光标向后移动该词的字符长度
        cursor += len(cleaned)
        # 记录该词的字符区间 [start, cursor)
        mapping.append((start, cursor))
    return mapping
