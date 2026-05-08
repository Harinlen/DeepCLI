#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

if command -v uv >/dev/null 2>&1; then
  exec uv run python "$repo_root/scripts/release.py" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$repo_root/scripts/release.py" "$@"
fi

exec python "$repo_root/scripts/release.py" "$@"
