# QR 工具使用说明

`gen_qr.sh` 基于 [qrencode](https://fukuchi.org/works/qrencode/) 将 WireGuard 客户端
配置转换为二维码，方便在手机/桌面端导入。

## 安装依赖

- **macOS (Homebrew)**：`brew install qrencode`
- **Ubuntu/Debian**：`sudo apt install qrencode`
- **Arch Linux**：`sudo pacman -S qrencode`

安装完成后在仓库根目录执行：

```bash
bash core/qr/gen_qr.sh /path/to/client.conf
```

## 输出模式

- **ANSI（默认）**：在终端直接渲染二维码，快速扫码。适合远程 SSH 或一次性导入。
- **PNG**：追加 `--png`，脚本会在同目录生成 `client.png`。适合保存、共享或在桌面端
  预览。

## WireGuard App 导入提示

1. 在 WireGuard iOS/macOS App 中选择 “Add a tunnel” → “Create from QR code”。
2. 将屏幕对准终端/PNG 中的二维码，等待自动识别。
3. 完成后可在 App 内编辑隧道名称并确认配置内容（注意保密）。

若二维码扫描失败，确认配置文件无额外注释、终端背景对比度足够，或改用 PNG 模式
重新扫描。
