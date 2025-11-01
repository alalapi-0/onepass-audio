"""按 EDL 渲染干净音频的核心逻辑。"""
from __future__ import annotations

import json  # 读取 EDL JSON
import shlex  # 美化 dry-run 输出
import subprocess  # 调用 ffmpeg/ffprobe
from dataclasses import dataclass  # 构建结构化数据模型
from pathlib import Path  # 统一路径处理
from typing import Iterable, Literal  # 提供类型注解

__all__ = [
    "EDLSegment",
    "EDLDoc",
    "load_edl",
    "resolve_source_audio",
    "probe_duration",
    "normalize_segments",
    "build_filter_complex",
    "render_audio",
]


@dataclass(slots=True)
class EDLSegment:
    """描述单个 EDL 片段的时间范围与动作。"""

    start: float
    end: float
    action: Literal["keep", "drop"]


@dataclass(slots=True)
class EDLDoc:
    """封装按段落描述剪辑动作的 EDL 文档。"""

    source_audio: str
    segments: list[EDLSegment]
    samplerate: int | None = None
    channels: int | None = None


_EPSILON = 1e-6  # 用于浮点比较的容差
_AUDIO_SUFFIXES: tuple[str, ...] = (".wav", ".flac", ".m4a", ".aac", ".mp3", ".ogg", ".wma")


def _to_float(value: object, field: str) -> float:
    """将任意对象转换为浮点数，失败时抛出带字段名的异常。"""

    try:  # 尝试直接转换
        return float(value)
    except (TypeError, ValueError) as exc:  # 转换失败时构造错误信息
        raise ValueError(f"字段 `{field}` 无法解析为浮点数: {value!r}") from exc


def _format_ts(value: float) -> str:
    """把秒数格式化为 ffmpeg 友好的字符串。"""

    # 保留 6 位小数，同时去掉结尾多余的零
    formatted = f"{value:.6f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _normalise_action(segment: dict) -> Literal["keep", "drop"]:
    """兼容 action/keep 两种字段并统一为 keep/drop。"""

    # 优先读取新式 action 字段
    action = segment.get("action")
    if isinstance(action, str):
        lowered = action.lower()
        if lowered in {"keep", "drop"}:
            return "keep" if lowered == "keep" else "drop"
    # 兼容旧式 keep 布尔字段
    keep_flag = segment.get("keep")
    if isinstance(keep_flag, bool):
        return "keep" if keep_flag else "drop"
    raise ValueError("片段需包含 action 或 keep 字段，且取值合法。")


def _merge_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """合并重叠或相邻的时间段。"""

    sorted_intervals = sorted(intervals, key=lambda pair: pair[0])  # 先按起点排序
    merged: list[tuple[float, float]] = []  # 初始化结果列表
    for start, end in sorted_intervals:  # 逐段处理
        if not merged:  # 第一个片段直接写入
            merged.append((start, end))
            continue
        last_start, last_end = merged[-1]  # 查看当前已合并的最后一个片段
        if start <= last_end + _EPSILON:  # 与上一个片段接触或重叠
            merged[-1] = (last_start, max(last_end, end))  # 扩展末尾范围
        else:  # 间隔明显，则新增片段
            merged.append((start, end))
    return merged


def load_edl(edl_path: Path) -> EDLDoc:
    """加载并校验 EDL JSON，兼容新旧段落结构。"""

    if not edl_path.exists():  # 若文件不存在提前报错
        raise FileNotFoundError(f"未找到 EDL 文件: {edl_path}")

    data = json.loads(edl_path.read_text(encoding="utf-8"))  # 读取 JSON 文本

    # 兼容新旧字段名 source_audio/audio
    source_audio_raw = (
        data.get("source_audio")
        or data.get("audio")
        or data.get("audio_stem")
        or ""
    )
    if not isinstance(source_audio_raw, str):  # 确保字段为字符串
        raise ValueError("EDL 文件需包含字符串类型的 source_audio/audio 字段。")

    # 解析采样率与声道设置
    samplerate = data.get("samplerate") or data.get("sample_rate")
    samplerate_int = int(samplerate) if isinstance(samplerate, (int, float)) else None
    channels = data.get("channels")
    channels_int = int(channels) if isinstance(channels, (int, float)) else None

    raw_segments = data.get("segments")  # 尝试读取新式片段列表
    if not isinstance(raw_segments, list):  # 当缺失时兼容旧式 actions
        raw_segments = []
        actions = data.get("actions")
        if isinstance(actions, list):
            for action in actions:  # 将旧式 cut 动作为 drop 片段
                if not isinstance(action, dict):
                    continue
                if action.get("type") not in {None, "cut"}:  # 仅处理剪切动作
                    continue
                start = _to_float(action.get("start"), "start")
                end = _to_float(action.get("end"), "end")
                raw_segments.append({"start": start, "end": end, "action": "drop"})

    if not raw_segments:
        raise ValueError("EDL 中未找到有效的 segments/actions 描述。")

    segments: list[EDLSegment] = []  # 存放转换后的片段
    for item in raw_segments:  # 遍历每个片段定义
        if not isinstance(item, dict):  # 忽略非法条目
            continue
        action = _normalise_action(item)  # 统一动作类型
        start = _to_float(item.get("start"), "start")  # 读取起始时间
        end = _to_float(item.get("end"), "end")  # 读取结束时间
        if end <= start:
            continue  # 去掉零长度或时间倒置片段
        segments.append(EDLSegment(start=start, end=end, action=action))  # 保存片段

    if not segments:
        raise ValueError("EDL 片段全为零长度或非法定义，无法继续。")

    return EDLDoc(
        source_audio=source_audio_raw,
        segments=segments,
        samplerate=samplerate_int,
        channels=channels_int,
    )


