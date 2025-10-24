//
//  PacketTunnelProvider.swift
//  PacketTunnelProvider
//
//  Purpose: Entry point of the Network Extension. It reconstructs the WGConfig
//  passed by the container app, applies NEPacketTunnelNetworkSettings, and
//  launches either the mock engine or the toy UDP/TUN bridge for development
//  validation. Round 7 extends the provider with unified health checking,
//  automatic reconnect (with exponential backoff), and a best-effort kill switch.
//

import Foundation
import NetworkExtension

final class PacketTunnelProvider: NEPacketTunnelProvider {
    private let providerConfigKey = "pt_config_json"

    private var engine: TunnelEngine?
    private var engineKind: WGConfig.Engine = .mock
    private var currentConfig: WGConfig?
    private var providerOptions: [String: Any] = [:]

    private var healthChecker: HealthChecker?
    private var latestHealthSnapshot: HealthSnapshot?
    private var backoff = BackoffPolicy()
    private var reconnectTimer: DispatchSourceTimer?
    private var reconnectAttemptCount = 0
    private var lastReconnectAt: Date?
    private var nextRetryDeadline: Date?

    private let controlQueue = DispatchQueue(label: "com.privatetunnel.provider.control", qos: .utility)
    private let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private let killSwitch = KillSwitch()
    private var killSwitchState = KillSwitchState(enabled: false, engaged: false, reason: nil)

    override init() {
        super.init()
        killSwitch.onStateChange = { [weak self] state in
            guard let self else { return }
            self.killSwitchState = state
            if state.enabled && state.engaged {
                self.engine?.setTrafficBlocked(true)
                Logger.log(event: .eventKillSwitchEngaged, level: .security, message: "Kill switch engaged", meta: ["reason": state.reason ?? ""])
            } else if !state.engaged {
                self.engine?.setTrafficBlocked(false)
                Logger.log(event: .eventKillSwitchReleased, level: .security, message: "Kill switch disengaged", meta: ["enabled": state.enabled ? "true" : "false"])
            } else {
                Logger.log(event: .securityKillSwitch, level: .security, message: "Kill switch state changed", meta: ["enabled": state.enabled ? "true" : "false", "engaged": state.engaged ? "true" : "false"])
            }
        }
    }

    override func startTunnel(options: [String : NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        Logger.log(event: .eventConnectStart, level: .info, message: "PacketTunnelProvider.startTunnel invoked")

        guard let protocolConfiguration = protocolConfiguration as? NETunnelProviderProtocol else {
            Logger.log(event: .errorEngineConfig, level: .error, message: "Protocol configuration is not NETunnelProviderProtocol")
            completionHandler(NSError(domain: "PacketTunnel", code: -1, userInfo: [NSLocalizedDescriptionKey: "Invalid protocol configuration"]))
            return
        }

        providerOptions = protocolConfiguration.providerConfiguration ?? [:]

        guard let jsonString = providerOptions[providerConfigKey] as? String,
              let data = jsonString.data(using: .utf8) else {
            Logger.log(event: .errorEngineConfig, level: .error, message: "Missing pt_config_json in providerConfiguration")
            completionHandler(NSError(domain: "PacketTunnel", code: -2, userInfo: [NSLocalizedDescriptionKey: "缺少配置数据"]))
            return
        }

        let config: WGConfig
        do {
            config = try WGConfigParser.parse(from: data)
            currentConfig = config
            controlQueue.sync {
                reconnectTimer?.cancel()
                reconnectTimer = nil
                backoff.reset()
                reconnectAttemptCount = 0
                lastReconnectAt = nil
                nextRetryDeadline = nil
            }
            do {
                try AuditGuards.assertConfigSane(config)
                try AuditGuards.assertKeychainAccess()
            } catch {
                Logger.log(event: .errorAuditFailure, level: .error, message: "Audit guard failed: \(error.localizedDescription)")
                completionHandler(error)
                return
            }
        } catch {
            Logger.log(event: .errorEngineConfig, level: .error, message: "Failed to parse configuration: \(error.localizedDescription)")
            completionHandler(error)
            return
        }

        applyNetworkSettings(for: config) { [weak self] error in
            guard let self else { return }
            if let error {
                Logger.log(event: .errorEngineConfig, level: .error, message: "Failed to apply network settings: \(error.localizedDescription)")
                completionHandler(error)
                return
            }

            do {
                try self.startEngine(for: config)
                self.configureKillSwitch(enabled: config.enableKillSwitch)
                self.setupHealthChecker(using: config)
                Logger.log(event: .eventConnectSuccess, level: .info, message: "PacketTunnelProvider started successfully")
                completionHandler(nil)
            } catch {
                Logger.log(event: .errorEngineConfig, level: .error, message: "Engine start failed: \(error.localizedDescription)")
                completionHandler(error)
            }
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        Logger.log(event: .eventDisconnect, level: .info, message: "PacketTunnelProvider.stopTunnel invoked.", meta: ["reason": String(reason.rawValue)])
        controlQueue.sync {
            reconnectTimer?.cancel()
            reconnectTimer = nil
            backoff.reset()
            reconnectAttemptCount = 0
            lastReconnectAt = nil
            nextRetryDeadline = nil
        }
        healthChecker?.stop()
        healthChecker = nil
        latestHealthSnapshot = nil
        stopActiveEngine()
        currentConfig = nil
        killSwitch.configure(enabled: false)
        killSwitchState = killSwitch.currentState()
        completionHandler()
    }

    override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)? = nil) {
        guard let handler = completionHandler else { return }

        var command: String = "status"
        if !messageData.isEmpty,
           let object = try? JSONSerialization.jsonObject(with: messageData, options: []),
           let dict = object as? [String: Any],
           let providedCommand = dict["command"] as? String {
            command = providedCommand
        }

        switch command {
        case "status":
            let response = buildStatusResponse()
            let data = try? JSONSerialization.data(withJSONObject: response, options: [])
            handler(data)
        case "events":
            let limit = (dict["limit"] as? Int) ?? 100
            let response = buildEventsResponse(limit: limit)
            let data = try? JSONSerialization.data(withJSONObject: response, options: [])
            handler(data)
        default:
            handler(nil)
        }
    }

