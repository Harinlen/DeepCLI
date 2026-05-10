# GatewayManager — Design

Status: **superseded for future work**. This document records the older
GatewayManager design; any current `kernel.agents.mustang.gateways` code should be treated as
legacy/transition code until it is migrated into the Access Agent Platform
Adapter model.

> 2026-04-30 architecture note: this standalone GatewayManager design is
> superseded for future implementation by
> [`agent-control-plane.md`](../history/plans/agent-control-plane.md). Platform
> integrations should become Access Agent Platform Adapters with ingress/reply
> sinks owned by Access Agent and target routing handled by Agent Hub.Router.
> Do not implement new platform entrypoints that deliver directly to
> `SessionManager`.

Enables the kernel to communicate with external messaging platforms
(Discord, Telegram, WhatsApp, etc.) as a first-class feature.
Modelled after the LLM Provider pattern: `GatewayAdapter` is to
`GatewayManager` as `Provider` is to `LLMProviderManager`.

---

## Relationship to Existing WS Entry Point

The existing `/session` WebSocket is **not** a `GatewayAdapter`.
It is a separate, parallel entry path for local interactive clients
that requires ACP protocol, real-time streaming, permission
round-trips, and multi-connection broadcast — none of which apply to
external messaging channels.

Both paths converge at the **Session layer**, not the Gateway layer:

```
WS client  ──WS /session──►  SessionHandler  ──►  Session.orchestrator
Discord  ──DiscordAdapter──►  SessionManager  ──►  Session.orchestrator
```

---

## Directory Layout

```
src/kernel/kernel/gateways/
├── base.py           ← GatewayAdapter ABC + InboundMessage
├── manager.py        ← GatewayManager (Subsystem)
└── discord/
    ├── __init__.py
    ├── adapter.py    ← DiscordAdapter (Discord Gateway WS + REST API)
    └── gateway.py    ← Discord Gateway WS connection lifecycle
```

Webhook-based platforms (WhatsApp, LINE) also need a FastAPI router:

```
src/kernel/kernel/routes/
└── gateways.py       ← POST /gateways/{adapter_id}/webhook
                         registered by GatewayManager at startup
```

---

## Types (`base.py`)

### InboundMessage

Internal normalization type. Produced by each Adapter's platform-specific
parser; consumed by the Adapter itself. Does not cross the Adapter→Manager
boundary as public API.

```python
@dataclass
class InboundMessage:
    instance_id: str        # "main-discord" — which config entry
    peer_id: str            # platform user identifier
    thread_id: str | None   # channel/thread/group (session isolation key)
    text: str
    attachments: list[Any]  # images, files — platform-specific
    raw: Any                # original platform payload, kept for debugging
```

---

## GatewayAdapter ABC (`base.py`)

Analogous to `Provider` ABC. One subclass per platform type.

```python
class GatewayAdapter(ABC):
    """Communication implementation for one platform type.

    Each instance corresponds to one config entry (one bot account).
    The instance owns its peer→session mapping and is responsible for
    the full message round-trip: receive → normalize → run orchestrator
    → send reply.
    """

    def __init__(
        self,
        instance_id: str,
        config: dict,
        module_table: KernelModuleTable,
    ) -> None: ...

    @abstractmethod
    async def start(self) -> None:
        """Start receiving messages (connect Gateway WS / register webhook)."""

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown."""

    @abstractmethod
    async def send(
        self,
        peer_id: str,
        thread_id: str | None,
        text: str,
    ) -> None:
        """Send a reply back to the user via the platform's outbound API."""
```

**Why `module_table` instead of individual managers?**
Adapter implementations (e.g. `DiscordAdapter`) need access to multiple
subsystems for command execution (`SessionManager`, `LLMManager`, etc.),
matching the pattern in `command-manager.md` where `_execute_for_channel`
already takes `module_table`. Passing `module_table` is simpler and avoids
an ever-growing constructor argument list.

### Internal state (per Adapter instance)

```python
_instance_id: str                                         # stored from __init__
_peer_sessions: dict[tuple[str, str | None], str]         # (peer_id, thread_id) → session_id
_session_locks: dict[tuple[str, str | None], asyncio.Lock]# per-session serialization lock
_pending_permissions: dict[tuple[str, str | None], asyncio.Future[PermissionResponse]]
```

