r"""提供句子预处理与可配置文本规范化的工具集合。"""
from __future__ import annotations

import importlib
import importlib.util
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

# 重新导出旧版 API 依赖的符号，保持向后兼容。
__all__ = [
    "Sentence",
    "split_sentences",
    "normalize_sentence",
    "tokenize_for_match",
    "TextNormConfig",
    "DEFAULT_COMPAT_MAP",
    "load_custom_map",
    "normalize_text",
    "find_nonstandard_chars",
]


@dataclass
class Sentence:
    """表示规范化后的句子及其分词序列。"""

    text: str
    tokens: List[str]


# 旧版辅助函数复用的正则表达式。
_SENTENCE_PATTERN = re.compile(r"[^。！？；?!;\r\n]+[。！？；?!;]*", re.MULTILINE)
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_PUNCTUATION_RE = re.compile(r"\s*([。！？；?!;,，、])")


def split_sentences(raw_text: str) -> List[str]:
    """依据标点将原始文本切分为粗粒度句子。"""

    sentences: List[str] = []
    for match in _SENTENCE_PATTERN.finditer(raw_text):
        # 对匹配到的句子两端做空白归一。
        sentence = match.group().strip()
        if sentence:
            sentences.append(sentence)
    return sentences


def normalize_sentence(text: str) -> str:
    """压缩空白与标点，便于后续模糊匹配。"""

    text = text.replace("\u3000", " ")  # 将全角空格替换为半角
    text = re.sub(r"\s+", " ", text)  # 压缩连续空白字符
    text = _PUNCTUATION_RE.sub(r"\1", text)  # 去除标点前多余空格
    return text.strip()


def tokenize_for_match(text: str) -> List[str]:
    """将文本拆成 ASCII 词块与单个中日韩字符的序列。"""

    tokens: List[str] = []
    pending_ascii: List[str] = []
    for ch in text:
        if ch.isspace():
            # 遇到空白字符时刷新缓存的 ASCII token。
            if pending_ascii:
                tokens.append("".join(pending_ascii))
                pending_ascii.clear()
            continue
        if _ASCII_WORD_RE.fullmatch(ch):
            pending_ascii.append(ch.lower())
            continue
        if pending_ascii:
            tokens.append("".join(pending_ascii))
            pending_ascii.clear()
        tokens.append(ch)
    if pending_ascii:
        tokens.append("".join(pending_ascii))
    return tokens


@dataclass(slots=True)
class TextNormConfig:
    """控制 ``normalize_text`` 行为的配置开关。"""

    nfkc: bool = True
    strip_bom: bool = True
    strip_zw: bool = True
    collapse_spaces: bool = True
    punct_style: str = "ascii"
    map_compat: bool = True
    opencc_mode: str | None = None
    custom_map_path: str | None = "config/textnorm_custom_map.json"


DEFAULT_COMPAT_MAP: Dict[str, str] = {
    # OCR 场景中常见的偏旁部首与兼容字符映射。
    "⼈": "人",
    "⼒": "力",
    "⾔": "言",
    "⽹": "网",
    "⻔": "门",
    "⻢": "马",
    "⻓": "长",
    "⻋": "车",
    "⼀": "一",
    "⼆": "二",
    "⼗": "十",
    "⽬": "目",
    "⼿": "手",
    "⼤": "大",
}


# 以下集合定义了需要提示的零宽字符或可疑字符。
_ZERO_WIDTH_CHARS = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\ufeff",
    "\u2060",
}
_SUSPECT_CONTROL_CATEGORIES = {"Cf", "Cc"}


# 针对不同标点风格的转换表。
_PUNCT_ASCII_TABLE: Sequence[Tuple[str, str]] = (
    ("，", ","),
    ("。", "."),
    ("！", "!"),
    ("？", "?"),
    ("：", ":"),
    ("；", ";"),
    ("（", "("),
    ("）", ")"),
    ("【", "["),
    ("】", "]"),
    ("《", "<"),
    ("》", ">"),
    ("「", '"'),
    ("」", '"'),
    ("『", '"'),
    ("』", '"'),
    ("‘", "'"),
    ("’", "'"),
    ("“", '"'),
    ("”", '"'),
    ("、", ","),
    ("……", "..."),
    ("…", "..."),
    ("——", "-"),
    ("—", "-"),
    ("－", "-"),
)
_PUNCT_CJK_TABLE: Sequence[Tuple[str, str]] = (
    (",", "，"),
    (".", "。"),
    ("!", "！"),
    ("?", "？"),
    (":", "："),
    (";", "；"),
    ("(", "（"),
    (")", "）"),
    ("[", "【"),
    ("]", "】"),
    ("<", "《"),
    (">", "》"),
    ("\"", "”"),
    ("'", "’"),
    ("-", "——"),
    ("...", "……"),
)


_OPENCC_SPEC = importlib.util.find_spec("opencc")
_OPENCC_MODULE = importlib.import_module("opencc") if _OPENCC_SPEC else None
# 记录是否已经提示过用户缺少 opencc 支持。
_OPENCC_WARNING_EMITTED = False


