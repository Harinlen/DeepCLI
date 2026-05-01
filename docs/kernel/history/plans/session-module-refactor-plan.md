# Session Module Refactor Plan

## Purpose

本计划细化 `src/kernel/kernel/session/` 的文件长度重构方案。目标是把当前
`session/__init__.py` 中交织的会话生命周期、prompt queue、permission
roundtrip、broadcast、replay、orchestrator event mapping、持久化写入等职责拆开，
同时保持外部 public API 稳定。

当前行为必须保持不变：

- `from kernel.session import SessionManager` 继续可用。
- `SessionManager` 继续实现 `SessionHandler` 的 7 个 ACP 方法。
- 当前存储实现是 SQLite event table + sidecar spillover；本重构不把存储改回 JSONL。
- 协议层仍然只通过 `SessionHandler` 调用 Session subsystem。

## Current Problem

当前 over-limit 文件：

| Lines | File |
|---:|---|
| 2095 | `src/kernel/kernel/session/__init__.py` |
| 321 | `src/kernel/kernel/session/events.py` |
| 306 | `src/kernel/kernel/session/store.py` |

主要问题：

- `__init__.py` 同时承载 public export、runtime state、SessionManager、turn runner、
  event mapper、replay、permission roundtrip、orchestrator factory。
- `events.py` 同时包含事件 schema 和 parse/serialize。
- `store.py` 同时包含 repository API、SQLite row mapping、spillover sidecar 文件处理。
- closure seam 集中在 `_make_orchestrator` 中，迁移时容易漏 probe。

## Target Layout

```text
src/kernel/kernel/session/
  __init__.py                  # public API exports only
  manager.py                   # SessionManager facade only
  events.py                    # compatibility exports + parse_event / serialize_event
  store.py                     # SessionStore public repository + SQLite queries
  api/
    handlers.py                # SessionHandler ACP methods
    gateway.py                 # gateway-only create/run-turn + cross-session delivery
  lifecycle/
    runtime.py                 # startup/shutdown, disconnect, get/evict/delete
    load.py                    # persisted event log -> active in-memory Session
  turns/
    runner.py                  # prompt queue + one turn execution
    permission.py              # session/request_permission roundtrip
  client_stream/
    event_mapper.py            # OrchestratorEvent -> persisted event + ACP update
    broadcast.py               # multi-connection session/update broadcast
    replay.py                  # stored SessionEvent -> session/update replay
  orchestration/
    factory.py                 # OrchestratorDeps assembly and cross-subsystem closures
  persistence/
    event_schema.py            # Pydantic event classes
    event_writer.py            # event construction, append, pending mode changes, spillover
    store_spillover.py         # tool-results sidecar read/write
  runtime/
    flags.py                   # SessionFlags
    state.py                   # Session / TurnState / QueuedTurn dataclasses
    helpers.py                 # cursor, git branch, stop-reason, summarise helpers
  _shared/
    imports.py                 # private compatibility import surface for internal mixins
```

Implementation note: the final layout deliberately keeps `kernel.session.events` and
`kernel.session.store` as modules rather than converting them to packages. This preserves
the existing import paths with the least risk while still moving schema and spillover
responsibilities out of the compatibility modules. Internal code is grouped by functional
path: API entrypoints, lifecycle, turns, client streaming, orchestration, persistence,
and runtime state.

## Module Responsibilities

### `manager.py`

Owns the public `SessionManager` class and keeps the external shape stable.

Keep these methods on `SessionManager`:

- `startup`
- `shutdown`
- `create_for_gateway`
- `run_turn_for_gateway`
- `on_disconnect`
- `new`
- `load_session`
- `list`
- `prompt`
- `set_mode`
- `set_config_option`
- `cancel`
- `deliver_message`
- `delete_session`

The methods may delegate to helper modules, but callers should not need to know.

### `state.py`

Move pure runtime state here:

- `Session`
- `TurnState`
- `QueuedTurn`

This file should have no dependency on ACP routing or storage internals. It can depend on
protocol contracts used by queued prompt state.

### `handlers.py` and `turn_runner.py`

Own prompt serialization behavior:

- append prompt to FIFO queue
- enforce `SessionFlags.max_queue_length`
- schedule the next queued turn
- resolve queued response futures
- cancel queued prompts

`handlers.py` owns the ACP entrypoints; `turn_runner.py` owns the execution loop and queued
turn dispatch. They intentionally stay separate from event persistence and ACP replay.

### `turn_runner.py`

Own one prompt turn:

- write user message and `TurnStartedEvent`
- call `session.orchestrator.query(...)`
- collect text/thought deltas for final persisted message events
- map `CancelledError` to `PromptResult(stop_reason="cancelled")`
- write `TurnCompletedEvent`
- hand off event processing to `event_mapper.py`

This is one of the highest-risk files because response timing and cancellation semantics
must not change.

### `event_mapper.py`

Own translation from orchestrator events to:

- persisted session events
- ACP `session/update` notifications
- accumulated text/thought buffers

Move current `_handle_orchestrator_event` here. Keep spillover decision delegated to
`event_writer.py` or a small spillover helper so mapping logic does not own file IO.

### `permission_roundtrip.py`

Move current `_on_permission` here:

- persist `PermissionRequestEvent`
- choose an active sender
- send `session/request_permission`
- map selected/cancelled outcome to `PermissionResponse`
- persist `PermissionResponseEvent`

This module is a closure seam between Orchestrator and ACP client IO, so it needs a real
roundtrip probe.

