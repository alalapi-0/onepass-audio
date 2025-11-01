"""根据 EDL 结果导出 Adobe Audition 标记的辅助函数。"""
from __future__ import annotations  # 启用未来注解语法

import csv  # 写入 CSV 标记文件
from pathlib import Path  # 统一路径处理

from .edl import EDL  # 引入 EDL 数据结构


def write_audition_markers(edl: EDL, out_csv: Path) -> None:
    """将 ``edl`` 中的剪切动作写入 Audition 标记 CSV。"""

    out_csv.parent.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    rows = [["Name", "Start", "Duration", "Type", "Description"]]  # CSV 表头
    for index, action in enumerate(edl.actions, start=1):  # 遍历每个剪切动作
        name_suffix = f"{index:03d}"  # 统一三位编号
        start = f"{action.start:.3f}"  # 起始时间（秒，保留三位）
        end = f"{action.end:.3f}"  # 结束时间
        duration = max(0.0, action.end - action.start)  # 计算持续时间
        rows.append([f"CUT_{name_suffix}", start, "0", "Marker", "cut duplicate sentence window"])  # 标记剪切起点
        rows.append([f"END_{name_suffix}", end, "0", "Marker", "end duplicate sentence window"])  # 标记剪切终点
        rows.append([f"CUTSPAN_{name_suffix}", start, f"{duration:.3f}", "Marker", "duplicate sentence span"])  # 标记剪切区间
    with out_csv.open("w", encoding="utf-8", newline="") as f:  # 打开输出文件
        writer = csv.writer(f)  # 初始化 CSV 写入器
        writer.writerows(rows)  # 批量写入所有行


__all__ = ["write_audition_markers"]  # 导出函数
