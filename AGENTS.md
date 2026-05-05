# DeepCLI — Agent Instructions

When answering, reason from first principles: start from the raw
need and the essence of the problem, not from convention or templates.

1. Do not assume I clearly know what I want. If the motivation or
   goal is unclear, stop and discuss it.
2. If the goal is clear but the path is not the shortest one, tell me
   directly and propose a better way.
3. When problems arise, trace the root cause; do not patch symptoms.
   Every decision must be able to answer "why".
4. Keep output focused. Cut everything that does not change the
   decision.

> **READ-ONLY ENTRY FILE.  DO NOT EDIT TO CHANGE BEHAVIOR.**
>
> This file is a bootstrap pointer.  All project rules, workflows,
> and decisions live under `docs/`.  To change agent behavior, edit
> the appropriate file in `docs/` — **not this file**.
> See [`docs/entry-files-policy.md`](docs/entry-files-policy.md).
>
> **First time on this machine?** See [`INIT.md`](INIT.md) first.

**Slogan**: _The agent that reinvents software._

DeepCLI **is** a **Personalize Dynamic Software (PDS)** — the next
generation of software.  The main UX is a conversation with a
**primary agent** (close in feel to Claude Code CLI); the deeper
purpose is to help each user **build a growing library of
personalized software** that runs on the same kernel and is
eventually surfaced through a single Home Screen launcher.  The
library is durable, composable, and compounds session by session.

**Naming**: DeepCLI is the project/product name.  **Mustang** remains
the kernel codename and compatibility namespace (`mustang-kernel`,
`~/.mustang`, `MUSTANG_*`, `_mustang.agent/*`).

**Three layers to keep straight**:

1. **Home Screen** — the unified entry.  A Shortcuts / iOS-style
   launcher + dashboard where the user browses, launches, and
   widgets the software they've built, and inspects kernel /
   session state.  *Planned separate frontend repo.  Not in this
   repo.*
2. **Multi-agent Kernel** — the runtime.  A **primary agent**
   chats with the user; **session agents** run independently in
   their own sessions (OpenClaw-style — not Claude Code's
   sub-agent-as-tool).  Self-evolving via memory / skills / hooks.
   *This repo.*
3. **User software library** — the product.  Every piece falls
   into one of three shapes (see below).

**Three shapes of user-built software** (the "SW" in PDS):

- **Plugin** — atomic unit: a skill, a UI template, a tool, an
  MCP server.  Registered with the kernel, callable by agents
  and other software.
- **Template-App** — UI template + config + light glue code.  A
  small composed application without its own agent loop (e.g.,
  a customized TradingView wired to specific tokens).
- **Session Agent** — a configured agent (own skills / tools /
  prompt / memory scope) running long-lived in its own session.
  The user can open a chat with it any time.

**Project goals**:

1. **PDS** — help users assemble their own personal software
   library over time, via chat collaboration with agents.
2. **Self-evolution** — memory + skills + hooks make the kernel
   accumulate user- and project-specific knowledge across sessions
   instead of starting cold every run.
3. **Multi-model benchmarking** — the provider-agnostic kernel is
   the point, not a side effect.  Same library, swap LLM, compare
   on real tasks.

**Kernel is built from three references** (not a rewrite of any
one):

- **Claude Code** — inner harness (agent loop, tool use, memory,
  compaction).
- **OpenClaw** — outer architecture *and* multi-agent model
  (session-per-agent, independent lifecycle).
- **Hermes Agent** — Python realisation (ACP adapter, gateway,
  SQLite session store).

Which reference wins when they disagree:
[`docs/reference/references.md`](docs/reference/references.md).

---

## Bootstrap

**Start here**: [`docs/README.md`](docs/README.md) — single
navigation hub with the full responsibility map.

> ⚠️ **Definition of Done — read this before claiming any
> implementation is complete.**  Unit tests passing is necessary
> but not sufficient.  Every closure seam (callable wired across
> subsystem boundaries) requires a probe against the real
> subsystem before the change is done.  See
> [`docs/workflow/definition-of-done.md`](docs/workflow/definition-of-done.md)
> — five gates, one page.  This rule has been violated three
> times already; do not make it four.

Minimum reading list before implementation work:

1. [`docs/README.md`](docs/README.md) — docs index + time-state
   convention (future / active / pending / past)
2. [`docs/kernel/architecture.md`](docs/kernel/architecture.md)
   — system layout, WS protocol, subsystem topology
3. [`docs/reference/decisions.md`](docs/reference/decisions.md)
   — D1–D22 + deferred, follow unless user overrides
4. [`docs/workflow/workflow.md`](docs/workflow/workflow.md) — 6-phase
   implementation flow (Phase 4 E2E + **Phase 4.5 closure-seam
   inventory**, mandatory)
