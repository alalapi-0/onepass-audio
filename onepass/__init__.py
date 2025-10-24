"""onepass
用途: 暴露 OnePass Audio 核心流程与常用类型。
依赖: Python 标准库；子模块依赖见各文件头。
示例: ``from onepass import run_once, Paths``。
"""
from __future__ import annotations

from .aggr import map_aggr
from .asr_loader import load_words
from .clean import is_filler, remove_fillers
from .config import DEFAULT_CFG, load_config
from .edl import build_edl, edl_to_json
from .markers import write_audition_markers
from .pipeline import run_once
from .retake import find_retake_keeps
from .segment import to_segments
from .textnorm import norm_text, split_sentences
from .types import (
    EDLAction,
    KeepSpan,
    Paths,
    Segment,
    Stats,
    Word,
    ensure_outdir,
    fmt_time_s,
)
from .writers import write_plain, write_srt, write_vtt

__all__ = [
    "DEFAULT_CFG",
    "EDLAction",
    "KeepSpan",
    "Paths",
    "Segment",
    "Stats",
    "Word",
    "build_edl",
    "edl_to_json",
    "ensure_outdir",
    "find_retake_keeps",
    "fmt_time_s",
    "is_filler",
    "load_config",
    "load_words",
    "map_aggr",
    "norm_text",
    "remove_fillers",
    "run_once",
    "split_sentences",
    "to_segments",
    "write_audition_markers",
    "write_plain",
    "write_srt",
    "write_vtt",
]
