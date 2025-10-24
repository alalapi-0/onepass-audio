import Combine
import Foundation
import NetworkExtension
import os.log

/// High level error domain used by the WireGuard engine facade.
public enum WGEngineError: Error, LocalizedError {
    case missingConfiguration
    case invalidConfiguration(String)
    case adapterUnavailable
    case adapterFailure(WGAdapterError)
    case missingDependency

    public var errorDescription: String? {
        switch self {
        case .missingConfiguration:
            return "Missing WireGuard configuration"
        case .invalidConfiguration(let message):
            return "Invalid WireGuard configuration: \(message)"
        case .adapterUnavailable:
            return "WireGuard adapter unavailable"
        case .adapterFailure(let error):
            return error.errorDescription
        case .missingDependency:
            return "WireGuardKit is not linked into the target"
        }
    }
}

/// Public statistics reported by the engine.
public struct WGEngineStatistics: Equatable {
    public var bytesIn: UInt64
    public var bytesOut: UInt64
    public var lastHandshakeTime: Date?

    public init(bytesIn: UInt64 = 0, bytesOut: UInt64 = 0, lastHandshakeTime: Date? = nil) {
        self.bytesIn = bytesIn
        self.bytesOut = bytesOut
        self.lastHandshakeTime = lastHandshakeTime
    }
}

/// Lightweight adapter error wrapper that normalises WireGuardKit specific errors.
public enum WGAdapterError: Error {
    case cannotLocateTunnelFileDescriptor
    case invalidState
    case dnsResolutionFailure
    case setNetworkSettings(Error)
    case startWireGuardBackend(Int32)
    case unknown

    var errorDescription: String {
        switch self {
        case .cannotLocateTunnelFileDescriptor:
            return "Failed to locate WireGuard tunnel file descriptor"
        case .invalidState:
            return "WireGuard adapter is in an invalid state"
        case .dnsResolutionFailure:
            return "WireGuard adapter failed to resolve the endpoint host"
        case .setNetworkSettings(let error):
            return "Failed to apply tunnel network settings: \(error.localizedDescription)"
        case .startWireGuardBackend(let code):
            return "WireGuard backend failed to start (error code \(code))"
        case .unknown:
            return "Unknown WireGuard adapter error"
        }
    }
}

#if canImport(WireGuardKit)
import WireGuardKit
public typealias WGTunnelConfiguration = TunnelConfiguration
public typealias WGInterfaceConfiguration = InterfaceConfiguration
public typealias WGPeerConfiguration = PeerConfiguration
#else
public struct WGInterfaceConfiguration {
    public var addresses: [String]
    public var dns: [String]
    public var dnsSearch: [String]
    public var mtu: UInt16?

    public init(addresses: [String] = [], dns: [String] = [], dnsSearch: [String] = [], mtu: UInt16? = nil) {
        self.addresses = addresses
        self.dns = dns
        self.dnsSearch = dnsSearch
        self.mtu = mtu
    }
}

public struct WGPeerConfiguration: Hashable {
    public var publicKey: String
    public var allowedIPs: [String]
    public var endpoint: String?
    public var persistentKeepAlive: UInt16?
    public var rxBytes: UInt64?
    public var txBytes: UInt64?
    public var lastHandshakeTime: Date?

    public init(publicKey: String,
                allowedIPs: [String] = [],
                endpoint: String? = nil,
                persistentKeepAlive: UInt16? = nil,
                rxBytes: UInt64? = nil,
                txBytes: UInt64? = nil,
                lastHandshakeTime: Date? = nil) {
        self.publicKey = publicKey
        self.allowedIPs = allowedIPs
        self.endpoint = endpoint
        self.persistentKeepAlive = persistentKeepAlive
        self.rxBytes = rxBytes
        self.txBytes = txBytes
        self.lastHandshakeTime = lastHandshakeTime
    }
}

public struct WGTunnelConfiguration {
    public var name: String?
    public var interface: WGInterfaceConfiguration
    public var peers: [WGPeerConfiguration]

    public init(name: String? = nil, interface: WGInterfaceConfiguration, peers: [WGPeerConfiguration]) {
        self.name = name
        self.interface = interface
        self.peers = peers
    }
}
#endif

