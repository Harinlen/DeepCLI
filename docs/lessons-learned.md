# Lessons Learned

Non-obvious pitfalls and design lessons.  Read before hitting the same
wall twice.

---

## Build & Environment

- **Nested package layout**: hatchling requires `src/kernel/kernel/`
  (outer is project root, inner is the Python package).
- **Pytest path checks must be separator-agnostic**: Windows reports
  test paths with backslashes, so marker hooks must use `Path.parts`
  instead of substring checks like `"/e2e/"`.  Otherwise E2E tests may
  miss the `e2e` marker and run during the default non-E2E setup smoke.
- **cloc**: PyPI's `cloc` package has no CLI.  Install the Perl tool
  via `apt install cloc`.
- **httpx HTTP/2 optional dependency**: `http2=True` needs package
  `h2`.  In environments without `h2`, web tools must gracefully
  downgrade to HTTP/1.1 instead of raising an internal error.
- **npm `--silent` removed in v11**: use `node --import tsx` directly
  instead of `npm run` to avoid noisy script banners.

---

## Async / Concurrency

- **OpenAI-compatible SSE must not treat EOF as success unless `[DONE]` arrived**:
  a provider can close a chunked response mid-stream with an incomplete body,
  or end the SSE stream without the terminal marker.  Both are transport
  failures, not normal completions.  Mark them as transient transport
  `StreamError`s so the orchestrator can retry before any history/tool commit.

- **Agent Runtime prompt calls cannot use short RPC timeouts**: the Hub
  to Primary Runtime `agent.prompt` path is a full LLM turn, not a
  control-plane ping.  A fixed 5s websocket response timeout turns
  normal tool/LLM latency into `[-32603] Internal error`; prompt
  contracts must wait for turn completion and be covered by a
  real Supervisor + CLI/Probe check.

- **`asyncio.gather(return_exceptions=True)` swallows `CancelledError`**:
  `CancelledError` is a `BaseException`, not an `Exception`.  With
  `return_exceptions=True`, it lands in the results list instead of
  propagating.  Always scan gather results for `BaseException`
  subclasses and re-raise `CancelledError` explicitly.

- **Sub-agent timeout must cancel the child task**: catching
  `TimeoutError` and emitting an error result is not enough — the
  child coroutine keeps running in the background.  Wrap the child
  query in an `asyncio.Task` and explicitly `.cancel()` it on timeout,
  then `await` the task to let cleanup run.

- **MCP reconnect race**: reject all pending futures *before*
  reconnecting, not after.  Otherwise in-flight callers unblock with
  stale state mid-reconnect.

- **Hook fire-and-forget leaks**: `asyncio.create_task()` without
  storing the reference allows tasks to be GC'd before completion.
  Keep task references in a set; remove on done callback; drain set
  on shutdown.

---

## Config & Serialisation

- **Never use `value or default` for config fields**: `0`, `""`, and
  `False` are all falsy, so users can never intentionally set those
  values.  Always use `value if value is not None else default`.

- **Env var substitution should warn on missing vars**: silently
  returning an empty string for `${UNDEFINED_VAR}` hides
  misconfiguration.  Log a warning so the operator sees it at startup.

- **Pass generated CLI secrets as `--flag=value`**: random tokens from
  `secrets.token_urlsafe()` can begin with `-`.  If a Supervisor passes
  such a token as `--primary-token <token>`, `argparse` may treat the
  token as another option and fail child startup.  Use
  `--primary-token=<token>` / `--registration-token=<token>` for
  generated values.

- **CLI/Probe live smokes must assert ACP initialization, not just socket auth**:
  before 2026-05-03 the kernel had a non-ACP echo stack, so a live
  CLI smoke could connect and authenticate successfully but hang forever
  on `initialize`.  The echo stack has been removed and `acp` is now the
  only production stack; keep live smokes checking the actual ACP
  handshake so this class of regression stays visible.

- **Pydantic field validators cannot always see sibling fields**:
  `AgentRuntimeSpec` tried to require `command` when `kind` was
  `child_kernel` or `external_acp`, but the field validator looked in
  `info.data` before the model was fully assembled, so the invariant did
  not run.  Cross-field invariants should use
  `@model_validator(mode="after")`.

---

## Security

