#!/usr/bin/env bash
# Start mustang daemon in dev mode (auto-reload).
#
# On the first run we install the daemon's npm-side runtime
# dependencies (currently just agent-browser, which the browser /
# page_fetch tools shell out to) and download a copy of Chrome from
# Chrome-for-Testing.  Subsequent runs are fast — npm + Chrome are
# cached.
set -euo pipefail

cd "$(dirname "$0")/daemon"

if [ ! -d node_modules/agent-browser ]; then
    echo "Installing daemon's npm runtime (agent-browser)..."
    npm install
    echo "Downloading Chrome-for-Testing for the browser tool..."
    ./node_modules/.bin/agent-browser install
fi

exec uv run uvicorn daemon.app:create_app \
    --factory \
    --host 127.0.0.1 \
    --port 7777 \
    --reload \
    --log-level info
