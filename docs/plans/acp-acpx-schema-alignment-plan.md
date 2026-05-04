# ACP / ACPX Schema Alignment Plan

Status: active
Created: 2026-04-29
Scope: protocol refactor toward ACPX readiness.

Implementation progress:

- 2026-04-29: Batch A started. Added ACP namespace constants and routing
  schema-audit coverage.
- 2026-04-29: Batch D started. Kernel accepts `_mustang.agent/*` extension
  method names while preserving legacy aliases; CLI and ProbeClient prefer the
  new namespace for shell/Python execution, model profile/default calls, session
  lifecycle actions, and provider-management probe helpers.
- 2026-04-29: Batch C landed for the active surfaces: codec serializes ACP
  metadata as `_meta`, incoming `_meta` is merged into schema models,
  `PromptRequest.maxTurns` and `session/list` filters are read from
  `mustang.agent/*` metadata, session archive/title metadata emits under
  `mustang.agent/session`, and worktree startup reads
  `mustang.agent/worktree` with the old key retained as input compatibility.
- 2026-04-29: Batch E landed for user REPL execution: kernel emits
  `_mustang.agent/session/execution_update` for shell/Python start/chunk/end,
  CLI and ProbeClient handle the new notification, and the old
  `session/update user_execution_*` parser path remains client-side only for
  compatibility with older kernels/fake probes.
- 2026-04-29: Batch F was reframed after architecture review. ACPX is treated
  as ACP-based semantics to absorb into DeepCLI's own Agent Control Plane, not
  as an external CLI/runtime dependency. The broader control-plane work now
  lives in [`agent-control-plane.md`](agent-control-plane.md).
- 2026-04-29: Batch G landed. Kernel routing no longer registers legacy
  non-standard method aliases, CLI/probe/tests use `_mustang.agent/*`, and old
  `session/update user_execution_*` compatibility parsing was removed from
  active clients.

## Goal

Prepare DeepCLI to absorb ACPX-compatible semantics while making the kernel's
ACP surface strictly official-schema-first:

1. Use the local ACP mirror as the source of truth for standard JSON-RPC
   methods and wire schemas.
2. Treat ACPX as a reference for ACP-based session, queue, status, identity,
   and flow semantics; do not depend on the `acpx` CLI or runtime API.
3. Move DeepCLI-only protocol affordances into an explicit DeepCLI extension
   namespace instead of occupying standard `session/*`, `model/*`, or
   `secrets/*` names.

Reference snapshots:

- ACP: [`docs/kernel/references/acp/`](../kernel/references/acp/)
- ACPX: [`docs/kernel/references/acpx/`](../kernel/references/acpx/)

## Current Audit

Official ACP methods in the current schema:

| Direction | Methods |
|---|---|
| Client -> Agent | `initialize`, `authenticate`, `session/new`, `session/load`, `session/list`, `session/prompt`, `session/cancel`, `session/close`, `session/resume`, `session/set_mode`, `session/set_config_option` |
| Agent -> Client | `session/update`, `session/request_permission`, `fs/read_text_file`, `fs/write_text_file`, `terminal/create`, `terminal/output`, `terminal/wait_for_exit`, `terminal/kill`, `terminal/release` |

DeepCLI currently implements these standard methods:

- `initialize`
- `authenticate` as a no-op compatibility response
- `session/new`
- `session/load`
- `session/list`
- `session/prompt`
- `session/cancel`
- `session/set_mode`
- `session/set_config_option`
- outgoing `session/update`
- outgoing `session/request_permission`

DeepCLI previously exposed non-standard methods on non-extension names. These
are now removed from kernel routing and replaced by `_mustang.agent/*`:

