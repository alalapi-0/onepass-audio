# KeyMesh 示例拓扑（Round 1）

本目录用于存放典型配置示例。本轮仅提供最小三节点拓扑示意，后续轮次将补充运行截图与同步流程。

## 拓扑简介
- 节点 A：集中调度与共享维护者，监听 51888 端口。
- 节点 B、节点 C：通过既有 VPN/内网穿透与 A 互联，分别拥有独立的私有共享域。

## 示例配置片段
```yaml
peers:
  - id: "host-B"
    addr: "10.8.0.12:51888"
    cert_fingerprint: "sha256:ABCD..."
    shares_access:
      - share: "common"
        mode: "rw"
      - share: "to-B"
        mode: "rw"
  - id: "host-C"
    addr: "10.8.0.13:51888"
    cert_fingerprint: "sha256:EFGH..."
    shares_access:
      - share: "common"
        mode: "ro"
      - share: "to-C"
        mode: "rw"
```

## 当前限制
- Round 1 仅提供配置示例与校验；尚未实现握手、同步或传输逻辑。
- 请根据自身网络环境填写实际可达地址，并手动同步证书指纹。

后续轮次会在此基础上补充运行命令、调试技巧与复杂拓扑模板。
