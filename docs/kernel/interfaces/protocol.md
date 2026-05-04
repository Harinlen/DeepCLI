# Protocol Layer

## Purpose

协议层是 WebSocket 传输层和会话层之间的中间层。它把**原始
JSON 帧**翻译成**类型化的方法调用**，反过来把**类型化的
响应和事件**翻译成 JSON 帧。Kernel 在 ACP 中扮演 **Agent** 角色，
客户端（IDE 扩展、浏览器等）扮演 **Client** 角色。

职责边界：

- ✅ JSON-RPC 2.0 帧的编解码
- ✅ ACP 方法路由（dispatch）
- ✅ `initialize` / `authenticate` 握手本身
- ✅ Request ID 跟踪（incoming in-flight、outgoing in-flight）
- ✅ 会话层事件 → ACP `session/update` 通知的映射
- ✅ 错误到 JSON-RPC error 的映射
- ✅ 取消协议（`session/cancel` + 可选 `$/cancel_request` RFD）
- ❌ **不管认证**（已在传输层由 [ConnectionAuthenticator](../subsystems/connection_authenticator.md) 完成）
- ❌ **不管 session 业务**（`session/new` / `session/prompt` 等的实际处理在会话层）
- ❌ **不管 WebSocket IO**（accept / recv / send 是传输层的事）

## 关系：ACP 规范的权威来源

所有 ACP 线上格式、方法语义、枚举值的**真相**在
[`../references/acp/`](../references/acp/) 的本地镜像里。这个
文档描述"我们**怎么实现**ACP"，**不**重复 ACP 本身的定义。遇到
"某字段长什么样"之类的问题请查镜像，不要在 protocol.md 里重写。

关键 ACP 文档对应：

- **协议总览**：[overview.md](../references/acp/protocol/overview.md)
- **握手**：[initialization.md](../references/acp/protocol/initialization.md)
- **Session 建立 / 加载**：[session-setup.md](../references/acp/protocol/session-setup.md)
- **对话主循环**：[prompt-turn.md](../references/acp/protocol/prompt-turn.md)
- **工具调用和权限**：[tool-calls.md](../references/acp/protocol/tool-calls.md)
- **内容块**：[content.md](../references/acp/protocol/content.md)
- **扩展机制**：[extensibility.md](../references/acp/protocol/extensibility.md)
- **取消 RFD**：[request-cancellation.md](../references/acp/rfds/request-cancellation.md)

## ACP Adoption Profile

ACP 里有很多"可选"能力。Kernel 作为 Agent 的采纳状态：

### 我们实现的方法（Agent 方向，Client → Kernel）

| 方法 | 类型 | 处理层 | 状态 |
|---|---|---|---|
| `initialize` | Request | **Protocol** | Mandatory |
| `authenticate` | Request | **Protocol (noop)** | Mandatory if advertised |
| `session/new` | Request | Session | Mandatory |
| `session/load` | Request | Session | Implemented（我们声明 `loadSession: true`）|
| `session/list` | Request | Session | Implemented（`sessionCapabilities.list: {}`）|
| `session/prompt` | Request | Session | Mandatory |
| `session/set_mode` | Request | Session | Implemented |
| `session/set_config_option` | Request | Session | Implemented（取代 set_mode，向后兼容保留两者）|
| `session/cancel` | Notification | Protocol + Session | Mandatory |
| `model/profile_list` | Request | Model (LLMManager) | DeepCLI 扩展 |
| `model/profile_add` | Request | Model (LLMManager) | DeepCLI 扩展 |
| `model/profile_remove` | Request | Model (LLMManager) | DeepCLI 扩展 |
| `model/set_current` | Request | Model (LLMManager) | DeepCLI 扩展 |
| `model/update` | Request | Model (LLMManager) | DeepCLI 扩展 |
| `session/compact` | Request | Session | DeepCLI 扩展 —— 待实现 |
| `session/delete` | Request | Session | DeepCLI 扩展 —— 待实现 |
| `session/get_usage` | Request | Session | DeepCLI 扩展 —— 待实现 |
| `commands/list` | Request | Commands (CommandManager) | DeepCLI 扩展 —— 待实现 |
| `$/cancel_request` | Notification | Protocol | Optional (RFD) —— 一期不实现，二期再加 |

`model/*`、`session/compact`、`session/delete`、`session/get_usage`、`commands/list` 均是 DeepCLI 在 ACP 规范外新增的命名空间（[ACP 扩展机制](../references/acp/protocol/extensibility.md)允许非标准方法）。

### 我们实现的方法（Client 方向，Kernel → Client）

| 方法 | 类型 | 发起方 | 状态 |
|---|---|---|---|
| `session/update` | Notification | Session (via ProtocolAPI) | Mandatory |
| `session/request_permission` | Request | Tool executor (via ProtocolAPI) | Mandatory |

### 我们**不**实现的方法（一期延后）

| 方法 | 一期决定 | 备注 |
|---|---|---|
| `fs/read_text_file` / `fs/write_text_file` | **不实现** | 一期 kernel 专注本地场景（client 和 kernel 在同一台机器、看到同一个文件系统），tool 直接读写磁盘即可。**未来 IDE 集成时**会需要这个能力让 agent 看到编辑器 buffer 而不是磁盘版本，到时候再加 |
| `terminal/create` / `terminal/output` / `terminal/release` / `terminal/wait_for_exit` / `terminal/kill` | **不实现** | 一期 bash tool 在 kernel 侧直接起子进程。Terminal 代理涉及 5 个方法 + 流式 stdout/stderr + 状态机，成本高，二期再评估 |

**意味着**：一期我们**不**发出 `fs/*` / `terminal/*` outgoing request。即使 client 在 `initialize` 里声明了 `fs.readTextFile: true` / `terminal: true`，我们也忽略，不会用它们。

> **二期 IDE 集成的设计预留**：当 client 是编辑器（VSCode / Zed / JetBrains）时，编辑器持有未保存的 buffer 状态，是文件状态的真相所在。届时 Tools 子系统需要引入 `FileSystem` Protocol 抽象（`LocalFileSystem` / `ProxyFileSystem`），由 Session 根据 `ConnectionContext.negotiated_capabilities.fs.*` 决定具体用哪个实现。这是一个 capability-aware tool 的设计，属于 Tools 子系统的范畴，不在 protocol.md 职责内。