/// Minimal protocol abstraction to allow mocking the adapter in tests while also
/// using the concrete WireGuardKit implementation at runtime.
public protocol WireGuardAdapterControlling: AnyObject {
    func start(tunnelConfiguration: WGTunnelConfiguration, completionHandler: @escaping (WGAdapterError?) -> Void)
    func stop(completionHandler: @escaping (WGAdapterError?) -> Void)
    func update(tunnelConfiguration: WGTunnelConfiguration, completionHandler: @escaping (WGAdapterError?) -> Void)
    func getRuntimeConfiguration(completionHandler: @escaping (String?) -> Void)
}

#if canImport(WireGuardKit)
extension WireGuardAdapter: WireGuardAdapterControlling {
    public func start(tunnelConfiguration: TunnelConfiguration, completionHandler: @escaping (WGAdapterError?) -> Void) {
        start(tunnelConfiguration: tunnelConfiguration) { completionHandler($0.map(WGAdapterError.init)) }
    }

    public func stop(completionHandler: @escaping (WGAdapterError?) -> Void) {
        stop { completionHandler($0.map(WGAdapterError.init)) }
    }

    public func update(tunnelConfiguration: TunnelConfiguration, completionHandler: @escaping (WGAdapterError?) -> Void) {
        update(tunnelConfiguration: tunnelConfiguration) { completionHandler($0.map(WGAdapterError.init)) }
    }
}

private extension WGAdapterError {
    init(_ error: WireGuardAdapterError) {
        switch error {
        case .cannotLocateTunnelFileDescriptor:
            self = .cannotLocateTunnelFileDescriptor
        case .invalidState:
            self = .invalidState
        case .dnsResolution:
            self = .dnsResolutionFailure
        case .setNetworkSettings(let systemError):
            self = .setNetworkSettings(systemError)
        case .startWireGuardBackend(let code):
            self = .startWireGuardBackend(code)
        }
    }
}
#endif

/// WireGuard engine that exposes a Combine friendly surface for the host app to
/// control the underlying WireGuardKit adapter.
public final class WGEngineReal {
    public enum Status: Equatable {
        case disconnected
        case connecting
        case connected
        case reasserting
    }

    private let log = Logger(subsystem: "com.alalapi.privatetunnel", category: "WGEngine")
    private let queue = DispatchQueue(label: "com.alalapi.privatetunnel.engine")
    private var adapter: WireGuardAdapterControlling?
    private var lastConfiguration: WGTunnelConfiguration?

    private let statusSubject = CurrentValueSubject<Status, Never>(.disconnected)
    private let statsSubject = CurrentValueSubject<WGEngineStatistics, Never>(.init())

    public init() {}

    /// Attach an adapter implementation. PacketTunnelProvider is responsible for
    /// creating the `WireGuardAdapter` and handing it to the engine.
    public func attachAdapter(_ adapter: WireGuardAdapterControlling) {
        self.adapter = adapter
    }

