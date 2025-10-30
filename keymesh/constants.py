"""集中维护 KeyMesh 的默认常量，方便后续轮次引用与修改。"""

# 默认监听端口，基于 WireGuard/ZeroTier 常用范围但不冲突。
DEFAULT_LISTEN_PORT = 51888

# 默认 chunk 大小（以 MB 为单位），用于传输配置兜底。
DEFAULT_CHUNK_MB = 16

# 默认并发文件数量，脚手架阶段仅作为配置提示。
DEFAULT_CONCURRENT_FILES = 2

# 默认每个文件的并发 chunk 数量。
DEFAULT_CONCURRENT_CHUNKS_PER_FILE = 2

# 默认速率限制（0 表示不限速）。
DEFAULT_RATE_LIMIT_MBPS = 0

# 默认日志级别。
DEFAULT_LOG_LEVEL = "info"

# 默认日志文件路径。
DEFAULT_LOG_FILE = "logs/keymesh.log"

# 默认配置文件名，供 CLI 引导。
DEFAULT_CONFIG_FILE = "config.yaml"

# 默认示例配置文件名。
SAMPLE_CONFIG_FILE = "config.sample.yaml"

# 默认共享目录的忽略文件名。
DEFAULT_IGNORE_FILE = ".keymeshignore"

# 初始化后创建的基础数据目录。
DATA_ROOT = "data"
