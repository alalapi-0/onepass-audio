import SwiftUI

struct SectionCard<Content: View>: View {
    enum Accent {
        case neon
        case purple
        case neutral
    }

    let title: String
    var subtitle: String? = nil
    var icon: String? = nil
    var accent: Accent = .neutral
    var cornerRadius: CGFloat = 24
    @ViewBuilder var content: () -> Content

    @State private var isVisible = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            content()
        }
        .padding(20)
        .background(background)
        .cyberGlass(cornerRadius: cornerRadius)
        .overlay(borderOverlay)
        .shadow(color: shadowColor.opacity(0.45), radius: 18, x: 0, y: 20)
        .opacity(isVisible ? 1 : 0)
        .offset(y: isVisible ? 0 : 12)
        .onAppear {
            withAnimation(.spring(response: 0.6, dampingFraction: 0.85)) {
                isVisible = true
            }
        }
    }

    @ViewBuilder
    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(.system(.title3, design: .rounded).weight(.semibold))
                    .foregroundColor(CyberTheme.textPrimary)
                    .lineSpacing(2)
                if let subtitle {
                    Text(subtitle)
                        .font(.system(.subheadline, design: .rounded))
                        .foregroundColor(CyberTheme.textSecondary)
                        .lineSpacing(2)
                }
            }
            Spacer()
            if let icon {
                Image(systemName: icon)
                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                    .foregroundStyle(iconStyle)
                    .padding(10)
                    .background(
                        Circle()
                            .fill(CyberTheme.edge.opacity(0.65))
                    )
                    .overlay(
                        Circle()
                            .stroke(iconStroke, lineWidth: 1)
                    )
                    .accessibilityHidden(true)
            }
        }
    }

    private var background: some View {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            .fill(CyberTheme.panel)
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(CyberTheme.edge.opacity(0.9), lineWidth: 1)
                    .blendMode(.plusLighter)
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(innerGlow, lineWidth: 4)
                    .blur(radius: 12)
                    .opacity(0.35)
            )
    }

    private var borderOverlay: some View {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            .stroke(borderGradient, lineWidth: 1.6)
            .blendMode(.screen)
    }

    private var borderGradient: LinearGradient {
        switch accent {
        case .neon:
            return LinearGradient(colors: [CyberTheme.neonGreen, CyberTheme.purple], startPoint: .topLeading, endPoint: .bottomTrailing)
        case .purple:
            return LinearGradient(colors: [CyberTheme.purple, CyberTheme.magenta], startPoint: .topLeading, endPoint: .bottomTrailing)
        case .neutral:
            return LinearGradient(colors: [CyberTheme.edge, CyberTheme.edge.opacity(0.7)], startPoint: .topLeading, endPoint: .bottomTrailing)
        }
    }

    private var innerGlow: Color {
        switch accent {
        case .neon:
            return CyberTheme.neonGreen
        case .purple:
            return CyberTheme.purple
        case .neutral:
            return CyberTheme.edge
        }
    }

    private var shadowColor: Color {
        switch accent {
        case .neon:
            return CyberTheme.neonGreen.opacity(0.4)
        case .purple:
            return CyberTheme.purple.opacity(0.35)
        case .neutral:
            return CyberTheme.edge.opacity(0.6)
        }
    }

    private var iconStyle: LinearGradient {
        switch accent {
        case .neon:
            return LinearGradient(colors: [CyberTheme.neonGreen, CyberTheme.purple], startPoint: .topLeading, endPoint: .bottomTrailing)
        case .purple:
            return LinearGradient(colors: [CyberTheme.magenta, CyberTheme.purple], startPoint: .topLeading, endPoint: .bottomTrailing)
        case .neutral:
            return LinearGradient(colors: [CyberTheme.textPrimary, CyberTheme.textSecondary], startPoint: .topLeading, endPoint: .bottomTrailing)
        }
    }

    private var iconStroke: Color {
        switch accent {
        case .neon:
            return CyberTheme.neonGreen.opacity(0.5)
        case .purple:
            return CyberTheme.purple.opacity(0.6)
        case .neutral:
            return CyberTheme.edge.opacity(0.6)
        }
    }
}
