//
//  ProviderStatus.swift
//  PrivateTunnel
//
//  Mirrors the JSON status payload returned by PacketTunnelProvider so the
//  container app can surface health/reconnect information.
//

import Foundation

struct ProviderStatus: Decodable {
    struct Health: Decodable {
        let state: String
        let consecutiveFails: Int
        let consecutiveSuccess: Int
        let lastSuccessAt: Date?
        let lastFailureAt: Date?
        let reasonCode: String
        let reasonMessage: String

        private enum CodingKeys: String, CodingKey {
            case state
            case consecutiveFails = "consecutive_fails"
            case consecutiveSuccess = "consecutive_success"
            case lastSuccessAt = "last_success_at"
            case lastFailureAt = "last_failure_at"
            case reasonCode = "reason_code"
            case reasonMessage = "reason_message"
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            state = try container.decode(String.self, forKey: .state)
            consecutiveFails = try container.decode(Int.self, forKey: .consecutiveFails)
            consecutiveSuccess = try container.decode(Int.self, forKey: .consecutiveSuccess)
            reasonCode = try container.decode(String.self, forKey: .reasonCode)
            reasonMessage = try container.decode(String.self, forKey: .reasonMessage)
            let formatter = ProviderStatus.isoFormatter
            if let successString = try container.decodeIfPresent(String.self, forKey: .lastSuccessAt) {
                lastSuccessAt = formatter.date(from: successString)
            } else {
                lastSuccessAt = nil
            }
            if let failureString = try container.decodeIfPresent(String.self, forKey: .lastFailureAt) {
                lastFailureAt = formatter.date(from: failureString)
            } else {
                lastFailureAt = nil
            }
        }
    }

    struct Reconnect: Decodable {
        let attempts: Int
        let lastStartedAt: Date?
        let nextRetryIn: TimeInterval?
        let lastDelay: TimeInterval?

        private enum CodingKeys: String, CodingKey {
            case attempts
            case lastStartedAt = "last_started_at"
            case nextRetryIn = "next_retry_in"
            case lastDelay = "last_delay"
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            attempts = try container.decodeIfPresent(Int.self, forKey: .attempts) ?? 0
            if let value = try container.decodeIfPresent(String.self, forKey: .lastStartedAt) {
                lastStartedAt = ProviderStatus.isoFormatter.date(from: value)
            } else {
                lastStartedAt = nil
            }
            nextRetryIn = try container.decodeIfPresent(Double.self, forKey: .nextRetryIn)
            lastDelay = try container.decodeIfPresent(Double.self, forKey: .lastDelay)
        }
    }

    struct KillSwitch: Decodable {
        let enabled: Bool
        let engaged: Bool
        let reason: String

        private enum CodingKeys: String, CodingKey {
            case enabled
            case engaged
            case reason
        }
    }

    struct EngineStats: Decodable {
        let txPackets: UInt64
        let rxPackets: UInt64
        let txBytes: UInt64
        let rxBytes: UInt64
        let lastAliveAt: Date?
        let endpoint: String
        let heartbeatsMissed: Int

        private enum CodingKeys: String, CodingKey {
            case txPackets = "tx_packets"
            case rxPackets = "rx_packets"
            case txBytes = "tx_bytes"
            case rxBytes = "rx_bytes"
            case lastAliveAt = "last_alive_at"
            case endpoint
            case heartbeatsMissed = "heartbeats_missed"
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            txPackets = try container.decodeIfPresent(UInt64.self, forKey: .txPackets) ?? 0
            rxPackets = try container.decodeIfPresent(UInt64.self, forKey: .rxPackets) ?? 0
            txBytes = try container.decodeIfPresent(UInt64.self, forKey: .txBytes) ?? 0
            rxBytes = try container.decodeIfPresent(UInt64.self, forKey: .rxBytes) ?? 0
            endpoint = try container.decodeIfPresent(String.self, forKey: .endpoint) ?? ""
            heartbeatsMissed = try container.decodeIfPresent(Int.self, forKey: .heartbeatsMissed) ?? 0
            if let value = try container.decodeIfPresent(String.self, forKey: .lastAliveAt) {
                lastAliveAt = ProviderStatus.isoFormatter.date(from: value)
            } else {
                lastAliveAt = nil
            }
        }
    }

    struct Event: Decodable, Identifiable {
        let id = UUID()
        let timestamp: Date
        let code: String
        let message: String
        let level: String
        let metadata: [String: String]

        private enum CodingKeys: String, CodingKey {
            case timestamp
            case code
            case message
            case level
            case metadata
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            code = try container.decode(String.self, forKey: .code)
            message = try container.decode(String.self, forKey: .message)
            level = try container.decodeIfPresent(String.self, forKey: .level) ?? "INFO"
            metadata = try container.decodeIfPresent([String: String].self, forKey: .metadata) ?? [:]
            let raw = try container.decode(String.self, forKey: .timestamp)
            timestamp = ProviderStatus.isoFormatter.date(from: raw) ?? Date()
        }
    }

    let status: Int
    let profileName: String?
    let engine: String?
    let health: Health?
    let reconnect: Reconnect?
    let killSwitch: KillSwitch?
    let engineStats: EngineStats?
    let events: [Event]

    private enum CodingKeys: String, CodingKey {
        case status
        case profileName = "profile_name"
        case engine
        case health
        case reconnect
        case killSwitch = "kill_switch"
        case engineStats = "engine_stats"
        case events
    }

    static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}
