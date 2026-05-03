#!/usr/bin/env bash
set -euo pipefail

version="${DEEPCLI_VERSION:-1.0.0}"
base_url="${DEEPCLI_BASE_URL:-https://releases.deepcli.dev/$version}"
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
restart_kernel_after_install=0
install_lock_fd=""

case "$arch" in
  x86_64) artifact_arch="amd64" ;;
  aarch64|arm64) artifact_arch="arm64" ;;
  *) echo "Unsupported Linux architecture: $arch" >&2; exit 1 ;;
esac

mkdir -p "$bin_dir" "$share_dir/bin" "$share_dir/assets" "$share_dir/cli/$version" "$share_dir/kernel/$version" "$runtime_dir" "$config_dir"

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

read_manifest() {
  if [ -n "$local_dir" ]; then
    cat "$local_dir/manifest.txt"
  else
    curl -fsSL "$base_url/manifest.txt"
  fi
}

launcher_name="deepcli-launcher-linux"
cli_name="deepcli-cli-linux-$artifact_arch"

download "$launcher_name" "$share_dir/bin/deepcli-$version"
chmod +x "$share_dir/bin/deepcli-$version"

download "$cli_name" "$share_dir/cli/$version/deepcli-cli"
chmod +x "$share_dir/cli/$version/deepcli-cli"
ln -sfn "$share_dir/cli/$version" "$share_dir/cli/current"

download "welcome-logo.txt" "$share_dir/assets/welcome-logo.txt"

wheel_name="$(read_manifest | awk '/mustang_kernel-.*\.whl/ {print $1; exit}')"
if [ -z "$wheel_name" ]; then
  echo "Could not find kernel wheel in manifest.txt" >&2
  exit 1
fi
download "$wheel_name" "$share_dir/kernel/$version/$wheel_name"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$home_dir/.local/bin:$home_dir/.cargo/bin:$PATH"
fi

uv python install 3.13
uv venv "$share_dir/kernel/$version/.venv" --python 3.13 --clear
uv pip install --python "$share_dir/kernel/$version/.venv/bin/python" "$share_dir/kernel/$version/$wheel_name"
ln -sfn "$share_dir/kernel/$version" "$share_dir/kernel/current"

ln -sfn "$share_dir/bin/deepcli-$version" "$bin_dir/deepcli"

if ! echo "$PATH" | tr ':' '\n' | grep -qx "$bin_dir"; then
  echo
  echo "Add this to your shell profile:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo "DeepCLI installed: $bin_dir/deepcli"
"$bin_dir/deepcli" --help >/dev/null || true
release_install_lock
restart_kernel_if_needed
