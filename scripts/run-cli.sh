#!/usr/bin/env bash
# Start DeepCLI CLI (ACP terminal client), ensuring the dev kernel is ready.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
kernel_port="${KERNEL_PORT:-8200}"
explicit_kernel_url="${KERNEL_URL:-}"

for ((i = 1; i <= $#; i++)); do
  arg="${!i}"
  case "$arg" in
    --help|-h|help )
      cd "$repo_root/src/cli"
      exec "${BUN:-bun}" run src/main.ts "$@"
      ;;
    --port )
      next_index=$((i + 1))
      if [ "$next_index" -le "$#" ]; then
        kernel_port="${!next_index}"
      fi
      ;;
    --kernel )
      next_index=$((i + 1))
      if [ "$next_index" -le "$#" ]; then
        explicit_kernel_url="${!next_index}"
      fi
      ;;
  esac
done

if [ -z "$explicit_kernel_url" ]; then
  readiness_url="http://127.0.0.1:${kernel_port}/access/readiness"

  if ! curl -fsS --max-time 1 "$readiness_url" 2>/dev/null | grep -q '"default_route_ready":true'; then
    log_path="$repo_root/src/.run-kernel.log"
    "$script_dir/run-kernel.sh" --access-port "$kernel_port" --dev >"$log_path" 2>&1 &

    ready=0
    for _ in $(seq 1 160); do
      if curl -fsS --max-time 1 "$readiness_url" 2>/dev/null | grep -q '"default_route_ready":true'; then
        ready=1
        break
      fi
      sleep 0.25
    done

    if [ "$ready" -ne 1 ]; then
      echo "Kernel did not become ready. See $log_path" >&2
      exit 1
    fi
  fi
fi

cd "$repo_root/src/cli"

exec "${BUN:-bun}" run src/main.ts "$@"
