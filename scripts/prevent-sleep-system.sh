#!/usr/bin/env bash
set -euo pipefail

sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/99-keep-awake-for-ssh.conf >/dev/null <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF

sudo systemctl restart systemd-logind

systemctl status sleep.target suspend.target hibernate.target hybrid-sleep.target --no-pager || true
loginctl show-user "$USER" -p Linger 2>/dev/null || true
