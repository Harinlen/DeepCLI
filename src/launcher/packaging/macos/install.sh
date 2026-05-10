#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec env DEEPCLI_TARGET_OS=macos "$script_dir/../posix/install.sh" "$@"