def load_custom_map(path: str | None) -> Dict[str, str]:
    """在文件存在时加载额外的兼容字符映射。"""

    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 用户提供的 JSON 非法
        raise ValueError(f"无法解析自定义映射 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("自定义映射文件必须是键值对 JSON 对象。")
    # 仅保留字符串到字符串的映射，避免类型不符导致异常。
    return {str(key): str(value) for key, value in data.items()}


def _apply_punctuation(text: str, style: str) -> Tuple[str, int]:
    """按照指定风格转换标点并返回变换次数。"""

    if style == "keep":
        return text, 0
    replaced = 0
    working = text
    table = _PUNCT_ASCII_TABLE if style == "ascii" else _PUNCT_CJK_TABLE
    for source, target in table:
        if not source:
            continue
        # 替换匹配到的源标点并更新计数。
        new_text = working.replace(source, target)
        if new_text != working:
            if len(source) == 1:
                replaced += working.count(source)
            else:
                replaced += working.count(source)
            working = new_text
    return working, replaced


def _strip_zero_width(text: str) -> Tuple[str, int]:
    """移除文本中的零宽字符与控制字符。"""

    removed = 0
    chars: List[str] = []
    for ch in text:
        if ch in _ZERO_WIDTH_CHARS:
            removed += 1
            continue
        if unicodedata.category(ch) in _SUSPECT_CONTROL_CATEGORIES and ch not in {"\n", "\r", "\t"}:
            removed += 1
            continue
        chars.append(ch)
    return "".join(chars), removed


def _collapse_whitespace(text: str) -> Tuple[str, int]:
    """压缩连续空白符，同时保留段落换行。"""

    changed = 0
    normalised_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    segments = normalised_newlines.split("\n")
    lines: List[str] = []
    for line in segments:
        stripped = re.sub(r"[ \t\f\v]+", " ", line.strip())
        if stripped != line:
            changed += 1
        lines.append(stripped)
    return "\n".join(lines), changed


def _ensure_trailing_newline(text: str) -> str:
    """确保文本以单个换行符结尾。"""

    if not text.endswith("\n"):
        return text + "\n"
    return text


def normalize_text(text: str, cfg: TextNormConfig) -> Tuple[str, Dict[str, int]]:
    """依据配置执行规范化并返回统计信息。"""

    stats: Dict[str, int] = {
        "len_before": len(text),
        "len_after": len(text),
        "replaced_compat": 0,
        "removed_zw": 0,
        "bom_removed": 0,
        "punct_changes": 0,
        "space_collapses": 0,
    }

    working = text

    if cfg.nfkc:
        # 在执行具体替换前先做 Unicode NFKC 折叠。
        working = unicodedata.normalize("NFKC", working)

    if cfg.strip_bom and working.startswith("\ufeff"):
        # 去除偶尔混入文件头部的 Unicode BOM。
        working = working.lstrip("\ufeff")
        stats["bom_removed"] = 1

    if cfg.strip_zw:
        # 删除妨碍对齐的零宽或控制字符。
        working, removed = _strip_zero_width(working)
        stats["removed_zw"] = removed

    compat_map: Dict[str, str] = DEFAULT_COMPAT_MAP.copy() if cfg.map_compat else {}
    if cfg.map_compat and cfg.custom_map_path:
        compat_map.update(load_custom_map(cfg.custom_map_path))
    if compat_map:
        replaced = 0
        chars: List[str] = []
        for ch in working:
            # 将兼容用部首替换成常用形态。
            target = compat_map.get(ch)
            if target is not None:
                replaced += 1
                chars.append(target)
            else:
                chars.append(ch)
        working = "".join(chars)
        stats["replaced_compat"] = replaced

    working, punct_changes = _apply_punctuation(working, cfg.punct_style)
    stats["punct_changes"] = punct_changes

    if cfg.collapse_spaces:
        # 压缩重复空格/制表符，降低对齐噪声。
        working, collapsed = _collapse_whitespace(working)
        stats["space_collapses"] = collapsed

    if cfg.opencc_mode == "t2s":
        if _OPENCC_MODULE is not None:
            converter = _OPENCC_MODULE.OpenCC("t2s")
            working = converter.convert(working)
        else:
            global _OPENCC_WARNING_EMITTED
            if not _OPENCC_WARNING_EMITTED:
                print(
                    "提示: 未安装 opencc，已跳过繁转简，可执行 `pip install opencc` 启用。",
                    flush=True,
                )
                _OPENCC_WARNING_EMITTED = True

    working = _ensure_trailing_newline(working)
    stats["len_after"] = len(working)

    return working, stats


def find_nonstandard_chars(text: str) -> Dict[str, int]:
    """统计文本中可疑字符的数量，便于输出报告。"""

    suspect_chars = set(DEFAULT_COMPAT_MAP)
    suspect_chars.update(_ZERO_WIDTH_CHARS)
    counts: Dict[str, int] = {}
    for ch in text:
        if ch in suspect_chars or unicodedata.category(ch) in _SUSPECT_CONTROL_CATEGORIES:
            counts[ch] = counts.get(ch, 0) + 1
    return counts

