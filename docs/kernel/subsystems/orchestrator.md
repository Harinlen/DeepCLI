# Orchestrator

## Purpose

Orchestrator 是对话引擎核心。它接收用户消息，驱动"LLM 推理 → 工具执行
→ 结果喂回 → 再次推理"的主循环，直到 LLM 不再调用工具为止，然后以
`StopReason` 结束。

**Session 和 Orchestrator 的边界**：

| Session | Orchestrator |
|---|---|
| 持久化 conversation 到 JSONL | 在内存里跑 conversation 主循环 |
| 多连接广播 | 不知道 WebSocket 的存在 |
| In-flight task 跟踪（cancel） | 接收 `CancelledError` 自行清理 |
| `SessionHandler` 7 个方法的业务实现 | 接受 prompt 输入、产事件输出 |
| Session lifecycle（create / load / destroy）| 一个 Session 一个实例，跟随 Session 生死 |
| 不知道 Provider / Tools 怎么工作 | 全部依赖 Provider / Tools / Memory / Hooks |

Session 拿到 Orchestrator 后调的方法全部定义在 `Orchestrator` Protocol 里。

---

## 外部接口

### `Orchestrator` Protocol（完整）

```python
class Orchestrator(Protocol):

    # ── Core ─────────────────────────────────────────────────────────────
    def query(
        self,
        prompt: list[ContentBlock],
        *,
        on_permission: PermissionCallback,
        token_budget: int | None = None,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        """
        Run one prompt turn.

        Drives the LLM ↔ tool loop until the model stops calling tools,
        yielding events as they occur.

        Python async generators cannot return a value, so StopReason is
        exposed via the ``stop_reason`` property after the loop ends.

        `on_permission` is called (and awaited) each time a tool
        requires user approval before execution.

        Cancellation: cancel the enclosing asyncio Task.  The generator
        catches CancelledError, yields a final CancelledEvent, then
        returns — giving callers a clean event boundary.
        """

    async def close(self) -> None:
        """
        Tear down the orchestrator.  Cancel any in-progress query,
        release provider connections.  Called when the Session is destroyed.
        """

    # ── State mutation（同步，fire-and-forget）────────────────────────────
    def set_plan_mode(self, enabled: bool) -> None:
        """
        Toggle plan mode.  Takes effect at the start of the next LLM call
        within the current or future query() — not mid-stream.

        Does not emit any event.  Session is responsible for broadcasting
        ModeChanged to connected clients after calling this.
        """

    def set_config(self, patch: OrchestratorConfigPatch) -> None:
        """
        Apply a partial config update (model, provider, temperature, …).
        Takes effect at the start of the next LLM call — not mid-stream.

        Does not emit any event.  Session is responsible for broadcasting
        ConfigOptionChanged to connected clients after calling this.
        """

    # ── State reads ───────────────────────────────────────────────────────
    @property
    def plan_mode(self) -> bool:
        """Current plan mode state.  Session reads this to build ACP responses."""

    @property
    def stop_reason(self) -> StopReason:
        """
        The StopReason from the most recent query() call.
        Only meaningful after the async for loop has finished.
        Defaults to end_turn before any query is made.

        Note: Python async generators cannot use `return value`, so
        StopReason is communicated via this property rather than as a
        generator return value.
        """

    @property
    def config(self) -> OrchestratorConfig:
        """
        Current user-visible config snapshot.  Session reads this to build
        ACP responses and to populate ConfigOptionChanged broadcasts.
        """
```

### 支撑类型

```python
@dataclass(frozen=True)
class OrchestratorConfig:
    """
    User-visible config.  Returned by Orchestrator.config property.
    Lives in kernel/orchestrator/__init__.py.
    """
    provider: str
    """LLM provider identifier, e.g. "anthropic".  Informational only —
    routing is done by LLMManager based on the model key."""

    model: str
    """Active model key/alias, e.g. "claude-opus" or "opus".
    LLMManager resolves this to the actual API model ID."""

    temperature: float | None = None   # None = use model config default

    streaming_tools: bool = False
    """When True, executor.add_tool() is called during the LLM stream
    (safe tools start immediately).  When False, tools are queued and
    dispatched after the stream ends.  Both paths use the same
    ToolExecutor interface; only timing changes."""

@dataclass
class OrchestratorConfigPatch:
    """Partial update applied by set_config().  None = leave unchanged."""
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    streaming_tools: bool | None = None
```

> `compaction_threshold` 是引擎内部参数，只在构造时设定，
> 不暴露给 Session，不出现在 `OrchestratorConfig` 里。
> `max_turns` 由调用方通过 `query(max_turns=...)` 控制，`0` = 不限制（与 CC 对齐）。
> Sub-agent 默认 200 turns。

### 设计依据（参照 Claude Code）