| Removed method | Problem | Replacement |
|---|---|---|
| `session/execute_shell` | Not in ACP schema; collides with future `session/*` namespace | `_mustang.agent/session/execute_shell` |
| `session/execute_python` | Not in ACP schema | `_mustang.agent/session/execute_python` |
| `session/cancel_execution` | Not in ACP schema | `_mustang.agent/session/cancel_execution` |
| `session/rename` | Not in ACP schema | `_mustang.agent/session/rename` |
| `session/archive` | Not in ACP schema | `_mustang.agent/session/archive` |
| `session/delete` | Not in current schema; related RFD exists | `_mustang.agent/session/delete` until ACP standardizes it |
| `model/profile_list` | Not in ACP schema | `_mustang.agent/model/profile_list` |
| `model/provider_list` | Not in ACP schema | `_mustang.agent/model/provider_list` |
| `model/provider_add` | Not in ACP schema | `_mustang.agent/model/provider_add` |
| `model/provider_remove` | Not in ACP schema | `_mustang.agent/model/provider_remove` |
| `model/provider_refresh` | Not in ACP schema | `_mustang.agent/model/provider_refresh` |
| `model/set_current` | Not in ACP schema | `_mustang.agent/model/set_current` |
| `secrets/auth` | Not in ACP schema | `_mustang.agent/secrets/auth` |

DeepCLI also has non-standard fields or update variants on standard methods:

| Surface | Current shape | Official-schema issue | Target |
|---|---|---|---|
| `session/prompt` | `maxTurns` root field | Official `PromptRequest` only has `sessionId`, `prompt`, `_meta` | Move to `_meta["mustang.agent/maxTurns"]` or an extension method |
| `session/list` | `includeArchived`, `archivedOnly` root fields | Official `ListSessionsRequest` only has `cursor`, `cwd`, `_meta` | Move filters to `_meta["mustang.agent/sessionFilters"]` or extension method |
| `SessionInfo` | `archivedAt`, `titleSource` root fields | Official `SessionInfo` only has `sessionId`, `cwd`, `title`, `updatedAt`, `_meta` | Move to `SessionInfo._meta["mustang.agent/session"]` |
| `session/update` | `user_execution_start/chunk/end` variants | Official `SessionUpdate` has no user-execution variants | Removed; use `_mustang.agent/session/execution_update` notifications |
| `available_commands_update` | Used as DeepCLI command catalog | Official slash-command page defines this as the standard command update shape | Align field types exactly; do not invent `commands/list` unless extension-prefixed |
| `_meta` keys | Mixed ad hoc keys | ACP says custom root fields are forbidden and `_meta` keys should be namespaced | Standardize on `mustang.agent/...` keys |

Official ACP methods we do not implement yet:

| Method | Notes |
|---|---|
| `session/close` | Directly overlaps with ACPX soft-close/resource release behavior. Implementation is tracked in [`agent-control-plane.md`](agent-control-plane.md) Batch B2. |
| `session/resume` | Useful for ACPX and external session-agent backends because it resumes without replay. Implementation is tracked in [`agent-control-plane.md`](agent-control-plane.md) Batch B2. |
| `fs/*` | Keep disabled for kernel-owned local tools, but implement client-side handling in ACPX/external-agent path. |
| `terminal/*` | Same as `fs/*`: needed for ACPX/external coding-agent operation, not for kernel local tools. |

## Naming Rule

ACP's current extension rule requires custom JSON-RPC method names to start
with `_`. DeepCLI extension methods will use:

```text
_mustang.agent/<area>/<action>
```

Examples:

- `_mustang.agent/session/delete`
- `_mustang.agent/model/provider_list`
- `_mustang.agent/secrets/auth`

Custom `_meta` keys will use:

```text
mustang.agent/<key>
```

Examples:

- `mustang.agent/session`
- `mustang.agent/tokenUsage`
- `mustang.agent/contextUsage`
- `mustang.agent/maxTurns`

## Compatibility and Probe Rule

Do not rename a live protocol method in isolation. Any batch that changes an
ACP method name, `_meta` layout, or `session/update` shape must include the
kernel, CLI, and probe changes in the same implementation slice.

Migration rule:

