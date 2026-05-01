# DeepCLI

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&colorA=222222&logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/Kernel-v1.0.0-F4A261?style=flat&colorA=222222" alt="Kernel v1.0.0">
  <img src="https://img.shields.io/badge/Status-Alpha-orange?style=flat&colorA=222222" alt="Status: Alpha">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-58A6FF?style=flat&colorA=222222" alt="License: MIT"></a>
  <a href="https://github.com/Harinlen/deepcli/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/Harinlen/deepcli/tests.yml?branch=main&style=flat&colorA=222222&colorB=3FB950&label=Tests" alt="Tests"></a>
  <a href="https://github.com/Harinlen/deepcli/actions/workflows/tests.yml"><img src="https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Harinlen/1b23b9cda527690c61fb08082ac7e1b3/raw/coverage.json&style=flat&colorA=222222" alt="Coverage"></a>
</p>

<p align="center">
  <em>The agent that reinvents software.</em>
</p>

**DeepCLI is a Personalize Dynamic Software (PDS) — the next generation of software.**
The main UX is a chat with your primary agent — close in feel to
Claude Code CLI.  The deeper purpose is to help you **author a
growing library of personalized software**: small plugins,
template-apps, and long-lived session agents, all living on one
runtime and eventually surfaced through a single Home Screen
launcher.  Your library compounds session by session; the software
is durable, composable, and shareable.

---

## Three layers

