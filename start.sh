#!/usr/bin/env bash
# Launcher: runs the dashboard using the project's venv Python.
# Sources .env (if present) to pick up FRED_API_KEY and friends.
# Any args (e.g. --live, --refresh 30) are passed through.
set -e
cd "$(dirname "$0")"

if [ -f ".env" ]; then
    set -a
    . ./.env
    set +a
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "venv missing — creating .venv and installing deps..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/python -m macro_monitor "$@"
