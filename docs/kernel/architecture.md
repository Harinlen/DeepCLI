# Kernel Architecture

> **Quick header**
> - **Role**: top-level runtime map for the supervised Kernel.
> - **Current code**: `kernel.supervisor`, `kernel.kernel_bus`, `kernel.access_router`, `kernel.agent_hub`, `kernel.agents.mustang.runtime`, `kernel.agents.mustang.*`, `kernel.core.*`.
> - **Owns**: process topology, request path, lifecycle order, storage boundaries.
> - **Does not own**: subsystem internals, ACP schema definitions, or historical plan details.

This document is the current topology map.  It should stay short:
subsystem internals belong in [`subsystems/`](subsystems/), protocol details
belong in [`interfaces/protocol.md`](interfaces/protocol.md), and shipped
milestones belong in [`history.md`](history.md).

## Runtime Topology

DeepCLI runs as a supervised local Agent Control Plane.  The CLI does not own
the agent loop and does not import Kernel Python modules.

The current implementation is a migration slice: `kernel.kernel_bus` provides
message/topology primitives, while live routing and topology projection are
implemented inside `kernel.access_router.router`.  `KernelBus` is therefore a
logical bus in the current code, not yet a separate Supervisor child process.
The same is true for `GlobalResourceHost`: global resources are owned by the
Access Router startup slice today, with a future extraction point preserved by
the topology owner name.

```text
deepcli launcher
  |
  | starts / monitors one user-local runtime
  v
Supervisor
  |-- AgentRuntimeHost process      current code: kernel.agent_hub
  |     |-- durable AgentManager     ResourceStore-backed definitions/runtime state
  |     |-- primary runtime owner    starts agent:primary
  |     `-- session-agent owner      starts durable autostart/running agent:<id>
  |
  `-- AccessAgent process           current code: kernel.access_router
        |-- SessionEdge             WS /session for CLI / Probe / Home Screen
        |-- RuntimeEdge             WS /runtime registrations from agent runtimes
        |-- KernelBus slice         route table, bus topology, route.status
        |-- GlobalResourceHost slice registers global resource projections
        |-- WebBridgeHttpEdge       /web-bridge/status,pair,reset,install,fetch
        `-- WebBridgeExtensionEdge  Chrome extension loopback WS ingress

Agent Runtime process(es)             current code: kernel.agents.mustang.runtime
  |-- agent:primary                   started by AgentRuntimeHost on every boot
  `-- agent:<id>                      restored when autostart or desired=running

Current topology projection:
  agent:<id>                          owner=AgentRuntimeHost
  resource:web_bridge                 owner=GlobalResourceHost
  resource:web_search                 owner=GlobalResourceHost
```

Request path:

```text
CLI / Probe / future Home Screen / Platform Adapter
  -> AccessAgent.SessionEdge
  -> KernelBus slice in AccessRouter
  -> agent:<id>
  -> SessionManager
  -> Orchestrator
  -> LLM / tools / memory / hooks / MCP
```

The product path always boots the durable default agent `agent:primary`.
Durable session agents created through `/agent add` are stored in the global
ResourceStore.  They are restarted by `kernel.agent_hub` on kernel boot when
their runtime is declared `autostart=True` or their last desired runtime state
was `running`.  The route identity is a service id (`agent:<id>`), not a bare
agent id.  Ephemeral child agents created by `AgentTool` remain private to
their parent runtime and do not become bus targets.

Resource request path:

```text
Agent WebFetch(browser)
  -> AgentWebBridgeClient
  -> AccessAgent.WebBridgeHttpEdge
  -> resource:web_bridge projection
  -> WebBridgeManager
  -> AccessAgent.WebBridgeExtensionEdge / Chrome extension WS
  -> Chrome Extension
```

`/web-bridge/*` HTTP routes terminate at `AccessAgent.WebBridgeHttpEdge` and
target the AccessAgent-owned `resource:web_bridge` implementation.  They must
not route through `agent:primary`.

WebBridge pairing state is durable.  The pairing secret is stored in the
global `SecretStore` under `web_bridge.extension.secret` when a `resource_home`
is present.  Rebooting the kernel must not require re-pairing the browser
extension.  The WebBridge extension WebSocket prefers the stable neighbor port
`<access-port> + 1` (for the default Access Router this is `8201`) and falls
back to a random loopback port only if the stable port is occupied.

Shutdown order matters.  `Supervisor.stop()` stops `kernel.agent_hub` before
`kernel.access_router`, so Agent runtimes disconnect from `/runtime` before
the Access Router shuts down.  Access Router WebSocket handlers still clean up
their own reader/dispatch tasks in `finally` blocks so Ctrl-C and server
cancellation paths do not leave background tasks behind.

### Router-Path Invariant

Most interactive CLI traffic uses the supervised router path:

```text
AccessAgent -> KernelBus slice -> agent:primary
```

That means adding a new `_mustang.agent/*` ACP method is not complete when the
Access-side handler works locally.  If the method is handled by the Mustang
Agent runtime, the change must also be visible as an addressed ACP/ACPX bus
route to `agent:<id>`.

