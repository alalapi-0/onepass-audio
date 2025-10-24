import Foundation

/// Convenience namespace exposing the application group identifiers used by the
/// container application and its Network Extension targets.
enum AppGroup {
    /// The shared app group identifier. Update this value to match the value
    /// configured in the Xcode project if it changes.
    static let identifier = "group.com.alalapi.privatetunnel"

    /// The root container URL for the shared app group. The directory is
    /// lazily created so callers may safely assume the URL exists on disk.
    static var containerURL: URL {
        if let url = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier) {
            ensureDirectoryExists(at: url)
            return url
        }
        let fallback = FileManager.default.temporaryDirectory.appendingPathComponent("privatetunnel-app-group", isDirectory: true)
        ensureDirectoryExists(at: fallback)
        return fallback
    }

    /// Location of the active WireGuard configuration file. The file follows
    /// the wg-quick format and is consumed by the Packet Tunnel provider.
    static var activeConfigURL: URL {
        let configsDirectory = containerURL.appendingPathComponent("configs", isDirectory: true)
        ensureDirectoryExists(at: configsDirectory)
        return configsDirectory.appendingPathComponent("active.conf", isDirectory: false)
    }

    /// Ensures that the provided directory exists, creating it recursively if
    /// needed. Errors are ignored because the caller will surface them when
    /// attempting to read/write.
    private static func ensureDirectoryExists(at directoryURL: URL) {
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: directoryURL.path, isDirectory: &isDirectory) {
            return
        }
        try? FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
    }
}

extension AppGroup {
    /// Reads the contents of the active configuration file, returning `nil`
    /// when the file does not exist or cannot be decoded as UTF-8 text.
    static func readActiveConfiguration() -> String? {
        guard let data = try? Data(contentsOf: activeConfigURL) else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    /// Writes the provided WireGuard configuration text to the active
    /// configuration location. Errors are surfaced to the caller so they may be
    /// handled appropriately.
    @discardableResult
    static func writeActiveConfiguration(_ text: String) throws -> URL {
        let data = Data(text.utf8)
        try data.write(to: activeConfigURL, options: [.atomic])
        return activeConfigURL
    }
}
