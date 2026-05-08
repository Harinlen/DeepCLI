# DeepCLI Launcher

Cross-platform `deepcli` launcher. The launcher owns local runtime discovery,
user-level singleton locking, port selection, detached Supervisor startup, and
CLI handoff.

Linux v1 is implemented as Bash to avoid adding another build toolchain before
the Kernel Python runtime is prepared.

Linux v1:

- user-level install via `install.sh`;
- no `.deb` / `.rpm`;
- no systemd requirement;
- Kernel ships as a source runtime inside a release tarball and runs from a
  release-local managed Python venv;
- CLI runs as a bundled single-file artifact;
- `uv` is installed as a DeepCLI-private tool under
  `~/.local/share/deepcli/tools/uv/` and is not added to the user's PATH;
- Supervisor is started as a detached process and gated by
  `/access/readiness`.
- `deepcli --uninstall` removes the installed launcher, CLI artifact, and
  managed Kernel venv for the current user while preserving config, state,
  sessions, logs, and editable UI assets.

Windows local install-dev is implemented for v1.0.0 development smoke:

- user-level install via `install-dev.ps1`;
- no GitHub Release Windows package yet;
- Kernel ships as a source runtime inside a release zip and runs from a
  release-local managed Python venv;
- CLI runs as a bundled `deepcli-cli.exe`;
- `uv.exe` is installed as a DeepCLI-private tool under
  `%LOCALAPPDATA%\DeepCLI\tools\uv\` unless overridden by `DEEPCLI_INSTALL_DIR`;
- Supervisor is started as a hidden process and gated by `/access/readiness`.

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

On Windows:

```powershell
.\install-dev.ps1
```

The root wrapper delegates to the launcher sub-repo script:

```bash
src/launcher/packaging/linux/install-dev.sh
src/launcher/packaging/windows/install-dev.ps1
```

For an isolated smoke test that does not touch the real home directory:

```bash
HOME=/tmp/deepcli-install-dev-home \
DEEPCLI_RELEASE_DIR=/tmp/deepcli-install-dev-release \
  src/launcher/packaging/linux/install-dev.sh
```

If `DEEPCLI_DEV_ROOT` is not set, the launcher walks upward from the current
directory and looks for this repo's `scripts/run-kernel.sh`, `src/cli/package.json`,
and `src/kernel/pyproject.toml`.

## Packaged Linux Layout

```text
~/.local/bin/deepcli
~/.local/share/deepcli/
├── tools/uv/<uv-version>/uv
└── releases/<version>/
    ├── kernel/.venv/
    ├── cli/deepcli-cli
    ├── launcher/deepcli
    └── assets/welcome-logo.txt
~/.local/state/deepcli/
└── runtime/
~/.config/deepcli/
```

`~/.local/bin/deepcli` points at the current release's
`launcher/deepcli`.

## Packaged Windows Layout

```text
%LOCALAPPDATA%\DeepCLI\
├── bin\deepcli.cmd
├── tools\uv\<uv-version>\uv.exe
└── releases\<version>\
    ├── kernel\.venv\
    ├── cli\deepcli-cli.exe
    ├── launcher\deepcli.ps1
    ├── launcher\deepcli.cmd
    └── assets\welcome-logo.txt
%USERPROFILE%\.deepcli\
└── state\runtime\
```

`bin\deepcli.cmd` is a shim that points at the current release's
`launcher\deepcli.ps1`.

## Customization

The Welcome logo is runtime-customizable. The CLI checks these locations in
order and falls back to its bundled default:

```text
$DEEPCLI_WELCOME_LOGO_FILE
~/.config/deepcli/welcome-logo.txt
~/.config/deepcli/ui/welcome-logo.txt
~/.local/share/deepcli/releases/<version>/assets/welcome-logo.txt
```

The installed asset is intentionally editable; future Kernel-served profile UI
assets can layer above this, but the first screen must still have an offline
fallback.
