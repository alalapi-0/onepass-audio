//
//  Backoff.swift
//  PacketTunnelProvider
//
//  Implements an exponential backoff strategy with jitter that is used when the
//  tunnel engine is considered unhealthy. The policy is intentionally simple so
//  it can be shared by the toy UDP engine and the future WireGuard data plane.
//

import Foundation

struct BackoffPolicy {
    private(set) var attempt: Int = 0
    private(set) var lastDelay: TimeInterval = 0

    let initialDelay: TimeInterval
    let multiplier: Double
    let maximumDelay: TimeInterval
    let jitterPercentage: ClosedRange<Double>

    init(initialDelay: TimeInterval = 2,
         multiplier: Double = 2,
         maximumDelay: TimeInterval = 60,
         jitterPercentage: ClosedRange<Double> = 0...0.2) {
        self.initialDelay = initialDelay
        self.multiplier = multiplier
        self.maximumDelay = maximumDelay
        self.jitterPercentage = jitterPercentage
    }

    mutating func nextDelay() -> TimeInterval {
        attempt += 1
        let raw = initialDelay * pow(multiplier, Double(max(0, attempt - 1)))
        let clamped = min(maximumDelay, raw)
        let jitterRatio = Double.random(in: jitterPercentage)
        let jitter = clamped * jitterRatio
        lastDelay = clamped + jitter
        return lastDelay
    }

    mutating func reset() {
        attempt = 0
        lastDelay = 0
    }
}
