#!/usr/bin/env bash
# Start Mustang through the Supervisor in dev mode.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${MUSTANG_FLAGS_PATH:-}" ]; then
  dev_flags_path="$script_dir/.mustang-dev-flags.yaml"
  cat > "$dev_flags_path" <<'YAML'
transport:
  stack: acp
YAML
  export MUSTANG_FLAGS_PATH="$dev_flags_path"
fi

cd "$script_dir/kernel"

if [ "$#" -eq 0 ]; then
  exec uv run python -m kernel.supervisor --access-port 8200 --dev
fi

exec uv run python -m kernel.supervisor "$@"
