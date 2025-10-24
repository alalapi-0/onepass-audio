import Foundation
import NetworkExtension

#if canImport(WireGuardKit)
import WireGuardKit
#endif

/// Wrapper around a wg-quick configuration file. The wrapper holds a reference
/// to the native `TunnelConfiguration` object (when WireGuardKit is linked) and
/// exposes convenient accessors for the Network Extension target.
public struct WireGuardQuickConfig {
    public struct PeerSummary: Equatable {
        public let publicKey: String
        public let allowedIPs: [CIDRAddress]
        public let endpoint: String?
        public let persistentKeepAlive: UInt16?
    }

    public let rawText: String
    public let interfaceAddresses: [CIDRAddress]
    public let dnsServers: [String]
    public let dnsSearchDomains: [String]
    public let peers: [PeerSummary]
    public let mtu: UInt16?

    let nativeConfiguration: WGTunnelConfiguration

    public init(text: String) throws {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw WGEngineError.missingConfiguration }
        #if canImport(WireGuardKit)
        let parser = WgQuickConfigFileParser(text: trimmed)
        let config = try parser.parse()
        let interface = config.interface
        self.rawText = trimmed
        self.nativeConfiguration = config
        self.interfaceAddresses = interface.addresses.compactMap { CIDRAddress(string: $0.stringRepresentation) }
        self.dnsServers = interface.dns.map { $0.stringRepresentation }
        self.dnsSearchDomains = interface.dnsSearch
        self.mtu = interface.mtu
        self.peers = config.peers.map { peer in
            let allowed = peer.allowedIPs.compactMap { CIDRAddress(string: $0.stringRepresentation) }
            return PeerSummary(publicKey: peer.publicKey.base64Key,
                                allowedIPs: allowed,
                                endpoint: peer.endpoint?.stringRepresentation,
                                persistentKeepAlive: peer.persistentKeepAlive)
        }
        #else
        let parser = MiniWgQuickParser(text: trimmed)
        let result = try parser.parse()
        self.rawText = trimmed
        self.nativeConfiguration = result.configuration
        self.interfaceAddresses = result.interface.addresses.compactMap { CIDRAddress(string: $0) }
        self.dnsServers = result.interface.dns
        self.dnsSearchDomains = result.interface.dnsSearch
        self.mtu = result.interface.mtu
        self.peers = result.peers.map { peer in
            let allowed = peer.allowedIPs.compactMap { CIDRAddress(string: $0) }
            return PeerSummary(publicKey: peer.publicKey,
                                allowedIPs: allowed,
                                endpoint: peer.endpoint,
                                persistentKeepAlive: peer.persistentKeepAlive)
        }
        #endif
    }

    /// Convenience accessor for the native configuration used by the adapter.
    public var configuration: WGTunnelConfiguration { nativeConfiguration }

    /// Generates the `NEPacketTunnelNetworkSettings` required to bring the
    /// interface up. The settings apply a kill-switch friendly configuration by
    /// leaving the system without routes until the tunnel is ready.
    public func generateNetworkSettings() throws -> NEPacketTunnelNetworkSettings {
        let remote = peers.first?.endpointHost ?? "127.0.0.1"
        let networkSettings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: remote)

        if !dnsServers.isEmpty {
            let dns = NEDNSSettings(servers: dnsServers)
            dns.matchDomains = [""]
            if !dnsSearchDomains.isEmpty {
                dns.searchDomains = dnsSearchDomains
            }
            networkSettings.dnsSettings = dns
        }

        if !interfaceAddresses.filter({ $0.isIPv4 }).isEmpty {
            let ipv4Addresses = interfaceAddresses.filter { $0.isIPv4 }
            let addresses = ipv4Addresses.map { $0.address }
            let masks = ipv4Addresses.map { $0.subnetMaskString }
            let ipv4Settings = NEIPv4Settings(addresses: addresses, subnetMasks: masks)
            let routes = routesForIPv4()
            ipv4Settings.includedRoutes = routes
            networkSettings.ipv4Settings = ipv4Settings
        }

        if !interfaceAddresses.filter({ $0.isIPv6 }).isEmpty {
            let ipv6Addresses = interfaceAddresses.filter { $0.isIPv6 }
            let addresses = ipv6Addresses.map { $0.address }
            let prefixes = ipv6Addresses.map { NSNumber(value: $0.prefixLength) }
            let ipv6Settings = NEIPv6Settings(addresses: addresses, networkPrefixLengths: prefixes)
            let routes = routesForIPv6()
            ipv6Settings.includedRoutes = routes
            networkSettings.ipv6Settings = ipv6Settings
        }

        if let mtu { networkSettings.mtu = NSNumber(value: mtu) }
        return networkSettings
    }

    private func routesForIPv4() -> [NEIPv4Route] {
        let allowed = peers.flatMap { $0.allowedIPs }.filter { $0.isIPv4 }
        if allowed.isEmpty {
            return [NEIPv4Route(destinationAddress: "0.0.0.0", subnetMask: "0.0.0.0")]
        }
        return allowed.map { address in
            NEIPv4Route(destinationAddress: address.address, subnetMask: address.subnetMaskString)
        }
    }

    private func routesForIPv6() -> [NEIPv6Route] {
        let allowed = peers.flatMap { $0.allowedIPs }.filter { $0.isIPv6 }
        if allowed.isEmpty {
            return [NEIPv6Route(destinationAddress: "::", networkPrefixLength: NSNumber(value: 0))]
        }
        return allowed.map { address in
            NEIPv6Route(destinationAddress: address.address, networkPrefixLength: NSNumber(value: address.prefixLength))
        }
    }
}

