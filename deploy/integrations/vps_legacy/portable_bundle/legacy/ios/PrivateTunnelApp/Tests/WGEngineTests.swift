import NetworkExtension
import XCTest
@testable import PrivateTunnelApp

final class WGEngineTests: XCTestCase {
    private let sampleConfig = """
    [Interface]
    PrivateKey = vAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
    Address = 10.6.0.2/32, fd86:ea04:1111::2/128
    DNS = 1.1.1.1, 2606:4700:4700::1111
    MTU = 1280

    [Peer]
    PublicKey = xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
    AllowedIPs = 0.0.0.0/0, ::/0
    Endpoint = 203.0.113.10:51820
    PersistentKeepalive = 25
    """

    func testParseQuickConfigProducesAddressesAndDNS() throws {
        let config = try WireGuardQuickConfig(text: sampleConfig)
        XCTAssertEqual(config.interfaceAddresses.count, 2)
        let ipv4 = config.interfaceAddresses.first { $0.isIPv4 }
        XCTAssertEqual(ipv4?.address, "10.6.0.2")
        XCTAssertEqual(ipv4?.prefixLength, 32)
        XCTAssertEqual(config.dnsServers.first, "1.1.1.1")
        XCTAssertEqual(config.dnsServers.last, "2606:4700:4700::1111")
        XCTAssertEqual(config.peers.count, 1)
        XCTAssertEqual(config.peers.first?.allowedIPs.count, 2)
    }

    func testGenerateNetworkSettingsCoversDefaultRoutes() throws {
        let config = try WireGuardQuickConfig(text: sampleConfig)
        let settings = try config.generateNetworkSettings()
        let dnsServers = settings.dnsSettings?.servers ?? []
        XCTAssertTrue(dnsServers.contains("1.1.1.1"))
        XCTAssertTrue(dnsServers.contains("2606:4700:4700::1111"))
        let ipv4Routes = settings.ipv4Settings?.includedRoutes ?? []
        XCTAssertTrue(ipv4Routes.contains { $0.destinationAddress == "0.0.0.0" && $0.subnetMask == "0.0.0.0" })
        let ipv6Routes = settings.ipv6Settings?.includedRoutes ?? []
        XCTAssertTrue(ipv6Routes.contains { $0.destinationAddress == "::" })
        XCTAssertEqual(settings.mtu?.intValue, 1280)
    }

    func testEngineStartStopAndStats() throws {
        let runtime = """
        interface
        private_key=
        peer
        public_key=xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
        last_handshake_time_sec=1700000000
        rx_bytes=12345
        tx_bytes=98765
        """
        let config = try WireGuardQuickConfig(text: sampleConfig)
        let adapter = MockAdapter(runtimeConfiguration: runtime)
        let engine = WGEngineReal()
        engine.attachAdapter(adapter)

        let startExpectation = expectation(description: "start")
        engine.start(with: config) { error in
            XCTAssertNil(error)
            startExpectation.fulfill()
        }
        wait(for: [startExpectation], timeout: 1.0)
        XCTAssertEqual(engine.currentStatus, .connected)
        let statsExpectation = expectation(description: "stats")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
            statsExpectation.fulfill()
        }
        wait(for: [statsExpectation], timeout: 1.0)
        let stats = engine.stats()
        XCTAssertEqual(stats.bytesIn, 12345)
        XCTAssertEqual(stats.bytesOut, 98765)
        XCTAssertNotNil(stats.lastHandshakeTime)

        let reconfigureExpectation = expectation(description: "reconfigure")
        engine.reconfigure(with: config) { error in
            XCTAssertNil(error)
            reconfigureExpectation.fulfill()
        }
        wait(for: [reconfigureExpectation], timeout: 1.0)
        XCTAssertTrue(adapter.updateCalled)

        let stopExpectation = expectation(description: "stop")
        engine.stop {
            stopExpectation.fulfill()
        }
        wait(for: [stopExpectation], timeout: 1.0)
        XCTAssertEqual(engine.currentStatus, .disconnected)
    }
}

private final class MockAdapter: WireGuardAdapterControlling {
    private(set) var startCalled = false
    private(set) var stopCalled = false
    private(set) var updateCalled = false
    private let runtimeConfiguration: String?

    init(runtimeConfiguration: String?) {
        self.runtimeConfiguration = runtimeConfiguration
    }

    func start(tunnelConfiguration: WGTunnelConfiguration, completionHandler: @escaping (WGAdapterError?) -> Void) {
        startCalled = true
        DispatchQueue.main.async { completionHandler(nil) }
    }

    func stop(completionHandler: @escaping (WGAdapterError?) -> Void) {
        stopCalled = true
        DispatchQueue.main.async { completionHandler(nil) }
    }

    func update(tunnelConfiguration: WGTunnelConfiguration, completionHandler: @escaping (WGAdapterError?) -> Void) {
        updateCalled = true
        DispatchQueue.main.async { completionHandler(nil) }
    }

    func getRuntimeConfiguration(completionHandler: @escaping (String?) -> Void) {
        DispatchQueue.main.async { completionHandler(self.runtimeConfiguration) }
    }
}
