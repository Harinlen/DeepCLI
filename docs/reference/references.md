# Reference Projects

> _The agent that reinvents software._ 🐎

**DeepCLI is a Personalize Dynamic Software (PDS) — the next
generation of software.**  The main UX is a chat with a primary
agent (Claude-Code-CLI-like); the deeper purpose is to help each
user build up a library of personalized software — plugins,
template-apps, and session agents — that runs on the same kernel
and is eventually surfaced through a Home Screen launcher (planned
separate frontend repo).

The three reference projects are how the **kernel layer** is built.
They do **not** cover the PDS product layer above the kernel (Home
Screen, user software library, the three software shapes) — that
layer is DeepCLI's own and has no direct counterpart to copy from.

Each reference contributes one slice of the kernel:

```
Claude Code  → Inner harness      (agent loop, tool use, memory, compaction,
                                    slash commands, plan mode, skills)
OpenClaw     → Outer architecture (kernel/client split, plugin system, policy
                                    pipeline, config lifecycle)
              + Multi-agent model (session-per-agent, independent lifecycle —
                                    not Claude Code's sub-agent-as-tool)
Hermes Agent → Python realisation (ACP adapter, multi-platform messaging
                                    gateway, SQLite session store, prompt
                                    caching, skill/tool config)
DeepCLI      → kernel = inner + outer + multi-agent + Python idioms, fused
                        into one engine
                PDS  = the product layer above the kernel — primary agent
                        chat + user-built software library (plugin /
                        template-app / session-agent) + Home Screen
```

Paths are per-machine; use `./resolve-ref.sh <name>` to resolve.

DeepCLI's path to PDS is **long-term collaborative authoring** —
the agents help the user build durable, composable, shareable
software over many sessions, not per-turn throwaway UI.

The kernel is **not** any one of the references with a new skin.
On top of what they contribute, DeepCLI pursues three goals none of
the three does on its own:

1. **PDS substrate** — memory + skills + hooks + MCP form the
   foundation by which chat becomes durable software.  The kernel's
   job is to make *"agent-assisted software authoring"* a
   first-class runtime concern, not a side workflow.
2. **Self-evolution** — the same substrate makes the kernel
   accumulate user- and project-specific knowledge across sessions
   instead of starting cold every run.
3. **Multi-model benchmarking** — the provider-agnostic engine is
   the point, not a byproduct.  Same library, swap LLM (Anthropic /
   OpenAI / local Qwen / …), measure on real workloads; the SQLite
   session log (D20) doubles as the evaluation data source.

**Arbitration when references disagree**:

- Agent-loop concerns (tool round-trip, prompt assembly, compaction,
  plan mode, skills) → Claude Code wins.
- Kernel / plugin / policy / config-lifecycle concerns, *and the
  multi-agent model* → OpenClaw wins.
- "How does this actually look in Python / async / Pydantic?" (ACP
  adapter, gateway adapters, schema, idioms) → Hermes wins.

When all three are silent or disagree on something DeepCLI-specific
(user software library, Home Screen surface, three software shapes,
benchmarking affordances, kernel/frontend split over WebSocket +
ACP), DeepCLI makes its own decision — recorded in
[`decisions.md`](decisions.md).

---

## Claude Code (logical: `claude-code`)

Anthropic's official CLI for Claude. TypeScript + Bun + React/Ink.
~1,884 files, 512k+ LOC, 88+ slash commands, 44+ tools.

**Borrowed**:
- Tool interface shape (`name`, `description`, `input_schema`,
  `permission_level`, `execute()`)
- System-prompt assembly (working-dir context, AGENTS.md discovery,
  tool descriptions, PromptSection caching boundary)
- Context compression (`autoCompact` LLM summary + 4-layer strategy, D15)
- Built-in tool set (bash, file_read (text + image + PDF)/write/edit,
  glob, grep, web_fetch, agent)
- Slash-command conventions (`/help /clear /compact /cost /model`)
- Git context snapshot semantics (captured at session start, not
  refreshed)

**Not copied**:
- React/Ink terminal UI → Rich + prompt_toolkit
- Single-process architecture → kernel-first
- Anthropic-only → multi-provider
- Hardcoded tools → extensible plugin system

---

## OpenClaw (logical: `openclaw`)

Personal AI assistant with 20+ messaging channels. TypeScript + Node.
~5,120 files, gateway + agent-runtime split.

**Architectural patterns borrowed**:

- **Kernel/client split** (highest-value): channels → gateway →
  agent runtime maps to CLI/Web → kernel → engine
- **Policy pipeline** for tool permissions (D5)
- **Session key routing** `{scope}:{owner}:{id}` (D6)
- **Source vs runtime config** (D7)
- **Plugin architecture** (lazy-loaded, config-driven, event-driven)

