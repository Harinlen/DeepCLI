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
- **Windows batch launchers cannot delete themselves synchronously**:
  if a `.cmd` wrapper calls PowerShell and the PowerShell uninstall path
  deletes that wrapper before returning, `cmd.exe` resumes by reading a
  now-missing batch file and prints `The batch file cannot be found`.
  Defer shim deletion until after the wrapper exits.

---

## Async / Concurrency

- **ASGI WebSocket apps cannot observe native pong in the current stack**:
  Uvicorn 0.42 passes `ws_ping_interval` / `ws_ping_timeout` to the
  `websockets` protocol, but Starlette applications only receive
  `websocket.receive` and `websocket.disconnect` ASGI events. Native pong
  keeps dead connections from lingering, but it is not an application-level
  timestamp. Route freshness must therefore use observable runtime traffic
  such as registration and successful runtime responses, while disconnect
  unregisters the route.

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

- **MCP stdio declarations need explicit normalization before SQLite
  round-trip**: Claude Code-style stdio server entries may omit
  `type`, but Mustang stores MCP declarations behind a Pydantic
  discriminated union.  Normalize missing `type` to `stdio` at the
  MCP schema boundary; otherwise legacy `mcp.yaml` imports can persist
  fine but fail validation on the next ResourceStore startup.

- **Config refresh does not imply subscriber signals.**  `ConfigManager
  .refresh_from_resource_store()` updates already-materialized sections
  from SQLite, but it is not the same path as owner `MutableSection.update()`
  and does not emit `changed`.  Subsystems that cache parsed config, such
  as ToolAuthorizer's `RuleStore`, need an explicit section revision check
  before calls or they will keep stale policy after a ResourceStore refresh.

- **Tool config must store stable secret refs, not compatibility names.**
  WebFetch can keep legacy name lookup for operator ergonomics, but durable
  config rows should persist the `secret:<uuid>` returned by SecretManager.
  Otherwise rename-stable SecretStore semantics are bypassed even though
  plaintext stays out of `config_sections`.

## Tool Contracts

- **CLI slash dispatch stubs are not E2E evidence**: a CLI test that
  replaces `managementRequest()` with an in-memory responder only proves
  parser-to-method mapping.  It does not prove the real
  `deepcli -> Access Router -> Runtime` route accepts that ACP method.
  ResourceStore management commands owned by the Access Router
  (`/agents`, `/gateways`, `/mcp`) need a Router `/session` WebSocket
  probe that asserts the request is handled locally and does not leak
  into the Runtime `agent.tools_request` fallback.

- **CLI-visible runtime ACP methods must be tested through Agent Hub,
  not only Access-local dispatch**: the real supervised CLI path is
  `Access Agent -> Agent Hub -> Primary Runtime`.  A method can pass
  Access-side ACP/E2E tests and still fail in the CLI if Hub does not
  forward its `agent.*` runtime contract.  Keep runtime contracts in
  `kernel.agent_hub.contracts.AgentRuntimeContract`, not ad hoc string
  sets, and run `tests/kernel/agent_hub/test_agent_hub_transport_c.py`
  plus a router-path probe for every new runtime ACP method.  This
  caught `/webfetch backend` failing with `unknown hub contract:
  agent.tools_request` after Access-local WebFetch probes had passed.

- **Agent Hub `agent.tools_request` is not a 5s control-plane ping**:
  tool-management slash commands can validate remote credentials, install
  local dependencies, or call provider APIs.  If Hub keeps a short
  forwarded-runtime timeout while the CLI allows a longer request timeout,
  the user sees `[-32603] Internal error` even though the operation is
  still ordinary latency.  Align Hub forwarded timeouts with the CLI
  command timeout, and test the timeout selection in
  `tests/kernel/agent_hub/test_agent_hub_transport_c.py`.

- **Tool implementation class names are not the LLM contract**:
  Claude Code's file reader is implemented as `FileReadTool`, but the
  exposed tool name is `Read`.  When porting tools, align the schema
  name, prompt text, permission rules, REPL hiding, and input parameter
  names with the exposed contract.  Keep implementation names only as
  code organization details, and add aliases only for backwards
  compatibility.

