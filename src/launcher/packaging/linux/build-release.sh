#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
launcher_dir="$(cd "$script_dir/../.." && pwd)"
repo_root="$(cd "$launcher_dir/../.." && pwd)"
version="${DEEPCLI_VERSION:-1.0.0}"
out_dir="${DEEPCLI_RELEASE_DIR:-$repo_root/dist/deepcli-linux-$version}"
arch="$(uname -m)"

case "$arch" in
  x86_64) artifact_arch="amd64" ;;
  aarch64|arm64) artifact_arch="arm64" ;;
  *) echo "Unsupported Linux architecture: $arch" >&2; exit 1 ;;
esac

mkdir -p "$out_dir"

echo "Staging Bash launcher..."
cp "$launcher_dir/bin/deepcli" "$out_dir/deepcli-launcher-linux"
chmod +x "$out_dir/deepcli-launcher-linux"

echo "Staging default UI assets..."
cp "$repo_root/src/cli/src/active-port/coding-agent/modes/components/welcome-logo.txt" "$out_dir/welcome-logo.txt"

echo "Building CLI single executable..."
(
  cd "$repo_root/src/cli"
  bun build src/main.ts --target=bun --compile --outfile "$out_dir/deepcli-cli-linux-$artifact_arch"
)

echo "Building Kernel wheel..."
(
  cd "$repo_root/src/kernel"
  uv build --wheel --out-dir "$out_dir"
)

echo "Writing manifest..."
(
  cd "$out_dir"
  ls -1 > manifest.txt
)

echo "Writing checksums..."
(
  cd "$out_dir"
  find . -maxdepth 1 -type f ! -name checksums.txt -printf '%f\n' | sort | xargs sha256sum > checksums.txt
)

echo "Release artifacts written to $out_dir"