### Message dispatch

Platform event listeners must call `_handle()` as a **fire-and-forget task**,
never directly awaited. Directly awaiting would block the platform's inbound
receive loop while a turn runs (potentially minutes):

```python
# In platform-specific event listener (e.g. Discord on_message):
asyncio.create_task(self._handle(msg))
```

### `_handle()` — lock scope

The per-session lock protects **only session creation** — the race where two
concurrent messages both see `session_id is None` and both call
`create_for_gateway`, producing duplicate sessions.

**The lock must NOT be held during `await run_turn_for_gateway`.**
If it were, a turn blocked on `on_permission` would hold the lock while the
user's "yes/no" reply arrived and tried to acquire the same lock → deadlock.

Turn serialization is handled by the FIFO consumer queue inside
`SessionManager`, not by this lock.

`dict.setdefault()` is atomic within asyncio (no await between check and
set), so lock creation is race-free:

```python
async def _handle(self, msg: InboundMessage) -> None:
    key = (msg.peer_id, msg.thread_id)

    # 1. Permission reply — atomic dict pop, no lock needed.
    #    Must be checked before the lock; otherwise the permission reply task
    #    would deadlock waiting for the lock held by the turn awaiting the reply.
    if key in self._pending_permissions:
        fut = self._pending_permissions.pop(key)
        text = msg.text.strip().lower()
        if text in ("yes", "y", "ok", "allow", "approve"):
            fut.set_result(PermissionResponse(decision=Decision.allow_once))
        else:
            fut.set_result(PermissionResponse(decision=Decision.reject))
        return

    # 2. Session creation — lock only around this critical section.
    session_manager = self._module_table.get(SessionManager)
    lock = self._session_locks.setdefault(key, asyncio.Lock())
    async with lock:
        session_id = self._peer_sessions.get(key)
        if session_id is None:
            session_id = await session_manager.create_for_gateway(
                instance_id=self._instance_id,
                peer_id=msg.peer_id,
            )
            self._peer_sessions[key] = session_id
    # Lock released here. Turn runs outside the lock.

    # 3. Turn execution — outside lock so permission replies can come through.
    try:
        if msg.text.startswith("/"):
            name, _, args = msg.text[1:].partition(" ")
            cmd_manager = self._module_table.get(CommandManager)
            cmd = cmd_manager.lookup(name)
            if cmd is None:
                await self.send(msg.peer_id, msg.thread_id, f"Unknown command: /{name}")
                return
            reply = await _execute_for_channel(cmd, args, session_id, self._module_table)
            await self.send(msg.peer_id, msg.thread_id, reply)
            return

        on_perm = self._make_permission_callback(msg.peer_id, msg.thread_id)
        reply = await session_manager.run_turn_for_gateway(session_id, msg.text, on_perm)
        if reply:   # skip send if turn produced no text (tool-only turns)
            await self.send(msg.peer_id, msg.thread_id, reply)

    except Exception:
        logger.exception("GatewayAdapter._handle failed for %s", self._instance_id)
        try:
            await self.send(msg.peer_id, msg.thread_id, "An error occurred. Please try again.")
        except Exception:
            pass
```

`_execute_for_channel` is a private helper inside each Adapter subclass,
not part of `CommandManager`. It maps `cmd.acp_method` to a direct kernel
call and returns plain text. See `command-manager.md — DiscordBackend 的职责`.

`_peer_sessions` is persisted to disk so session continuity survives kernel
restarts. Storage path: `~/.deepcli/gateways/<instance_id>/peer_sessions.json`.

---

## SessionManager Internal API

GatewayAdapter has no WebSocket connection, so it cannot call the public
`SessionManager` methods (`new`, `prompt`) which require a `HandlerContext`.
Two internal methods need to be added to `SessionManager`:

### `create_for_gateway(instance_id, peer_id) -> str`

Creates a new session without a WebSocket connection. Returns `session_id`.
Internally equivalent to `new()` but skips connection binding:

