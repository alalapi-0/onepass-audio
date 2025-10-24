# 示例数据说明

`examples/` 目录提供无需音频的最小化素材，帮助在陌生环境下快速验证“去口癖 + 保留最后一遍 + 断句 + 生成 EDL + Adobe Audition 标记”链路。

## 运行示例

```bash
python scripts/retake_keep_last.py --json examples/demo.json \
  --original examples/demo.txt --outdir out --aggr 50 --dry-run
```

运行后预期在 `out/` 目录生成以下文件：

- `out/demo.keepLast.clean.srt`
- `out/demo.keepLast.clean.vtt`
- `out/demo.keepLast.clean.txt`
- `out/demo.keepLast.edl.json`
- `out/demo.keepLast.audition_markers.csv`
- `out/demo.log`

## 常见错误

- JSON 字段名不匹配：确保使用 `segments`、`words`、`word`、`start`、`end`。
- 编码问题：所有示例文件均为 UTF-8，使用其他编码会导致读取失败。
- 路径写错：命令必须在仓库根目录执行，并确保 `out/` 目录可写。
