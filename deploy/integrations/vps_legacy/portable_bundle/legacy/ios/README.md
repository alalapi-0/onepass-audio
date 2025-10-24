# PrivateTunnel iOS Container App

## 环境要求
- macOS 14 或更新版本，安装 Xcode 15 及命令行工具。
- iOS 16 或更新版本的真机设备（用于摄像头扫码与 Keychain 调试）。
- 在 `core/config-schema.json` 生成的 JSON 配置文件（来自工具链 Round 3 输出）。

## 目录说明
```
apps/
  ios/
    PrivateTunnelApp/          # SwiftUI 容器 App
    PacketTunnelProvider/      # Round 5 预留的 Packet Tunnel 扩展目录
```

`PrivateTunnelApp` 目标包含以下关键文件：
- `PrivateTunnelApp.swift`：SwiftUI App 入口，注入共享 `ConfigManager`。
- `ContentView.swift`：主界面，展示已保存配置并提供扫码/文件导入按钮。
- `QRScannerView.swift`：AVFoundation 扫码封装，解析二维码中的 JSON。
- `FileImporter.swift`：UIDocumentPicker 封装，读取 `.json` 文件。
- `ConfigManager.swift`：负责 Keychain 存储、读取与删除。
- `KeychainHelper.swift`：Keychain 操作工具类，封装增删查逻辑。
- `Models/TunnelConfig.swift`：对应 `core/config-schema.json` 的数据结构与校验逻辑。

## 运行步骤
1. 使用 Xcode 打开仓库根目录下生成的 `PrivateTunnelApp.xcodeproj`（本轮提供源码，Xcode 打开文件夹即可自动识别 SwiftPM 布局）。
2. 在 Signing & Capabilities 中配置开发者 Team，确保 Bundle Identifier 唯一。
3. 连接真机，选择 `PrivateTunnelApp` 目标并点击 **Run**。
4. 首次运行会请求相机权限，允许后即可进入主界面。

## 使用流程
1. 点击「📷 扫码导入 JSON」扫描 Round 3 导出的二维码，或点击「📂 从文件导入」从 Files 选择 JSON 文件。
2. 导入成功后界面会显示 Profile Name、Endpoint、模式、AllowedIPs 等详情。
3. 校验通过后点击「保存配置」；配置将被序列化并写入 iOS Keychain，并在列表中显示。
4. 下次启动 App 时，`ConfigManager` 会自动从 Keychain 与 UserDefaults 读取索引，恢复所有配置。
5. 向左滑动列表项可删除对应配置。

## 调试建议
- 如需查看 Keychain 存储情况，可通过 Xcode Debug Memory Graph 或在真机使用 `keychain-access-groups` Profile。
- 若遇到校验失败，请对照 `core/config-schema.json` 检查端口范围、CIDR 格式等字段。
- 扫码过程中若提示未授权，请在 iOS 设置中为 App 开启相机权限。

## 后续计划
- Round 5 将在 `PacketTunnelProvider` 目录加入 `NETunnelProvider` 扩展，与本 App 共享配置并建立 WireGuard 隧道。
