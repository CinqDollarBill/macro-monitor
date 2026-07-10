#!/usr/bin/env bash
# Double-click launcher for Macro Monitor (macOS).
# Opens a Terminal window, sets everything up on first run (virtualenv +
# dependencies), and starts the dashboard in live mode. Safe to run anytime —
# subsequent launches skip straight to the app.
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Macro Monitor needs Python 3, which isn't installed yet."
    echo
    echo "macOS will now offer to install it (Command Line Tools) —"
    echo "click Install in the dialog, wait for it to finish, then"
    echo "double-click this file again."
    xcode-select --install >/dev/null 2>&1 || true
    echo
    read -n 1 -s -r -p "Press any key to close this window..."
    exit 1
fi

exec ./start.sh --live
