"""为 EDL 文件补充或更新 source_audio 字段。

用法示例：
    python scripts/edl_set_source.py \
      --edl out/demo.keepLast.edl.json \
      --source materials/example/demo.wav
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from onepass.logging_utils import default_log_dir, setup_logger


def main(argv: list[str] | None = None) -> int:
    """命令行入口，为现有 EDL 写入 source_audio。"""

    parser = argparse.ArgumentParser(description="为 EDL 写入或更新 source_audio 字段")
    parser.add_argument("--edl", required=True, help="目标 EDL JSON 路径")
    parser.add_argument("--source", required=True, help="音频文件相对路径或绝对路径")
    args = parser.parse_args(argv)

    logger = setup_logger(__name__, default_log_dir())
    edl_path = Path(args.edl)
    source_path = Path(args.source)

    if not edl_path.is_file():
        message = f"未找到 EDL 文件：{edl_path}"
        logger.error(message)
        print(message, file=sys.stderr)
        return 1

    try:
        content = edl_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except FileNotFoundError:
        message = f"未找到 EDL 文件：{edl_path}"
        logger.error(message)
        print(message, file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        message = f"解析 EDL JSON 失败：{exc}"
        logger.exception(message)
        print(message, file=sys.stderr)
        return 1

    # 使用 POSIX 风格写入路径，便于跨平台共享
    data["source_audio"] = source_path.as_posix()

    edl_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已更新 %s 的 source_audio → %s", edl_path, data["source_audio"])
    print(f"已更新 {edl_path} 的 source_audio → {data['source_audio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
