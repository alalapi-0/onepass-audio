"""最小化的文本规范化与对齐辅助工具。"""
from __future__ import annotations

import json  # 读取字符映射配置
import logging
import re  # 处理空白归一
import shutil  # 检测可执行文件是否存在
import subprocess  # 调用外部 opencc
import unicodedata  # 进行 Unicode 归一化
from pathlib import Path  # 使用 Path 处理路径
from typing import Any, Dict, Mapping, Sequence

try:  # 优先复用脚本目录实现的折行规则，便于单独调试
    from scripts.text_normalize import collapse_lines_preserve_spacing_rules
except ModuleNotFoundError:  # pragma: no cover - fallback when脚本不可用
    _CJK_RANGE = r"\u3400-\u9FFF\U00020000-\U0002FFFF"

    def collapse_lines_preserve_spacing_rules(text: str) -> str:
        """折叠换行并按照中英文空格规则收敛空白。"""

        if not text:
            return ""

        collapsed = text.replace("\t", "")
        collapsed = re.sub(r"\r?\n+", "", collapsed)
        collapsed = re.sub(r"[ \u00A0]+", " ", collapsed)
        collapsed = re.sub(fr"(?<=[{_CJK_RANGE}])\s+(?=[{_CJK_RANGE}])", "", collapsed)
        collapsed = re.sub(fr"(?<=[{_CJK_RANGE}])\s+(?=[A-Za-z0-9])", "", collapsed)
        collapsed = re.sub(fr"(?<=[A-Za-z0-9])\s+(?=[{_CJK_RANGE}])", "", collapsed)
        collapsed = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[A-Za-z0-9])", " ", collapsed)
        return collapsed.strip()

__all__ = [
    "load_alias_map",
    "normalize_text",
    "apply_alias_map",
    "load_char_map",
    "fullwidth_halfwidth_normalize",
    "apply_char_map",
    "normalize_spaces",
    "merge_hard_wraps",
    "normalize_pipeline",
    "run_opencc_if_available",
    "scan_suspects",
    "normalize_for_align",
    "normalize_for_alignment",
    "prepare_alignment_text",
    "cjk_or_latin_seq",
    "build_char_index_map",
    "sentence_lines_from_text",
    "validate_sentence_lines",
    "normalize_chinese_text",
    "collapse_and_resplit",
]

LOGGER = logging.getLogger("onepass.text_norm")

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

_CJK_CHAR_CLASS = "\u4e00-\u9fff"
_CJK_PUNCT_CHARS = "，。！？；：、“”‘’（）《》〈〉『』【】〔〕—…·"
_FULLWIDTH_SPACE = "\u3000"

_CJK_EXTENDED_RANGE = "\u3400-\u9FFF\uf900-\ufaff"
_CJK_OPENER = "（〔［【《〈「『“‘"
_CJK_CLOSER = "）〕］】》〉」』”’"

_RE_CJK_SPACE = re.compile(rf"(?<=[{_CJK_EXTENDED_RANGE}])\s+(?=[{_CJK_EXTENDED_RANGE}])")
_RE_CJK_OPEN_SPACE = re.compile(rf"(?<=[{_CJK_EXTENDED_RANGE}])\s+(?=[{_CJK_OPENER}])")
_RE_CJK_CLOSE_SPACE = re.compile(rf"(?<=[{_CJK_CLOSER}])\s+(?=[{_CJK_EXTENDED_RANGE}])")

_RE_ASCII_ELLIPSIS = re.compile(r"\.{3,}")
_RE_CJK_ELLIPSIS = re.compile(r"…{2,}")
_RE_DASH_VARIANTS = re.compile(r"[‒–—―﹘﹣━─‐‑]{1,}")
_RE_MIDDLE_DOT_RUN = re.compile(r"[·•・]{2,}")
_RE_SENTENCE_DOT = re.compile(r"\.(?=\s*(?:$|[A-Z]))")

