import SwiftUI

enum CyberTheme {
    static let neonGreen = Color(.sRGB, red: 0.2235, green: 1.0, blue: 0.0784, opacity: 1.0)
    static let neonGreenDim = Color(.sRGB, red: 0.1216, green: 0.7490, blue: 0.0510, opacity: 1.0)
    static let magenta = Color(.sRGB, red: 0.7804, green: 0.1412, blue: 0.6941, opacity: 1.0)
    static let purple = Color(.sRGB, red: 0.4314, green: 0.0, blue: 1.0, opacity: 1.0)
    static let cyberBackground = Color(.sRGB, red: 0.0392, green: 0.0392, blue: 0.0706, opacity: 1.0)
    static let panel = Color(.sRGB, red: 0.0667, green: 0.0667, blue: 0.1098, opacity: 1.0)
    static let edge = Color(.sRGB, red: 0.1098, green: 0.1098, blue: 0.1647, opacity: 1.0)
    static let textPrimary = Color(.sRGB, red: 0.9020, green: 0.9059, blue: 1.0, opacity: 1.0)
    static let textSecondary = Color(.sRGB, red: 0.6588, green: 0.6627, blue: 0.7686, opacity: 1.0)
    static let warn = Color(.sRGB, red: 1.0, green: 0.3020, blue: 0.3020, opacity: 1.0)
    static let okay = Color(.sRGB, red: 0.0, green: 0.8980, blue: 1.0, opacity: 1.0)

    static let gradientPrimary = LinearGradient(
        colors: [neonGreen, purple],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    static let gradientPulse = AngularGradient(
        gradient: Gradient(colors: [neonGreen, magenta, purple, neonGreen]),
        center: .center
    )

    struct CyberGlass: ViewModifier {
        var cornerRadius: CGFloat = 20

        func body(content: Content) -> some View {
            content
                .background(
                    Color.white
                        .opacity(0.08)
                        .blur(radius: 0)
                )
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                        .stroke(Color.white.opacity(0.12), lineWidth: 1)
                        .blendMode(.screen)
                )
        }
    }
}

extension View {
    func cyberGlass(cornerRadius: CGFloat = 20) -> some View {
        modifier(CyberTheme.CyberGlass(cornerRadius: cornerRadius))
    }
}
