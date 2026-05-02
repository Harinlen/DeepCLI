# DeepCLI

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&colorA=222222&logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/Kernel-v2.0.0-F4A261?style=flat&colorA=222222" alt="Kernel v2.0.0">
  <img src="https://img.shields.io/badge/Status-Alpha-orange?style=flat&colorA=222222" alt="Status: Alpha">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-58A6FF?style=flat&colorA=222222" alt="License: MIT"></a>
</p>

<p align="center">
  <em>The agent that reinvents software.</em>
</p>

DeepCLI is an open, modular agent harness for the **DeepSeek V4** era.  It
wraps the model with the parts a real coding agent needs: tools, sessions,
memory, skills, permissions, provider routing, and a clean protocol boundary
between clients and runtime.  It borrows the inner loop instincts of Claude
Code, the independent multi-agent shape of OpenClaw, and the practical Python
runtime patterns of Hermes Agent, then rebuilds them as one small, hackable
kernel.

This repo is also the point of the experiment: **DeepCLI is being built
entirely by AI Coding Agents**.  Humans steer the taste, product direction,
and acceptance bar; agents write the code, run the tests, fix the bugs, and
keep the system moving.  The codebase is deliberately easy to take apart:
frontends talk to the kernel over ACP/JSON-RPC on WebSocket, the backend is
split into replaceable subsystems, and anyone can swap a client, add a tool,
change the agent loop, or route the same harness to another model provider.

DeepCLI keeps **Mustang** as the kernel codename and compatibility namespace:
`mustang-kernel`, `~/.mustang`, `MUSTANG_*`, and `_mustang.agent/*`.

## Architecture

```text
Clients
  CLI / Probe / IDE / future Home Screen / custom frontend
    |
    | ACP + JSON-RPC over WebSocket
    v
DeepCLI Kernel
  transport -> protocol -> session -> orchestrator
                                |
                                v
       LLM routing / tools / skills / MCP / memory / hooks
                                |
                                v
                 authz / config / persistence / gateways
```

Active code lives under `src/`:

- `src/kernel/` - the Mustang kernel, a FastAPI runtime for sessions,
  orchestration, tools, providers, memory, and protocol handling.
- `src/cli/` - a thin TypeScript/Bun ACP client.
- `src/probe/` - an interactive and automated ACP test client.
- `archive/` - old daemon-era reference code; not active development.

## Quick Start

DeepCLI is still alpha software.  Run it from source:

```bash
git clone <repo-url> deepcli
cd deepcli
uv sync
uv run pytest -q tests/
```

Start the kernel and probe:

```bash
src/run-kernel.sh
src/run-probe.sh
```

The kernel listens on:

```text
ws://127.0.0.1:8200/session
```

Any ACP-capable client can connect to that WebSocket endpoint.

## Development

For first-time setup, read [`INIT.md`](INIT.md).  For project rules,
architecture, workflow, and current progress, start with
[`docs/README.md`](docs/README.md).

Useful commands:

```bash
uv run pytest tests/ -q
./resolve-ref.sh claude-code
./resolve-ref.sh openclaw
./resolve-ref.sh hermes-agent
```

## Status

DeepCLI is in alpha.  Kernel 2.0.0 is online with ACP transport,
SQLite-backed sessions, tool authorization, LLM provider routing, skills,
hooks, memory, MCP, gateways, and the Agent Control Plane groundwork.

The current source of truth for active work is
[`docs/plans/progress.md`](docs/plans/progress.md).

## License

[MIT](LICENSE) - Copyright (c) 2026 Haolei (Saki) Ye.
