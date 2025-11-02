"""环境自检脚本。

用法示例::

    python scripts/env_check.py --out out --verbose

该脚本会检测 Python 版本、虚拟环境状态、关键外部工具以及常用目录的读写权限，
并输出人类可读摘要与 JSON 报告，帮助排查常见部署问题。
"""

from __future__ import annotations

import argparse  # 解析命令行参数
import json  # 写入 JSON 报告
import os  # 访问环境变量与权限
import platform  # 获取平台信息
import shutil  # 探测命令可执行文件
import subprocess  # 调用外部命令获取版本信息
import sys  # 获取解释器信息
from dataclasses import dataclass  # 结构化存储检查结果
from datetime import datetime  # 生成报告时间戳
from pathlib import Path  # 统一路径处理
from typing import Any, Dict, List, Optional


@dataclass
class CheckItem:
    """单项检查的摘要结果。"""

    name: str  # 检查项名称
    status: str  # 状态：ok/warn/fail
    detail: str  # 简要说明
    advice: str  # 修复建议，可为空字符串


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """解析命令行参数。"""

    # 创建解析器并描述脚本用途
    parser = argparse.ArgumentParser(description="OnePass Audio 环境自检工具")
    # 指定报告输出目录，默认 out
    parser.add_argument("--out", default="out", help="报告输出目录 (默认 out)")
    # 允许自定义 JSON 文件名
    parser.add_argument("--json", help="自定义 JSON 报告文件名 (默认 env_report.json)")
    # 控制是否打印额外调试信息
    parser.add_argument("--verbose", action="store_true", help="打印详细检查过程")
    # 是否在检测到问题后自动尝试修复
    parser.add_argument(
        "--auto-fix",
        action="store_true",
        help="检测到 warn/fail 时自动执行预设修复动作",
    )
    # 自动创建虚拟环境时使用的路径
    parser.add_argument(
        "--venv-path",
        default=".venv",
        help="自动修复虚拟环境问题时使用的 venv 目录 (默认 .venv)",
    )
    # 返回解析结果
    return parser.parse_args(argv)


def _run_command(command: List[str], verbose: bool) -> tuple[int, str]:
    """运行外部命令并返回退出码与输出。"""

    # 当 verbose 开启时，先打印命令以便复现
    if verbose:
        print(f"执行命令: {' '.join(command)}")
    try:
        # 捕获标准输出与错误输出，统一返回文本
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        # 命令启动失败时返回错误信息
        return 1, str(exc)
    # 将 stdout 与 stderr 合并，兼容 ffmpeg 只写 stderr 的行为
    output = (completed.stdout or "") + (completed.stderr or "")
    # 在 verbose 模式下打印退出码
    if verbose:
        print(f"返回码: {completed.returncode}")
    return completed.returncode, output.strip()


def _check_python() -> tuple[Dict[str, Any], List[CheckItem], List[str]]:
    """检查 Python 版本与虚拟环境状态。"""

    # 读取当前解释器版本信息
    version_info = sys.version_info
    version_str = platform.python_version()
    # 判断是否满足 3.10+ 要求
    meets_requirement = version_info >= (3, 10)
    # 判断当前是否处于虚拟环境中
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    # 构造版本检查摘要
    version_item = CheckItem(
        "Python 版本",
        "ok" if meets_requirement else "fail",
        f"当前版本 {version_str}",
        "" if meets_requirement else "请使用 Python 3.10 或更高版本。",
    )
    # 构造虚拟环境提示
    venv_item = CheckItem(
        "虚拟环境",
        "ok" if in_venv else "warn",
        "已启用 venv" if in_venv else "当前使用系统解释器",
        "" if in_venv else "建议使用 python -m venv 隔离依赖。",
    )
    # 缺少虚拟环境时追加提醒
    notes: List[str] = []
    if not in_venv:
        notes.append("未检测到虚拟环境，后续安装依赖可能影响系统 Python。")
    # 构建 JSON 片段
    payload = {
        "version": version_str,
        "ok": meets_requirement,
        "in_venv": in_venv,
    }
    # 返回 JSON 数据、摘要项列表与备注
    return payload, [version_item, venv_item], notes


