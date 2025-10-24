# PacketTunnelProvider

Round 5 引入了 iOS Packet Tunnel Extension 的最小可运行骨架。该目录包含：

- `PacketTunnelProvider.swift`：Network Extension 入口，负责解析配置、应用 `NEPacketTunnelNetworkSettings` 并启动 Mock Engine。
- `WGConfig.swift` / `WGConfigParser.swift`：定义 WireGuard 客户端配置模型与 JSON 解析逻辑。
- `WGEngineMock.swift`：模拟 WireGuard 引擎的读写循环，仅用于打通生命周期流程。
- `Logger.swift`：封装 `os_log` 便于统一日志格式。
- `Info.plist` / `PacketTunnelProvider.entitlements`：扩展的基础元数据与权限声明。

该实现不会传递真实加解密流量，而是通过日志模拟数据面。后续迭代将替换为真正的 WireGuard userspace 引擎。