    /// Parses a wg-quick configuration string into the canonical configuration
    /// wrapper used by both the engine and PacketTunnelProvider.
    public func parseConfiguration(configText: String) throws -> WireGuardQuickConfig {
        guard !configText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw WGEngineError.missingConfiguration
        }
        return try WireGuardQuickConfig(text: configText)
    }

    /// Convenience API that parses and starts the adapter in one go.
    public func start(configText: String, completion: @escaping (Error?) -> Void) {
        do {
            let parsed = try parseConfiguration(configText: configText)
            start(with: parsed, completion: completion)
        } catch {
            completion(error)
        }
    }

    /// Starts the WireGuard adapter using the provided configuration wrapper.
    public func start(with parsedConfiguration: WireGuardQuickConfig,
                      completion: @escaping (Error?) -> Void) {
        guard let adapter else {
            completion(WGEngineError.adapterUnavailable)
            return
        }
        lastConfiguration = parsedConfiguration.nativeConfiguration
        statusSubject.send(.connecting)
        queue.async {
            adapter.start(tunnelConfiguration: parsedConfiguration.nativeConfiguration) { [weak self] error in
                guard let self else { return }
                self.queue.async {
                    if let error {
                        self.log.error("Failed to start WireGuard adapter: \(error.errorDescription)")
                        self.statusSubject.send(.disconnected)
                        DispatchQueue.main.async { completion(WGEngineError.adapterFailure(error)) }
                    } else {
                        self.log.info("WireGuard adapter started")
                        self.statusSubject.send(.connected)
                        self.refreshRuntimeStatistics()
                        DispatchQueue.main.async { completion(nil) }
                    }
                }
            }
        }
    }

    /// Stops the currently running adapter if any.
    public func stop(completion: @escaping () -> Void) {
        guard let adapter else {
            statusSubject.send(.disconnected)
            DispatchQueue.main.async { completion() }
            return
        }
        queue.async {
            adapter.stop { [weak self] _ in
                guard let self else { return }
                self.statusSubject.send(.disconnected)
                self.statsSubject.send(WGEngineStatistics())
                DispatchQueue.main.async { completion() }
            }
        }
    }

    /// Reconfigures the adapter with a new wg-quick configuration while keeping
    /// the tunnel up. The adapter must already be running.
    public func reconfigure(configText: String, completion: @escaping (Error?) -> Void) {
        do {
            let parsed = try parseConfiguration(configText: configText)
            reconfigure(with: parsed, completion: completion)
        } catch {
            completion(error)
        }
    }

    /// Reconfigures using a pre-parsed configuration wrapper.
    public func reconfigure(with parsedConfiguration: WireGuardQuickConfig,
                            completion: @escaping (Error?) -> Void) {
        guard let adapter else {
            completion(WGEngineError.adapterUnavailable)
            return
        }
        lastConfiguration = parsedConfiguration.nativeConfiguration
        queue.async {
            adapter.update(tunnelConfiguration: parsedConfiguration.nativeConfiguration) { [weak self] error in
                guard let self else { return }
                self.queue.async {
                    if let error {
                        self.log.error("Reconfiguration failed: \(error.errorDescription)")
                        DispatchQueue.main.async { completion(WGEngineError.adapterFailure(error)) }
                    } else {
                        self.log.info("WireGuard adapter reconfigured")
                        self.refreshRuntimeStatistics()
                        DispatchQueue.main.async { completion(nil) }
                    }
                }
            }
        }
    }

    /// Publishes status updates.
    public var statusPublisher: AnyPublisher<Status, Never> {
        statusSubject.eraseToAnyPublisher()
    }

    /// Returns the latest cached status.
    public var currentStatus: Status {
        statusSubject.value
    }

    /// Returns the latest cached statistics snapshot.
    public func stats() -> WGEngineStatistics {
        statsSubject.value
    }

    /// Requests the adapter to expose the runtime configuration and refreshes
    /// statistics accordingly.
    public func refreshRuntimeStatistics() {
        guard let adapter else { return }
        adapter.getRuntimeConfiguration { [weak self] configurationText in
            guard let self else { return }
            guard let configurationText else { return }
            let stats = WGEngineReal.parseStatistics(fromRuntimeConfiguration: configurationText)
            self.statsSubject.send(stats)
        }
    }

    /// Maps the system VPN status notification into an engine specific status.
    public func updateConnectionStatus(_ status: NEVPNStatus) {
        switch status {
        case .connected:
            statusSubject.send(.connected)
        case .connecting:
            statusSubject.send(.connecting)
        case .reasserting:
            statusSubject.send(.reasserting)
        default:
            statusSubject.send(.disconnected)
        }
    }

    /// Last successfully parsed configuration, handy for diagnostics.
    public var lastKnownConfiguration: WGTunnelConfiguration? {
        lastConfiguration
    }

    private static func parseStatistics(fromRuntimeConfiguration configuration: String) -> WGEngineStatistics {
        var rx: UInt64 = 0
        var tx: UInt64 = 0
        var lastHandshake: Date?
        var currentPeer: [String: String] = [:]

        func consumePeer() {
            guard !currentPeer.isEmpty else { return }
            if let value = currentPeer["rx_bytes"], let parsed = UInt64(value) {
                rx = parsed
            }
            if let value = currentPeer["tx_bytes"], let parsed = UInt64(value) {
                tx = parsed
            }
            if let secString = currentPeer["last_handshake_time_sec"], let sec = UInt64(secString) {
                var timeInterval = TimeInterval(sec)
                if let nsecString = currentPeer["last_handshake_time_nsec"], let nsec = UInt64(nsecString) {
                    timeInterval += TimeInterval(nsec) / 1_000_000_000.0
                }
                if sec > 0 {
                    lastHandshake = Date(timeIntervalSince1970: timeInterval)
                }
            }
            currentPeer.removeAll(keepingCapacity: false)
        }

        configuration.enumerateLines { line, _ in
            if line == "peer" {
                consumePeer()
            } else {
                let parts = line.split(separator: "=", maxSplits: 1)
                guard parts.count == 2 else { return }
                let key = String(parts[0])
                let value = String(parts[1])
                currentPeer[key] = value
            }
        }
        consumePeer()

        return WGEngineStatistics(bytesIn: rx, bytesOut: tx, lastHandshakeTime: lastHandshake)
    }
}
