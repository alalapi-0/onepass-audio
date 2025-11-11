from __future__ import annotations

import re

_ASCII_ALNUM = re.compile(r'^[\x00-\x7F]+$')


def _is_ascii_alnum_edge(left: str, right: str) -> bool:
    """仅当左右均是 ASCII，且左尾/右首是字母或数字时，认为需要空格。"""
    if not left or not right:
        return False
    # 两端都是 ASCII
    if not (_ASCII_ALNUM.match(left[-1]) and _ASCII_ALNUM.match(right[0])):
        return False
    return left[-1].isalnum() and right[0].isalnum()


def collapse_soft_linebreaks(text: str) -> str:
    """
    合并软换行/制表符：
      - \r\n / \n / \t 统一处理；
      - 仅在 ASCII-ASCII 单词被换行切断时插入单个空格；
      - 其他场景（含中文/全角符号）直接相连，不插空格；
      - 幂等：重复调用结果不变。
    """
    if not text:
        return text

    # 先把制表符视作空格，避免与中文之间出现多余空白
    text = text.replace('\t', ' ')
    # 把多连空格收敛为单空格（仅限连续 ASCII 空格段）
    text = re.sub(r'([ -~])\s{2,}([ -~])', r'\1 \2', text)

    def _join(m: re.Match) -> str:
        left = m.string[: m.start()]
        right = m.string[m.end() :]
        left_char = left[-1] if left else ""
        right_char = right[0] if right else ""
        # 如果两侧都是 ASCII 且是字母/数字边界，插一个空格
        if _is_ascii_alnum_edge(left_char, right_char):
            return " "
        # 否则紧密相连（中文不断句）
        return ""

    # 合并所有软换行：保留两端字符，移除中间 \r?\n
    pattern = re.compile(r'(?:\r?\n)+')
    text = re.sub(pattern, _join, text)

    # 去除文首文末空白
    return text.strip()
