#!/bin/sh
set -eu

target_os="${DEEPCLI_TARGET_OS:-auto}"
if [ "$target_os" = "__DEEPCLI_TARGET_OS__" ]; then
  target_os="auto"
fi
if [ "$target_os" = "auto" ]; then
  case "$(uname -s)" in
    Linux) target_os="linux" ;;
    Darwin) target_os="macos" ;;
    *) echo "DeepCLI POSIX installer supports Linux and macOS. Found: $(uname -s)" >&2; exit 1 ;;
  esac
fi
case "$target_os" in
  linux|macos) ;;
  *) echo "Unsupported DeepCLI POSIX install target: $target_os" >&2; exit 1 ;;
esac

requested_version="${DEEPCLI_VERSION:-latest}"
github_repository="${DEEPCLI_GITHUB_REPOSITORY:-Harinlen/DeepCLI}"
if [ -n "${DEEPCLI_BASE_URL:-}" ]; then
  base_url="$DEEPCLI_BASE_URL"
elif [ "$requested_version" = "latest" ]; then
  base_url="https://github.com/$github_repository/releases/latest/download"
else
  case "$requested_version" in
    v*) base_url="https://github.com/$github_repository/releases/download/$requested_version" ;;
    *) base_url="https://github.com/$github_repository/releases/download/v$requested_version" ;;
  esac
fi

local_dir="${DEEPCLI_LOCAL_DIR:-}"
home_dir="${HOME:?HOME is required}"
deepcli_home="${DEEPCLI_HOME:-$home_dir/.deepcli}"
bin_dir="$home_dir/.local/bin"
state_dir="${DEEPCLI_STATE_DIR:-$deepcli_home/state}"
config_dir="${DEEPCLI_CONFIG_DIR:-$deepcli_home/config}"
runtime_dir="$state_dir/runtime"
runtime_state_file="$runtime_dir/launcher-runtime.env"
launcher_lock_dir="$state_dir/launcher.lock.d"
launcher_lock_pid="$launcher_lock_dir/pid"
arch="$(uname -m)"
artifact_arch=""
tarball_name=""
checksums_name=""
private_uv_triple=""
uv_version="${DEEPCLI_UV_VERSION:-0.9.28}"
python_version="${DEEPCLI_PYTHON_VERSION:-3.13}"
restart_kernel_after_install=0
install_lock_held=0

case "$target_os" in
  linux)
    case "$(uname -s)" in
      Linux) ;;
      *) echo "DeepCLI Linux installer must run on Linux. Found: $(uname -s)" >&2; exit 1 ;;
    esac
    case "$arch" in
      x86_64) artifact_arch="amd64"; private_uv_triple="x86_64-unknown-linux-gnu" ;;
      *) echo "DeepCLI Linux installer currently supports x86_64 only. Found: $arch" >&2; exit 1 ;;
    esac
    share_dir="${DEEPCLI_DATA_DIR:-$home_dir/.local/share/deepcli}"
    checksums_name="checksums.txt"
    ;;
  macos)
    case "$(uname -s)" in
      Darwin) ;;
      *) echo "DeepCLI macOS installer must run on macOS. Found: $(uname -s)" >&2; exit 1 ;;
    esac
    case "$arch" in
      x86_64) artifact_arch="amd64"; private_uv_triple="x86_64-apple-darwin" ;;
      arm64|aarch64) artifact_arch="arm64"; private_uv_triple="aarch64-apple-darwin" ;;
      *) echo "DeepCLI macOS installer supports x86_64 and arm64 only. Found: $arch" >&2; exit 1 ;;
    esac
    share_dir="${DEEPCLI_DATA_DIR:-$home_dir/Library/Application Support/DeepCLI}"
    checksums_name="checksums-macos-$artifact_arch.txt"
    ;;
esac

tarball_name="deepcli-$target_os-$artifact_arch.tar.gz"
tools_dir="$share_dir/tools"
uv_bin="$tools_dir/uv/$uv_version/uv"
python_install_dir="$tools_dir/python"
downloads_dir="$share_dir/downloads"

mkdir -p "$bin_dir" "$share_dir/releases" "$tools_dir" "$downloads_dir" "$runtime_dir" "$config_dir"

pid_alive() {
  pid_to_check="$1"
  [ -n "$pid_to_check" ] && kill -0 "$pid_to_check" 2>/dev/null
}

