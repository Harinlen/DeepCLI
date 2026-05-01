#!/usr/bin/env bash
# Start Mustang through the Supervisor in dev mode.
set -euo pipefail

cd "$(dirname "$0")/kernel"

if [ "$#" -eq 0 ]; then
  exec uv run python -m kernel.supervisor --access-port 8200 --dev
fi

exec uv run python -m kernel.supervisor "$@"
