import SwiftUI

struct ParticlesView: View {
    struct Particle: Identifiable {
        let id = UUID()
        var x: CGFloat
        var y: CGFloat
        var size: CGFloat
        var blur: CGFloat
        var speed: Double
        var hue: Double
    }

    @State private var particles: [Particle] = ParticlesView.generateParticles()
    @State private var phase: Bool = false

    var body: some View {
        GeometryReader { _ in
            TimelineView(.animation) { _ in
                Canvas { context, size in
                    for particle in particles {
                        var resolved = context.resolve(
                            Circle()
                                .strokeBorder(Color(hue: particle.hue, saturation: 0.8, brightness: 1.0, opacity: 0.65), lineWidth: 1.4)
                        )
                        let offsetY = particle.y + (phase ? 1 : -1) * CGFloat(particle.speed)
                        let rect = CGRect(
                            x: particle.x * size.width,
                            y: offsetY.truncatingRemainder(dividingBy: size.height),
                            width: particle.size,
                            height: particle.size
                        )
                        resolved.shading = .color(Color(hue: particle.hue, saturation: 0.9, brightness: 1.0, opacity: 0.7))
                        context.addFilter(.blur(radius: particle.blur))
                        context.opacity = 0.7
                        context.draw(resolved, in: rect)
                    }
                }
            }
            .onAppear {
                withAnimation(.easeInOut(duration: 6).repeatForever(autoreverses: true)) {
                    phase.toggle()
                }
            }
        }
        .allowsHitTesting(false)
    }

    private static func generateParticles() -> [Particle] {
        (0..<48).map { index in
            Particle(
                x: CGFloat.random(in: 0...1),
                y: CGFloat.random(in: 0...1) * 400,
                size: CGFloat.random(in: 6...18),
                blur: CGFloat.random(in: 2...10),
                speed: Double.random(in: 4...9),
                hue: index.isMultiple(of: 3) ? 0.77 : 0.32
            )
        }
    }
}