### Reusable OpenClaw patterns (post-MVP reference)

**Config lifecycle (7 steps)**: read YAML → expand `${VAR}` →
legacy migration → path normalisation → function-based defaults →
merge extension schemas → Pydantic validation. Key trick:
function-based defaults compute values at merge time using other
config values or runtime context. Secrets should round-trip as
`${VAR}` references — don't expand-and-write.

**Tool middleware chain**: wrap tools layer by layer — abort signal,
permission check, before/after hooks, workspace guard, timeout,
error boundary. Apply global policy (`disabled`, `overrides`) last.

**Extension discovery**: scan `manifest.yaml` only at startup
(cheap); `importlib.import_module()` at first use (lazy).
Reject symlink escape and world-writable paths.

**Hook event naming**: `domain:action` (`tool:before_call`,
`tool:after_call`, `session:start`, `engine:error`). Hook runner is
fail-open — hook failures don't block the main pipeline unless a
hook explicitly returns `action="abort"`.

**Skill eligibility check**: `requires.bins`, `requires.env_vars`,
`requires.python_packages` verified before activation.

**Not copied**: 20+ messaging channel integrations, device pairing,
browser/canvas native tools, multi-auth credential rotation,
directory-format skills (replaced by single-file skills per D12).

---

## Hermes Agent (logical: `hermes-agent`)

Python AI harness (`run_agent.py` + `cli.py` + `gateway/` + `acp_adapter/`).
Same language + many of the same concerns as DeepCLI — so it's the
closest thing to a working reference implementation, and the place to
look first when a design question is "how does this actually shape up
in Python / async / Pydantic".

**Borrowed (or to-be-borrowed) patterns**:

- **ACP adapter** (`acp_adapter/server.py`, `session.py`, `events.py`,
  `permissions.py`) — a production ACP server talking to IDEs (Zed,
  VS Code, JetBrains). Primary reference for our
  [`kernel/interfaces/protocol.md`](protocol.md) layer: request
  dispatch, session lifecycle, event mapping, permission prompts.
- **Gateway / GatewayManager** (`gateway/run.py`, `gateway/session.py`,
  `gateway/platforms/*`) — 20+ messaging platform adapters under a
  single `base.py` ABC plus a shared `SessionStore`. Primary reference
  for the upcoming GatewayManager
  ([`kernel/subsystems/gateways.md`](../kernel/subsystems/gateways.md)):
  adapter shape, per-platform auth, session mapping from external
  thread id → internal session.
- **SQLite session store with FTS5** (`hermes_state.py::SessionDB`) —
  reference for the session-storage migration
  ([`kernel/subsystems/session.md`](../kernel/subsystems/session.md)):
  schema, index layout, search queries.
- **Prompt assembly + caching** (`agent/prompt_builder.py`,
  `agent/prompt_caching.py`, `agent/context_compressor.py`) — Python
  idioms for Claude-Code-style system-prompt assembly and autoCompact.
- **Slash command registry** (`hermes_cli/commands.py`
  `COMMAND_REGISTRY` + `CommandDef`) — one source of truth reused by
  CLI autocomplete, gateway `/help`, and ACP command metadata.
  Reference for CommandManager
  ([`kernel/subsystems/commands.md`](../kernel/subsystems/commands.md)).
- **Tool registry + middleware** (`tools/registry.py`, `tools/approval.py`,
  dispatch inside `model_tools.py`) — Python realisation of the
  Claude-Code tool interface with approval / dangerous-command
  detection bolted on.
- **Skill / tool config per platform** (`hermes_cli/skills_config.py`,
  `hermes_cli/tools_config.py`) — how enable/disable flags propagate
  across CLI, gateway, and ACP surfaces.
- **Auxiliary LLM client** (`agent/auxiliary_client.py`) — separate
  cheap/fast model for vision + summarisation + compaction, orthogonal
  to the main conversation model. Relevant to LLMManager routing.

**Not copied**:

- Direct OpenAI-style sync `run_conversation()` loop — DeepCLI's
  orchestrator is async and stream-first (D-series decisions).
- `hermes_cli/` skin/theme engine, model-catalog UI, setup wizard —
  frontend concerns, out of kernel scope.
- RL training environments (`environments/`, `tinker-atropos/`,
  `batch_runner.py`) — research infra, unrelated to the harness.
- Per-platform features baked into adapters (e.g. Telegram message
  splitting, Discord slash-command syntax) — kept behind the adapter
  boundary, not pulled into the kernel.

---

## Detailed Comparison

See [`claude-code-comparison.md`](claude-code-comparison.md) for a
full feature-by-feature and prompt-by-prompt comparison (written
2026-04-06).  Covers prompt system, tool inventory, engine/orchestrator,
permissions/config/extensions, and a prioritised improvement matrix.
