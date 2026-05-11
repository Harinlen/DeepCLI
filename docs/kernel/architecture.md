# Kernel Architecture

> **Quick header**
> - **Role**: top-level runtime map for the supervised Kernel.
> - **Current code**: `kernel.supervisor`, `kernel.agent_hub`, `kernel.agents.access`, `kernel.agents.mustang.runtime`, `kernel.agents.mustang.*`, `kernel.core.*`.
> - **Owns**: process topology, request path, lifecycle order, storage boundaries.
> - **Does not own**: subsystem internals, ACP schema definitions, or historical plan details.

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
  |     `-- ResourceRevisionMonitor shared resource revision boundary
  |
  |-- Access Agent                 FastAPI edge, health/readiness, WS /session
  |
  `-- Mustang Agent (`primary`)    real SessionManager + Orchestrator path
```

Request path:

```text
CLI / Probe / future Home Screen / Platform Adapter
  -> Access Agent
  -> Agent Hub.Router
  -> Mustang Agent (`primary`)
  -> SessionManager
  -> Orchestrator
  -> LLM / tools / memory / hooks / MCP
```

The current product path is a single default durable agent: `primary`.
The Hub/Router layer exists so future peer Mustang Agent instances can be added without
changing the client wire protocol.  Ephemeral child agents created by
`AgentTool` remain private to their parent runtime and do not become router
targets.

### Router-Path Invariant

Most interactive CLI traffic uses the supervised router path:

```text
Access Agent -> Agent Hub -> Mustang Agent (`primary`)
```

That means adding a new `_mustang.agent/*` ACP method is not complete when the
Access-side handler works locally.  If the method is handled by the Mustang
Agent runtime, the change must also declare and verify the corresponding
`agent.*` Hub runtime contract.

The contract source of truth is:

```text
kernel.agent_hub.contracts.AgentRuntimeContract
kernel.agent_hub.contracts.AGENT_RUNTIME_FORWARDED_CONTRACTS
kernel.agent_hub.contracts.AGENT_RUNTIME_STREAMING_CONTRACTS
```

Do not add one-off string allowlists in `agent_hub/server.py`.  New runtime
contracts must be added to the shared contract enum, implemented by the
runtime dispatcher, and covered by the Agent Hub transport tests.  The
regression test
`tests/kernel/agent_hub/test_agent_hub_transport_c.py` scans Access and Runtime
`agent.*` contract literals and fails if Hub forwarding can drift silently.

## Process Responsibilities

| Process | Code | Owns | Does not own |
|---|---|---|---|
| Supervisor | `kernel.supervisor` | Child process lifecycle, restart budget, control socket, runtime files under `~/.deepcli/state/supervisor/`. | ACP session handling, LLM/tool execution. |
| Agent Hub | `kernel.agent_hub` | Router, Manager, runtime registration, routing snapshots, shared resource revision boundary. | FastAPI edge, user auth, agent loop. |
| Access Agent | `kernel.agents.access` + `kernel.agents.access.app` | Loopback FastAPI edge, `/session`, readiness, connection auth, operator ACP methods, platform ingress/reply sinks. | Durable session truth, tool execution. |
| Mustang Agent (`primary`) | `kernel.agents.mustang.runtime` | `AgentSessionRuntimeService`, `SessionManager`, `SessionStore`, Orchestrator, LLM/tools/memory/hooks/MCP path for the default `primary` instance. | User-facing socket, process supervision. |
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

## Mustang Runtime Bootstrap

Inside the supervised Mustang Agent runtime,
`kernel.agents.mustang.runtime.session_service.AgentSessionRuntimeService`
builds a `KernelModuleTable` and starts bootstrap services plus subsystems.
`kernel.agents.access.app:create_app` owns the Access Agent FastAPI edge and
keeps a compatibility in-process runtime path for unsupervised/dev mode; the
supervised product path routes through Agent Hub to the Mustang runtime.

The important split is:

- **Bootstrap services** are fatal on startup failure and are not
  `Subsystem` subclasses.
- **Regular subsystems** inherit `kernel.core.lifecycle.Subsystem` and degrade on
  startup failure unless explicitly required by a caller.
- **Access transport** is bound to FastAPI and selected by flags; production
  uses the ACP stack at the Access Agent edge.

Mustang runtime startup order in `AgentSessionRuntimeService`:

```text
0. FlagManager
1. SecretManager
2. ConfigManager
3. PromptManager
4. Core subsystems
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
6. Session subsystem
   - SessionManager
7. Trailing subsystems
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
├── agents/primary/             # default Mustang Agent instance state
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
current code.  Historical plans and superseded designs belong beside the
owning area under `*/history/`, with index links from `history/plans/`.
