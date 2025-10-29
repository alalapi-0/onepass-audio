"""
项目: OnePass Audio
用途: 初始化包命名空间，并预留后续 loader/retake/clean/segment/edl/writers/markers/aggr/pipeline 模块的聚合入口。
依赖: 仅使用 Python 标准库。
示例用法:
    from onepass import __version__, planned_modules
    print(__version__)
    print(", ".join(planned_modules))
"""

from __future__ import annotations

__all__ = ["__version__", "planned_modules"]

__version__: str = "0.0.0"
"""当前预发行版本号，占位用。"""

planned_modules: tuple[str, ...] = (
    "loader",
    "retake",
    "clean",
    "segment",
    "edl",
    "writers",
    "markers",
    "aggr",
    "pipeline",
)
"""预期将在后续迭代中补充的子模块名称。"""