def resolve_source_audio(edl: EDLDoc, edl_path: Path, audio_root: Path) -> Path:
    """根据 EDL 描述解析源音频的实际路径。"""

    raw_path = Path(edl.source_audio.strip()) if edl.source_audio else Path()
    candidates: list[Path] = []  # 准备候选路径集合

    if raw_path.is_absolute():  # 若 EDL 已提供绝对路径
        candidates.append(raw_path)
    elif raw_path and raw_path.parts:  # 处理相对路径
        search_bases = [audio_root, edl_path.parent, audio_root.parent]
        seen: set[Path] = set()
        for base in search_bases:  # 遍历搜索基准目录
            if not base:
                continue
            try:
                base_resolved = base.resolve()
            except FileNotFoundError:
                base_resolved = base
            if base_resolved in seen:
                continue
            seen.add(base_resolved)
            candidates.append((base_resolved / raw_path).resolve())
    else:  # 当缺少路径信息时回退为空候选
        candidates = []

    for candidate in candidates:  # 检查候选是否存在
        if candidate.exists():
            return candidate

    if raw_path and not raw_path.suffix and audio_root.exists():  # 根据文件名前缀扫描
        matches: list[Path] = []
        for suffix in _AUDIO_SUFFIXES:
            matches.extend(audio_root.rglob(f"{raw_path.name}{suffix}"))
        if matches:
            return matches[0].resolve()

    raise FileNotFoundError(
        "无法定位源音频。请确认 EDL 中的 source_audio 字段填写正确，"
        "或在命令行中调整 --audio-root 指向包含音频的目录。"
    )


def probe_duration(audio_path: Path) -> float:
    """通过 ffprobe 获取音频总时长（秒）。"""

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:  # 未安装 ffprobe
        raise RuntimeError(
            "未找到 ffprobe，可通过安装 ffmpeg 并将其加入 PATH 解决。"
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            "ffprobe 调用失败，无法获取音频时长。请确认音频文件可访问且 ffmpeg 正常工作。"
        )

    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:  # 输出解析失败
        raise RuntimeError("无法解析 ffprobe 输出的时长，请检查音频文件是否损坏。") from exc

    if duration <= 0:
        raise RuntimeError("ffprobe 返回的时长非正值，可能是输入音频文件异常。")

    return duration


def normalize_segments(segments: list[EDLSegment], total: float) -> list[EDLSegment]:
    """整理片段定义，得到按时间升序的保留片段列表。"""

    if total <= 0:
        raise ValueError("音频总时长需为正数。")

    keeps_raw: list[tuple[float, float]] = []  # 收集 keep 片段
    drops_raw: list[tuple[float, float]] = []  # 收集 drop 片段
    for segment in segments:
        start = max(0.0, min(total, segment.start))  # 裁剪起点到合法范围
        end = max(0.0, min(total, segment.end))  # 裁剪终点到合法范围
        if end - start <= _EPSILON:  # 忽略零长度片段
            continue
        if segment.action == "keep":
            keeps_raw.append((start, end))
        else:
            drops_raw.append((start, end))

    if keeps_raw:  # 当直接提供 keep 段时
        merged = _merge_intervals(keeps_raw)
        return [EDLSegment(start=s, end=e, action="keep") for s, e in merged]

    if not drops_raw:  # 两者皆空视为错误
        raise ValueError("EDL 未提供任何可用于计算的片段。")

    merged_drops = _merge_intervals(drops_raw)  # 合并连续 drop 段
    keeps: list[EDLSegment] = []  # 准备保留片段结果
    cursor = 0.0  # 从时间轴起点开始扫描
    for start, end in merged_drops:
        if cursor + _EPSILON < start:  # 当前区域在 drop 前形成保留区
            keeps.append(EDLSegment(start=cursor, end=start, action="keep"))
        cursor = max(cursor, end)
    if cursor + _EPSILON < total:  # 处理末尾残余部分
        keeps.append(EDLSegment(start=cursor, end=total, action="keep"))

    filtered = [segment for segment in keeps if segment.end - segment.start > _EPSILON]
    if not filtered:
        raise ValueError("剪切片段覆盖了整段音频，无法导出有效结果。")
    return filtered


