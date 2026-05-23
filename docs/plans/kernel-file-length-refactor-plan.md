# Kernel File-Length Refactor Plan

## Purpose

This plan replaces the stale 2026-04-26 audit.  The old plan targeted the
pre-supervisor layout (`src/kernel/kernel/session`, `orchestrator`, etc.).
Current code now lives behind the supervised topology:

```text
Access Agent -> Agent Hub -> Mustang Agent (`primary`)
```

The file-length problem is no longer one monolithic `SessionManager`.  It is a
set of oversized boundary files across protocol routing, Access Router, Agent
Hub, Mustang runtime, and Mustang subsystems.

Raw need: keep implementation files readable enough to modify safely without
breaking the live router path.  The 300-line rule is a signal, not the goal.
The real goal is to restore clear ownership boundaries while preserving public
imports and ACP wire contracts.

## Current Scan

- Scan date: 2026-05-24
- Scan command:

```bash
find src/kernel/kernel -name '*.py' -type f -print0 \
  | xargs -0 wc -l \
  | awk '$1 > 300 && $2 != "total" {print $1 "\t" $2}' \
  | sort -nr
```

- Result: **62** Python files over 300 lines.
- Over-limit lines: **31,167**.

## Root Causes

1. **Protocol boundary files absorbed domains.**  `core/protocol/acp/routing.py`
   and `session_handler.py` now contain many `_mustang.agent/*` management
   domains instead of delegating to domain routers.
2. **Control-plane files carry both lifecycle and request handling.**  Access
   Router and Agent Hub manager files mix process/bootstrap concerns with
   repository, routing, health, and ACP method behavior.
3. **Mustang runtime entry files are still implementation-heavy.**  Several
   `__init__.py` files are package exports in name but manager implementations
   in practice.
4. **Recent refactors split old monoliths but left second-level files large.**
   Session and Orchestrator are no longer single huge modules, but their API,
   turn, event, loop, and scheduler files still need a second pass.
5. **Subsystems with real closure seams need grouped refactors.**  Tool auth,
   MCP, skills, memory, schedule, and web tools are connected to live subsystem
   callbacks.  Splitting by line count alone would create unverified seams.

## Non-Goals

- Do not change ACP method names, JSON-RPC shapes, or `_mustang.agent/*`
  namespaces.
- Do not bypass the Access Router -> Agent Hub -> Primary Runtime path for
  CLI-visible behavior.
- Do not move frontend/CLI logic into kernel internals.
- Do not refactor archived `archive/` code.
- Do not chase an arbitrary tiny-file style.  Files just below 300 lines are
  acceptable when their ownership is clear.

## Invariants

- `src/kernel/` remains the active implementation root.
- CLI and Probe stay thin ACP/WebSocket clients.
- `__init__.py` files should export public API only unless a package has a
  documented exception.
- Public imports must remain stable through compatibility re-exports during
  each batch.
- Every batch must identify closure seams before it claims completion.
- Any router-visible method must be verified on the supervised path, not only
  by in-process unit tests.

## Current Over-Limit Inventory

