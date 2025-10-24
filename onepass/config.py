"""onepass.config
用途: 加载 OnePass Audio 所需的配置并提供默认值。
依赖: Python 标准库 json、pathlib、copy。
示例: ``from onepass.config import load_config``。
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


DEFAULT_CFG: dict = {
    "filler_terms": [
        "嗯",
        "呃",
        "啊",
        "哦",
        "噢",
        "唔",
        "诶",
        "嘛",
        "吧",
        "就是",
        "然后",
        "对吧",
        "其实",
        "那个",
        "就是说",
    ],
    "gap_newline_s": 0.6,
    "max_seg_dur_s": 5.0,
    "max_seg_chars": 32,
    "safety_pad_s": 0.08,
    "merge_gap_s": 0.25,
    "long_silence_s": 0.8,
    "retake_sim_threshold": 0.88,
    "tighten_target_ms": 300,
    "sentence_min_chars": 6,
    "align_min_sim": 0.84,
    "align_window_expand_ratio": 0.35,
    "align_strategy": "hybrid",
    "align_dp_max_chars": 200,
    "overlap_keep": "last",
    "punct_insensitive": True,
    "case_insensitive": True,
}


def load_config(path: Path | None) -> dict:
    """加载配置文件并返回配置字典。"""

    if path is None:
        return deepcopy(DEFAULT_CFG)
    path = path.expanduser()
    if not path.exists():
        return deepcopy(DEFAULT_CFG)
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError(f"配置文件 {path} 不是有效的 JSON: {exc}") from exc
    if not isinstance(data, dict):  # pragma: no cover - defensive
        raise ValueError(f"配置文件 {path} 应为 JSON 对象。")
    cfg = deepcopy(DEFAULT_CFG)
    cfg.update(data)
    return cfg