public struct CIDRAddress: Equatable {
    public let address: String
    public let prefixLength: Int

    public init(address: String, prefixLength: Int) {
        self.address = address
        self.prefixLength = prefixLength
    }

    init?(string: String) {
        let trimmed = string.trimmingCharacters(in: .whitespaces)
        let parts = trimmed.split(separator: "/", maxSplits: 1).map(String.init)
        guard let addressPart = parts.first else { return nil }
        let prefix: Int
        if parts.count == 2 {
            prefix = Int(parts[1]) ?? (CIDRAddress.isIPv4(addressPart) ? 32 : 128)
        } else {
            prefix = CIDRAddress.isIPv4(addressPart) ? 32 : 128
        }
        self.address = addressPart
        self.prefixLength = prefix
    }

    public var isIPv4: Bool { CIDRAddress.isIPv4(address) }
    public var isIPv6: Bool { !isIPv4 }

    public var subnetMaskString: String {
        guard isIPv4 else { return "0.0.0.0" }
        let mask = prefixLength == 0 ? UInt32(0) : UInt32.max << (32 - UInt32(prefixLength))
        let a = (mask >> 24) & 0xff
        let b = (mask >> 16) & 0xff
        let c = (mask >> 8) & 0xff
        let d = mask & 0xff
        return "\(a).\(b).\(c).\(d)"
    }

    private static func isIPv4(_ address: String) -> Bool {
        return address.contains(".") && !address.contains(":")
    }
}

#if !canImport(WireGuardKit)
private struct MiniWgQuickParser {
    struct ParsedResult {
        let configuration: WGTunnelConfiguration
        let interface: WGInterfaceConfiguration
        let peers: [WGPeerConfiguration]
    }

    let text: String

    func parse() throws -> ParsedResult {
        enum Section { case none, interface, peer }
        var interfaceAttributes: [String: String] = [:]
        var peers: [[String: String]] = []
        var currentPeer: [String: String] = [:]
        var section: Section = .none

        func flushPeer() {
            if !currentPeer.isEmpty {
                peers.append(currentPeer)
                currentPeer.removeAll()
            }
        }

        for rawLine in text.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") || line.hasPrefix(";") {
                continue
            }
            if line.caseInsensitiveCompare("[Interface]") == .orderedSame {
                flushPeer()
                section = .interface
                continue
            }
            if line.caseInsensitiveCompare("[Peer]") == .orderedSame {
                flushPeer()
                section = .peer
                continue
            }
            let parts = line.split(separator: "=", maxSplits: 1)
            guard parts.count == 2 else { continue }
            let key = parts[0].trimmingCharacters(in: .whitespaces)
            let value = parts[1].trimmingCharacters(in: .whitespaces)
            switch section {
            case .interface:
                interfaceAttributes[key] = value
            case .peer:
                currentPeer[key] = value
            case .none:
                continue
            }
        }
        flushPeer()

        guard let privateKey = interfaceAttributes["PrivateKey"], !privateKey.isEmpty else {
            throw WGEngineError.invalidConfiguration("Missing PrivateKey in Interface section")
        }
        let addresses = interfaceAttributes["Address"].map { $0.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) } } ?? []
        let dns = interfaceAttributes["DNS"].map { $0.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) } } ?? []
        let dnsSearch = interfaceAttributes["DNSSearch"].map { $0.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) } } ?? []
        let mtu = interfaceAttributes["MTU"].flatMap { UInt16($0) }
        var interface = WGInterfaceConfiguration(addresses: addresses, dns: dns, dnsSearch: dnsSearch, mtu: mtu)
        interface.addresses = addresses
        interface.dns = dns
        interface.dnsSearch = dnsSearch

        let peerModels: [WGPeerConfiguration] = peers.compactMap { entry in
            guard let publicKey = entry["PublicKey"] else { return nil }
            let allowedIPs = entry["AllowedIPs"].map { $0.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) } } ?? []
            let endpoint = entry["Endpoint"]
            let keepAlive = entry["PersistentKeepalive"].flatMap { UInt16($0) }
            return WGPeerConfiguration(publicKey: publicKey, allowedIPs: allowedIPs, endpoint: endpoint, persistentKeepAlive: keepAlive)
        }

        let configuration = WGTunnelConfiguration(name: nil, interface: interface, peers: peerModels)
        return ParsedResult(configuration: configuration, interface: interface, peers: peerModels)
    }
}
#endif

private extension WireGuardQuickConfig.PeerSummary {
    var endpointHost: String? {
        guard let endpoint else { return nil }
        if endpoint.hasPrefix("[") { // IPv6 literal
            if let closing = endpoint.firstIndex(of: "]") {
                return String(endpoint[endpoint.index(after: endpoint.startIndex)..<closing])
            }
        }
        if let colonIndex = endpoint.lastIndex(of: ":"), endpoint[endpoint.index(before: colonIndex)] != "]" {
            return String(endpoint[..<colonIndex])
        }
        return endpoint
    }
}