| Lines | File |
|---:|---|
| 2270 | `src/kernel/kernel/core/protocol/acp/routing.py` |
| 1357 | `src/kernel/kernel/core/protocol/acp/session_handler.py` |
| 1061 | `src/kernel/kernel/agents/mustang/sessions/api/handlers.py` |
| 1010 | `src/kernel/kernel/agent_hub/manager/manager.py` |
| 818 | `src/kernel/kernel/agents/mustang/llm/__init__.py` |
| 774 | `src/kernel/kernel/agents/mustang/tools/__init__.py` |
| 749 | `src/kernel/kernel/agents/mustang/tools/builtin/bash.py` |
| 745 | `src/kernel/kernel/agents/mustang/skills/__init__.py` |
| 665 | `src/kernel/kernel/agents/mustang/runtime/session_service.py` |
| 648 | `src/kernel/kernel/agents/mustang/gateways/base.py` |
| 642 | `src/kernel/kernel/agents/mustang/schedule/store.py` |
| 620 | `src/kernel/kernel/agents/mustang/runtime/__main__.py` |
| 590 | `src/kernel/kernel/access_router/app.py` |
| 574 | `src/kernel/kernel/agents/mustang/tool_authz/authorizer.py` |
| 564 | `src/kernel/kernel/agents/mustang/mcp/__init__.py` |
| 519 | `src/kernel/kernel/access_router/repository.py` |
| 488 | `src/kernel/kernel/agents/mustang/memory/tools.py` |
| 482 | `src/kernel/kernel/core/storage/resource_store.py` |
| 476 | `src/kernel/kernel/agents/mustang/mcp/client.py` |
| 472 | `src/kernel/kernel/agents/mustang/mcp/oauth.py` |
| 466 | `src/kernel/kernel/core/secrets/__init__.py` |
| 466 | `src/kernel/kernel/agents/mustang/sessions/turns/runner.py` |
| 462 | `src/kernel/kernel/agents/mustang/schedule/scheduler.py` |
| 447 | `src/kernel/kernel/access_router/router.py` |
| 446 | `src/kernel/kernel/supervisor/runtime.py` |
| 446 | `src/kernel/kernel/core/storage/secret_store.py` |
| 445 | `src/kernel/kernel/agents/mustang/orchestrator/orchestrator.py` |
| 442 | `src/kernel/kernel/agents/mustang/memory/store.py` |
| 431 | `src/kernel/kernel/agents/mustang/sessions/client_stream/event_mapper.py` |
| 430 | `src/kernel/kernel/agents/mustang/tools/builtin/file_read.py` |
| 419 | `src/kernel/kernel/agents/mustang/sessions/lifecycle/runtime.py` |
| 419 | `src/kernel/kernel/agents/mustang/orchestrator/tools_exec/scheduler.py` |
| 418 | `src/kernel/kernel/agents/mustang/memory/background.py` |
| 412 | `src/kernel/kernel/agents/mustang/memory/selector.py` |
| 404 | `src/kernel/kernel/agents/mustang/tools/web/management.py` |
| 384 | `src/kernel/kernel/core/protocol/acp/event_mapper.py` |
| 383 | `src/kernel/kernel/agents/mustang/git/__init__.py` |
| 376 | `src/kernel/kernel/agents/mustang/skills/manifest.py` |
| 375 | `src/kernel/kernel/core/config/manager.py` |
| 365 | `src/kernel/kernel/agents/mustang/orchestrator/loop/engine.py` |
| 362 | `src/kernel/kernel/agents/mustang/sessions/store.py` |
| 362 | `src/kernel/kernel/agents/mustang/mcp/config.py` |
| 358 | `src/kernel/kernel/agents/mustang/memory/__init__.py` |
| 353 | `src/kernel/kernel/agents/mustang/sessions/client_stream/replay.py` |
| 348 | `src/kernel/kernel/core/protocol/acp/schemas/session.py` |
| 342 | `src/kernel/kernel/agents/mustang/tools/repl/worker_main.py` |
| 335 | `src/kernel/kernel/agents/mustang/orchestrator/history/conversation.py` |
| 331 | `src/kernel/kernel/agents/mustang/tools/builtin/ask_user_question.py` |
| 328 | `src/kernel/kernel/agents/mustang/tools/tool.py` |
| 327 | `src/kernel/kernel/agents/mustang/tools/builtin/powershell.py` |
| 322 | `src/kernel/kernel/agents/mustang/orchestrator/prompt_builder.py` |
| 320 | `src/kernel/kernel/agents/mustang/schedule/__init__.py` |
| 320 | `src/kernel/kernel/agents/mustang/commands/__init__.py` |
| 318 | `src/kernel/kernel/agents/mustang/skills/types.py` |
| 317 | `src/kernel/kernel/agents/mustang/skills/declarations.py` |
| 315 | `src/kernel/kernel/core/storage/tables.py` |
| 314 | `src/kernel/kernel/agents/mustang/sessions/user_repl/service.py` |
| 309 | `src/kernel/kernel/agent_hub/contracts/schemas.py` |
| 308 | `src/kernel/kernel/agents/mustang/tasks/registry.py` |
| 308 | `src/kernel/kernel/agents/mustang/schedule/delivery.py` |
| 307 | `src/kernel/kernel/agents/mustang/skills/loader.py` |
| 303 | `src/kernel/kernel/agents/mustang/tool_authz/bash_classifier.py` |