    private func buildStatusResponse() -> [String: Any] {
        var response: [String: Any] = [
            "status": connection.status.rawValue
        ]
        if let config = currentConfig {
            response["profile_name"] = config.profileName
            response["engine"] = engineKind.rawValue
        }

        if let snapshot = latestHealthSnapshot {
            var health: [String: Any] = [
                "state": snapshot.state.rawValue,
                "consecutive_fails": snapshot.consecutiveFails,
                "consecutive_success": snapshot.consecutiveSuccess,
                "reason_code": snapshot.reasonCode,
                "reason_message": snapshot.reasonMessage
            ]
            if let last = snapshot.lastSuccessAt {
                health["last_success_at"] = isoFormatter.string(from: last)
            }
            if let lastFail = snapshot.lastFailureAt {
                health["last_failure_at"] = isoFormatter.string(from: lastFail)
            }
            response["health"] = health
        }

        if let engine = engine {
            let stats = engine.stats()
            var statsDict: [String: Any] = [
                "tx_packets": stats.txPackets,
                "rx_packets": stats.rxPackets,
                "tx_bytes": stats.txBytes,
                "rx_bytes": stats.rxBytes,
                "endpoint": stats.endpoint,
                "heartbeats_missed": stats.heartbeatsMissed
            ]
            if let last = stats.lastAliveAt {
                statsDict["last_alive_at"] = isoFormatter.string(from: last)
            }
            response["engine_stats"] = statsDict
        }

        controlQueue.sync {
            var reconnect: [String: Any] = [
                "attempts": reconnectAttemptCount
            ]
            if let last = lastReconnectAt {
                reconnect["last_started_at"] = isoFormatter.string(from: last)
            }
            if let deadline = nextRetryDeadline {
                reconnect["next_retry_in"] = max(0, deadline.timeIntervalSinceNow)
            }
            if backoff.lastDelay > 0 {
                reconnect["last_delay"] = backoff.lastDelay
            }
            response["reconnect"] = reconnect
        }

        response["kill_switch"] = [
            "enabled": killSwitchState.enabled,
            "engaged": killSwitchState.engaged,
            "reason": killSwitchState.reason ?? ""
        ]

        let events = Logger.recentEvents(limit: 50).map { record in
            [
                "timestamp": isoFormatter.string(from: record.timestamp),
                "code": record.code.rawValue,
                "message": record.message,
                "level": record.level.rawValue,
                "metadata": record.metadata ?? [:]
            ]
        }
        response["events"] = events
        return response
    }

