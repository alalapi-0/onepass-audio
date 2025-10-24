# PrivateTunnel iOS WireGuard Engine

This document explains how the Packet Tunnel provider shipped with the iOS app
integrates the WireGuard userspace implementation via `WireGuardKit` and how to
configure the required project capabilities.

## Overview

* The container application writes the active WireGuard configuration in
  wg-quick format to the shared app group path at
  `Group Container/configs/active.conf`.
* `PacketTunnelProvider` reads the file, parses it using
  `WireGuardQuickConfig`, and passes the resulting `TunnelConfiguration` to the
  `WireGuardAdapter` supplied by `WireGuardKit`.
* `WGEngineReal` manages the adapter lifecycle, exposes status/metric
  publishers, and provides helper APIs for the UI layer.
* `KillSwitch` applies Network Extension settings only after the tunnel starts
  successfully and clears them immediately when the tunnel stops or fails. This
  ensures that no direct routes/DNS servers are installed while the tunnel is
  down.

## Project Setup

1. **Enable App Groups**
   * In Xcode open the project and select both the application target and the
     `PacketTunnelProvider` extension target.
   * Enable the *App Groups* capability and add
     `group.com.alalapi.privatetunnel`. Adjust the identifier if your bundle ID
     differs and update `AppGroup.identifier` accordingly.

2. **Network Extension Capability**
   * For the extension target enable the *Network Extensions* capability and
     select *Packet Tunnel*. Ensure the provisioning profile includes the VPN
     entitlement when running on device.

3. **Bundle Resources**
   * Add `apps/ios/PrivateTunnelApp/Resources/Info.plist` to the extension
     target if it is not already configured. This plist contains the
     `NSExtension` block that declares the Packet Tunnel provider entry point.

4. **WireGuardKit via Swift Package Manager**
   * Open **File ▸ Add Packages…** in Xcode.
   * Enter the repository URL `https://git.zx2c4.com/wireguard-apple` and select
     the latest tag.
   * Add the `WireGuardKit` product to both the app target (for config parsing
     and diagnostics) and the Packet Tunnel extension target.

5. **Shared Configuration File**
   * The application should save the selected WireGuard configuration to
     `<AppGroup>/configs/active.conf` using UTF-8 encoding. During development
     you can drop a `DEBUG_SAMPLE.conf` file into the same directory to bootstrap
     the tunnel.

6. **Real Device Testing**
   * Packet Tunnel extensions require a physical iOS device with a development
     provisioning profile that includes the *Personal VPN* entitlement.
   * On first launch the system presents a VPN permission alert. Accept it to
     allow the extension to install the tunnel interface.
   * Use Xcode's console to inspect logs produced by the adapter and engine.
     Diagnostics such as last handshake time and bytes transferred are available
     through `WGEngineReal.stats()`.

## Kill Switch Behaviour

`KillSwitch` leaves the device without VPN routes/DNS until the WireGuard
backend confirms the tunnel is running. When the tunnel stops or fails to start,
`setTunnelNetworkSettings(nil)` is invoked to immediately drop any leftover
settings. This prevents traffic leaks while the engine is reconnecting.

## License and Distribution Notice

`WireGuardKit` (from `wireguard-apple`) is licensed under GPLv2 and additional
terms. The current integration is provided for internal evaluation/testing.
Commercial redistribution or App Store submission may require further legal
review to ensure license compliance.
