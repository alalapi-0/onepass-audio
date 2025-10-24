//
//  ConfigManager.swift
//  PrivateTunnel
//
//  Purpose: Observable object responsible for persisting, loading, and deleting tunnel configurations using Keychain.
//  Author: OpenAI Assistant
//  Created: 2024-05-15
//
//  Example:
//      let manager = ConfigManager()
//      try manager.save(config: config)
//      let configs = manager.storedConfigs
//
import Foundation

final class ConfigManager: ObservableObject {
    @Published private(set) var storedConfigs: [TunnelConfig] = []

    private let indexKey = "com.privatetunnel.config.index"
    private let encoder = JSONEncoder()

    enum ManagerError: LocalizedError {
        case invalidConfiguration(String)
        case serializationFailed

        var errorDescription: String? {
            switch self {
            case .invalidConfiguration(let message):
                return message
            case .serializationFailed:
                return "无法序列化配置，请稍后再试。"
            }
        }
    }

    init() {
        reloadStoredConfigs()
    }

    func reloadStoredConfigs() {
        let identifiers = UserDefaults.standard.stringArray(forKey: indexKey) ?? []
        var loadedConfigs: [TunnelConfig] = []
        var validIdentifiers: [String] = []

        for account in identifiers {
            do {
                let data = try KeychainHelper.shared.read(account: account)
                let config = try TunnelConfig.from(jsonData: data)
                try config.isValid()
                loadedConfigs.append(config)
                validIdentifiers.append(account)
            } catch {
                continue
            }
        }

        if validIdentifiers != identifiers {
            UserDefaults.standard.set(validIdentifiers, forKey: indexKey)
        }

        DispatchQueue.main.async {
            self.storedConfigs = loadedConfigs
        }
    }

    func save(config: TunnelConfig) throws {
        do {
            try config.isValid()
        } catch {
            throw ManagerError.invalidConfiguration(error.localizedDescription)
        }

        guard let data = try? encoder.encode(config) else {
            throw ManagerError.serializationFailed
        }

        try KeychainHelper.shared.save(data: data, account: config.profile_name)
        var identifiers = UserDefaults.standard.stringArray(forKey: indexKey) ?? []
        if !identifiers.contains(config.profile_name) {
            identifiers.append(config.profile_name)
            UserDefaults.standard.set(identifiers, forKey: indexKey)
        }
        reloadStoredConfigs()
    }

    func delete(config: TunnelConfig) throws {
        try KeychainHelper.shared.delete(account: config.profile_name)
        var identifiers = UserDefaults.standard.stringArray(forKey: indexKey) ?? []
        identifiers.removeAll { $0 == config.profile_name }
        UserDefaults.standard.set(identifiers, forKey: indexKey)
        reloadStoredConfigs()
    }
}
