"""配置加载与校验模块，负责解析 config.yaml 并提供结构化数据。"""

from __future__ import annotations  # 兼容未来类型注解

from dataclasses import dataclass  # 使用 dataclass 表示配置结构
from pathlib import Path  # 统一路径处理
from typing import Any, Dict, List, Optional  # 类型注解辅助

import yaml  # 解析 YAML 配置

from .constants import (  # 导入常量方便使用默认值
    DATA_ROOT,
    DEFAULT_CHUNK_MB,
    DEFAULT_CONCURRENT_CHUNKS_PER_FILE,
    DEFAULT_CONCURRENT_FILES,
    DEFAULT_CONFIG_FILE,
    DEFAULT_LOG_FILE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_RATE_LIMIT_MBPS,
)
from .utils.pathing import ensure_within, normalize_path  # 路径归一化与越权检测


@dataclass
class NodeConfig:
    """描述本节点的监听信息。"""

    id: str  # 节点唯一 ID
    listen_port: int  # 监听端口
    bind_host: Optional[str] = None  # 可选绑定地址


@dataclass
class SecurityConfig:
    """描述证书与密钥相关配置。"""

    ca_cert: Path  # CA 证书路径
    cert: Path  # 节点证书路径
    key: Path  # 节点私钥路径
    fingerprint_whitelist: List[str]  # 允许的证书指纹列表


@dataclass
class ShareConfig:
    """描述共享目录定义。"""

    name: str  # 共享名称
    path: Path  # 共享路径
    delete_propagation: bool  # 是否传播删除
    ignore_file: Optional[str] = None  # 可选忽略文件


@dataclass
class PeerAccess:
    """描述对等节点对共享域的访问权限。"""

    share: str  # 关联的共享名称
    mode: str  # 访问模式 ro/rw


@dataclass
class PeerConfig:
    """描述单个对等节点配置。"""

    id: str  # 对端 ID
    addr: str  # 可达地址
    cert_fingerprint: str  # 证书指纹
    shares_access: List[PeerAccess]  # 可访问的共享列表


@dataclass
class TransferConfig:
    """描述传输相关参数。"""

    chunk_mb: int  # 每块大小（MB）
    concurrent_files: int  # 并发文件数
    concurrent_chunks_per_file: int  # 单文件并发块数
    rate_limit_mbps: int  # 速率限制


@dataclass
class LoggingConfig:
    """描述日志配置。"""

    level: str  # 日志级别
    file: Optional[Path]  # 日志文件路径


@dataclass
class AppConfig:
    """聚合所有配置段，供后续轮次使用。"""

    node: NodeConfig  # 节点配置
    security: SecurityConfig  # 安全配置
    peers: List[PeerConfig]  # 对端列表
    shares: List[ShareConfig]  # 共享列表
    transfer: TransferConfig  # 传输配置
    logging: LoggingConfig  # 日志配置
    config_path: Path  # 配置文件所在路径


class ConfigError(Exception):
    """配置解析相关的自定义异常。"""


def _load_yaml(path: Path) -> Dict[str, Any]:
    """内部函数：加载 YAML 并返回字典。"""

    try:
        with path.open("r", encoding="utf-8") as fh:  # 打开配置文件
            data = yaml.safe_load(fh) or {}  # 使用 safe_load 解析，空文件回退空 dict
    except FileNotFoundError as exc:  # 捕获文件不存在异常
        raise ConfigError(f"找不到配置文件: {path}") from exc  # 包装为 ConfigError
    except yaml.YAMLError as exc:  # 捕获 YAML 语法错误
        raise ConfigError(f"解析 YAML 失败: {exc}") from exc  # 转换异常类型

    if not isinstance(data, dict):  # 确保根节点是字典
        raise ConfigError("配置文件顶层必须是映射类型")  # 给出明确错误
    return data  # 返回解析后的字典


def _parse_node(data: Dict[str, Any]) -> NodeConfig:
    """解析 node 段落。"""

    node_data = data.get("node")  # 获取 node 配置
    if not isinstance(node_data, dict):  # 验证类型
        raise ConfigError("缺少 node 配置段或类型错误")  # 提示问题

    try:
        node_id = str(node_data["id"])  # 读取节点 ID
        listen_port = int(node_data.get("listen_port", 0))  # 读取端口
    except KeyError as exc:  # 捕获缺失字段
        raise ConfigError(f"node 段缺少字段: {exc}") from exc  # 提示具体字段

    bind_host = node_data.get("bind_host")  # 读取可选绑定地址
    return NodeConfig(id=node_id, listen_port=listen_port, bind_host=bind_host)  # 构建 dataclass