def build_filter_complex(
    keeps: list[EDLSegment],
    samplerate: int | None,
    channels: int | None,
) -> tuple[list[str], str]:
    """基于保留片段构造 ffmpeg filter_complex 参数。"""

    if not keeps:
        raise ValueError("缺少保留片段，无法构造滤镜。")

    chains: list[str] = []  # 存放逐片段的滤镜链
    for index, segment in enumerate(keeps):
        start = _format_ts(segment.start)
        end = _format_ts(segment.end)
        chains.append(
            f"[0:a]atrim=start={start}:end={end},asetpts=N/SR/TB[a{index}]"
        )  # 截取 + 校正时间戳

    concat_inputs = "".join(f"[a{idx}]" for idx in range(len(keeps)))  # 拼接所有标签
    post_filters: list[str] = []  # 收集可选的后处理滤镜
    if samplerate:
        post_filters.append(f"aresample={samplerate}")
    if channels:
        layout = {1: "mono", 2: "stereo"}.get(channels, f"{channels}c")  # 根据声道数映射布局
        post_filters.append(f"aformat=sample_fmts=s16:channel_layouts={layout}")

    final_label = "[ac]"  # 约定最终输出标签
    concat_filter = f"{concat_inputs}concat=n={len(keeps)}:v=0:a=1"
    if post_filters:
        concat_filter += "," + ",".join(post_filters)
    concat_filter += final_label  # 绑定输出标签

    filter_complex = ";".join(chains + [concat_filter])  # 拼装完整滤镜
    return ["-filter_complex", filter_complex], final_label


def render_audio(
    edl_path: Path,
    audio_root: Path,
    out_dir: Path,
    samplerate: int | None,
    channels: int | None,
    *,
    dry_run: bool = False,
) -> Path:
    """综合以上步骤执行音频裁剪与拼接，返回输出文件路径。"""

    edl = load_edl(edl_path)  # 读取并校验 EDL

    # 计算最终滤镜使用的采样率与声道设置，优先采用用户显式指定的值
    target_samplerate = samplerate if samplerate is not None else edl.samplerate  # 得到滤镜中使用的采样率
    target_channels = channels if channels is not None else edl.channels  # 得到滤镜中使用的声道数

    source_audio = resolve_source_audio(edl, edl_path, audio_root)  # 定位原始音频
    duration = probe_duration(source_audio)  # 获取音频总时长
    keeps = normalize_segments(edl.segments, duration)  # 归一化保留片段

    total_keep = sum(segment.end - segment.start for segment in keeps)
    if total_keep <= _EPSILON:
        raise RuntimeError("有效保留片段时长为 0，无法生成输出。")

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    output_path = out_dir / f"{source_audio.stem}.clean.wav"  # 约定输出文件名

    filter_args, label = build_filter_complex(keeps, target_samplerate, target_channels)

    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source_audio),
        *filter_args,
        "-map",
        label,
    ]
    if samplerate is not None:  # 仅在用户显式指定时追加采样率参数
        cmd.extend(["-ar", str(samplerate)])
    if channels is not None:  # 仅在用户显式指定时追加声道参数
        cmd.extend(["-ac", str(channels)])
    cmd.append(str(output_path))

    if dry_run:  # 仅打印命令供用户预览
        print(shlex.join(cmd))
        return output_path

    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError as exc:  # ffmpeg 不存在
        raise RuntimeError("未找到 ffmpeg，请安装后再试或将其加入 PATH。") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 渲染失败（退出码 {result.returncode}）。请检查命令行输出定位问题。"
        )

    if not output_path.exists():
        raise RuntimeError("ffmpeg 未生成预期的输出文件，请检查参数设置。")

    return output_path
