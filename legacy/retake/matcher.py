"""稳定的行级匹配器，兼容轻度口误与多音情况。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import re
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

try:  # pragma: no cover - 运行时可选依赖
    from pypinyin import Style, lazy_pinyin
except Exception:  # pragma: no cover - 容忍未安装 pypinyin
    Style = None  # type: ignore

    def lazy_pinyin(text: str, style: object | None = None, strict: bool = False) -> list[str]:
        """简易占位实现：无法获得准确拼音，仅返回原字符。"""

        return [char for char in text]


LOGGER = logging.getLogger(__name__)

_RE_SPACE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_DEFAULT_ALIAS_PATH = Path(__file__).resolve().parents[1] / "config" / "alias_map_zh.json"
_HASH_BASE = 257
_HASH_MASK = (1 << 64) - 1


@dataclass(slots=True)
class MatchRequest:
    """描述一次匹配请求。"""

    target_text: str
    max_distance_ratio: float
    min_anchor_ngram: int
    max_windows: int
    deadline: float | None = None


@dataclass(slots=True)
class MatchResponse:
    """匹配输出及诊断信息。"""

    success: bool
    char_range: tuple[int, int] | None
    distance: float | None
    ratio: float
    normalized_target: str
    normalized_candidate: str
    coarse_total: int
    coarse_passed: int
    fine_evaluated: int
    pruned_candidates: int
    elapsed_sec: float


def _normalize_for_match(text: str) -> str:
    """对匹配阶段的文本做 NFKC、大小写折叠与空白压缩。"""

    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text)
    value = value.lower()
    value = value.replace("\u3000", " ")
    value = _RE_SPACE.sub(" ", value)
    return value.strip()


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """返回匹配字符串及 processed->original 的索引映射。"""

    if not text:
        return "", []
    builder: list[str] = []
    mapping: list[int] = []
    prev_space = False
    for idx, ch in enumerate(text):
        normalized = unicodedata.normalize("NFKC", ch)
        for unit in normalized:
            token = unit.lower()
            if token.isspace():
                if prev_space:
                    continue
                builder.append(" ")
                mapping.append(idx)
                prev_space = True
            else:
                prev_space = False
                builder.append(token)
                mapping.append(idx)
    return "".join(builder), mapping


def _load_alias_pairs(path: Path | None) -> set[frozenset[str]]:
    """加载字级 alias 映射表。"""

    candidate_pairs: set[frozenset[str]] = set()
    actual_path = path or _DEFAULT_ALIAS_PATH
    try:
        if not actual_path.exists():
            return candidate_pairs
        data = json.loads(actual_path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as exc:  # pragma: no cover - 配置出错时兜底
        LOGGER.warning("加载 alias_map_zh 失败: path=%s error=%s", actual_path, exc)
        return candidate_pairs
    if not isinstance(data, dict):
        return candidate_pairs
    for canonical, variants in data.items():
        base = str(canonical or "").strip()
        if not base:
            continue
        raw_variants: Sequence[str]
        if isinstance(variants, str):
            raw_variants = [variants]
        elif isinstance(variants, Sequence):
            raw_variants = [str(item or "").strip() for item in variants]
        else:
            continue
        texts = [base, *[item for item in raw_variants if item and item != base]]
        for left in texts:
            for right in texts:
                if left == right or len(left) != len(right):
                    continue
                for l_char, r_char in zip(left, right):
                    if l_char == r_char:
                        continue
                    candidate_pairs.add(frozenset((l_char, r_char)))
    return candidate_pairs


@lru_cache(maxsize=8192)
def _first_key(char: str) -> str:
    """返回汉字拼音首字母，否则退回原字符。"""

    if not char:
        return ""
    if _CJK_RE.match(char):
        try:
            letters = lazy_pinyin(char, style=Style.FIRST_LETTER if Style else None, strict=False)
        except Exception:  # pragma: no cover - 第三方库异常
            letters = []
        if letters:
            first = letters[0]
            if first:
                return first[0]
    return char[0]


def _rolling_hash_power(base: int, exp: int) -> int:
    if exp <= 0:
        return 1
    return pow(base, exp, 1 << 64)


def _fast_candidates(words_chars: str, line_chars: str, k: int, limit: int) -> list[int]:
    if not words_chars or not line_chars or limit <= 0:
        return []
    limit = max(1, limit)
    max_k = min(max(k, 3), len(words_chars), len(line_chars))
    best_hits: dict[int, int] = {}
    for current_k in range(max_k, 2, -1):
        if len(line_chars) < current_k or len(words_chars) < current_k:
            continue
        high = _rolling_hash_power(_HASH_BASE, current_k - 1)
        index: dict[int, list[int]] = {}
        hash_value = 0
        for idx, ch in enumerate(words_chars):
            hash_value = ((hash_value * _HASH_BASE) + ord(ch)) & _HASH_MASK
            if idx + 1 >= current_k:
                start = idx + 1 - current_k
                index.setdefault(hash_value, []).append(start)
                leading = ord(words_chars[start])
                hash_value = (hash_value - (leading * high)) & _HASH_MASK
        if not index:
            continue
        line_hash = 0
        for idx, ch in enumerate(line_chars):
            line_hash = ((line_hash * _HASH_BASE) + ord(ch)) & _HASH_MASK
            if idx + 1 >= current_k:
                candidate_positions = index.get(line_hash)
                if candidate_positions:
                    segment = line_chars[idx + 1 - current_k : idx + 1]
                    for pos in candidate_positions:
                        if words_chars[pos : pos + current_k] == segment:
                            best_hits[pos] = best_hits.get(pos, 0) + 1
                leading = ord(line_chars[idx + 1 - current_k])
                line_hash = (line_hash - (leading * high)) & _HASH_MASK
        if best_hits:
            break
    if not best_hits:
        snippet_len = min(max_k, len(line_chars))
        snippet = line_chars[:snippet_len]
        pos = words_chars.find(snippet)
        if pos >= 0:
            best_hits[pos] = 1
    ordered = sorted(best_hits.items(), key=lambda item: (-item[1], item[0]))
    return [pos for pos, _ in ordered[:limit]]


def _coarse_features(text: str) -> dict[str, int]:
    if not text:
        return {}
    features: dict[str, int] = {}
    length = len(text)
    if length >= 3:
        for idx in range(length - 2):
            key = text[idx : idx + 3]
            features[key] = features.get(key, 0) + 1
    elif length == 2:
        key = text
        features[key] = features.get(key, 0) + 1
    else:
        features[text] = features.get(text, 0) + 1
    return features


def _coarse_similarity(candidate: str, target_features: dict[str, int], cache: dict[str, dict[str, int]]) -> float:
    feats = cache.get(candidate)
    if feats is None:
        feats = _coarse_features(candidate)
        cache[candidate] = feats
    if not feats and not target_features:
        return 1.0
    intersection = 0
    for key, value in feats.items():
        other = target_features.get(key)
        if other:
            intersection += min(value, other)
    union = sum(feats.values()) + sum(target_features.values()) - intersection
    if union <= 0:
        return 1.0
    return intersection / union


class StableMatcher:
    """在统一预处理后的词串上执行稳定编辑距离匹配。"""

    def __init__(
        self,
        transcript_chars: str,
        *,
        alias_map_path: Path | None = None,
    ) -> None:
        self._raw = transcript_chars or ""
        processed, mapping = _normalize_with_map(self._raw)
        self._processed = processed
        self._map = mapping
        self._first_keys = "".join(_first_key(ch) for ch in self._processed)
        self._alias_pairs = _load_alias_pairs(alias_map_path)

    def match(self, request: MatchRequest) -> MatchResponse:
        target_processed = _normalize_for_match(request.target_text)
        if not target_processed:
            return MatchResponse(False, None, None, 1.0, "", "", 0, 0, 0, 0, 0.0)
        target_keys = "".join(_first_key(ch) for ch in target_processed)
        candidates = _fast_candidates(self._processed, target_processed, request.min_anchor_ngram, request.max_windows)
        LOGGER.info(
            "stable-match: candidates=%s target_len=%s anchor=%s ratio=%.2f",
            len(candidates),
            len(target_processed),
            request.min_anchor_ngram,
            request.max_distance_ratio,
        )
        widen = max(1, int(len(target_processed) * max(request.max_distance_ratio, 0.15)))
        target_features = _coarse_features(target_keys)
        coarse_cache: dict[str, dict[str, int]] = {}
        best_range: tuple[int, int] | None = None
        best_distance: float | None = None
        best_ratio = 1.0
        best_candidate = ""
        coarse_total = 0
        coarse_passed = 0
        fine_evaluated = 0
        pruned_candidates = 0
        start_ts = time.monotonic()
        total_windows_est = max(1, len(candidates) * ((widen * 2) + 1))
        last_log_ts = start_ts
        for start_pos in candidates:
            if request.deadline and time.monotonic() > request.deadline:
                raise TimeoutError("match deadline")
            for delta in range(-widen, widen + 1):
                local_start = max(0, start_pos + delta)
                local_end = min(len(self._processed), local_start + len(target_processed) + widen)
                if local_end <= local_start:
                    continue
                candidate_text = self._processed[local_start:local_end]
                candidate_keys = self._first_keys[local_start:local_end]
                coarse_total += 1
                if coarse_total % 200 == 0 or coarse_total == total_windows_est:
                    now = time.monotonic()
                    if now - last_log_ts >= 1.5:
                        LOGGER.info(
                            "stable-match progress: %s/%s windows",
                            min(coarse_total, total_windows_est),
                            total_windows_est,
                        )
                        last_log_ts = now
                similarity = _coarse_similarity(candidate_keys, target_features, coarse_cache)
                if similarity < 0.12:
                    pruned_candidates += 1
                    continue
                coarse_passed += 1
                limit = max(1, int(math.ceil(max(len(candidate_text), len(target_processed)) * request.max_distance_ratio)))
                distance = self._stable_distance(candidate_text, target_processed, limit, request.deadline)
                fine_evaluated += 1
                if distance > limit:
                    continue
                ratio = 1.0
                denom = max(len(candidate_text), len(target_processed))
                if denom > 0:
                    ratio = distance / denom
                if (
                    best_distance is None
                    or distance < best_distance
                    or (math.isclose(distance, best_distance) and ratio < best_ratio)
                ):
                    best_distance = distance
                    best_ratio = ratio
                    best_range = (local_start, local_end)
                    best_candidate = candidate_text
                if distance <= max(1.0, limit * 0.2):
                    elapsed = time.monotonic() - start_ts
                    return MatchResponse(
                        True,
                        self._convert_range(best_range),
                        best_distance,
                        best_ratio,
                        target_processed,
                        best_candidate,
                        coarse_total,
                        coarse_passed,
                        fine_evaluated,
                        pruned_candidates,
                        elapsed,
                    )
        elapsed = time.monotonic() - start_ts
        return MatchResponse(
            best_range is not None,
            self._convert_range(best_range),
            best_distance,
            best_ratio,
            target_processed,
            best_candidate,
            coarse_total,
            coarse_passed,
            fine_evaluated,
            pruned_candidates,
            elapsed,
        )

    def _convert_range(self, processed_range: tuple[int, int] | None) -> tuple[int, int] | None:
        if not processed_range or not self._map:
            return processed_range
        start_idx, end_idx = processed_range
        if start_idx >= len(self._map) or end_idx <= 0:
            return None
        start_idx = max(0, min(start_idx, len(self._map) - 1))
        end_idx = max(0, min(end_idx - 1, len(self._map) - 1))
        orig_start = self._map[start_idx]
        orig_end = self._map[end_idx] + 1
        return orig_start, orig_end

    def _stable_distance(
        self,
        candidate: str,
        target: str,
        limit: int,
        deadline: float | None,
    ) -> float:
        len_a = len(candidate)
        len_b = len(target)
        if len_a == 0:
            return float(len_b)
        if len_b == 0:
            return float(len_a)
        band = limit + 2
        INF = float(limit) + 2.0
        prev = [float(j) for j in range(len_b + 1)]
        for i, char_a in enumerate(candidate, start=1):
            if deadline and time.monotonic() > deadline:
                raise TimeoutError("match deadline")
            current = [float(i)] + [INF] * len_b
            min_j = max(1, i - band)
            max_j = min(len_b, i + band)
            for j in range(min_j, max_j + 1):
                cost = self._char_cost(char_a, target[j - 1])
                sub_cost = prev[j - 1] + cost
                del_cost = prev[j] + 1.0
                ins_cost = current[j - 1] + 1.0
                current[j] = min(sub_cost, del_cost, ins_cost)
            prev = current
        return prev[len_b]

    def _char_cost(self, left: str, right: str) -> float:
        if left == right:
            return 0.0
        if self._alias_pairs and frozenset((left, right)) in self._alias_pairs:
            return 0.25
        if _first_key(left) == _first_key(right):
            return 0.25
        return 1.0


def match_lines(
    matcher: StableMatcher,
    lines: Iterable[tuple[int, str]],
    *,
    max_distance_ratio: float,
    min_anchor_ngram: int,
    max_windows: int,
    deadline: float | None,
) -> tuple[list[tuple[int, tuple[int, int]]], dict[str, object]]:
    """批量匹配行文本，返回命中区间及统计。"""

    spans: list[tuple[int, tuple[int, int]]] = []
    stats = {
        "coarse_total": 0,
        "coarse_passed": 0,
        "fine_evaluated": 0,
        "pruned_candidates": 0,
        "search_elapsed_sec": 0.0,
    }
    for line_no, text in lines:
        req = MatchRequest(
            target_text=text,
            max_distance_ratio=max_distance_ratio,
            min_anchor_ngram=min_anchor_ngram,
            max_windows=max_windows,
            deadline=deadline,
        )
        result = matcher.match(req)
        stats["coarse_total"] += result.coarse_total
        stats["coarse_passed"] += result.coarse_passed
        stats["fine_evaluated"] += result.fine_evaluated
        stats["pruned_candidates"] += result.pruned_candidates
        stats["search_elapsed_sec"] += result.elapsed_sec
        if result.success and result.char_range:
            spans.append((line_no, result.char_range))
    return spans, stats

