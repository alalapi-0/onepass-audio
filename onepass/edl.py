"""根据对齐结果生成“保留最后一遍”的剪辑决策列表（EDL）。"""
from __future__ import annotations  # 启用未来注解语法，支持前置引用

from dataclasses import dataclass  # 引入数据类装饰器
from datetime import datetime, timezone  # 用于生成 UTC 时间戳
from typing import Dict, List, Tuple  # 引入常用类型注解

from .align import AlignResult  # 引入对齐结果数据结构
from .asr_loader import Word  # 引入词级时间戳数据结构


@dataclass  # 使用数据类简化初始化
class EDLAction:
    """描述 EDL 中的一条剪切动作。"""  # 解释该结构用途

    type: str  # 动作类型，例如 cut
    start: float  # 动作起始时间（秒）
    end: float  # 动作结束时间（秒）
    reason: str  # 剪切原因说明


@dataclass  # 数据类记录整个 EDL
class EDL:
    """封装生成的剪辑决策列表及统计信息。"""  # 说明字段含义

    audio_stem: str  # 对应音频文件的基础前缀
    sample_rate: float | None  # 可选的采样率信息
    actions: List[EDLAction]  # 剪切动作列表
    stats: Dict[str, float | int | None]  # 附带统计数据
    created_at: str  # 创建时间戳（ISO 格式）


def merge_intervals(
    intervals: List[Tuple[float, float]],  # 待合并的时间区间
    *,
    join_gap: float = 0.05,  # 允许合并的最大间隔秒数
) -> List[Tuple[float, float]]:
    """合并所有相互重叠或间距小于 ``join_gap`` 的区间。"""  # 函数说明

    if not intervals:  # 输入为空直接返回
        return []

    intervals = sorted(intervals, key=lambda pair: pair[0])  # 按起始时间排序
    merged: List[Tuple[float, float]] = [intervals[0]]  # 初始化结果列表
    for start, end in intervals[1:]:  # 遍历后续区间
        last_start, last_end = merged[-1]  # 取出上一个结果区间
        if start <= last_end + join_gap:  # 若当前区间与上一区间重叠或紧邻
            merged[-1] = (last_start, max(last_end, end))  # 合并区间，更新结束时间
        else:  # 否则认为是新的独立区间
            merged.append((start, end))  # 追加到结果列表
    return merged  # 返回最终合并结果


def build_keep_last_edl(words: List[Word], align: AlignResult) -> EDL:
    """根据对齐结果构建“保留最后一遍”的剪辑决策列表。"""  # 函数说明

    duplicate_intervals: List[Tuple[float, float]] = []  # 存放需要剪掉的重复区间

    for windows in align.dups.values():  # 遍历每个句子的重复窗口
        for window in windows:  # 遍历该句子的所有重复窗口
            duplicate_intervals.append((window.start, window.end))  # 收集时间区间

    merged = merge_intervals(duplicate_intervals)  # 合并重叠区间
    actions = [  # 构造剪切动作列表
        EDLAction(type="cut", start=start, end=end, reason="dup_sentence")  # 每个区间都标记为重复句子
        for start, end in merged
    ]

    total_cut = sum(max(0.0, action.end - action.start) for action in actions)  # 统计剪切总时长
    edl = EDL(
        audio_stem="",  # 默认不指定音频前缀，后续流程会填充
        sample_rate=None,  # 采样率信息可由后续流程写入
        actions=actions,  # 装载剪切动作
        stats={
            "total_input_sec": None,  # 总时长可由渲染阶段补充
            "total_cut_sec": total_cut,  # 剪切掉的总时长
            "num_sentences": len(align.kept),  # 原始句子总数
            "num_unaligned": len(align.unaligned),  # 未成功对齐的句子数量
        },
        created_at=datetime.now(tz=timezone.utc).isoformat(),  # 记录 UTC 创建时间
    )
    return edl  # 返回完整的 EDL 结构


__all__ = ["EDL", "EDLAction", "build_keep_last_edl", "merge_intervals"]  # 对外导出符号
