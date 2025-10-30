"""KeyMesh CLI 入口，使用 argparse 解析子命令并执行对应逻辑。"""

from __future__ import annotations  # 确保未来兼容性

import argparse  # 标准库 CLI 解析器
import shutil  # 复制示例配置
import sys  # 控制退出码
from pathlib import Path  # 文件路径操作

import yaml  # 读取 sample 配置以创建目录
from rich.console import Console  # 优化控制台输出
from rich.table import Table  # 用于格式化列表展示

from .config import AppConfig, ConfigError, load_config  # 导入配置加载函数
from .constants import (  # 导入常量方便维护
    DATA_ROOT,
    DEFAULT_CONFIG_FILE,
    DEFAULT_IGNORE_FILE,
    SAMPLE_CONFIG_FILE,
)

console = Console()  # 全局控制台实例，方便输出


def _print_post_init_note(note_path: Path) -> None:
    """读取 post-init 提示并打印。"""

    if note_path.exists():  # 若提示文件存在
        content = note_path.read_text(encoding="utf-8")  # 读取文本内容
        console.print("[bold green]Post-init checklist:[/bold green]\n" + content)  # 打印内容
    else:  # 若文件缺失
        console.print(f"[yellow]提示文件缺失: {note_path}[/yellow]")  # 输出警告


def _create_share_directories(sample_path: Path, base_dir: Path) -> None:
    """根据 sample 配置创建共享目录。"""

    try:
        with sample_path.open("r", encoding="utf-8") as fh:  # 打开 sample 配置
            sample_data = yaml.safe_load(fh) or {}  # 解析为字典
    except FileNotFoundError:  # 若 sample 缺失
        console.print(f"[yellow]找不到 {sample_path}，跳过共享目录创建。[/yellow]")  # 提示并返回
        return

    shares = sample_data.get("shares", [])  # 获取 share 列表
    for entry in shares:  # 遍历每个共享
        if not isinstance(entry, dict):  # 仅处理映射类型
            continue  # 跳过非法条目
        raw_path = entry.get("path")  # 读取路径
        if not raw_path:  # 缺失路径则跳过
            continue
        share_path = (base_dir / raw_path).resolve()  # 将路径解析为绝对路径
        share_path.mkdir(parents=True, exist_ok=True)  # 创建目录


def cmd_init(args: argparse.Namespace) -> int:
    """处理 init 子命令，生成初始文件与目录。"""

    base_dir = Path.cwd()  # 当前工作目录视为项目根
    config_path = base_dir / args.config  # 目标配置文件路径，允许用户自定义
    sample_path = base_dir / SAMPLE_CONFIG_FILE  # 示例配置路径

    if not sample_path.exists():  # 确保 sample 存在
        console.print(f"[red]示例配置缺失: {sample_path}[/red]")  # 输出错误
        return 1  # 返回非零退出码

    if config_path.exists():  # 若配置已存在
        console.print(f"[yellow]{config_path} 已存在，保留原文件。[/yellow]")  # 提示不覆盖
    else:  # 不存在则复制
        shutil.copyfile(sample_path, config_path)  # 复制 sample 到 config
        console.print(f"[green]已创建 {config_path}。[/green]")  # 输出成功信息

    data_root = base_dir / DATA_ROOT  # 数据目录根
    data_root.mkdir(parents=True, exist_ok=True)  # 创建数据目录
    (data_root / "peers").mkdir(parents=True, exist_ok=True)  # 创建 peers 子目录

    ignore_file = data_root / DEFAULT_IGNORE_FILE  # 忽略文件路径
    if not ignore_file.exists():  # 若忽略文件不存在
        ignore_file.write_text(
            "# KeyMesh ignore sample\n*.tmp\n*.swp\n",
            encoding="utf-8",
        )  # 写入示例内容
        console.print(f"[green]已创建 {ignore_file} 示例。[/green]")  # 输出提示
    else:  # 若文件已存在
        console.print(f"[yellow]{ignore_file} 已存在，保留原内容。[/yellow]")  # 提示保留

    keys_dir = base_dir / "keys"  # 证书输出目录
    keys_dir.mkdir(parents=True, exist_ok=True)  # 创建 keys 目录
    console.print(f"[green]确保密钥目录存在: {keys_dir}。[/green]")  # 提示用户

    _create_share_directories(sample_path, base_dir)  # 根据 sample 创建共享目录
    _print_post_init_note(base_dir / "scripts" / "post-init-note.txt")  # 打印后续提示
    console.print("[bold green]init 完成。[/bold green]")  # 总结输出
    return 0  # 返回成功


