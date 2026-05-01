#!/usr/bin/env bash
# Start deepcli-probe (interactive ACP test client).
set -euo pipefail

cd "$(dirname "$0")/probe"

exec uv run python -m probe "$@"