acquire_install_lock() {
  while :; do
    if mkdir "$launcher_lock_dir" 2>/dev/null; then
      printf '%s\n' "$$" > "$launcher_lock_pid"
      install_lock_held=1
      return 0
    fi
    lock_pid=""
    if [ -f "$launcher_lock_pid" ]; then
      lock_pid="$(cat "$launcher_lock_pid" 2>/dev/null || true)"
    fi
    if [ -n "$lock_pid" ] && ! pid_alive "$lock_pid"; then
      rm -rf "$launcher_lock_dir"
      continue
    fi
    echo "DeepCLI launcher is busy. Stop active installs/startup and retry." >&2
    exit 1
  done
}

release_install_lock() {
  if [ "$install_lock_held" -eq 1 ]; then
    if [ -f "$launcher_lock_pid" ] && [ "$(cat "$launcher_lock_pid" 2>/dev/null || true)" = "$$" ]; then
      rm -f "$launcher_lock_pid"
      rmdir "$launcher_lock_dir" 2>/dev/null || true
    fi
    install_lock_held=0
  fi
}

readiness_url() {
  readiness_port="$1"
  printf 'http://127.0.0.1:%s/access/readiness\n' "$readiness_port"
}

probe_ready() {
  probe_port="$1"
  probe_payload="$(curl -fsS --max-time 1 "$(readiness_url "$probe_port")" 2>/dev/null)" || return 1
  printf '%s\n' "$probe_payload" | grep -q '"default_route_ready"[[:space:]]*:[[:space:]]*true' &&
    printf '%s\n' "$probe_payload" | grep -q '"hub_ready"[[:space:]]*:[[:space:]]*true' &&
    printf '%s\n' "$probe_payload" | grep -q '"primary_registered"[[:space:]]*:[[:space:]]*true'
}

stop_running_kernel_for_upgrade() {
  if [ "${DEEPCLI_INSTALL_KEEP_KERNEL:-}" = "1" ]; then
    return 0
  fi
  if [ ! -f "$runtime_state_file" ]; then
    return 0
  fi

  . "$runtime_state_file"

  stop_port="${DEEPCLI_RUNTIME_PORT:-}"
  stop_pid="${DEEPCLI_RUNTIME_PID:-}"
  stop_pgid="${DEEPCLI_RUNTIME_PGID:-$stop_pid}"
  stop_was_running=0
  if [ -n "$stop_port" ] && probe_ready "$stop_port"; then
    stop_was_running=1
  elif pid_alive "$stop_pid"; then
    stop_was_running=1
  fi
  if [ "$stop_was_running" -ne 1 ]; then
    rm -f "$runtime_state_file"
    return 0
  fi

  restart_kernel_after_install=1
  echo "Stopping running DeepCLI Kernel before install..."
  if [ -n "$stop_pgid" ]; then
    kill -TERM "-$stop_pgid" 2>/dev/null || true
  elif [ -n "$stop_pid" ]; then
    kill -TERM "$stop_pid" 2>/dev/null || true
  fi

  for _ in $(seq 1 40); do
    if { [ -z "$stop_port" ] || ! probe_ready "$stop_port"; } && ! pid_alive "$stop_pid"; then
      rm -f "$runtime_state_file"
      return 0
    fi
    sleep 0.25
  done

  echo "Kernel did not stop cleanly; forcing shutdown..."
  if [ -n "$stop_pgid" ]; then
    kill -KILL "-$stop_pgid" 2>/dev/null || true
  elif [ -n "$stop_pid" ]; then
    kill -KILL "$stop_pid" 2>/dev/null || true
  fi
  rm -f "$runtime_state_file"
}

restart_kernel_if_needed() {
  if [ "$restart_kernel_after_install" -ne 1 ]; then
    return 0
  fi
  echo "Restarting DeepCLI Kernel with installed version..."
  "$bin_dir/deepcli" kernel start
}

download() {
  download_name="$1"
  download_dest="$2"
  download_tmp="$download_dest.tmp.$$"
  if [ -n "$local_dir" ]; then
    echo "Copying $download_name..."
    cp "$local_dir/$download_name" "$download_tmp"
  else
    echo "Downloading $download_name..."
    curl -fsSL "$base_url/$download_name" -o "$download_tmp"
  fi
  mv -f "$download_tmp" "$download_dest"
}

download_text() {
  download_text_name="$1"
  if [ -n "$local_dir" ]; then
    cat "$local_dir/$download_text_name"
  else
    curl -fsSL "$base_url/$download_text_name"
  fi
}

