"""onepass.logging_utils
=========================

提供统一的日志初始化方法，确保所有脚本输出一致格式并同时写入文件与控制台。
"""

from __future__ import annotations

import logging  # 标准日志模块
from datetime import datetime  # 用于生成按日分目录与文件名
from logging.handlers import RotatingFileHandler  # 负责滚动日志文件
from pathlib import Path  # 统一路径对象


def default_log_dir() -> Path:
    """返回默认的日志目录路径。"""

    # 固定将日志放在项目 out/logs/ 目录下，便于集中管理
    return Path("out") / "logs"


def setup_logger(name: str, log_dir: Path, level: int = logging.INFO, to_console: bool = True) -> logging.Logger:
    """构建带有文件轮转能力的日志器。

    Args:
        name: 日志器名称。
        log_dir: 日志根目录，将在其中创建按日分目录的文件。
        level: 日志级别，默认 INFO。
        to_console: 是否同步输出到标准输出。

    Returns:
        已完成配置的 ``logging.Logger`` 实例。
    """

    # 获取或创建指定名称的日志器
    logger = logging.getLogger(name)
    # 如果已经按 OnePass 规范配置过则直接返回，避免重复添加 Handler
    if getattr(logger, "_onepass_configured", False):
        return logger

    # 确保日志目录存在，使用 resolve() 避免重复路径
    log_root = Path(log_dir).expanduser().resolve()
    # 默认日志目录下按日期划分子目录，提升可读性
    day_dir = log_root / datetime.now().strftime("%Y-%m-%d")
    # 若目录不存在则递归创建
    day_dir.mkdir(parents=True, exist_ok=True)

    # 日志文件名包含日期，便于快速定位，同时使用 yyyymmdd 保持连续性
    filename = f"onepass-{datetime.now().strftime('%Y%m%d')}.log"
    log_file = day_dir / filename

    # 统一设置日志级别，确保文件与控制台等级一致
    logger.setLevel(level)

    # 构建日志格式器，包含时间、级别、名称与消息
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 创建滚动文件处理器，单文件 2MB，最多保留 5 个轮转
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    # 将统一格式应用于文件处理器
    file_handler.setFormatter(formatter)
    # 将文件处理器附加到日志器
    logger.addHandler(file_handler)

    if to_console:
        # 控制台处理器用于实时反馈信息
        console_handler = logging.StreamHandler()
        # 控制台输出也使用相同的格式，方便检索关键字
        console_handler.setFormatter(formatter)
        # 附加控制台处理器
        logger.addHandler(console_handler)

    # 禁止向父级传播，避免重复输出
    logger.propagate = False
    # 标记该日志器已经按规范初始化
    setattr(logger, "_onepass_configured", True)

    return logger

