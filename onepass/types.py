"""onepass.types
用途: 提供 OnePass Audio 核心流程使用的数据类型与通用工具。
依赖: Python 标准库 dataclasses、pathlib、datetime。
示例: ``from onepass.types import Word, ensure_outdir``。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path


@dataclass(slots=True)
class Word:
    """表示单个词语的时间戳信息。"""

    text: str
    start: float
    end: float


@dataclass(slots=True)
class Segment:
    """字幕或段落的文本与时间范围。"""

    text: str
    start: float
    end: float


@dataclass(slots=True)
class KeepSpan:
    """在词序列中需要保留的窗口信息。"""

    i: int
    j: int
    score: float
    start: float
    end: float


@dataclass(slots=True)
class EDLAction:
    """剪辑列表中的单个操作。"""

    type: str
    start: float
    end: float
    reason: str | None = None
    target_ms: int | None = None


@dataclass(slots=True)
class Paths:
    """运行一次流程所需的输入输出路径集合。"""

    json: Path
    original: Path
    outdir: Path


@dataclass(slots=True)
class Stats:
    """流程运行的统计数据。"""

    total_words: int = 0
    filler_removed: int = 0
    retake_cuts: int = 0
    long_pauses: int = 0
    shortened_ms: int = 0
    duplicated_sentences: int = 0


def ensure_outdir(p: Path) -> None:
    """确保输出目录存在。"""

    p.mkdir(parents=True, exist_ok=True)


def fmt_time_s(seconds: float) -> str:
    """格式化秒为 ``HH:MM:SS.mmm`` 字符串。"""

    if seconds < 0:
        seconds = 0.0
    total_seconds = float(seconds)
    td = timedelta(seconds=total_seconds)
    total_ms = int(round(td.total_seconds() * 1000))
    hours, remainder_ms = divmod(total_ms, 3600 * 1000)
    minutes, remainder_ms = divmod(remainder_ms, 60 * 1000)
    secs, millis = divmod(remainder_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
