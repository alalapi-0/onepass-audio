"""OnePass Audio 统一日志工具。

该模块集中提供日志目录与初始化函数，确保所有脚本都写入统一的滚动日志，
并可选同步输出到控制台，便于排查问题。"""

from __future__ import annotations

import logging  # 标准日志模块
from datetime import datetime  # 生成日期目录与文件名
from logging.handlers import RotatingFileHandler  # 实现滚动日志文件
from pathlib import Path  # 统一路径处理


def default_log_dir() -> Path:
    """返回项目的默认日志目录。"""

    # 日志统一放置在 out/logs/ 下，便于集中管理与打包
    return Path("out") / "logs"


def setup_logger(name: str, log_dir: Path, level: int = logging.INFO, to_console: bool = True) -> logging.Logger:
    """初始化指定名称的日志器并启用滚动文件。"""

    # 获取或创建同名日志器，后续在其上挂载 Handler
    logger = logging.getLogger(name)
    # 若日志器或其父级已存在 Handler，说明已经按规范初始化，直接复用
    if logger.hasHandlers():
        logger.setLevel(level)  # 保持与调用方期望的日志级别一致
        logger.propagate = False  # 禁止向父级传播，避免重复输出
        return logger

    # 将日志根目录解析为绝对路径，自动创建缺失的目录
    log_root = Path(log_dir).expanduser().resolve()
    # 使用当前日期创建子目录，形如 out/logs/2025-01-02/
    current = datetime.now()
    day_dir = log_root / current.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    # 组合日志文件名 onepass-YYYYMMDD.log，确保每日独立文件
    filename = f"onepass-{current.strftime('%Y%m%d')}.log"
    log_file = day_dir / filename

    # 设置日志级别，确保后续 Handler 使用相同的过滤阈值
    logger.setLevel(level)

    # 统一的日志格式：本地时间、级别、日志器名称与消息体
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 创建滚动文件处理器，单文件 2MB，最多保留 5 个备份
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    # 文件处理器应用统一格式，便于 grep 与人工阅读
    file_handler.setFormatter(formatter)
    # 将文件处理器注册到日志器上
    logger.addHandler(file_handler)

    if to_console:
        # 同步输出到控制台，便于实时观察进度
        console_handler = logging.StreamHandler()
        # 控制台输出使用与文件相同的格式，方便定位
        console_handler.setFormatter(formatter)
        # 控制台处理器同样挂载到日志器
        logger.addHandler(console_handler)

    # 停止向父级传播，避免重复打印或被基础配置截走
    logger.propagate = False

    return logger

