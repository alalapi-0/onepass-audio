# ux.py

- 最后更新时间：2025-11-02 01:32:41 +0900

## 功能概述
封装命令行交互与彩色输出的辅助函数，统一 OnePass Audio CLI 的提示风格、用户输入校验以及目录/文件选择流程。

## 关键职责
- 定义 `AnsiStyle` 数据类及一组预设样式，实现跨平台的彩色终端输出。
- 提供 `print_header`、`print_info`、`print_warning`、`print_error` 等格式化输出函数。
- 实现 `prompt_text`、`prompt_existing_file`、`prompt_existing_directory`、`prompt_choice`、`prompt_yes_no` 等输入函数，包含路径校验与错误提示。
