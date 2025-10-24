//
//  KeychainHelper.swift
//  PrivateTunnel
//
//  Purpose: Provides a lightweight wrapper around Keychain Services for persisting configuration payloads.
//  Author: OpenAI Assistant
//  Created: 2024-05-15
//
//  Example:
//      let data = Data("example".utf8)
//      try KeychainHelper.shared.save(data: data, account: "demo")
//      let restored = try KeychainHelper.shared.read(account: "demo")
//      try KeychainHelper.shared.delete(account: "demo")
//
import Foundation
import Security

final class KeychainHelper {
    static let shared = KeychainHelper()

    private let service = "com.privatetunnel.config"

    private init() {}

    enum KeychainError: LocalizedError {
        case unexpectedStatus(OSStatus)
        case noData

        var errorDescription: String? {
            switch self {
            case .unexpectedStatus(let status):
                if let message = SecCopyErrorMessageString(status, nil) as String? {
                    return "Keychain 错误: \(message)"
                }
                return "Keychain 操作失败，状态码 \(status)。"
            case .noData:
                return "Keychain 中未找到数据。"
            }
        }
    }

    func save(data: Data, account: String) throws {
        try delete(account: account)

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]

        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw KeychainError.unexpectedStatus(status)
        }
    }

    func read(account: String) throws -> Data {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true
        ]

        var item: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &item)

        switch status {
        case errSecSuccess:
            guard let data = item as? Data else {
                throw KeychainError.noData
            }
            return data
        case errSecItemNotFound:
            throw KeychainError.noData
        default:
            throw KeychainError.unexpectedStatus(status)
        }
    }

    func delete(account: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]

        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.unexpectedStatus(status)
        }
    }
}
