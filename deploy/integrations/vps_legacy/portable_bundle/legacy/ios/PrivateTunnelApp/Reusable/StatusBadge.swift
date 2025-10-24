import SwiftUI

enum TunnelStatus: Equatable {
    case connected
    case connecting
    case disconnected
}

struct StatusBadge: View {
    let status: TunnelStatus

    @State private var animateBreath = false

    var body: some View {
        HStack(spacing: 10) {
            indicator
            Text(text)
                .font(.system(.caption, design: .rounded).weight(.semibold))
                .foregroundColor(CyberTheme.textPrimary)
                .textCase(.uppercase)
                .lineSpacing(1.5)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(
            Capsule(style: .continuous)
                .fill(backgroundColor.opacity(0.4))
                .overlay(
                    Capsule(style: .continuous)
                        .stroke(borderColor.opacity(0.9), lineWidth: 1)
                )
        )
        .shadow(color: glowColor.opacity(0.35), radius: 8, x: 0, y: 4)
        .onAppear {
            if status == .connecting {
                animateBreath = true
            }
        }
        .onChange(of: status) { newValue in
            if newValue == .connecting {
                animateBreath = true
            } else {
                animateBreath = false
            }
        }
        .animation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true), value: animateBreath)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(voiceOverLabel)
    }

    private var voiceOverLabel: Text {
        switch status {
        case .connected:
            return Text("VPN 已连接")
        case .connecting:
            return Text("VPN 正在连接")
        case .disconnected:
            return Text("VPN 已断开")
        }
    }

    @ViewBuilder
    private var indicator: some View {
        switch status {
        case .connected:
            Circle()
                .fill(CyberTheme.neonGreen)
                .frame(width: 10, height: 10)
                .shadow(color: CyberTheme.neonGreen.opacity(0.75), radius: 10)
        case .connecting:
            ZStack {
                Circle()
                    .strokeBorder(CyberTheme.purple.opacity(0.4), lineWidth: 2)
                    .frame(width: 14, height: 14)
                Circle()
                    .fill(CyberTheme.magenta)
                    .frame(width: 10, height: 10)
                    .scaleEffect(animateBreath ? 1.25 : 0.8)
                    .opacity(animateBreath ? 0.4 : 0.9)
            }
        case .disconnected:
            Circle()
                .strokeBorder(CyberTheme.edge, lineWidth: 1.5)
                .background(Circle().fill(CyberTheme.edge.opacity(0.4)))
                .frame(width: 10, height: 10)
        }
    }

    private var text: String {
        switch status {
        case .connected: return "Connected"
        case .connecting: return "Connecting…"
        case .disconnected: return "Disconnected"
        }
    }

    private var backgroundColor: Color {
        switch status {
        case .connected:
            return CyberTheme.neonGreen.opacity(0.15)
        case .connecting:
            return CyberTheme.purple.opacity(0.3)
        case .disconnected:
            return CyberTheme.edge
        }
    }

    private var borderColor: Color {
        switch status {
        case .connected:
            return CyberTheme.neonGreen.opacity(0.7)
        case .connecting:
            return CyberTheme.purple
        case .disconnected:
            return CyberTheme.edge.opacity(0.8)
        }
    }

    private var glowColor: Color {
        switch status {
        case .connected:
            return CyberTheme.neonGreen
        case .connecting:
            return CyberTheme.purple
        case .disconnected:
            return CyberTheme.edge
        }
    }
}
