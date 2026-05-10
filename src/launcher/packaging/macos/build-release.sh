#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec env DEEPCLI_TARGET_OS=macos "$script_dir/../posix/build-release.sh" "$@"
