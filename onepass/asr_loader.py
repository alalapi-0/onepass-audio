"""统一的词级 ASR JSON 适配层。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, Sequence
import json


@dataclass(slots=True)
class Word:
    """单个词的基础时间戳信息。"""

    text: str
    start: float
    end: float

    def duration(self) -> float:
        """返回该词对应的时间跨度（秒）。"""

        return self.end - self.start


@dataclass(slots=True)
class ASRDoc:
    """封装词级序列与可选元数据。"""

    words: List[Word]
    meta: dict | None = field(default=None)

    def __post_init__(self) -> None:
        """确保 meta 至少是字典，便于后续记录修复信息。"""

        if self.meta is None:
            self.meta = {}

    def __iter__(self) -> Iterator[Word]:
        """允许像列表一样迭代 :class:`Word` 条目。"""

        return iter(self.words)

    def __len__(self) -> int:
        """返回词的数量，兼容旧代码的 ``len(doc)`` 写法。"""

        return len(self.words)

    def __getitem__(self, index: int) -> Word:
        """支持 ``doc[index]`` 访问，保持向后兼容。"""

        return self.words[index]


def _word_from_raw(raw: object) -> Word | None:
    """从任意字典条目中解析 :class:`Word`。

    使用 ``word`` 或 ``text`` 字段作为词面值，自动 ``strip`` 去掉多余空格。
    若缺少 ``start``/``end`` 则返回 ``None``。"""

    if not isinstance(raw, dict):
        return None

    # 允许 "word" 或 "text" 两种命名，并对前导空格做 strip
    text_raw = raw.get("word") if "word" in raw else raw.get("text", "")
    text = str(text_raw).strip()
    if not text:
        return None

    try:
        start = float(raw["start"])
        end = float(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None

    if end <= start:
        return None
    return Word(text=text, start=start, end=end)


def _iter_words_from_segment(segment: object) -> Iterable[Word]:
    """从 faster-whisper 风格的 ``segment`` 中提取词数组。"""

    if not isinstance(segment, dict):
        return []
    words = segment.get("words")
    if not isinstance(words, Sequence):
        return []
    parsed: List[Word] = []
    for item in words:
        word = _word_from_raw(item)
        if word is not None:
            parsed.append(word)
    return parsed


def _load_raw_json(json_path: Path) -> object:
    """读取 JSON 文件并提供统一的错误信息。"""

    try:
        text = json_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - I/O 错误提示
        raise FileNotFoundError(
            f"未找到词级 JSON 文件: {json_path}. 请确认路径正确或素材已导出。"
        ) from exc
    except OSError as exc:  # pragma: no cover - I/O 错误提示
        raise OSError(
            f"无法读取 {json_path}: {exc}. 请检查权限或关闭占用该文件的程序。"
        ) from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"解析 JSON 失败: {json_path}. 请确认文件是否完整且为合法 JSON。"
        ) from exc


def load_words(json_path: Path) -> ASRDoc:
    """加载词级 ASR JSON 并返回统一数据结构。

    ``json_path`` 可以是 faster-whisper/Funasr 等包含 ``segments`` 或
    顶层 ``words`` 数组的格式。返回的对象实现了列表协议，可与第一轮代码
    向后兼容。"""

    data = _load_raw_json(json_path)
    words: List[Word] = []

    # 逐种结构尝试解析，优先处理 segments->words 的嵌套结构
    if isinstance(data, dict):
        segments = data.get("segments")
        if isinstance(segments, Sequence):
            for segment in segments:
                words.extend(_iter_words_from_segment(segment))
        # 某些实现直接把 words 放在顶层
        if not words and isinstance(data.get("words"), Sequence):
            for item in data["words"]:
                word = _word_from_raw(item)
                if word is not None:
                    words.append(word)
    elif isinstance(data, Sequence):
        for item in data:
            word = _word_from_raw(item)
            if word is not None:
                words.append(word)

    if not words:
        raise ValueError(
            "JSON 中未找到有效的词级条目。请确认导出的 ASR 结果包含 words 字段。"
        )

    # 检查时间是否递增，必要时排序并记录修复
    fixes: list[str] = []
    is_sorted = all(words[i].start <= words[i + 1].start for i in range(len(words) - 1))
    if not is_sorted:
        words = sorted(words, key=lambda item: (item.start, item.end))
        fixes.append("words_reordered_by_start")

    total_duration = words[-1].end - words[0].start
    if total_duration <= 0:
        raise ValueError(
            "词级时间戳总时长异常 (<=0)。请检查 start/end 是否正确或重新导出 JSON。"
        )

    meta: dict[str, object] = {"source": str(json_path)}
    if fixes:
        meta["fixes"] = fixes

    return ASRDoc(words=words, meta=meta)


__all__ = ["Word", "ASRDoc", "load_words"]
