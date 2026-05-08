# DeepCLI

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&colorA=222222&logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/Kernel-v1.0.0-F4A261?style=flat&colorA=222222" alt="Kernel v1.0.0">
  <img src="https://img.shields.io/badge/Status-Alpha-orange?style=flat&colorA=222222" alt="Status: Alpha">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-58A6FF?style=flat&colorA=222222" alt="License: MIT"></a>
</p>

<p align="center">
  <em>Definitive DeepSeek coding CLI on any OS that grows with you 🐋</em>
</p>

DeepCLI is an open, modular agent harness for the **DeepSeek V4** era with native support for Windows, macOS, and Linux. Its harness architecture, tool system, and prompt engineering are heavily adapted from Claude Code's codebase, while the TUI is mainly a customized integration of Oh-my-pi; OpenClaw and Hermes Agent inform the process model, routing layer, and Python runtime. It gives coding agents the runtime pieces they need: sessions, tools, memory, skills, permissions, hooks, MCP, provider routing, persistence, and a clean ACP/JSON-RPC boundary between clients and runtime. The repo is also the experiment: **DeepCLI is being built entirely by AI Coding Agents**. Every kernel capability is a replaceable subsystem, so memory, skills, tools, LLM providers, permissions, hooks, gateways, storage, and even the agent loop can be swapped, extended, or rebuilt without coupling frontends to kernel internals. The project documentation is open too, making the architecture easier to understand and giving different contributors a shared map for sustained agent-driven development.

<p align="center">
  <img src="docs/assets/snapshot.png" alt="DeepCLI terminal home screen" width="760">
</p>

## Architecture

DeepCLI now runs as a supervised local agent control plane, not as a
single CLI process talking directly to an in-process agent loop.

```text
deepcli launcher
  |
  | starts / monitors one user-local runtime
  v
Supervisor
  |-- Agent Hub
  |     |-- Router                 user -> agent / agent -> agent / agent -> user
  |     |-- Manager                runtime records, status, control
  |     `-- GlobalResourceMonitor  shared config / skills / memory / MCP revisions
  |
  |-- Access Agent                 FastAPI edge, health/readiness, WebSocket /session
  |
  `-- Primary Agent Runtime        real SessionManager + Orchestrator + tools
        |
        | private task fanout
        v
      Ephemeral child agents

CLI / Probe / future Home Screen / platform adapters
  |
  | ACP + DeepCLI _mustang.agent/* JSON-RPC over WebSocket
  v
Access Agent -> Agent Hub Router -> Primary Agent Runtime
```

The current product path is a **single Primary Agent** behind the Agent Hub.
That gives DeepCLI process supervision, reconnect-safe routing,
permission tunneling, `clientTurnId` idempotency, and a clean place to add
future peer Session Agents without changing the client protocol. Platform
adapters such as Discord enter through the Access Agent and route through
the same Hub path instead of calling session internals directly.

Inside the Primary Agent Runtime, the Mustang kernel subsystem layout is
still the agent core:

```text
transport -> protocol -> session -> orchestrator
                              |
                              v
LLM routing / tools / skills / MCP / memory / hooks / authz / persistence
```

Active code lives under `src/`:

- `src/kernel/` - the Mustang kernel and supervised agent-control-plane processes: Supervisor, Agent Hub, Access Agent, Primary Agent Runtime, session orchestration, tools, providers, memory, protocol handling, and runtime persistence.
- `src/cli/` - a thin TypeScript/Bun ACP client.
- `src/probe/` - an interactive and automated ACP test client.
- `src/launcher/` - user-local launchers and release packaging for Linux, macOS, and Windows.
- `archive/` - old daemon-era reference code; not active development.

## Quick Start

DeepCLI is still alpha software. Linux x86_64, macOS x86_64 / Apple Silicon,
and Windows x86_64 have user-local release installers.

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/Harinlen/DeepCLI/releases/latest/download/install.ps1 | iex"
```

Open a new terminal tab after install if this shell still cannot find
`deepcli`.

Linux / WSL2:

```bash
sh -c "$(curl -fsSL https://github.com/Harinlen/DeepCLI/releases/latest/download/install.sh)"
```

If `~/.local/bin` is not already on your shell `PATH`, add it after install:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

macOS:

```bash
sh -c "$(curl -fsSL https://github.com/Harinlen/DeepCLI/releases/latest/download/install-macos.sh)"
```
Then start DeepCLI:

```text
deepcli
```

The `deepcli` launcher keeps the Kernel as a per-user singleton. It checks for
an existing ready Kernel, starts one in the background when needed, chooses
`127.0.0.1:8200` by default, and falls back to a free port if the default is
already occupied.

Useful launcher commands:

```bash
deepcli status
deepcli stop
deepcli restart
deepcli kernel logs
deepcli --resume <session-id>
deepcli --uninstall
```

## Install From Source

To install the current checkout into the same user layout as a release:

Linux / WSL2:

```bash
git clone https://github.com/Harinlen/DeepCLI.git deepcli
cd deepcli
./install-dev.sh
```

macOS:

```bash
git clone https://github.com/Harinlen/DeepCLI.git deepcli
cd deepcli
./install-dev.sh
```

Windows PowerShell:

```powershell
git clone https://github.com/Harinlen/DeepCLI.git deepcli
cd deepcli
.\install-dev.ps1
```

This builds release-shaped local artifacts from the checkout, installs a
precompiled CLI executable, prepares the Kernel runtime with DeepCLI-private
`uv` and managed Python, and updates the user-local `deepcli` launcher.

## Source Development

For source-only development without installing, start the dev Supervisor first:

```bash
scripts/run-kernel.sh
```

Then, in another terminal, run the CLI from source:

```bash
scripts/run-cli.sh
```

`scripts/run-cli.sh` forwards CLI arguments, including `--resume`, `--port`,
and `--kernel`.

## Development

For first-time setup, read [`INIT.md`](INIT.md).  For project rules, architecture, workflow, and current progress, start with [`docs/README.md`](docs/README.md).

Useful commands:

```bash
uv run pytest tests/ -q
./resolve-ref.sh claude-code
./resolve-ref.sh openclaw
./resolve-ref.sh hermes-agent
```

## Status

DeepCLI is in alpha. Kernel 1.0.0 is online with the supervised single-Primary Agent Control Plane, ACP transport, SQLite-backed sessions, reconnect-safe prompt idempotency, permission tunneling, tool authorization, LLM provider routing, skills, hooks, memory, MCP, gateways, and release-shaped Linux/Windows local installers.

The current source of truth for active work is [`docs/plans/progress.md`](docs/plans/progress.md).

## License

[MIT](LICENSE) - Copyright (c) 2026 Haolei (Saki) Ye.
