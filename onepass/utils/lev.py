"""Bounded Levenshtein distance utilities."""
from __future__ import annotations

from typing import Optional
import time

__all__ = ["bounded_levenshtein"]


def bounded_levenshtein(
    a: str,
    b: str,
    max_distance: int,
    *,
    deadline: Optional[float] = None,
) -> int:
    """Compute Levenshtein distance within a max threshold.

    The implementation follows the banded dynamic programming idea from
    Ukkonen's algorithm. Only cells satisfying ``|i - j| <= max_distance``
    are explored. If the minimum value of a row exceeds ``max_distance`` the
    computation aborts early and ``max_distance + 1`` is returned. A
    ``deadline`` (``time.monotonic`` based) may be supplied to support
    cooperative cancellation.
    """

    if max_distance < 0:
        return 0
    if a == b:
        return 0
    len_a = len(a)
    len_b = len(b)
    if len_a == 0:
        return len_b if len_b <= max_distance else max_distance + 1
    if len_b == 0:
        return len_a if len_a <= max_distance else max_distance + 1
    if abs(len_a - len_b) > max_distance:
        return max_distance + 1

    # Guarantee ``a`` is the shorter string to keep the band narrow.
    if len_a > len_b:
        a, b = b, a
        len_a, len_b = len_b, len_a

    band = max_distance
    size = (band * 2) + 1
    inf = max_distance + 1
    prev = [inf] * size
    cur = [inf] * size
    offset = band
    last_reset_template = [inf] * size

    for i in range(len_a + 1):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("bounded_levenshtein deadline reached")
        low = max(0, i - band)
        high = min(len_b, i + band)
        if low > high:
            return max_distance + 1
        row_min = inf
        for j in range(low, high + 1):
            band_idx = j - i + offset
            if band_idx < 0 or band_idx >= size:
                continue
            if i == 0:
                cur[band_idx] = j
            elif j == 0:
                cur[band_idx] = i
            else:
                cost = 0 if a[i - 1] == b[j - 1] else 1
                best = prev[band_idx] + cost
                if band_idx + 1 < size:
                    deletion = prev[band_idx + 1] + 1
                    if deletion < best:
                        best = deletion
                if band_idx - 1 >= 0:
                    insertion = cur[band_idx - 1] + 1
                    if insertion < best:
                        best = insertion
                cur[band_idx] = best
            if cur[band_idx] < row_min:
                row_min = cur[band_idx]
        if row_min > max_distance:
            return max_distance + 1
        prev, cur = cur, prev
        cur[:] = last_reset_template

    result_idx = len_b - len_a + offset
    if result_idx < 0 or result_idx >= len(prev):
        return max_distance + 1
    result = prev[result_idx]
    return result if result <= max_distance else max_distance + 1
