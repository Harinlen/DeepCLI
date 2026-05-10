#!/usr/bin/env bash
# Build this checkout and install DeepCLI into the local user layout.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  --help|-h|help )
    cat <<'EOF'
Usage: ./install-dev.sh

Build this checkout into release-shaped artifacts for the current POSIX
platform, then install them into the local user layout.

Environment:
  DEEPCLI_VERSION       Version directory to install. Default: 1.0.0
  DEEPCLI_RELEASE_DIR   Local artifact output directory.
  DEEPCLI_ARTIFACT_ARCH macOS only: amd64 or arm64. Default: current arch.
  DEEPCLI_INSTALL_KEEP_KERNEL=1
                        Do not stop/restart a running packaged Kernel.
EOF
    exit 0
    ;;
esac

case "$(uname -s)" in
  Linux)
    exec "$repo_root/src/launcher/packaging/linux/install-dev.sh" "$@"
    ;;
  Darwin)
    exec "$repo_root/src/launcher/packaging/macos/install-dev.sh" "$@"
    ;;
  *)
    echo "install-dev.sh supports Linux and macOS. Use install-dev.ps1 on Windows." >&2
    exit 1
    ;;
esac
