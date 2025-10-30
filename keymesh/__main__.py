"""`python -m keymesh` 的入口模块，负责初始化日志并调用 CLI。"""

from __future__ import annotations  # 未来兼容性

import sys  # 允许设置进程退出码

from . import __version__  # 导入版本号便于日志输出
from .constants import DEFAULT_LOG_FILE, DEFAULT_LOG_LEVEL  # 默认日志配置
from .logging_setup import init_logging  # 日志初始化函数
from .cli import main as cli_main  # CLI 主入口


def main() -> int:
    """初始化日志后执行 CLI 逻辑。"""

    logger = init_logging(DEFAULT_LOG_LEVEL, DEFAULT_LOG_FILE)  # 初始化日志系统
    logger.info("Starting KeyMesh %s", __version__)  # 记录启动信息
    return cli_main()  # 调用 CLI 并返回退出码


if __name__ == "__main__":  # 当模块被直接执行时
    sys.exit(main())  # 使用 main 的返回值作为退出码
