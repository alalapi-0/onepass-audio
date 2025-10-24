//
//  DiagnosticsRedactorTests.swift
//  PrivateTunnelAppTests
//
//  Ensures container-side redaction mirrors the extension logic.
//

import XCTest
@testable import PrivateTunnelApp

final class DiagnosticsRedactorTests: XCTestCase {
    func testConfigRedaction() {
        let endpoint = TunnelConfig.Endpoint(host: "gateway.example.com", port: 51820, public_key: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", ipv6: false)
        let client = TunnelConfig.Client(private_key: "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=", address: "10.10.10.2/32", dns: ["1.1.1.1"], mtu: nil, keepalive: nil)
        let routing = TunnelConfig.Routing(mode: "global", allowed_ips: ["0.0.0.0/0"], whitelist_domains: nil)
        let config = TunnelConfig(version: "1", profile_name: "Office", endpoint: endpoint, client: client, routing: routing, notes: "测试", enable_kill_switch: true)
        let redacted = DiagnosticsRedactor.redact(config: config)
        let endpointDict = redacted["endpoint"] as? [String: Any]
        XCTAssertEqual(endpointDict?["public_key"] as? String, "***KEY_REDACTED***")
        XCTAssertTrue((endpointDict?["host"] as? String)?.contains("ga*******") ?? false)
        XCTAssertEqual((redacted["client"] as? [String: Any])?["private_key"] as? String, "***KEY_REDACTED***")
    }
}
