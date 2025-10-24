//
//  TunnelManager.swift
//  PrivateTunnel
//
//  Purpose: Bridges the container app UI with the Network Extension by managing the
//  lifecycle of NETunnelProviderManager and forwarding connect/disconnect requests.
//  The manager serialises TunnelConfig into providerConfiguration so the extension
//  can reconstruct WireGuard parameters when the tunnel starts.
//
//  Usage:
//      let tunnelManager = TunnelManager()
//      tunnelManager.save(configuration: config) { _ in }
//      tunnelManager.connect()
//
//  Notes:
//      - This file intentionally avoids singletons to keep dependency injection simple.
//      - All callbacks are delivered on the main thread for UI friendliness.
//
import Foundation
import NetworkExtension

final class TunnelManager: ObservableObject {
    enum TunnelError: LocalizedError {
        case configurationUnavailable
        case noConfigurationSelected
        case startFailed(Error)
        case stopFailed(Error)

        var errorDescription: String? {
            switch self {
            case .configurationUnavailable:
                return "无法加载或创建 VPN 配置。请检查 Network Extension 权限。"
            case .noConfigurationSelected:
                return "请先选择需要连接的配置。"
            case .startFailed(let error):
                return "启动隧道失败：\(error.localizedDescription)"
            case .stopFailed(let error):
                return "停止隧道失败：\(error.localizedDescription)"
            }
        }
    }

    @Published private(set) var currentStatus: NEVPNStatus = .invalid
    @Published private(set) var providerStatus: ProviderStatus?
    @Published private(set) var recentEvents: [DiagnosticLogEntry] = []

    private let providerBundleIdentifier = "com.privatetunnel.PacketTunnelProvider"
    private let providerConfigKey = "pt_config_json"
    private let routingModeDefaultsKeyPrefix = "com.privatetunnel.routing.mode."

    private var manager: NETunnelProviderManager?
    private var statusObserver: NSObjectProtocol?
    private var statusTimer: Timer?
    private let logBuffer = LogRingBuffer()
    private var knownExtensionEventKeys = Set<String>()

    deinit {
        if let observer = statusObserver {
            NotificationCenter.default.removeObserver(observer)
        }
        statusTimer?.invalidate()
    }