    private func buildEventsResponse(limit: Int) -> [String: Any] {
        let records = Logger.recentEvents(limit: limit)
        let events = records.map { record -> [String: Any] in
            var entry: [String: Any] = [
                "timestamp": isoFormatter.string(from: record.timestamp),
                "code": record.code.rawValue,
                "message": record.message,
                "level": record.level.rawValue
            ]
            if let meta = record.metadata {
                entry["metadata"] = meta
            }
            return entry
        }
        return ["events": events]
    }

    private func configureKillSwitch(enabled: Bool) {
        killSwitch.configure(enabled: enabled)
        killSwitchState = killSwitch.currentState()
    }

    private func setupHealthChecker(using config: WGConfig) {
        healthChecker?.stop()
        guard let engine else { return }

        let probeInterval = (providerOptions["probe_interval_sec"] as? Double).map(TimeInterval.init) ?? 10
        let failThreshold = providerOptions["fail_threshold"] as? Int ?? 3
        let successThreshold = providerOptions["success_threshold"] as? Int ?? 2
        let httpsURLs: [URL]
        if let raw = providerOptions["https_probe_urls"] as? [String] {
            httpsURLs = raw.compactMap { URL(string: $0) }
        } else {
            httpsURLs = [
                URL(string: "https://1.1.1.1/cdn-cgi/trace")!,
                URL(string: "https://api.openai.com/robots.txt")!
            ]
        }
        let dnsHost = providerOptions["dns_probe_host"] as? String ?? "api.openai.com"

        let configuration = HealthChecker.Configuration(
            probeInterval: probeInterval,
            failThreshold: failThreshold,
            successThreshold: successThreshold,
            httpsProbeURLs: httpsURLs,
            dnsHost: dnsHost
        )

        let checker = HealthChecker(engine: engine, configuration: configuration)
        checker.onSnapshot = { [weak self] snapshot in
            self?.latestHealthSnapshot = snapshot
        }
        checker.onHealthChange = { [weak self] isHealthy, reason in
            self?.handleHealthChange(isHealthy: isHealthy, reason: reason)
        }
        checker.start()
        healthChecker = checker
        latestHealthSnapshot = checker.currentSnapshot()
    }

    private func handleHealthChange(isHealthy: Bool, reason: HealthReason) {
        if isHealthy {
            backoff.reset()
            controlQueue.async { [weak self] in
                guard let self else { return }
                self.reconnectTimer?.cancel()
                self.reconnectTimer = nil
                self.lastReconnectAt = nil
                self.nextRetryDeadline = nil
            }
            killSwitch.disengage(reason: "Tunnel healthy")
        } else {
            scheduleReconnect(reason: reason)
        }
    }

    private func scheduleReconnect(reason: HealthReason) {
        controlQueue.async { [weak self] in
            guard let self else { return }
            guard self.reconnectTimer == nil else { return }
            guard let config = self.currentConfig, let engine = self.engine else { return }

            self.healthChecker?.pause()
            engine.stop()
            if self.killSwitchState.enabled {
                self.killSwitch.engage(reason: "Tunnel unhealthy: \(reason.message)")
                engine.setTrafficBlocked(true)
                Logger.log(event: .eventKillSwitchEngaged, level: .security, message: "Kill switch engaged due to health failure", meta: ["reason": reason.code])
            }

            if config.routing.mode == .whitelist {
                Logger.log(event: .eventEngineWaiting, level: .warn, message: "Whitelist routing in effect; 若持续异常，可在容器 App 中切换回 Global 模式排查。")
            }

            let delay = self.backoff.nextDelay()
            self.reconnectAttemptCount += 1
            self.lastReconnectAt = Date()
            self.nextRetryDeadline = self.lastReconnectAt?.addingTimeInterval(delay)
            Logger.log(event: .eventEngineReconnect, level: .info, message: "Scheduling reconnect in \(String(format: "%.1f", delay))s")

            let timer = DispatchSource.makeTimerSource(queue: self.controlQueue)
            timer.schedule(deadline: .now() + delay)
            timer.setEventHandler { [weak self] in
                self?.performReconnect()
            }
            timer.resume()
            self.reconnectTimer = timer
        }
    }

