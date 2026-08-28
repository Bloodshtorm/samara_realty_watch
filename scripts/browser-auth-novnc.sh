#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
NOVNC_PASSWORD="${NOVNC_PASSWORD:-change_me}"
AUTH_URLS="${AUTH_URLS:-}"

Xvfb "$DISPLAY" -screen 0 "${XVFB_SCREEN:-1440x1000x24}" -ac +extension GLX +render -noreset &
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -passwd "$NOVNC_PASSWORD" -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ 0.0.0.0:6080 localhost:5900 >/tmp/novnc.log 2>&1 &

args=(python -m app browser-init --include-disabled)
for target_url in $AUTH_URLS; do
  args+=(--url "$target_url")
done

echo "noVNC is available on http://localhost:6080/vnc.html"
echo "Use NOVNC_PASSWORD to log in. Browser profile path: ${BROWSER_PROFILE_DIR:-/data/browser-profile}"
exec "${args[@]}"