    private func appendAppLog(level: DiagnosticLogLevel, code: String, message: String, metadata: [String: String] = [:]) {
        logBuffer.append(level: level, code: code, message: message, metadata: metadata)
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.recentEvents = self.logBuffer.recent(limit: 100)
        }
    }

    func loadOrCreateProvider(completion: @escaping (Result<NETunnelProviderManager, Error>) -> Void) {
        let bundleIdentifier = providerBundleIdentifier

        NETunnelProviderManager.loadAllFromPreferences { [weak self] managers, error in
            if let error {
                DispatchQueue.main.async {
                    completion(.failure(error))
                }
                return
            }

            let existing = managers?.first(where: { manager in
                guard let proto = manager.protocolConfiguration as? NETunnelProviderProtocol else { return false }
                return proto.providerBundleIdentifier == bundleIdentifier
            })

            let targetManager: NETunnelProviderManager
            if let existing {
                targetManager = existing
            } else {
                let newManager = NETunnelProviderManager()
                let proto = NETunnelProviderProtocol()
                proto.providerBundleIdentifier = bundleIdentifier
                proto.serverAddress = "placeholder"
                newManager.protocolConfiguration = proto
                newManager.localizedDescription = "PrivateTunnel"
                newManager.isEnabled = true
                targetManager = newManager
            }

            self?.observeStatusUpdates(for: targetManager)
            self?.manager = targetManager
            self?.startStatusPolling()

            DispatchQueue.main.async {
                completion(.success(targetManager))
            }
        }
    }

    func save(configuration: TunnelConfig, completion: @escaping (Result<Void, Error>) -> Void) {
        loadOrCreateProvider { [weak self] result in
            switch result {
            case .failure(let error):
                completion(.failure(error))
            case .success(let manager):
                guard let self else {
                    completion(.failure(TunnelError.configurationUnavailable))
                    return
                }

                let proto: NETunnelProviderProtocol
                if let existing = manager.protocolConfiguration as? NETunnelProviderProtocol {
                    proto = existing
                } else {
                    let newProto = NETunnelProviderProtocol()
                    newProto.providerBundleIdentifier = self.providerBundleIdentifier
                    proto = newProto
                }

                do {
                    let data = try JSONEncoder().encode(configuration)
                    guard let jsonString = String(data: data, encoding: .utf8) else {
                        throw TunnelError.configurationUnavailable
                    }
                    proto.providerBundleIdentifier = self.providerBundleIdentifier
                    proto.serverAddress = "\(configuration.endpoint.host):\(configuration.endpoint.port)"
                    var providerConfig = proto.providerConfiguration ?? [:]
                    providerConfig[self.providerConfigKey] = jsonString
                    providerConfig["routing_mode"] = configuration.routing.mode
                    proto.providerConfiguration = providerConfig
                    manager.protocolConfiguration = proto
                    manager.localizedDescription = configuration.profile_name
                    manager.isEnabled = true
                } catch {
                    completion(.failure(error))
                    return
                }

                manager.saveToPreferences { error in
                    guard error == nil else {
                        DispatchQueue.main.async {
                            completion(.failure(error!))
                        }
                        return
                    }

                    manager.loadFromPreferences { loadError in
                        DispatchQueue.main.async {
                            if let loadError {
                                completion(.failure(loadError))
                            } else {
                                completion(.success(()))
                            }
                        }
                    }
                }
            }
        }
    }

    func persistedRoutingMode(for profile: String) -> String? {
        UserDefaults.standard.string(forKey: routingModeDefaultsKeyPrefix + profile)
    }

    func rememberRoutingMode(_ mode: String, for profile: String) {
        UserDefaults.standard.set(mode, forKey: routingModeDefaultsKeyPrefix + profile)
    }

    func connect(completion: ((Result<Void, Error>) -> Void)? = nil) {
        appendAppLog(level: .info, code: "APP_CONNECT", message: "用户触发连接请求")
        loadOrCreateProvider { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                self.appendAppLog(level: .error, code: "APP_CONNECT_FAIL", message: "加载 VPN 配置失败", metadata: ["error": error.localizedDescription])
                completion?(.failure(error))
            case .success(let manager):
                manager.loadFromPreferences { error in
                    if let error {
                        self.appendAppLog(level: .error, code: "APP_CONNECT_FAIL", message: "同步 VPN 配置失败", metadata: ["error": error.localizedDescription])
                        DispatchQueue.main.async {
                            completion?(.failure(error))
                        }
                        return
                    }

                    do {
                        try manager.connection.startVPNTunnel()
                        self.requestStatusUpdate()
                        self.appendAppLog(level: .info, code: "APP_CONNECT_OK", message: "VPN 启动命令已发送")
                        DispatchQueue.main.async {
                            completion?(.success(()))
                        }
                    } catch {
                        self.appendAppLog(level: .error, code: "APP_CONNECT_FAIL", message: "启动隧道失败", metadata: ["error": error.localizedDescription])
                        DispatchQueue.main.async {
                            completion?(.failure(TunnelError.startFailed(error)))
                        }
                    }
                }
            }
        }
    }

    func disconnect(completion: ((Result<Void, Error>) -> Void)? = nil) {
        appendAppLog(level: .info, code: "APP_DISCONNECT", message: "用户触发断开请求")
        if let manager {
            manager.connection.stopVPNTunnel()
            stopStatusPolling()
            appendAppLog(level: .info, code: "APP_DISCONNECT_OK", message: "VPN 停止命令已发送")
            DispatchQueue.main.async {
                completion?(.success(()))
            }
        } else {
            loadOrCreateProvider { [weak self] result in
                guard let self else { return }
                switch result {
                case .failure(let error):
                    self.appendAppLog(level: .error, code: "APP_DISCONNECT_FAIL", message: "加载 VPN 管理器失败", metadata: ["error": error.localizedDescription])
                    completion?(.failure(error))
                case .success(let manager):
                    manager.connection.stopVPNTunnel()
                    self.stopStatusPolling()
                    self.appendAppLog(level: .info, code: "APP_DISCONNECT_OK", message: "VPN 停止命令已发送")
                    DispatchQueue.main.async {
                        completion?(.success(()))
                    }
                }
            }
        }
    }

    func status() -> NEVPNStatus {
        manager?.connection.status ?? currentStatus
    }

    func fetchExtensionEvents(limit: Int = 200, completion: @escaping (Result<[ProviderStatus.Event], Error>) -> Void) {
        guard let manager else {
            completion(.failure(TunnelError.configurationUnavailable))
            return
        }
        let payload: [String: Any] = [
            "command": "events",
            "limit": limit
        ]
        let message = (try? JSONSerialization.data(withJSONObject: payload, options: [])) ?? Data()
        do {
            try manager.connection.sendProviderMessage(message) { data in
                guard let data else {
                    completion(.success([]))
                    return
                }
                do {
                    let envelope = try JSONDecoder().decode(EventsEnvelope.self, from: data)
                    completion(.success(envelope.events))
                } catch {
                    completion(.failure(error))
                }
            }
        } catch {
            completion(.failure(error))
        }
    }

    func exportDiagnostics(activeConfig: TunnelConfig?, completion: @escaping (Result<URL, Error>) -> Void) {
        fetchExtensionEvents(limit: 200) { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                completion(.failure(error))
            case .success(let events):
                let appLogs = self.logBuffer.recent(limit: 200)
                var additional: [String: Data] = [:]
                if let status = self.providerStatus {
                    if let data = try? JSONSerialization.data(withJSONObject: self.providerStatusDictionary(status), options: [.prettyPrinted, .sortedKeys]) {
                        additional["provider_status.json"] = data
                    }
                }
                let snapshot = DiagnosticsSnapshot(appLogs: appLogs, extensionEvents: events, activeConfig: activeConfig, additionalFiles: additional)
                do {
                    let url = try ExportDiagnostics.buildArchive(from: snapshot)
                    completion(.success(url))
                } catch {
                    completion(.failure(error))
                }
            }
        }
    }

    private func observeStatusUpdates(for manager: NETunnelProviderManager) {
        if let observer = statusObserver {
            NotificationCenter.default.removeObserver(observer)
        }
        statusObserver = NotificationCenter.default.addObserver(
            forName: .NEVPNStatusDidChange,
            object: manager.connection,
            queue: .main
        ) { [weak self] _ in
            self?.currentStatus = manager.connection.status
            self?.requestStatusUpdate()
        }
        currentStatus = manager.connection.status
    }

    private func startStatusPolling() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.statusTimer?.invalidate()
            let timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
                self?.requestStatusUpdate()
            }
            RunLoop.main.add(timer, forMode: .common)
            self.statusTimer = timer
        }
    }

    private func stopStatusPolling() {
        DispatchQueue.main.async { [weak self] in
            self?.statusTimer?.invalidate()
            self?.statusTimer = nil
            self?.providerStatus = nil
        }
    }

    private func requestStatusUpdate() {
        guard let manager else { return }
        let message = (try? JSONSerialization.data(withJSONObject: ["command": "status"], options: [])) ?? Data()
        do {
            try manager.connection.sendProviderMessage(message) { [weak self] data in
                guard let data, let status = try? JSONDecoder().decode(ProviderStatus.self, from: data) else { return }
                DispatchQueue.main.async {
                    self?.providerStatus = status
                }
                self?.ingestExtensionEvents(status.events)
            }
        } catch {
            DispatchQueue.main.async { [weak self] in
                self?.providerStatus = nil
            }
        }
    }

    private func ingestExtensionEvents(_ events: [ProviderStatus.Event]) {
        guard !events.isEmpty else { return }
        var added = false
        for event in events.sorted(by: { $0.timestamp < $1.timestamp }) {
            let key = "\(event.code)|\(event.message)|\(event.timestamp.timeIntervalSince1970)"
            if knownExtensionEventKeys.contains(key) { continue }
            knownExtensionEventKeys.insert(key)
            let level = DiagnosticLogLevel(rawValue: event.level.uppercased()) ?? .info
            let entry = DiagnosticLogEntry(timestamp: event.timestamp, level: level, code: event.code, message: event.message, metadata: event.metadata)
            logBuffer.append(entry)
            added = true
        }
        if added {
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.recentEvents = self.logBuffer.recent(limit: 100)
            }
        }
    }
}