```python
async def create_for_gateway(
    self,
    instance_id: str,   # for metadata / title ("discord:main-discord")
    peer_id: str,       # platform user, for metadata only
) -> str:
    # 1. uuid4 session_id
    # 2. cwd = Path.home() — gateway sessions have no project directory
    # 3. construct Session + Orchestrator
    # 4. write session_created through SessionStore
    # 5. update the sessions row/index state
    # 6. register in self._sessions
    # 7. ensure consumer task is running (see note below)
    # 8. return session_id
    # NOTE: no ctx.conn added — session starts with no connections
```

**`cwd` for gateway sessions**: set to `Path.home()`. Gateway users have no
meaningful working directory; home dir is used as a safe fallback so that
file-system tools have a sane starting point if invoked.

### `run_turn_for_gateway(session_id, text, on_permission) -> str`

Submits a prompt through the **normal turn queue** (same FIFO consumer loop
used by WS clients) and blocks until the turn completes, returning the
assistant's final text reply.

```python
async def run_turn_for_gateway(
    self,
    session_id: str,
    text: str,
    on_permission: PermissionCallback,
) -> str:
    session = self._get_or_load_session(session_id)
    text_collector: asyncio.Future[str] = asyncio.Future()
    queued = QueuedTurn(
        request_id=_new_request_id(),
        params=PromptRequest(sessionId=session_id, content=[...]),
        queued_at=datetime.now(UTC),
        response_future=asyncio.Future(),
        text_collector=text_collector,
        on_permission=on_permission,
    )
    session.queue.put_nowait(queued)   # asyncio.Queue, not deque — wakes consumer loop
    await queued.response_future       # wait for turn to finish
    return await text_collector        # already resolved by _run_turn_internal
```

`on_permission` is passed in by the Adapter, which constructs a closure
over `peer_id`/`thread_id` — see `_make_permission_callback` below.

`text_collector` and `on_permission` are new optional fields on `QueuedTurn`
(see session.md). `_run_turn_internal` reads `TextDelta` events into the
collector and calls `on_permission` when the Orchestrator requests it.

**Consumer loop wakeup**: `session.queue` must be `asyncio.Queue` (not
`deque`) so that `put_nowait()` from `run_turn_for_gateway` immediately wakes
the consumer task's `await queue.get()`. The session.md design uses `deque` +
`_next_queued()` polling today — this is a dependency that session.md must
resolve when implementing this method. See session.md Gateway Internal API
section for the full change.

**Why not bypass the consumer loop?**
The consumer loop is where SessionStore persistence, turn serialization, and
WS broadcasting happen. Bypassing it would cause:
- Concurrent WS client + Gateway turns racing on the same Orchestrator
- Gateway-originated turns not written to SessionStore (invisible to `session show`)

Using `run_turn_for_gateway` ensures Gateway turns are first-class citizens
in the session history.

---

## Permission Handling for External Platforms

Orchestrator requires `on_permission: PermissionCallback` for every
`query()` call. Rather than a hardcoded auto-approve policy, GatewayAdapter
sends the permission request to the platform user as a message and waits
for their **yes / no** reply — giving users the same approval flow they'd
have in the WS client, over the messaging platform.

### `_make_permission_callback`

Each Adapter implements this method to build an `on_permission` closure
bound to a specific channel:

```python
_PERMISSION_TIMEOUT_S: float = 300.0   # 5 minutes — class-level default

def _make_permission_callback(
    self,
    peer_id: str,
    thread_id: str | None,
) -> PermissionCallback:
    async def on_permission(req: PermissionRequest) -> PermissionResponse:
        # 1. Send permission request message to the user
        await self.send(
            peer_id, thread_id,
            f"Permission required: `{req.tool_name}`\n"
            f"{req.input_summary}\n"
            f"Reply **yes** to allow or **no** to deny "
            f"(timeout: {int(self._PERMISSION_TIMEOUT_S)}s).",
        )
        # 2. Register a Future for the user's reply
        key = (peer_id, thread_id)
        fut: asyncio.Future[PermissionResponse] = asyncio.get_running_loop().create_future()
        self._pending_permissions[key] = fut
        # 3. Block until _handle() resolves the future, or timeout → reject
        try:
            return await asyncio.wait_for(fut, timeout=self._PERMISSION_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending_permissions.pop(key, None)
            await self.send(peer_id, thread_id, "Permission request timed out — tool call rejected.")
            return PermissionResponse(decision=Decision.reject)

    return on_permission
```

