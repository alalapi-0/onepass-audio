# Split Routing Toolkit

Round 8 introduces a practical toolchain for server-side domain based split
routing. The components in this directory work together as follows:

1. Maintain the domain inventory in `domains.yaml` grouped by purpose.
2. Run `resolve_domains.py` to expand domains into IPv4/IPv6 results while
   generating state snapshots under `state/`.
3. Apply the aggregated IPv4 CIDRs to the dataplane using either
   `ipset_apply.sh` (iptables) or `nft_apply.sh` (nftables).
4. Automate refreshes every 10 minutes via `cron-install.sh`, which installs a
   systemd service and timer calling the resolver and chosen backend.

See [docs/SPLIT-IPSET.md](../../docs/SPLIT-IPSET.md) for a full operational
walkthrough, rollback tips, and troubleshooting guidance.
