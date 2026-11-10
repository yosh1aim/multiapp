#!/bin/bash
# Installs multiapp as a macOS LaunchAgent: starts at login, restarts if it crashes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.multiapp.server"
PORT="${MULTIAPP_PORT:-8731}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "No virtualenv found. Run:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
[ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
mkdir -p "$ROOT/logs" "$ROOT/data" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/python</string>
    <string>-m</string><string>uvicorn</string>
    <string>app.main:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$ROOT/logs/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/server.log</string>
</dict>
</plist>
EOF

# Reload if already installed, then start.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Installed. multiapp will start at every login."
echo "Open:  http://127.0.0.1:$PORT"
echo "Logs:  $ROOT/logs/server.log"
echo "Restart later with:  launchctl kickstart -k $DOMAIN/$LABEL"
