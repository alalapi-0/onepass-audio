"""加载 EDL JSON 并将剪切动作转换为保留区间的辅助工具。"""
from __future__ import annotations  # 启用未来注解语法

import json  # 读取与解析 JSON 数据
from pathlib import Path  # 统一路径对象
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple  # 引入常用类型注解


def load_edl(path: Path) -> Dict[str, Any]:
    """读取 EDL JSON 文件并返回字典结构。"""  # 函数说明

    with path.open("r", encoding="utf-8") as fh:  # 打开文件并保证 UTF-8 编码
        return json.load(fh)  # 解析 JSON 内容


def _merge_intervals(intervals: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """合并所有重叠或相连的区间。"""  # 内部工具

    if not intervals:  # 若无区间直接返回空列表
        return []

    sorted_intervals = sorted(intervals, key=lambda item: item[0])  # 按起始时间排序
    merged: List[Tuple[float, float]] = [sorted_intervals[0]]  # 初始化合并结果

    for start, end in sorted_intervals[1:]:  # 遍历剩余区间
        last_start, last_end = merged[-1]  # 取出最后的结果区间
        if start <= last_end:  # 如果当前区间与上一个重叠或相接
            merged[-1] = (last_start, max(last_end, end))  # 合并并更新结束时间
        else:  # 否则视为新的区间
            merged.append((start, end))  # 追加到结果中

    return merged  # 返回合并后的区间


def _merge_small_gaps(
    intervals: Iterable[Tuple[float, float]],  # 待处理的保留区间
    *,
    tolerance: float = 0.005,  # 允许自动合并的小间隙阈值（秒）
) -> List[Tuple[float, float]]:
    """合并间隔小于 ``tolerance`` 秒的相邻保留区间。"""  # 内部工具说明

    merged: List[Tuple[float, float]] = []  # 初始化输出列表
    for start, end in intervals:  # 遍历区间
        if not merged:  # 第一个区间直接放入
            merged.append((start, end))
            continue

        prev_start, prev_end = merged[-1]  # 取最后一个区间
        if start - prev_end < tolerance:  # 间隙小于阈值则合并
            merged[-1] = (prev_start, max(prev_end, end))
        else:  # 否则直接追加
            merged.append((start, end))
    return merged  # 返回处理结果


def edl_to_keep_intervals(
    edl: Dict[str, Any],  # 解析后的 EDL 字典
    *,
    audio_duration: Optional[float] = None,  # 可选音频总时长提示
) -> List[Tuple[float, float]]:
    """将剪切动作求补，得到需要保留的时间区间列表。"""  # 函数说明

    actions = edl.get("actions")  # 读取剪切动作列表
    if not isinstance(actions, list):  # 校验结构
        raise ValueError("EDL JSON 缺少 'actions' 列表")

    cut_intervals: List[Tuple[float, float]] = []  # 收集剪切区间
    for action in actions:  # 遍历每条动作
        if not isinstance(action, dict):  # 跳过非法结构
            continue
        if action.get("type") != "cut":  # 仅处理剪切动作
            continue

        try:
            start = float(action.get("start", 0.0))  # 解析开始时间
            end = float(action.get("end", start))  # 解析结束时间，默认不小于 start
        except (TypeError, ValueError) as exc:  # 防御性捕获异常
            raise ValueError("EDL 动作中的 start/end 无法解析") from exc

        start = max(0.0, start)  # 保证非负
        end = max(0.0, end)  # 保证非负
        if end <= start:  # 忽略无效区间
            continue
        cut_intervals.append((start, end))  # 收集有效剪切区间

    max_end_candidates: List[float] = []  # 准备候选音频结束时间
    if audio_duration is not None and audio_duration > 0:  # 若显式提供时长
        max_end_candidates.append(audio_duration)

    stats = edl.get("stats")  # 尝试读取统计信息
    if isinstance(stats, dict):
        total_input = stats.get("total_input_sec")  # 读取原始时长
        if isinstance(total_input, (int, float)) and total_input > 0:
            max_end_candidates.append(float(total_input))  # 加入候选

    if cut_intervals:  # 若存在剪切区间
        max_end_candidates.append(max(end for _, end in cut_intervals))  # 用最后一个剪切端点作为备选

    if not max_end_candidates:  # 无法确定时间轴上限
        raise ValueError("无法从 EDL 或参数中推断音频总时长")

    timeline_end = max(max_end_candidates)  # 取最大值作为时间轴终点
    merged_cuts = _merge_intervals(cut_intervals)  # 合并剪切区间避免重叠

    keep_intervals: List[Tuple[float, float]] = []  # 存放保留区间
    cursor = 0.0  # 当前指针，从 0 秒开始
    for start, end in merged_cuts:  # 遍历每个剪切区间
        start = min(start, timeline_end)  # 限制在时间轴范围内
        end = min(end, timeline_end)  # 限制在时间轴范围内
        if cursor < start:  # 指针之前是需要保留的内容
            keep_intervals.append((cursor, start))  # 追加保留区间
        cursor = max(cursor, end)  # 更新指针至当前剪切末尾

    if cursor < timeline_end:  # 剩余尾段也需要保留
        keep_intervals.append((cursor, timeline_end))

    filtered: List[Tuple[float, float]] = [  # 过滤掉极短或负值区间
        (max(0.0, start), max(0.0, end))
        for start, end in keep_intervals
        if end - start > 1e-6
    ]

    return _merge_small_gaps(filtered)  # 返回合并微小间隙后的结果


def human_sec(seconds: float) -> str:
    """将秒数转换为更易读的字符串形式。"""  # 工具函数说明

    if seconds < 0:  # 负值视为 0
        seconds = 0.0

    total_seconds = int(seconds)  # 取整秒部分
    remainder = seconds - total_seconds  # 计算小数部分

    hours, remainder_seconds = divmod(total_seconds, 3600)  # 分离小时
    minutes, secs = divmod(remainder_seconds, 60)  # 分离分钟与秒
    frac = remainder  # 小数部分保留到毫秒
    if hours:  # 含小时的格式
        return f"{hours:d}:{minutes:02d}:{secs:02d}.{int(frac * 1000):03d}s"
    if minutes:  # 含分钟的格式
        return f"{minutes:d}:{secs:02d}.{int(frac * 1000):03d}s"
    return f"{seconds:.3f}s"  # 默认以秒为单位保留三位小数


__all__ = ["edl_to_keep_intervals", "human_sec", "load_edl"]  # 对外暴露的函数
