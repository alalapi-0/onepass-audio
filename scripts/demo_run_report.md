# demo_run.sh

- 最后更新时间：2025-11-02 02:08:18 +0900

## 功能概述
用于快速演示 OnePass Audio 流程的 Bash 包装脚本，直接调用 `scripts/smoke_test.py` 并传递所有参数，方便一键体验。

## 关键职责
- 设置 `set -e` 确保遇到错误立即退出。
- 将命令行参数原样转发给 smoke test，避免重复维护。
