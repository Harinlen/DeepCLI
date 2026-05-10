# Kernel Architecture

This document is the current topology map.  It should stay short:
subsystem internals belong in [`subsystems/`](subsystems/), protocol details
belong in [`interfaces/protocol.md`](interfaces/protocol.md), and shipped
milestones belong in [`history.md`](history.md).

## Runtime Topology

DeepCLI runs as a supervised local Agent Control Plane.  The CLI does not own
the agent loop and does not import Kernel Python modules.

```text
deepcli launcher
  |
  | starts / monitors one user-local runtime
  v
Supervisor
  |-- Agent Hub
  |     |-- Router                 user -> agent / agent -> agent / agent -> user
  |     |-- Manager                runtime records, status, control
  |     `-- GlobalResourceMonitor  shared resource revision boundary
  |
  |-- Access Agent                 FastAPI edge, health/readiness, WS /session
  |
  `-- Primary Agent Runtime        real SessionManager + Orchestrator path
```

Request path:

```text
CLI / Probe / future Home Screen / Platform Adapter
  -> Access Agent
  -> Agent Hub.Router
  -> Primary Agent Runtime
  -> SessionManager
  -> Orchestrator
  -> LLM / tools / memory / hooks / MCP
```

The current product path is a single default durable agent: `primary`.
The Hub/Router layer exists so future peer Session Agents can be added without
changing the client wire protocol.  Ephemeral child agents created by
`AgentTool` remain private to their parent runtime and do not become router
targets.

## Process Responsibilities

| Process | Code | Owns | Does not own |
|---|---|---|---|
| Supervisor | `kernel.supervisor` | Child process lifecycle, restart budget, control socket, runtime files under `~/.deepcli/state/supervisor/`. | ACP session handling, LLM/tool execution. |
| Agent Hub | `kernel.agent_hub` | Router, Manager, runtime registration, routing snapshots, shared resource revision boundary. | FastAPI edge, user auth, agent loop. |
| Access Agent | `kernel.access_agent` + `kernel.app` | Loopback FastAPI edge, `/session`, readiness, connection auth, operator ACP methods, platform ingress/reply sinks. | Durable session truth, tool execution. |
| Primary Agent Runtime | `kernel.agent_runtime` | `AgentSessionRuntimeService`, `SessionManager`, `SessionStore`, Orchestrator, LLM/tools/memory/hooks/MCP path for `primary`. | User-facing socket, process supervision. |
| CLI / Probe | `src/cli`, `src/probe` | Thin ACP clients and TUI/probe rendering. | Kernel internals, SQLite/state files, process supervision. |

## Wire Boundary

The user-facing wire protocol is ACP/JSON-RPC over WebSocket.  The default
launcher exposes the Access Agent on loopback, usually `127.0.0.1:8200`.

```text
Transport        FastAPI WebSocket accept/recv/send/close
Protocol         JSON-RPC frames <-> typed ACP schemas
Access routing   Local handler or Hub router backend
Runtime service  ACP session contract <-> SessionManager calls
Session          durable sessions, prompt queue, permission futures
Orchestrator     provider/tool loop, history, compaction, plan mode
```

DeepCLI-specific methods use the `_mustang.agent/*` namespace.  Legacy
unprefixed extension aliases are not part of the active protocol.

## Primary Runtime Bootstrap

Inside the Primary Agent Runtime, `kernel.app:create_app` still builds a
`KernelModuleTable` and starts bootstrap services plus subsystems.  The
important split is:

- **Bootstrap services** are fatal on startup failure and are not
  `Subsystem` subclasses.
- **Regular subsystems** inherit `kernel.core.lifecycle.Subsystem` and degrade on
  startup failure unless explicitly required by a caller.
- **Transport** is bound to FastAPI and selected by flags; production uses the
  ACP stack.

Startup order in `kernel.app`:

```text
0. FlagManager
1. SecretManager
2. ConfigManager
3. PromptManager
4. Core subsystems
   - ConnectionAuthenticator
   - ToolAuthorizer
   - LLMProviderManager
   - LLMManager
5. Optional subsystems gated by KernelFlags
   - MCPManager
   - ToolManager
   - SkillManager
   - HookManager
   - MemoryManager
   - GitManager
6. Trailing subsystems
   - SessionManager
   - CommandManager
   - GatewayManager
   - ScheduleManager
```

Shutdown unloads regular subsystems in reverse registration order.  Bootstrap
services have no async teardown path: flags are runtime-frozen, config updates
write synchronously, secrets close through process shutdown.

## Module Table

`KernelModuleTable` is the only in-process registry for live Kernel services.
Bootstrap services live as typed fields; regular subsystems are registered by
class key after successful `Subsystem.load()`.

```python
KernelModuleTable(
    flags=FlagManager,
    config=ConfigManager,
    state_dir=Path("~/.deepcli/state"),
    secrets=SecretManager,
    prompts=PromptManager,
)
```

Handlers and routes should reach Kernel services through the module table, not
module-level singletons.

## Storage Boundaries

Default user state is under `~/.deepcli/`:

```text
~/.deepcli/
├── config/
│   ├── client.yaml             # CLI preferences and OOBE state
│   ├── flags.yaml              # Kernel boot-time feature flags
│   └── kernel.yaml             # Kernel business config
├── state/                      # launcher/auth/runtime state
├── agents/primary/             # Primary Agent runtime state
├── sessions/                   # legacy/global session DB path
├── memory/
└── secrets.db
```

Clients must not read or write Kernel SQLite databases or sidecar files.
Needed capabilities must be exposed through ACP or `_mustang.agent/*` methods.

## Subsystem Index

| Area | Active design doc |
|---|---|
| Bootstrap/config/auth | [flags](subsystems/flags.md), [secrets](subsystems/secrets.md), [config](subsystems/config.md), [connection auth](subsystems/connection_authenticator.md), [tool authz](subsystems/tool_authorizer.md) |
| Model/provider path | [LLM](subsystems/llm.md), [LLM provider](subsystems/llm_provider.md) |
| Agent loop | [session](subsystems/session.md), [orchestrator](subsystems/orchestrator.md), [compaction](subsystems/compaction.md), [tasks](subsystems/tasks.md) |
| Tools/extensibility | [tools](subsystems/tools.md), [skills](subsystems/skills.md), [MCP](subsystems/mcp.md), [hooks](subsystems/hooks.md), [commands](subsystems/commands.md) |
| Persistent context | [memory](subsystems/memory/design.md), [git](subsystems/git.md), [schedule](subsystems/schedule.md) |
| Protocol/edge | [transport](subsystems/transport.md), [protocol interface](interfaces/protocol.md), [gateways legacy note](subsystems/gateways.md) |

Large subsystem documents should describe only behavior that is true in the
current code.  Historical plans and superseded designs belong under
`docs/*/history/plans/`.
