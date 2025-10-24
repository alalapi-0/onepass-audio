//
//  ContentView.swift
//  PrivateTunnel
//
//  Purpose: Presents the primary SwiftUI interface for importing, validating, and saving tunnel configurations.
//  Author: OpenAI Assistant
//  Created: 2024-05-15
//
//  Example:
//      ContentView()
//          .environmentObject(ConfigManager())
//
import SwiftUI
import NetworkExtension
import UIKit

struct AlertDescriptor: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}

enum RoutingModeOption: String, CaseIterable, Identifiable {
    case global
    case whitelist

    var id: String { rawValue }

    var label: String {
        switch self {
        case .global:
            return "Global"
        case .whitelist:
            return "Whitelist"
        }
    }

    var description: String {
        switch self {
        case .global:
            return "所有流量均通过隧道出口。"
        case .whitelist:
            return "所有流量进入隧道，由服务器 ipset/nftables 决定真正出网的目标。"
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var configManager: ConfigManager
    @StateObject private var tunnelManager = TunnelManager()

    @State private var isPresentingScanner = false
    @State private var isPresentingFileImporter = false
    @State private var importedConfig: TunnelConfig?
    @State private var alertDescriptor: AlertDescriptor?
    @State private var selectedProfileName: String?
    @State private var isPerformingAction = false
    @State private var killSwitchEnabled = false
    @State private var selectedRoutingMode: RoutingModeOption = .global
    @State private var pendingRoutingMode: RoutingModeOption?
    @State private var isRoutingConfirmationPresented = false
    @State private var isPresentingExportConfirmation = false
    @State private var isExportingDiagnostics = false
    @State private var diagnosticsURL: URL?
    @State private var showShareSheet = false
    @State private var isLogExpanded = true
    @State private var connectSweepToggle = false

    private static let splitDocURL = URL(string: "https://github.com/PrivateTunnel/PrivateTunnel/blob/main/docs/SPLIT-IPSET.md")

    var body: some View {
        NavigationStack {
            ZStack {
                CyberTheme.cyberBackground.ignoresSafeArea()
                ScrollView(showsIndicators: false) {
                    VStack(spacing: 28) {
                        heroSection
                        profilesSection
                        connectionSection
                        logsSection
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 28)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(CyberTheme.cyberBackground.opacity(0.95), for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        configManager.reloadStoredConfigs()
                    } label: {
                        Label("刷新", systemImage: "arrow.clockwise")
                            .labelStyle(.iconOnly)
                    }
                    .tint(CyberTheme.textPrimary)
                    .accessibilityLabel("刷新配置列表")
                }
            }
            .sheet(isPresented: $isPresentingScanner) {
                QRScannerView { result in
                    isPresentingScanner = false
                    handleImportResult(result)
                }
                .preferredColorScheme(.dark)
            }
            .sheet(isPresented: $isPresentingFileImporter) {
                FileImporter { result in
                    isPresentingFileImporter = false
                    handleImportResult(result)
                }
                .preferredColorScheme(.dark)
            }
            .sheet(isPresented: $showShareSheet, onDismiss: { diagnosticsURL = nil }) {
                if let url = diagnosticsURL {
                    ActivityView(activityItems: [url])
                } else {
                    Text("未找到诊断文件")
                        .foregroundStyle(CyberTheme.textPrimary)
                }
            }
            .alert(item: $alertDescriptor) { descriptor in
                Alert(title: Text(descriptor.title), message: Text(descriptor.message), dismissButton: .default(Text("好的")))
            }
            .confirmationDialog("切换分流模式", isPresented: $isRoutingConfirmationPresented, titleVisibility: .visible) {
                Button("确认") {
                    applyPendingRoutingMode()
                }
                Button("取消", role: .cancel) {
                    pendingRoutingMode = nil
                }
            } message: {
                Text("切换分流模式将保存新偏好，并在下次连接时应用。")
            }
            .confirmationDialog("导出诊断包", isPresented: $isPresentingExportConfirmation, titleVisibility: .visible) {
                Button("确认导出") {
                    startDiagnosticsExport()
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text("诊断包包含最近日志与脱敏配置，用于排查问题。私钥与令牌已被遮蔽。")
            }
            .onAppear {
                if selectedProfileName == nil {
                    selectedProfileName = configManager.storedConfigs.first?.profile_name
                    killSwitchEnabled = configManager.storedConfigs.first?.enable_kill_switch ?? false
                    if let config = configManager.storedConfigs.first {
                        selectedRoutingMode = routingMode(for: config)
                    }
                }
                tunnelManager.loadOrCreateProvider { result in
                    if case .failure(let error) = result {
                        alertDescriptor = AlertDescriptor(title: "加载 VPN 管理器失败", message: error.localizedDescription)
                    }
                }
            }
            .onReceive(configManager.$storedConfigs) { configs in
                if let currentSelection = selectedProfileName,
                   !configs.contains(where: { $0.profile_name == currentSelection }) {
                    selectedProfileName = configs.first?.profile_name
                    killSwitchEnabled = configs.first?.enable_kill_switch ?? false
                    if let config = configs.first {
                        selectedRoutingMode = routingMode(for: config)
                    }
                } else if selectedProfileName == nil {
                    selectedProfileName = configs.first?.profile_name
                    killSwitchEnabled = configs.first?.enable_kill_switch ?? false
                    if let config = configs.first {
                        selectedRoutingMode = routingMode(for: config)
                    }
                }
            }
            .onChange(of: selectedProfileName) { newValue in
                if let name = newValue,
                   let config = configManager.storedConfigs.first(where: { $0.profile_name == name }) {
                    killSwitchEnabled = config.enable_kill_switch
                    selectedRoutingMode = routingMode(for: config)
                }
            }
            .onReceive(tunnelManager.$currentStatus) { status in
                if status == .connected {
                    connectSweepToggle.toggle()
                }
                if status == .connected || status == .disconnected || status == .invalid {
                    isPerformingAction = false
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var heroSection: some View {
        ZStack(alignment: .bottomLeading) {
            RoundedRectangle(cornerRadius: 32, style: .continuous)
                .fill(CyberTheme.gradientPrimary)
                .frame(height: 220)
                .overlay(
                    ParticlesView()
                        .blendMode(.plusLighter)
                        .opacity(0.6)
                        .clipShape(RoundedRectangle(cornerRadius: 32, style: .continuous))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 32, style: .continuous)
                        .stroke(CyberTheme.neonGreen.opacity(0.35), lineWidth: 1)
                )
            VStack(alignment: .leading, spacing: 10) {
                Text("PrivateTunnel")
                    .font(.system(size: 34, weight: .semibold, design: .rounded))
                    .foregroundColor(CyberTheme.textPrimary)
                    .accessibilityAddTraits(.isHeader)
                Text("Your Private Neon Gateway")
                    .font(.system(.title3, design: .rounded))
                    .foregroundColor(CyberTheme.textPrimary.opacity(0.9))
                Text("加密隧道、设备管理与诊断全部集中于此，霓虹光感即刻启用。")
                    .font(.system(.subheadline, design: .rounded))
                    .foregroundColor(CyberTheme.textSecondary)
                    .lineSpacing(3)
            }
            .padding(28)
        }
        .padding(.top, 12)
        .accessibilityElement(children: .combine)
    }

    private var profilesSection: some View {
        SectionCard(title: "Profiles",
                    subtitle: "管理配置、扫码导入或从文件导入",
                    icon: "person.crop.circle.badge.plus",
                    accent: .neon) {
            VStack(spacing: 16) {
                HStack(spacing: 12) {
                    CyberButton(style: .secondary) {
                        isPresentingScanner = true
                    } label: {
                        Label("扫码导入", systemImage: "qrcode.viewfinder")
                            .foregroundColor(CyberTheme.textPrimary)
                    }
                    .disabled(isBusyConnecting)
                    .opacity(isBusyConnecting ? 0.5 : 1.0)
                    .accessibilityLabel("扫码导入配置")

                    CyberButton(style: .secondary) {
                        isPresentingFileImporter = true
                    } label: {
                        Label("文件导入", systemImage: "folder")
                            .foregroundColor(CyberTheme.textPrimary)
                    }
                    .disabled(isBusyConnecting)
                    .opacity(isBusyConnecting ? 0.5 : 1.0)
                    .accessibilityLabel("从文件导入配置")
                }

                if configManager.storedConfigs.isEmpty {
                    Text("尚未保存任何配置。通过上方按钮导入 JSON。")
                        .font(.system(.subheadline, design: .rounded))
                        .foregroundColor(CyberTheme.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 12)
                        .background(
                            RoundedRectangle(cornerRadius: 16, style: .continuous)
                                .fill(CyberTheme.edge.opacity(0.45))
                        )
                        .accessibilityLabel("尚未保存配置")
                } else {
                    VStack(spacing: 12) {
                        ForEach(configManager.storedConfigs, id: \.profile_name) { config in
                            profileRow(for: config)
                                .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                    Button(role: .destructive) {
                                        delete(config: config)
                                    } label: {
                                        Label("删除", systemImage: "trash")
                                    }
                                    .disabled(isBusyConnecting)
                                }
                                .contextMenu {
                                    Button(role: .destructive) {
                                        delete(config: config)
                                    } label: {
                                        Label("删除", systemImage: "trash")
                                    }
                                    .disabled(isBusyConnecting)
                                }
                        }
                    }
                }

                if let importedConfig {
                    ImportedConfigView(config: importedConfig) {
                        do {
                            try configManager.save(config: importedConfig)
                            alertDescriptor = AlertDescriptor(title: "保存成功", message: "配置已保存，可在上方列表中选择并连接。")
                            self.importedConfig = nil
                        } catch {
                            alertDescriptor = AlertDescriptor(title: "保存失败", message: error.localizedDescription)
                        }
                    }
                }
            }
        }
    }

    private var connectionSection: some View {
        SectionCard(title: "Connection",
                    subtitle: "选择配置并发起连接",
                    icon: "bolt.fill",
                    accent: .purple) {
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    StatusBadge(status: tunnelStatus)
                    Spacer()
                    Text(statusDescription(for: tunnelManager.status()))
                        .font(.system(.subheadline, design: .rounded).weight(.semibold))
                        .foregroundColor(CyberTheme.textPrimary)
                }

                connectionDetails

                VStack(spacing: 14) {
                    CyberButton(style: .primary,
                                isLoading: isActionInFlight,
                                animateLightSweep: connectSweepToggle) {
                        connectSelectedConfig()
                    } label: {
                        Label(connectButtonTitle, systemImage: "bolt.horizontal.circle.fill")
                            .foregroundColor(CyberTheme.textPrimary)
                    }
                    .disabled(isConnectDisabled)
                    .accessibilityLabel(connectButtonTitle)

                    CyberButton(style: .secondary,
                                isLoading: isDisconnectLoading) {
                        disconnectTunnel()
                    } label: {
                        Label("Disconnect", systemImage: "bolt.slash")
                            .foregroundColor(CyberTheme.textPrimary)
                    }
                    .disabled(isDisconnectDisabled)
                    .accessibilityLabel("断开连接")

                    CyberButton(style: .secondary, isLoading: isExportingDiagnostics) {
                        isPresentingExportConfirmation = true
                    } label: {
                        Label("Export Diagnostics", systemImage: "square.and.arrow.up")
                            .foregroundColor(CyberTheme.textPrimary)
                    }
                    .accessibilityLabel("导出诊断包")
                }

                if isExportingDiagnostics {
                    HStack(spacing: 8) {
                        ProgressView()
                            .progressViewStyle(.circular)
                            .tint(CyberTheme.neonGreen)
                        Text("正在打包诊断信息…")
                            .font(.system(.footnote, design: .rounded))
                            .foregroundColor(CyberTheme.textSecondary)
                    }
                    .accessibilityElement(children: .combine)
                }

                Divider()
                    .background(CyberTheme.edge)

                VStack(alignment: .leading, spacing: 12) {
                    Text("分流模式")
                        .font(.system(.headline, design: .rounded))
                        .foregroundColor(CyberTheme.textPrimary)
                    Picker("Routing Mode", selection: routingModeBinding) {
                        ForEach(RoutingModeOption.allCases) { mode in
                            Text(mode.label).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                    .tint(CyberTheme.neonGreen)
                    .disabled(selectedProfileName == nil)

                    Text(selectedRoutingMode.description)
                        .font(.system(.footnote, design: .rounded))
                        .foregroundColor(CyberTheme.textSecondary)
                        .lineSpacing(2)

                    if selectedRoutingMode == .whitelist {
                        Text("服务器侧的 ipset/nftables 策略将决定哪些域名出公网，其他目的地将保持私网源地址返回。若出现异常，可切回 Global 验证。")
                            .font(.system(.footnote, design: .rounded))
                            .foregroundColor(CyberTheme.textSecondary.opacity(0.9))
                            .lineSpacing(3)
                    }

                    if let url = Self.splitDocURL {
                        Link(destination: url) {
                            Label("查看分流运维说明", systemImage: "book")
                                .font(.system(.footnote, design: .rounded))
                                .foregroundColor(CyberTheme.okay)
                        }
                    }
                }

                Toggle(isOn: $killSwitchEnabled) {
                    Text("Enable Kill Switch（实验性）")
                        .font(.system(.subheadline, design: .rounded))
                        .foregroundColor(CyberTheme.textPrimary)
                }
                .toggleStyle(SwitchToggleStyle(tint: CyberTheme.warn))
                .padding(.top, 4)
            }
        }
    }

    private var connectionDetails: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let profile = selectedProfile,
               let config = configManager.storedConfigs.first(where: { $0.profile_name == profile }) {
                Text(config.profile_name)
                    .font(.system(.title3, design: .rounded).weight(.semibold))
                    .foregroundColor(CyberTheme.textPrimary)
                Text("Endpoint • \(config.endpoint.host):\(config.endpoint.port)")
                    .font(.system(.footnote, design: .rounded))
                    .foregroundColor(CyberTheme.textSecondary)
                Text("Mode • \(selectedRoutingMode.label)")
                    .font(.system(.footnote, design: .rounded))
                    .foregroundColor(CyberTheme.textSecondary)
            } else {
                Text("请选择一个配置以启动连接。")
                    .font(.system(.subheadline, design: .rounded))
                    .foregroundColor(CyberTheme.textSecondary)
            }
        }
    }

    private var logsSection: some View {
        SectionCard(title: "Activity",
                    subtitle: "最近事件和诊断日志",
                    icon: "waveform.path.ecg",
                    accent: .neutral) {
            DisclosureGroup(isExpanded: $isLogExpanded) {
                VStack(alignment: .leading, spacing: 14) {
                    if tunnelManager.recentEvents.isEmpty {
                        Text("暂无日志。完成一次连接后可查看事件。")
                            .font(.system(.footnote, design: .rounded))
                            .foregroundColor(CyberTheme.textSecondary)
                    } else {
                        ForEach(Array(tunnelManager.recentEvents.suffix(10).reversed())) { entry in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack(alignment: .center, spacing: 10) {
                                    Circle()
                                        .fill(color(for: entry.level))
                                        .frame(width: 10, height: 10)
                                        .shadow(color: color(for: entry.level).opacity(0.5), radius: 6)
                                    Text("\(timelineFormatter.string(from: entry.timestamp)) · \(entry.code)")
                                        .font(.system(.caption, design: .rounded))
                                        .foregroundColor(CyberTheme.textSecondary)
                                }
                                Text(entry.message)
                                    .font(.system(.footnote, design: .rounded))
                                    .foregroundColor(CyberTheme.textPrimary)
                                    .lineSpacing(2)
                                if !entry.metadata.isEmpty {
                                    Text(entry.metadata.map { "\($0.key)=\($0.value)" }.joined(separator: ", "))
                                        .font(.system(.caption2, design: .rounded))
                                        .foregroundColor(CyberTheme.textSecondary)
                                }
                            }
                            .padding(.vertical, 6)
                            .accessibilityElement(children: .combine)
                            .accessibilityLabel("\(timelineFormatter.string(from: entry.timestamp)) \(entry.code) \(entry.message)")
                        }
                    }
                }
                .padding(.top, 12)
            } label: {
                Text(isLogExpanded ? "收起最近事件" : "展开最近事件")
                    .font(.system(.subheadline, design: .rounded))
                    .foregroundColor(CyberTheme.textSecondary)
            }
        }
    }

    private var selectedProfile: String? {
        selectedProfileName
    }

    private var isBusyConnecting: Bool {
        let status = tunnelManager.status()
        return status == .connecting || status == .reasserting
    }

    private var isActionInFlight: Bool {
        isPerformingAction || tunnelManager.status() == .connecting || tunnelManager.status() == .reasserting
    }

    private var isDisconnectLoading: Bool {
        isPerformingAction || tunnelManager.status() == .disconnecting
    }

    private var isConnectDisabled: Bool {
        isActionInFlight || selectedProfileName == nil || tunnelManager.status() == .connected
    }

    private var isDisconnectDisabled: Bool {
        isPerformingAction || tunnelManager.status() == .disconnected || tunnelManager.status() == .invalid
    }

    private var connectButtonTitle: String {
        switch tunnelManager.status() {
        case .connected:
            return "Connected"
        case .connecting, .reasserting:
            return "Connecting…"
        default:
            return "Connect"
        }
    }

    private var routingModeBinding: Binding<RoutingModeOption> {
        Binding(
            get: { selectedRoutingMode },
            set: { newValue in
                guard newValue != selectedRoutingMode else { return }
                pendingRoutingMode = newValue
                isRoutingConfirmationPresented = true
            }
        )
    }

    private var tunnelStatus: TunnelStatus {
        switch tunnelManager.status() {
        case .connected:
            return .connected
        case .connecting, .reasserting:
            return .connecting
        default:
            return .disconnected
        }
    }

    private func applyPendingRoutingMode() {
        guard let pendingRoutingMode,
              let selectedProfileName,
              let config = configManager.storedConfigs.first(where: { $0.profile_name == selectedProfileName }) else {
            self.pendingRoutingMode = nil
            return
        }
        selectedRoutingMode = pendingRoutingMode
        tunnelManager.rememberRoutingMode(pendingRoutingMode.rawValue, for: config.profile_name)
        self.pendingRoutingMode = nil
    }

    private func handleImportResult(_ result: Result<TunnelConfig, Error>) {
        switch result {
        case .success(let config):
            importedConfig = config
        case .failure(let error):
            alertDescriptor = AlertDescriptor(title: "解析失败", message: error.localizedDescription)
        }
    }

    @ViewBuilder
    private func profileRow(for config: TunnelConfig) -> some View {
        let isSelected = config.profile_name == selectedProfileName
        Button {
            selectedProfileName = config.profile_name
            killSwitchEnabled = config.enable_kill_switch
            selectedRoutingMode = routingMode(for: config)
        } label: {
            HStack(alignment: .center, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(config.profile_name)
                        .font(.system(.headline, design: .rounded).weight(.semibold))
                        .foregroundColor(CyberTheme.textPrimary)
                    Text("\(config.endpoint.host):\(config.endpoint.port)")
                        .font(.system(.footnote, design: .rounded))
                        .foregroundColor(CyberTheme.textSecondary)
                    Text(config.routing.mode)
                        .font(.system(.caption, design: .rounded))
                        .foregroundColor(CyberTheme.textSecondary.opacity(0.8))
                }
                Spacer()
                if isSelected {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundColor(CyberTheme.neonGreen)
                        .shadow(color: CyberTheme.neonGreen.opacity(0.6), radius: 10)
                        .accessibilityHidden(true)
                }
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
            .background(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(isSelected ? CyberTheme.neonGreen.opacity(0.18) : CyberTheme.edge.opacity(0.55))
                    .overlay(
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(isSelected ? CyberTheme.neonGreen : CyberTheme.edge.opacity(0.7), lineWidth: 1.2)
                    )
            )
        }
        .buttonStyle(.plain)
        .disabled(isBusyConnecting)
        .accessibilityLabel("配置 \(config.profile_name)")
    }

    private func delete(config: TunnelConfig) {
        do {
            try configManager.delete(config: config)
        } catch {
            alertDescriptor = AlertDescriptor(title: "删除失败", message: error.localizedDescription)
        }
    }

    private func connectSelectedConfig() {
        guard let profile = selectedProfileName,
              let config = configManager.storedConfigs.first(where: { $0.profile_name == profile }) else {
            alertDescriptor = AlertDescriptor(title: "未选择配置", message: "请选择要连接的配置。")
            return
        }

        var persistentConfig = config
        persistentConfig.enable_kill_switch = killSwitchEnabled
        if persistentConfig.enable_kill_switch != config.enable_kill_switch {
            do {
                try configManager.save(config: persistentConfig)
            } catch {
                alertDescriptor = AlertDescriptor(title: "更新 Kill Switch 失败", message: error.localizedDescription)
                return
            }
        }

        var runtimeConfig = persistentConfig
        runtimeConfig.routing.mode = selectedRoutingMode.rawValue
        runtimeConfig.routing.allowed_ips = normalizedAllowedIPs(for: config, desiredMode: selectedRoutingMode)

        tunnelManager.rememberRoutingMode(selectedRoutingMode.rawValue, for: config.profile_name)

        isPerformingAction = true
        tunnelManager.save(configuration: runtimeConfig) { result in
            switch result {
            case .failure(let error):
                isPerformingAction = false
                alertDescriptor = AlertDescriptor(title: "保存配置失败", message: error.localizedDescription)
            case .success:
                tunnelManager.connect { connectResult in
                    switch connectResult {
                    case .success:
                        alertDescriptor = AlertDescriptor(title: "连接中", message: "请稍候，系统将提示 VPN 状态。")
                    case .failure(let error):
                        isPerformingAction = false
                        alertDescriptor = AlertDescriptor(title: "连接失败", message: error.localizedDescription)
                    }
                }
            }
        }
    }

    private func disconnectTunnel() {
        isPerformingAction = true
        tunnelManager.disconnect { result in
            if case .failure(let error) = result {
                isPerformingAction = false
                alertDescriptor = AlertDescriptor(title: "断开失败", message: error.localizedDescription)
            }
        }
    }

    private func startDiagnosticsExport() {
        let activeConfig: TunnelConfig?
        if let profile = selectedProfileName {
            activeConfig = configManager.storedConfigs.first(where: { $0.profile_name == profile })
        } else {
            activeConfig = nil
        }

        isExportingDiagnostics = true
        tunnelManager.exportDiagnostics(activeConfig: activeConfig) { result in
            DispatchQueue.main.async {
                isExportingDiagnostics = false
                switch result {
                case .failure(let error):
                    alertDescriptor = AlertDescriptor(title: "导出失败", message: error.localizedDescription)
                case .success(let url):
                    diagnosticsURL = url
                    showShareSheet = true
                }
            }
        }
    }

    private func statusDescription(for status: NEVPNStatus) -> String {
        switch status {
        case .connected:
            return "Connected"
        case .connecting:
            return "Connecting"
        case .disconnected:
            return "Disconnected"
        case .disconnecting:
            return "Disconnecting"
        case .invalid:
            return "Invalid"
        case .reasserting:
            return "Reasserting"
        @unknown default:
            return "Unknown"
        }
    }
}

extension ContentView {
    private func routingMode(for config: TunnelConfig) -> RoutingModeOption {
        if let stored = tunnelManager.persistedRoutingMode(for: config.profile_name),
           let mode = RoutingModeOption(rawValue: stored) {
            return mode
        }
        return RoutingModeOption(rawValue: config.routing.mode) ?? .global
    }

    private func normalizedAllowedIPs(for config: TunnelConfig, desiredMode: RoutingModeOption) -> [String] {
        var base = (config.routing.allowed_ips ?? []).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        if base.isEmpty {
            base = ["0.0.0.0/0"]
        }
        switch desiredMode {
        case .global:
            return base
        case .whitelist:
            if !base.contains("0.0.0.0/0") {
                base.insert("0.0.0.0/0", at: 0)
            }
            return base
        }
    }
}

private let timelineFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateStyle = .none
    formatter.timeStyle = .medium
    return formatter
}()

private func color(for level: DiagnosticLogLevel) -> Color {
    switch level {
    case .info:
        return CyberTheme.okay
    case .warn:
        return CyberTheme.warn
    case .error:
        return CyberTheme.warn
    case .security:
        return CyberTheme.purple
    }
}

private struct ImportedConfigView: View {
    let config: TunnelConfig
    var onSave: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("导入的配置")
                .font(.system(.headline, design: .rounded))
                .foregroundColor(CyberTheme.textPrimary)
            Group {
                Text("Profile • \(config.profile_name)")
                Text("Endpoint • \(config.endpoint.host):\(config.endpoint.port)")
                Text("Mode • \(config.routing.mode)")
                if let allowed = config.routing.allowed_ips, !allowed.isEmpty {
                    Text("AllowedIPs • \(allowed.joined(separator: ", "))")
                }
                if let whitelist = config.routing.whitelist_domains, !whitelist.isEmpty {
                    Text("Whitelist • \(whitelist.joined(separator: ", "))")
                }
                Text("Address • \(config.client.address)")
                Text("DNS • \(config.client.dns.joined(separator: ", "))")
            }
            .font(.system(.footnote, design: .rounded))
            .foregroundColor(CyberTheme.textSecondary)
            .lineSpacing(2)

            if let notes = config.notes, !notes.isEmpty {
                Text("Notes • \(notes)")
                    .font(.system(.footnote, design: .rounded))
                    .foregroundColor(CyberTheme.textSecondary)
            }

            CyberButton(style: .primary) {
                onSave()
            } label: {
                Label("保存配置", systemImage: "square.and.arrow.down")
                    .foregroundColor(CyberTheme.textPrimary)
            }
            .accessibilityLabel("保存导入的配置")
        }
        .padding(18)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(CyberTheme.edge.opacity(0.65))
                .overlay(
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .stroke(CyberTheme.neonGreen.opacity(0.6), lineWidth: 1)
                )
        )
    }
}

struct ActivityView: UIViewControllerRepresentable {
    let activityItems: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: activityItems, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
            .environmentObject(ConfigManager())
    }
}
