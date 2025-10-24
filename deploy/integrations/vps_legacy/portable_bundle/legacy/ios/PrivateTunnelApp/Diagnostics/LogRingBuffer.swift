//
//  LogRingBuffer.swift
//  PrivateTunnelApp
//
//  Provides an in-memory ring buffer to retain recent diagnostic events surfaced
//  by both the container app and the PacketTunnel extension. The data structure
//  is optimised for small bounded histories (default 500 entries) and supports
//  concurrent reads while mutations are serialised via a barrier queue.
//

import Foundation

enum DiagnosticLogLevel: String, Codable, CaseIterable {
    case info = "INFO"
    case warn = "WARN"
    case error = "ERROR"
    case security = "SECURITY"

    var symbolName: String {
        switch self {
        case .info:
            return "info.circle"
        case .warn:
            return "exclamationmark.triangle"
        case .error:
            return "xmark.octagon"
        case .security:
            return "lock.shield"
        }
    }
}

struct DiagnosticLogEntry: Identifiable, Codable {
    let id = UUID()
    let timestamp: Date
    let level: DiagnosticLogLevel
    let code: String
    let message: String
    let metadata: [String: String]
}

final class LogRingBuffer {
    private let capacity: Int
    private var storage: [DiagnosticLogEntry] = []
    private let queue = DispatchQueue(label: "com.privatetunnel.app.logbuffer", attributes: .concurrent)

    init(capacity: Int = 500) {
        self.capacity = max(50, capacity)
    }

    func append(level: DiagnosticLogLevel, code: String, message: String, metadata: [String: String] = [:]) {
        let entry = DiagnosticLogEntry(timestamp: Date(), level: level, code: code, message: message, metadata: metadata)
        append(entry)
    }

    func append(_ entry: DiagnosticLogEntry) {
        queue.async(flags: .barrier) { [weak self] in
            guard let self else { return }
            self.storage.append(entry)
            if self.storage.count > self.capacity {
                self.storage.removeFirst(self.storage.count - self.capacity)
            }
        }
    }

    func recent(limit: Int? = nil) -> [DiagnosticLogEntry] {
        queue.sync {
            if let limit, limit > 0 {
                return Array(storage.suffix(limit))
            }
            return storage
        }
    }

    func exportSnapshot() -> [DiagnosticLogEntry] {
        recent()
    }

    func clear() {
        queue.async(flags: .barrier) { [weak self] in
            self?.storage.removeAll()
        }
    }
}
