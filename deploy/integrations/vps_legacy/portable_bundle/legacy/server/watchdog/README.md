# Watchdog & Health Probes

This directory contains helper scripts and systemd units that keep the PrivateTunnel
edge node healthy. They are intentionally lightweight so they can be dropped on
a small VPS without additional dependencies.

## Files

- `endpoint_probe.sh` – Shell script that validates outbound reachability. It
  runs an ICMP ping against `1.1.1.1` and performs an HTTPS HEAD request. If
  either probe fails it can optionally execute a restart command (e.g.
  `systemctl restart toy-gateway`).
- `toy-watchdog.service` – systemd service that keeps the toy UDP/TUN gateway
  process alive. If the Python process exits unexpectedly systemd will attempt
  to relaunch it after three seconds.
- `wg-watchdog.service` – Placeholder for future WireGuard integration. Once the
  WireGuard data plane is wired in, replace the `ExecStart` directive with the
  appropriate restart command (e.g. `systemctl restart wg-quick@wg0`).

## Deployment

1. Copy the files into `/etc/systemd/system/` on your VPS.
2. Adjust `WorkingDirectory` and `ExecStart` paths in the unit files to match
   your installation directory.
3. Enable services:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable toy-watchdog.service
   sudo systemctl start toy-watchdog.service
   ```
4. Use cron or a systemd timer to execute `endpoint_probe.sh` every few minutes.
   Example cron entry:
   ```cron
   */5 * * * * TARGET_HTTP=https://www.apple.com RESTART_COMMAND="systemctl restart toy-watchdog" /opt/privatetunnel/watchdog/endpoint_probe.sh >> /var/log/endpoint_probe.log 2>&1
   ```

## Logging

- Runtime logs for the gateway and probe scripts should be stored under
  `/var/log/private-tunnel/`. The sample `server/security/logrotate/` configs
  rotate these files daily (toy gateway) or weekly (WireGuard journal export).
- After deploying, symlink the sample logrotate files into
  `/etc/logrotate.d/` and verify permissions via `server/security/audit.sh`.
- Journald persistence defaults can be applied via
  `server/security/journald/20-private-tunnel.conf` to retain a limited history
  even across reboots.

## Troubleshooting

- **Repeated ICMP failures** – Check cloud firewall/ security group rules. Some
  providers block ping replies by default.
- **HTTPS probe failing** – Inspect outbound firewall rules, TLS interception or
  SNI filtering. Try switching to `https://api.openai.com/robots.txt` or another
  known-good endpoint.
- **UDP-specific issues** – Confirm that the upstream port (default 51820 for
  WireGuard or custom port for the toy engine) is open and not throttled.
- **MTU-related drops** – When packets consistently fail, experiment with lower
  MTU values in the client configuration (e.g. 1280).

These scripts should be combined with the iOS health checker (Round 7) to get
end-to-end visibility into the tunnel's behaviour.