1. Add the new official or `_mustang.agent/*` surface first.
2. Update the CLI and probe layers in the same implementation slice.
3. Remove temporary legacy aliases only after the new surface is covered by
   tests and probes. Batch G has removed the aliases listed in this plan.

Probe hierarchy:

- The live kernel probe is the truth gate. It must exercise the real FastAPI
  kernel over ACP WebSocket and prove that the kernel protocol still works
  without fake-kernel shortcuts.
- The CLI PTY probe is the user-interface gate. It must launch the real CLI,
  drive real `!` / `$` key input through the pseudo-terminal, and assert that
  the CLI sends the new ACP methods while still rendering execution output.
- The fake-kernel PTY probe cannot replace the live kernel probe. It catches CLI
  regressions; it does not prove that the kernel route, schema, and session
  execution path are wired.

## Implementation Batches

### Batch A — Schema Truth and Drift Checks

Deliverables:

- Add `kernel.protocol.acp.namespaces` or equivalent constants for official
  method names, DeepCLI extension method names, and temporary legacy aliases.
  Avoid scattering string literals such as `session/execute_shell` across
  routing, handlers, tests, CLI, and probe code.
- Add a small schema-audit script or test that reads
  `docs/kernel/references/acp/schema.json` and compares official `x-method`
  names against `src/kernel/kernel/protocol/acp/routing.py`.
- Classify every routed method as `standard`, `mustang_extension`,
  `legacy_alias`, `client_method`, or `unsupported_official`.
- Fail the audit if a non-standard method is added without an underscore
  prefix.
- Extend the audit to scan `src/cli/`, `src/probe/`, `tests/`, and
  `tests/e2e/` for legacy method literals. During the compatibility window,
  legacy literals must be isolated behind constants, alias tests, or explicit
  compatibility assertions.
- Add a Pydantic/schema conformance test for standard request/response/update
  models against the mirrored JSON Schema.

Acceptance:

- The audit prints the same custom-method list as this plan, or a deliberately
  updated list.
- `rg` for unprefixed non-standard method strings is clean outside the
  documented legacy-alias allowlist.
- No runtime behavior changes yet.

### Batch B — Official Standard Gaps

Status: superseded by [`agent-control-plane.md`](agent-control-plane.md) Batch B2 for
`session/close` / `session/resume`; keep this section as protocol rationale.

Deliverables:

- Implement official `session/close` as resource release without deleting
  durable history. It should cancel in-flight work, detach live senders, and
  release runtime state.
- Implement official `session/resume` as load-without-replay.
- Update `InitializeResponse.agentCapabilities.sessionCapabilities` to
  advertise `close` and `resume` only once implemented.
- Keep `session/load` replay semantics intact.

Acceptance:

- Real ACP probe covers `session/new -> session/close -> session/resume`.
- Existing CLI startup/session flows still pass.

### Batch C — Move Root Custom Fields into `_meta`

Deliverables:

- Move `PromptRequest.maxTurns` to `_meta["mustang.agent/maxTurns"]`.
- Move `ListSessionsRequest.includeArchived` and `archivedOnly` to
  `_meta["mustang.agent/sessionFilters"]`.
- Move `SessionInfo.archivedAt` and `titleSource` to
  `SessionInfo._meta["mustang.agent/session"]`.
- Move worktree startup metadata from `_meta["worktree"]` to
  `_meta["mustang.agent/worktree"]`; keep a dual-read fallback for existing
  clients and E2E tests during the compatibility window.
- Move agent lifecycle metadata currently emitted as `mustang/agent_start` and
  `mustang/agent_end` to `mustang.agent/agentStart` and
  `mustang.agent/agentEnd`.
- Keep temporary backward-compatible readers for old fields and emit deprecation
  warnings in protocol logs.
- Update `src/cli/src/sessions/mapper.ts` and related CLI session state to
  dual-read old root fields and the new `mustang.agent/session` metadata.
- Update probe fixtures and assertions so generated session summaries include
  the new `_meta` shape.

Acceptance:

- Standard fields validate against ACP `schema.json`.
- CLI still sees archive/title metadata through the mapped DeepCLI `_meta`
  path.
