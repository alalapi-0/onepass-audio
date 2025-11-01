"""onepass.align
=================

句子与词级时间轴的模糊对齐模块，支持仅保留同句的最后一次出现。

示例
----
>>> from pathlib import Path  # 导入路径对象
>>> from onepass.asr_loader import load_words  # 读取词级 ASR 输出
>>> from onepass import textnorm  # 引入文本规范化工具
>>> words = load_words(Path('data/asr-json/001.json'))  # doctest: +SKIP  # 加载词级 JSON（示例跳过）
>>> sentences = [textnorm.Sentence(text=textnorm.normalize_sentence(s),  # 构造句子对象
...             tokens=textnorm.tokenize_for_match(textnorm.normalize_sentence(s)))  # 生成匹配用分词
...             for s in textnorm.split_sentences('示例文本')]  # doctest: +SKIP  # 逐句切分示例文本
>>> result = align_sentences(words, sentences)  # doctest: +SKIP  # 执行对齐并得到结果
"""
from __future__ import annotations  # 启用未来注解语法，避免前置引用限制

from dataclasses import dataclass  # 导入数据类装饰器，简化结构定义
from typing import Dict, Iterable, List, Optional  # 引入常用类型注解集合
from rapidfuzz import fuzz  # 引入模糊匹配评分函数

from .asr_loader import Word  # 导入词级时间戳数据结构
from .textnorm import Sentence, normalize_sentence, tokenize_for_match  # 导入句子结构与规范化函数


@dataclass  # 使用数据类自动生成初始化等方法
class MatchWindow:
    """在 ASR 词序列中记录一次模糊匹配窗口。"""  # 描述匹配窗口的结构

    sent_idx: int  # 对应的句子索引
    start_idx: int  # 起始词索引
    end_idx: int  # 结束词索引
    start: float  # 起始时间戳（秒）
    end: float  # 结束时间戳（秒）
    score: int  # 模糊匹配得分


@dataclass  # 使用数据类存储对齐结果
class AlignResult:
    """保存对齐后保留窗口、重复窗口与未对齐索引。"""  # 描述结果内容

    kept: Dict[int, Optional[MatchWindow]]  # 每个句子保留的最终窗口
    dups: Dict[int, List[MatchWindow]]  # 每个句子的重复窗口列表
    unaligned: List[int]  # 未找到匹配的句子索引


def _join_tokens(tokens: Iterable[str]) -> str:
    """将分词列表拼接为连续字符串。"""  # 简化拼接逻辑

    return "".join(tokens)  # 直接连接所有 token


def align_sentences(
    words: List[Word],  # ASR 输出的词列表
    sentences: List[Sentence],  # 规范化后的句子列表
    *,
    score_threshold: int = 80,  # 默认模糊匹配阈值
) -> AlignResult:
    """将句子与 ASR 词序列进行模糊对齐，保留同句的最后一次出现。"""  # 函数整体说明

    kept: Dict[int, Optional[MatchWindow]] = {}  # 初始化保留窗口字典
    dups: Dict[int, List[MatchWindow]] = {}  # 初始化重复窗口字典
    unaligned: List[int] = []  # 初始化未对齐索引列表

    if not sentences:  # 若句子列表为空
        return AlignResult(kept=kept, dups=dups, unaligned=unaligned)  # 直接返回空结果

    word_tokens: List[List[str]] = [  # 构建每个词的匹配用 token 列表
        tokenize_for_match(normalize_sentence(word.text)) for word in words  # 对词文本先规范化再分词
    ]
    word_token_strings: List[str] = [_join_tokens(toks) for toks in word_tokens]  # 预先拼接每个词窗口的 token
    token_prefix: List[int] = [0]  # 记录 token 累积长度的前缀和
    for toks in word_tokens:  # 遍历每个词的 token 列表
        token_prefix.append(token_prefix[-1] + max(len(toks), 1))  # 累加长度（至少为 1）

    total_words = len(words)  # 记录词总数

    for sent_idx, sentence in enumerate(sentences):  # 遍历每个句子
        target_tokens = sentence.tokens  # 取出目标句子的 token 列表
        if not target_tokens:  # 如果句子为空
            kept[sent_idx] = None  # 标记为空窗口
            unaligned.append(sent_idx)  # 记录未对齐索引
            continue  # 进入下一个句子

        base_len = len(target_tokens)  # 计算目标 token 数
        if base_len <= 6:  # 针对短句设定更宽松的窗口
            min_tokens = max(1, base_len - 2)  # 允许略短的窗口
            max_tokens = base_len + 2  # 允许略长的窗口
            threshold = min(score_threshold, 75)  # 阈值收紧至 75
        else:  # 普通长度句子
            slack = max(1, int(round(base_len * 0.2)))  # 根据句长设置允许偏差
            min_tokens = max(1, base_len - slack)  # 计算最小窗口长度
            max_tokens = base_len + slack  # 计算最大窗口长度
            threshold = score_threshold  # 使用默认阈值

        target_str = _join_tokens(target_tokens)  # 将目标 token 拼接为字符串
        matches: List[MatchWindow] = []  # 存放所有匹配窗口

        for start_idx in range(total_words):  # 枚举窗口起点
            for end_idx in range(start_idx, total_words):  # 枚举窗口终点
                token_count = token_prefix[end_idx + 1] - token_prefix[start_idx]  # 计算窗口内 token 数
                if token_count > max_tokens:  # 超过最大长度直接结束内层循环
                    break
                if token_count < min_tokens:  # 未达到最小长度则继续扩展
                    continue
                window_str = "".join(word_token_strings[start_idx : end_idx + 1])  # 拼接窗口字符串
                if not window_str:  # 若窗口为空字符串
                    continue  # 跳过该窗口
                score = int(fuzz.ratio(target_str, window_str))  # 计算模糊匹配得分
                if score >= threshold:  # 得分达到阈值
                    match = MatchWindow(  # 构造匹配窗口记录
                        sent_idx=sent_idx,  # 记录句子索引
                        start_idx=start_idx,  # 起始词索引
                        end_idx=end_idx,  # 结束词索引
                        start=words[start_idx].start,  # 起始时间
                        end=words[end_idx].end,  # 结束时间
                        score=score,  # 匹配得分
                    )
                    matches.append(match)  # 将匹配加入列表

        if not matches:  # 若没有找到匹配
            kept[sent_idx] = None  # 保留窗口为空
            unaligned.append(sent_idx)  # 记录未对齐
            continue  # 处理下一个句子

        matches.sort(key=lambda m: (m.end, m.score))  # 按结束时间与得分排序，确保最后出现的靠后
        kept_window = matches[-1]  # 取出最后一个作为保留窗口
        kept[sent_idx] = kept_window  # 保存保留窗口
        dups[sent_idx] = matches[:-1]  # 其他窗口作为重复记录

    return AlignResult(kept=kept, dups=dups, unaligned=unaligned)  # 返回综合结果