- **Runtime lifecycle is a control-plane concern, not a shell side
  effect**: letting an Agent run `kill` against its own runtime can cut
  the turn before the tool result is persisted, producing orphan
  `tool_calls` and provider-invalid history on the next request.
  Self-restart must be a narrow tool (`RestartSelf`) that first returns
  a normal tool result, then asks Supervisor to restart the current Agent
  after the response has had time to flush.  Full runtime restarts belong
  to operator ACP/CLI methods such as `/kernel restart`, not model-visible
  Bash commands.

- **OpenAI-compatible tool results must repair adjacency, not just
  existence**: providers such as DeepSeek reject any assistant message
  with `tool_calls` unless matching tool messages immediately follow it.
  A later user retry or assistant error means appending a synthetic result
  at the end is too late.  Repair the retained conversation by inserting
  synthetic error tool results directly after the offending assistant
  message, remove duplicate/orphan tool results that no longer have a
  preceding assistant `tool_calls`, persist a `HistorySnapshot`, then
  retry the provider call once.

- **Resume state must restore behavior, not only UI metadata**:
  after a Primary Runtime restart, `session/resume` may correctly return
  `modes.currentModeId="bypass"` while the newly constructed Orchestrator
  remains in its default mode.  The CLI then displays Bypass but the
  ToolAuthorizer still asks for permissions.  When loading a session from
  disk, apply the persisted mode to both `Session.mode_id` and
  `session.orchestrator.set_mode(...)`, then verify with a live
  restart/resume/tool probe, not only a resume response assertion.

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

- **Completed-turn replay must use the same de-duplication rules as
  session load.**  Session logs can contain both explicit UI events
  (`AgentMessageEvent`) and conversation-history fallback rows
  (`ConversationMessageEvent`) for the same assistant text.  `session/load`
  already de-dupes those rows, but duplicate `clientTurnId` replay once
  bypassed that path and emitted `pongpong`.  Any new replay surface must
  reuse explicit replay keys before sending client-visible chunks.

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

- **Skill slash commands must be Kernel projections, not CLI filesystem
  reads.**  Skills can be project/user/dynamic/MCP-scoped, and router mode
  executes prompts in the Primary Runtime.  If the CLI scans local skill
  files, autocomplete and activation can diverge from the runtime prompt.
  Project `user_invocable` skills through CommandManager, expose them via
  `_mustang.agent/commands/list`, and activate through
  `_mustang.agent/session/activate_skill`.

- **Session titles must come from user-visible text, not internal prompt
  wrappers.**  Skill activation wraps the skill body in a text prompt that
  includes `<system-reminder>` and `<skill>` blocks.  Blindly using the
  first text block as an auto title leaks internal instructions into Recent
  sessions and can break the TUI if the title contains newlines.  Strip
  internal blocks in Kernel title generation, summarise skill activations
  as `/skill args`, and keep CLI list/welcome renderers defensive against
  old dirty titles.

- **Current skill state must override stale skill context.**  Deleting
  `~/.deepcli/skills/*/SKILL.md` after Kernel startup leaves two stale
  surfaces unless handled explicitly: the in-memory SkillRegistry and old
  assistant/system-reminder text already persisted in a conversation.  Prune
  missing file-backed skills whenever listings/lookups/activations are read,
  emit `skills_changed` so command projections update, and inject a current
  empty Available skills reminder so resumed sessions ignore old skill lists.

- **OpenAI-compatible tool histories must be sealed before resume.**  If a
  process dies after an assistant `tool_calls` message is persisted but
  before matching `tool_result` messages are written, the next user prompt
  creates a provider-invalid transcript.  Seal pending tool uses with
  synthetic error results before appending the resumed prompt; do not rely on
  provider formatters to silently repair or discard history.

- **Primary Runtime needs the same trailing subsystem order as the
  kernel app.**  Cron tools looked registered but failed under Probe
  because the Runtime loaded `Tools` before `SessionManager` and never
  loaded `ScheduleManager` afterward.  Subsystems that depend on
  session/gateway state (`GatewayManager`, `ScheduleManager`) must start
  after the Runtime `SessionManager`, mirroring `kernel.app` ordering.

- **Supervisor control cannot assume Unix sockets on Windows.**  The
  packaged Windows launcher can start the Supervisor only if
  `kernel.supervisor.control` avoids `socketserver.UnixStreamServer`.
  Keep POSIX on Unix sockets, but use a loopback TCP fallback on Windows
  behind the same control-path marker so Access Agent / Runtime callers
  do not need a different argument contract.

