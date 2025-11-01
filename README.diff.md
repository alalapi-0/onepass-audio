# README.diff

- 2025-11-01T14:59:34Z | 分支: main
  - 改动文件：/miniprogram/core/*、/miniprogram/pages/index/*、/miniprogram/README.md、根 README.md
  - 问题现象：实现小程序端渲染与交互，接入关卡 HUD
  - 解决方案：使用 2D canvas 绘制、二分法拟合字号、滑动手势识别
  - 验证方式：微信开发者工具运行首页，操作与通关
  - 后续工作：第 5 轮参数化与持久化扩展（统一配置、bestScore 与总分策略等）
