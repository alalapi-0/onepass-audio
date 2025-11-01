"""批量规范化原文 TXT 的命令行脚本。

用法示例：

```bash
python scripts/normalize_original.py \
  --in materials/example/demo.txt \
  --out out/norm \
  --char-map config/default_char_map.json \
  --opencc none
```
"""
from __future__ import annotations

import argparse  # 解析命令行参数
import csv  # 输出规范化报表
import sys  # 控制退出码
from pathlib import Path  # 处理跨平台路径
from typing import Dict, Iterable, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT_DIR) not in sys.path:  # 确保可以导入 onepass 包
    sys.path.insert(0, str(ROOT_DIR))

from onepass.text_norm import (  # 导入规范化工具
    load_char_map,
    normalize_pipeline,
    run_opencc_if_available,
    scan_suspects,
)
DEFAULT_OUT_DIR = ROOT_DIR / "out" / "norm"  # 默认输出目录
DEFAULT_REPORT_PATH = ROOT_DIR / "out" / "normalize_report.csv"  # 报表路径
DEFAULT_CHAR_MAP = ROOT_DIR / "config" / "default_char_map.json"  # 默认字符映射

REPORT_FIELDS = [
    "file",
    "orig_len",
    "norm_len",
    "deleted_count",
    "mapped_count",
    "width_normalized_count",
    "space_normalized_count",
    "opencc_mode",
    "opencc_applied",
    "suspects_found",
    "suspects_examples",
    "status",
    "message",
]


def _ensure_out_dir(out_dir: Path) -> Path:
    """确保输出目录位于 out/ 下并创建。"""

    resolved = (out_dir if out_dir.is_absolute() else (ROOT_DIR / out_dir)).resolve()  # 解析输出路径
    out_root = (ROOT_DIR / "out").resolve()  # out 根目录
    try:
        resolved.relative_to(out_root)  # 验证是否位于 out/ 下
    except ValueError as exc:
        raise ValueError(
            f"输出目录必须位于 {out_root} 内，请使用 out/<子目录>。当前: {resolved}"
        ) from exc
    resolved.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    return resolved


def _collect_input_paths(target: Path, pattern: str) -> List[Path]:
    """收集待处理的 TXT 路径列表。"""

    if target.is_file():  # 单文件直接返回
        return [target]
    if not target.is_dir():  # 非法路径给出提示
        raise FileNotFoundError(f"输入路径不存在: {target}。请检查 --in 参数。")
    return sorted(p for p in target.rglob(pattern) if p.is_file())  # 递归匹配指定后缀


def _relative_to_base(path: Path, base: Path) -> Path:
    """计算文件相对路径，若失败则仅返回文件名。"""

    try:
        return path.relative_to(base)  # 优先相对路径
    except ValueError:
        return Path(path.name)  # 回退为文件名


def _format_suspects(suspects: Dict[str, Dict[str, object]]) -> Tuple[bool, str]:
    """整理可疑字符统计与示例。"""

    parts: List[str] = []  # 汇总描述
    found = False  # 是否存在可疑字符
    for key, info in suspects.items():  # 遍历类别
        count = int(info.get("count", 0))
        examples = info.get("examples", [])
        if count:
            found = True
            sample = ", ".join(str(item) for item in examples)
            parts.append(f"{key}:{sample}")
    return found, "; ".join(parts)


