#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f pyproject.toml ]]; then
  echo "Run this script from the repository root." >&2
  exit 1
fi

repo_dir="$(pwd)"
user_systemd_dir="${HOME}/.config/systemd/user"
mkdir -p "$user_systemd_dir"

cat > "${user_systemd_dir}/samara-realty-collector.service" <<EOF
[Unit]
Description=Samara Realty Watch collector

[Service]
Type=oneshot
WorkingDirectory=${repo_dir}
EnvironmentFile=${repo_dir}/.env
ExecStart=${repo_dir}/.venv/bin/python -m app collect
EOF

cat > "${user_systemd_dir}/samara-realty-collector.timer" <<'EOF'
[Unit]
Description=Run Samara Realty Watch collector every 2 hours

[Timer]
OnBootSec=5min
OnUnitActiveSec=2h
RandomizedDelaySec=10min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now samara-realty-collector.timer
systemctl --user list-timers --all samara-realty-collector.timer --no-pager

if loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
  exit 0
fi

cat <<EOF

Timer is enabled for the current user session.
For startup after reboot and after logout, run once:

  sudo loginctl enable-linger ${USER}
EOF