| Layer | What it is | Where it lives |
|---|---|---|
| **Home Screen** | Unified entry.  A Shortcuts / iOS-home-screen-style launcher + dashboard: browse the software you've built, launch it, pin widgets, share, and see what's running in the kernel. | *Planned separate frontend repo* |
| **Multi-agent Kernel** | Runtime engine.  One **primary agent** chats with you (main UX); **session agents** run independently in their own sessions (OpenClaw-style — not Claude Code's sub-agent-as-tool).  Self-evolving via memory, skills, and hooks. | *This repo* |
| **Your software library** | The product.  The growing pile of personalized software you've built — three shapes, below. | *Your DeepCLI data dir* |

## What you build

Every piece of software in your library takes one of three shapes:

| Shape | What it is | Example |
|---|---|---|
| **Plugin** | An atomic contribution — a skill, a UI template, a tool, an MCP server.  Plugs into the kernel's registry; available to agents and to other software. | A custom `/standup` skill · a "news card" UI template · a CoinGecko tool |
| **Template-App** | A UI template + config + a little glue code.  A small composed application with no agent loop of its own; runs like a widget. | A customized TradingView wired to your favourite tokens · a morning digest dashboard |
| **Session Agent** | A configured agent with its own skills, tools, prompt, and memory scope, running long-lived in its own session.  You can open a chat with it anytime. | A "Research Assistant" · an "Email Triage" agent · a pair-programmer scoped to one repo |

All three show up as icons / widgets on the Home Screen, are
one-click shareable, and can be evolved further by asking your
primary agent to refine them.

---

## How it's shaped

<table>
<tr><td><b>Chat is the default surface, not the only one</b></td><td>The primary agent's chat is the default UX — ACP over WebSocket, Claude-Code-CLI-like.  But the Home Screen, IDE extensions, terminal probe, and messaging gateways are all thin ACP clients against the same kernel.</td></tr>
<tr><td><b>Multi-agent, OpenClaw-style</b></td><td>Spawning a session agent is not the Claude-Code sub-agent-as-tool pattern (synchronous, blocking, returns a string).  Each session agent owns its own session, its own state, its own lifetime.  The primary agent observes and coordinates without blocking.</td></tr>
<tr><td><b>Self-evolving by design</b></td><td>Memory (global + per-project, agent-curated) · Skills (single-file markdown, lazy-loaded) · Hooks (event-driven interception) form a closed loop so the kernel accumulates knowledge and capability across sessions instead of starting cold every run.</td></tr>
<tr><td><b>Built software is first-class runtime state</b></td><td>Plugins / template-apps / session agents aren't side artifacts — they are indexed, versioned, introspectable from the Home Screen, and shareable by export.</td></tr>
<tr><td><b>Bring any LLM</b></td><td>Anthropic, OpenAI (or OpenAI-compatible: DeepSeek, MiniMax, z.ai/GLM, Moonshot/Kimi, Qwen, local llama.cpp / Ollama / vLLM), AWS Bedrock, or your own endpoint.  Context windows auto-detected from the provider, never hardcoded.  Swap with a config change — your library keeps working.</td></tr>
<tr><td><b>Multi-model benchmarkable</b></td><td>Same agent, same skills, same hooks — swap LLM and compare.  SQLite session log records every turn with per-call token counts and latency, directly consumable as an evaluation dataset.</td></tr>
<tr><td><b>Highly modular kernel</b></td><td>Every subsystem (Config, Auth, Tools, Skills, Hooks, MCP, Memory, Session, Orchestrator, LLM, Gateway, Command) is independent — disable, replace, or remove without cascading breakage.  Cross-module contact only via Protocol / ABC.</td></tr>
<tr><td><b>Protocol-native</b></td><td>ACP (Agent Client Protocol) for client ↔ kernel — native IDE support.  MCP as first-class extension protocol — servers declared in config auto-connect on startup, their tools transparently appear in the registry.</td></tr>
<tr><td><b>Two-layer auth model</b></td><td>AuthN / AuthZ split into independent subsystems: <code>ConnectionAuthenticator</code> gates WS connections (loopback-only, token + password, scrypt-hashed); <code>ToolAuthorizer</code> gates tool calls (layered rules + session grants + bash classifier).  See <a href="docs/reference/decisions.md">D22</a>.</td></tr>
<tr><td><b>Runs anywhere small</b></td><td>Designed to run on Raspberry Pi 4/5 (4 GB RAM): idle &lt; 50 MB RSS, extensions lazy-loaded, cold start &lt; 3 s.  No cloud dependency beyond the LLM API itself.</td></tr>
</table>

---

## How the kernel is built

The kernel layer is triangulated against three external references,
each covering one slice of the engineering.  The Home Screen and
the user-authored-software concept above the kernel are
DeepCLI-specific — no direct counterpart to copy from.

- **Claude Code** — inner harness: agent loop, tool use, memory,
  compaction, plan mode, skills, slash commands.
- **OpenClaw** — outer architecture *and* the multi-agent model
  (session-per-agent, independent lifecycle).
- **Hermes Agent** — Python realisation: ACP adapter, multi-platform
  gateway, SQLite session store, prompt caching.

DeepCLI is not a rewrite of any one of them.  Arbitration rules
when references disagree live in
[`docs/reference/references.md`](docs/reference/references.md).

---

## Kernel codename: Mustang

DeepCLI is the product name.  **Mustang** remains the kernel codename
and the name of the legacy state/protocol namespace (`mustang-kernel`,
`~/.mustang`, `MUSTANG_*`, `_mustang.agent/*`).

The codename comes from the original kernel metaphor: a mustang is a
wild horse — raw power, unusable for work until it is tacked up with
bit, saddle, and reins.  The kernel is the harness; the LLM is the
horse; DeepCLI is the product built around that harness.

---

> **Project status: Alpha.**  Kernel 1.0.0 is online (SQLite session
> storage, ConnectionAuthenticator, CommandManager, GatewayManager).
> Multi-agent session support and the three user-software shapes
> (plugin / template-app / session agent) are the active
> implementation front.  The Home Screen frontend is planned as a
> separate repo and has not started yet.  See
> [`docs/plans/progress.md`](docs/plans/progress.md) for the current
> phase and outstanding work; archived TUI / CLI frontends are being
> replaced by new thin ACP clients.

---

## Quick Start

DeepCLI is pre-release — no public install script yet.  Clone and
run from source:

```bash
git clone <repo-url> deepcli && cd deepcli
uv sync                 # install dependencies (uv workspace)
uv run pytest -q tests/ # verify the install
```

Then point an AI coding agent (Claude Code, Codex, …) at the repo
root and say:

> Read `INIT.md` and set up my development environment.

The agent runs the remaining setup — `.mustang-refs.yaml` for
reference-project paths, and a final dev-environment sanity check.
See [`INIT.md`](INIT.md) for the full bootstrap protocol.

---

## Getting Started

```bash
# Start the kernel (FastAPI + uvicorn on :8200, auto-reload)
src/run-kernel.sh

# Start the probe — interactive ACP test client
src/run-probe.sh
```

The kernel speaks ACP over WebSocket at `ws://127.0.0.1:8200/session`.
Any ACP-capable client (Zed, probe, or your own) can connect.

To see what the kernel currently does:

```bash
uv run pytest tests/ -q                # full test suite
./resolve-ref.sh claude-code     # resolve a reference project
./resolve-ref.sh openclaw
./resolve-ref.sh hermes-agent
```

---

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────┐
│                 Mustang Kernel (DeepCLI runtime)                 │
│                                                                  │
│  transport ──▶ protocol ──▶ session                              │
│   (WS accept)  (ACP)        (SessionHandler, Orchestrator)       │
│       │            │              │                              │
│       ▼            ▼              ▼                              │
│  ConnectionAuthN   protocol/*     Orchestrator ──▶ LLMManager    │
│                                         │                        │
│                                         ▼                        │
│                          Tools / Skills / MCP / Memory / Hooks   │
│                                         │                        │
│                                         ▼                        │
│                          ToolAuthorizer (layered rules + grants) │
│                                                                  │
│  ConfigManager (layered: default → user → project)               │
│  FlagManager   (start-time FuseBox, runtime-immutable)           │
│  SessionStore  (SQLite WAL, append-only event log)               │
└──────────────────────────────────────────────────────────────────┘
```

Frontends (Home Screen / IDE / messaging gateway / probe) never
import kernel internals.  WebSocket + ACP is the only coupling.

---

## Documentation

All documentation lives under [`docs/`](docs/).  Start with
[`docs/README.md`](docs/README.md) — single navigation hub.

| Section | What's Covered |
|---|---|
| [docs/README.md](docs/README.md) | Docs index + responsibility map |
| [AGENTS.md](AGENTS.md) | Agent bootstrap — read this first in a fresh session |
| [reference/decisions.md](docs/reference/decisions.md) | D1–D22 design decisions (follow unless user overrides) |
| [reference/references.md](docs/reference/references.md) | Claude Code / OpenClaw / Hermes — roles & arbitration rules |
| [reference/prompts.md](docs/reference/prompts.md) | System-prompt assembly, prompt-file index |
| [kernel/overview.md](docs/kernel/overview.md) | Kernel goals, design principles, tech stack |
| [kernel/architecture.md](docs/kernel/architecture.md) | Subsystem topology, lifespan, Subsystem ABC |
| [kernel/subsystems/](docs/kernel/subsystems/) | Per-subsystem design docs |
| [kernel/interfaces/protocol.md](docs/kernel/interfaces/protocol.md) | ACP adoption profile, handlers, events, cancellation |
| [kernel/references/acp/](docs/kernel/references/acp/) | Local ACP spec snapshot |
| [workflow/workflow.md](docs/workflow/workflow.md) | 5-phase implementation flow |
| [workflow/code-quality.md](docs/workflow/code-quality.md) | Post-impl checklist (mandatory) |
| [plans/progress.md](docs/plans/progress.md) | Current phase + completed steps |
| [plans/roadmap.md](docs/plans/roadmap.md) | Future phases |
| [plans/backlog.md](docs/plans/backlog.md) | Deferred features from design docs |
| [lessons-learned.md](docs/lessons-learned.md) | Gotchas, design debt |

---

## Reference Projects

The kernel layer is triangulated against three external projects.
Per-machine paths live in `.mustang-refs.yaml` (gitignored); resolve
via `./resolve-ref.sh <logical-name>`.

| Logical name | Role | Why |
|---|---|---|
| `claude-code` | Inner harness blueprint | Agent loop, tool use, memory, compaction, plan mode, skills.  When a harness question is unclear, find the Claude Code counterpart and adapt. |
| `openclaw` | Outer architecture + multi-agent model | Daemon/client split, plugin system, policy pipeline, config lifecycle.  Also the reference for session-per-agent, which DeepCLI's multi-agent model follows instead of Claude Code's sub-agent-as-tool. |
| `hermes-agent` | Python stack-match | ACP adapter, multi-platform messaging gateway, SQLite session store, prompt caching.  Closest real-world Python implementation. |

Full mental model + arbitration rules:
[docs/reference/references.md](docs/reference/references.md).

---

## Project Layout

```
deepcli/
├── INIT.md                     # First-time dev setup (for agents)
├── AGENTS.md                   # Canonical agent entry point
├── CLAUDE.md                   # Redirect to AGENTS.md (for Claude Code)
├── README.md                   # This file
├── pyproject.toml              # uv workspace root
├── src/
│   ├── run-kernel.sh           # Start the kernel (uvicorn, :8200)
│   ├── run-probe.sh            # Start the ACP test client
│   ├── kernel/                 # mustang-kernel — FastAPI server (ACTIVE)
│   │   └── kernel/
│   │       ├── app.py          # FastAPI app + lifespan
│   │       ├── routes/         # health (GET /) + session (WS /session)
│   │       ├── config/ auth/ flags/
│   │       ├── llm/ llm_provider/
│   │       ├── tools/ skills/ hooks/ mcp/ memory/
│   │       ├── session/ orchestrator/ protocol/
│   │       ├── commands/ gateways/
│   │       └── module_table.py # Subsystem registry
│   └── probe/                  # deepcli-probe — interactive ACP client
├── tests/
│   ├── kernel/                 # Per-subsystem tests
│   └── probe/                  # End-to-end via ACP
├── docs/                       # All project rules & designs
├── archive/                    # Daemon-era code (READ-ONLY)
│   ├── cli/  tui/  daemon/
└── scripts/
    └── resolve-ref.sh          # Reference-project path lookup
```

The Home Screen frontend will live in a **separate repo** (planned;
not started).  It will talk to this kernel over ACP like every
other frontend.

**Hard rules**:
- `src/kernel/` is the only active codebase — `archive/` is
  read-only reference material.
- Frontends never import kernel internals.  Kernel never imports
  frontend code.  The only coupling is WebSocket + ACP.

---

## Contributing

DeepCLI isn't open to outside contributions yet (pre-public alpha),
but the contribution flow is the same as local dev:

```bash
git clone <repo-url> deepcli && cd deepcli
uv sync
uv run pytest -q tests/
```

Before submitting any change, the post-implementation checklist in
[`docs/workflow/code-quality.md`](docs/workflow/code-quality.md) is
**mandatory** — 5 steps covering tests, types, lint, comment density,
and progress-doc update.

Design decisions should be added to
[`docs/reference/decisions.md`](docs/reference/decisions.md)
as a new `D<n>` entry; architectural changes must update
[`docs/kernel/architecture.md`](docs/kernel/architecture.md)
and the relevant `docs/kernel/subsystems/*.md`.

---

## License

[MIT](LICENSE) — Copyright (c) 2026 Haolei (Saki) Ye.