- **SSRF via redirect chain**: checking the domain only on the
  *initial* URL is insufficient.  An attacker redirects `safe.com →
  169.254.169.254`.  The request to the private IP has already been
  sent by the time the final URL is inspected.  Solution: set
  `follow_redirects=False`, manually follow each hop, and check the
  domain *at every hop* before issuing the next request.

---

## Implementation Discipline

- **Subsystem-dependent tools need a real context bridge probe**:
  SkillTool had documentation saying `ToolContext` carried
  `module_table`, but the dataclass and ToolExecutor builder did not
  actually pass it.  Unit tests that only checked SkillManager startup
  and prompt construction missed the real closure seam; a live
  SkillTool probe caught the failure.  For subsystem-backed tools,
  test both discovery/listing and actual tool invocation through
  ToolExecutor.

- **Root `.gitignore` `scripts/` matches nested script directories**:
  the pattern ignores `src/cli/scripts/` as well as the repo-root
  scratch directory.  Formal, version-controlled script directories
  need explicit unignore rules such as `!src/cli/scripts/**`.

- **Never silently skip plan items**: if the plan lists 12 test files,
  all 12 must be written.  During SkillManager implementation, 5 of 12
  planned test files were skipped without explanation — including
  `test_skill_tool.py` which would have caught a missing `display`
  parameter on `ToolCallResult`.  The bug shipped and was only found
  during manual probe testing.  Rule: cross-check the plan's file list
  against `tests/` before marking done.  If a plan item is genuinely
  unnecessary, update the plan with the reason — don't silently drop it.

- **E2E tests must exercise actual code paths**: a test that sends a
  prompt and only asserts `stop_reason == "end_turn"` proves nothing
  about the feature.  E2E tests must assert on observable output —
  returned text content, tool call events in the stream, specific error
  messages for invalid input.  "Kernel didn't crash" is a smoke test,
  not feature verification.

