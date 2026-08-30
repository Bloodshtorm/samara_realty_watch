#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
NOVNC_PASSWORD="${NOVNC_PASSWORD:-change_me}"
AUTH_URLS="${AUTH_URLS:-}"
AUTH_URLS_FILE="${AUTH_URLS_FILE:-}"
NOVNC_LISTEN_HOST="${NOVNC_LISTEN_HOST:-127.0.0.1}"
NOVNC_PASSWORD_FILE="${NOVNC_PASSWORD_FILE:-}"
AUTH_BROWSER_MODE="${AUTH_BROWSER_MODE:-system-chrome}"

if [[ -n "$NOVNC_PASSWORD_FILE" ]]; then
  vnc_auth_args=(-rfbauth "$NOVNC_PASSWORD_FILE")
else
  vnc_auth_args=(-passwd "$NOVNC_PASSWORD")
fi

Xvfb "$DISPLAY" -screen 0 "${XVFB_SCREEN:-1440x1000x24}" -ac +extension GLX +render -noreset &
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -localhost "${vnc_auth_args[@]}" -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ "$NOVNC_LISTEN_HOST:6080" localhost:5900 >/tmp/novnc.log 2>&1 &

browser_urls=()
if [[ -n "$AUTH_URLS_FILE" && -f "$AUTH_URLS_FILE" ]]; then
  while IFS= read -r target_url; do
    [[ -z "$target_url" || "$target_url" =~ ^# ]] && continue
    browser_urls+=("$target_url")
  done < "$AUTH_URLS_FILE"
fi
for target_url in $AUTH_URLS; do
  browser_urls+=("$target_url")
done

echo "noVNC is available on http://${NOVNC_LISTEN_HOST}:6080/vnc.html"
echo "Use NOVNC_PASSWORD to log in. Browser profile path: ${BROWSER_PROFILE_DIR:-/data/browser-profile}"
if [[ "$AUTH_BROWSER_MODE" == "system-chrome" ]]; then
  chrome_bin="${CHROME_BIN:-google-chrome}"
  profile_dir="${BROWSER_PROFILE_DIR:-data/browser-profile}"
  mkdir -p "$profile_dir"
  exec "$chrome_bin" \
    --user-data-dir="$profile_dir" \
    --no-first-run \
    --no-default-browser-check \
    --ozone-platform=x11 \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="${CHROME_REMOTE_DEBUGGING_PORT:-9222}" \
    --window-size=1440,1000 \
    "${browser_urls[@]:-about:blank}"
fi

python_bin="${PYTHON_BIN:-.venv/bin/python}"
args=("$python_bin" -m app browser-init --skip-searches)
for target_url in "${browser_urls[@]}"; do
  args+=(--url "$target_url")
done
exec "${args[@]}"
