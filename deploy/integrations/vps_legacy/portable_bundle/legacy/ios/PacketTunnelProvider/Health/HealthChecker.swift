//
//  HealthChecker.swift
//  PacketTunnelProvider
//
//  Coordinates multi-modal health probes (UDP ping, HTTPS GET, DNS resolution)
//  to determine whether the active tunnel is functioning. The checker runs on a
//  dedicated queue so probes never block the main run loop of the Network
//  Extension. Results feed into Backoff + KillSwitch orchestration in
//  PacketTunnelProvider.
//

import Foundation
import Darwin

enum HealthState: String, Codable {
    case probing
    case healthy
    case unhealthy
}

enum HealthReason {
    case initial
    case probing
    case udpPingSucceeded
    case udpPingTimeout(String)
    case httpsReachable(String)
    case httpsFailed(String, String)
    case dnsResolved
    case dnsFailed(String)
    case paused
    case resumed

    var code: String {
        switch self {
        case .initial: return "EVT_HEALTH_INIT"
        case .probing: return "EVT_HEALTH_PROBE"
        case .udpPingSucceeded: return "EVT_HEALTH_UDP"
        case .udpPingTimeout: return "ERR_PING_TIMEOUT"
        case .httpsReachable: return "EVT_HEALTH_HTTPS"
        case .httpsFailed: return "ERR_HTTPS_UNREACHABLE"
        case .dnsResolved: return "EVT_HEALTH_DNS"
        case .dnsFailed: return "ERR_DNS_FAILURE"
        case .paused: return "EVT_HEALTH_PAUSED"
        case .resumed: return "EVT_HEALTH_RESUMED"
        }
    }

    var message: String {
        switch self {
        case .initial:
            return "Health checker initialised"
        case .probing:
            return "Running health probes"
        case .udpPingSucceeded:
            return "Toy engine pong received"
        case .udpPingTimeout(let error):
            return "UDP ping timed out: \(error)"
        case .httpsReachable(let url):
            return "HTTPS probe succeeded: \(url)"
        case .httpsFailed(let url, let detail):
            return "HTTPS probe failed for \(url): \(detail)"
        case .dnsResolved:
            return "DNS resolved successfully"
        case .dnsFailed(let detail):
            return "DNS resolution failed: \(detail)"
        case .paused:
            return "Health checker paused during reconnect"
        case .resumed:
            return "Health checker resumed"
        }
    }
}

struct HealthSnapshot: Codable {
    let state: HealthState
    let consecutiveFails: Int
    let consecutiveSuccess: Int
    let lastSuccessAt: Date?
    let lastFailureAt: Date?
    let reasonCode: String
    let reasonMessage: String
}

final class HealthChecker {
    struct Configuration {
        let probeInterval: TimeInterval
        let failThreshold: Int
        let successThreshold: Int
        let httpsProbeURLs: [URL]
        let dnsHost: String

        init(probeInterval: TimeInterval = 10,
             failThreshold: Int = 3,
             successThreshold: Int = 2,
             httpsProbeURLs: [URL] = [
                URL(string: "https://1.1.1.1/cdn-cgi/trace")!,
                URL(string: "https://api.openai.com/robots.txt")!
             ],
             dnsHost: String = "api.openai.com") {
            self.probeInterval = probeInterval
            self.failThreshold = failThreshold
            self.successThreshold = successThreshold
            self.httpsProbeURLs = httpsProbeURLs
            self.dnsHost = dnsHost
        }
    }

    var onHealthChange: ((Bool, HealthReason) -> Void)?
    var onSnapshot: ((HealthSnapshot) -> Void)?

    private let queue = DispatchQueue(label: "com.privatetunnel.health", qos: .utility)
    private let engine: TunnelEngine
    private let configuration: Configuration

    private var timer: DispatchSourceTimer?
    private var isRunning = false
    private var isPaused = false

    private var state: HealthState = .probing
    private var consecutiveFails = 0
    private var consecutiveSuccess = 0
    private var lastSuccessAt: Date?
    private var lastFailureAt: Date?
    private var lastReason: HealthReason = .initial
    private var snapshot: HealthSnapshot

    init(engine: TunnelEngine, configuration: Configuration = Configuration()) {
        self.engine = engine
        self.configuration = configuration
        self.snapshot = HealthSnapshot(
            state: .probing,
            consecutiveFails: 0,
            consecutiveSuccess: 0,
            lastSuccessAt: nil,
            lastFailureAt: nil,
            reasonCode: HealthReason.initial.code,
            reasonMessage: HealthReason.initial.message
        )
    }

    func start() {
        queue.async { [weak self] in
            guard let self else { return }
            guard !self.isRunning else { return }
            self.isRunning = true
            self.isPaused = false
            Logger.log(event: .eventHealthInit, level: .info, message: HealthReason.initial.message)
            self.updateSnapshot(state: .probing, reason: .initial)
            self.performHealthCheck()
            self.startTimer()
        }
    }

    func stop() {
        queue.async { [weak self] in
            guard let self else { return }
            self.isRunning = false
            self.timer?.cancel()
            self.timer = nil
        }
    }

    func pause() {
        queue.async { [weak self] in
            guard let self else { return }
            guard self.isRunning else { return }
            self.isPaused = true
            self.updateSnapshot(state: .probing, reason: .paused)
        }
    }

    func resume() {
        queue.async { [weak self] in
            guard let self else { return }
            guard self.isRunning else { return }
            self.isPaused = false
            self.updateSnapshot(state: .probing, reason: .resumed)
            self.performHealthCheck()
        }
    }