### Reply parsing (in `_handle`)

When `_pending_permissions` contains an entry for the incoming channel,
`_handle` intercepts the message before normal processing:

```python
if key in self._pending_permissions:
    fut = self._pending_permissions.pop(key)
    text = msg.text.strip().lower()
    if text in ("yes", "y", "ok", "allow", "approve"):
        fut.set_result(PermissionResponse(decision=Decision.allow_once))
    else:
        # Any other reply → reject (includes "no", "n", gibberish)
        fut.set_result(PermissionResponse(decision=Decision.reject))
    return
```

### Cleanup on `stop()`

The `stop()` method must cancel any pending permission futures to avoid
blocking the consumer loop during shutdown:

```python
async def stop(self) -> None:
    # Cancel all pending permission futures before stopping
    for fut in self._pending_permissions.values():
        if not fut.done():
            fut.set_result(PermissionResponse(decision=Decision.reject))
    self._pending_permissions.clear()
    # ... platform-specific teardown (close WS / deregister webhook) ...
```

### Why this approach

- Users on Discord/Telegram get the same tool-approval experience as WS client users
- No hardcoded allow/deny lists to maintain
- The Orchestrator's `on_permission` contract is preserved exactly — it
  awaits a `PermissionResponse` regardless of how the Adapter resolves it
- `_pending_permissions` is per-channel `(peer_id, thread_id)`, so multiple
  simultaneous conversations on the same bot don't interfere
- Timeout prevents turns from blocking indefinitely if the user walks away

---

## Session Isolation Scope

`_peer_sessions` uses `(peer_id, thread_id)` as the session key. This gives
each unique `(user, channel/thread)` pair its own independent session —
equivalent to OpenClaw's `"per-channel-peer"` DM scope.

**Implications:**

| Scenario | Behavior |
|----------|----------|
| Same user, same channel | Continuous conversation — same session |
| Same user, different channels | Two separate sessions (different thread_id) |
| Different users, same channel | Two separate sessions (different peer_id) |
| Group chat | Each user in the group gets their own session |

**Group chats**: each sender is a distinct `peer_id`, so each user in a group
channel gets their own independent conversation with the bot. The bot does not
maintain a shared group context. This is intentional for MVP — shared group
sessions would require a different key strategy (`(channel_id,)` without
`peer_id`) and are deferred.

---

## DiscordAdapter

```
start():
  Fetch bot user id via GET /users/@me — needed for self-message filtering.
  Store as self._bot_user_id.
  Connect to Discord Gateway WebSocket (outbound long-lived connection).
  Register MessageCreate listener:
    if event.author.id == self._bot_user_id: return   ← filter own messages first
    normalize → asyncio.create_task(self._handle(InboundMessage))

stop():
  Reject all pending permission futures (see Permission Handling § Cleanup on stop()).
  Send close frame to Gateway WS.

send():
  Chunk text into ≤2000-character segments (Discord hard limit).
  For each segment:
    POST https://discord.com/api/v10/channels/{thread_id}/messages
    Authorization: Bot {token}
    Body: { "content": segment }
```

Platform details:
- **Inbound**: Discord Gateway WebSocket (bot connects outbound to Discord)
- **Outbound**: Discord REST API v10
- **Self-message filter**: bots receive their own sent messages as events; filter on `author.id == _bot_user_id` before `create_task`, or send→handle→send infinite loop occurs
- **Fire-and-forget dispatch**: `asyncio.create_task` is mandatory — direct `await _handle()` would block the Gateway WS receive loop for the duration of a full LLM turn
- **Message size limit**: 2000 characters per message — `send()` must chunk long replies
- **No FastAPI route needed** — DiscordAdapter manages its own connection

---

## GatewayManager (`manager.py`)

Subsystem #11 in startup order (after CommandManager).