def _check_tool(tool: str, verbose: bool, required: bool) -> tuple[Dict[str, Any], CheckItem, Optional[str]]:
    """检测外部工具的可用性。"""

    # 首先通过 PATH 查找可执行文件
    path = shutil.which(tool)
    if not path:
        # 未找到时根据是否关键工具设置状态
        status = "fail" if required else "warn"
        detail = "未在 PATH 中找到"
        advice = (
            f"请安装 {tool} 并确认其在 PATH 中可用。"
            if required
            else f"未安装 {tool}，相关功能将被跳过。"
        )
        note = None if required else f"{tool} 未安装，繁简转换等依赖将被跳过。"
        return {"found": False}, CheckItem(tool, status, detail, advice), note

    # 记录找到的可执行文件路径
    version_info = {"found": True, "path": path}
    # 调用 -version 获取版本字符串
    code, output = _run_command([path, "-version"], verbose)
    if code == 0 and output:
        version_info["version"] = output.splitlines()[0]
        detail = version_info["version"]
        status = "ok"
        advice = ""
        note = None
    else:
        # 版本命令失败也视为警告
        detail = "版本查询失败"
        status = "warn" if not required else "fail"
        advice = f"运行 {tool} -version 失败: {output}".strip()
        note = advice if status != "ok" else None
    return version_info, CheckItem(tool, status, detail, advice), note


def _test_writable(path: Path) -> bool:
    """通过创建临时文件判断目录是否可写。"""

    try:
        # 创建临时文件并立即删除，验证写入权限
        test_file = path / ".env_check_write_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        return True
    except OSError:
        return False


def _check_paths(out_dir: Path) -> tuple[Dict[str, Any], List[CheckItem], List[str]]:
    """检查输出与素材目录状态。"""

    # 初始化结果字典
    paths_info: Dict[str, Any] = {}
    rows: List[CheckItem] = []
    notes: List[str] = []

    # 检查 out 目录
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        detail = f"创建失败: {exc}"
        rows.append(
            CheckItem(
                "输出目录",
                "fail",
                detail,
                "请检查路径是否存在权限限制或被占用。",
            )
        )
        paths_info["out"] = {
            "path": str(out_dir),
            "exists": False,
            "writable": False,
            "error": detail,
        }
        return paths_info, rows, notes

    writable = _test_writable(out_dir)
    paths_info["out"] = {
        "path": str(out_dir),
        "exists": True,
        "readable": os.access(out_dir, os.R_OK),
        "writable": writable,
    }
    if writable:
        rows.append(CheckItem("输出目录", "ok", f"{out_dir}", ""))
    else:
        rows.append(
            CheckItem(
                "输出目录",
                "fail",
                f"{out_dir}",
                "请为该目录授予写权限或选择其他位置。",
            )
        )

    # materials 目录可选检查
    materials_dir = Path("materials").resolve()
    if materials_dir.exists():
        readable = os.access(materials_dir, os.R_OK)
        writable_materials = os.access(materials_dir, os.W_OK)
        paths_info["materials"] = {
            "path": str(materials_dir),
            "exists": True,
            "readable": bool(readable),
            "writable": bool(writable_materials),
        }
        status = "ok" if readable else "warn"
        advice = "" if readable else "请检查目录访问权限。"
        detail = "可读写" if readable and writable_materials else (
            "仅可读" if readable else "不可读"
        )
        rows.append(CheckItem("素材目录", status, detail, advice))
        if not writable_materials:
            notes.append("materials 目录不可写，部分脚本可能无法缓存中间文件。")
    else:
        paths_info["materials"] = {"path": str(materials_dir), "exists": False}
        rows.append(
            CheckItem(
                "素材目录",
                "warn",
                "未找到 materials/ 目录",
                "根据需要创建或指定素材路径。",
            )
        )

    return paths_info, rows, notes