Claude Code 里 plan mode 和 model 都存在 `AppState`（Zustand store）里，
query loop 通过 `getAppState()` 闭包在**每次 LLM 调用前**读取最新值，
外部改 AppState 后 loop 的下一个 iteration 自动生效。没有"config changed"
事件从 `query()` 里出来——UI 直接读 AppState，Session 直接广播。

我们的等价设计：
- `set_plan_mode` / `set_config` = `setAppState()`，**同步、无返回值、无回调**
- 主循环在每次调用 LLM 前读 `self._plan_guard.active` 和 `self._config`（等价于 `getAppState()`），变更自动对下一个 iteration 生效
- Orchestrator 不发"我的 config 变了"事件；Session 在调完变更方法后自行广播

### 为什么 `set_plan_mode` / `set_config` 在 Protocol 上

Session 只持有 `Orchestrator` Protocol 引用，不知道 `StandardOrchestrator`
类。若把这两个方法放在具体类上，Session 就要 downcast，破坏抽象。
Protocol 上定义确保 Session 始终只依赖接口。

### 为什么不传 `HandlerContext` 进 `query()`

Session 层的 `HandlerContext` 含有 `ProtocolAPI`（WebSocket 句柄）。
Orchestrator 不知道 WebSocket 的存在——它只产事件，Session 负责把事件
广播给所有连接。让 `HandlerContext` 进 `query()` 会破坏这层隔离。

