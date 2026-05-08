#!/usr/bin/env bash
# Build DeepCLI from this checkout and install it into the local macOS user layout.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../../.." && pwd)"
version="${DEEPCLI_VERSION:-1.0.0}"
release_dir="${DEEPCLI_RELEASE_DIR:-$repo_root/dist/deepcli-macos-$version}"

case "${1:-}" in
  --help|-h|help )
    cat <<'EOF'
Usage: install-dev.sh

Build the current checkout into macOS release-shaped artifacts, then install
them into the local user layout through install.sh.

Environment:
  DEEPCLI_VERSION       Version directory to install. Default: 1.0.0
  DEEPCLI_RELEASE_DIR   Local artifact output directory.
  DEEPCLI_ARTIFACT_ARCH amd64 or arm64. Default: current machine arch.
  DEEPCLI_INSTALL_KEEP_KERNEL=1
                        Do not stop/restart a running packaged Kernel.
EOF
    exit 0
    ;;
esac

case "$(uname -s)" in
  Darwin) ;;
  *) echo "macOS install-dev.sh must run on macOS." >&2; exit 1 ;;
esac

echo "Building local DeepCLI macOS release artifacts..."
DEEPCLI_RELEASE_DIR="$release_dir" "$script_dir/build-release.sh"

echo
echo "Installing from local artifacts..."
DEEPCLI_LOCAL_DIR="$release_dir" "$script_dir/install.sh"

echo
echo "Installed DeepCLI from checkout."
echo "Release artifacts: $release_dir"
echo "Command: ${HOME}/.local/bin/deepcli"
