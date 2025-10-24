//
//  Redactor.swift
//  PacketTunnelProvider
//
//  Centralises redaction rules so both log emission and diagnostics export use
//  the same masking policy. The routines intentionally favour deterministic
//  replacements over perfect detection to ensure sensitive values never leak.
//

import Foundation

struct Redactor {
    private static let keyRegex: NSRegularExpression = {
        let pattern = "(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{32,64}(?![A-Za-z0-9+/=])"
        return try! NSRegularExpression(pattern: pattern, options: [])
    }()

    private static let tokenRegex: NSRegularExpression = {
        let pattern = "(?i)(bearer|token|authorization)\\s+([A-Za-z0-9._\-]+)"
        return try! NSRegularExpression(pattern: pattern, options: [])
    }()

    private static let domainRegex: NSRegularExpression = {
        let pattern = "(?<!@)(?:[A-Za-z0-9-]{1,63}\\.)+[A-Za-z]{2,}"
        return try! NSRegularExpression(pattern: pattern, options: [])
    }()

    private static let ipRegex: NSRegularExpression = {
        let pattern = "\\b((?:\\d{1,3}\\.){3}\\d{1,3})\\b"
        return try! NSRegularExpression(pattern: pattern, options: [])
    }()

    private static let pathRegex: NSRegularExpression = {
        let pattern = "(?<![A-Za-z0-9])(/[^\\s:]+)"
        return try! NSRegularExpression(pattern: pattern, options: [])
    }()

    static func redact(string: String) -> String {
        guard !string.isEmpty else { return string }
        var value = string
        value = replace(regex: keyRegex, in: value) { _ in "***KEY_REDACTED***" }
        value = replace(regex: tokenRegex, in: value) { match in
            guard match.numberOfRanges >= 3 else { return "***TOKEN***" }
            let prefixRange = match.range(at: 1)
            let prefix = (value as NSString).substring(with: prefixRange)
            return "\(prefix.uppercased()) ***TOKEN***"
        }
        value = replace(regex: domainRegex, in: value) { match in
            let domain = (value as NSString).substring(with: match.range)
            return redactDomain(domain)
        }
        value = replace(regex: ipRegex, in: value) { match in
            let ip = (value as NSString).substring(with: match.range(at: 1))
            return redactIPv4(ip)
        }
        value = replace(regex: pathRegex, in: value) { match in
            let path = (value as NSString).substring(with: match.range(at: 1))
            guard !path.contains("//") else { return path }
            return "/\((path as NSString).lastPathComponent)"
        }
        return value
    }

    static func redact(dict: [String: String]) -> [String: String] {
        var result: [String: String] = [:]
        for (key, value) in dict {
            result[key] = redact(string: value)
        }
        return result
    }

    private static func replace(regex: NSRegularExpression, in string: String, transform: (NSTextCheckingResult) -> String) -> String {
        let nsString = string as NSString
        var mutable = string
        var offset = 0
        regex.enumerateMatches(in: string, options: [], range: NSRange(location: 0, length: nsString.length)) { result, _, _ in
            guard let result else { return }
            let replacement = transform(result)
            let adjustedRange = NSRange(location: result.range.location + offset, length: result.range.length)
            let startIndex = mutable.index(mutable.startIndex, offsetBy: adjustedRange.location)
            let endIndex = mutable.index(startIndex, offsetBy: adjustedRange.length)
            mutable.replaceSubrange(startIndex..<endIndex, with: replacement)
            offset += replacement.count - result.range.length
        }
        return mutable
    }

    private static func redactDomain(_ domain: String) -> String {
        let parts = domain.split(separator: ".")
        guard parts.count >= 2 else { return domain }
        var modified: [String] = []
        for (index, part) in parts.enumerated() {
            if index == parts.count - 1 {
                modified.append(String(part))
            } else if index == 0 {
                let prefix = part.prefix(2)
                let maskedCount = max(0, part.count - 2)
                let masked = String(repeating: "*", count: maskedCount)
                modified.append("\(prefix)\(masked)")
            } else {
                modified.append(String(repeating: "*", count: part.count))
            }
        }
        return modified.joined(separator: ".")
    }

    private static func redactIPv4(_ ip: String) -> String {
        let components = ip.split(separator: ".")
        guard components.count == 4 else { return ip }
        return "***.***.\(components[2]).\(components[3])"
    }
}