    private func performReconnect() {
        controlQueue.async { [weak self] in
            guard let self else { return }
            self.reconnectTimer?.cancel()
            self.reconnectTimer = nil
            self.nextRetryDeadline = nil
            guard let config = self.currentConfig, let engine = self.engine else { return }
            do {
                try engine.start(with: config)
                self.healthChecker?.resume()
                Logger.log(event: .eventEngineReady, level: .info, message: "Engine restarted after reconnect attempt")
            } catch {
                Logger.log(event: .errorEngineConfig, level: .error, message: "Failed to restart engine: \(error.localizedDescription)")
                self.scheduleReconnect(reason: .udpPingTimeout(error.localizedDescription))
            }
        }
    }

    private func startEngine(for config: WGConfig) throws {
        if engine == nil || engineKind != config.engine {
            let created = instantiateEngine(for: config)
            engine = created.engine
            engineKind = created.kind
            if killSwitchState.enabled && killSwitchState.engaged {
                engine?.setTrafficBlocked(true)
            }
        }
        guard let engine else {
            throw NSError(domain: "PacketTunnel", code: -3, userInfo: [NSLocalizedDescriptionKey: "Engine unavailable"])
        }
        try engine.start(with: config)
    }

    private func instantiateEngine(for config: WGConfig) -> (engine: TunnelEngine, kind: WGConfig.Engine) {
        switch config.engine {
        case .mock:
            return (WGEngineMock(packetFlow: packetFlow), .mock)
        case .toy:
            guard config.routing.mode == .global else {
                Logger.log(event: .eventEngineWaiting, level: .warn, message: "Toy engine currently supports global routing only; falling back to mock engine.")
                return (WGEngineMock(packetFlow: packetFlow), .mock)
            }
            return (WGEngineToy(packetFlow: packetFlow), .toy)
        case .wireguard:
            Logger.log(event: .eventEngineWaiting, level: .warn, message: "WireGuard engine not yet integrated; using mock placeholder.")
            return (WGEngineMock(packetFlow: packetFlow), .mock)
        }
    }

    private func stopActiveEngine() {
        engine?.stop()
        engine = nil
    }

    private func applyNetworkSettings(for config: WGConfig, completion: @escaping (Error?) -> Void) {
        let remoteAddress = config.endpoint.host
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: remoteAddress)

        let (address, mask) = parseAddress(config.client.address)
        let ipv4Settings = NEIPv4Settings(addresses: [address], subnetMasks: [mask])
        ipv4Settings.includedRoutes = [NEIPv4Route.default()]
        if config.routing.mode == .whitelist {
            Logger.log(event: .eventEngineWaiting, level: .info, message: "Whitelist mode: retaining default IPv4 route while server-side ipset filters destinations.")
        }
        settings.ipv4Settings = ipv4Settings

        let dnsSettings = NEDNSSettings(servers: config.client.dns)
        dnsSettings.matchDomains = [""]
        settings.dnsSettings = dnsSettings

        if let mtu = config.client.mtu {
            settings.mtu = NSNumber(value: mtu)
        }

        Logger.log(event: .engineStart, level: .info, message: "Applying network settings", meta: [
            "address": address,
            "mask": mask,
            "dns": config.client.dns.joined(separator: ",")
        ])

        setTunnelNetworkSettings(settings) { error in
            completion(error)
        }
    }

    private func parseAddress(_ cidr: String) -> (String, String) {
        let components = cidr.split(separator: "/")
        guard components.count == 2, let prefix = Int(components[1]) else {
            return (cidr, "255.255.255.255")
        }
        return (String(components[0]), subnetMask(from: prefix))
    }

    private func subnetMask(from prefixLength: Int) -> String {
        guard prefixLength >= 0 && prefixLength <= 32 else { return "255.255.255.255" }
        var mask: UInt32 = prefixLength == 0 ? 0 : ~UInt32(0) << (32 - UInt32(prefixLength))
        var octets: [String] = []
        for _ in 0..<4 {
            let value = (mask & 0xFF000000) >> 24
            octets.append(String(value))
            mask <<= 8
        }
        return octets.joined(separator: ".")
    }
}
