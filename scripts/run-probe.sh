#!/usr/bin/env bash
# Start deepcli-probe (interactive ACP test client).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

cd "$repo_root/src/probe"

exec uv run python -m probe "$@"
