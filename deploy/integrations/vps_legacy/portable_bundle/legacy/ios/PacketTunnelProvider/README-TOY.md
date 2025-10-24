# Toy UDP/TUN 引擎（仅限开发自测）

> ⚠️ **极度不安全：无加密、无鉴权。仅允许在私人环境短时联调。**
>
> - 只在真机开发调试时使用，用于验证从 iOS Packet Tunnel 到自建 VPS 的链路。
> - 一旦测试完成，请立即断开 VPN、停止服务器端 `toy_tun_gateway.py` 并执行 `teardown_tun.sh` 清理规则。
> - 任何生产用途都必须替换为真正的 WireGuard 数据面。

## 功能概览

`WGEngineToy` 会从 `NEPacketTunnelFlow` 读取系统发出的 IPv4 包，封装为自定义的 Toy Frame 后通过 UDP 发送给 VPS。
服务器端的 [toy_tun_gateway.py](../../../server/toy-gateway/toy_tun_gateway.py) 将帧解包后写入 `/dev/net/tun`，配合 `setup_tun.sh` 配置的路由/NAT，公网流量会穿过 VPS 再返回 iOS 设备。

特性与限制：

- ✅ 支持 PING/PONG 心跳，每 10 秒发送一次，用于维持 NAT 会话。
- ✅ 简单的收发统计，可通过容器 App 的 `handleAppMessage` 查询。
- ⚠️ **仅支持 IPv4**，大包和高吞吐场景不保证可靠性。
- ⚠️ 无拥塞控制、无重传、无加密与鉴权。
- ⚠️ 依赖系统 UDP/路由 MTU，请保持 iOS 与服务器侧 MTU 在 1280–1380 区间。

## 使用说明

1. 在容器 App 中解析配置时，将 `engine` 字段设置为 `"toy"`（示例见 `core/examples/minimal.json` 的扩展）。
2. 在 `WGConfig.routing.mode` 为 `global` 时，扩展会调用 `WGEngineToy`；其它模式暂时退回 `WGEngineMock`。
3. 部署 VPS 侧桥接组件：
   ```bash
   cd server/toy-gateway
   sudo bash setup_tun.sh           # 创建 TUN、启用 NAT
   python3 toy_tun_gateway.py --verbose
   ```
4. 在 iOS 真机发起连接，系统会显示 VPN 图标，可通过 Safari 访问 http://1.1.1.1 验证链路。
5. 完成测试后执行：
   ```bash
   sudo bash teardown_tun.sh
   ```

## 调试建议

- `toy_tun_gateway.py --verbose` 会打印收发帧信息与心跳日志。
- 如果 UDP 被防火墙阻断，可在 `.env` 中将端口改为 443/853 等常见端口。
- 使用 `tcpdump -i <WAN_IF>` 和 `tcpdump -i toy0` 观察流量是否正确转发。

再次提醒：**切勿在公网长时间开启 toy 通道**。下一轮迭代会用真实的 WireGuard 引擎替换数据面。
