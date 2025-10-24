//
//  WGConfig.swift
//  PacketTunnelProvider
//
//  Purpose: Defines the WireGuard client configuration model consumed by the
//  Network Extension. The schema mirrors the JSON payload stored by the container
//  app and aligns with core/tools/generate_wg_conf.py.
//
import Foundation

struct WGConfig: Decodable {
    struct Endpoint: Decodable {
        let host: String
        let port: Int
        let publicKey: String

        enum CodingKeys: String, CodingKey {
            case host
            case port
            case publicKey = "public_key"
        }
    }

    struct Client: Decodable {
        let privateKey: String
        let address: String
        let dns: [String]
        let mtu: Int?
        let keepalive: Int?

        enum CodingKeys: String, CodingKey {
            case privateKey = "private_key"
            case address
            case dns
            case mtu
            case keepalive
        }
    }

    struct Routing: Decodable {
        enum Mode: String, Decodable {
            case global
            case whitelist
        }

        let mode: Mode
        let allowedIPs: [String]?
        let whitelistDomains: [String]?

        enum CodingKeys: String, CodingKey {
            case mode
            case allowedIPs = "allowed_ips"
            case whitelistDomains = "whitelist_domains"
        }
    }

    enum Engine: String, Decodable {
        case mock
        case toy
        case wireguard
    }

    let profileName: String
    let endpoint: Endpoint
    let client: Client
    let routing: Routing
    let enableKillSwitch: Bool
    let engine: Engine

    enum CodingKeys: String, CodingKey {
        case profileName = "profile_name"
        case endpoint
        case client
        case routing
        case enableKillSwitch = "enable_kill_switch"
        case engine
    }

    init(profileName: String, endpoint: Endpoint, client: Client, routing: Routing, enableKillSwitch: Bool = false, engine: Engine = .mock) {
        self.profileName = profileName
        self.endpoint = endpoint
        self.client = client
        self.routing = routing
        self.enableKillSwitch = enableKillSwitch
        self.engine = engine
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        profileName = try container.decode(String.self, forKey: .profileName)
        endpoint = try container.decode(Endpoint.self, forKey: .endpoint)
        client = try container.decode(Client.self, forKey: .client)
        routing = try container.decode(Routing.self, forKey: .routing)
        enableKillSwitch = try container.decodeIfPresent(Bool.self, forKey: .enableKillSwitch) ?? false
        engine = try container.decodeIfPresent(Engine.self, forKey: .engine) ?? .mock
    }
}
