import SwiftUI

struct CyberButton<Label: View>: View {
    enum Style {
        case primary
        case secondary
    }

    let style: Style
    var isLoading: Bool = false
    var animateLightSweep: Bool = false
    var action: () -> Void
    @ViewBuilder var label: () -> Label

    @State private var sweepOffset: CGFloat = -0.9
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button {
            guard !isLoading else { return }
            action()
        } label: {
            HStack(spacing: 10) {
                if isLoading {
                    CyberButtonSpinner(color: style == .primary ? CyberTheme.neonGreen : CyberTheme.purple)
                        .accessibilityHidden(true)
                }
                label()
                    .font(.system(.headline, design: .rounded).weight(.semibold))
                    .foregroundColor(foregroundColor)
                    .lineSpacing(2)
            }
            .padding(.vertical, 16)
            .padding(.horizontal, 18)
            .frame(maxWidth: .infinity)
            .contentShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        }
        .buttonStyle(CyberButtonStyle(style: style))
        .overlay(lightSweepOverlay)
        .onChange(of: animateLightSweep) { value in
            guard value else { return }
            runLightSweep()
        }
        .onAppear {
            if animateLightSweep {
                runLightSweep()
            }
        }
        .disabled(isLoading)
        .accessibilityAddTraits(isLoading ? .updatesFrequently : [])
    }

    private var foregroundColor: Color {
        if !isEnabled {
            return CyberTheme.textSecondary.opacity(0.6)
        }
        switch style {
        case .primary:
            return CyberTheme.textPrimary
        case .secondary:
            return CyberTheme.textPrimary.opacity(0.9)
        }
    }

    private func runLightSweep() {
        sweepOffset = -0.9
        withAnimation(.easeInOut(duration: 0.8)) {
            sweepOffset = 1.4
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.85) {
            sweepOffset = -0.9
        }
    }

    @ViewBuilder
    private var lightSweepOverlay: some View {
        GeometryReader { proxy in
            let width = proxy.size.width
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(Color.clear)
                .overlay(
                    Group {
                        if style == .primary {
                            LinearGradient(
                                colors: [Color.white.opacity(0.0), Color.white.opacity(0.85), Color.white.opacity(0.0)],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                            .frame(width: width * 0.65)
                            .offset(x: sweepOffset * width)
                            .mask(
                                RoundedRectangle(cornerRadius: 18, style: .continuous)
                                    .stroke(lineWidth: 3)
                            )
                            .blendMode(.screen)
                            .opacity(animateLightSweep ? 1 : 0)
                        }
                    }
                )
        }
        .allowsHitTesting(false)
        .clipped()
    }
}

private struct CyberButtonStyle: ButtonStyle {
    let style: CyberButton.Style

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(maxWidth: .infinity)
            .background(background(isPressed: configuration.isPressed))
            .scaleEffect(configuration.isPressed ? 0.97 : 1.0)
            .animation(.spring(response: 0.28, dampingFraction: 0.7, blendDuration: 0.25), value: configuration.isPressed)
    }

    @ViewBuilder
    private func background(isPressed: Bool) -> some View {
        let cornerRadius: CGFloat = 18
        switch style {
        case .primary:
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .fill(CyberTheme.panel.opacity(isPressed ? 0.85 : 0.95))
                .overlay(
                    RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                        .stroke(CyberTheme.neonGreen, lineWidth: 2.2)
                        .shadow(color: CyberTheme.neonGreen.opacity(isPressed ? 0.35 : 0.65), radius: isPressed ? 10 : 16)
                        .blur(radius: 0)
                )
                .shadow(color: CyberTheme.neonGreen.opacity(0.35), radius: isPressed ? 10 : 18, x: 0, y: 12)
        case .secondary:
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .fill(CyberTheme.panel.opacity(isPressed ? 0.92 : 0.98))
                .overlay(
                    RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                        .stroke(CyberTheme.purple.opacity(isPressed ? 0.9 : 1.0), lineWidth: 1.4)
                        .shadow(color: CyberTheme.purple.opacity(0.35), radius: 12)
                        .blur(radius: 0)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                        .fill(Color.white.opacity(0.08))
                )
        }
    }
}

private struct CyberButtonSpinner: View {
    var color: Color
    @State private var isAnimating = false

    var body: some View {
        Circle()
            .trim(from: 0.0, to: 0.7)
            .stroke(color, style: StrokeStyle(lineWidth: 3, lineCap: .round))
            .frame(width: 18, height: 18)
            .rotationEffect(.degrees(isAnimating ? 360 : 0))
            .animation(.linear(duration: 0.9).repeatForever(autoreverses: false), value: isAnimating)
            .onAppear { isAnimating = true }
    }
}
