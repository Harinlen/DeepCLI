# DeepCLI Launcher

Linux `deepcli` launcher. The launcher owns local runtime discovery, user-level
singleton locking, port selection, detached Supervisor startup, and CLI handoff.

Linux v1 is implemented as Bash to avoid adding another build toolchain before
the Kernel Python runtime is prepared.

Linux v1 is the active target:

- user-level install via `install.sh`;
- no `.deb` / `.rpm`;
- no systemd requirement;
- Kernel runs from a managed Python venv;
- CLI runs as a bundled artifact;
- Supervisor is started as a detached process and gated by
  `/access/readiness`.
- `deepcli --uninstall` removes the installed launcher, CLI artifact, and
  managed Kernel venv for the current user while preserving config, state,
  sessions, logs, and editable UI assets.

## Development

From this repo:

```bash
cd src/launcher
bash -n bin/deepcli packaging/linux/*.sh
DEEPCLI_DEV_ROOT=/path/to/mustang ./bin/deepcli status
DEEPCLI_DEV_ROOT=/path/to/mustang ./bin/deepcli kernel start
```

To build release-shaped artifacts from the current checkout and install them
into the local user layout:

```bash
./install-dev.sh
```

The root wrapper delegates to the launcher sub-repo script:

```bash
src/launcher/packaging/linux/install-dev.sh
```

For an isolated smoke test that does not touch the real home directory:

```bash
HOME=/tmp/deepcli-install-dev-home \
DEEPCLI_RELEASE_DIR=/tmp/deepcli-install-dev-release \
  src/launcher/packaging/linux/install-dev.sh
```

If `DEEPCLI_DEV_ROOT` is not set, the launcher walks upward from the current
directory and looks for this repo's `src/run-kernel.sh`, `src/cli/package.json`,
and `src/kernel/pyproject.toml`.

## Packaged Linux Layout

```text
~/.local/bin/deepcli
~/.local/share/deepcli/
├── bin/deepcli-<version>
├── assets/welcome-logo.txt
├── kernel/current/.venv/
└── cli/current/
~/.local/state/deepcli/
└── runtime/
~/.config/deepcli/
```

`cli/current` may contain either `deepcli-cli` or `bun + dist/main.js`.

## Customization

The Welcome logo is runtime-customizable. The CLI checks these locations in
order and falls back to its bundled default:

```text
$DEEPCLI_WELCOME_LOGO_FILE
~/.config/deepcli/welcome-logo.txt
~/.config/deepcli/ui/welcome-logo.txt
~/.local/share/deepcli/assets/welcome-logo.txt
```

The installed asset is intentionally editable; future Kernel-served profile UI
assets can layer above this, but the first screen must still have an offline
fallback.
