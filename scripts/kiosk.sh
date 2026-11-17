#!/usr/bin/env bash
# OPTIONAL: full-screen browser on the Pi's own screen (7-inch touch display). Not needed for the system to work.
set -euo pipefail

APP_URL="${APP_URL:-http://127.0.0.1:8000/}"
BROWSER=""
for candidate in chromium chromium-browser google-chrome google-chrome-stable; do
  if command -v "$candidate" >/dev/null 2>&1; then
    BROWSER="$candidate"
    break
  fi
done
if [[ -z "$BROWSER" ]]; then
  echo "[kiosk] no supported browser found (chromium / google-chrome)" >&2
  exit 1
fi

export DISPLAY="${DISPLAY:-:0}"
if command -v xset >/dev/null 2>&1; then   # no screen blanking
  xset s off || true
  xset -dpms || true
  xset s noblank || true
fi

for _ in $(seq 1 60); do                   # wait for the dashboard
  curl -fsS "$APP_URL" >/dev/null 2>&1 && break
  sleep 1
done

exec "$BROWSER" --kiosk --disable-infobars --noerrdialogs \
  --disable-session-crashed-bubble --disable-features=TranslateUI \
  --user-data-dir=/tmp/proximity-kiosk "$APP_URL"
