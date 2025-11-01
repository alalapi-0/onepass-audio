"""最小化的文本规范化与对齐辅助工具。"""
from __future__ import annotations

import unicodedata

__all__ = [
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
