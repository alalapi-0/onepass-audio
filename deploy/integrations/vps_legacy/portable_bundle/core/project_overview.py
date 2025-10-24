"""自动生成项目功能概览的后台脚本。"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


@dataclass
class MethodSummary:
    """表示类方法的概览信息。"""

    name: str
    doc: str


@dataclass
class DefinitionSummary:
    """表示 Python 源文件中的定义信息。"""

    name: str
    kind: str
    doc: str
    methods: Sequence[MethodSummary] | None = None


def _iter_python_files(root: Path) -> Iterable[Path]:
    """遍历 ``root`` 目录下的 Python 源文件。"""

    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        yield path


def _extract_doc(node: ast.AST) -> str:
    """提取 ``node`` 的文档字符串首行。"""

    doc = ast.get_docstring(node) or ""
    if not doc:
        return "无文档"
    first_line = doc.strip().splitlines()[0]
    return first_line or "无文档"


def _summarize_class(node: ast.ClassDef) -> DefinitionSummary:
    """收集类及其方法的概要信息。"""

    methods: list[MethodSummary] = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(MethodSummary(name=item.name, doc=_extract_doc(item)))
    return DefinitionSummary(name=node.name, kind="class", doc=_extract_doc(node), methods=methods)


def _summarize_module(path: Path) -> Sequence[DefinitionSummary]:
    """解析 ``path`` 并返回该模块中的定义信息。"""

    try:
        module_ast = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    summaries: list[DefinitionSummary] = []
    for node in module_ast.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            summaries.append(
                DefinitionSummary(name=node.name, kind="function", doc=_extract_doc(node), methods=None)
            )
        elif isinstance(node, ast.ClassDef):
            summaries.append(_summarize_class(node))
    return summaries


def generate_project_overview(project_root: Path, output_path: Path) -> Path:
    """生成项目功能概览文档。

    Parameters
    ----------
    project_root:
        需要扫描的项目根目录。
    output_path:
        文档的输出路径。
    """

    lines: list[str] = ["# 项目功能概览", "", f"自动生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    for source in _iter_python_files(project_root):
        summaries = _summarize_module(source)
        if not summaries:
            continue
        relative = source.relative_to(project_root)
        lines.append(f"## {relative}")
        for summary in summaries:
            if summary.kind == "function":
                lines.append(f"- 函数 `{summary.name}`：{summary.doc}")
            elif summary.kind == "class":
                lines.append(f"- 类 `{summary.name}`：{summary.doc}")
                for method in summary.methods or []:
                    lines.append(f"  - 方法 `{method.name}`：{method.doc}")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
