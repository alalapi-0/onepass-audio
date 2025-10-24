//
//  EngineProtocol.swift
//  PacketTunnelProvider
//
//  Defines the abstraction used by PacketTunnelProvider to drive multiple tunnel
//  engines (toy UDP bridge, WireGuard, etc.). Future engines should conform to
//  this protocol so that health checking, automatic reconnect, and kill switch
//  logic can operate uniformly.
//

import Foundation

struct EngineStats: Codable {
    let txPackets: UInt64
    let rxPackets: UInt64
    let txBytes: UInt64
    let rxBytes: UInt64
    let lastAliveAt: Date?
    let endpoint: String
    let heartbeatsMissed: Int
}

protocol TunnelEngine: AnyObject {
    func start(with configuration: WGConfig) throws
    func stop()
    func sendPing(completion: @escaping (Result<Void, Error>) -> Void)
    func stats() -> EngineStats
    func setTrafficBlocked(_ blocked: Bool)
}

extension TunnelEngine {
    func setTrafficBlocked(_ blocked: Bool) {
        // Default no-op so engines that do not support software kill switch
        // semantics can simply ignore the request. PacketTunnelProvider will
        // still surface the state to the UI so the limitation is visible.
    }
}