def _parse_security(data: Dict[str, Any], base_dir: Path) -> SecurityConfig:
    """解析 security 段落并归一化路径。"""

    sec_data = data.get("security")  # 获取 security 数据
    if not isinstance(sec_data, dict):  # 验证类型
        raise ConfigError("缺少 security 配置段或类型错误")  # 报错

    raw_ca = sec_data.get("ca_cert")  # 读取 CA 路径原值
    raw_cert = sec_data.get("cert")  # 读取证书路径原值
    raw_key = sec_data.get("key")  # 读取私钥路径原值
    if not all([raw_ca, raw_cert, raw_key]):  # 确保三者均存在
        raise ConfigError("security 段必须提供 ca_cert/cert/key 路径")  # 报错提醒

    ca_cert = normalize_path(base_dir, raw_ca)  # 归一化 CA 证书路径
    cert = normalize_path(base_dir, raw_cert)  # 归一化节点证书
    key = normalize_path(base_dir, raw_key)  # 归一化私钥
    whitelist = sec_data.get("fingerprint_whitelist", [])  # 获取指纹列表
    if not isinstance(whitelist, list):  # 验证指纹类型
        raise ConfigError("fingerprint_whitelist 必须是列表")  # 抛出错误
    whitelist_str = [str(item) for item in whitelist]  # 统一为字符串

    return SecurityConfig(  # 返回 dataclass
        ca_cert=ca_cert,
        cert=cert,
        key=key,
        fingerprint_whitelist=whitelist_str,
    )


def _parse_shares(data: Dict[str, Any], base_dir: Path) -> List[ShareConfig]:
    """解析 shares 列表并验证路径。"""

    shares_data = data.get("shares", [])  # 获取 shares 数据
    if not isinstance(shares_data, list):  # 校验类型
        raise ConfigError("shares 必须是列表")  # 报错

    shares: List[ShareConfig] = []  # 准备结果列表
    for entry in shares_data:  # 遍历每个共享
        if not isinstance(entry, dict):  # 确保类型正确
            raise ConfigError("share 项必须是映射")  # 报错
        name = str(entry.get("name"))  # 读取共享名
        if not name:  # 检查名称存在
            raise ConfigError("share 项缺少 name")  # 抛错
        raw_path = entry.get("path", "")  # 获取路径字符串
        normalized_path = normalize_path(base_dir, raw_path)  # 归一化路径
        normalized_path = ensure_within(base_dir / DATA_ROOT, normalized_path)  # 确认路径位于 data 根目录内
        delete_propagation = bool(entry.get("delete_propagation", False))  # 布尔化删除策略
        ignore_file = entry.get("ignore_file")  # 可选忽略文件
        shares.append(  # 添加到列表
            ShareConfig(
                name=name,
                path=normalized_path,
                delete_propagation=delete_propagation,
                ignore_file=str(ignore_file) if ignore_file else None,
            )
        )

    return shares  # 返回共享列表


def _parse_peers(data: Dict[str, Any], known_shares: List[ShareConfig]) -> List[PeerConfig]:
    """解析 peers 列表并确保引用的 share 存在。"""

    peers_data = data.get("peers", [])  # 获取 peers 数据
    if not isinstance(peers_data, list):  # 校验类型
        raise ConfigError("peers 必须是列表")  # 报错

    share_names = {share.name for share in known_shares}  # 构建共享名集合
    peers: List[PeerConfig] = []  # 结果列表
    for entry in peers_data:  # 遍历对端
        if not isinstance(entry, dict):  # 确保条目类型正确
            raise ConfigError("peer 项必须是映射")  # 抛错
        peer_id = str(entry.get("id"))  # 读取对端 ID
        if not peer_id:  # 检查 ID
            raise ConfigError("peer 项缺少 id")  # 报错
        addr = str(entry.get("addr"))  # 读取地址
        if not addr:  # 检查地址
            raise ConfigError(f"peer {peer_id} 缺少 addr")  # 报错
        fingerprint = str(entry.get("cert_fingerprint"))  # 读取指纹
        if not fingerprint:  # 检查指纹
            raise ConfigError(f"peer {peer_id} 缺少 cert_fingerprint")  # 抛错

        access_list = entry.get("shares_access", [])  # 获取访问列表
        if not isinstance(access_list, list):  # 校验类型
            raise ConfigError(f"peer {peer_id} 的 shares_access 必须是列表")  # 报错

        parsed_access: List[PeerAccess] = []  # 准备访问列表
        for access in access_list:  # 遍历访问配置
            if not isinstance(access, dict):  # 确保类型
                raise ConfigError(f"peer {peer_id} 的 shares_access 项必须是映射")  # 抛错
            share_name = str(access.get("share"))  # 读取共享名
            if share_name not in share_names:  # 校验共享存在
                raise ConfigError(f"peer {peer_id} 引用了不存在的 share: {share_name}")  # 报错
            mode = str(access.get("mode", ""))  # 读取访问模式
            if mode not in {"ro", "rw"}:  # 验证模式
                raise ConfigError(f"peer {peer_id} 的 share {share_name} 使用非法 mode: {mode}")  # 抛错
            parsed_access.append(PeerAccess(share=share_name, mode=mode))  # 添加到列表

        peers.append(  # 构建 PeerConfig
            PeerConfig(
                id=peer_id,
                addr=addr,
                cert_fingerprint=fingerprint,
                shares_access=parsed_access,
            )
        )

    return peers  # 返回对端配置列表


