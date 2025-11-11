# 手工验收要点

1. **中文紧连**：重新生成 `001序言01.norm.txt` 与 `002序言02_天真的信息观.norm.txt` 后，确认 `观我们` 等中文词之间没有被插入空格，且 `.align.txt` 内不存在制表符或换行。
2. **无音频也产出**：将示例 `*.m4a` 暂时移走，执行 `[1] 一键流水线`，仍能生成 `*.keepLast.srt`、`*.keepLast.txt`、`*.keepLast.edl.json` 与 `*.keepLast.audition_markers.csv`。
3. **识别词级 JSON**：CLI 日志需要展示命中 `001序言01.json`、`002序言02_天真的信息观.json`，即便文件名未使用 `.words.json` 后缀。
4. **识别 m4a 音频**：素材扫描时需列出 `*.m4a` 音频文件，并在存在音频时完成干净音频渲染。
5. **日志友好**：流水线开始时打印参数快照及解析后的 `glob_words`/`glob_audio`，并输出 `stem | text | words | audio` 汇总表；当素材套件为 0 或缺少词级 JSON 时，给出显式警告并正常退出。