- **Closures that cross subsystem boundaries REQUIRE a probe against
  the real subsystem — mock tests of the closure only prove your
  mental model is internally consistent**.  If a closure calls out to
  LLMManager, HookManager, MCP, or any subprocess/API, write a probe
  that actually invokes that subsystem.  Full procedure in
  [`workflow/definition-of-done.md`](workflow/definition-of-done.md)
  (five gates) and [`workflow/workflow.md`](workflow/workflow.md)
  Phase 4.5 (closure-seam inventory).  The `/done-check` skill
  (`.claude/skills/done-check/`) runs the enumeration automatically.
  Caught during Phase 1 CC alignment — 3 bugs lived in 3 such
  closures, all covered by passing mock tests:

  1. `_make_summarise_closure` iterated `async for chunk in
     llm_manager.stream(...)`, but `LLMManager.stream()` is
     `async def` returning a generator — must `await` first.  Mock
     returned a plain async generator so the bug was invisible.

  2. Same closure sent `PromptSection(text="")`.  Anthropic/Bedrock
     reject empty system text ("system: text content blocks must
     be non-empty").  Mock LLM accepted it fine.

  3. `fire_hook` closure called `hooks.fire(event, ctx)`, but
     `HookManager.fire()` only takes `ctx` (reads `ctx.event`
     internally).  Mock accepted any arg arity.

  Rule: for every such closure, there is a `scripts/probe_<name>.py`
  or `tests/e2e/test_<name>_e2e.py` that runs it against the real
  thing.  "Unit tests pass" is necessary but never sufficient.

- **When `LLMManager.stream()` changes, grep all subsystem callers, not
  just orchestrator-adjacent closures.**  Memory selector/background
  kept the old `stream(model, messages, max_tokens)` shape after the
  LLM interface required `system`, `tool_schemas`, and `temperature`
  keyword-only args plus awaiting the returned generator factory.
  Fix shared helpers at subsystem boundaries so sibling paths cannot
  drift independently.

- **Idempotent cleanup APIs must report whether they actually changed
  state.**  The cron session reaper repeatedly logged
  `deleted 1 expired cron sessions` for an already-missing session
  because `SessionStore.delete_session()` treated "0 rows deleted" as
  success.  Maintenance loops that run from durable audit tables must
  distinguish "already gone" from "deleted now" or their logs become
  misleading until the audit record ages out.

- **CLI streaming event listeners are async and must be serialized.**
  `DeepCLIAgentSessionAdapter` originally emitted OMP-style events with
  `void listener(event)`.  Slow tool rendering could still be handling a
  `message_update` / `tool_execution_end` when `message_end` and
  `agent_end` arrived, causing the final assistant text to be persisted
  by the kernel but never rendered in the TUI.  Queue adapter events and
  flush before ending the assistant turn.

- **CLI status area is for one-line status, not structured output.**
  `active-port` `/session list` originally rendered the numbered session
  list through `showStatus()`, which writes to the bottom status
  container.  Multiline content there visually collides with the editor
  and status line.  Lists, tables, transcripts, and other durable output
  should render into `chatContainer` or a dedicated selector component.

- **Do not mount empty assistant components before tool output.**
  The OMP event controller adaptation originally added an
  `AssistantMessageComponent` on `message_start` even when it had no
  visible text/thinking yet.  Tool components were appended later, so
  final text streamed into the already-mounted component appeared above
  the tools.  Mount assistant components lazily when visible content
  arrives so tool-first turns render as tool output first, answer second.

- **Copied active-port code needs an automated drift ledger.**
  "Copied from OMP" is not a guarantee unless the copied files are
  compared against a recorded OMP baseline.  For CLI/TUI work, keep
  upstream-identical files enforced by `check_omp_parity.ts`, and
  require every intentional diff to be classified as an ACP adapter
  seam or unsupported-service stub with a regression test.

- **Full assistant message updates can replay completed tool calls.**
  `DeepCLIAgentSessionAdapter` emits OMP-style `message_update` events
  with the whole assistant message.  After a tool has completed,
  later answer chunks still carry the earlier `toolCall` block.  If the
  TUI has already removed that id from `pendingTools`, blindly scanning
  the full message recreates a stale `pending <tool>` component below
  the final answer.  Track completed tool call ids in the event
  controller and skip replay unless the tool is still genuinely pending.

- **Router backend lifecycle methods must use the same runtime as
  prompt/new**.  After `session/new` moved through Access -> Hub ->
  Primary Runtime, leaving `session/list` and `session/load` on the
  Access-local SessionManager produced an empty/stale view and hid
  completed-turn replay from Probe tests.  Any router backend lifecycle
  method that observes or mutates session state must route to the same
  Primary Runtime session store.

- **Router backend extension methods are session-state methods too.**
  `session/set_mode` and DeepCLI-owned execution methods
  (`_mustang.agent/session/execute_shell`, `execute_python`,
  `cancel_execution`) originally looked like local protocol extensions,
  but in router mode they mutate or observe the Primary Runtime session.
  Probe caught the mismatch as `Session not found` from the Access-local
  SessionManager.  Treat every method carrying a `sessionId` as suspect
  when adding router backend support.

- **Router backend model methods must mutate the Primary Runtime, not
  Access-local config only.**  `/model` reads/writes look global, but
  prompt execution in router mode uses the Primary Runtime's LLMManager
  and active Orchestrator instances.  If `_mustang.agent/model/*` stops
  at Access, the UI can show the new default while the next prompt still
  goes to the old provider.  Route model-management ACP methods through
  Hub to Primary Runtime and probe by asserting the fake provider sees
  the switched model on an already-open session.

- **Primary Runtime needs the same trailing subsystem order as the
  kernel app.**  Cron tools looked registered but failed under Probe
  because the Runtime loaded `Tools` before `SessionManager` and never
  loaded `ScheduleManager` afterward.  Subsystems that depend on
  session/gateway state (`GatewayManager`, `ScheduleManager`) must start
  after the Runtime `SessionManager`, mirroring `kernel.app` ordering.

- **TUI visibility toggles that affect prior transcript lines need a
  forced redraw.**  The active-port differential renderer intentionally
  skips changes above the current viewport.  That is usually correct,
  but `Ctrl+T` can change old Thinking blocks split around tool calls.
  After mutating existing transcript components for a global visibility
  toggle, call `requestRender(true)` and cover it with a real PTY probe.

---

## Kernel Design-debt Backlog

- **Hook executor dispatch**: hardcodes executor types.  Refactor to
  self-registering dispatch when `agent` type joins.
- **Orchestrator permission injection**: imports `needs_permission`
  directly.  Replace with injectable callable.
- **Glob `**` on huge directories**: slow scan, no guardrail yet.
- **web_fetch anti-bot fallback**: some modern sites require browser
  execution.  HTTP fetch is primary; add optional headless-browser
  fallback (Playwright) for timeout/empty-content failures.
