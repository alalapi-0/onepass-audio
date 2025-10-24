import Foundation
import NetworkExtension
import os.log

/// Helpers that keep the Packet Tunnel provider's network settings aligned
/// with the desired kill switch behaviour.
enum KillSwitch {
    private static let log = Logger(subsystem: "com.alalapi.privatetunnel", category: "KillSwitch")

    /// Applies the provided network settings to the running tunnel. Callers are
    /// responsible for invoking this method only after the WireGuard adapter
    /// successfully starts.
    static func apply(settings: NEPacketTunnelNetworkSettings,
                      on provider: NEPacketTunnelProvider,
                      completion: @escaping (Error?) -> Void) {
        log.debug("Applying tunnel network settings: \(settings.debugDescription)")
        provider.setTunnelNetworkSettings(settings) { error in
            if let error { log.error("Failed to apply tunnel settings: \(error.localizedDescription)") }
            completion(error)
        }
    }

    /// Clears any previously applied network settings. This method is invoked
    /// whenever the tunnel stops or fails to start, ensuring the system does not
    /// retain direct routing information (kill switch behaviour).
    static func clear(on provider: NEPacketTunnelProvider, completion: @escaping () -> Void) {
        log.debug("Clearing tunnel network settings")
        provider.setTunnelNetworkSettings(nil) { _ in completion() }
    }
}
