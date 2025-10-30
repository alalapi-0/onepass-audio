"""提供统一的日志初始化函数，支持控制台与可选文件输出。"""

from __future__ import annotations  # 确保未来兼容性

import logging  # 标准库 logging 模块
from logging import Logger  # 导入类型别名便于注解
from pathlib import Path  # 用于处理文件路径
from typing import Optional  # 允许可选的日志文件参数

from rich.logging import RichHandler  # 丰富的控制台日志输出处理器


def init_logging(level: str, logfile: Optional[str] = None) -> Logger:
    """初始化日志系统并返回根 logger。"""
    level_upper = level.upper()  # 将级别转换为大写以兼容 logging
    logging_level = getattr(logging, level_upper, logging.INFO)  # 若无法解析则回退到 INFO

    logging.basicConfig(  # 配置基础日志设置
        level=logging_level,  # 设置日志级别
        format="%(message)s",  # RichHandler 会负责格式化
        handlers=[RichHandler(rich_tracebacks=True, markup=True)]  # 使用 Rich 提供的处理器
    )

    logger = logging.getLogger("keymesh")  # 获取项目专用 logger

    if logfile:  # 当提供日志文件路径时
        log_path = Path(logfile).expanduser().resolve()  # 解析并归一化日志文件路径
        log_path.parent.mkdir(parents=True, exist_ok=True)  # 确保日志目录存在
        file_handler = logging.FileHandler(log_path, encoding="utf-8")  # 创建文件处理器
        file_handler.setLevel(logging_level)  # 文件处理器沿用同级别
        file_handler.setFormatter(  # 使用包含时间戳与级别的格式
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)  # 将文件处理器附加到 logger

    logger.debug("Logging initialized at level %s", level_upper)  # 输出调试信息确认初始化
    return logger  # 返回配置好的 logger 供调用方使用
