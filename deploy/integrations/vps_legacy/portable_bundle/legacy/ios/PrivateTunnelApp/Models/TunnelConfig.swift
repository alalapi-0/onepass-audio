//
//  TunnelConfig.swift
//  PrivateTunnel
//
//  Purpose: Defines the client tunnel configuration model matching core/config-schema.json with validation helpers.
//  Author: OpenAI Assistant
//  Created: 2024-05-15
//
//  Example:
//      let config = try TunnelConfig.from(jsonData: data)
//      try config.isValid()
//
import Foundation

struct TunnelConfig: Codable, Identifiable {
    struct Endpoint: Codable {
        var host: String
        var port: Int
        var public_key: String
        var ipv6: Bool
    }

    struct Client: Codable {
        var private_key: String
        var address: String
        var dns: [String]
        var mtu: Int?
        var keepalive: Int?
    }

    struct Routing: Codable {
        var mode: String
        var allowed_ips: [String]?
        var whitelist_domains: [String]?
    }

    struct ValidationError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    var id = UUID()
    var version: String
    var profile_name: String
    var endpoint: Endpoint
    var client: Client
    var routing: Routing
    var notes: String?
    var enable_kill_switch: Bool

    enum CodingKeys: String, CodingKey {
        case version
        case profile_name
        case endpoint
        case client
        case routing
        case notes
        case enable_kill_switch = "enable_kill_switch"
    }

    init(version: String, profile_name: String, endpoint: Endpoint, client: Client, routing: Routing, notes: String? = nil, enable_kill_switch: Bool = false) {
        self.version = version
        self.profile_name = profile_name
        self.endpoint = endpoint
        self.client = client
        self.routing = routing
        self.notes = notes
        self.enable_kill_switch = enable_kill_switch
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        version = try container.decode(String.self, forKey: .version)
        profile_name = try container.decode(String.self, forKey: .profile_name)
        endpoint = try container.decode(Endpoint.self, forKey: .endpoint)
        client = try container.decode(Client.self, forKey: .client)
        routing = try container.decode(Routing.self, forKey: .routing)
        notes = try container.decodeIfPresent(String.self, forKey: .notes)
        enable_kill_switch = try container.decodeIfPresent(Bool.self, forKey: .enable_kill_switch) ?? false
        id = UUID()
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(version, forKey: .version)
        try container.encode(profile_name, forKey: .profile_name)
        try container.encode(endpoint, forKey: .endpoint)
        try container.encode(client, forKey: .client)
        try container.encode(routing, forKey: .routing)
        try container.encodeIfPresent(notes, forKey: .notes)
        try container.encode(enable_kill_switch, forKey: .enable_kill_switch)
    }

    static func from(jsonData: Data) throws -> TunnelConfig {
        let decoder = JSONDecoder()
        return try decoder.decode(TunnelConfig.self, from: jsonData)
    }

    static func from(jsonString: String) throws -> TunnelConfig {
        guard let data = jsonString.data(using: .utf8) else {
            throw ValidationError(message: "二维码内容不是有效的 UTF-8 文本。")
        }
        return try from(jsonData: data)
    }

    @discardableResult
    func isValid() throws -> Bool {
        guard version == "1" else {
            throw ValidationError(message: "仅支持版本号为 1 的配置。")
        }
        guard !profile_name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw ValidationError(message: "Profile Name 不能为空。")
        }
        guard !endpoint.host.isEmpty else {
            throw ValidationError(message: "Endpoint 主机地址不能为空。")
        }
        guard (1...65535).contains(endpoint.port) else {
            throw ValidationError(message: "端口必须在 1-65535 范围内。")
        }
        guard endpoint.public_key.count >= 44 else {
            throw ValidationError(message: "服务器公钥长度异常，请确认 Base64 字符串是否完整。")
        }
        guard client.private_key.count >= 44 else {
            throw ValidationError(message: "客户端私钥长度异常，请确认 Base64 字符串是否完整。")
        }
        guard isValidCIDR(client.address) else {
            throw ValidationError(message: "客户端地址必须是合法的 IPv4 CIDR，例如 10.0.0.2/32。")
        }
        guard !client.dns.isEmpty else {
            throw ValidationError(message: "DNS 服务器列表不能为空。")
        }
        if let mtu = client.mtu {
            guard (576...9200).contains(mtu) else {
                throw ValidationError(message: "MTU 必须介于 576-9200。")
            }
        }
        if let keepalive = client.keepalive {
            guard (0...65535).contains(keepalive) else {
                throw ValidationError(message: "Keepalive 必须介于 0-65535。")
            }
        }
        guard ["global", "whitelist"].contains(routing.mode) else {
            throw ValidationError(message: "routing.mode 必须是 global 或 whitelist。")
        }
        if routing.mode == "global" {
            guard let allowed = routing.allowed_ips, !allowed.isEmpty else {
                throw ValidationError(message: "global 模式下必须提供 AllowedIPs。")
            }
            for value in allowed {
                guard isValidCIDR(value) else {
                    throw ValidationError(message: "AllowedIPs 中存在非法 IPv4 CIDR。")
                }
            }
        }
        if routing.mode == "whitelist" {
            guard let domains = routing.whitelist_domains, !domains.isEmpty else {
                throw ValidationError(message: "whitelist 模式下必须提供域名列表。")
            }
        }
        return true
    }

    private func isValidCIDR(_ cidr: String) -> Bool {
        let components = cidr.split(separator: "/")
        guard components.count == 2,
              let prefixLength = Int(components[1]),
              (0...32).contains(prefixLength) else {
            return false
        }
        let octets = components[0].split(separator: ".")
        guard octets.count == 4 else { return false }
        return octets.allSatisfy { octet in
            if let value = Int(octet), (0...255).contains(value) {
                return true
            }
            return false
        }
    }
}