def _process_file(
    path: Path,
    *,
    out_dir: Path,
    base_dir: Path,
    cmap: dict,
    opencc_mode: str,
    dry_run: bool,
) -> Dict[str, object]:
    """处理单个文本文件并返回报表数据。"""

    message_notes: List[str] = []  # 处理过程中的提示
    try:
        raw_text = path.read_text(encoding="utf-8")  # 尝试以 UTF-8 读取
        decode_note = ""
    except UnicodeDecodeError:
        raw_text = path.read_text(encoding="utf-8", errors="replace")  # 失败则使用替换模式
        decode_note = "原文包含非 UTF-8 字符，已用替换符号保留，请检查源文件编码。"
    except OSError as exc:
        return {
            "file": str(path),
            "orig_len": 0,
            "norm_len": 0,
            "deleted_count": 0,
            "mapped_count": 0,
            "width_normalized_count": 0,
            "space_normalized_count": 0,
            "opencc_mode": opencc_mode,
            "opencc_applied": "false",
            "suspects_found": "false",
            "suspects_examples": "",
            "status": "failed",
            "message": f"读取失败: {exc}. 请检查文件权限或路径是否存在。",
        }

    if decode_note:
        message_notes.append(decode_note)

    try:
        normalized_text, stats = normalize_pipeline(
            raw_text,
            cmap,
            use_width=bool(cmap.get("normalize_width", False)),
            use_space=bool(cmap.get("normalize_space", False)),
            preserve_cjk_punct=bool(cmap.get("preserve_cjk_punct", False)),
        )  # 执行规范化管线
    except Exception as exc:
        return {
            "file": str(path),
            "orig_len": len(raw_text),
            "norm_len": 0,
            "deleted_count": 0,
            "mapped_count": 0,
            "width_normalized_count": 0,
            "space_normalized_count": 0,
            "opencc_mode": opencc_mode,
            "opencc_applied": "false",
            "suspects_found": "false",
            "suspects_examples": "",
            "status": "failed",
            "message": f"规范化失败: {exc}. 请检查字符映射配置或文本内容。",
        }

    converted_text, opencc_applied = run_opencc_if_available(normalized_text, opencc_mode)  # 调用 opencc
    if opencc_mode != "none" and not opencc_applied:
        message_notes.append("OpenCC 未安装或执行失败，已跳过繁简转换。请安装 opencc 后重试。")

    suspects = scan_suspects(converted_text)  # 扫描可疑字符
    suspects_found, suspects_examples = _format_suspects(suspects)  # 整理结果

    relative = _relative_to_base(path, base_dir)  # 计算相对路径
    out_path = out_dir / relative.parent / f"{relative.stem}.norm.txt"  # 输出文件路径

    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
        payload = converted_text  # 准备写入的文本内容
        if payload and not payload.endswith("\n"):  # 确保以换行结尾
            payload += "\n"
        try:
            out_path.write_text(payload, encoding="utf-8")  # 写入规范化文本
        except OSError as exc:
            return {
                "file": str(path),
                "orig_len": len(raw_text),
                "norm_len": len(converted_text),
                "deleted_count": stats.get("deleted_count", 0),
                "mapped_count": stats.get("mapped_count", 0),
                "width_normalized_count": stats.get("width_normalized_count", 0),
                "space_normalized_count": stats.get("space_normalized_count", 0),
                "opencc_mode": opencc_mode,
                "opencc_applied": str(opencc_applied).lower(),
                "suspects_found": str(suspects_found).lower(),
                "suspects_examples": suspects_examples,
                "status": "failed",
                "message": f"写入失败: {exc}. 请确认输出目录可写。",
            }

    if not message_notes:
        message_notes.append("处理成功。")

    return {
        "file": str(path),
        "orig_len": len(raw_text),
        "norm_len": len(converted_text),
        "deleted_count": stats.get("deleted_count", 0),
        "mapped_count": stats.get("mapped_count", 0),
        "width_normalized_count": stats.get("width_normalized_count", 0),
        "space_normalized_count": stats.get("space_normalized_count", 0),
        "opencc_mode": opencc_mode,
        "opencc_applied": str(opencc_applied).lower(),
        "suspects_found": str(suspects_found).lower(),
        "suspects_examples": suspects_examples,
        "status": "ok",
        "message": "；".join(message_notes),
    }


def _write_report(rows: Iterable[Dict[str, object]], report_path: Path) -> None:
    """写出 CSV 报表。"""

    report_path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    with report_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    """命令行入口，执行批量规范化。"""

    parser = argparse.ArgumentParser(description="批量规范化原文 TXT 并生成报表。")
    parser.add_argument("--in", dest="input", required=True, help="输入文件或目录路径")
    parser.add_argument("--out", dest="output", default=str(DEFAULT_OUT_DIR), help="规范化文本输出目录")
    parser.add_argument("--char-map", dest="char_map", default=str(DEFAULT_CHAR_MAP), help="字符映射配置 JSON")
    parser.add_argument("--opencc", choices=["none", "t2s", "s2t"], default="none", help="opencc 转换模式")
    parser.add_argument("--glob", default="*.txt", help="目录模式匹配，默认 *.txt")
    parser.add_argument("--dry-run", action="store_true", help="仅生成报表，不写规范化文本")
    args = parser.parse_args()

    try:
        out_dir = _ensure_out_dir(Path(args.output))  # 解析并创建输出目录
    except Exception as exc:
        print(f"[错误] {exc}")
        sys.exit(1)

    try:
        cmap = load_char_map(Path(args.char_map))  # 加载字符映射
    except Exception as exc:
        print(f"[错误] 加载字符映射失败: {exc}")
        sys.exit(1)

    input_path = Path(args.input).expanduser().resolve()  # 解析输入路径
    try:
        input_files = _collect_input_paths(input_path, args.glob)  # 收集待处理文件
    except Exception as exc:
        print(f"[错误] {exc}")
        sys.exit(1)

    if not input_files:
        print("[警告] 未找到任何匹配的文本文件，已生成空报告。")

    if input_path.is_file():
        base_dir = input_path.parent
    else:
        base_dir = input_path

    rows: List[Dict[str, object]] = []
    for path in input_files:
        row = _process_file(
            path,
            out_dir=out_dir,
            base_dir=base_dir,
            cmap=cmap,
            opencc_mode=args.opencc,
            dry_run=args.dry_run,
        )
        rows.append(row)
        status = row.get("status", "ok")
        print(f"[{status}] {path}")
        message = row.get("message")
        if message:
            print(f"    {message}")

    _write_report(rows, DEFAULT_REPORT_PATH)
    print(f"[完成] 已写入报表: {DEFAULT_REPORT_PATH}")
    if not args.dry_run:
        success_count = sum(1 for row in rows if row.get("status") == "ok")
        print(f"[完成] 写入规范化文本 {success_count} / {len(rows)} 个。")

    if any(row.get("status") == "failed" for row in rows):
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover - 命令行入口
    try:
        main()
    except KeyboardInterrupt:
        print("[错误] 用户中断，操作已取消。")
        sys.exit(1)