需要跨越这条边界的唯一"往回"通道是 **permission 请求**，由专门的
`on_permission: PermissionCallback` 参数承载（见 [Permission](#permission)
小节），而不是把整个 `HandlerContext` 带进来。

---

## 内部架构

Orchestrator 由五个内部组件构成，各自职责严格隔离：

```
StandardOrchestrator
  ├── ConversationHistory   消息历史 + token 计数
  ├── PromptBuilder         system prompt 组装（memory + skills + context）
  ├── ToolExecutor          工具执行流水线（permission → run → events）
  ├── Compactor             context 压缩（历史过长时触发）
  └── PlanModeGuard         plan mode 状态 + 工具调用限制
```

### 文件布局

```
kernel/orchestrator/
  __init__.py          # 导出 Orchestrator Protocol + 所有公共类型
  orchestrator.py      # StandardOrchestrator（实现 Orchestrator Protocol）
  history.py           # ConversationHistory
  prompt_builder.py    # PromptBuilder
  tool_executor.py     # ToolExecutor
  compactor.py         # Compactor
  events.py            # OrchestratorEvent union + 所有事件 dataclass
  types.py             # OrchestratorDeps、窄 Protocol 接口、StopReason、
                       # PermissionCallback、OrchestratorConfig/Patch
```

> `PlanModeGuard` 是 `StandardOrchestrator` 内部的一个简单 bool 状态
> （`self._plan_mode: bool`），不需要独立文件。

---

## 主循环（Main Loop）

参照 Claude Code `queryLoop()` 的 6 步结构（见
[claude-code-query-loop.md](../../reference/claude-code-query-loop.md)），
`_run_query()` 按相同顺序编排各阶段。

### 流程总览

```
┌─────────────────────────────────────────────────────────────┐
│  STEP 0: SETUP (before loop)                                │
│    Drain reminders, fire user_prompt_submit hook,           │
│    append user message to history                           │
│                                                             │
│  while True:                                                │
│    STEP 1: PREPARE — compress / trim context (5 layers)     │
│    STEP 2: BUILD PROMPT — rebuild system prompt each turn   │
│    STEP 3: STREAM LLM — call model, distribute chunks       │
│    STEP 4: COMMIT + BRANCH — save to history, check tools   │
│    STEP 5: STOP (no tool_use) — error recovery, stop hooks  │
│    STEP 6: TOOLS — execute, post-process, loop back         │
└─────────────────────────────────────────────────────────────┘
```

### 实现状态（对照 Claude Code query.ts）

| 步骤 | 实现 | 剩余 TODO |
|------|------|-----------|
| **0. Setup** | ✅ drain reminders + `user_prompt_submit` hook（block/rewrite）+ append | — |
| **1. Prepare** | ✅ snip (1b) + microcompact (1c) + autocompact (1e) | context collapse (1d) — CC 也未实装（仅预埋 interface） |
| **2. Build prompt** | ✅ 每轮 rebuild | — |
| **3. Stream LLM** | ✅ per-turn ToolExecutor; `streaming_tools=True` 边 stream 边 `add_tool()`; `POST_SAMPLING` hook; abort check ① (`sleep(0)` checkpoint) | — |
| **4. Commit + branch** | ✅ append assistant + branch on tool_calls | — |
| **5. Stop** | ✅ PromptTooLongError reactive compact; MediaSizeError strip+compact; max_output_tokens escalation (8k→64k, ×3 retry); STOP hook; token budget check | — |
| **6. Tools** | ✅ 并发分批 (`partition_tool_calls` + Queue merge + Semaphore + Lock); abort check ② | ~~Haiku summary~~ (WONTFIX), attachments |
| **Cancel** | ✅ `CancelledError` → synthetic `ToolResultContent` for orphan tool_use → `CancelledEvent` → `StopReason.cancelled` | — |

### Transient stream retry

Provider `StreamError(code="transient_transport")` 表示 HTTP/SSE 传输层断流
（例如 incomplete chunked read、timeout、SSE 没收到 `[DONE]`）。Orchestrator
在同一个 logical turn 内最多重试 2 次，并 rewind 当前 loop iteration：

- 重试不追加新的 user message，不写 assistant history，不执行工具。
- 只有尚未向客户端流出 text/thought/tool output 时自动重试，避免 CLI 出现重复
  partial assistant 内容。
- 如果已经流出 partial output，或重试耗尽，则 yield `QueryError` 并以
  `StopReason.error` 结束；Session 会把错误文本和 `stopReason="error"` 传给客户端。

---

## 内部组件详解

### ConversationHistory

管理 LLM 看到的完整消息列表，以及 token 计数。

```python
class ConversationHistory:
    messages: list[Message]     # user / assistant / tool_result 交替
    token_count: int            # 估算值，用于触发 compaction

    def append_user(self, content: list[ContentBlock]) -> None: ...
    def append_assistant(
        self,
        text: str,
        thoughts: list[ThoughtChunk],   # Anthropic extended thinking；其他 provider 传 []
        tool_calls: list[ToolUseChunk],
    ) -> None:
        # thoughts → list[ThinkingContent] 存入 AssistantMessage.content（必须进 history）
        # tool_calls → list[ToolUseContent] 存入 AssistantMessage.content
        ...
    def append_tool_results(self, results: list[ToolResultContent]) -> None: ...
    def update_token_count(self, input_tokens: int, output_tokens: int) -> None:
        # 用 provider 返回的精确值覆盖估算，供 compaction 阈值判断
        ...
    def pending_tool_use_ids(self) -> list[str]:
        # 返回最后一条 assistant message 中没有匹配 tool_result 的 tool_use IDs。
        # 用于 cancel handler 合成 synthetic tool_results，防止 orphan tool_use。
        ...
    def replace_with_compacted(self, summary: str, boundary: int) -> None: ...
    # boundary 是 find_compaction_boundary() 返回的 index。
    # boundary 以前的消息替换为一条 summary UserMessage，boundary 以后的保留。
```

**Token 计数策略**：初始估算用字符数（1 token ≈ 4 chars），每次 LLM 调用
结束后用 `UsageChunk` 里的精确值覆盖（`update_token_count()`）。这样 compaction
阈值判断使用的是 provider 的准确数字，而不是粗估。

**类型命名约定**：
- `ToolUseChunk`（来自 provider stream）和 `ToolUseContent`（存在 `AssistantMessage` 里）
  字段完全相同（`id, name, input`），可以是同一个类型或别名。实现时以 `ToolUseChunk`
  为权威类型，`ToolUseContent` = `ToolUseChunk`（别名）即可，不需要两套类。
- 同理 `ToolResultContent` 是唯一类型，主循环里的 `tool_results` 直接用它，
  不再有 `ToolResultBlock` 这个名字。

**Session 如何重建 history**：`session/load` 时 Session 从 JSONL 重放事件
序列，把 user message / assistant message / tool result 按顺序重建成
`list[Message]`，作为 `initial_history` 传给 Orchestrator 构造函数。
Orchestrator 不关心 JSONL 格式，只接受已经重建好的 `list[Message]`。

---

### PromptBuilder

**每次循环迭代**（STEP 2）在 LLM 调用之前重建 system prompt。

```
system prompt = [
    kernel_base_prompt,          # 内置：DeepCLI 身份、能力说明
    active_skills_content,       # SkillManager: 激活的 skill body 拼接
    memory_injections,           # MemoryManager: 相关 memory 条目（top-N）
    user_context,                # cwd、OS、日期时间、项目信息
    plan_mode_instructions,      # PlanModeGuard: plan mode 时附加限制说明
]
```

**为什么每次迭代重建而不是循环外构建一次**：Claude Code 在
`queryLoop` 的 `while(true)` 内每轮重建 `fullSystemPrompt`（query.ts:449），
因为 plan_mode 和 model 可能在工具执行期间被切换（通过 `set_plan_mode` /
`set_config`），下一轮 LLM 需要看到更新后的 system prompt。
重建成本低（无 LLM 调用，纯文本拼接），不值得维护缓存的失效逻辑。

---

### ToolExecutor

处理一批 `tool_use` block 的完整流水线。

#### 工具并发模型

并发由**每个工具自己声明的 `is_concurrency_safe: bool`** 决定，不是按
ToolKind 大分类。每个工具在注册时声明自己是否可以和其他工具并发执行：

- `is_concurrency_safe = True`：可与其他 safe 工具并发（`asyncio.gather`）
- `is_concurrency_safe = False`：独占执行，前后都不能有其他工具并行

`partition_tool_calls()` 把同一批 tool_use blocks 按**连续 safe 分组**拆开：

```
tool_calls = [read_A(safe), read_B(safe), edit_C(unsafe), read_D(safe), bash_E(unsafe)]
                 ↓  partition_tool_calls()
batches = [
    {safe: True,  calls: [read_A, read_B]},   # 并发批
    {safe: False, calls: [edit_C]},            # 独占批
    {safe: True,  calls: [read_D]},            # 并发批（但只有一个）
    {safe: False, calls: [bash_E]},            # 独占批
]
                 ↓  依次执行各批
1. asyncio.gather(read_A, read_B)   # 并发
2. edit_C                           # 独占串行
3. read_D                           # 独占串行（safe 但前面跟着 unsafe，单独一批）
4. bash_E                           # 独占串行
```

原始顺序在批间保持。最大并发数（同一 safe 批内）由配置控制，默认 10。

ToolKind（`read` / `edit` / `execute` / …）仍然存在，但它的用途是 **UI
显示**（图标、颜色）和 **permission 风险评估**，不决定并发策略。工具的
`is_concurrency_safe` 需要作者在注册时明确声明。

> **参照**：Claude Code 在 `toolOrchestration.ts` 的 `partitionToolCalls()`
> 里按 `isConcurrencySafe()` 分批，最大并发数由环境变量 `MAX_TOOL_CONCURRENCY`
> 控制（默认 10）。

#### 工具执行时机（`streaming_tools` config flag）

ToolExecutor 支持两种执行时机，由 `OrchestratorConfig.streaming_tools`
控制：

**`streaming_tools=False`（默认）**：等 LLM stream 全部结束后，将所有
tool_use block 一次性 `add_tool()` + `finalize_stream()`，再通过
`results()` 消费。**batch 内** safe tools 仍然**并行执行**。

**`streaming_tools=True`**：LLM stream 中每收到一个完整 `ToolUseChunk`
即调用 `executor.add_tool()`。Safe tools 立即启动执行（LLM 还在继续
streaming 后续 block），non-safe tools 队列等待 `finalize_stream()`。
将 LLM 推理时间和工具执行时间**重叠**，减少等待时长。

#### Per-tool 7-step 流水线（`_run_one`）

每个 tool call 经过 7 步，无论串行还是并行 batch：

1. **validate_input** — 便宜早拒绝，在授权之前
2. **authorize** — `ToolAuthorizer` 仲裁 allow/deny/ask；ask 时通过
   `on_permission` 回调（`asyncio.Lock` 序列化，并发 batch 不会同时弹 UI）
3. **pre_tool_use hook** — 可 block 或 rewrite `tool_input`
4. **ToolCallStart event** — 通知客户端
5. **tool.call()** — async generator，yield `ToolCallProgress*` + `ToolCallResult`
6. **post_tool_use hook**（成功）/ **post_tool_failure hook**（异常）
6a. **context_modifier** — 如果 `ToolCallResult.context_modifier` 非 None，
    调用 modifier 生成新 ToolContext，通过 `on_context_changed` 回调通知
    Orchestrator 更新 `_cwd` + 触发 `git.invalidate_context()`（Phase 15）
7. **emit ToolCallResult + ToolResultContent**（经 result budget 截断）

Tool 接口详见 [tools.md](tools.md) § 3。

#### `ToolExecutor` 公共接口

```python
class ToolExecutor:
    def __init__(self, deps, *, session_id, cwd, agent_depth=0,
                 on_context_changed=None,  # Phase 15: worktree cwd switch
                 streaming=False, max_concurrency=10): ...
    def add_tool(self, tool_use: ToolUseContent) -> None: ...
    def finalize_stream(self) -> None: ...
    async def results(self, on_permission, plan_mode) -> AsyncGenerator[...]: ...
    def discard(self) -> None: ...
    # Legacy wrapper:
    async def run(self, tool_calls, on_permission, plan_mode) -> AsyncGenerator[...]: ...
```

`results()` 内部：`partition_tool_calls()` 分批 → 单 tool batch
直接跑 `_run_one` → 多 tool safe batch 用 `asyncio.create_task` +
`asyncio.Queue` per-tool + `_merge_queues` 按完成顺序 yield →
`asyncio.Semaphore(max_concurrency)` 限流。

#### Permission 序列化

`on_permission` 的 `await` 被 executor 级 `asyncio.Lock` 保护。并发
batch 中多个 tool 同时需要 ask 时，permission prompt 逐个出现。
Lock 只保护 `on_permission` 那一个 await，不阻塞 authorization 其余
部分。

`allow_always` 决策由 `ToolAuthorizer.grant()` 记录到
`SessionGrantCache`，后续同类调用直接 allow 不再弹 UI。

---

### Compactor

当 `history.token_count > config.compaction_threshold`（默认为 provider
上下文窗口的 80%）时触发。

```python
class Compactor:
    async def compact(self, history: ConversationHistory) -> None:
        # 1. 找到 compaction boundary（最近 N 条保留，其余压缩）
        boundary = history.find_compaction_boundary(keep_recent=config.keep_recent_turns)

        # 2. 把 boundary 以前的内容发给 LLM 做摘要
        summary = await self._provider.summarize(
            messages=history.messages[:boundary],
            instruction="Summarize this conversation concisely, preserving key facts, decisions, and context needed to continue.",
        )

        # 3. 替换历史
        history.replace_with_compacted(
            summary=summary,
            boundary=boundary,
        )
        # 替换后 history.messages = [SystemMessage("Prior conversation summary:\n" + summary), ...recent]
```

**Compaction boundary 的选择**：找最近的 user message 边界，保证
compaction 不把一个 assistant+tool_result 对切断。始终至少保留最近
`keep_recent_turns`（默认 5）个完整 turn。

> **参照**：Claude Code 的 `autocompact()` 在每个 iteration **开始时**、
> LLM 调用之前检查 token count 并触发压缩。压缩在 LLM 调用前完成，确保
> LLM 看到的 history 始终是压缩后的版本。我们采用相同时机。

---

### PlanModeGuard

维护 plan mode 开/关状态。

```python
class PlanModeGuard:
    active: bool = False

    def enter(self) -> None:
        self.active = True
    def exit(self) -> None:
        self.active = False
```

Session 层通过 `Orchestrator` Protocol 上的 `set_plan_mode()` 更新状态：

```python
# SessionManager 实现
async def set_mode(self, ctx, params: SetSessionModeRequest) -> SetSessionModeResponse:
    session = self._get_session(ctx.conn.bound_session_id)
    session.orchestrator.set_plan_mode(params.modeId == "plan")
    # Session 负责广播 ModeChanged 给所有连接
    ...
```

Plan mode 效果：
- ToolExecutor 拒绝执行所有非 `think` 类工具
- PromptBuilder 在 system prompt 里追加 plan mode 限制说明

---

## 事件类型（`OrchestratorEvent`）

`query()` 产出的所有事件构成一个 tagged union：

```python
type OrchestratorEvent = (
    TextDelta
    | ThoughtDelta
    | ToolCallStart
    | ToolCallProgress
    | ToolCallResult
    | ToolCallError
    | ToolCallDiff
    | ToolCallLocations
    | PlanUpdate
    | ModeChanged
    | ConfigOptionChanged
    | SessionInfoChanged
    | AvailableCommandsChanged
    | SubAgentStart
    | SubAgentEnd
    | CompactionEvent
    | QueryError          # provider StreamError 透传（限流、API 故障）
    | CancelledEvent
)
```

### 事件定义

```python
@dataclass(frozen=True)
class TextDelta:
    content: str                     # 流式文本 chunk

@dataclass(frozen=True)
class ThoughtDelta:
    content: str                     # reasoning / extended thinking chunk

@dataclass(frozen=True)
class ToolCallStart:
    id: str                          # tool_use_id，全局唯一
    title: str                       # 显示名（"Read file"）
    kind: ToolKind                   # read | edit | delete | move | search | execute | think | fetch | other
    raw_input: str | None = None     # 原始 JSON input，调试用

@dataclass(frozen=True)
class ToolCallProgress:
    id: str                          # 匹配 ToolCallStart.id
    content: list[ContentBlock]      # 进度消息（部分输出）

@dataclass(frozen=True)
class ToolCallResult:
    id: str
    content: list[ContentBlock]      # 工具最终输出

@dataclass(frozen=True)
class ToolCallError:
    id: str
    error: str                       # 错误信息（已格式化，可直接给 LLM 看）

@dataclass(frozen=True)
class ToolCallDiff:
    id: str
    path: str                        # 文件路径
    old_text: str | None             # None = 新建文件
    new_text: str

@dataclass(frozen=True)
class ToolCallLocations:
    id: str
    locations: list[FileLocation]    # "follow the agent" 跳转目标

@dataclass(frozen=True)
class PlanUpdate:
    entries: list[PlanEntry]         # 完整 plan 状态（非 diff）

@dataclass(frozen=True)
class ModeChanged:
    mode_id: str

@dataclass(frozen=True)
class ConfigOptionChanged:
    options: dict[str, Any]          # 完整 config state（非 diff）

@dataclass(frozen=True)
class SessionInfoChanged:
    title: str | None = None         # 只含变动字段（partial update）

@dataclass(frozen=True)
class AvailableCommandsChanged:
    commands: list[AvailableCommand]

@dataclass(frozen=True)
class SubAgentStart:
    agent_id: str                    # 与 session aux 目录里的 agent-<id> 对应
    description: str
    agent_type: str                  # "Explore" | "general-purpose" | ...
    spawned_by_tool_id: str          # 触发 spawn 的 AgentTool call id

@dataclass(frozen=True)
class SubAgentEnd:
    agent_id: str
    stop_reason: StopReason
    input_tokens: int = 0
    output_tokens: int = 0
    tool_use_count: int = 0
    duration_ms: int = 0

@dataclass(frozen=True)
class CompactionEvent:
    tokens_before: int
    tokens_after: int

@dataclass(frozen=True)
class QueryError:
    message: str                     # 来自 provider StreamError，可给用户看
    code: str | None = None          # provider 错误码（"rate_limit_error" 等）
    # QueryError 之后 query() return StopReason.error，stream 结束。
    # 与 ToolCallError 区别：QueryError 是 provider 层故障（非工具执行失败）。

@dataclass(frozen=True)
class CancelledEvent:
    pass                             # 取消时的最后一个事件，标记 stream 结束
```

### 与 ACP `session/update` 的映射

Session 层把 `OrchestratorEvent` 翻译成 ACP 帧。完整映射表见
[protocol.md — 会话层事件映射](../interfaces/protocol.md#会话层事件--sessionupdate-映射)。

---

## `StopReason`

`query()` 的 generator return value（不是 yield value）是 `StopReason`：

```python
class StopReason(str, Enum):
    end_turn         = "end_turn"        # LLM 正常结束，不再调用工具
    max_turns        = "max_turns"       # 达到 max_turns 上限（0 = 不限制，>0 = 具体上限）
    cancelled        = "cancelled"       # asyncio Task 被 cancel
    error            = "error"           # 不可恢复的错误（provider 故障等）
    hook_blocked     = "hook_blocked"    # blocking hook 拒绝了 query
    budget_exceeded  = "budget_exceeded" # 累计 token 超出 token_budget
```

> ACP 规范的 `StopReason` 还包含 `max_tokens`（provider 返回的 token
> limit）和 `refusal`（模型拒绝回答）。这两个由 provider 层转译后映射到
> 我们的 `end_turn`（provider 会在 stop_reason 字段里带原始原因），
> Session 层在把 `StopReason` 写进 ACP response 时透传 provider 的原始值。

---

## Permission

工具执行前如果需要用户确认，Orchestrator 调用 `on_permission` 回调：

```python
@dataclass(frozen=True)
class PermissionRequest:
    tool_use_id: str
    tool_name: str
    tool_title: str
    input_summary: str               # 给用户看的一行描述
    risk_level: Literal["low", "medium", "high"]

@dataclass(frozen=True)
class PermissionResponse:
    decision: Literal["allow_once", "allow_always", "reject"]

PermissionCallback = Callable[[PermissionRequest], Awaitable[PermissionResponse]]
```

Session 层实现 `PermissionCallback`，通过 `ProtocolAPI` 发出
`session/request_permission` 请求，等待 client 回复后 resolve：

```python
async def _on_permission(req: PermissionRequest) -> PermissionResponse:
    fut: asyncio.Future[PermissionResponse] = asyncio.get_event_loop().create_future()
    self._pending_permissions[req.tool_use_id] = fut
    await ctx.protocol.request(
        "session/request_permission",
        params=PermissionParams.from_request(req),
        result_type=PermissionResult,
    )
    # 当 client 回复时，SessionHandler.handle_permission_response() resolve 这个 fut
    return await fut
```

Orchestrator 只知道"await on_permission 得到 allow/reject"，对
WebSocket 一无所知。

---

## 取消语义

**取消是正常的结束路径，不是异常。**

Session 层 cancel 一个 in-flight query：

```python
task.cancel()
```

Orchestrator 内部处理（已在主循环伪代码里体现）：

```python
except asyncio.CancelledError:
    self._stop_reason = StopReason.cancelled
    # Patch orphan tool_use blocks — Anthropic API requires every
    # tool_use to have a matching tool_result.  (CC: query.ts:1015-1052)
    orphan_ids = self._history.pending_tool_use_ids()
    if orphan_ids:
        synthetic = [ToolResultContent(tool_use_id=tid, content="Interrupted by user", is_error=True)
                     for tid in orphan_ids]
        self._history.append_tool_results(synthetic)
    yield CancelledEvent()
    return   # bare return — async generators cannot return a value
    # 不 re-raise：让 Session 的 async for 循环干净结束
    # Session 调用完后通过 orchestrator.stop_reason 得到 StopReason.cancelled
```

ACP 规范要求（[prompt-turn.md cancellation](../references/acp/protocol/prompt-turn.md)）：
> Agents MUST catch these errors and return the semantically meaningful
> `cancelled` stop reason.

我们通过捕获 `CancelledError` 而非透传实现这一点。

---

## Conversation History

Conversation history **住在 Orchestrator 实例里**，不在 Session 里。

```
Session 创建  →  构造 Orchestrator（history 为空）
第 1 次 query() →  Orchestrator 把 prompt + 助手回复 + tool results 追加到内部 history
第 2 次 query() →  继续从上一次的 history 末尾开始
...
Session 销毁  →  Orchestrator.close()，history 随之释放
```

Session 负责把每个 `OrchestratorEvent` 落盘到 JSONL，这是持久化侧。
Orchestrator 持有内存侧的 history 用于喂给 LLM。两者互不依赖。

> **差异 vs Claude Code**：Claude Code 的 `query()` 是无状态函数，
> `messages` 每次作为参数传入。我们选择有状态实例，因为 conversation
> history 自然属于 Orchestrator，不用在 Session 和 Orchestrator 之间
> 反复搬运。

---

## Sub-agent

Sub-agent 通过 **AgentTool** 融入工具执行流程，但 child transcript
不会作为父会话的顶层 assistant/tool 事件直接透传：

1. LLM 输出 `AgentTool` 的 `tool_use`
2. `ToolExecutor` 执行 `AgentTool.call()`
3. `AgentTool` 内部构造一个新的 `StandardOrchestrator`（depth + 1），
   调用其 `query()`
4. Sub-agent 产出的 `TextDelta` 被收集为 Agent 工具的最终结果，
   供父 LLM 在 `tool_result` 中读取
5. `SubAgentEnd` 汇总 child tool use / token / duration 统计，最终挂到
   Agent 工具结果的 `mustang.agent/agentStats`
6. Child `OrchestratorEvent` 不映射为父会话的顶层 `session/update`；
   这与 Claude Code main 对齐，避免子 Agent 回复直接刷到主窗口

### 事件流结构

父会话客户端看到的是 Agent 工具自己的生命周期。子 Agent 的正文不会
以父 assistant message 的形式出现：

```
父 Orchestrator 事件流:
  ...
  ToolCallStart(id="tc_1", kind=agent)
  ToolCallResult(id="tc_1", content=[...], meta={"mustang.agent/agentStats": ...})
  ...
```

`SubAgentStart` / `SubAgentEnd` 仍可用于内部 transcript / SDK progress
场景，但前台 `AgentTool` 不再用 `passthrough_event` 把 child stream 投射
到父客户端。

### 上下文隔离

Sub-agent 的 conversation history 独立于父 Orchestrator。`AgentTool.call()`
把父 context 的**副本**（fork）传给子 Orchestrator，子 Orchestrator
的历史变更不影响父。`AgentTool.call()` 返回后子 Orchestrator 随之释放，
不需要显式 `close()`。

> **参照**：Claude Code main 的 `AgentTool` 收集 child messages，通过
> Agent 工具 progress/result 渲染；最终 `finalizeAgentTool(...)` 把
> child 最后一条 assistant text 作为工具结果交还，不把 child assistant
> text 当作父会话正文直接输出。

---

## 构造

### 依赖窄接口（`OrchestratorDeps`）

Orchestrator **不持有 `KernelModuleTable`**。依赖由 `SessionManager` 从
`module_table` 提取后，以**窄 Protocol 接口**打包成 `OrchestratorDeps`
传入。Orchestrator 只知道它需要的那一小块接口，不知道也不访问其他子系统。

```python
# kernel/orchestrator/types.py

@dataclass
class OrchestratorDeps:
    provider:    LLMProvider               # stream() + model_for() — LLMManager 满足
    tool_source: ToolManager | None        # lookup() + snapshot_for_session()
    authorizer:  ToolAuthorizer | None     # authorize() + grant(); None = allow-all
    hooks:       HookManager | None        # fire(ctx); None = skip hooks
    memory:      MemorySource | None       # PromptBuilder memory 注入
    skills:      SkillManager | None       # PromptBuilder skill 注入 + on_file_touched
    prompts:     PromptManager | None      # prompt 模板查找
    connection_auth: AuthContext | None    # 传给 AuthorizeContext
    should_avoid_prompts_provider: Callable[[], bool] | None  # 非交互模式检测
    queue_reminders:  Callable[[list[str]], None] | None  # hook → Session reminder 通道
    drain_reminders:  Callable[[], list[str]] | None      # Session → Orchestrator 通道
    task_registry:   TaskRegistry | None      # 后台任务跟踪
    deliver_cross_session: Callable | None    # 跨 session 消息投递
    schedule_manager: ScheduleManager | None  # CronCreate/Delete/List 工具
    git:             GitManager | None        # git context 注入 + worktree 工具
```

窄 Protocol 接口（`LLMProvider`）定义在同一文件。SessionManager 从
`module_table` 提取各子系统后组装此 dataclass。

### 构造函数

```python
StandardOrchestrator(
    deps:            OrchestratorDeps,
    session_id:      str,
    initial_history: list[Message],   # session/load 时从 JSONL 重建；新建时为 []
    config:          OrchestratorConfig,
    depth:           int = 0,         # sub-agent 时 depth > 0
)
```

### SessionManager 组装

SessionManager 从 `module_table` 提取子系统引用，组装 `OrchestratorDeps`
后传给 `StandardOrchestrator`。`module_table` 只出现在 SessionManager 里；
Orchestrator 的依赖从构造函数签名完全可读。单测直接传 mock，无需
构造 module_table。

### AgentTool 构造子 Orchestrator

AgentTool 不传 `deps=None`——它直接复用父 Orchestrator 的 deps，
深度 +1：

```python
# AgentTool.call() 内部
child = StandardOrchestrator(
    deps    = self._deps,   # 父 deps 直接复用，不重新查 module_table
    depth   = self._depth + 1,
    # messages fork 自父 history
    ...
)
```

> **参照**：Claude Code 的 AgentTool 不传 `deps`，子 `query()` 自动
> `productionDeps()` ——效果等同于"继承父的生产依赖"。我们显式复用
> `deps` 对象，语义相同，且对测试更友好（mock 自动传递到子 agent）。

---

## Hook 节点

| Hook 事件 | 触发时机 | 可修改的数据 |
|---|---|---|
| `user_prompt_submit` | append user message 之前 | prompt content |
| `post_sampling` | LLM stream 结束后、abort check 和 tool/stop 分支之前（STEP 3c） | —（纯通知，`can_block=False`） |
| `pre_tool_use` | tool 执行之前（permission 已批准后）| tool input |
| `post_tool_use` | tool 执行之后 | tool result |
| `stop` | 主循环结束（任何 StopReason）| `ctx.stop_reason`（provider 级 stop reason，如 `"end_turn"` / `"max_tokens"`） |

Hook 由 `HookManager` 管理，Orchestrator 在关键节点调用
`self._hooks.fire(event_name, **kwargs)`。Hook 返回值可以修改 prompt /
tool input / tool result，实现注入上下文、审计日志、安全过滤等能力。

---

## 与其他子系统的关系

Orchestrator **不直接引用任何 kernel 子系统**。它只持有 `OrchestratorDeps`
里的窄 Protocol 接口。下表描述各子系统如何满足这些接口：

| 子系统 | 提供的接口 | Orchestrator 使用方式 |
|---|---|---|
| **LLMManager** | `deps.provider` | `stream()` 每次 LLM 调用；`model_for()` 取默认 model |
| **ToolManager** | `deps.tool_source` | `snapshot_for_session()` 取 tool schemas；`lookup()` 解析 tool name |
| **ToolAuthorizer** | `deps.authorizer` | `authorize()` 仲裁 allow/deny/ask；`grant()` 记录 allow_always |
| **HookManager** | `deps.hooks` | `fire(ctx)` 在 7 个 hook 节点触发 |
| **MemoryManager** | `deps.memory` | PromptBuilder 拉相关 memory |
| **SkillManager** | `deps.skills` | PromptBuilder 拉 skill body；`on_file_touched()` 动态发现 |
| **PromptManager** | `deps.prompts` | prompt 模板查找 |
| **MCP** | —（不直接接触）| MCP tools 由 ToolManager 在 `snapshot_for_session()` 时统一返回 |
| **Session** | —（不直接接触）| Session 只通过 `Orchestrator` Protocol 交互 |

---

## 设计约束

- **Orchestrator 不持有长生命周期的 asyncio Task**：`query()` 在调用方的
  task 里跑，取消通过 task cancellation 传入。ToolExecutor 在并发 batch
  执行时创建短生命周期 task（per-tool），这些 task 在 batch 结束或
  `discard()` 时被 join/cancel。`close()` 只需清理 provider 连接。

- **Orchestrator 不写磁盘**：持久化全部由 Session 层的 JSONL writer 负责。
  Orchestrator 只写内存 history。

- **Orchestrator 不知道 session_id 用来做什么**：它只在构造时接收
  `session_id`，传给 sub-agent 和 hook 事件用于日志关联。不用它查询
  Session 状态。
