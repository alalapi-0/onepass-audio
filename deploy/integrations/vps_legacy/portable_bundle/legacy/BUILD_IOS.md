# iOS 构建指南

本文档介绍如何在本地构建 PrivateTunnel iOS 应用及 PacketTunnel 扩展，并总结常见编译错误的排查方法。CI 工作流仅验证能否成功编译，不会进行签名或导出。请根据自身需求在本地完成签名与打包。

## 环境要求

- **Xcode**：推荐使用 Xcode 14.3 及以上版本；
- **iOS 部署目标**：当前工程的 `IPHONEOS_DEPLOYMENT_TARGET` 设定为 iOS 16.0；
- **Swift 工具链**：使用随 Xcode 自带版本即可，不需额外安装。

## 工程结构与 Scheme

仓库提供统一的 Xcode 工程：`apps/ios/PrivateTunnelApp/PrivateTunnelApp.xcodeproj`，包含两个 Scheme：

- `PrivateTunnelApp`：容器应用，负责配置导入、健康状态展示；
- `PacketTunnelProvider`：Network Extension，处理 WireGuard 客户端逻辑。

在 Xcode 中选择 `PrivateTunnelApp` Scheme 即可构建包含扩展的完整应用。CI 中也会对扩展 Scheme 单独进行编译，确保 extension 能独立通过构建。

## 本地构建步骤

1. 打开工程：`open apps/ios/PrivateTunnelApp/PrivateTunnelApp.xcodeproj`；
2. 选择目标设备（建议使用真机）；
3. 在 `Signing & Capabilities` 中选择你的 Apple Developer 团队，并确认容器与扩展共享同一个 App Group；
4. 点击 `Product → Run`（或 `Cmd + R`）开始构建并部署到设备；
5. 如果只需要验证编译而不签名，可在 `Product → Scheme → Edit Scheme → Build` 中自定义 `CODE_SIGNING_ALLOWED=NO`，或直接在命令行执行：
   ```bash
   xcodebuild -project apps/ios/PrivateTunnelApp/PrivateTunnelApp.xcodeproj \
     -scheme PrivateTunnelApp \
     -configuration Debug \
     -sdk iphoneos \
     -destination 'generic/platform=iOS' \
     CODE_SIGNING_ALLOWED=NO clean build
   ```

## 通过脚本构建 Archive

仓库提供了 `scripts/xcenv.sh`、`scripts/ios_build.sh` 与 `scripts/ios_export.sh`：

- `./scripts/ios_build.sh`：默认执行 Release 配置并生成 `.xcarchive`；
- `./scripts/ios_build.sh --no-sign`：显式禁用签名，适用于无证书环境；
- `./scripts/ios_export.sh --method adhoc --export-options apps/ios/PrivateTunnelApp/ExportOptions_adhoc.plist`：从现有 Archive 导出 `.ipa`。

`Makefile` 也提供了快捷命令，例如 `make build`、`make export-adhoc`。执行前请先根据 [docs/CODE_SIGNING.md](CODE_SIGNING.md) 准备证书和 ExportOptions。

## 常见错误排查

| 错误提示 | 原因分析 | 解决方案 |
| --- | --- | --- |
| `CodeSign error: No such provisioning profile` | 当前机器未安装匹配的描述文件 | 打开 Xcode → Settings → Accounts，下载团队的 Provisioning Profiles，或在 Apple Developer 网站重新生成。|
| `Signing for "PacketTunnelProvider" requires a development team` | 扩展 target 未设置团队 | 在扩展 target 的 `Signing & Capabilities` 中指定同一团队，并勾选自动签名或手动选择证书。|
| `Provisioning profile "XXX" doesn't support the Network Extensions capability` | 描述文件未启用 Network Extension 权限 | 在 Apple Developer 后台重新创建包含 Network Extensions 的 App ID 和 Profile。|
| `Multiple commands produce .../Info.plist` | 旧的构建缓存导致冲突 | 在 Xcode 菜单中执行 `Product → Clean Build Folder`，或删除 `DerivedData/` 后重试。|
| `App Group mismatch between container and extension` | 容器与扩展使用不同 App Group | 在两个 target 的 `Signing & Capabilities` 中勾选相同的 App Group。|

## 进一步阅读

- [CODE_SIGNING.md](CODE_SIGNING.md)：详细说明证书、描述文件与权限配置；
- [DISTRIBUTION_TESTFLIGHT.md](DISTRIBUTION_TESTFLIGHT.md) 与 [DISTRIBUTION_ADHOC.md](DISTRIBUTION_ADHOC.md)：发布与内部分发指南；
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)：连通性、MTU、分流等运行时问题的排查步骤。
