"""onepass.aggr
用途: 将力度百分比映射到配置覆盖值。
依赖: Python 标准库 copy。
示例: ``from onepass.aggr import map_aggr``。
"""
from __future__ import annotations

from copy import deepcopy


def map_aggr(aggr: int, base_cfg: dict) -> dict:
    """根据力度百分比生成新的配置字典。"""

    level = max(0, min(100, int(aggr)))
    cfg = deepcopy(base_cfg)
    cfg["retake_sim_threshold"] = round(0.82 + 0.11 * (level / 100.0), 4)
    cfg["long_silence_s"] = round(1.2 - 0.7 * (level / 100.0), 3)
    cfg["tighten_target_ms"] = int(round(500 - 320 * (level / 100.0)))
    cfg["filler_strict"] = level >= 60
    return cfg
