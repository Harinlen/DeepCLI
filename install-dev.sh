#!/usr/bin/env bash
# Build this checkout and install DeepCLI into the local user layout.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  --help|-h|help )
    cat <<'EOF'
Usage: ./install-dev.sh

Build this checkout into Linux release-shaped artifacts, then install them
into the local user layout through src/launcher/packaging/linux/install.sh.

Environment:
  DEEPCLI_VERSION       Version directory to install. Default: 1.0.0
  DEEPCLI_RELEASE_DIR   Local artifact output directory.
  DEEPCLI_INSTALL_KEEP_KERNEL=1
                        Do not stop/restart a running packaged Kernel.
EOF
    exit 0
    ;;
esac

exec "$repo_root/src/launcher/packaging/linux/install-dev.sh" "$@"