```python
class GatewayManager(Subsystem):
    async def startup(self) -> None:
        for instance_id, cfg in self._config.gateways.items():
            try:
                adapter = _create_adapter(
                    adapter_type=cfg["type"],    # "discord", "telegram", ...
                    instance_id=instance_id,
                    config=cfg,
                    module_table=self._module_table,
                )
                await adapter.start()
                self._adapters[instance_id] = adapter
                logger.info("Gateway adapter started: %s (%s)", instance_id, cfg["type"])
            except Exception:
                # One adapter failure does not prevent others from starting.
                logger.exception("Failed to start gateway adapter %s — skipping", instance_id)

    async def shutdown(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Error stopping gateway adapter %s", adapter._instance_id)
```

`_create_adapter` is a registry lookup: `"discord"` → `DiscordAdapter`, etc.
GatewayManager contains no message routing logic — that lives in each Adapter.

---

## Config Shape

Follows the same pattern as `models:` in the LLM config:

```yaml
gateways:
  main-discord:
    type: discord
    token: "Bot ${secret:discord_token}"
    allow_guilds: ["123456789"]

  saki-telegram:
    type: telegram
    token: "${secret:telegram_token}"

  support-whatsapp:
    type: whatsapp
    account_sid: "${secret:twilio_sid}"
    auth_token: "${secret:twilio_auth}"
    from_number: "+14155552671"
```

Credentials reference the credential store (`${secret:name}` syntax, Phase 5.5.2).

---

## Analogy Table: LLM Layer vs Gateway Layer

| LLM Layer | Gateway Layer |
|-----------|---------------|
| `Provider` ABC | `GatewayAdapter` ABC |
| `AnthropicProvider` | `DiscordAdapter` |
| `OpenAICompatibleProvider` | `TelegramAdapter` |
| `models.claude-opus` (config entry) | `gateways.main-discord` (config entry) |
| `model_id: claude-opus-4-6` | `token: "Bot xxx"` |
| `LLMProviderManager` | `GatewayManager` |

---

## Startup Order Update

```
1.  Config
2.  Auth
3.  Provider (LLM)
4.  Tools
5.  Skills
6.  Hooks
7.  MCP
8.  Memory
9.  Session
10. Commands      ← new
11. Gateways      ← new
```

Gateways start last because they depend on both Session (to create sessions)
and Commands (to dispatch slash commands).

---

## Boundary Rules

- `GatewayManager` — lifecycle only; no message routing, no business logic; individual adapter failures are caught and logged, not fatal
- `GatewayAdapter` subclass — owns the full round-trip for its platform
- `GatewayAdapter` — holds its own `peer→session` mapping (no separate Bridge class)
- `GatewayAdapter` constructor — takes `module_table`, not individual managers; stores `instance_id` as `self._instance_id`
- `GatewayAdapter.stop()` — must reject all pending permission futures before teardown
- `GatewayAdapter._handle()` — must be dispatched via `asyncio.create_task()`, never directly awaited; per-session lock covers only session creation, never the running turn
- Platform event listeners — must filter self-messages (bot's own `author.id`) before dispatching
- `InboundMessage` — internal to each Adapter; not a public kernel type
- `_execute_for_channel` — private helper inside each Adapter; belongs to the Adapter, not CommandManager
- `_make_permission_callback` — defined on base `GatewayAdapter`; subclasses inherit it; uses `asyncio.get_running_loop()`
- `run_turn_for_gateway` / `create_for_gateway` — internal SessionManager methods; not part of ACP/WS public API
- `_run_turn_internal` — must set `text_collector` before `response_future` when both are present
- `session.queue` — must be `asyncio.Queue` (not `deque`) so Gateway enqueues wake the consumer loop
- Session isolation scope — `(peer_id, thread_id)` = per-channel-peer; group sessions are deferred
- WS `/session` — unrelated to GatewayManager; kept entirely separate

---

## Open Issues

The issues below belong to the superseded GatewayManager design and must not be
used as current implementation tasks. Re-evaluate them under the Access Agent
Platform Adapter design before coding.

| Issue | Decision |
|-------|---------|
| `_peer_sessions` persistence format | Superseded by AgentDefinition platform bindings and Access Agent reply sinks. |
| `session.queue` type | session.md currently uses `deque` + `_next_queued`. Must migrate to `asyncio.Queue` before implementing `run_turn_for_gateway`. This is a session.md implementation detail but gateway depends on it. |