def _parse_transfer(data: Dict[str, Any]) -> TransferConfig:
    """解析 transfer 段落并提供默认值。"""

    transfer_data = data.get("transfer", {})  # 获取 transfer 数据
    if not isinstance(transfer_data, dict):  # 校验类型
        raise ConfigError("transfer 必须是映射")  # 报错

    chunk_mb = int(transfer_data.get("chunk_mb", DEFAULT_CHUNK_MB))  # 块大小
    concurrent_files = int(transfer_data.get("concurrent_files", DEFAULT_CONCURRENT_FILES))  # 并发文件数
    concurrent_chunks_per_file = int(
        transfer_data.get("concurrent_chunks_per_file", DEFAULT_CONCURRENT_CHUNKS_PER_FILE)
    )  # 单文件并发块数
    rate_limit_mbps = int(transfer_data.get("rate_limit_mbps", DEFAULT_RATE_LIMIT_MBPS))  # 速率限制

    return TransferConfig(  # 返回 dataclass
        chunk_mb=chunk_mb,
        concurrent_files=concurrent_files,
        concurrent_chunks_per_file=concurrent_chunks_per_file,
        rate_limit_mbps=rate_limit_mbps,
    )


def _parse_logging(data: Dict[str, Any], base_dir: Path) -> LoggingConfig:
    """解析 logging 段落并归一化文件路径。"""

    logging_data = data.get("logging", {})  # 获取 logging 数据
    if not isinstance(logging_data, dict):  # 校验类型
        raise ConfigError("logging 必须是映射")  # 报错

    level = str(logging_data.get("level", DEFAULT_LOG_LEVEL))  # 日志级别
    file_value = logging_data.get("file", DEFAULT_LOG_FILE)  # 文件路径
    file_path = normalize_path(base_dir, file_value) if file_value else None  # 条件归一化

    return LoggingConfig(level=level, file=file_path)  # 返回 dataclass


def load_config(path: str | Path = DEFAULT_CONFIG_FILE, check_files: bool = False) -> AppConfig:
    """加载并校验配置；当 check_files 为 True 时确保证书文件存在。"""

    config_path = Path(path).expanduser().resolve()  # 解析配置文件路径
    data = _load_yaml(config_path)  # 加载 YAML 数据
    base_dir = config_path.parent  # 获取配置文件所在目录

    node = _parse_node(data)  # 解析 node 段
    security = _parse_security(data, base_dir)  # 解析 security 段
    shares = _parse_shares(data, base_dir)  # 解析 shares 段
    peers = _parse_peers(data, shares)  # 解析 peers 列表
    transfer = _parse_transfer(data)  # 解析 transfer 段
    logging_cfg = _parse_logging(data, base_dir)  # 解析 logging 段

    if check_files:  # 当启用文件检查时
        for path_item in (security.ca_cert, security.cert, security.key):  # 遍历证书路径
            if not path_item.exists():  # 检查文件是否存在
                raise ConfigError(f"证书或密钥不存在: {path_item}")  # 报错提示
        for share in shares:  # 检查共享路径
            share.path.mkdir(parents=True, exist_ok=True)  # 确保目录存在

    return AppConfig(  # 返回聚合配置
        node=node,
        security=security,
        peers=peers,
        shares=shares,
        transfer=transfer,
        logging=logging_cfg,
        config_path=config_path,
    )


__all__ = [  # 导出公开 API
    "AppConfig",
    "ConfigError",
    "load_config",
]