### Agent Capabilities 和 Info 声明

完整的 `InitializeResponse` 形态：

```json
{
  "protocolVersion": 1,
  "agentCapabilities": {
    "loadSession": true,
    "promptCapabilities": {
      "image": true,
      "audio": false,
      "embeddedContext": true
    },
    "mcpCapabilities": {
      "http": true,
      "sse": false
    },
    "sessionCapabilities": {
      "list": {}
    }
  },
  "agentInfo": {
    "name": "mustang-kernel",
    "title": "DeepCLI",
    "version": "<kernel version from pyproject.toml>"
  },
  "authMethods": []
}
```

**Capabilities**：

- **`loadSession: true`** —— 我们支持 session 持久化恢复
- **`promptCapabilities.image: true`** —— 可以接收图片 content block（多模态 provider 需要）
- **`promptCapabilities.audio: false`** —— 一期不做
- **`promptCapabilities.embeddedContext: true`** —— 支持 `resource` 嵌入（文件引用、@-mention）
- **`mcpCapabilities.http: true`** —— 我们支持 HTTP MCP transport（MCP 子系统的责任）
- **`mcpCapabilities.sse: false`** —— ACP 本身已 deprecate，不采纳
- **`sessionCapabilities.list: {}`** —— 支持 `session/list`

**Agent Info**：

- **`name: "mustang-kernel"`** —— 程序识别名，固定不变
- **`title: "DeepCLI"`** —— 给 UI 显示用，不带环境 / 版本标注
- **`version`** —— 从 `pyproject.toml` 读取，启动时注入到 Python 模块的 `__version__` 属性里

### Auth 方法

Kernel 的认证**全部在传输层完成**（[ConnectionAuthenticator](../subsystems/connection_authenticator.md) 在 WebSocket `accept()` 后立即验证 token / password），协议层从不处理认证。`InitializeResponse.authMethods` 永远返回 `[]`：

```json
{"authMethods": []}
```

空数组是 ACP 规范明确允许的配置，告知 client "协议层不需要 authenticate 调用"。