sha256_value() {
  sha256_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$sha256_path" | awk '{print $1}'
  else
    shasum -a 256 "$sha256_path" | awk '{print $1}'
  fi
}

verify_checksums() {
  checksum_artifact_path="$1"
  checksum_file_path="$2"
  checksum_artifact_file="$(basename "$checksum_artifact_path")"
  checksum_line="$(grep "  $checksum_artifact_file\$" "$checksum_file_path")" || {
    echo "No checksum found for $checksum_artifact_file" >&2
    exit 1
  }
  checksum_expected="$(printf '%s\n' "$checksum_line" | awk '{print $1}')"
  checksum_actual="$(sha256_value "$checksum_artifact_path")"
  if [ "$checksum_expected" != "$checksum_actual" ]; then
    echo "Checksum failed for $checksum_artifact_file" >&2
    exit 1
  fi
  echo "$checksum_artifact_file: OK"
}

install_private_uv() {
  if [ -x "$uv_bin" ]; then
    return 0
  fi

  mkdir -p "$(dirname "$uv_bin")"

  if [ -n "${DEEPCLI_LOCAL_UV:-}" ]; then
    echo "Copying private uv..."
    cp "$DEEPCLI_LOCAL_UV" "$uv_bin"
    chmod +x "$uv_bin"
    return 0
  fi

  private_uv_archive="uv-$private_uv_triple.tar.gz"
  private_uv_url="https://github.com/astral-sh/uv/releases/download/$uv_version/$private_uv_archive"
  private_uv_tmp_dir="$downloads_dir/uv-$uv_version.$$"
  mkdir -p "$private_uv_tmp_dir"

  echo "Downloading private uv $uv_version..."
  curl -fsSL "$private_uv_url" -o "$private_uv_tmp_dir/$private_uv_archive"
  tar -xzf "$private_uv_tmp_dir/$private_uv_archive" -C "$private_uv_tmp_dir"
  cp "$private_uv_tmp_dir/uv-$private_uv_triple/uv" "$uv_bin"
  chmod +x "$uv_bin"
  rm -rf "$private_uv_tmp_dir"
}

prepare_kernel_venv() {
  prepare_release_dir="$1"
  echo "Preparing managed Python $python_version..."
  UV_PYTHON_INSTALL_DIR="$python_install_dir" \
  UV_CACHE_DIR="$share_dir/cache/uv" \
    "$uv_bin" python install "$python_version" \
      --managed-python \
      --install-dir "$python_install_dir" \
      --no-bin

  echo "Preparing Kernel venv..."
  (
    cd "$prepare_release_dir/kernel"
    UV_PYTHON_INSTALL_DIR="$python_install_dir" \
    UV_CACHE_DIR="$share_dir/cache/uv" \
      "$uv_bin" sync --locked --no-dev --python "$python_version" --managed-python
  )
}

acquire_install_lock
trap release_install_lock EXIT
stop_running_kernel_for_upgrade

download "$tarball_name" "$downloads_dir/$tarball_name"
download_text "$checksums_name" > "$downloads_dir/$checksums_name"
verify_checksums "$downloads_dir/$tarball_name" "$downloads_dir/$checksums_name"

tmp_extract="$downloads_dir/extract.$$"
rm -rf "$tmp_extract"
mkdir -p "$tmp_extract"
tar -xzf "$downloads_dir/$tarball_name" -C "$tmp_extract"
release_root="$(find "$tmp_extract" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "$release_root" ] || [ ! -f "$release_root/VERSION" ]; then
  echo "Release tarball did not contain a valid DeepCLI release root." >&2
  exit 1
fi
version="$(tr -d '\r\n' < "$release_root/VERSION")"
release_dir="$share_dir/releases/$version"
release_tmp="$share_dir/releases/.$version.tmp.$$"

rm -rf "$release_tmp"
mv "$release_root" "$release_tmp"
rm -rf "$tmp_extract"

install_private_uv
prepare_kernel_venv "$release_tmp"

rm -rf "$release_dir"
mv "$release_tmp" "$release_dir"
chmod +x "$release_dir/launcher/deepcli" "$release_dir/cli/deepcli-cli"

ln -sfn "$release_dir/launcher/deepcli" "$bin_dir/deepcli"

if ! echo "$PATH" | tr ':' '\n' | grep -qx "$bin_dir"; then
  echo
  echo "Add this to your shell profile:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo "DeepCLI installed: $bin_dir/deepcli"
"$bin_dir/deepcli" --help >/dev/null || true
release_install_lock
restart_kernel_if_needed
