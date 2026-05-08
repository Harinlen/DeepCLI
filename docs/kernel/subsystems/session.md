# SessionManager

`SessionManager` is the Primary Agent Runtime's durable conversation
subsystem.  It implements the ACP session contract after the protocol layer
has already decoded JSON-RPC frames into typed request objects.

It does not own WebSocket transport, JSON-RPC ids, provider streaming, tool
logic, or process supervision.

## Current Boundaries

```text
Access Agent
  -> Agent Hub.Router
  -> Primary Agent Runtime
  -> AgentSessionRuntimeService
  -> SessionManager
  -> Orchestrator
```

`SessionManager` owns:

- session lifecycle: new, load, resume, close, rename, archive, delete
- prompt turns: FIFO queue, one active turn per session, cancellation
- durable event storage through `SessionStore`
- client stream replay and broadcasting to attached clients
- permission futures for `session/request_permission`
- mode/config updates that must be reflected into active orchestrators
- user REPL execution methods exposed through `_mustang.agent/*`

`Orchestrator` owns:

- prompt construction and conversation history inside the agent loop
- provider streaming and tool-call iteration
- tool authorization call sites and hook fire sites
- compaction and plan-mode behavior

The seam between the two is intentionally narrow: Session calls
`orchestrator.query(...)`, persists and maps emitted events, and supplies the
permission callback used to round-trip client approvals.

## Persistence

Current storage is SQLite, not the old JSONL/index design.

`SessionStore` owns:

```text
<agent-state>/sessions/
├── sessions.db
├── sessions.db-wal
├── sessions.db-shm
└── <session-id>/
    └── tool-results/
        └── <hash>.txt
```

For the default Primary Agent, the Supervisor passes:

```text
~/.deepcli/agents/primary/sessions/sessions.db
```

The DB has two logical tables:

- `sessions` — one row per conversation, including cwd/title/archive state,
  mode/config metadata, and cumulative token counters.
- `session_events` — append-only event rows ordered by timestamp.

Large tool outputs spill to per-session `tool-results/` files and are
referenced from events.  This preserves SQLite as the metadata/event store
without putting large blobs into the database.

Migrations are startup-owned by `SessionStore.open()` through
`kernel.session.migrations.apply()`.  Current unreleased baseline is schema
version 1.

## Runtime State

An active `Session` dataclass is memory-only and contains:

- the durable `session_id`, cwd, title, mode, config options, and git branch
- the long-lived Orchestrator instance for this session
- attached client senders keyed by connection id
- one optional in-flight `TurnState`
- a FIFO queue of `QueuedTurn`
- pending mode changes and hook/system reminders
- per-session user REPL execution tasks

Idle sessions can be evicted from memory; the SQLite event log remains the
source for future `session/load` or `session/resume`.

## Prompt Turn Semantics

One session runs at most one prompt turn at a time.

```text
session/prompt
  -> duplicate clientTurnId check
  -> enqueue or start immediately
  -> append user event
  -> run Orchestrator.query(...)
  -> persist emitted events
  -> broadcast ACP session/update events
  -> write TurnCompletedEvent with token deltas
  -> start next queued turn
```

`clientTurnId` is the reconnect/idempotency key.  SessionManager detects
active, queued, completed, and incomplete duplicate turns so reconnecting
clients do not accidentally execute the same prompt twice.

Provider/orchestrator errors are surfaced as visible assistant error updates
and `stopReason="error"`; they are not silently converted to normal
`end_turn`.

## Client Stream And Replay

SessionManager stores canonical events, then maps them into ACP
`session/update` notifications for attached clients.

Replay paths exist for:

- `session/load` — full transcript replay plus usage snapshot
- `session/resume` — no transcript replay, but still emits the latest
  `usage_update` snapshot so clients can rehydrate session status
- completed duplicate `clientTurnId`
- reconnect after a dropped WebSocket

Replay prefers persisted UI/client-stream events when available and falls
back to conversation events only when necessary.  This prevents duplicate
assistant text when both conversation-level and UI-level events exist.

`usage_update` is also the canonical frontend context snapshot.  Its
`inputTokens` / `outputTokens` fields describe the latest provider turn, while
`used` / `size` describe the current context-window occupancy for the active
session.  Clients must render context from this snapshot (or from
`_mustang.agent/session/get_usage`) instead of deriving it from cumulative
session token totals or local transcript caches.

## Permission Round Trip

The Orchestrator receives a permission callback from SessionManager.  When a
tool authorization decision requires user input:

```text
ToolAuthorizer ask
  -> Orchestrator permission callback
  -> SessionManager creates Future
  -> Access/CLI receives session/request_permission
  -> client selects allow/reject
  -> SessionManager resolves Future
  -> tool execution continues or is denied
```

In router mode, permission requests are tunneled Runtime -> Hub -> Access ->
CLI/Probe or platform reply sink.

## Public Surface

Primary session methods are implemented in
`kernel.session.api.handlers.SessionHandlerMixin` and routed through protocol
handlers.  Active surfaces include:

- ACP: `session/new`, `session/load`, `session/resume`, `session/list`,
  `session/prompt`, `session/cancel`, `session/close`
- ACP config/mode: `session/set_mode`, `session/set_config_option`
- DeepCLI extensions: usage, rename/archive/delete, skill activation,
  shell/Python execution and cancellation

Gateway delivery remains as legacy/transition support.  New platform ingress
should route through Access Agent Platform Adapter semantics and Agent Hub
Router, not directly into SessionManager.