- Live kernel probe covers `session/new` with `mustang.agent/worktree` metadata
  and verifies the old `_meta.worktree` compatibility path while the alias is
  still supported.

### Batch D — Namespace Extension Methods

Deliverables:

- Add `_mustang.agent/*` routes for every DeepCLI-only method listed in the
  audit.
- Keep the routing table explicit: new methods and legacy aliases may point to
  the same handler, but the alias status must be visible to the schema audit.
- Keep legacy aliases for one compatibility window. Completed in Batch G:
  `session/execute_shell`, `session/execute_python`, `session/cancel_execution`,
  `session/rename`, `session/archive`, `session/delete`, `model/*`,
  `secrets/auth` are no longer registered.
- Advertise extension support in `InitializeResponse.agentCapabilities._meta`
  under `mustang.agent`.
- Update CLI callers to prefer `_mustang.agent/*`, including:
  `src/cli/src/acp/client.ts`, `src/cli/src/session.ts`,
  `src/cli/src/sessions/service.ts`, `src/cli/src/models/service.ts`, and the
  active-port command/controller surfaces that dispatch model/session actions.
- Update `CommandManager` command definitions so non-standard command-backed
  ACP calls use `_mustang.agent/*` method names.
- Update kernel and E2E tests that currently call `model/profile_list`,
  `model/set_current`, `session/rename`, `session/archive`, `session/delete`,
  `session/execute_shell`, `session/execute_python`, or
  `session/cancel_execution` directly.
- Update `src/cli/tests/probe_phase_b_pty.ts` so the fake kernel accepts the
  new `_mustang.agent/*` calls and the main assertion requires the real CLI to
  send the new methods.

Acceptance:

- New CLI uses only official ACP methods or `_mustang.agent/*` methods.
- Legacy aliases were temporary and are removed in Batch G.
- Live kernel probe verifies `_mustang.agent/session/execute_shell`,
  `_mustang.agent/session/execute_python`, and
  `_mustang.agent/session/cancel_execution` against the real kernel.
- CLI PTY probe sends `!echo ...` and `$print(...)`, then asserts that the fake
  kernel saw `_mustang.agent/session/execute_shell` and
  `_mustang.agent/session/execute_python`.

### Batch E — Update Extension Events

Deliverables:

- Replace non-standard `session/update` variants
  `user_execution_start/chunk/end` with an extension notification:
  `_mustang.agent/session/execution_update`.
- Preferred shape:

  ```json
  {
    "sessionId": "session-id",
    "execution": {
      "phase": "start|chunk|end",
      "kind": "shell|python",
      "executionId": "execution-id",
      "stream": "stdout|stderr",
      "text": "chunk text",
      "exitCode": 0,
      "cancelled": false,
      "input": "original user input",
      "shell": "auto",
      "excludeFromContext": false
    },
    "_meta": {
      "mustang.agent/version": 1
    }
  }
  ```

- If this shape proves awkward for ACP clients, the fallback is to represent
  kernel user REPL execution as standard `tool_call` / `tool_call_update`
  updates with `kind: "execute"` and `mustang.agent/repl` metadata. Choose
  explicitly before coding and document the reason.
- Update CLI adapter mapping and PTY probes accordingly.
- Batch G removes client-side compatibility parsing for the old
  `session/update user_execution_*` variants; clients should consume
  `_mustang.agent/session/execution_update`.

Acceptance:

- Standard `SessionUpdate` union validates against ACP schema.
- CLI still renders `!`, `!!`, `$`, and `$$` execution streams.
- Live kernel probe verifies start, chunk, end, and cancel behavior for shell
  and Python execution.
- CLI PTY probe verifies visible shell/Python output and cancellation rendering
  through the new notification path.

### Batch F — Agent Control Plane Foundations

Deliverables:

- Add the dedicated Agent Control Plane plan:
  [`agent-control-plane.md`](agent-control-plane.md).
