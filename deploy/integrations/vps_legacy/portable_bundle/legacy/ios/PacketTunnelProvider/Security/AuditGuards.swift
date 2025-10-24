//
//  AuditGuards.swift
//  PacketTunnelProvider
//
//  Lightweight defensive checks executed before sensitive operations. The
//  guards favour transparency by logging security events without exposing
//  sensitive material.
//

import Foundation
import Network
import Security

enum AuditError: LocalizedError {
    case invalidPort
    case invalidAddress(String)
    case invalidAllowedIP(String)
    case missingKeyMaterial

    var errorDescription: String? {
        switch self {
        case .invalidPort:
            return "端口范围不合法，应在 1-65535 之间。"
        case .invalidAddress(let value):
            return "客户端地址不合法：\(value)"
        case .invalidAllowedIP(let value):
            return "路由表条目不合法：\(value)"
        case .missingKeyMaterial:
            return "密钥材料缺失或格式异常。"
        }
    }
}

enum AuditGuards {
    static func assertConfigSane(_ cfg: WGConfig) throws {
        guard (1...65535).contains(cfg.endpoint.port) else {
            Logger.log(level: .error, code: .errorAuditFailure, message: "Endpoint port outside allowed range", metadata: ["port": String(cfg.endpoint.port)])
            throw AuditError.invalidPort
        }

        guard !cfg.client.privateKey.isEmpty, !cfg.endpoint.publicKey.isEmpty else {
            Logger.log(level: .error, code: .errorAuditFailure, message: "Key material missing", metadata: nil)
            throw AuditError.missingKeyMaterial
        }

        if !isValidCIDR(cfg.client.address) {
            Logger.log(level: .error, code: .errorAuditFailure, message: "Client address invalid", metadata: ["address": cfg.client.address])
            throw AuditError.invalidAddress(cfg.client.address)
        }

        if let allowedIPs = cfg.routing.allowedIPs {
            for entry in allowedIPs where !isValidCIDR(entry) {
                Logger.log(level: .error, code: .errorAuditFailure, message: "Allowed IP invalid", metadata: ["entry": entry])
                throw AuditError.invalidAllowedIP(entry)
            }
        }

        if cfg.routing.mode == .whitelist {
            Logger.log(level: .warn, code: .eventEngineWaiting, message: "Whitelist routing enabled. Server side ipset controls egress; client still routes full traffic.")
        }

        Logger.logSecurity("Configuration audit passed", code: .securityConfigAudit, metadata: [
            "profile": cfg.profileName,
            "engine": cfg.engine.rawValue
        ])
    }

    static func assertKeychainAccess() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.privatetunnel.runtime",
            kSecAttrAccount as String: "diagnostic-probe",
            kSecReturnData as String: kCFBooleanFalse as Any,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]

        let status = SecItemCopyMatching(query as CFDictionary, nil)
        switch status {
        case errSecSuccess, errSecItemNotFound:
            Logger.logSecurity("Keychain probe succeeded", code: .securityKeychainAccess, metadata: nil)
        case errSecInteractionNotAllowed:
            Logger.log(level: .error, code: .errorAuditFailure, message: "Keychain interaction not allowed", metadata: nil)
            throw AuditError.missingKeyMaterial
        default:
            Logger.log(level: .error, code: .errorAuditFailure, message: "Keychain status \(status)", metadata: nil)
            throw AuditError.missingKeyMaterial
        }
    }

    private static func isValidCIDR(_ value: String) -> Bool {
        let parts = value.split(separator: "/")
        guard parts.count == 2, let prefix = UInt8(parts[1]), IPv4Address(String(parts[0])) != nil else {
            return false
        }
        return prefix <= 32
    }
}