5. [`docs/workflow/definition-of-done.md`](docs/workflow/definition-of-done.md)
   — the five gates; probe output must be pasted in the report
6. [`docs/workflow/code-quality.md`](docs/workflow/code-quality.md)
   — 5-step post-impl checklist (mandatory)
7. [`docs/plans/progress.md`](docs/plans/progress.md) — current
   phase, completed steps
8. [`docs/plans/roadmap.md`](docs/plans/roadmap.md) — future phases
   (high level)
9. [`docs/lessons-learned.md`](docs/lessons-learned.md) — gotchas,
   design debt

Read the others under `docs/kernel/subsystems/` (per-subsystem design
docs) and `docs/reference/` (design decisions, CC comparison) as needed.

**After reading**, check `docs/plans/progress.md` for current
phase, then **confirm with the user** before starting any work.

### User phrases

- **"continue" / "next phase"** → implement the next uncompleted
  item from `docs/plans/roadmap.md` or `docs/plans/backlog.md`.
- **"Code Review"** → execute
  [`docs/workflow/code-review.md`](docs/workflow/code-review.md).

---

## Ground Rules

- **Only do what the user asks.**  No autonomous "next step" —
  wait for instruction.
- **Progress tracking**: `docs/plans/progress.md` is the single
  source of truth.  Update it after every completed step.
- **Record gotchas**: route non-obvious pitfalls, environment
  quirks, deferred design debt to
  [`docs/lessons-learned.md`](docs/lessons-learned.md).
- **Code quality is mandatory**: after any implementation task,
  run the 5-step checklist in
  [`docs/workflow/code-quality.md`](docs/workflow/code-quality.md).
  Don't skip steps, don't mark done until tests pass and comment
  density is 20–25%.

---

## Monorepo Structure

```
src/
└── kernel/              # mustang-kernel (FastAPI server) ← ONLY ACTIVE CODE
    └── kernel/          # import as `kernel`
        ├── app.py       # FastAPI app + lifespan (subsystem lifecycle)
        ├── module_table.py  # KernelModuleTable (live module registry)
        ├── subsystem.py     # Subsystem base class
        ├── routes/      # health (GET /) + session (WS /session)
        ├── flags/       # FlagManager (bootstrap — feature flags)
        ├── config/      # ConfigManager (bootstrap — layered config)
        ├── prompts/     # PromptManager (bootstrap — .txt prompt files)
        ├── connection_auth/  # ConnectionAuthenticator (WS accept AuthN)
        ├── tool_authz/  # ToolAuthorizer (tool call AuthZ)
        ├── llm_provider/# LLMProviderManager (Provider instance lifecycle)
        ├── llm/         # LLMManager (model config + routing)
        ├── mcp/         # MCP server management
        ├── tools/       # tool registry (builtin + custom)
        ├── skills/      # skill discovery + lazy loading
        ├── hooks/       # event-driven hooks
        ├── memory/      # long-term memory (global + project)
        ├── session/     # session lifecycle + SQLite persistence
        ├── tasks/       # TaskRegistry (in-memory task state + output collection)
        ├── plans.py     # plan file management (slug generation, persistent storage)
        ├── commands/    # CommandManager (slash command catalog)
        ├── gateways/    # GatewayManager (external messaging platforms)
        ├── protocol/    # ACP protocol layer (JSON-RPC ↔ Pydantic)
        └── orchestrator/# conversation engine core (per-session)

archive/                 # ARCHIVED — no longer under active development
└── daemon/              # original codebase (superseded by kernel)
```

**Hard rules**:
- `src/kernel/` is the only active codebase — do not modify `archive/`
- Frontends never import kernel internals — WebSocket is the only coupling
- Kernel never imports frontend code
- Kernel is self-contained under `src/kernel/kernel/`
- Kernel design docs: `docs/kernel/`

---

## Reference Projects

Paths are per-machine.  Look them up with:

```bash
./resolve-ref.sh <logical-name>
```

| Logical name | Purpose |
|---|---|
| `claude-code` | **Implementation blueprint.**  Before designing any feature, find the corresponding implementation in Claude Code's source and adapt — don't invent from scratch. |
| `openclaw` | Daemon architecture, plugin system, policy pipeline patterns. |
| `hermes-agent` | Python AI harness — closest stack-match to DeepCLI.  Primary reference for the ACP adapter (`acp_adapter/`) and the multi-platform messaging gateway (`gateway/platforms/`, 20+ platforms). |

If `.mustang-refs.yaml` doesn't exist, copy
`.mustang-refs.example.yaml` over and fill in paths (or ask the
user).

---

## Agent-specific

Both Claude Code and Codex read this file (or `CLAUDE.md`, which
redirects here).  Agent-specific configuration (tool permissions,
model preferences) lives in each user's local agent config, not
in this repo.

When in doubt, check `docs/README.md` first.  If a rule isn't
documented anywhere, ask the user.
