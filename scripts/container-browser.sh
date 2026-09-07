#!/usr/bin/env bash
set -euo pipefail
export DISPLAY=:99
children=()
chrome_pid=""
cleanup() {
  trap - TERM INT EXIT
  if [[ -n "$chrome_pid" ]]; then
    kill -TERM "$chrome_pid" 2>/dev/null || true
    wait "$chrome_pid" 2>/dev/null || true
  fi
  for pid in "${children[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  wait || true
}
trap 'cleanup; exit 0' TERM INT
trap cleanup EXIT
test -s "$NOVNC_PASSWORD_FILE"
test -n "$BROWSER_PROFILE_DIR"
mkdir -p -- "$BROWSER_PROFILE_DIR"
exec 9>"$BROWSER_PROFILE_DIR/.container-browser.lock"
flock --nonblock 9 || exit 75
# This dedicated copy is owned by this supervisor; copied Chrome host/PID locks are stale.
rm -f -- "$BROWSER_PROFILE_DIR/SingletonLock" "$BROWSER_PROFILE_DIR/SingletonSocket" "$BROWSER_PROFILE_DIR/SingletonCookie"
Xvfb "$DISPLAY" -screen 0 1440x1000x24 -ac +extension GLX +render -noreset &
children+=($!)
for attempt in {1..30}; do
  if xdpyinfo >/dev/null 2>&1; then break; fi
  sleep 1
done
xdpyinfo >/dev/null
fluxbox &
children+=($!)
x11vnc -display "$DISPLAY" -forever -shared -localhost -rfbauth "$NOVNC_PASSWORD_FILE" -rfbport 5900 &
children+=($!)
websockify --web=/usr/share/novnc/ 0.0.0.0:6080 localhost:5900 &
children+=($!)
urls=()
if [[ -f "${AUTH_URLS_FILE:-}" ]]; then
  while IFS= read -r url; do
    [[ -z "$url" || "$url" =~ ^# ]] || urls+=("$url")
  done < "$AUTH_URLS_FILE"
fi
google-chrome --user-data-dir="$BROWSER_PROFILE_DIR" --no-first-run --no-default-browser-check \
  --ozone-platform=x11 --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
  --window-size=1440,1000 "${urls[@]:-about:blank}" &
children+=($!)
chrome_pid=$!
wait -n "${children[@]}"
# A failed display/VNC/browser process must restart the whole supervised stack.
exit 1
