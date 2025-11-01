"""按 EDL 渲染干净音频的命令行脚本。

示例:
    python scripts/edl_render.py \
      --edl materials/demo/demo.keepLast.edl.json \
      --audio-root materials \
      --out out \
      --samplerate 48000 \
      --channels 1
"""
from __future__ import annotations

import argparse  # 解析命令行参数
import sys  # 控制退出码
from pathlib import Path  # 统一路径处理

from onepass.edl_renderer import (  # 导入核心渲染逻辑
    load_edl,
    normalize_segments,
    probe_duration,
    render_audio,
    resolve_source_audio,
)
from onepass.logging_utils import default_log_dir, setup_logger  # 引入统一日志工具


def _parse_args() -> argparse.Namespace:
    """定义并解析脚本参数。"""

    parser = argparse.ArgumentParser(description="按 EDL 渲染干净音频")  # 创建解析器
    parser.add_argument("--edl", required=True, help="EDL JSON 文件路径")  # 必填：EDL 文件
    parser.add_argument("--audio-root", required=True, help="源音频搜索根目录")  # 必填：音频搜索目录
    parser.add_argument("--out", required=True, help="输出目录")  # 必填：输出目录
    parser.add_argument("--samplerate", type=int, default=None, help="目标采样率 (Hz)")  # 可选采样率
    parser.add_argument("--channels", type=int, default=None, help="目标声道数")  # 可选声道
    parser.add_argument("--dry-run", action="store_true", help="仅打印命令，不实际执行")  # Dry-Run 开关
    return parser.parse_args()  # 返回解析结果


def main() -> None:
    """脚本主入口，读取 EDL 并执行渲染。"""

    args = _parse_args()  # 解析参数
    logger = setup_logger(__name__, default_log_dir())  # 初始化日志器

    edl_path = Path(args.edl).expanduser().resolve()  # 规范化 EDL 路径
    audio_root = Path(args.audio_root).expanduser().resolve()  # 规范化音频目录
    out_dir = Path(args.out).expanduser().resolve()  # 规范化输出目录

    logger.info("启动 EDL 渲染流程", extra={"edl": str(edl_path), "audio_root": str(audio_root), "out": str(out_dir)})

    if args.samplerate is not None and args.samplerate <= 0:  # 校验采样率
        logger.error("收到非法采样率参数: %s", args.samplerate)
        print("错误: --samplerate 需为正整数。", file=sys.stderr)
        sys.exit(1)
    if args.channels is not None and args.channels <= 0:  # 校验声道数
        logger.error("收到非法声道数参数: %s", args.channels)
        print("错误: --channels 需为正整数。", file=sys.stderr)
        sys.exit(1)

    try:
        edl = load_edl(edl_path)  # 读取 EDL
        source_audio = resolve_source_audio(edl, edl_path, audio_root)  # 定位源音频
        duration = probe_duration(source_audio)  # 获取音频时长
        keeps = normalize_segments(edl.segments, duration)  # 归一化保留片段
    except Exception as exc:
        logger.exception("解析 EDL 或源音频失败")
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    keep_duration = sum(segment.end - segment.start for segment in keeps)  # 统计保留时长
    out_path = out_dir / f"{source_audio.stem}.clean.wav"  # 预期输出路径

    logger.info(
        "完成片段归一化，准备渲染: 片段=%s 总时长=%.3fs 输出=%s",
        len(keeps),
        keep_duration,
        out_path,
    )

    print(f"源音频: {source_audio}")  # 打印解析结果
    print(f"输出文件: {out_path}")
    print(f"片段数量: {len(keeps)}，累计保留时长: {keep_duration:.3f}s")

    override_samplerate = args.samplerate  # 记录用户是否显式设置采样率
    override_channels = args.channels  # 记录用户是否显式设置声道数
    samplerate = override_samplerate or edl.samplerate  # 计算实际使用的采样率
    channels = override_channels or edl.channels  # 计算实际使用的声道数

    if override_samplerate is not None:
        print(f"目标采样率: {samplerate} Hz（命令行指定）")
    elif samplerate is not None:
        print(f"目标采样率: {samplerate} Hz（来自 EDL 建议）")
    if override_channels is not None:
        print(f"目标声道数: {channels}（命令行指定）")
    elif channels is not None:
        print(f"目标声道数: {channels}（来自 EDL 建议）")

    if args.dry_run:
        try:
            render_audio(  # Dry-Run 模式仅输出命令
                edl_path,
                audio_root,
                out_dir,
                override_samplerate,
                override_channels,
                dry_run=True,
            )
            logger.info("Dry-run 已输出渲染命令")
        except Exception as exc:
            logger.exception("Dry-run 渲染命令生成失败")
            print(f"错误: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    try:
        output = render_audio(  # 实际渲染音频
            edl_path,
            audio_root,
            out_dir,
            override_samplerate,
            override_channels,
            dry_run=False,
        )
    except Exception as exc:
        logger.exception("渲染音频失败")
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    logger.info(
        "渲染完成: 输出=%s 片段数=%s 保留时长=%.3fs",
        output,
        len(keeps),
        keep_duration,
    )
    print(f"已完成渲染: {output}")  # 输出结果
    print(f"累计保留时长 {keep_duration:.3f}s，片段 {len(keeps)} 段。")


if __name__ == "__main__":  # pragma: no cover
    main()
