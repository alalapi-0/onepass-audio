//
//  ExportDiagnostics.swift
//  PrivateTunnelApp
//
//  Builds a redacted diagnostics archive combining recent container logs,
//  extension events and a configuration snapshot. The resulting zip can be
//  shared via the standard iOS share sheet without exposing private keys.
//

import Foundation
import UIKit

struct DiagnosticsSnapshot {
    let appLogs: [DiagnosticLogEntry]
    let extensionEvents: [ProviderStatus.Event]
    let activeConfig: TunnelConfig?
    let additionalFiles: [String: Data]

    init(appLogs: [DiagnosticLogEntry],
         extensionEvents: [ProviderStatus.Event],
         activeConfig: TunnelConfig?,
         additionalFiles: [String: Data] = [:]) {
        self.appLogs = appLogs
        self.extensionEvents = extensionEvents
        self.activeConfig = activeConfig
        self.additionalFiles = additionalFiles
    }
}

enum DiagnosticsExporterError: LocalizedError {
    case archiveCreationFailed

    var errorDescription: String? {
        switch self {
        case .archiveCreationFailed:
            return "无法生成诊断包，请稍后再试。"
        }
    }
}

final class ExportDiagnostics {
    static func buildArchive(from snapshot: DiagnosticsSnapshot) throws -> URL {
        let fm = FileManager.default
        let timestamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
        let workingDirectory = fm.temporaryDirectory.appendingPathComponent("PrivateTunnelDiag_\(timestamp)", isDirectory: true)

        if fm.fileExists(atPath: workingDirectory.path) {
            try? fm.removeItem(at: workingDirectory)
        }
        try fm.createDirectory(at: workingDirectory, withIntermediateDirectories: true)

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

        let appPayload = snapshot.appLogs.map { entry -> [String: Any] in
            [
                "timestamp": formatter.string(from: entry.timestamp),
                "level": entry.level.rawValue,
                "code": entry.code,
                "message": entry.message,
                "metadata": entry.metadata
            ]
        }
        let appLogsData = try JSONSerialization.data(withJSONObject: appPayload, options: [.prettyPrinted, .sortedKeys])
        try appLogsData.write(to: workingDirectory.appendingPathComponent("app_logs.json"))

        let extensionPayload = snapshot.extensionEvents.map { event -> [String: Any] in
            [
                "timestamp": formatter.string(from: event.timestamp),
                "level": event.level,
                "code": event.code,
                "message": event.message,
                "metadata": event.metadata
            ]
        }
        let extensionData = try JSONSerialization.data(withJSONObject: extensionPayload, options: [.prettyPrinted, .sortedKeys])
        try extensionData.write(to: workingDirectory.appendingPathComponent("extension_logs.json"))

        if let config = snapshot.activeConfig {
            let redacted = DiagnosticsRedactor.redact(config: config)
            let configData = try JSONSerialization.data(withJSONObject: redacted, options: [.prettyPrinted, .sortedKeys])
            try configData.write(to: workingDirectory.appendingPathComponent("config_snapshot.json"))
        }

        let infoText = buildInfoText()
        try infoText.data(using: .utf8)?.write(to: workingDirectory.appendingPathComponent("environment.txt"))

        for (name, data) in snapshot.additionalFiles {
            try data.write(to: workingDirectory.appendingPathComponent(name))
        }

        let archiveURL = fm.temporaryDirectory.appendingPathComponent("PrivateTunnel_Diagnostics_\(timestamp).zip")
        if fm.fileExists(atPath: archiveURL.path) {
            try? fm.removeItem(at: archiveURL)
        }

        do {
            try fm.zipItem(at: workingDirectory, to: archiveURL, shouldKeepParent: false, compressionMethod: .deflate)
        } catch {
            throw DiagnosticsExporterError.archiveCreationFailed
        }

        return archiveURL
    }

    private static func buildInfoText() -> String {
        let device = UIDevice.current
        let bundle = Bundle.main
        let version = bundle.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
        let build = bundle.infoDictionary?["CFBundleVersion"] as? String ?? "unknown"
        let lines: [String] = [
            "Generated at: \(ISO8601DateFormatter().string(from: Date()))",
            "App version: \(version) (\(build))",
            "Device: \(device.model)",
            "System: iOS \(device.systemVersion)",
            "Note: 日志已自动脱敏，不包含私钥原文。"
        ]
        return lines.joined(separator: "\n")
    }
}
