import NetworkExtension
import os.log

#if canImport(WireGuardKit)
import WireGuardKit
#endif

final class PacketTunnelProvider: NEPacketTunnelProvider {
    private let log = Logger(subsystem: "com.alalapi.privatetunnel", category: "PacketTunnel")
    #if canImport(WireGuardKit)
    private var adapter: WireGuardAdapter?
    #endif
    private var statusObserver: Any?
    private let engine = WGEngineReal()

    override func startTunnel(options: [String : NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        do {
            let configurationText = try loadActiveConfigurationText()
            let parsedConfig = try engine.parseConfiguration(configText: configurationText)
            #if canImport(WireGuardKit)
            let adapter = WireGuardAdapter(with: self) { [weak self] level, message in
                guard let self else { return }
                switch level {
                case .error:
                    self.log.error("WireGuard: \(message)")
                default:
                    self.log.debug("WireGuard: \(message)")
                }
            }
            self.adapter = adapter
            engine.attachAdapter(adapter)
            observeStatusChanges()
            engine.start(with: parsedConfig) { [weak self] error in
                guard let self else { return }
                if let error {
                    self.log.error("Failed to start engine: \(error.localizedDescription)")
                    KillSwitch.clear(on: self) {
                        completionHandler(error)
                    }
                    return
                }
                do {
                    let settings = try parsedConfig.generateNetworkSettings()
                    KillSwitch.apply(settings: settings, on: self) { applyError in
                        if let applyError {
                            self.log.error("Failed to apply network settings: \(applyError.localizedDescription)")
                            completionHandler(applyError)
                        } else {
                            completionHandler(nil)
                        }
                    }
                } catch {
                    self.log.error("Configuration to settings conversion failed: \(error.localizedDescription)")
                    KillSwitch.clear(on: self) {
                        completionHandler(error)
                    }
                }
            }
            #else
            KillSwitch.clear(on: self) {
                completionHandler(WGEngineError.missingDependency)
            }
            #endif
        } catch {
            KillSwitch.clear(on: self) {
                completionHandler(error)
            }
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        removeStatusObserver()
        engine.stop { [weak self] in
            guard let self else {
                completionHandler()
                return
            }
            #if canImport(WireGuardKit)
            self.adapter = nil
            #endif
            KillSwitch.clear(on: self) {
                completionHandler()
            }
        }
    }

    override func cancelTunnelWithError(_ error: Error?) {
        log.error("Tunnel cancelled with error: \(error?.localizedDescription ?? "unknown")")
        super.cancelTunnelWithError(error)
    }

    deinit {
        removeStatusObserver()
    }

    private func loadActiveConfigurationText() throws -> String {
        if let text = AppGroup.readActiveConfiguration() {
            return text
        }
        let debugURL = AppGroup.activeConfigURL.deletingLastPathComponent().appendingPathComponent("DEBUG_SAMPLE.conf")
        if let data = try? Data(contentsOf: debugURL), let string = String(data: data, encoding: .utf8) {
            return string
        }
        if let bundleURL = Bundle.main.url(forResource: "DEBUG_SAMPLE", withExtension: "conf"),
           let data = try? Data(contentsOf: bundleURL),
           let string = String(data: data, encoding: .utf8) {
            return string
        }
        throw WGEngineError.missingConfiguration
    }

    private func observeStatusChanges() {
        guard statusObserver == nil else { return }
        statusObserver = NotificationCenter.default.addObserver(forName: .NEVPNStatusDidChange, object: nil, queue: .main) { [weak self] notification in
            guard let self else { return }
            self.engine.updateConnectionStatus(self.connection.status)
        }
    }

    private func removeStatusObserver() {
        if let observer = statusObserver {
            NotificationCenter.default.removeObserver(observer)
            statusObserver = nil
        }
    }
}
