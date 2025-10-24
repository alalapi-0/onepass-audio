# Firewall Template (Placeholder)

This directory contains placeholders for nftables/ufw snippets. Administrators
should adapt the ruleset to their environment:

```
# nftables example
add table inet privatetunnel
add chain inet privatetunnel input { type filter hook input priority 0; }
add rule inet privatetunnel input udp dport {51820,35000} accept
add rule inet privatetunnel input ct state established,related accept
add rule inet privatetunnel input drop
```

Copy the template into `/etc/nftables.d/` or integrate with your existing
firewall orchestration.