## Workstreams

### A — Core ACP Protocol Routing

Files:

- `core/protocol/acp/routing.py`
- `core/protocol/acp/session_handler.py`
- `core/protocol/acp/event_mapper.py`
- `core/protocol/acp/schemas/session.py`

Target shape:

- Split routing by domain: session, runtime, resources, skills, tools, memory,
  schedule, MCP, gateways, and errors.
- Keep a small central router that owns JSON-RPC envelope handling only.
- Move large schema groups under `schemas/session/` if needed, with
  compatibility exports.

Verification:

- ACP codec/routing unit tests.
- Access Router local-path tests for Access-owned methods.
- Router-path probe for runtime-forwarded methods.
- `tests/kernel/agent_hub/test_agent_hub_transport_c.py` for forwarded
  runtime contract drift.

### B — Access Router, Agent Hub, Supervisor

Files:

- `access_router/app.py`
- `access_router/router.py`
- `access_router/repository.py`
- `agent_hub/manager/manager.py`
- `agent_hub/contracts/schemas.py`
- `supervisor/runtime.py`

Target shape:

- Keep FastAPI assembly separate from local ACP method dispatch.
- Split router registration, delivery, freshness, and management surfaces.
- Split AgentManager repository/identity/lifecycle/health/grants behavior.
- Keep shared runtime contracts in `agent_hub/contracts`, not ad hoc lists.

Verification:

- Access Router websocket tests.
- Agent Hub manager/transport tests.
- Real supervised route probe for a runtime method and an Access-owned
  management method.

### C — Mustang Runtime And Sessions

Files:

- `agents/mustang/runtime/session_service.py`
- `agents/mustang/runtime/__main__.py`
- `agents/mustang/sessions/api/handlers.py`
- `agents/mustang/sessions/turns/runner.py`
- `agents/mustang/sessions/client_stream/event_mapper.py`
- `agents/mustang/sessions/client_stream/replay.py`
- `agents/mustang/sessions/lifecycle/runtime.py`
- `agents/mustang/sessions/store.py`
- `agents/mustang/sessions/user_repl/service.py`

Target shape:

- Keep runtime bootstrap, CLI argv parsing, and subsystem assembly separate.
- Split session API methods by lifecycle, prompt, config/mode, replay, and
  management.
- Keep turn execution separate from event mapping and persistence.
- Preserve the completed session package layout and compatibility imports.

Verification:

- Session unit tests and store tests.
- E2E/probe for `session/new`, `session/load`, `session/prompt`,
  `session/cancel`, replay, permission roundtrip, and user REPL.
- Closure-seam inventory for OrchestratorDeps callbacks created by the session
  factory.

### D — Mustang Orchestrator Second Pass

Files:

- `agents/mustang/orchestrator/orchestrator.py`
- `agents/mustang/orchestrator/loop/engine.py`
- `agents/mustang/orchestrator/tools_exec/scheduler.py`
- `agents/mustang/orchestrator/history/conversation.py`
- `agents/mustang/orchestrator/prompt_builder.py`

Target shape:

- Keep `StandardOrchestrator` as facade and move residual runtime behavior into
  loop, prompt, history, and tool scheduling helpers.
- Keep tool execution scheduling separate from single-tool pipeline behavior.
- Preserve old compatibility imports documented in the orchestrator history
  plan.

Verification:

- Orchestrator unit tests.
- Tool execution/hook tests.
- Compaction and history tests.
- Live query-loop E2E with a fake local provider where possible.

### E — Core Storage, Config, And Secrets

Files:

- `core/storage/resource_store.py`
- `core/storage/secret_store.py`
- `core/storage/tables.py`
- `core/config/manager.py`
- `core/secrets/__init__.py`

Target shape:

- Split table definitions, migrations, query helpers, import/export, and
  optimistic revision behavior.
- Move `SecretManager` implementation out of `__init__.py`.
- Keep ResourceStore as durable global truth; do not reintroduce YAML writes.

Verification:

- Config/flags/secrets/storage unit tests.
- ResourceStore aggregate probe.
- Plaintext secret export guard checks.

