"""路径归一化与越权检测工具函数，带有逐行注释与示例。"""

from __future__ import annotations  # 未来兼容性

from pathlib import Path  # 使用 pathlib 处理路径


def normalize_path(base: Path | str, target: Path | str) -> Path:
    """将目标路径相对于 base 进行归一化并返回绝对路径。

    >>> normalize_path("/tmp", "../tmp/data")
    PosixPath('/tmp/data')
    """

    base_path = Path(base).expanduser().resolve()  # 将 base 解析为绝对路径
    target_path = Path(target)  # 将目标转换为 Path 对象
    combined = (base_path / target_path).resolve()  # 合并后解析为绝对路径
    return combined  # 返回归一化结果


def ensure_within(base: Path | str, target: Path | str) -> Path:
    """确保 target 落在 base 目录内，若越界则抛出 ValueError。"""

    normalized = normalize_path(base, target)  # 先归一化目标路径
    base_path = Path(base).expanduser().resolve()  # 解析 base 的绝对路径

    try:
        normalized.relative_to(base_path)  # 若成功则说明在 base 内部
    except ValueError as exc:  # 捕获相对化失败异常
        raise ValueError(f"路径 {normalized} 越过共享根 {base_path}") from exc  # 重新抛出带解释的异常

    return normalized  # 返回安全的归一化路径


__all__ = ["normalize_path", "ensure_within"]  # 导出函数列表供其他模块引用
