"""EDL helpers for both legacy action lists and unified segment schema."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from typing import Literal

from legacy.align import AlignResult  # 引入对齐结果数据结构
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


@dataclass(slots=True)
class Segment:
    """Unified segment representation used by renderer and CLI."""

    start: float
    end: float
    action: Literal["keep", "cut"] = "keep"
    metadata: Dict[str, Any] | None = None


@dataclass(slots=True)
class SegmentEDL:
    """Structured document returned by :func:`load`."""

    source_audio: str | None
    segments: List[Segment]
    samplerate: int | None = None
    channels: int | None = None
    stem: str | None = None
    version: int | None = None
    source_samplerate: int | None = None
    source_audio_basename: str | None = None
    path_style: str = "posix"
    stats: Dict[str, Any] | None = None


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


def _derive_stem_from_path(path: Path) -> str:
    name = path.name
    if name.endswith(".edl.json"):
        name = name[: -len(".edl.json")]
    elif name.endswith(".json"):
        name = name[: -len(".json")]
    for suffix in (".edl", ".keepLast", ".keep", ".sentence"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _to_float(value: object, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"字段 `{field}` 无法解析为浮点数: {value!r}") from exc


def _to_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalise_action(raw: object, keep_flag: object, legacy_type: object) -> Literal["keep", "cut"]:
    candidates: list[object] = [raw, legacy_type]
    for candidate in candidates:
        if isinstance(candidate, str):
            lowered = candidate.strip().lower()
            if lowered in {"keep", "cut"}:
                return "keep" if lowered == "keep" else "cut"
            if lowered == "drop":
                return "cut"
    if isinstance(keep_flag, bool):
        return "keep" if keep_flag else "cut"
    return "keep"


def load(path: Path | str) -> SegmentEDL:
    """Load an EDL file and normalise it into unified segments."""

    edl_path = Path(path).expanduser()
    if not edl_path.exists():
        raise FileNotFoundError(f"未找到 EDL 文件: {edl_path}")
    data = json.loads(edl_path.read_text(encoding="utf-8", errors="replace"))
    raw_segments = data.get("segments")
    items: list[dict[str, Any]] = [item for item in raw_segments or [] if isinstance(item, dict)]
    segments: list[Segment] = []
    for item in items:
        start = _to_float(item.get("start"), "start")
        end = _to_float(item.get("end"), "end")
        if end <= start:
            continue
        action = _normalise_action(item.get("action"), item.get("keep"), item.get("type"))
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else None
        segments.append(Segment(start=start, end=end, action=action, metadata=metadata))
    if not segments:
        actions = data.get("actions")
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                if action.get("type") not in {None, "cut"}:
                    continue
                start = _to_float(action.get("start"), "start")
                end = _to_float(action.get("end"), "end")
                if end <= start:
                    continue
                reason = action.get("reason")
                metadata = {"reason": reason.strip()} if isinstance(reason, str) and reason.strip() else None
                segments.append(Segment(start=start, end=end, action="cut", metadata=metadata))
    if not segments:
        raise ValueError(f"EDL 缺少有效的 segments/actions 描述: {edl_path}")
    segments.sort(key=lambda seg: (seg.start, seg.end))
    source_raw = data.get("source_audio") or data.get("audio") or ""
    source_audio = source_raw.strip() if isinstance(source_raw, str) else None
    samplerate = _to_optional_int(data.get("samplerate") or data.get("sample_rate"))
    channels = _to_optional_int(data.get("channels"))
    source_samplerate = _to_optional_int(data.get("source_samplerate"))
    stem_field = data.get("stem")
    stem = stem_field.strip() if isinstance(stem_field, str) and stem_field.strip() else _derive_stem_from_path(edl_path)
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else None
    path_style_field = data.get("path_style")
    path_style = path_style_field.strip().lower() if isinstance(path_style_field, str) else "posix"
    if path_style not in {"posix", "windows"}:
        path_style = "posix"
    basename_field = data.get("source_audio_basename")
    basename = basename_field.strip() if isinstance(basename_field, str) and basename_field.strip() else None
    if not basename and source_audio:
        basename = Path(source_audio).name
    version_value = data.get("version") or data.get("schema_version")
    version = _to_optional_int(version_value)
    return SegmentEDL(
        source_audio=source_audio,
        segments=segments,
        samplerate=samplerate,
        channels=channels,
        stem=stem,
        version=version,
        source_samplerate=source_samplerate,
        source_audio_basename=basename,
        path_style=path_style,
        stats=stats,
    )


__all__ = [
    "EDL",
    "EDLAction",
    "Segment",
    "SegmentEDL",
    "build_keep_last_edl",
    "merge_intervals",
    "load",
]
