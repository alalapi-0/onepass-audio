//
//  PrivateTunnelApp.swift
//  PrivateTunnel
//
//  Purpose: Entry point for the iOS SwiftUI container app that orchestrates configuration import and persistence.
//  Author: OpenAI Assistant
//  Created: 2024-05-15
//
//  Example:
//      @main
//      struct PrivateTunnelApp: App {
//          var body: some Scene {
//              WindowGroup {
//                  ContentView()
//              }
//          }
//      }
//
import SwiftUI

@main
struct PrivateTunnelApp: App {
    @StateObject private var configManager = ConfigManager()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(configManager)
        }
    }
}