_PUNCT_TRANSLATION = str.maketrans(
    {
        "“": "\"",
        "”": "\"",
        "„": "\"",
        "‟": "\"",
        "〝": "\"",
        "〞": "\"",
        "﹁": "\"",
        "﹂": "\"",
        "﹃": "\"",
        "﹄": "\"",
        "『": "\"",
        "』": "\"",
        "「": "\"",
        "」": "\"",
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "‹": "<",
        "›": ">",
        "﹤": "<",
        "﹥": ">",
        "⋯": "…",
        "︰": ":",
        "﹒": ".",
        "﹑": "、",
        "﹔": "；",
        "﹕": ":",
        "﹖": "？",
        "﹗": "！",
        "﹘": "—",
        "﹣": "—",
        "﹔": "；",
        "﹟": "#",
        "﹠": "&",
        "﹡": "*",
        "﹦": "=",
        "﹨": "\\",
        "﹩": "$",
        "•": "·",
        "・": "·",
        "･": "·",
        "‧": "·",
    }
)

_RE_MULTISPACE = re.compile(r"[ \t]+")
_RE_CJK_GAPS = re.compile(rf"(?<=[{_CJK_CHAR_CLASS}])\s+(?=[{_CJK_CHAR_CLASS}])")
_RE_CJK_PUNCT_LEFT = re.compile(rf"\s+(?=[{_CJK_PUNCT_CHARS}])")
_RE_CJK_PUNCT_RIGHT = re.compile(rf"(?<=[{_CJK_PUNCT_CHARS}])\s+")
_RE_LATIN_GAPS_LEFT = re.compile(rf"([{_CJK_CHAR_CLASS}])\s{{2,}}([0-9A-Za-z])")
_RE_LATIN_GAPS_RIGHT = re.compile(rf"([0-9A-Za-z])\s{{2,}}([{_CJK_CHAR_CLASS}])")
_RE_CJK_ASCII_GAP_LEFT = re.compile(rf"(?<=[{_CJK_CHAR_CLASS}])[ \t]+(?=[0-9A-Za-z])")
_RE_CJK_ASCII_GAP_RIGHT = re.compile(rf"(?<=[0-9A-Za-z])[ \t]+(?=[{_CJK_CHAR_CLASS}])")
_RE_NEWLINE_TRIM = re.compile(r"[ \t]*\n[ \t]*")
_RE_PAREN_INNER_LEFT = re.compile(r"（\s+")
_RE_PAREN_INNER_RIGHT = re.compile(r"\s+）")
_RE_PAREN_OUTER_LEFT = re.compile(rf"(?<=[{_CJK_CHAR_CLASS}])\s+（")
_RE_PAREN_OUTER_RIGHT = re.compile(rf"）\s+(?=[{_CJK_CHAR_CLASS}])")
_RE_ELLIPSIS = re.compile(r"。{2,}")
_RE_FULLWIDTH_DOT_ELLIPSIS = re.compile(r"．{2,}")
_RE_ASCII_DOT_ELLIPSIS = re.compile(r"\.{6,}")
_RE_DASH_VARIANTS = re.compile(r"\s*(?:—|-){2,}\s*")
_RE_EM_DASH_SPACES = re.compile(r"\s*——\s*")
_RE_ELLIPSIS_SPACES = re.compile(r"\s*……\s*")

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