- **TUI visibility toggles that affect prior transcript lines need a
  forced redraw.**  The active-port differential renderer intentionally
  skips changes above the current viewport.  That is usually correct,
  but `Ctrl+T` can change old Thinking blocks split around tool calls.
  After mutating existing transcript components for a global visibility
  toggle, call `requestRender(true)` and cover it with a real PTY probe.

- **Legacy global-resource import must be lazy from bootstrap managers.**
  `apply_legacy_yaml_import()` imports Config/Flag SQLite backends, while
  `ConfigManager` and `FlagManager` are exported from package
  `__init__.py`.  Importing the legacy helper at manager module load time
  creates a partial-initialization cycle during test collection.  Keep that
  helper import inside `startup()` / `initialize()` or move the import
  dependency out of the helper before making it eager.

- **SecretStore migration cannot trust `user_version` alone.**  The legacy
  sqlite3 `SecretManager` and the new UUID `SecretStore` both used schema
  version 2 at one point, but their `secrets` table shapes are incompatible
  (`name/value/type` versus `secret_id/name/value_ciphertext`).  Startup
  must inspect the table columns, move the legacy DB aside, and import rows
  into UUID records instead of assuming `PRAGMA user_version` proves schema
  compatibility.

- **Management ACP methods need both Access Router and Runtime routing.**
  The Access-local `AcpSessionHandler` can dispatch Kernel-owned management
  methods in tests, but real router mode forwards selected `_mustang.agent/*`
  calls to the Primary Runtime.  Register new management methods in
  `REQUEST_DISPATCH` and in the runtime `_deliver_router_acp` allowlist, or
  the real subsystem path falls through to the wrong contract handler.

- **Agent message management must stay at the Access Router boundary.**
  Most management ACP methods can delegate to command services wherever their
  owning subsystem lives, but `/agent send` needs the live `AccessRouter`
  route table.  Do not route it through Agent Hub or a runtime-local manager;
  tests/probes should assert `agent_hub_forward_count == 0` and that delivery
  used `AccessRouter.deliver_turn()`.

- **Agent state-dir cleanup must be narrowly owned.**  Agent definitions can
  technically store any `state_dir`, but delete-time recursive cleanup is only
  safe for the manager-owned path `AgentManager.home / "agents" / agent_id`.
  External state dirs should leave `state_dir_deletion_status=pending` with a
  cleanup error instead of deleting arbitrary user paths.

- **Schedule ResourceStore migration must remove hidden direct-SQL paths.**
  `CronScheduler` previously reached through `CronStore.db` for claims,
  heartbeats, stale-claim cleanup, and old execution reaping.  Moving durable
  schedule truth to `scheduled_tasks` is incomplete if scheduler code can still
  write old `cron_tasks` tables directly.  Keep scheduler persistence behind
  `CronStore` methods so ResourceStore remains the only task declaration truth.

- **Aggregate probes must exercise non-empty ResourceStore revision paths.**
  `/flags set` can pass against an empty ResourceStore row without an
  `expectedRevision`, but the real management path usually mutates an existing
  startup snapshot row.  Closure probes should include pre-existing rows and
  pass the observed revision, so CAS conflicts are tested instead of hidden.

- **CLI thin layers must not invent absent Kernel methods.**  The global
  ResourceStore CLI bridge can parse slash commands and call existing ACP
  methods, but `/gateways create/delete` had no Kernel ACP surface when the CLI
  bridge landed.  Report those commands as unsupported until the Kernel owns
  real create/delete semantics; do not emulate them with direct SQLite writes
  or misleading enable/disable aliases.

- **Gateway delete is routing metadata cleanup, not Agent cleanup.**  Access
  Router gateway declarations live in `access_adapters`; gateway/channel routes
  live in `access_channel_bindings`.  Deleting a gateway should remove the
  adapter declaration, append an adapter event, and disable that gateway's
  channel bindings.  It must not touch Agent definitions, workspaces, or the
  reserved `agent_bindings` table.