### F — Mustang Extensibility Subsystems

Files:

- `agents/mustang/tools/__init__.py`
- `agents/mustang/tools/tool.py`
- `agents/mustang/tools/builtin/bash.py`
- `agents/mustang/tools/builtin/file_read.py`
- `agents/mustang/tools/builtin/powershell.py`
- `agents/mustang/tools/builtin/ask_user_question.py`
- `agents/mustang/tools/repl/worker_main.py`
- `agents/mustang/tools/web/management.py`
- `agents/mustang/tool_authz/authorizer.py`
- `agents/mustang/tool_authz/bash_classifier.py`
- `agents/mustang/mcp/__init__.py`
- `agents/mustang/mcp/client.py`
- `agents/mustang/mcp/oauth.py`
- `agents/mustang/mcp/config.py`
- `agents/mustang/skills/__init__.py`
- `agents/mustang/skills/manifest.py`
- `agents/mustang/skills/types.py`
- `agents/mustang/skills/declarations.py`
- `agents/mustang/skills/loader.py`

Target shape:

- Move manager implementations out of package roots.
- Split tool schemas, execution, risk classification, result mapping, and web
  management domains.
- Split MCP manager, client transports, OAuth lifecycle, and declaration config.
- Split skills manager, discovery, declaration store, manifest parsing, and
  activation.

Verification:

- ToolManager, ToolAuthorizer, MCP, and SkillManager suites.
- Real ToolExecutor bridge probe for subsystem-backed tools.
- MCP connect/health/reconnect/OAuth probes where config is available.
- Skill discovery/load/activation probe through the runtime path.

### G — Mustang Memory, Schedule, LLM, Commands, And Remaining Subsystems

Files:

- `agents/mustang/memory/__init__.py`
- `agents/mustang/memory/tools.py`
- `agents/mustang/memory/store.py`
- `agents/mustang/memory/background.py`
- `agents/mustang/memory/selector.py`
- `agents/mustang/schedule/__init__.py`
- `agents/mustang/schedule/store.py`
- `agents/mustang/schedule/scheduler.py`
- `agents/mustang/schedule/delivery.py`
- `agents/mustang/llm/__init__.py`
- `agents/mustang/commands/__init__.py`
- `agents/mustang/gateways/base.py`
- `agents/mustang/git/__init__.py`
- `agents/mustang/tasks/registry.py`

Target shape:

- Move managers out of package roots.
- Split memory tools/background/selector/store by responsibility.
- Split schedule CRUD, claim, timer, recovery, and delivery retry behavior.
- Split LLM model registry, alias resolution, routing, and manager lifecycle.
- Split commands catalog from projection/rendering helpers.
- Split gateway base protocol, lifecycle, and chunking helpers.

Verification:

- Memory, schedule, LLM, commands, gateways, git, and tasks targeted tests.
- Schedule timer/delivery probe.
- Memory tool/store/selection probe.
- LLM model resolution/routing probe.

## Execution Rules

1. Re-scan before each workstream.  The file list may change under active
   development.
2. Work by subsystem boundary, not by largest file first.
3. For each file moved, keep compatibility exports until all in-repo imports are
   migrated.
4. Do not mix behavior changes with movement unless a test exposes a real bug.
5. Run `git diff --check` after each workstream.
6. Update this plan if the chosen module split diverges from it.
7. Update `docs/plans/progress.md` only when an implementation workstream is
   completed with verification evidence.

## Definition Of Done

A workstream is complete only when:

- The touched files in that workstream are below 300 lines or have a documented
  exception.
- Package `__init__.py` files touched by the workstream are export-only.
- Public imports and ACP wire behavior are unchanged.
- Unit tests for touched modules pass.
- Relevant E2E/probe checks exercise the real subsystem path.
- Closure-seam inventory and probe output are included in the report.
- Docs that describe the touched subsystem match the final layout.

The whole plan is complete only when:

- `src/kernel/kernel/**/*.py` has **0** files over 300 lines, except any
  explicitly documented generated/schema exception.
- All compatibility exports are either intentionally retained or removed after a
  repo-wide import audit.
- The final scan command returns no unplanned over-limit files.