The contract source of truth is:

```text
kernel.agent_hub.contracts.AgentRuntimeContract
kernel.agent_hub.contracts.AGENT_RUNTIME_FORWARDED_CONTRACTS
kernel.agent_hub.contracts.AGENT_RUNTIME_STREAMING_CONTRACTS
```

During migration, legacy Agent Hub frame contracts remain compatibility
plumbing for existing runtime tests.  New cross-host traffic should be modeled
as a `kernel.kernel_bus.BusMessage`: `source`, `target`, raw ACP JSON-RPC
payload, and delivery metadata.  Do not add new business methods to the legacy
Hub frame path.

## Process Responsibilities

| Process | Code | Owns | Does not own |
|---|---|---|---|
| Supervisor | `kernel.supervisor` | Coarse host lifecycle, restart budget, control socket, runtime files under `~/.deepcli/state/supervisor/`. Starts/stops `kernel.agent_hub` and `kernel.access_router` only. | Individual Agent runtime lifecycle, individual resource lifecycle, ACP session handling, LLM/tool execution. |
| KernelBus slice | `kernel.kernel_bus`, `kernel.access_router.router` | Addressed ACP/ACPX routing metadata, service registry projection, topology snapshot, route status. | Business RPC schema, Agent lifecycle, resource business state. |
| AccessAgent | `kernel.access_router.app` | Loopback FastAPI edge, `/session`, `/runtime`, readiness, local HTTP routes, WebBridge HTTP/extension edge, current GlobalResourceHost slice. | Durable session truth, Agent lifecycle. |
| AgentRuntimeHost | `kernel.agent_hub`, `kernel.agent_hub.manager` | Durable Agent definitions, Agent runtime lifecycle, startup restoration for `autostart`/desired-running agents, `agent:<id>` registration/projection. | KernelBus ownership, global resources. |
| GlobalResourceHost slice | `kernel.access_router.app` startup slice | Global resources including `resource:web_bridge` and `resource:web_search`, WebBridge pairing secret persistence, shared resource metadata. | Agent lifecycle, user-facing CLI sessions. |
| Mustang Agent (`agent:<id>`) | `kernel.agents.mustang.runtime` | `AgentSessionRuntimeService`, `SessionManager`, `SessionStore`, Orchestrator, LLM/tools/memory/hooks/MCP path for one durable Agent instance. | User-facing socket, process supervision, Chrome extension WebSocket ownership. |
| CLI / Probe | `src/cli`, `src/probe` | Thin ACP clients and TUI/probe rendering. | Kernel internals, SQLite/state files, process supervision. |

## Wire Boundary

The user-facing wire protocol is ACP/JSON-RPC over WebSocket.  The default
launcher exposes the Access Agent on loopback, usually `127.0.0.1:8200`.

```text
Transport        FastAPI WebSocket accept/recv/send/close
Protocol         JSON-RPC frames <-> typed ACP schemas
Access routing   Local handler or KernelBus target route
Runtime service  ACP session contract <-> SessionManager calls
Session          durable sessions, prompt queue, permission futures
Orchestrator     provider/tool loop, history, compaction, plan mode
```

DeepCLI-specific methods use `_mustang.agent/*` for Agent methods and
`_mustang.resource/*` for Resource methods.  KernelBus discovery methods live
under `_mustang.bus/*`.

Current bus/topology methods:

```text
GET /bus/topology
_mustang.bus/topology.snapshot
_mustang.bus/topology.subscribe
_mustang.bus/route.status
```

`topology.subscribe` currently returns the same point-in-time snapshot as
`topology.snapshot`; it is a compatibility shape for the future streaming
publisher.  `route.status` returns the registered/unavailable/stale projection
for one service id.

Current WebBridge methods:

```text
GET  /web-bridge/status.json
POST /web-bridge/pair
POST /web-bridge/reset
POST /web-bridge/fetch
GET  /web-bridge/install
GET  /web-bridge/deepcli-web-bridge.zip
```

Agent runtimes call these through `AccessAgentWebBridgeClient`.  The browser
backend is considered available only when `resource:web_bridge` reports
`connected=true`.

## Mustang Runtime Bootstrap

Inside the supervised Mustang Agent runtime,
`kernel.agents.mustang.runtime.session_service.AgentSessionRuntimeService`
builds a `KernelModuleTable` and starts bootstrap services plus subsystems.
`kernel.access_router.app:create_app` owns the supervised AccessAgent FastAPI
edge.  `kernel.agents.access.app:create_app` remains a compatibility
in-process path for unsupervised/dev mode; the product path routes through the
AccessRouter KernelBus-compatible route projection to the Mustang runtime.

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

Each runtime receives an `AgentContext` from its launch arguments.  The
orchestrator prompt includes the current agent id, name, workspace, state dir,
bus identity (`agent:<id>`), resource scopes, and non-secret identity fields.
This is how a durable session agent such as `research` knows it is not the
primary agent after restart.

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
