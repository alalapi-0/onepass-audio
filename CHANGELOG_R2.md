# R2 对齐提速与稳态保障更新

## 新增参数
- `--fast-match/--no-fast-match`
- `--max-windows`
- `--match-timeout`
- `--max-distance-ratio`
- `--min-anchor-ngram`
- `--fallback-policy`

以上参数已在 `retake-keep-last` 与 `all-in-one` 子命令中暴露，并记录在 `batch_report.json` 的 `params_snapshot` 字段。

## 核心算法调整
- 引入 `_fast_candidates` 与 `_bounded_levenshtein_banded`，结合候选预筛与带宽受限 DP，实现快速模糊匹配与时间预算控制。
- `_search_fuzzy_window` 改为仅在候选窗口上执行迭代加宽的编辑距离计算，支持早停与超时抛出。
- `compute_retake_keep_last` 加入匹配超时检测、回退策略（`safe`/`align-greedy`/`keep-all`）、过裁剪二次评估，确保任何情况下都能生成非空 KEEP 段。
- 对齐统计扩展：新增 `match_engine`、`timed_out`、`unmatched_examples`、`latency_ms`、`cut_seconds` 等指标。

## 写出保障
- 新增 `onepass.edl_writer.write_edl` 与 `onepass.markers_writer.write_audition_csv`，统一 EDL/CSV 产出并在无匹配时生成兜底段。
- 渲染失败时仍保证 EDL/CSV 存在，可供网页端或后续流程使用。

## 验证建议
1. 执行：
   ```bash
   python scripts/onepass_cli.py all-in-one \
     --in "E:\onepass-audio\materials" \
     --out "E:\onepass-audio\out" \
     --emit-align --collapse-lines \
     --glob-text "*.txt" --glob-words "*.words.json;*.json" \
     --glob-audio "*.wav;*.m4a;*.mp3;*.flac" \
     --render auto --no-interaction \
     --fast-match true --max-windows 50 \
     --match-timeout 20 --max-distance-ratio 0.25 \
     --min-anchor-ngram 8 --fallback-policy safe
   ```
2. 检查输出目录内每个 stem 的 `.keepLast.edl.json` 与 `.keepLast.audition_markers.csv` 均非空。
3. 查看日志中是否出现候选统计、早停提示、fallback 通知以及 `retake done elapsed=...`。
4. 确认 `batch_report.json` 包含新的统计字段。

## 日志示例
```
[fuzzy] line=12 dist=2 score=0.875 candidates=5
[unmatched] line=27 text=信息官 | words=信息观
触发兜底策略(no-match) -> fallback-keep-all
retake done elapsed=3.24s
```
