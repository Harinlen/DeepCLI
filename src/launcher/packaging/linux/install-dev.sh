#!/usr/bin/env bash
# Build DeepCLI from this checkout and install it into the local user layout.
#
# This is a developer convenience wrapper around:
#   1. build-release.sh
#   2. install.sh with DEEPCLI_LOCAL_DIR=<local artifacts>
#
# It does not require a published release server.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../../.." && pwd)"
version="${DEEPCLI_VERSION:-1.0.0}"
release_dir="${DEEPCLI_RELEASE_DIR:-$repo_root/dist/deepcli-linux-$version}"

case "${1:-}" in
  --help|-h|help )
    cat <<'EOF'
Usage: install-dev.sh

Build the current checkout into Linux release-shaped artifacts, then install
them into the local user layout through install.sh.

Environment:
  DEEPCLI_VERSION       Version directory to install. Default: 1.0.0
  DEEPCLI_RELEASE_DIR   Local artifact output directory.
  DEEPCLI_INSTALL_KEEP_KERNEL=1
                        Do not stop/restart a running packaged Kernel.
EOF
    exit 0
    ;;
esac

echo "Building local DeepCLI release artifacts..."
DEEPCLI_RELEASE_DIR="$release_dir" "$script_dir/build-release.sh"

echo
echo "Installing from local artifacts..."
DEEPCLI_LOCAL_DIR="$release_dir" "$script_dir/install.sh"

echo
echo "Installed DeepCLI from checkout."
echo "Release artifacts: $release_dir"
echo "Command: ${HOME}/.local/bin/deepcli"