- Define the northbound/southbound symmetry:
  - northbound: CLI / Probe / future Home Screen control the primary Kernel;
  - southbound: the Kernel controls child Kernels, durable Session Agents, and
    external ACP-compatible agents.
- Keep the naming precise:
  - "Agent Control Plane" means DeepCLI-owned lifecycle, queue, status,
    identity, permission, and control semantics;
  - "External ACP Runtime Adapter" means the optional adapter for third-party
    ACP stdio agents;
  - "ACPX-compatible semantics" means behavior we implement directly after
    reviewing ACPX, not a dependency on the `acpx` executable or runtime API.
- Audit `AgentTool` as the current local sub-agent primitive and document the
  gap from nested in-process turns to durable controllable Session Agents.
- Define the shared identity model for DeepCLI agent ids, DeepCLI task ids,
  ACP session ids, provider-native session ids, and optional ACPX-compatible
  record ids.
- Define the shared control vocabulary for `prompt`, `send_message`, `cancel`,
  `pause/resume`, `status`, queue ownership, named sessions, and close/release.
- Decide which capabilities belong in official ACP methods, which belong in
  `_mustang.agent/*`, and which remain internal kernel APIs.

Acceptance:

- The plan explicitly says DeepCLI must not shell out to `acpx` or import ACPX
  runtime APIs for core control-plane behavior.
- The plan separates internal DeepCLI Session Agents / child Kernels from
  external ACP-compatible agents.
- Probe requirements are updated to verify DeepCLI-owned control-plane seams
  first, and only then optional external ACP adapter seams.
- DeepCLI kernel local tools continue to bypass ACP client `fs/*`/`terminal/*`;
  those ACP client methods are only for external ACP runtime adapters.

### Batch G — Remove Legacy Aliases

Deliverables:

- Remove legacy non-standard methods after CLI and probe have moved.
- Update docs so `session/*` contains only official ACP methods.
- Keep migration notes in `docs/plans/progress.md`.

Acceptance:

- Schema audit has no `legacy_alias` entries.
- Protocol docs and routing tables agree.
- `rg` over active source/tests has no old unprefixed method strings or
  `user_execution_*` variants.

## Test and Probe Requirements

- Unit tests for schema classification and extension naming.
- ACP conformance tests for standard method params/results.
- Live kernel ACP probes for `session/close`, `session/resume`, and renamed
  extension methods.
- CLI PTY probe updates for `_mustang.agent/*` calls.
- ACPX probe for one external agent path before claiming the ACPX switch is
  complete.

Minimum command set for any implementation batch that touches protocol names or
execution streams:

```bash
uv run pytest tests/kernel/protocol/test_routing.py tests/kernel/protocol/test_session_handler.py tests/kernel/session/test_user_repl.py -q
uv run pytest tests/e2e/test_kernel_e2e.py tests/e2e/test_secret_e2e.py -q -m e2e
bun run src/cli/tests/probe_phase_b_pty.ts
bun run src/cli/tests/run_all.ts
```

Add or update a live-kernel probe before Batch D lands. It should use
`ProbeClient` or raw JSON-RPC over the real `/session` WebSocket and cover:

- `_mustang.agent/session/execute_shell`
- `_mustang.agent/session/execute_python`
- `_mustang.agent/session/cancel_execution`
- streamed start/chunk/end execution notifications

Definition of done for a protocol migration batch:

- Kernel protocol/unit tests pass.
- Live kernel probe output is pasted into the completion report.
- CLI PTY probe passes and proves real `!` and `$` input still works.
- `src/cli/` and `src/probe/` are updated in the same change as the kernel.
- Schema audit reports no unprefixed non-standard method in active routing.

## Non-Goals

- Replacing DeepCLI's kernel orchestrator with ACPX.
- Moving kernel local Bash/File/Python tools to ACP client `fs/*` or
  `terminal/*`.
- Implementing every ACP RFD immediately. RFDs are tracked as candidates until
  they land in `schema.json` or DeepCLI deliberately adopts them as extensions.
