#!/bin/bash
# Stops multiapp and removes it from login items. Your data (data/multiapp.db) is kept.
set -euo pipefail
LABEL="com.multiapp.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Uninstalled. Your data is still in $(cd "$(dirname "$0")/.." && pwd)/data/"
