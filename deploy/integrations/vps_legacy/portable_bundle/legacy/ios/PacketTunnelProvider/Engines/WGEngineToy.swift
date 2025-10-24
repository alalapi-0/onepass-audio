//
//  WGEngineToy.swift
//  PacketTunnelProvider
//
//  Minimal UDP tunnel engine for development-only end-to-end testing. The engine
//  wraps IPv4 packets into a custom frame and ships them over UDP to a companion
//  Python gateway that bridges into a TUN device. There is no encryption or
//  authentication and the implementation is intentionally naive — it should
//  never be used outside of controlled lab environments.
//

import Foundation
import Network
import NetworkExtension
import os.log
import Darwin

enum ToyEngineError: Error {
    case connectionUnavailable
}

private enum ToyEngineInternalError: Error {
    case notRunning
    case pingInFlight
}

private struct PingRequest {
    let identifier: UUID
    let deadline: Date
    let completion: (Result<Void, Error>) -> Void
}

final class WGEngineToy: TunnelEngine {
    private let packetFlow: NEPacketTunnelFlow
    private let queue = DispatchQueue(label: "com.privatetunnel.toyengine", qos: .userInitiated)

    private var endpointHost: String = ""
    private var endpointPort: Int = 0
    private var connection: NWConnection?
    private var isRunning = false

    private var heartbeatTimer: DispatchSourceTimer?
    private var lastPong: Date = Date()
    private var missedHeartbeats: Int = 0

    private var packetsSent: UInt64 = 0
    private var packetsReceived: UInt64 = 0
    private var bytesSent: UInt64 = 0
    private var bytesReceived: UInt64 = 0
    private var lastActivity: Date = Date()

    private var pendingPing: PingRequest?
    private var trafficBlocked = false

    init(packetFlow: NEPacketTunnelFlow) {
        self.packetFlow = packetFlow
    }

    // MARK: - TunnelEngine

    func start(with configuration: WGConfig) throws {
        queue.async { [weak self] in
            guard let self else { return }
            guard !self.isRunning else { return }
            self.endpointHost = configuration.endpoint.host
            self.endpointPort = configuration.endpoint.port
            self.isRunning = true
            self.packetsSent = 0
            self.packetsReceived = 0
            self.bytesSent = 0
            self.bytesReceived = 0
            self.lastActivity = Date()
            self.lastPong = Date()
            self.missedHeartbeats = 0
            self.pendingPing = nil
            self.trafficBlocked = false

            Logger.record(code: .engineStart, message: "Toy engine starting. Endpoint=\(self.endpointHost):\(self.endpointPort)")
            self.setupConnection()
            self.schedulePacketFlowRead()
            self.startHeartbeat()
        }
    }

    func stop() {
        queue.async { [weak self] in
            guard let self else { return }
            guard self.isRunning else { return }
            self.isRunning = false
            self.heartbeatTimer?.cancel()
            self.heartbeatTimer = nil
            self.connection?.cancel()
            self.connection = nil
            self.pendingPing = nil
            Logger.record(code: .engineStop, message: "Toy engine stopped. TX=\(self.packetsSent) RX=\(self.packetsReceived)")
        }
    }

    func sendPing(completion: @escaping (Result<Void, Error>) -> Void) {
        queue.async { [weak self] in
            guard let self else { return }
            guard self.isRunning else {
                completion(.failure(ToyEngineInternalError.notRunning))
                return
            }
            if self.pendingPing != nil {
                completion(.failure(ToyEngineInternalError.pingInFlight))
                return
            }

            let identifier = UUID()
            let request = PingRequest(identifier: identifier, deadline: Date().addingTimeInterval(5), completion: completion)
            self.pendingPing = request

            do {
                try self.sendFrame(type: .ping, payload: Data())
            } catch {
                self.pendingPing = nil
                completion(.failure(error))
                return
            }

            self.queue.asyncAfter(deadline: .now() + .seconds(5)) { [weak self] in
                guard let self else { return }
                guard let pending = self.pendingPing, pending.identifier == identifier else { return }
                self.pendingPing = nil
                Logger.record(code: .errorPingTimeout, message: "Toy engine ping timed out")
                pending.completion(.failure(ToyEngineError.connectionUnavailable))
            }
        }
    }

    func stats() -> EngineStats {
        queue.sync {
            EngineStats(
                txPackets: packetsSent,
                rxPackets: packetsReceived,
                txBytes: bytesSent,
                rxBytes: bytesReceived,
                lastAliveAt: lastActivity,
                endpoint: "\(endpointHost):\(endpointPort)",
                heartbeatsMissed: missedHeartbeats
            )
        }
    }

    func setTrafficBlocked(_ blocked: Bool) {
        queue.async { [weak self] in
            guard let self else { return }
            self.trafficBlocked = blocked
            if blocked {
                Logger.record(code: .eventKillSwitchEngaged, message: "Toy engine is dropping packets due to kill switch")
            } else {
                Logger.record(code: .eventKillSwitchReleased, message: "Toy engine resumed packet forwarding")
            }
        }
    }

    // MARK: - Internal plumbing