**为什么不用 ACP 的 `authenticate` 方法**：详细理由见 [connection_authenticator.md#为什么是传输层认证](../subsystems/connection_authenticator.md#为什么是传输层认证不走-acp-的-authenticate-方法)。核心是四点：ACP 的 `AuthenticateRequest` schema 无法承载凭证；传输层认证让未认证连接根本进不了协议层、最小化攻击面；POSIX 文件权限已经是充分的认证介质；本机 kernel 不是云服务，协议层认证没有真实收益。

**`authenticate` 请求的处理策略**：协议层仍然注册这个方法（进 REQUEST_DISPATCH），收到调用时**直接返回空的成功响应**（`AuthenticateResponse {}`）。理由：

- Client 如果按 ACP 防御式实现误发 `authenticate`，应该优雅应对而不是报 Method not found 让 client 以为 kernel 不合规
- 既然已经通过传输层认证，再跑一次 noop 是无害的
- 规避"ACP 强制方法却返回 Method not found"这种矛盾信号

## 消息结构和握手流程

### 连接生命周期

```
[WebSocket 连接已建立（传输层已通过 ConnectionAuthenticator 认证）]
     │
     ├─ Client → Kernel: initialize
     ├─ Kernel → Client: InitializeResponse
     │  └─ 协议层填 ConnectionContext.client_info / negotiated_capabilities
     │
     ├─ Client → Kernel: authenticate    (可选，我们总是返回成功)
     ├─ Kernel → Client: AuthenticateResponse
     │
     ├─ Client → Kernel: session/new 或 session/load 或 session/list
     ├─ Kernel → Client: response (sessionId etc.)
     │  └─ 协议层填 ConnectionContext.bound_session_id
     │
     ├─┐  重复以下循环
     │ ├─ Client → Kernel: session/prompt
     │ │   └─ Session 层开始处理
     │ │       ├─ Kernel → Client: session/update (notification, many)
     │ │       └─ Kernel → Client: session/request_permission (request, optional)
     │ │           └─ Client → Kernel: permission response
     │ ├─ (任何时候) Client → Kernel: session/cancel (notification)
     │ │   └─ 协议层 cancel task → Session handler 返回 stopReason: cancelled
     │ └─ Kernel → Client: PromptResponse { stopReason }
     │
     └─ [WebSocket close]
```

### Initialize 阶段的具体细节

Client 第一条消息**必须**是 `initialize`。在收到 initialize 之前，协议层对任何其他方法回 `-32600` (Invalid Request)，error message `"Connection not initialized; send 'initialize' first"`。

> **为什么不是 `-32002`**：ACP 的 `-32002` 是 **Resource not found**（文件 / session / tool 不存在），不是 LSP 的 "ServerNotInitialized"。两个协议用同一个数字表达不同含义。ACP 没有为 "未初始化" 定义专门的码，所以我们用最贴近语义的标准 JSON-RPC `-32600` Invalid Request。

**版本协商策略**（符合 ACP 规范）：

- Client 发送 `protocolVersion: N`（它支持的最高版本）
- 如果 `N >= 我们支持的最高版本`：我们返回自己支持的最高版本
- 如果 `N < 我们支持的最高版本`：我们返回 `N`（降级支持，如果我们确实支持 N）
- 如果 `N > 我们支持的最高版本` 且我们无法降级到 N：返回我们最高版本，Client 自行决定是否断开

一期我们只支持 `protocolVersion: 1`。

**协议层在握手后填充 ConnectionContext**：

```python
conn_ctx.client_info = ClientInfo(
    name=params.clientInfo.name,
    title=params.clientInfo.title,
    version=params.clientInfo.version,
)
conn_ctx.negotiated_capabilities = params.clientCapabilities.model_dump()
```

`negotiated_capabilities` 里记录的是 client 声明的能力（`fs.readTextFile` 等），便于将来诊断 —— 虽然我们不用这些能力，但值得存下来。

## 协议层架构

### 三张表

协议层的核心是**三张查找表**，分别对应三个 dispatch 方向：

```python
# 1. 入站 request（Client → Kernel）
REQUEST_DISPATCH: dict[str, RequestSpec] = {
    "initialize":                RequestSpec(...),  # protocol 层自己处理
    "authenticate":              RequestSpec(...),  # protocol 层 noop 成功
    # session/* — target="session" → SessionHandler (SessionManager)
    "session/new":               RequestSpec(..., target="session"),
    "session/load":              RequestSpec(..., target="session"),
    "session/list":              RequestSpec(..., target="session"),
    "session/prompt":            RequestSpec(..., target="session"),
    "session/set_mode":          RequestSpec(..., target="session"),
    "session/set_config_option": RequestSpec(..., target="session"),
    # model/* — target="model" → ModelHandler (LLMManager)
    "model/profile_list":        RequestSpec(..., target="model"),
    "model/profile_add":         RequestSpec(..., target="model"),
    "model/profile_remove":      RequestSpec(..., target="model"),
    "model/set_current":         RequestSpec(..., target="model"),
    "model/update":              RequestSpec(..., target="model"),
}

# 2. 入站 notification（Client → Kernel）
NOTIFICATION_DISPATCH: dict[str, NotificationSpec] = {
    "session/cancel":   NotificationSpec(...),    # protocol + session 协作
    # "$/cancel_request": ...                     # 二期
}

# 3. 出站 method（Kernel → Client）—— 不是查找表，是类型约束
# 由 ProtocolAPI.notify / ProtocolAPI.request 按 method 字符串发送
# 协议层维护合法的出站方法名列表用于校验
OUTGOING_METHODS = {
    "session/update":             ("notification", SessionUpdateParams),
    "session/request_permission": ("request", PermissionParams, PermissionResult),
    # "$/cancel_request":          ("notification", CancelRequestParams),  # 二期
}
```

表结构：

```python
HandlerTarget = Literal["session", "model"]

@dataclass(frozen=True)
class RequestSpec:
    handler: Callable[[Any, HandlerContext, BaseModel], Awaitable[BaseModel]]
    # 第一个参数是具体的 handler 对象（SessionHandler 或 ModelHandler），
    # 运行时由 AcpSessionHandler._get_handler_for(spec.target) 提供。
    params_type: type[BaseModel]
    result_type: type[BaseModel]
    target: HandlerTarget = "session"
    # "session" → SessionHandler（SessionManager）
    # "model"   → ModelHandler（LLMManager）

@dataclass(frozen=True)
class NotificationSpec:
    handler: Callable[[SessionHandler, HandlerContext, BaseModel], Awaitable[None]]
    params_type: type[BaseModel]
    # 没有 result_type —— 通知不产生响应
```

`initialize` / `authenticate` 的 `handler` 不指向 SessionHandler，而是指向协议层自己的内部函数。这样 dispatch 表是**统一的**（协议层和会话层用同一套机制），方便测试和 debug。

新增 target 之后，`AcpSessionHandler._route_request` 根据 `spec.target` 调 `_get_handler_for(target)` 分发，不再硬编码只有 `SessionHandler` 一条路径。扩展新的 target 类型只需在 `routing.py` 加 `Literal` 值，在 `session_handler.py` 加对应的 `_get_<target>_handler()` 方法。

### SessionHandler 接口

```python
class SessionHandler(Protocol):
    """Session-layer contract. Implemented by SessionManager subsystem."""

    async def new(
        self, ctx: HandlerContext, params: NewSessionRequest
    ) -> NewSessionResponse: ...

    async def load(
        self, ctx: HandlerContext, params: LoadSessionRequest
    ) -> LoadSessionResponse: ...

    async def list(
        self, ctx: HandlerContext, params: ListSessionsRequest
    ) -> ListSessionsResponse: ...

    async def prompt(
        self, ctx: HandlerContext, params: PromptRequest
    ) -> PromptResponse: ...

    async def set_mode(
        self, ctx: HandlerContext, params: SetSessionModeRequest
    ) -> SetSessionModeResponse: ...

    async def set_config_option(
        self, ctx: HandlerContext, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse: ...

    async def cancel(
        self, ctx: HandlerContext, params: CancelNotificationParams
    ) -> None:
        """Handle session/cancel notification. See Cancellation section."""
```

Params / Result 的类型名采用 ACP schema.json 里的官方命名（`NewSessionRequest`、`PromptResponse` 等），直接用 Pydantic 复刻。一期手写这些 schema 对照 [schema.json](../references/acp/schema.json)，将来可以考虑用 datamodel-code-generator 自动生成。

### ModelHandler 接口

`ModelHandler` 是 `model/*` 方法（DeepCLI 扩展）的处理层接口，由 `LLMManager` 实现。它管理的是 **kernel-global** 操作（model profile CRUD），与 session 无关。

```python
class ModelHandler(Protocol):
    """LLM model profile management. Implemented by LLMManager subsystem."""

    async def list_profiles(
        self, ctx: HandlerContext, params: ListProfilesParams
    ) -> ListProfilesResult:
        """Return all registered model profiles and the current default."""

    async def add_profile(
        self, ctx: HandlerContext, params: AddProfileParams
    ) -> AddProfileResult:
        """Add a new model profile and persist to kernel.yaml."""

    async def remove_profile(
        self, ctx: HandlerContext, params: RemoveProfileParams
    ) -> RemoveProfileResult:
        """Remove a profile by name and persist the change."""

    async def set_current_model(
        self, ctx: HandlerContext, params: SetCurrentModelParams
    ) -> SetCurrentModelResult:
        """Set one llm.current_used role and persist."""

    async def update_model(
        self, ctx: HandlerContext, params: UpdateModelParams
    ) -> UpdateModelResult:
        """Update one provider model's display settings and roles."""
```

`ModelHandler` 和 `SessionHandler` 共享 `HandlerContext`（连接上下文），但 `model/*` 方法的实现不使用 `ctx.sender` 发送 notification ——  它们是简单的 request/response 操作。`HandlerContext` 统一传入是为了保持接口一致性，方便未来按需扩展（例如 model 操作的 audit log）。

isolation 保证同 SessionHandler：`LLMManager` 不 import 任何 `kernel.protocol.acp` 内容，只见 Pydantic contract 类型。

`model/provider_list` exposes all configured provider models, role refs
via `currentUsed`, per-model `contextWindows`, optional `displayNames`,
and `defaultContextWindow` for models whose provider cannot report an
exact window. `model/update` persists model-level display name,
context-window override, and exact role assignments.

### 协议层 / 会话层的 seam

这条 seam 是整个栈里最重要的隔离点：

```
协议层（protocol）                        会话层（session）
─────────────────────────────────────    ─────────────────────────────────────
原始 JSON bytes                          从不出现
  ↓ json.loads()
{"method": "session/new", "params": {}}
  ↓ REQUEST_DISPATCH 查表
  ↓ NewSessionRequest(**params)          NewSessionRequest(...)  ← 从这里开始
                                           ↓ handler.new(ctx, params)
                                         NewSessionResponse(...)  ← 到这里结束
  ↓ result.model_dump()
{"result": {...}}
  ↓ json.dumps() + WebSocket send
```

会话层只见 Pydantic 对象，**不知道**：
- JSON-RPC 2.0 frame 结构（`id` / `jsonrpc` / `method` 字段）
- ACP 方法名字符串（`"session/new"` 等）
- WebSocket 句柄 / 连接细节

所有序列化、方法路由、错误帧格式化在协议层完成，会话层只做业务。这个隔离让 SessionManager 可以不依赖任何网络 / 协议基础设施，直接在单元测试里用 `FakeProtocol` + Pydantic 对象驱动（见[测试 handler](#测试-handler)小节）。

### HandlerContext

```python
@dataclass(frozen=True)
class HandlerContext:
    """Per-dispatch context passed to every SessionHandler call."""

    conn: ConnectionContext
    """Immutable connection identity + mutable协议协商状态。
    See ../subsystems/connection_authenticator.md for ConnectionContext definition."""

    protocol: ProtocolAPI
    """Capability injection: handler uses this to send outgoing
    notifications and requests to the client."""

    request_id: str | int
    """The JSON-RPC id of the inbound request being handled.
    Useful for log correlation.  For notifications, this is None."""
```

**为什么字段这么少**：handler 需要的其他东西（config、hooks、memory、session map 等）都能从 `app.state.module_table.get(...)` 或 `conn.bound_session_id` 拿到。少放字段让 handler 测试最简单（mock 三个字段就够了）。

### ProtocolAPI

```python
class ProtocolAPI(Protocol):
    """Capability injection given to handlers by the protocol layer.

    Each instance is scoped to a single connection — the underlying
    WebSocket is captured in a closure, handlers never see it.
    """

    async def notify(
        self,
        method: str,
        params: BaseModel,
    ) -> None:
        """Send an outgoing notification (kernel → client).

        Raises ValueError if `method` is not in OUTGOING_METHODS
        or is registered as a request type (not notification).
        """

    async def request(
        self,
        method: str,
        params: BaseModel,
        *,
        result_type: type[T],
        timeout: float | None = None,
    ) -> T:
        """Send an outgoing request and await the client's response.

        Allocates a fresh JSON-RPC id, sends the frame, registers a
        Future in outgoing_in_flight, waits for the response.

        Raises:
            TimeoutError: if timeout elapses before client responds
            ProtocolError: if client sends malformed response
            ClientError: if client responds with a JSON-RPC error (any code)
            asyncio.CancelledError: if the caller's task is cancelled
                (finally block removes the Future from outgoing_in_flight)
        """
```

### 测试 handler

`ProtocolAPI` 是 Protocol 类型（structural typing），写一个假实现就能单测 session handler：

```python
class FakeProtocol:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, BaseModel]] = []
        self.request_responses: dict[str, Any] = {}

    async def notify(self, method, params):
        self.notifications.append((method, params))

    async def request(self, method, params, *, result_type, timeout=None):
        return result_type.model_validate(self.request_responses[method])
```

Handler 测试不需要真起 WebSocket + 真协议层 —— 这是"注入 ProtocolAPI"相对于"内嵌事件流"的主要好处。

## 会话层事件 → `session/update` 映射

Session handler 内部运行 orchestrator，orchestrator 产出一系列事件。这些事件必须映射到 ACP 的 `session/update` 通知变体（由 `update.sessionUpdate` 字段区分）。

ACP 一共定义 **10 个** `sessionUpdate` variant（见 [schema.json](../references/acp/schema.json) 的 `SessionUpdate` union）。下表覆盖了所有 10 个，加上 2 个走非 update 路径的事件（permission、sub-agent）。

| Orchestrator 事件 | ACP `sessionUpdate` | 备注 |
|---|---|---|
| `TextDelta(content)` | `agent_message_chunk` with `content: { type: "text", text: content }` | 流式文本，payload 类型 `ContentChunk` |
| `ThoughtDelta(content)` | `agent_thought_chunk` with `content: { type: "text", text: content }` | 流式 reasoning（Claude extended thinking、OpenAI o1 reasoning trace 等），payload 也是 `ContentChunk` |
| `UserMessageEcho(content)` | `user_message_chunk` | **仅** `session/load` 回放历史时用 —— 普通 prompt turn 不发，因为用户消息本就是 client 自己发的 |
| `PlanUpdate(entries)` | `plan` with `entries: [{content, priority, status}, ...]` | 直接映射，priority ∈ `high`/`medium`/`low`，status ∈ `pending`/`in_progress`/`completed` |
| `ToolCallStart(id, title, kind, raw_input?)` | `tool_call` with `status: "pending"` | `kind` 映射到 ACP `ToolKind` 枚举 |
| `ToolCallInProgress(id)` | `tool_call_update` with `status: "in_progress"` | |
| `ToolCallResult(id, content)` | `tool_call_update` with `status: "completed", content: [...]` | 成功 |
| `ToolCallError(id, error)` | `tool_call_update` with `status: "failed", content: [...]` | 失败 |
| `ToolCallDiff(id, path, old, new)` | `tool_call_update` with `content: [{ type: "diff", path, oldText, newText }]` | 文件修改直接用 ACP diff content 类型 |
| `ToolCallLocations(id, locations)` | `tool_call_update` with `locations: [{path, line?}]` | "Follow the agent" 功能：客户端可以跟随 agent 在文件间跳转 |
| `ModeChanged(mode_id)` | `current_mode_update` with `modeId` | 模式切换通知 |
| `ConfigOptionChanged(...)` | `config_option_update` with **完整** config state | ACP 要求发完整状态，不是 diff |
| `SessionInfoChanged(title, ...)` | `session_info_update` with **只改动的字段** | 和 ConfigOption 相反，这里是 partial update |
| `AvailableCommandsChanged(commands)` | `available_commands_update` with `availableCommands: [{name, description, _meta?}]` | 由 SkillRegistry 在 skill 列表变化时 emit。`AvailableCommand` schema 和 skill metadata 一一对应 |
| `PermissionRequest(...)` | ⚠ **不是 update** —— 走 `ProtocolAPI.request("session/request_permission", ...)` | 这是出站 request 不是 notification |
| `SubAgentStart / SubAgentEnd` | ⚠ **ACP 无原生对应** —— 放在 update 的 `_meta` 字段下，namespace `mustang/agent_start` / `mustang/agent_end` | 详见下文 `_meta` 扩展 |

**实现位置**：这层映射由 **Session handler 的 prompt 方法**内部执行 —— 它从 orchestrator 拿原生事件，调 `self.ctx.protocol.notify("session/update", ACPUpdateParams(...))`。协议层**不**理解 orchestrator 事件，只认 ACP 类型。

**ContentChunk 内部结构**：所有三个 `*_chunk` variant（`user_message_chunk` / `agent_message_chunk` / `agent_thought_chunk`）的 payload 都是 `ContentChunk { content: ContentBlock, _meta? }`，其中 `ContentBlock` 是 [content.md](../references/acp/protocol/content.md) 里定义的 union（text / image / audio / resource_link / resource）。流式文本最常见的形态就是 `{ type: "text", text: "..." }`。

**Tool kind 映射**：Kernel 的 tool 各自声明 ACP `ToolKind`（`read` / `edit` / `execute` / `search` / `fetch` / `think` / `delete` / `move` / `other`）。由 tool subsystem 在 tool schema 定义时指定，session handler 不做翻译。

## 流式通知的批处理（Batching）

LLM 可能以极高频率产生小片段的文本 delta（10-20ms 一次、每次 5-20 个字符），每个 delta 直接 → 一条 WebSocket 帧会造成大量协议 overhead 而内容几乎为零。协议层对**可合并**的 session/update variant 做批处理，显著降低帧数。

### 可合并 vs 不可合并

| variant | 可合并？ | 合并语义 |
|---|---|---|
| `agent_message_chunk` | ✅ | 同一轮连续 chunk 的 `content.text` 字符串拼接 |
| `agent_thought_chunk` | ✅ | 同上 |
| `user_message_chunk` | ✅ | 同上（`session/load` 回放历史时也受益） |
| `tool_call` | ❌ | 每条是状态迁移 `pending`，不能合并 |
| `tool_call_update` | ❌ | 每条是独立的状态变化，合并语义不清 |
| `plan` | ❌ | 每条代表一次 plan revision，覆盖语义 |
| `current_mode_update` | ❌ | 离散事件 |
| `config_option_update` | ❌ | 离散事件，而且 ACP 要求发完整状态 |
| `session_info_update` | ❌ | 离散事件 |
| `available_commands_update` | ❌ | 离散事件 |

只有三个 `*_chunk` variant 可合并 —— 因为它们的 payload 都是 `ContentChunk { content: ContentBlock }` 且 `content.type == "text"` 时可以自然拼字符串。

### 合并策略

- **时间窗口**：50ms。来自经验值 —— 对人眼还是流畅的连续流（20 Hz），但帧数从 100 Hz 降到 20 Hz，overhead 降 80%
- **Flush 触发**：任一条件成立立即 flush
  - 时间窗口到
  - 有**不可合并**的 variant 要发（必须先 flush 当前 buffer 再发）
  - Handler 响应当前 request，准备收尾（buffer 必须在响应前排空）
  - 连接关闭
- **每种 variant 独立 buffer** —— `agent_message_chunk` 和 `agent_thought_chunk` 不互相合并，因为内容语义不同
- **只合并 `type: "text"` 的 content** —— 如果某条 chunk 里是 `image` / `audio`，直接 flush 后单独发这条
- **合并窗口可配置**：通过 Config 读 `config.yaml` 的 `protocol.batching.chunk_window_ms`，默认 50

### 定位：批处理不是 backpressure

**重要区分**：批处理只是降低**发送频率**，它**不**解决"client 消费跟不上"的问题：

- 如果 client 慢但稳定，批处理降低协议 overhead，是净收益
- 如果 client 真的卡住（CPU 忙 / 网络抖），send buffer 仍然会堆积 —— 批处理只是让它堆得慢一点
- 真正的 backpressure（drop / 阻塞生产者 / 断开连接）需要独立机制，一期不做，对本地场景不必要

## Protocol Logging

协议层的日志直接暴露给开发者调试用，但 `session/prompt` 的 params 可能包含用户敏感内容（代码、私钥、商业逻辑），不能无脑全量 log。采用**分级策略**：

| 日志级别 | 记录内容 |
|---|---|
| **`INFO`** | 帧元数据：方向（in/out）、method、request id、params 字节大小、响应状态（success / error code） |
| **`DEBUG`** | 完整 JSON-RPC 帧，包括 params 内容。**默认关闭**，开发者显式开启时才生效 |
| **`ERROR`** | 内部错误的完整栈 + 上下文，但**不包含**原始 params 内容 |

### INFO 级日志格式示例

```
[INFO] protocol: in  method=session/prompt id=42 params_bytes=1284
[INFO] protocol: out method=session/update (agent_message_chunk) params_bytes=64
[INFO] protocol: out method=session/update (agent_message_chunk) params_bytes=72
[INFO] protocol: out method=session/update (tool_call) params_bytes=156
[INFO] protocol: in  method=session/cancel params_bytes=52
[INFO] protocol: out method=session/prompt id=42 result=success (stopReason=cancelled)
```

INFO 级足够回答 95% 的诊断问题（"哪条消息丢了"、"哪个 method 超时了"、"cancel 有没有到"），且**不泄漏任何用户内容**。

### DEBUG 级的开启方式

- 通过标准 Python logging 配置：`logging.getLogger("kernel.protocol").setLevel(logging.DEBUG)`
- 文档里明确警告："DEBUG 级日志会记录原始用户 prompt / 工具参数 / LLM 响应内容，请仅在隔离环境启用，不要在生产环境开启"
- 不提供 config.yaml 入口 —— 故意让开启这件事需要"改代码 / 改环境变量"的门槛，避免用户误触

### 信息脱敏规则

即使在 INFO 级，以下字段永远**不**出现在 log 里：

- 原始 `params` 内容
- `authenticate` 的 `methodId`
- 任何 `_meta` 字段的内容（可能含 trace context、自定义元数据）
- Error response 的完整 message（只记 code）

### 什么必须记

无论什么级别，这几类事件**必须**进 INFO 级 log：

- 连接建立 / 关闭（带 connection_id、credential_type、remote_addr，和 [AuthContext](../subsystems/connection_authenticator.md) 保持一致）
- `initialize` / `authenticate` 结果（成功 / 失败 + 错误码，不记 params）
- 每个 session 的 create / load / destroy（带 session_id）
- 任何 `-32603 Internal error` 级别的异常（带完整栈到 ERROR log）

## 取消（Cancellation）

ACP 有**两套**取消机制，我们一期只实现第一套。

### `session/cancel` notification（一期实现）

这是 ACP 的**会话级**取消通知，参数是 `sessionId`，无响应。

**流程**（参考 [prompt-turn.md#cancellation](../references/acp/protocol/prompt-turn.md)）：

```
1. Client 发 session/cancel { sessionId } notification
        ↓
2. Protocol 层收到通知 → 定位 sessionId 对应的 in-flight
   session/prompt task → task.cancel()
        ↓
3. CancelledError 在 Session handler 的 prompt 方法中冒泡
        ↓
4. handler 的 finally 块执行：
   - 对所有 pending outgoing request（存在 outgoing_in_flight 里的
     permission request 等）发 $/cancel_request notification（二期）
     或直接 abandon（一期，让 Future 在连接关闭时清理）
   - Orchestrator / Tool / Sub-agent 各层 finally 自行释放资源
        ↓
5. handler 最终返回 PromptResponse { stopReason: "cancelled" }
        ↓
6. Protocol 层把这个结果作为原 session/prompt request 的响应发给 client
```

**ACP 的硬约束**（参考 prompt-turn.md）：

- Client 发 cancel 后**必须**对所有 pending `session/request_permission`
  请求回**带 `outcome: "cancelled"`** 的响应（client 那边的义务）
- Agent 收到 cancel 后"**SHOULD** 尽快停止 LLM 请求和 tool 调用"
- Agent 可以在 cancel 之后继续发 `session/update`（比如清理阶段的最后一条状态），**只要**在对 `session/prompt` 响应**之前**发完
- **Agent 必须以 `stopReason: "cancelled"` 响应原 prompt**，不能让底层 API 抛出的异常泄漏成 JSON-RPC error —— 否则 client 会把 cancel 误显示为异常

### 协议层 + 会话层的分工

根据 ACP 规范，`session/cancel` 是**会话级**操作（按 sessionId，不是按 requestId），所以协议层**不能**自己 task.cancel —— 它不知道 sessionId 对应哪个 task。所以分工是：

- **协议层**：收到 `session/cancel` notification 后，调用 `SessionHandler.cancel(ctx, params)` 把信号转发给会话层
- **会话层**：`cancel` 方法内部维护 `{sessionId → in-flight task}` 的 map，找到对应 task 调 `task.cancel()`

这比我之前说的"协议层直接 task.cancel()"更对 —— 因为**只有会话层知道哪个 task 在处理哪个 session**。

```python
class SessionManager(SessionHandler):
    def __init__(self) -> None:
        self._in_flight_prompts: dict[str, asyncio.Task] = {}  # sessionId → prompt task

    async def prompt(self, ctx, params: PromptRequest) -> PromptResponse:
        current_task = asyncio.current_task()
        self._in_flight_prompts[params.sessionId] = current_task
        try:
            # ...run orchestrator, emit session/update via ctx.protocol.notify...
            return PromptResponse(stopReason="end_turn")
        except asyncio.CancelledError:
            return PromptResponse(stopReason="cancelled")
        finally:
            self._in_flight_prompts.pop(params.sessionId, None)

    async def cancel(self, ctx, params: CancelNotificationParams) -> None:
        task = self._in_flight_prompts.get(params.sessionId)
        if task is not None and not task.done():
            task.cancel()
        # No response for notifications
```

**协作式取消的三条纪律**（所有子系统作者必须遵守）：

1. **不吞 `CancelledError`** 除非立即 re-raise
2. **自己创建的 `asyncio.Task` 自己在 finally 里 cancel**
3. **不用 `asyncio.shield()`** 除非有非常具体的理由并写注释说明

这三条一旦违反，cancel 就无法正确穿透到 tool 和 sub-agent 内部。

### `session/cancel` vs `$/cancel_request` —— 两种取消的区别

ACP 有两套不同粒度的取消机制，经常被混淆。**一句话区别**：`session/cancel` 取消"**一整个 prompt turn**"，`$/cancel_request` 取消"**一个具体的 JSON-RPC 请求**"。

| | `session/cancel` | `$/cancel_request` |
|---|---|---|
| **目标** | 一个 session 的 prompt turn | 一个具体的 in-flight 请求（按 requestId）|
| **粒度** | 粗 —— 整个 turn 和它 spawn 的所有子操作 | 细 —— 单个请求 |
| **方向** | Client → Agent 单向 | 双向（两边都可发）|
| **触发场景** | "用户按了停止键" | "我不再需要这个具体请求的响应了" |
| **ACP 地位** | 正式标准，Mandatory | RFD 阶段，Optional |
| **标识方式** | `params.sessionId` | `params.requestId` |
| **响应** | 无响应（notification）+ 原 prompt 以 `stopReason: "cancelled"` 结束 | 无响应 + 被取消请求以 `-32800` error 结束 |

**为什么有 `session/cancel` 还要 `$/cancel_request`**：当 session/cancel 命中一个还在 fan-out 的 prompt turn 时，agent 可能已经对 client 发了几个 nested request（`fs/read_text_file`、`session/request_permission` 等）。Agent 要通知 client："我不再需要这些 nested request 的响应了，你可以停止处理"。这时候 `session/cancel` 不够精细 —— 它只说"停这个 session"，但 client 那边具体哪几个请求该放弃？需要 agent 逐个对每个 pending 的 nested request 发 `$/cancel_request`，client 才知道这些可以 abort。

**级联取消的典型流程**（RFD 里的图）：

```
1. Client → Agent:  session/prompt (id=1)
2. Agent → Client:  fs/read_text_file (id=2)
3. Agent → Client:  session/request_permission (id=3)
4. Client → Agent:  session/cancel {sessionId}
5. Agent → Client:  $/cancel_request {requestId: 2}   ← agent 清理 nested
6. Agent → Client:  $/cancel_request {requestId: 3}
7. Client → Agent:  error -32800 对 id=2              ← client 回错误
8. Client → Agent:  error -32800 对 id=3
9. Agent → Client:  response to id=1 { stopReason: "cancelled" }
```

### `$/cancel_request` —— 一期不实现的原因

在我们当前的 kernel 设计下，上面那个级联流程**几乎用不上**：

1. **我们一期不发 `fs/*` / `terminal/*`** —— agent → client 方向的 nested request 只剩 `session/request_permission` 一种
2. **同时只会有 1 个 pending permission request**（tool 串行执行）
3. **那个 permission 被 abandon 的代价接近 0** —— client 的 UI 就是关掉许可对话框，不需要后端发 `$/cancel_request` 告知
4. **Agent 侧的 outgoing_in_flight map 自动清理** —— `ProtocolAPI.request` 的 `finally` 块在 CancelledError 冒出时 pop 掉 Future

所以一期不实现 `$/cancel_request`，现象等价于"client 发了 $/cancel_request 给 kernel → kernel 忽略（因为该方法未注册）"。ACP 规范**明确允许**忽略未识别的 `$/` 开头通知，这是合规的。

### `$/cancel_request` —— 二期实装清单

当我们二期开始用 `fs/*` / `terminal/*`、或者 permission 开始支持并发请求时，会出现 nested request 被 abandon 造成 client 资源浪费的实际问题。那时候补上 `$/cancel_request`：

1. **注册到 `NOTIFICATION_DISPATCH`** —— 加入 `$/cancel_request` entry
2. **双向处理**：
   - 收到 client 发来的 $/cancel_request：在 incoming in-flight map 里按 requestId 找 task → cancel，handler 最终返回 `-32800` error response
   - Agent 主动 cancel outgoing request：`ProtocolAPI` 新增 `cancel_request(request_id)` 方法，向 client 发 notification，同时立即在 `outgoing_in_flight` map 里清掉对应的 Future
3. **会话层级联**：SessionHandler 的 prompt task 在被 cancel 的 finally 块里，对所有由本 prompt spawn 出去的 outgoing request 发 `$/cancel_request`
4. **能力声明**：采用 `agentCapabilities._meta.mustang/cancelRequest: true`
   - 用我们的命名空间 `mustang/` 避免和将来 ACP 正式转正的字段冲突
   - 如果后来 ACP 转正成 `agentCapabilities.cancelRequest` 这样的顶层字段，我们再做一次迁移即可
   - 例子：
     ```json
     {
       "agentCapabilities": {
         "loadSession": true,
         "_meta": {
           "mustang/cancelRequest": true
         }
       }
     }
     ```

## 错误映射

Handler 抛出的异常会被协议层的 dispatch 包装器捕获，转换成 JSON-RPC error response。**只使用 ACP `ErrorCode` enum 里定义的值**，不要随意编数字（ACP schema.json 的 `ErrorCode` 是我们的权威来源）。

### ACP 定义的错误码（我们使用）

| ACP 码 | 含义 | 我们什么时候用 |
|---|---|---|
| `-32700` | Parse error | JSON 无法解析 |
| `-32600` | Invalid Request | 缺 `method` / `jsonrpc`、在 initialize 之前发其他方法、状态机违规 |
| `-32601` | Method not found | 不在 DISPATCH 里的 method（包括未知 `_` 扩展）|
| `-32602` | Invalid params | params 不合 schema（Pydantic 验证失败）|
| `-32603` | Internal error | 兜底，消息永远是通用的 "Internal error"，具体原因只进 log |
| `-32000` | Authentication required | ACP 专用。理论上我们不发 —— 我们在传输层认证、`authMethods: []`、所有连接都已认证 |
| `-32002` | Resource not found | ACP 专用，文件 / session / tool 不存在 |

### 异常 → 错误码映射

| 异常 | 使用的 code | 备注 |
|---|---|---|
| `ParseError`（协议层 JSON 解析失败） | `-32700` | |
| `InvalidRequest`（缺 `method` / `jsonrpc`、或在 initialize 之前调其他方法） | `-32600` | |
| `MethodNotFound`（不在 DISPATCH 里） | `-32601` | `_` 开头的未知扩展也用这个（ACP 规定）|
| `pydantic.ValidationError`（params 校验失败） | `-32602` | 消息里**不**包含原始 params 内容（防信息泄漏）|
| `SessionNotFoundError` / `ToolCallNotFoundError` | `-32002` | ACP 的 "Resource not found" 含义包括 session / tool 这类资源 |
| `InternalError` / 任何未捕获异常 | `-32603` | 兜底。消息永远是通用 "Internal error"，具体原因只进 log |

### 故意不用的码

| 码 | 为什么不用 |
|---|---|
| `-32000` Authentication required | 我们在传输层已经认证；协议层不会遇到"未认证"状态 |
| `-32800` Request cancelled | 来自 [`$/cancel_request` RFD](../references/acp/rfds/request-cancellation.md)，**不在 ACP stable schema 里**。我们一期不实现 `$/cancel_request`，也不使用 `-32800`。二期实现时再加 |

### Cancel 的特殊处理

**Cancel 本身不走 error 通道**：

- `session/prompt` 被 `session/cancel` 取消时，返回 `PromptResponse { stopReason: "cancelled" }` 作为**成功响应**，**不**返回 error
- 这是 ACP 规范明确要求的 —— 如果把 cancel 当 error 回复，client 会把它显示成失败，而不是"用户主动取消"
- 其他 in-flight 请求（如 `session/load` 正在回放历史）一期**不支持被取消**。如果客户端真的需要强行打断，只能断开 WebSocket
- 二期实现 `$/cancel_request` 后，非 prompt 的 request 取消会返回 `-32800`

### 错误消息的信息脱敏

- **内部错误的具体原因不写进 error message** —— 防止栈 / 路径 / 密钥意外泄漏给 client
- 异常 message 只记 debug 级 log，JSON-RPC error response 永远是通用描述
- 特别注意：`pydantic.ValidationError` 的默认 message 里可能包含原始参数值，要剥掉再写进 error response

## `_meta` 扩展

ACP 所有消息类型都有 `_meta: { [key: string]: unknown }` 字段，用于：

1. **跨进程 trace 关联** —— 保留 W3C Trace Context 字段：
   - `traceparent`
   - `tracestate`
   - `baggage`

2. **我们的扩展** —— 放在命名空间 `mustang/` 下，防止和 ACP 或其他 agent 的扩展冲突。

### 一期使用的 `_meta` 字段

| Key | 出现位置 | 用途 |
|---|---|---|
| `mustang/agent_start` | `session/update._meta` | 子 agent 启动通知（ACP 无原生对应）|
| `mustang/agent_end` | `session/update._meta` | 子 agent 结束 |
| `mustang/token_usage` | `PromptResponse._meta` | 本次 turn 的 token 统计 |
| `mustang/context_usage` | `session/update._meta` | 上下文使用率（百分比）|
| `traceparent` / `tracestate` / `baggage` | 任意 request 的 `_meta` | W3C Trace Context |

### 将来可能的扩展

- `mustang/memory_read` / `mustang/memory_write` —— Memory 系统事件
- `mustang/compaction` —— Context compaction 通知

**规约**：所有我们自定义的 key 都以 `mustang/` 开头，避免碰撞。不要在 `_meta` 根上放裸 key（ACP 规范禁止）。

### 自定义方法（以 `_` 开头）

除了 `_meta`，ACP 还允许以 `_` 开头的自定义方法名。一期不使用。如果将来需要（比如管理端点），用格式 `_mustang.truenorth/<method>`。

## 实现位置

协议层的代码组织建议（和文档对应）：

```
kernel/protocol/
  __init__.py              # 导出 ProtocolLayer 主入口
  dispatch.py              # REQUEST_DISPATCH / NOTIFICATION_DISPATCH / OUTGOING_METHODS
  schemas/                 # Pydantic 复刻 ACP schema.json
    __init__.py
    initialize.py          # InitializeRequest / InitializeResponse
    session.py             # NewSessionRequest / LoadSessionRequest / PromptRequest / ...
    tool_call.py           # tool_call / tool_call_update session update 变体
    content.py             # ContentBlock union
    permission.py          # RequestPermissionRequest / Response
    enums.py               # StopReason / ToolKind / ToolCallStatus / PermissionOptionKind
  api.py                   # ProtocolAPI concrete implementation
  context.py               # HandlerContext, ConnectionContext
  errors.py                # JSON-RPC error codes + exception mapping
  handshake.py             # initialize / authenticate handlers (protocol-owned)
  event_mapper.py          # Orchestrator event → session/update 变体
```

**不**在协议层的代码：

- WebSocket IO → `kernel/transport/`
- Session 业务逻辑 → `kernel/session/` (SessionManager 实现 SessionHandler Protocol)
- Tool 执行 → `kernel/tools/` (通过 ProtocolAPI.request 发 permission request)

## Open Questions (TODO)

protocol.md 当前版本里没有悬而未决的**设计**问题。所有 ACP 规范里有答案的和需要我们拍板的都已经落进文档。

以下是**实装阶段**才会遇到的细节（不阻塞设计定稿）：

1. **真正的 backpressure 策略** —— 当 client 消费持续跟不上 orchestrator 产出时（罕见场景，但远程 client 可能遇到），除了已有的[批处理](#流式通知的批处理batching)还需要额外机制（send buffer 上限 / drop 低优先级 / 阻塞生产者 / 断连）。一期不做，留给传输层实装时评估
2. **`$/cancel_request` 二期实装细节** —— 能力声明位置已决定（`agentCapabilities._meta.mustang/cancelRequest: true`），但具体什么时候切换到这条路径（开始用 `fs/*` 时？还是更早？）、以及级联 cancel 的触发边界，等到二期实装 `fs/*` 时再决定

### 已解决的问题（设计决策历史）

以下问题在讨论中解决，列在这里作为设计决策的记录，避免将来重新发现：

- ~~**`agentInfo` 具体值**~~ —— `name: "mustang-kernel"` / `title: "DeepCLI"` / `version: <pyproject.toml>`，不标环境
- ~~**Protocol 层 logging 策略**~~ —— 分级（INFO 元数据、DEBUG 全帧且默认关闭）+ 不提供 config.yaml 开关
- ~~**批处理 vs backpressure**~~ —— 批处理是优化不是 backpressure，只合并三个 `*_chunk` variant，50ms 窗口；真正的 backpressure 留给二期
- ~~**`$/cancel_request` 能力声明位置**~~ —— 用 `agentCapabilities._meta.mustang/cancelRequest: true`
- ~~**"initialize 之前收到其他方法"的错误码**~~ —— 用 `-32600` Invalid Request。ACP 的 `-32002` 是 "Resource not found"（不是 LSP 的 ServerNotInitialized），ACP 没有专门的 "未初始化" 码，我们选最贴近语义的标准 JSON-RPC `-32600`
- ~~**`thought_chunk` session update 变体**~~ —— 确认存在，`agent_thought_chunk`，payload 是 `ContentChunk`
- ~~**`available_commands_update` 和 skill 系统的对应关系**~~ —— `AvailableCommand` schema (`name` / `description` / `_meta`) 和 skill metadata 一一对应，SkillRegistry 在 skill 列表变化时 emit
- ~~**Pagination cursor 实现**~~ —— ACP 明确规定 cursor 是 **opaque token**，client MUST NOT parse / modify / persist。协议层不关心具体实现，是 Session 子系统的自由度
- ~~**`$/cancel_request` 的错误码 `-32800`**~~ —— 一期不实现 `$/cancel_request`，也就不用 `-32800`。`session/prompt` 的取消走 `stopReason: "cancelled"`（成功响应而非 error），其他 in-flight request 一期无法被取消

## Related

- [../architecture.md](../architecture.md) —— 传输层 / 协议层 / 会话层三层分工
- [../subsystems/connection_authenticator.md](../subsystems/connection_authenticator.md) —— 传输层认证、AuthContext / ConnectionContext 定义
- [../subsystems/config.md](../subsystems/config.md) —— Signal 基础设施（event mapper 用同样的模式）
- [../references/acp/](../references/acp/) —— ACP 规范本地镜像
