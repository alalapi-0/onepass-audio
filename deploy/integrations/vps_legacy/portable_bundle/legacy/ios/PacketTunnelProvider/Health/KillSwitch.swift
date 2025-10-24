//
//  KillSwitch.swift
//  PacketTunnelProvider
//
//  Implements a best-effort software kill switch for iOS. Apple does not expose
//  system-wide firewall controls to Network Extension providers so we cannot
//  guarantee that traffic leaks are impossible. Instead we pause packet
//  processing inside the tunnel engine and surface clear messaging to the
//  container application so the user can manually disable Wi-Fi/cellular when
//  desired.
//

import Foundation

struct KillSwitchState: Codable {
    let enabled: Bool
    let engaged: Bool
    let reason: String?
}

final class KillSwitch {
    private let queue = DispatchQueue(label: "com.privatetunnel.killswitch", qos: .utility)
    private var state = KillSwitchState(enabled: false, engaged: false, reason: nil)

    var onStateChange: ((KillSwitchState) -> Void)?

    func configure(enabled: Bool) {
        queue.async { [weak self] in
            guard let self else { return }
            self.state = KillSwitchState(enabled: enabled, engaged: self.state.engaged && enabled, reason: self.state.reason)
            self.notify()
        }
    }

    func engage(reason: String) {
        queue.async { [weak self] in
            guard let self else { return }
            guard self.state.enabled else { return }
            self.state = KillSwitchState(enabled: true, engaged: true, reason: reason)
            Logger.record(code: .eventKillSwitchEngaged, message: reason)
            self.notify()
        }
    }

    func disengage(reason: String? = nil) {
        queue.async { [weak self] in
            guard let self else { return }
            guard self.state.engaged else { return }
            self.state = KillSwitchState(enabled: self.state.enabled, engaged: false, reason: reason)
            Logger.record(code: .eventKillSwitchReleased, message: reason ?? "Kill switch released")
            self.notify()
        }
    }

    func currentState() -> KillSwitchState {
        queue.sync { state }
    }

    private func notify() {
        let current = state
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.onStateChange?(current)
        }
    }
}