    private func setupConnection() {
        guard endpointPort > 0 && endpointPort < 65536 else {
            Logger.record(code: .errorEngineConfig, message: "Toy engine endpoint port out of range: \(endpointPort)")
            return
        }

        let params = NWParameters.udp
        let endpoint = NWEndpoint.Host(endpointHost)
        guard let nwPort = NWEndpoint.Port(rawValue: UInt16(endpointPort)) else {
            Logger.record(code: .errorEngineConfig, message: "Toy engine invalid endpoint port: \(endpointPort)")
            return
        }

        let connection = NWConnection(host: endpoint, port: nwPort, using: params)
        connection.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                Logger.record(code: .eventEngineReady, message: "Toy engine UDP connection ready")
                self.queue.async {
                    self.scheduleReceive()
                }
            case .failed(let error):
                Logger.record(code: .errorEngineTransport, message: "Toy engine connection failed: \(error.localizedDescription)")
                self.handleConnectionFailure()
            case .waiting(let error):
                Logger.record(code: .eventEngineWaiting, message: "Toy engine connection waiting: \(error.localizedDescription)")
            case .cancelled:
                Logger.logDebug("[ToyEngine] Connection cancelled")
            default:
                break
            }
        }

        connection.start(queue: queue)
        self.connection = connection
    }

    private func handleConnectionFailure() {
        guard isRunning else { return }
        Logger.record(code: .eventEngineReconnect, message: "Toy engine attempting reconnection in 3 seconds")
        missedHeartbeats = 0
        lastPong = Date()
        queue.asyncAfter(deadline: .now() + .seconds(3)) { [weak self] in
            guard let self else { return }
            guard self.isRunning else { return }
            self.connection?.cancel()
            self.connection = nil
            self.setupConnection()
        }
    }

    private func schedulePacketFlowRead() {
        packetFlow.readPackets { [weak self] packets, protocols in
            guard let self else { return }
            self.queue.async {
                guard self.isRunning else { return }
                if packets.isEmpty {
                    self.schedulePacketFlowRead()
                    return
                }

                for (packet, proto) in zip(packets, protocols) {
                    guard proto.intValue == AF_INET else {
                        Logger.logDebug("[ToyEngine] Dropping non-IPv4 packet (proto=\(proto))")
                        continue
                    }

                    guard !self.trafficBlocked else {
                        Logger.logDebug("[ToyEngine] Kill switch dropping outgoing packet")
                        continue
                    }

                    do {
                        try self.sendFrame(type: .dataIP, payload: packet)
                    } catch {
                        Logger.record(code: .errorEngineTransport, message: "Toy engine failed to send packet: \(error.localizedDescription)")
                    }
                }

                self.schedulePacketFlowRead()
            }
        }
    }

    private func scheduleReceive() {
        connection?.receiveMessage { [weak self] data, _, _, error in
            guard let self else { return }
            self.queue.async {
                guard self.isRunning else { return }
                if let error {
                    Logger.record(code: .errorEngineTransport, message: "Toy engine receive failed: \(error.localizedDescription)")
                    self.handleConnectionFailure()
                    return
                }

                guard let data else {
                    self.scheduleReceive()
                    return
                }

                do {
                    let frame = try TunnelProtocol.parseFrame(data)
                    try self.process(frame: frame)
                } catch {
                    Logger.record(code: .errorEngineProtocol, message: "Toy engine failed to parse frame: \(error.localizedDescription)")
                }

                self.scheduleReceive()
            }
        }
    }

    private func sendFrame(type: TunnelFrameType, payload: Data) throws {
        guard let connection else {
            throw ToyEngineError.connectionUnavailable
        }

        let frame = try TunnelProtocol.encodeFrame(type: type, payload: payload)
        connection.send(content: frame, completion: .contentProcessed { error in
            if let error {
                Logger.record(code: .errorEngineTransport, message: "Toy engine UDP send failed: \(error.localizedDescription)")
            }
        })

        if type == .dataIP {
            packetsSent += 1
            bytesSent += UInt64(payload.count)
        }
        lastActivity = Date()
    }

    private func process(frame: TunnelFrame) throws {
        switch frame.type {
        case .dataIP:
            guard !frame.payload.isEmpty else {
                return
            }
            packetsReceived += 1
            bytesReceived += UInt64(frame.payload.count)
            lastActivity = Date()
            guard !trafficBlocked else {
                Logger.logDebug("[ToyEngine] Kill switch dropping incoming packet")
                return
            }
            packetFlow.writePackets([frame.payload], withProtocols: [NSNumber(value: AF_INET)])
        case .ping:
            Logger.logDebug("[ToyEngine] Received ping, replying with pong")
            lastActivity = Date()
            try sendFrame(type: .pong, payload: Data())
        case .pong:
            Logger.logDebug("[ToyEngine] Received pong")
            missedHeartbeats = 0
            lastPong = Date()
            lastActivity = Date()
            if let request = pendingPing {
                pendingPing = nil
                request.completion(.success(()))
            }
        }
    }

    private func startHeartbeat() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + .seconds(10), repeating: .seconds(10))
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            guard self.isRunning else { return }

            do {
                try self.sendFrame(type: .ping, payload: Data())
            } catch {
                Logger.record(code: .errorEngineTransport, message: "Toy engine heartbeat send failed: \(error.localizedDescription)")
            }

            self.missedHeartbeats += 1
            if self.missedHeartbeats >= 3 {
                Logger.record(code: .eventEngineReconnect, message: "Toy engine missed \(self.missedHeartbeats) heartbeats, forcing reconnect")
                self.missedHeartbeats = 0
                self.lastPong = Date()
                self.connection?.cancel()
                self.connection = nil
                self.setupConnection()
            }
        }
        timer.resume()
        heartbeatTimer = timer
    }
}
