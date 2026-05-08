#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
launcher_dir="$(cd "$script_dir/../.." && pwd)"
repo_root="$(cd "$launcher_dir/../.." && pwd)"
version="${DEEPCLI_VERSION:-1.0.0}"
out_dir="${DEEPCLI_RELEASE_DIR:-$repo_root/dist/deepcli-linux-$version}"
arch="$(uname -m)"
uv_version="${DEEPCLI_UV_VERSION:-0.9.28}"
python_version="${DEEPCLI_PYTHON_VERSION:-3.13}"

case "$out_dir" in
  /*) ;;
  *) out_dir="$repo_root/$out_dir" ;;
esac

case "$arch" in
  x86_64) artifact_arch="amd64" ;;
  *) echo "Linux v1 release builds support x86_64 only. Found: $arch" >&2; exit 1 ;;
esac

release_name="deepcli-$version-linux-$artifact_arch"
stage_dir="$out_dir/stage/$release_name"
tarball_name="deepcli-linux-$artifact_arch.tar.gz"

rm -rf "$stage_dir"
mkdir -p "$stage_dir/kernel" "$stage_dir/cli" "$stage_dir/launcher" "$stage_dir/assets" "$out_dir"

echo "Staging Kernel source runtime..."
cp "$repo_root/src/kernel/pyproject.toml" "$stage_dir/kernel/pyproject.toml"
cp -R "$repo_root/src/kernel/kernel" "$stage_dir/kernel/kernel"
find "$stage_dir/kernel/kernel" \
  \( -type d -name __pycache__ -o -type d -name .pytest_cache -o -type d -name .mypy_cache \) \
  -prune -exec rm -rf {} +
find "$stage_dir/kernel/kernel" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

echo "Locking staged Kernel runtime dependencies..."
(
  cd "$stage_dir/kernel"
  uv lock --python "$python_version"
)

echo "Staging Bash launcher..."
cp "$launcher_dir/bin/deepcli" "$stage_dir/launcher/deepcli"
chmod +x "$stage_dir/launcher/deepcli"

echo "Staging default UI assets..."
cp "$repo_root/src/cli/src/active-port/coding-agent/modes/components/welcome-logo.txt" "$stage_dir/assets/welcome-logo.txt"

echo "Building CLI single executable..."
(
  cd "$repo_root/src/cli"
  bun build src/main.ts --target=bun --compile --outfile "$stage_dir/cli/deepcli-cli"
)
chmod +x "$stage_dir/cli/deepcli-cli"

printf '%s\n' "$version" > "$stage_dir/VERSION"

echo "Writing release tarball..."
(
  cd "$out_dir/stage"
  tar -czf "$out_dir/$tarball_name" "$release_name"
)

echo "Writing manifest..."
cat > "$out_dir/manifest.json" <<EOF
{
  "version": "$version",
  "arch": "$artifact_arch",
  "artifact": "$tarball_name",
  "uvVersion": "$uv_version",
  "pythonVersion": "$python_version"
}
EOF

cp "$script_dir/install.sh" "$out_dir/install.sh"
chmod +x "$out_dir/install.sh"

echo "Writing checksums..."
(
  cd "$out_dir"
  sha256sum "$tarball_name" install.sh manifest.json > checksums.txt
)

echo "Release artifacts written to $out_dir"