private struct EventsEnvelope: Decodable {
    let events: [ProviderStatus.Event]
}

extension TunnelManager {
    private func providerStatusDictionary(_ status: ProviderStatus) -> [String: Any] {
        var dict: [String: Any] = [
            "status": status.status
        ]
        if let name = status.profileName {
            dict["profile_name"] = name
        }
        if let engine = status.engine {
            dict["engine"] = engine
        }
        if let health = status.health {
            var healthDict: [String: Any] = [
                "state": health.state,
                "consecutive_fails": health.consecutiveFails,
                "consecutive_success": health.consecutiveSuccess,
                "reason_code": health.reasonCode,
                "reason_message": health.reasonMessage
            ]
            if let lastSuccess = health.lastSuccessAt {
                healthDict["last_success_at"] = ProviderStatus.isoFormatter.string(from: lastSuccess)
            }
            if let lastFailure = health.lastFailureAt {
                healthDict["last_failure_at"] = ProviderStatus.isoFormatter.string(from: lastFailure)
            }
            dict["health"] = healthDict
        }
        if let reconnect = status.reconnect {
            var reconnectDict: [String: Any] = [
                "attempts": reconnect.attempts
            ]
            if let last = reconnect.lastStartedAt {
                reconnectDict["last_started_at"] = ProviderStatus.isoFormatter.string(from: last)
            }
            if let next = reconnect.nextRetryIn {
                reconnectDict["next_retry_in"] = next
            }
            if let delay = reconnect.lastDelay {
                reconnectDict["last_delay"] = delay
            }
            dict["reconnect"] = reconnectDict
        }
        if let kill = status.killSwitch {
            dict["kill_switch"] = [
                "enabled": kill.enabled,
                "engaged": kill.engaged,
                "reason": kill.reason
            ]
        }
        if let stats = status.engineStats {
            var statsDict: [String: Any] = [
                "tx_packets": stats.txPackets,
                "rx_packets": stats.rxPackets,
                "tx_bytes": stats.txBytes,
                "rx_bytes": stats.rxBytes,
                "endpoint": stats.endpoint,
                "heartbeats_missed": stats.heartbeatsMissed
            ]
            if let last = stats.lastAliveAt {
                statsDict["last_alive_at"] = ProviderStatus.isoFormatter.string(from: last)
            }
            dict["engine_stats"] = statsDict
        }
        if !status.events.isEmpty {
            let events = status.events.map { event -> [String: Any] in
                var entry: [String: Any] = [
                    "timestamp": ProviderStatus.isoFormatter.string(from: event.timestamp),
                    "code": event.code,
                    "message": event.message,
                    "level": event.level,
                    "metadata": event.metadata
                ]
                return entry
            }
            dict["events"] = events
        }
        return dict
    }
}
