"""scripts.verify_asr_words
用途：验证 data/asr-json/ 下的 JSON 是否包含 segments[].words[*].start/end 字段。
依赖：Python 标准库 json、pathlib、sys；内部模块 ``onepass.ux``。
示例：
  python scripts/verify_asr_words.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from onepass.ux import enable_ansi, log_err, log_ok, log_warn, section

PROJ_ROOT = Path(__file__).resolve().parent.parent
ASR_DIR = PROJ_ROOT / "data" / "asr-json"


def _check_words(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    segments = data.get("segments")
    if not isinstance(segments, list):
        return False
    if not segments:
        return False
    for seg in segments:
        words = seg.get("words") if isinstance(seg, dict) else None
        if not isinstance(words, list) or not words:
            return False
        for word in words:
            if not isinstance(word, dict):
                return False
            if "start" not in word or "end" not in word:
                return False
    return True


def scan_directory(path: Path) -> tuple[list[Path], list[Path]]:
    ok_files: list[Path] = []
    warn_files: list[Path] = []
    for json_path in sorted(path.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log_err(f"解析失败：{json_path} -> {exc}")
            raise
        if _check_words(data):
            ok_files.append(json_path)
        else:
            warn_files.append(json_path)
    return ok_files, warn_files


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    section("验证 ASR JSON words 字段")
    if not ASR_DIR.exists():
        log_warn(f"目录不存在：{ASR_DIR}")
        return 1
    try:
        ok_files, warn_files = scan_directory(ASR_DIR)
    except Exception:
        log_err("FAIL：解析 JSON 时出现错误。")
        return 2
    if not ok_files and not warn_files:
        log_warn("目录中未找到 JSON 文件。")
        return 1
    if warn_files:
        log_warn("WARN：以下文件缺少 words 信息：")
        for path in warn_files:
            log_warn(f" - {path.relative_to(PROJ_ROOT)}")
        return 1
    log_ok("OK：所有 ASR JSON 均包含 words.start/end。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