def _display_config_summary(config: AppConfig) -> None:
    """以表格形式展示配置摘要。"""

    table = Table(title="KeyMesh Configuration Summary")  # 创建表格
    table.add_column("Section")  # 添加列：段落
    table.add_column("Details")  # 添加列：详情

    table.add_row("Node", f"id={config.node.id}\nport={config.node.listen_port}")  # 节点信息
    table.add_row("Security", f"cert={config.security.cert}\nkey={config.security.key}")  # 证书摘要
    share_lines = "\n".join(f"{share.name}: {share.path}" for share in config.shares)  # 汇总共享
    table.add_row("Shares", share_lines or "(none)")  # 添加共享行
    peer_lines = "\n".join(f"{peer.id}: {peer.addr}" for peer in config.peers)  # 汇总对端
    table.add_row("Peers", peer_lines or "(none)")  # 添加对端行

    console.print(table)  # 输出表格


def cmd_check(args: argparse.Namespace) -> int:
    """处理 check 子命令，执行配置校验。"""

    try:
        config = load_config(args.config, check_files=True)  # 加载并校验证书
    except ConfigError as exc:  # 捕获配置错误
        console.print(f"[red]配置校验失败: {exc}[/red]")  # 输出错误
        return 1  # 返回非零退出码

    _display_config_summary(config)  # 展示摘要
    console.print("[bold green]配置校验通过。[/bold green]")  # 输出成功消息
    return 0  # 返回成功


def cmd_run(args: argparse.Namespace) -> int:
    """run 子命令占位，实现将在 Round 2 提供。"""

    console.print("[cyan]run 命令将在 Round 2 实现，目前为占位。[/cyan]")  # 输出提示
    return 0  # 返回成功


def cmd_add_peer(args: argparse.Namespace) -> int:
    """add-peer 子命令占位。"""

    console.print("[cyan]add-peer 将在 Round 2 实现，请手动编辑配置。[/cyan]")  # 提示占位
    return 0  # 返回成功


def cmd_list_shares(args: argparse.Namespace) -> int:
    """列出配置中的共享域。"""

    try:
        config = load_config(args.config, check_files=False)  # 加载配置但不强制证书存在
    except ConfigError as exc:  # 捕获错误
        console.print(f"[red]无法加载配置: {exc}[/red]")  # 输出错误
        return 1  # 返回非零

    for share in config.shares:  # 遍历共享列表
        console.print(f"- {share.name}: {share.path}")  # 输出共享名称与路径
    if not config.shares:  # 若列表为空
        console.print("[yellow]未找到共享定义。[/yellow]")  # 提示无共享
    return 0  # 返回成功


def build_parser() -> argparse.ArgumentParser:
    """构建顶级 argparse 解析器。"""

    parser = argparse.ArgumentParser(description="KeyMesh CLI")  # 创建解析器
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILE,
        help="指定配置文件路径 (默认: config.yaml)",
    )  # 全局配置文件参数

    subparsers = parser.add_subparsers(dest="command", required=True)  # 注册子命令

    subparsers.add_parser("init", help="初始化项目结构").set_defaults(func=cmd_init)  # init 子命令
    subparsers.add_parser("check", help="校验配置").set_defaults(func=cmd_check)  # check 子命令
    subparsers.add_parser("run", help="运行主循环").set_defaults(func=cmd_run)  # run 占位
    subparsers.add_parser("add-peer", help="添加对等节点占位").set_defaults(func=cmd_add_peer)  # add-peer
    subparsers.add_parser("list-shares", help="列出共享").set_defaults(func=cmd_list_shares)  # list-shares

    return parser  # 返回解析器供 main 使用


def main(argv: list[str] | None = None) -> int:
    """CLI 入口函数，解析参数并派发。"""

    parser = build_parser()  # 构建解析器
    args = parser.parse_args(argv)  # 解析参数
    return args.func(args)  # 调用子命令处理函数


if __name__ == "__main__":  # 允许直接运行模块
    sys.exit(main())  # 调用 main 并使用返回值作为退出码
