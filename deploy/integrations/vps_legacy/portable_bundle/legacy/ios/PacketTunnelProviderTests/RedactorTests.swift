//
//  RedactorTests.swift
//  PacketTunnelProviderTests
//
//  Minimal XCTest cases to ensure the redaction logic masks sensitive material.
//

import XCTest
@testable import PacketTunnelProvider

final class RedactorTests: XCTestCase {
    func testKeyRedaction() {
        let sample = "private_key=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+/=="
        let output = Redactor.redact(string: sample)
        XCTAssertFalse(output.contains("AbCdEf"))
        XCTAssertTrue(output.contains("***KEY_REDACTED***"))
    }

    func testTokenRedaction() {
        let sample = "Authorization Bearer abcdefghijk1234567890"
        let output = Redactor.redact(string: sample)
        XCTAssertTrue(output.contains("AUTHORIZATION ***TOKEN***"))
    }

    func testEndpointRedaction() {
        let sample = "endpoint=toy-gateway.example.com:51820"
        let output = Redactor.redact(string: sample)
        XCTAssertTrue(output.contains("to*********.*******.com"))
    }

    func testIPRedaction() {
        let sample = "Connecting to 192.168.10.55"
        let output = Redactor.redact(string: sample)
        XCTAssertTrue(output.contains("***.***.10.55"))
    }
}