def _check_windows_path_hints(out_dir: Path, raw_out: str, verbose: bool) -> tuple[List[CheckItem], List[str]]:
    """在 Windows 平台输出额外路径提示。"""

    rows: List[CheckItem] = []
    notes: List[str] = []

    # 检测命令行参数中是否包含制表符等控制字符，提示可能的转义问题
    if any(ch in raw_out for ch in ("\t", "\n", "\r")):
        rows.append(
            CheckItem(
                "Windows 路径",
                "warn",
                "检测到控制字符，可能因未正确转义反斜杠。",
                "请在命令行中使用双反斜杠或加引号。",
            )
        )

    # 检查路径各部分是否以空格或点结尾
    trailing_issue = any(part.endswith(" ") or part.endswith(".") for part in out_dir.parts)
    if trailing_issue:
        rows.append(
            CheckItem(
                "Windows 路径",
                "warn",
                "路径包含以空格或点结尾的片段。",
                "建议调整目录名称，避免 Windows 自动截断。",
            )
        )

    # 通过注册表查询 LongPathsEnabled 状态
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
            if value != 1:
                rows.append(
                    CheckItem(
                        "长路径支持",
                        "warn",
                        "未启用 LongPathsEnabled。",
                        "可参考微软文档启用长路径策略，避免处理超长路径时失败。",
                    )
                )
                notes.append("Windows 未启用长路径策略，处理超长路径时可能失败。")
            else:
                rows.append(CheckItem("长路径支持", "ok", "已启用 LongPathsEnabled", ""))
    except Exception as exc:  # pragma: no cover - Windows 专属分支
        if verbose:
            print(f"无法读取 LongPathsEnabled: {exc}")
        rows.append(
            CheckItem(
                "长路径支持",
                "warn",
                "无法检测 LongPathsEnabled 状态。",
                "请在本地组策略或注册表中确认已启用长路径。",
            )
        )

    return rows, notes


def _ensure_virtualenv(path: Path, verbose: bool) -> tuple[bool, str]:
    """尝试创建虚拟环境。"""

    if path.exists():
        return False, f"虚拟环境已存在: {path}"

    path.parent.mkdir(parents=True, exist_ok=True)
    code, output = _run_command([sys.executable, "-m", "venv", str(path)], verbose)
    if code == 0:
        return True, f"已创建虚拟环境: {path}"
    return False, f"创建虚拟环境失败 ({code}): {output}"


def _install_opencc(verbose: bool) -> tuple[bool, str]:
    """尝试使用 pip 安装 opencc。"""

    candidates = ["opencc", "opencc-python-reimplemented"]
    errors: List[str] = []

    for package in candidates:
        code, output = _run_command(
            [sys.executable, "-m", "pip", "install", package],
            verbose,
        )
        if code == 0 and shutil.which("opencc"):
            return True, f"已通过 pip 安装 {package}，opencc 命令已可用。"
        errors.append(f"{package}({code}): {output}")

    if shutil.which("opencc"):
        return True, "opencc 命令已存在，可能已在 PATH 中。"

    joined = "；".join(errors)
    return False, f"pip 安装 opencc 失败: {joined}"


def _enable_long_paths(verbose: bool) -> tuple[bool, str]:
    """尝试启用 Windows LongPathsEnabled 策略。"""

    if platform.system().lower() != "windows":
        return False, "当前平台非 Windows，跳过长路径设置。"

    command = [
        "reg",
        "add",
        r"HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem",
        "/v",
        "LongPathsEnabled",
        "/t",
        "REG_DWORD",
        "/d",
        "1",
        "/f",
    ]
    code, output = _run_command(command, verbose)
    if code == 0:
        return True, "已设置 LongPathsEnabled=1，重启后生效。"
    return False, f"设置 LongPathsEnabled 失败 ({code}): {output}"


def _auto_fix(
    items: List[CheckItem],
    *,
    verbose: bool,
    venv_path: Path,
) -> tuple[List[Dict[str, str]], List[str]]:
    """根据检测结果尝试自动修复已知问题。"""

    logs: List[Dict[str, str]] = []
    notes: List[str] = []

    handled = set()

    for item in items:
        if item.status not in {"warn", "fail"}:
            continue

        name = item.name
        key = name.lower()
        if key in handled:
            continue

        if name == "虚拟环境":
            success, message = _ensure_virtualenv(venv_path, verbose)
            status = "success" if success else "skipped"
            logs.append({"target": name, "status": status, "message": message})
            if success:
                notes.append("已尝试创建虚拟环境，请在下次运行前手动激活。")
            handled.add(key)
            continue

        if key == "opencc":
            success, message = _install_opencc(verbose)
            status = "success" if success else "failed"
            logs.append({"target": name, "status": status, "message": message})
            if success:
                item.status = "ok"
                item.detail = "opencc 命令已可用"
                item.advice = ""
                notes.append("opencc 已安装，可重新运行自检确认状态。")
            handled.add(key)
            continue

        if name == "长路径支持":
            success, message = _enable_long_paths(verbose)
            status = "success" if success else "failed"
            logs.append({"target": name, "status": status, "message": message})
            if success:
                item.status = "ok"
                item.detail = "已尝试启用 LongPathsEnabled"
                item.advice = ""
                notes.append("已写入长路径策略设置，Windows 可能需要重启生效。")
            handled.add(key)
            continue

    return logs, notes


