"""根据 EDL 结果导出 Adobe Audition 标记的辅助函数。"""
from __future__ import annotations  # 启用未来注解语法

import csv  # 写入 CSV 标记文件
from pathlib import Path  # 统一路径处理

from .edl import EDL  # 引入 EDL 数据结构


def seconds_to_hmsms(seconds: float) -> str:
    """将秒数格式化为 ``hh:mm:ss.mmm`` 字符串。"""

    millis = max(0, round(seconds * 1000))  # 避免负值
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def ensure_csv_header(row: list[str]) -> None:
    """在开发阶段的最小断言，防止回归。"""

    assert row == ["Name", "Start", "Duration", "Type", "Description"], row


def write_audition_markers(edl: EDL, out_csv: Path) -> None:
    """将 ``edl`` 中的剪切动作写入 Audition 标记 CSV。"""

    out_csv.parent.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    rows = [["Name", "Start", "Duration", "Type", "Description"]]  # CSV 表头
    for index, action in enumerate(edl.actions, start=1):  # 遍历每个剪切动作
        name_suffix = f"{index:03d}"  # 统一三位编号
        start = seconds_to_hmsms(action.start)  # 起始时间（hh:mm:ss.mmm）
        end = seconds_to_hmsms(action.end)  # 结束时间
        duration = max(0.0, action.end - action.start)  # 计算持续时间
        span_duration = seconds_to_hmsms(duration)  # 标记持续时长
        rows.append([f"CUT_{name_suffix}", start, "00:00:00.000", "Marker", "cut duplicate sentence window"])  # 起点
        rows.append([f"END_{name_suffix}", end, "00:00:00.000", "Marker", "end duplicate sentence window"])  # 终点
        rows.append([f"CUTSPAN_{name_suffix}", start, span_duration, "Marker", "duplicate sentence span"])  # 区间

    ensure_csv_header(rows[0])  # 最小断言，避免表头被意外修改
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:  # 使用带 BOM 的 UTF-8 编码
        writer = csv.writer(f)  # 初始化 CSV 写入器
        writer.writerows(rows)  # 批量写入所有行


__all__ = ["write_audition_markers", "seconds_to_hmsms", "ensure_csv_header"]  # 导出函数
