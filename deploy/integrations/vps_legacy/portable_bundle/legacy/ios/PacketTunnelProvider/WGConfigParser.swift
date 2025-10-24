//
//  WGConfigParser.swift
//  PacketTunnelProvider
//
//  Purpose: Converts the persisted JSON configuration into WGConfig and performs
//  minimal validation that mirrors the logic shipped in the container app.
//
import Foundation

enum WGConfigParserError: LocalizedError {
    case jsonDecodingFailed(String)
    case invalidHost
    case invalidPort
    case invalidAddress
    case invalidDNS(String)
    case missingAllowedIPs

    var errorDescription: String? {
        switch self {
        case .jsonDecodingFailed(let message):
            return "配置解析失败：\(message)"
        case .invalidHost:
            return "配置中的 endpoint.host 为空。"
        case .invalidPort:
            return "端口必须介于 1-65535。"
        case .invalidAddress:
            return "客户端地址必须是合法的 IPv4 CIDR。"
        case .invalidDNS(let value):
            return "DNS 地址无效：\(value)"
        case .missingAllowedIPs:
            return "全局模式下需要提供 AllowedIPs。"
        }
    }
}

enum WGConfigParser {
    static func parse(from data: Data) throws -> WGConfig {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .useDefaultKeys

        let config: WGConfig
        do {
            config = try decoder.decode(WGConfig.self, from: data)
        } catch {
            throw WGConfigParserError.jsonDecodingFailed(error.localizedDescription)
        }

        guard !config.endpoint.host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw WGConfigParserError.invalidHost
        }
        guard (1...65535).contains(config.endpoint.port) else {
            throw WGConfigParserError.invalidPort
        }

        guard isValidCIDR(config.client.address) else {
            throw WGConfigParserError.invalidAddress
        }

        for dns in config.client.dns {
            guard isValidIPv4(dns) else {
                throw WGConfigParserError.invalidDNS(dns)
            }
        }

        if config.routing.mode == .global {
            guard let allowed = config.routing.allowedIPs, !allowed.isEmpty else {
                throw WGConfigParserError.missingAllowedIPs
            }
            for cidr in allowed {
                guard isValidCIDR(cidr) else {
                    throw WGConfigParserError.jsonDecodingFailed("AllowedIPs 包含非法 CIDR：\(cidr)")
                }
            }
        } else {
            if let allowed = config.routing.allowedIPs {
                for cidr in allowed where !isValidCIDR(cidr) {
                    throw WGConfigParserError.jsonDecodingFailed("AllowedIPs 包含非法 CIDR：\(cidr)")
                }
            }
        }

        return config
    }

    private static func isValidCIDR(_ value: String) -> Bool {
        let components = value.split(separator: "/")
        guard components.count == 2,
              let prefix = Int(components[1]),
              (0...32).contains(prefix) else {
            return false
        }
        return isValidIPv4(String(components[0]))
    }

    private static func isValidIPv4(_ value: String) -> Bool {
        let octets = value.split(separator: ".")
        guard octets.count == 4 else { return false }
        return octets.allSatisfy { octet in
            guard let number = Int(octet) else { return false }
            return (0...255).contains(number)
        }
    }
}

extension WGConfig {
    func renderMinimalWireGuardConfig() -> String {
        var lines: [String] = []
        lines.append("[Interface]")
        lines.append("PrivateKey = \(client.privateKey)")
        lines.append("Address = \(client.address)")
        if !client.dns.isEmpty {
            lines.append("DNS = \(client.dns.joined(separator: ", "))")
        }
        if let mtu = client.mtu {
            lines.append("MTU = \(mtu)")
        }
        lines.append("")
        lines.append("[Peer]")
        lines.append("PublicKey = \(endpoint.publicKey)")
        lines.append("Endpoint = \(endpoint.host):\(endpoint.port)")
        if let allowed = routing.allowedIPs, !allowed.isEmpty {
            lines.append("AllowedIPs = \(allowed.joined(separator: ", "))")
        } else {
            lines.append("AllowedIPs = 0.0.0.0/0")
        }
        if let keepalive = client.keepalive {
            lines.append("PersistentKeepalive = \(keepalive)")
        }
        return lines.joined(separator: "\n")
    }
}
