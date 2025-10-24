//
//  WGEngineReal.swift
//  PacketTunnelProvider
//
//  Placeholder for the production WireGuard engine integration. The file keeps
//  a consistent structure so future rounds can plug in the official userspace
//  backend while reusing the health/backoff logic implemented in Round 7.
//
//  TODO:
//  - Wrap the WireGuard-Go or WireGuardKit interfaces behind TunnelEngine.
//  - Translate WGConfig into wg-quick compatible runtime configuration.
//  - Feed handshake/keepalive timestamps into `sendPing()` and `stats()`.
//

import Foundation

final class WGEngineReal: TunnelEngine {
    func start(with configuration: WGConfig) throws {
        throw NSError(domain: "PacketTunnel", code: -99, userInfo: [NSLocalizedDescriptionKey: "WireGuard engine not yet implemented"])
    }

    func stop() {}

    func sendPing(completion: @escaping (Result<Void, Error>) -> Void) {
        completion(.failure(NSError(domain: "PacketTunnel", code: -100, userInfo: [NSLocalizedDescriptionKey: "WireGuard engine not yet implemented"])))
    }

    func stats() -> EngineStats {
        EngineStats(txPackets: 0, rxPackets: 0, txBytes: 0, rxBytes: 0, lastAliveAt: nil, endpoint: "", heartbeatsMissed: 0)
    }
}