    func currentSnapshot() -> HealthSnapshot {
        queue.sync { snapshot }
    }

    private func startTimer() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + configuration.probeInterval, repeating: configuration.probeInterval)
        timer.setEventHandler { [weak self] in
            self?.performHealthCheck()
        }
        timer.resume()
        self.timer = timer
    }

    private func performHealthCheck() {
        guard isRunning, !isPaused else { return }

        updateSnapshot(state: .probing, reason: .probing)

        let token = UUID()
        var didComplete = false
        var recordedFailures = 0
        var lastFailureReason: HealthReason = .initial
        let totalProbes = max(1, configuration.httpsProbeURLs.count + 2) // UDP + DNS + HTTPS count

        func succeed(_ reason: HealthReason) {
            guard !didComplete else { return }
            didComplete = true
            recordSuccess(reason: reason)
        }

        func fail(_ reason: HealthReason) {
            guard !didComplete else { return }
            recordedFailures += 1
            lastFailureReason = reason
            if recordedFailures >= totalProbes {
                didComplete = true
                recordFailure(reason: lastFailureReason)
            }
        }

        engine.sendPing { [weak self] result in
            guard let self else { return }
            self.queue.async {
                switch result {
                case .success:
                    succeed(.udpPingSucceeded)
                case .failure(let error):
                    fail(.udpPingTimeout(error.localizedDescription))
                }
            }
        }

        for url in configuration.httpsProbeURLs {
            performHTTPSProbe(url: url) { reason in
                succeed(reason)
            } failure: { reason in
                fail(reason)
            }
        }

        performDNSProbe(host: configuration.dnsHost) { reason in
            succeed(reason)
        } failure: { reason in
            fail(reason)
        }
    }

    private func performHTTPSProbe(url: URL,
                                    success: @escaping (HealthReason) -> Void,
                                    failure: @escaping (HealthReason) -> Void) {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 6

        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 6
        config.timeoutIntervalForResource = 6
        config.waitsForConnectivity = true

        let session = URLSession(configuration: config)
        let task = session.dataTask(with: request) { [weak self] _, response, error in
            guard let self else { return }
            self.queue.async {
                defer { session.invalidateAndCancel() }
                if let error {
                    failure(.httpsFailed(url.absoluteString, error.localizedDescription))
                    return
                }
                if let http = response as? HTTPURLResponse {
                    if (200..<400).contains(http.statusCode) {
                        success(.httpsReachable(url.absoluteString))
                    } else {
                        failure(.httpsFailed(url.absoluteString, "HTTP \(http.statusCode)"))
                    }
                } else {
                    success(.httpsReachable(url.absoluteString))
                }
            }
        }
        task.resume()
    }

    private func performDNSProbe(host: String,
                                  success: @escaping (HealthReason) -> Void,
                                  failure: @escaping (HealthReason) -> Void) {
        queue.async { [weak self] in
            guard let self else { return }
            var hints = addrinfo(
                ai_flags: AI_ADDRCONFIG,
                ai_family: AF_UNSPEC,
                ai_socktype: SOCK_DGRAM,
                ai_protocol: IPPROTO_UDP,
                ai_addrlen: 0,
                ai_canonname: nil,
                ai_addr: nil,
                ai_next: nil
            )
            var resultPointer: UnsafeMutablePointer<addrinfo>? = nil
            let status = getaddrinfo(host, nil, &hints, &resultPointer)
            if status == 0, resultPointer != nil {
                freeaddrinfo(resultPointer)
                success(.dnsResolved)
            } else {
                if let pointer = resultPointer {
                    freeaddrinfo(pointer)
                }
                let message = String(cString: gai_strerror(status))
                failure(.dnsFailed(message))
            }
        }
    }

    private func recordSuccess(reason: HealthReason) {
        let previous = state
        consecutiveSuccess += 1
        consecutiveFails = 0
        lastSuccessAt = Date()
        lastReason = reason
        if consecutiveSuccess >= configuration.successThreshold {
            state = .healthy
        } else {
            state = .probing
        }
        updateSnapshot(state: state, reason: reason)
        if state == .healthy && previous != .healthy {
            Logger.log(event: .eventHealthPass, level: .info, message: reason.message)
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.onHealthChange?(true, reason)
            }
        }
    }

    private func recordFailure(reason: HealthReason) {
        let previous = state
        consecutiveFails += 1
        consecutiveSuccess = 0
        lastFailureAt = Date()
        lastReason = reason
        switch reason {
        case .udpPingTimeout:
            Logger.log(event: .errorPingTimeout, level: .error, message: reason.message)
        case .httpsFailed:
            Logger.log(event: .errorHTTPSUnreachable, level: .error, message: reason.message)
        case .dnsFailed:
            Logger.log(event: .errorDNSFailure, level: .error, message: reason.message)
        default:
            break
        }
        if consecutiveFails >= configuration.failThreshold {
            state = .unhealthy
        } else {
            state = .probing
        }
        updateSnapshot(state: state, reason: reason)
        if state == .unhealthy && previous != .unhealthy {
            Logger.log(event: .eventHealthFail, level: .warn, message: reason.message)
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.onHealthChange?(false, reason)
            }
        }
    }

    private func updateSnapshot(state: HealthState, reason: HealthReason) {
        snapshot = HealthSnapshot(
            state: state,
            consecutiveFails: consecutiveFails,
            consecutiveSuccess: consecutiveSuccess,
            lastSuccessAt: lastSuccessAt,
            lastFailureAt: lastFailureAt,
            reasonCode: reason.code,
            reasonMessage: reason.message
        )
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.onSnapshot?(self.snapshot)
        }
    }
}
