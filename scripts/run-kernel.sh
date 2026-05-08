#!/usr/bin/env bash
# Start Mustang through the Supervisor in dev mode.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

cd "$repo_root/src/kernel"

if [ "$#" -eq 0 ]; then
  exec uv run python -m kernel.supervisor --access-port 8200 --dev
fi

exec uv run python -m kernel.supervisor "$@"