def _print_summary_table(items: List[CheckItem]) -> None:
    """以表格形式打印检查摘要。"""

    if not items:
        return

    # 计算各列宽度以对齐输出
    name_width = max(len(item.name) for item in items) + 2
    status_width = max(len(item.status) for item in items) + 2

    header = f"{'项目'.ljust(name_width)}{'状态'.ljust(status_width)}详情 / 建议"
    separator = "-" * len(header)
    print(separator)
    print(header)
    print(separator)
    for item in items:
        detail = item.detail
        if item.advice:
            detail = f"{detail} ｜ 建议: {item.advice}"
        print(f"{item.name.ljust(name_width)}{item.status.ljust(status_width)}{detail}")
    print(separator)


def main(argv: Optional[List[str]] = None) -> int:
    """脚本入口，负责 orchestrate 所有检查并输出结果。"""

    # 解析命令行参数
    args = _parse_args(argv)
    out_dir = Path(args.out).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[1]
    venv_path = Path(args.venv_path).expanduser()
    if not venv_path.is_absolute():
        venv_path = (project_root / venv_path).resolve()

    # 初始化整体结果字典
    report: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    # 平台信息
    platform_info = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }
    report["platform"] = platform_info

    # 收集摘要条目与备注
    summary_items: List[CheckItem] = []
    notes: List[str] = []

    # Python 检查
    python_info, python_items, python_notes = _check_python()
    report["python"] = python_info
    summary_items.extend(python_items)
    notes.extend(python_notes)

    # 工具检查
    tools_payload: Dict[str, Any] = {}
    for tool, required in (("ffmpeg", True), ("ffprobe", True), ("opencc", False)):
        info, item, note = _check_tool(tool, args.verbose, required)
        tools_payload[tool] = info
        summary_items.append(item)
        if note:
            notes.append(note)
    report["tools"] = tools_payload

    # 路径检查
    paths_info, path_items, path_notes = _check_paths(out_dir)
    report["paths"] = paths_info
    summary_items.extend(path_items)
    notes.extend(path_notes)

    # Windows 特殊提示
    if platform_info["system"].lower() == "windows":
        win_items, win_notes = _check_windows_path_hints(out_dir, args.out, args.verbose)
        summary_items.extend(win_items)
        notes.extend(win_notes)

    auto_fix_logs: List[Dict[str, str]] = []
    if args.auto_fix:
        print("检测到 warn/fail 时将尝试自动修复...")
        auto_fix_logs, fix_notes = _auto_fix(
            summary_items,
            verbose=args.verbose,
            venv_path=venv_path,
        )
        if auto_fix_logs:
            print("自动修复结果:")
            for log in auto_fix_logs:
                print(
                    f" - [{log['status']}] {log['target']}: {log['message']}"
                )
        else:
            print("没有可自动修复的项目。")
        notes.extend(fix_notes)
    else:
        print("未启用自动修复，如需自动处理常见问题请增加 --auto-fix 参数。")

    # 计算整体状态
    has_fail = any(item.status == "fail" for item in summary_items)
    has_warn = any(item.status == "warn" for item in summary_items)
    report["summary"] = {
        "ok": not has_fail,
        "has_warning": has_warn,
        "notes": notes,
    }
    report["checks"] = [
        {
            "name": item.name,
            "status": item.status,
            "detail": item.detail,
            "advice": item.advice,
        }
        for item in summary_items
    ]
    report["auto_fix"] = auto_fix_logs

    # 打印摘要表格
    _print_summary_table(summary_items)

    if notes:
        print("提示:")
        for note in notes:
            print(f" - {note}")

    # 写入 JSON 报告
    json_name = args.json or "env_report.json"
    json_path = Path(json_name)
    if not json_path.is_absolute():
        json_path = out_dir / json_path
    json_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"写入 JSON 报告失败: {exc}", file=sys.stderr)
        return 1

    print(f"JSON 报告已写入: {json_path}")

    # 根据结果返回退出码
    if has_fail:
        print("检测到失败项，请根据上方建议修复后重试。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 脚本入口
    raise SystemExit(main())

