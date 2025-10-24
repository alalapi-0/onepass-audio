# Windows 客户端骨架（占位说明）

> 这是占位实现：此目录将用于存放 Windows 桌面客户端或服务端组件。

## 计划内容

- WPF/WinUI 前端，提供连接控制、日志查看、分流策略配置。
- 后台服务负责调用 WireGuard 驱动与同步配置。
- 自动更新模块（可考虑 Squirrel.Windows 或自研方案）。

## TODO

- 初始化 Visual Studio 解决方案与项目结构。
- 调研 WireGuard 官方 API/CLI 在 Windows 下的调用方式。
- 确定配置存储方案（注册表/本地文件/加密存储）。