- **Skill declarations are not skill content.**  `parse_skill_manifest()` can
  derive a missing description from the Markdown body, but ResourceStore global
  skill declarations must persist only manifest/index metadata.  When importing
  global skill declarations, avoid persisting body-derived text, `SKILL.md`
  body content, supporting file contents, invoked-skill cache, or secret setup
  defaults; the durable row should remain a revisioned index pointing at
  filesystem content, not a copy of the skill.

- **Hook declarations are trigger metadata, not handler state.**  ResourceStore
  hook rows should contain manifest fields, trigger bindings, enabled state,
  and handler path pointers only.  `handler.py` bodies are trusted runtime code
  loaded from disk, and `HookEventCtx.messages` / execution output are runtime
  state.  Persisting either as declaration truth leaks implementation details
  and makes manifest drift semantics impossible to reason about.

- **Memory declarations are policy, not memory.**  The durable global memory
  declaration can own namespace enablement, disposition, retention policy, and
  index policy.  Actual memory entries, markdown bodies, embeddings/vector
  files, generated indexes, recall caches, summaries, runtime notes, and logs
  are memory data or runtime state.  Keeping those out of `config_sections`
  prevents ResourceStore exports from becoming hidden memory dumps.

- **Prompt declarations are indexes, not prompt text.**  The durable prompt
  declaration should track prompt ids, source paths, enabled state, routing
  metadata, and placeholders.  Prompt bodies, rendered prompt text, session
  prompt snapshots, and render caches must stay file/runtime data; otherwise a
  ResourceStore export can leak prompt content or per-session secrets.

- **MCP management writes declarations, not live sessions.**  Global MCP
  create/update/delete should mutate only the ResourceStore declaration row and
  report after-restart semantics.  Runtime connection/session state remains
  owned by `MCPManager`; trying to make the management surface hot-edit live
  connections risks mixing durable config with transient transport state.

- **Runtime route freshness needs runtime-originated heartbeats.**  Native
  WebSocket pong is not observable through the ASGI layer, and refreshing
  `last_seen` only after successful runtime traffic leaves the first command
  after an idle period vulnerable to `route stale: primary`.  The Access Router
  route freshness contract needs an application-level runtime ping that can be
  consumed even while an ACP request is in flight.

- **Access Router `/session` must mirror SessionHandler control-plane specials.**
  Methods such as `_mustang.agent/runtime/status` are not ordinary runtime tool
  requests and are not always in `REQUEST_DISPATCH`.  When the CLI connects to
  the lightweight Access Router `/session` path, those methods must be handled
  locally through the Supervisor control socket; otherwise they leak to the
  Primary Runtime and fail as unsupported tool requests.

- **Slash-command closure needs a real CLI-to-kernel probe.**  A mock
  `managementRequest()` probe can prove parser/dispatch behavior and prevent
  direct SQLite writes, but it cannot prove Access Router `/session`, runtime
  route freshness, Supervisor control methods, or Primary Runtime handlers.
  User-facing slash commands need at least one probe that starts the supervised
  kernel, connects through the CLI `AcpClient`, and invokes the same
  `executeBuiltinSlashCommand()` path as the TUI.

- **Skill commands need both command projection and runtime activation routing.**
  Listing `skill:<name>` in the command catalog only proves discoverability.
  The active Access Router path sends `/skill:<name>` as
  `_mustang.agent/session/activate_skill`; the Primary Runtime direct ACP
  dispatcher must handle that method explicitly, or it falls through to
  `tools_request` and fails as an unsupported tools request.

- **Real command probes need catalog coverage guards.**  A real-kernel slash
  smoke test can still drift if new builtin commands are added without adding
  smoke inputs.  Import the builtin slash catalog in the real probe and fail
  when any catalog command is missing from the smoke list; keep mock dispatch
  probes clearly labeled as parser-only.

- **Dynamic skill commands need catalog-driven activation checks.**  Testing one
  fixed `/skill:<name>` proves only that single built-in skill path.  Because
  the runtime command catalog can project every user-invocable skill as
  `skill:<name>`, the real CLI-to-kernel probe should fetch `commands/list`,
  filter `source=skill`, and activate each returned command through the same
  CLI path users run.

- **Final migration closure should orchestrate existing probes.**  When a plan
  already has source-backed subsystem probes, the final monolithic target should
  run those probes as subprocesses and assert their public markers instead of
  copying their setup logic.  That keeps one acceptance command while avoiding
  a second, drifting implementation of the same closure seams.

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
