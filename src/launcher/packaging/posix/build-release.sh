#!/usr/bin/env bash
set -euo pipefail

target_os="${DEEPCLI_TARGET_OS:?DEEPCLI_TARGET_OS is required: linux or macos}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
launcher_dir="$(cd "$script_dir/../.." && pwd)"
repo_root="$(cd "$launcher_dir/../.." && pwd)"
version="${DEEPCLI_VERSION:-1.0.0}"
artifact_arch="${DEEPCLI_ARTIFACT_ARCH:-}"
out_dir="${DEEPCLI_RELEASE_DIR:-$repo_root/dist/deepcli-$target_os-$version}"
uv_version="${DEEPCLI_UV_VERSION:-0.9.28}"
python_version="${DEEPCLI_PYTHON_VERSION:-3.13}"

case "$out_dir" in
  /*) ;;
  *) out_dir="$repo_root/$out_dir" ;;
esac

case "$target_os" in
  linux)
    case "$(uname -s)" in
      Linux) ;;
      *) echo "Linux release builds must run on Linux." >&2; exit 1 ;;
    esac
    if [[ -z "$artifact_arch" ]]; then
      case "$(uname -m)" in
        x86_64) artifact_arch="amd64" ;;
        *) echo "Linux v1 release builds support x86_64 only. Found: $(uname -m)" >&2; exit 1 ;;
      esac
    fi
    case "$artifact_arch" in
      amd64) bun_target="bun" ;;
      *) echo "Unsupported Linux artifact arch: $artifact_arch" >&2; exit 1 ;;
    esac
    installer_name="install.sh"
    manifest_name="manifest.json"
    checksums_name="checksums.txt"
    ;;
  macos)
    case "$(uname -s)" in
      Darwin) ;;
      *) echo "macOS release builds must run on macOS." >&2; exit 1 ;;
    esac
    if [[ -z "$artifact_arch" ]]; then
      case "$(uname -m)" in
        x86_64) artifact_arch="amd64" ;;
        arm64|aarch64) artifact_arch="arm64" ;;
        *) echo "macOS v1 release builds support x86_64 and arm64 only. Found: $(uname -m)" >&2; exit 1 ;;
      esac
    fi
    case "$artifact_arch" in
      amd64) bun_target="bun-darwin-x64" ;;
      arm64) bun_target="bun-darwin-arm64" ;;
      *) echo "Unsupported macOS artifact arch: $artifact_arch" >&2; exit 1 ;;
    esac
    installer_name="install-macos.sh"
    manifest_name="manifest-macos-$artifact_arch.json"
    checksums_name="checksums-macos-$artifact_arch.txt"
    ;;
  *) echo "Unsupported POSIX release target: $target_os" >&2; exit 1 ;;
esac

release_name="deepcli-$version-$target_os-$artifact_arch"
stage_dir="$out_dir/stage/$release_name"
tarball_name="deepcli-$target_os-$artifact_arch.tar.gz"

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

write_installer_asset() {
  if [[ "$installer_name" == "install.sh" ]]; then
    cp "$script_dir/install.sh" "$out_dir/$installer_name"
  else
    sed "s/__DEEPCLI_TARGET_OS__/$target_os/g" \
      "$script_dir/install.sh" > "$out_dir/$installer_name"
  fi
  chmod +x "$out_dir/$installer_name"
}

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

echo "Staging POSIX launcher..."
cp "$launcher_dir/bin/deepcli" "$stage_dir/launcher/deepcli"
chmod +x "$stage_dir/launcher/deepcli"

echo "Staging default UI assets..."
cp "$repo_root/src/cli/src/active-port/coding-agent/modes/components/welcome-logo.txt" "$stage_dir/assets/welcome-logo.txt"

echo "Building CLI single executable..."
(
  cd "$repo_root/src/cli"
  bun build src/main.ts --target="$bun_target" --compile --outfile "$stage_dir/cli/deepcli-cli"
)
chmod +x "$stage_dir/cli/deepcli-cli"

printf '%s\n' "$version" > "$stage_dir/VERSION"

echo "Writing release tarball..."
(
  cd "$out_dir/stage"
  tar -czf "$out_dir/$tarball_name" "$release_name"
)

echo "Writing manifest..."
cat > "$out_dir/$manifest_name" <<EOF
{
  "version": "$version",
  "os": "$target_os",
  "arch": "$artifact_arch",
  "artifact": "$tarball_name",
  "uvVersion": "$uv_version",
  "pythonVersion": "$python_version"
}
EOF

write_installer_asset

echo "Writing checksums..."
(
  cd "$out_dir"
  {
    printf '%s  %s\n' "$(sha256_file "$tarball_name")" "$tarball_name"
    printf '%s  %s\n' "$(sha256_file "$installer_name")" "$installer_name"
    printf '%s  %s\n' "$(sha256_file "$manifest_name")" "$manifest_name"
  } > "$checksums_name"
)

echo "Release artifacts written to $out_dir"
