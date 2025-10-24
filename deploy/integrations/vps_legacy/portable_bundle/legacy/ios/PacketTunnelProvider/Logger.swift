//
//  Logger.swift
//  PacketTunnelProvider
//
//  Provides a unified logging surface for the packet tunnel extension with
//  support for log levels, structured event codes and redaction of sensitive
//  material before it is persisted or emitted to the system log. The
//  implementation deliberately keeps dependencies to Foundation / os.log so it
//  can be safely embedded inside the Network Extension target.
//

import Foundation
import os.log

enum LogLevel: String, Codable {
    case info = "INFO"
    case warn = "WARN"
    case error = "ERROR"
    case security = "SECURITY"

    var osLogType: OSLogType {
        switch self {
        case .info:
            return .info
        case .warn:
            return .default
        case .error:
            return .error
        case .security:
            return .fault
        }
    }

    var symbol: String {
        switch self {
        case .info:
            return "info.circle"
        case .warn:
            return "exclamationmark.triangle"
        case .error:
            return "xmark.octagon"
        case .security:
            return "lock.shield"
        }
    }
}

enum LogEventCode: String, Codable {
    case eventConnectStart = "EVT_CONNECT_START"
    case eventConnectSuccess = "EVT_CONNECT_SUCCESS"
    case eventDisconnect = "EVT_DISCONNECT"
    case eventHealthInit = "EVT_HEALTH_INIT"
    case eventHealthPass = "EVT_HEALTH_PASS"
    case eventHealthFail = "EVT_HEALTH_FAIL"
    case eventKillSwitchEngaged = "EVT_KILLSWITCH_ENGAGED"
    case eventKillSwitchReleased = "EVT_KILLSWITCH_RELEASED"
    case eventEngineReady = "EVT_ENGINE_READY"
    case eventEngineWaiting = "EVT_ENGINE_WAITING"
    case eventEngineReconnect = "EVT_ENGINE_RECONNECT"
    case engineStart = "EVT_ENGINE_START"
    case engineStop = "EVT_ENGINE_STOP"
    case securityKeychainAccess = "SEC_KEYCHAIN_ACCESS"
    case securityConfigAudit = "SEC_CONFIG_AUDIT"
    case securityKillSwitch = "SEC_KILL_SWITCH"
    case securityRedaction = "SEC_REDACTION"
    case errorPingTimeout = "ERR_PING_TIMEOUT"
    case errorEngineTransport = "ERR_ENGINE_TRANSPORT"
    case errorEngineProtocol = "ERR_ENGINE_PROTOCOL"
    case errorEngineConfig = "ERR_ENGINE_CONFIG"
    case errorHTTPSUnreachable = "ERR_HTTPS_UNREACHABLE"
    case errorDNSFailure = "ERR_DNS_FAILURE"
    case errorAuditFailure = "ERR_AUDIT_FAILURE"
}

struct LogEventRecord: Codable {
    let timestamp: Date
    let level: LogLevel
    let code: LogEventCode
    let message: String
    let metadata: [String: String]?
}

enum Logger {
    private static let subsystem = "com.privatetunnel.PacketTunnelProvider"
    private static let generalLog = OSLog(subsystem: subsystem, category: "general")
    private static let eventQueue = DispatchQueue(label: "com.privatetunnel.logger", qos: .utility)
    private static var events: [LogEventRecord] = []
    private static let maxEvents = 200

    static func log(level: LogLevel, code: LogEventCode, message: String, metadata: [String: String]? = nil) {
        let redactedMessage = Redactor.redact(string: message)
        let redactedMetadata = metadata.map { Redactor.redact(dict: $0) }

        if let meta = redactedMetadata, !meta.isEmpty {
            let metaString = meta.map { "\($0.key)=\($0.value)" }.joined(separator: "; ")
            os_log("[%{public}@] %{public}@ { %{public}@ }", log: generalLog, type: level.osLogType, code.rawValue, redactedMessage, metaString)
        } else {
            os_log("[%{public}@] %{public}@", log: generalLog, type: level.osLogType, code.rawValue, redactedMessage)
        }

        let record = LogEventRecord(
            timestamp: Date(),
            level: level,
            code: code,
            message: redactedMessage,
            metadata: redactedMetadata
        )
        eventQueue.async {
            events.append(record)
            if events.count > maxEvents {
                events.removeFirst(events.count - maxEvents)
            }
        }
    }

    static func logInfo(_ message: String, code: LogEventCode = .eventEngineReady, metadata: [String: String]? = nil) {
        log(level: .info, code: code, message: message, metadata: metadata)
    }

    static func logWarn(_ message: String, code: LogEventCode = .eventEngineWaiting, metadata: [String: String]? = nil) {
        log(level: .warn, code: code, message: message, metadata: metadata)
    }

    static func logError(_ message: String, code: LogEventCode = .errorEngineConfig, metadata: [String: String]? = nil) {
        log(level: .error, code: code, message: message, metadata: metadata)
    }

    static func logSecurity(_ message: String, code: LogEventCode, metadata: [String: String]? = nil) {
        log(level: .security, code: code, message: message, metadata: metadata)
    }

    static func log(event code: LogEventCode, level: LogLevel = .info, message: String, meta: [String: String]? = nil) {
        log(level: level, code: code, message: message, metadata: meta)
    }

    static func recentEvents(limit: Int = 50) -> [LogEventRecord] {
        eventQueue.sync {
            Array(events.suffix(limit))
        }
    }

    static func clear() {
        eventQueue.async {
            events.removeAll()
        }
    }
}
