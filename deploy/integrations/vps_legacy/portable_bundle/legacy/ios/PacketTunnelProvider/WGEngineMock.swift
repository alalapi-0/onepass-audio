//
//  WGEngineMock.swift
//  PacketTunnelProvider
//
//  Purpose: Simulates a WireGuard userspace engine to validate start/stop flows
//  without touching the real data plane. The engine continuously drains packets
//  from the NEPacketTunnelFlow to prove the container <-> extension lifecycle.
//
import Foundation
import NetworkExtension
import Darwin

final class WGEngineMock: TunnelEngine {
    private let packetFlow: NEPacketTunnelFlow
    private let queue = DispatchQueue(label: "com.privatetunnel.mockengine", qos: .utility)

    private var healthTimer: DispatchSourceTimer?
    private var isRunning = false
    private var packetsRead: UInt64 = 0
    private var packetsWritten: UInt64 = 0
    private var lastActivity: Date = Date()

    init(packetFlow: NEPacketTunnelFlow) {
        self.packetFlow = packetFlow
    }

    func start(with configuration: WGConfig) throws {
        guard !isRunning else { return }
        isRunning = true
        packetsRead = 0
        packetsWritten = 0
        lastActivity = Date()

        Logger.logInfo("[MockEngine] Starting for profile \(configuration.profileName)")
        Logger.logInfo("[MockEngine] Effective WireGuard config:\n\(configuration.renderMinimalWireGuardConfig())")

        scheduleReadLoop()
        scheduleHealthTimer()
    }

    func stop() {
        guard isRunning else { return }
        isRunning = false
        healthTimer?.cancel()
        healthTimer = nil
        Logger.logInfo("[MockEngine] Stopped. Total read: \(packetsRead) packets, written: \(packetsWritten)")
    }

    func sendPing(completion: @escaping (Result<Void, Error>) -> Void) {
        completion(.success(()))
    }

    func stats() -> EngineStats {
        EngineStats(
            txPackets: packetsWritten,
            rxPackets: packetsRead,
            txBytes: packetsWritten,
            rxBytes: packetsRead,
            lastAliveAt: lastActivity,
            endpoint: "mock",
            heartbeatsMissed: 0
        )
    }

    private func scheduleReadLoop() {
        queue.async { [weak self] in
            guard let self, self.isRunning else { return }
            self.packetFlow.readPackets { packets, protocols in
                guard self.isRunning else { return }
                if !packets.isEmpty {
                    self.packetsRead += UInt64(packets.count)
                    self.lastActivity = Date()
                    Logger.logInfo("[MockEngine] Received \(packets.count) packets, protocols: \(protocols)")
                }
                self.scheduleReadLoop()
            }
        }
    }

    private func scheduleHealthTimer() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + .seconds(5), repeating: .seconds(5))
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            if !self.isRunning {
                return
            }
            let delta = Int(Date().timeIntervalSince(self.lastActivity))
            Logger.logInfo("[MockEngine] alive — read=\(self.packetsRead) write=\(self.packetsWritten) lastActivity=\(delta)s ago")
            if self.packetsWritten == 0 {
                // Emit a keep-alive no-op packet to exercise the write path.
                let emptyPacket = Data()
                self.packetFlow.writePackets([emptyPacket], withProtocols: [NSNumber(value: AF_INET)])
                self.packetsWritten += 1
            }
        }
        timer.resume()
        healthTimer = timer
    }
}
