#!/usr/bin/env bash
set -euo pipefail

requested_version="${DEEPCLI_VERSION:-latest}"
github_repository="${DEEPCLI_GITHUB_REPOSITORY:-deepcli/deepcli}"
if [ -n "${DEEPCLI_BASE_URL:-}" ]; then
  base_url="$DEEPCLI_BASE_URL"
elif [ "$requested_version" = "latest" ]; then
  base_url="https://github.com/$github_repository/releases/latest/download"
elif [[ "$requested_version" = v* ]]; then
  base_url="https://github.com/$github_repository/releases/download/$requested_version"
else
  base_url="https://github.com/$github_repository/releases/download/v$requested_version"
fi
local_dir="${DEEPCLI_LOCAL_DIR:-}"
home_dir="${HOME:?HOME is required}"
bin_dir="$home_dir/.local/bin"
share_dir="${DEEPCLI_DATA_DIR:-$home_dir/.local/share/deepcli}"
state_dir="${DEEPCLI_STATE_DIR:-$home_dir/.local/state/deepcli}"
config_dir="${DEEPCLI_CONFIG_DIR:-$home_dir/.config/deepcli}"
runtime_dir="$state_dir/runtime"
runtime_state_file="$runtime_dir/launcher-runtime.env"
launcher_lock_file="$state_dir/launcher.lock"
arch="$(uname -m)"
artifact_arch=""
tarball_name=""
uv_version="${DEEPCLI_UV_VERSION:-0.9.28}"
python_version="${DEEPCLI_PYTHON_VERSION:-3.13}"
tools_dir="$share_dir/tools"
uv_bin="$tools_dir/uv/$uv_version/uv"
python_install_dir="$tools_dir/python"
downloads_dir="$share_dir/downloads"
restart_kernel_after_install=0
install_lock_fd=""

case "$arch" in
  x86_64) artifact_arch="amd64" ;;
  *) echo "DeepCLI Linux installer currently supports x86_64 only. Found: $arch" >&2; exit 1 ;;
esac

tarball_name="deepcli-linux-$artifact_arch.tar.gz"

mkdir -p "$bin_dir" "$share_dir/releases" "$tools_dir" "$downloads_dir" "$runtime_dir" "$config_dir"

acquire_install_lock() {
  exec {install_lock_fd}>"$launcher_lock_file"
  if ! flock -n "$install_lock_fd"; then
    echo "DeepCLI launcher is busy. Stop active installs/startup and retry." >&2
    exit 1
  fi
}

release_install_lock() {
  if [ -n "$install_lock_fd" ]; then
    flock -u "$install_lock_fd" 2>/dev/null || true
    eval "exec ${install_lock_fd}>&-"
    install_lock_fd=""
  fi
}

readiness_url() {
  local port="$1"
  printf 'http://127.0.0.1:%s/access/readiness\n' "$port"
}

probe_ready() {
  local port="$1"
  local payload
  payload="$(curl -fsS --max-time 1 "$(readiness_url "$port")" 2>/dev/null)" || return 1
  grep -q '"default_route_ready"[[:space:]]*:[[:space:]]*true' <<<"$payload" &&
    grep -q '"hub_ready"[[:space:]]*:[[:space:]]*true' <<<"$payload" &&
    grep -q '"primary_registered"[[:space:]]*:[[:space:]]*true' <<<"$payload"
}

pid_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_running_kernel_for_upgrade() {
  if [ "${DEEPCLI_INSTALL_KEEP_KERNEL:-}" = "1" ]; then
    return 0
  fi
  if [ ! -f "$runtime_state_file" ]; then
    return 0
  fi

  # shellcheck disable=SC1090
  source "$runtime_state_file"

  local port="${DEEPCLI_RUNTIME_PORT:-}"
  local pid="${DEEPCLI_RUNTIME_PID:-}"
  local pgid="${DEEPCLI_RUNTIME_PGID:-$pid}"
  local was_running=0
  if [ -n "$port" ] && probe_ready "$port"; then
    was_running=1
  elif pid_alive "$pid"; then
    was_running=1
  fi
  if [ "$was_running" -ne 1 ]; then
    rm -f "$runtime_state_file"
    return 0
  fi

  restart_kernel_after_install=1
  echo "Stopping running DeepCLI Kernel before install..."
  if [ -n "$pgid" ]; then
    kill -TERM "-$pgid" 2>/dev/null || true
  elif [ -n "$pid" ]; then
    kill -TERM "$pid" 2>/dev/null || true
  fi

  for _ in $(seq 1 40); do
    if { [ -z "$port" ] || ! probe_ready "$port"; } && ! pid_alive "$pid"; then
      rm -f "$runtime_state_file"
      return 0
    fi
    sleep 0.25
  done

  echo "Kernel did not stop cleanly; forcing shutdown..."
  if [ -n "$pgid" ]; then
    kill -KILL "-$pgid" 2>/dev/null || true
  elif [ -n "$pid" ]; then
    kill -KILL "$pid" 2>/dev/null || true
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

acquire_install_lock
stop_running_kernel_for_upgrade

download() {
  local name="$1"
  local dest="$2"
  local tmp="$dest.tmp.$$"
  if [ -n "$local_dir" ]; then
    echo "Copying $name..."
    cp "$local_dir/$name" "$tmp"
  else
    echo "Downloading $name..."
    curl -fsSL "$base_url/$name" -o "$tmp"
  fi
  mv -f "$tmp" "$dest"
}

download_text() {
  local name="$1"
  if [ -n "$local_dir" ]; then
    cat "$local_dir/$name"
  else
    curl -fsSL "$base_url/$name"
  fi
}

verify_checksums() {
  local artifact_path="$1"
  local checksums_path="$2"
  local artifact_file
  artifact_file="$(basename "$artifact_path")"
  (
    cd "$(dirname "$artifact_path")"
    grep "  $artifact_file\$" "$checksums_path" | sha256sum -c -
  )
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

  local uv_triple="x86_64-unknown-linux-gnu"
  local uv_archive="uv-$uv_triple.tar.gz"
  local uv_url="https://github.com/astral-sh/uv/releases/download/$uv_version/$uv_archive"
  local tmp_dir="$downloads_dir/uv-$uv_version.$$"
  mkdir -p "$tmp_dir"

  echo "Downloading private uv $uv_version..."
  curl -fsSL "$uv_url" -o "$tmp_dir/$uv_archive"
  tar -xzf "$tmp_dir/$uv_archive" -C "$tmp_dir"
  cp "$tmp_dir/uv-$uv_triple/uv" "$uv_bin"
  chmod +x "$uv_bin"
  rm -rf "$tmp_dir"
}

prepare_kernel_venv() {
  local release_dir="$1"
  echo "Preparing managed Python $python_version..."
  UV_PYTHON_INSTALL_DIR="$python_install_dir" \
  UV_CACHE_DIR="$share_dir/cache/uv" \
    "$uv_bin" python install "$python_version" \
      --managed-python \
      --install-dir "$python_install_dir" \
      --no-bin

  echo "Preparing Kernel venv..."
  (
    cd "$release_dir/kernel"
    UV_PYTHON_INSTALL_DIR="$python_install_dir" \
    UV_CACHE_DIR="$share_dir/cache/uv" \
      "$uv_bin" sync --locked --no-dev --python "$python_version" --managed-python
  )
}

download "$tarball_name" "$downloads_dir/$tarball_name"
download_text "checksums.txt" > "$downloads_dir/checksums.txt"
verify_checksums "$downloads_dir/$tarball_name" "$downloads_dir/checksums.txt"

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