### `broadcast.py`

Move current `_broadcast` here:

- build `SessionUpdateNotification`
- notify all connected senders
- remove dead connections

Keep failure handling local and deterministic.

### `replay.py`

Move current `_replay_event` here:

- convert stored `SessionEvent` records back to ACP `session/update`
- restore spilled tool result content before replay
- ignore non-replayable lifecycle and permission events

This module depends on ACP update schemas and store spillover read access.

### `event_writer.py`

Own event creation and append:

- generate `event_id`
- set `parent_id`, timestamp, cwd, git branch, kernel version
- call `SessionStore.append_event`
- update `session.last_event_id` and `session.updated_at`
- drain pending mode changes
- maybe spill large tool output

This keeps persistence write semantics out of event mapping and turn execution.

### `lifecycle.py`

Own session lifecycle helpers:

- create in-memory `Session`
- load from persisted events
- reconstruct persistent state and orchestrator history
- get-or-raise / get-or-load
- maybe evict idle sessions
- delete session
- close orchestrator and task registry on shutdown/eviction

Worktree restore/startup can either stay delegated to `git_context.py` or be called from here.

### `orchestrator_factory.py`

Move `_make_orchestrator` and `_make_summarise_closure` here.

This module wires many cross-subsystem closures:

- `should_avoid_prompts_provider`
- `set_mode`
- `queue_reminders`
- `drain_reminders`
- `deliver_cross_session`
- `summarise`
- `mcp_instructions`

Treat this as the main Phase 4.5 closure-seam inventory area for the batch.

### `event_schema.py` and `events.py`

Split current `session/events.py` while keeping `kernel.session.events` import-compatible:

- `event_schema.py`: event Pydantic models and `SessionEvent` union
- `events.py`: compatibility exports plus `parse_event`, `serialize_event`

Keep `from kernel.session.events import SessionCreatedEvent` working.

### `store.py` and `store_spillover.py`

Split current `session/store.py` while keeping `kernel.session.store` import-compatible:

- `store.py`: public `SessionStore`, DB open/close, SQLite query methods
- `store_spillover.py`: sidecar tool result read/write

Keep `from kernel.session.store import SessionStore` working.

## Migration Order

1. Extract pure files: `flags.py`, `state.py`, `helpers.py`.
2. Split `events.py` into `event_schema.py` plus compatibility `events.py`.
3. Split store spillover helpers into `store_spillover.py`, preserving `SessionStore` behavior.
4. Extract `broadcast.py`, `permission_roundtrip.py`, and `replay.py`.
5. Extract `event_writer.py` and `event_mapper.py`.
6. Extract `prompt_queue.py` and `turn_runner.py`.
7. Extract `lifecycle.py`.
8. Extract `orchestrator_factory.py`.
9. Collapse `session/__init__.py` to package exports only.
10. Re-run line-count scan and remove any remaining over-300 files in the module.

## Compatibility Strategy

- Do not change ACP method names, request/response models, or notification shapes.
- Keep `SessionManager` as the only subsystem registered by `kernel.app`.
- Keep `SessionStore` public behavior identical.
- Avoid moving protocol schemas into session; session may import protocol contracts, but protocol
  must not import session implementation details.
- Avoid changing orchestrator APIs during this batch.

## Test Plan

Unit and integration tests to run or add:

- `tests/kernel/session/test_session_manager.py`
- `tests/kernel/session/test_store.py`
- `tests/kernel/session/test_message_serde.py`
- `tests/kernel/protocol/test_session_handler.py`
- targeted tests for new modules mirroring source paths

E2E/probe coverage required:

- `session/new` creates DB record and initial event.
- `session/list` returns paginated records with cursor behavior.
- `session/load` replays persisted history as `session/update`.
- `session/prompt` streams updates and returns `PromptResult`.
- FIFO prompt queue preserves serial execution.
- `session/cancel` cancels in-flight and queued turns.
- permission roundtrip sends real `session/request_permission` and maps response.
- multi-connection broadcast sends one update to every connected sender.
- spilled tool result is restored during replay.
- gateway path can create a session and run a turn without a WebSocket sender.

## Closure-Seam Inventory

The batch must explicitly verify any changed closure seam:

| Seam | Owner | Probe expectation |
|---|---|---|
| `on_permission` callback into Orchestrator | `permission_roundtrip.py` | real ACP permission request receives client response |
| `should_avoid_prompts_provider` | `orchestrator_factory.py` | true when no sender, false when connected |
| `set_mode` closure | `orchestrator_factory.py` | tool-driven mode change updates session state and broadcasts |
| `queue_reminders` / `drain_reminders` | `orchestrator_factory.py` | hook/tool reminder appears in next turn prompt path |
| `deliver_cross_session` | `orchestrator_factory.py` | SendMessage reaches active target session pending reminders |
| `summarise` | `orchestrator_factory.py` | real LLMManager compact/default route returns text |
| `mcp_instructions` | `orchestrator_factory.py` | connected MCP instructions flow into orchestrator deps |

## Acceptance Criteria

- `src/kernel/kernel/session/**/*.py` has no file over 300 lines.
- `session/__init__.py` contains only public exports.
- `SessionManager` public import path and behavior remain stable.
- Store behavior remains SQLite-backed.
- Existing kernel/session and relevant protocol tests pass.
- Relevant e2e/probe output is included in the implementation report.
- Any doc drift found during the migration is corrected in the same batch.