def load_alias_map(path: Path | str | None) -> dict[str, list[str]]:
    """加载词别名映射 JSON，失败时返回空映射。"""

    if not path:
        return {}
    candidate = Path(path).expanduser()
    try:
        if not candidate.exists():
            LOGGER.debug("alias map not found: %s", candidate)
            return {}
        data = json.loads(candidate.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as exc:  # pragma: no cover - 容忍配置异常
        LOGGER.warning("加载 alias map 失败: path=%s error=%s", candidate, exc)
        return {}
    mapping: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for key, values in data.items():
            canonical = str(key or "").strip()
            if not canonical:
                continue
            variants: list[str] = []
            if isinstance(values, str):
                values = [values]
            if isinstance(values, Sequence):
                for item in values:
                    text_value = str(item or "").strip()
                    if text_value and text_value != canonical:
                        variants.append(text_value)
            mapping[canonical] = variants
    return mapping


def apply_alias_map(text: str, alias_map: Mapping[str, Sequence[str]] | None) -> str:
    """使用别名映射表将变体替换为主形。"""

    if not alias_map or not text:
        return text
    result = text
    for canonical, variants in alias_map.items():
        canonical_str = str(canonical or "").strip()
        if not canonical_str:
            continue
        for variant in variants:
            variant_str = str(variant or "").strip()
            if not variant_str or variant_str == canonical_str:
                continue
            result = result.replace(variant_str, canonical_str)
    return result


def normalize_text(
    text: str,
    *,
    collapse_lines: bool = True,
    drop_foreign_brackets: bool = False,
    alias_map: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """对原始文本做轻量规范化，兼顾中英文间距与别名归一。"""

    if not text:
        return ""
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    value = value.translate(_REMOVE_ZERO_WIDTH)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace(_FULLWIDTH_SPACE, " ")
    value = value.translate(_PUNCT_TRANSLATION)
    value = _RE_ASCII_ELLIPSIS.sub("…", value)
    value = _RE_CJK_ELLIPSIS.sub("…", value)
    value = _RE_DASH_VARIANTS.sub("—", value)
    value = _RE_MIDDLE_DOT_RUN.sub("·", value)
    if collapse_lines:
        value = collapse_lines_preserve_spacing_rules(value)
    else:
        value = value.replace("	", " ")
        value = _RE_NEWLINE_TRIM.sub("\n", value)
        value = re.sub(r" {2,}", " ", value)
    value = _RE_CJK_GAPS.sub("", value)
    value = _RE_CJK_PUNCT_LEFT.sub("", value)
    value = _RE_CJK_PUNCT_RIGHT.sub("", value)
    value = _RE_CJK_ASCII_GAP_LEFT.sub("", value)
    value = _RE_CJK_ASCII_GAP_RIGHT.sub("", value)
    value = _RE_LATIN_GAPS_LEFT.sub(r"\1 \2", value)
    value = _RE_LATIN_GAPS_RIGHT.sub(r"\1 \2", value)
    if drop_foreign_brackets:
        value = re.sub(r"（[^）]*[A-Za-z][^）]*）", "", value)
    if alias_map:
        value = apply_alias_map(value, alias_map)
    if collapse_lines:
        return value.strip()
    lines = [line.strip() for line in value.split("\n")]
    return "\n".join(lines)


_SENTENCE_ENDINGS = set("。！？!?…」』”’）】》")
_WORD_CHAR_PATTERN = re.compile(r"[\w\u4e00-\u9fff]")


def _clip_example(text: str, limit: int = 40) -> str:
    """截断字符串到指定长度并在需要时追加省略号。"""

    if len(text) <= limit:  # 未超出限制时直接返回
        return text
    return text[: limit - 1] + "…"  # 超出限制时在末尾追加省略号


def merge_hard_wraps(raw: str) -> str:
    """
    将“非句末”的换行合并为单个空格，以减少短碎句导致的误判重复。
    - 句末符集合：'。！？!?…」』”’）】》'
    - 规则：
      1) 若行尾不以以上句末符结束，则与下一行合并，中间放一个空格；
      2) 类似“配不\n配得上”这类明显被硬切的词，强制合并；
      3) 连续空行折叠为一个空行。
    返回合并后的文本。
    """

    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")  # 统一换行符并拆分
    merged_lines: list[str] = []  # 存放处理后的行
    merged_count = 0  # 记录换行被合并的次数
    merged_examples: list[str] = []  # 收集合并前后的示例

    index = 0
    blank_pending = False  # 标记是否已经写入空行以折叠连续空行
    while index < len(lines):  # 遍历所有行
        current = lines[index].strip()  # 去除首尾空白便于判断
        if not current:  # 处理空行逻辑
            if not blank_pending:  # 仅保留一个空行
                merged_lines.append("")
                blank_pending = True
            index += 1
            continue

        blank_pending = False  # 遇到非空行时重置空行标记
        buffer = current  # 初始化当前缓冲行
        index += 1

        while index < len(lines):  # 检查后续行是否需要合并
            next_line_raw = lines[index]
            next_line = next_line_raw.strip()
            if not next_line:  # 遇到空行表示段落结束
                break

            last_char = buffer[-1] if buffer else ""
            first_char = next_line[0] if next_line else ""
            looks_hard_split = bool(
                last_char
                and first_char
                and _WORD_CHAR_PATTERN.fullmatch(last_char)
                and _WORD_CHAR_PATTERN.fullmatch(first_char)
            )  # 判断是否为被硬切的词

            if last_char in _SENTENCE_ENDINGS and not looks_hard_split:
                break  # 句末标点且非强制合并，停止继续合并

            before = f"{buffer}↵{next_line}"  # 记录合并前示例
            buffer = buffer.rstrip() + " " + next_line.lstrip()  # 将换行改为空格
            merged_count += 1  # 合并次数加一
            if len(merged_examples) < 3:  # 仅保留前三个示例
                after = buffer
                merged_examples.append(f"{_clip_example(before)} => {_clip_example(after)}")
            index += 1  # 消耗下一行

        merged_lines.append(buffer)  # 写入处理后的行

        # 跳过触发合并后遇到的空行，避免重复写入
        while index < len(lines) and not lines[index].strip():
            if not blank_pending:
                merged_lines.append("")  # 保留一个空行
                blank_pending = True
            index += 1

        blank_pending = False  # 写入非空行后重置

    result = "\n".join(merged_lines).strip("\n")  # 重新拼接文本并去除多余首尾空行
    merge_hard_wraps.last_stats = {
        "merged_count": merged_count,
        "examples": merged_examples,
    }  # type: ignore[attr-defined]
    return result


def _is_cjk_char(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (
        0x3400 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FFFF
    )


def _drop_ascii_parentheticals(value: str, threshold: float = 0.7) -> str:
    if not value:
        return value
    openers = {"(": ")", "（": "）"}
    closers = {
        ")": "(",
        "）": "（",
    }
    stack: list[tuple[str, int]] = []
    spans: list[tuple[int, int]] = []
    for idx, ch in enumerate(value):
        if ch in openers:
            stack.append((ch, idx))
            continue
        match_open = closers.get(ch)
        if not match_open:
            continue
        for pos in range(len(stack) - 1, -1, -1):
            open_ch, start = stack[pos]
            if open_ch != match_open:
                continue
            content = value[start + 1 : idx]
            stack = stack[:pos]
            payload = "".join(ch for ch in content if not ch.isspace())
            if not payload:
                break
            ascii_count = sum(1 for char in payload if ord(char) < 128)
            ratio = ascii_count / len(payload) if payload else 0.0
            if ratio >= threshold:
                spans.append((start, idx + 1))
            break
    if not spans:
        return value
    spans.sort()
    cleaned: list[str] = []
    cursor = 0
    for start, end in spans:
        if start < cursor:
            continue
        cleaned.append(value[cursor:start])
        cursor = end
    cleaned.append(value[cursor:])
    return "".join(cleaned)


def _strip_mixed_english_tail(line: str) -> str:
    if not line:
        return line
    stripped = line.rstrip()
    if not stripped:
        return stripped
    idx = len(stripped)
    while idx > 0 and ord(stripped[idx - 1]) < 128:
        if stripped[idx - 1] == "\n":
            break
        idx -= 1
    if idx == len(stripped):
        return stripped
    tail = stripped[idx:]
    if not tail or not any(char.isalnum() and ord(char) < 128 for char in tail):
        return stripped
    if any(_is_cjk_char(ch) for ch in tail):
        return stripped
    prev_char: str | None = None
    for ch in reversed(stripped[:idx]):
        if ch.isspace():
            continue
        prev_char = ch
        break
    if not prev_char or not _is_cjk_char(prev_char):
        return stripped
    return stripped[:idx].rstrip()


def _squash_mixed_english_tails(text: str) -> str:
    if not text:
        return text
    lines = text.split("\n")
    adjusted = [_strip_mixed_english_tail(line) for line in lines]
    return "\n".join(adjusted)


def normalize_chinese_text(
    text: str,
    *,
    collapse_lines: bool = True,
    drop_ascii_parens: bool = False,
    squash_mixed_english: bool = False,
) -> str:
    """针对中文文本执行空白与标点修正，支持可选折叠换行。"""

    if not text:
        return ""

    normalized = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u2028", "\n")
        .replace("\u2029", "\n")
        .replace("\t", " ")
    )
    normalized = normalized.replace(_FULLWIDTH_SPACE, " ")
    normalized = normalized.replace("\xa0", " ")
    normalized = _RE_MULTISPACE.sub(" ", normalized)
    normalized = _RE_CJK_GAPS.sub("", normalized)
    normalized = _RE_CJK_PUNCT_LEFT.sub("", normalized)
    normalized = _RE_CJK_PUNCT_RIGHT.sub("", normalized)
    normalized = _RE_PAREN_INNER_LEFT.sub("（", normalized)
    normalized = _RE_PAREN_INNER_RIGHT.sub("）", normalized)
    normalized = _RE_PAREN_OUTER_LEFT.sub("（", normalized)
    normalized = _RE_PAREN_OUTER_RIGHT.sub("）", normalized)
    normalized = _RE_DASH_VARIANTS.sub("——", normalized)
    normalized = _RE_EM_DASH_SPACES.sub("——", normalized)
    normalized = _RE_ELLIPSIS.sub("……", normalized)
    normalized = _RE_FULLWIDTH_DOT_ELLIPSIS.sub("……", normalized)
    normalized = _RE_ASCII_DOT_ELLIPSIS.sub("……", normalized)
    normalized = _RE_ELLIPSIS_SPACES.sub("……", normalized)
    normalized = _RE_LATIN_GAPS_LEFT.sub(r"\1 \2", normalized)
    normalized = _RE_LATIN_GAPS_RIGHT.sub(r"\1 \2", normalized)
    if drop_ascii_parens:
        normalized = _drop_ascii_parentheticals(normalized)
    if squash_mixed_english:
        normalized = _squash_mixed_english_tails(normalized)
    normalized = _RE_MULTISPACE.sub(" ", normalized)

    if collapse_lines:
        normalized_lines = collapse_and_resplit(normalized)
        normalized = "\n".join(normalized_lines)
    else:
        normalized = "\n".join(part.strip() for part in normalized.splitlines())

    normalized = normalized.replace("\t", "")
    normalized = normalized.strip()
    return normalized


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

_SENTENCE_SPLIT_PATTERN = re.compile(r"([。！？!?；;…\.]+[”’」』》）】]?)(?=\s|$)")
_LINE_BREAK_CLEAN_PATTERN = re.compile(r"[\n\t\u3000\xa0]+")
_MULTI_SPACE_PATTERN = re.compile(r" {2,}")
_ALLOWED_BOUNDARY_CHARS = set("。！？!?；;：:…．.」』”’》）】\"")


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


def _strip_cjk_spaces(text: str) -> str:
    """移除 CJK 字符之间以及与括号、引号之间的多余空格。"""

    stripped = _RE_CJK_SPACE.sub("", text)
    stripped = _RE_CJK_OPEN_SPACE.sub("", stripped)
    stripped = _RE_CJK_CLOSE_SPACE.sub("", stripped)
    return stripped


def collapse_and_resplit(text: str) -> list[str]:
    """折叠空白并按句号/省略号重新断句，避免整篇文本合并为一行。"""

    if not text:
        return []

    normalized = text.replace("\r", "")
    normalized = normalized.replace("\t", " ")
    normalized = _LINE_BREAK_CLEAN_PATTERN.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.translate(_PUNCT_TRANSLATION)
    normalized = _RE_ASCII_ELLIPSIS.sub("…", normalized)
    normalized = _RE_CJK_ELLIPSIS.sub("…", normalized)
    normalized = _RE_DASH_VARIANTS.sub("—", normalized)
    normalized = _RE_MIDDLE_DOT_RUN.sub("·", normalized)
    normalized = normalized.strip()
    if not normalized:
        return []

    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = _strip_cjk_spaces(normalized)

    sentences: list[str] = []
    length = len(normalized)
    start = 0
    idx = 0
    right_trail = set(_CJK_CLOSER + '\"\'')
    sentence_endings = set("。！？!?；;:…")

    while idx < length:
        ch = normalized[idx]
        end_index: int | None = None
        if ch == "…":
            run = 1
            while idx + run < length and normalized[idx + run] == "…":
                run += 1
            end_index = idx + run
        elif ch in sentence_endings:
            end_index = idx + 1
        elif ch == ".":
            match = _RE_SENTENCE_DOT.match(normalized, idx)
            if match:
                end_index = match.end()

        if end_index is None:
            idx += 1
            continue

        while end_index < length and normalized[end_index] in right_trail:
            end_index += 1

        chunk = normalized[start:end_index].strip()
        if chunk:
            cleaned = _strip_cjk_spaces(chunk)
            sentences.append(cleaned)

        start = end_index
        while start < length and normalized[start].isspace():
            start += 1
        idx = max(start, end_index)

    if start < length:
        tail = normalized[start:].strip()
        if tail:
            sentences.append(_strip_cjk_spaces(tail))

    return sentences


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


def _split_sentences(text: str) -> list[str]:
    """按照句末标点分句，包含尾随右引号与括号。"""

    sentences: list[str] = []
    last = 0
    for match in _SENTENCE_SPLIT_PATTERN.finditer(text):
        end = match.end()
        chunk = text[last:end].strip()
        if chunk:
            sentences.append(chunk)
        last = end
    tail = text[last:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def sentence_lines_from_text(text: str, collapse_lines: bool = True) -> list[str]:
    """根据需求生成按句分行的文本。"""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return []
    if collapse_lines:
        sentences = collapse_and_resplit(normalized)
        return sentences
    return [line.rstrip("\r") for line in normalized.split("\n")]


def validate_sentence_lines(lines: Sequence[str]) -> None:
    """确保句子行满足无制表符、无首尾空格等约束。"""

    for line in lines:
        if "\t" in line:
            raise ValueError("检测到制表符，请检查规范化结果。")
        if line != line.strip():
            raise ValueError("句子行存在首尾空格，请检查规范化结果。")
    joined = "\n".join(lines)
    for match in re.finditer(r"(\S)\n(?=\S)", joined):
        if match.group(1) not in _ALLOWED_BOUNDARY_CHARS:
            raise ValueError("检测到疑似行内断句，请确认分句逻辑是否正确。")


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


_ALIGNMENT_CJK_RANGE = _CJK_EXTENDED_RANGE
_ALIGNMENT_ASCII_RANGE = "A-Za-z0-9"
_RE_ALIGN_CJK_STRICT_SPACE = re.compile(
    rf"(?<=[{_ALIGNMENT_CJK_RANGE}])\s+(?=[{_ALIGNMENT_CJK_RANGE}])"
)
_RE_ALIGN_CJK_ASCII_SPACE = re.compile(
    rf"(?<=[{_ALIGNMENT_CJK_RANGE}])\s+(?=[{_ALIGNMENT_ASCII_RANGE}])"
)
_RE_ALIGN_ASCII_CJK_SPACE = re.compile(
    rf"(?<=[{_ALIGNMENT_ASCII_RANGE}])\s+(?=[{_ALIGNMENT_CJK_RANGE}])"
)
_RE_ALIGN_CJK_ASCII_COMPACT = re.compile(
    rf"([{_ALIGNMENT_CJK_RANGE}])([{_ALIGNMENT_ASCII_RANGE}])"
)
_RE_ALIGN_ASCII_CJK_COMPACT = re.compile(
    rf"([{_ALIGNMENT_ASCII_RANGE}])([{_ALIGNMENT_CJK_RANGE}])"
)
_RE_ALIGN_ASCII_WORD_SPACE = re.compile(
    rf"([{_ALIGNMENT_ASCII_RANGE}])\s+([{_ALIGNMENT_ASCII_RANGE}])"
)


def normalize_for_align(text: str) -> str:
    """规范化文本以便做粗对齐。"""

    text = text.translate(_REMOVE_ZERO_WIDTH)  # 删除零宽与控制字符
    text = unicodedata.normalize("NFKC", text)  # 使用 NFKC 统一全半角
    text = text.translate(_ASCII_UPPER)  # 将 ASCII 字母转换为小写
    text = text.translate(_PUNCT_TO_SPACE)  # 常见句读符号替换为空格
    text = text.translate(_REMOVE_OTHER_PUNCT)  # 其他标点直接移除
    text = " ".join(text.split())  # 折叠多余空白为单空格
    return text.strip()  # 去掉首尾空白后返回


def normalize_for_alignment(text: str, keep_ascii_word_spaces: bool = True) -> str:
    """生成用于词级对齐的单行文本。"""

    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\t", " ")
    normalized = re.sub(r"\s+", " ", normalized)

    base = normalize_for_align(normalized)
    if not base:
        return ""

    compacted = _RE_ALIGN_CJK_STRICT_SPACE.sub("", base)
    compacted = _RE_ALIGN_CJK_ASCII_SPACE.sub("", compacted)
    compacted = _RE_ALIGN_ASCII_CJK_SPACE.sub("", compacted)
    compacted = _RE_ALIGN_CJK_ASCII_COMPACT.sub(r"\1 \2", compacted)
    compacted = _RE_ALIGN_ASCII_CJK_COMPACT.sub(r"\1 \2", compacted)

    if not keep_ascii_word_spaces:
        compacted = _RE_ALIGN_ASCII_WORD_SPACE.sub(r"\1\2", compacted)

    compacted = re.sub(r"\s+", " ", compacted)
    return compacted.strip()


def prepare_alignment_text(text: str, collapse_lines: bool = False) -> str:
    """将规范化文本进一步转换为词级对齐友好的纯文本。"""

    normalised_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    if collapse_lines:
        return normalize_for_alignment(normalised_newlines)

    align_lines: list[str] = []
    for line in normalised_newlines.split("\n"):
        align_lines.append(normalize_for_alignment(line))

    return "\n".join(align_lines)


def _remove_spaces(text: str) -> str:
    """去除文本中的所有空白字符。"""

    return "".join(ch for ch in text if not ch.isspace())


def cjk_or_latin_seq(words: list[str]) -> str:
    """将词序列拼接为对齐用字符串。"""

    joined: list[str] = []  # 初始化结果列表
    for word in words:  # 遍历词序列
        joined.append(_remove_spaces(word))  # 去掉空白后写入结果
    return "".join(joined)  # 拼接成连续字符串


def build_char_index_map(word_texts: list[str]) -> list[tuple[int, int]]:
    """构建词到字符的索引映射。"""

    mapping: list[tuple[int, int]] = []  # 存储区间的结果列表
    cursor = 0  # 记录当前字符位置
    for text in word_texts:  # 遍历每个词的文本
        cleaned = _remove_spaces(text)  # 去掉空白获取用于匹配的内容
        start = cursor  # 记录当前词的起始下标
        cursor += len(cleaned)  # 根据字符长度推进光标
        mapping.append((start, cursor))  # 保存区间 [start, cursor)
    return mapping  # 返回索引映射
