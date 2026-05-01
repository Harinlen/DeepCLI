#!/usr/bin/env bash
# Start mustang CLI (ACP terminal client).
set -euo pipefail

cd "$(dirname "$0")/cli"

exec ~/.bun/bin/bun run src/main.ts "$@"
