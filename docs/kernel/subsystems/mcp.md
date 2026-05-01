# MCPManager — Design

Status: **pending** — 参考 Claude Code main 源码设计，尚未实装。

> 前置阅读：
> - 架构子系统表：[kernel/architecture.md](../../kernel/architecture.md)
> - ToolManager 设计 §4.3 startup / §10 phase 6：[tools.md](tools.md)
> - Claude Code MCP 实现：`claude-code-main/src/services/mcp/client.ts`、`types.ts`、`config.ts`
> - Claude Code MCPTool：`claude-code-main/src/tools/MCPTool/MCPTool.ts`

---

## 1. 核心概念

**MCPManager 是 MCP server 连接的生命周期管理器**。它建连、重连、监控健康、关闭连接，向 ToolManager 暴露 live connections。

它**做**：
1. 加载 MCP server 配置（ConfigManager 三层 + `.mcp.json` 兼容）
2. 按 transport 类型建立连接（stdio / SSE / HTTP / WebSocket）
3. 维护每个 server 的连接状态机（connected / failed / pending / needs-auth / disabled）
4. 健康监控 + 自动重连（指数退避）
5. 暴露 `on_tools_changed: Signal` 通知 ToolManager 连接变化
6. 提供 `call_tool()` 供 MCPAdapter 使用

它**不**做：
- Tool 发现/注册（ToolManager 通过 MCPAdapter 负责）
- 权限判定（ToolAuthorizer 负责）
- Tool schema 缓存 / deferred 分区（ToolManager + ToolRegistry 负责）

---

## 2. 职责边界

| Claude Code 模块 | mustang 归属 | 说明 |
|---|---|---|
| `connectToServer()` | ✅ MCPManager | 单 server 建连，返回 `MCPServerConnection` union |
| `getMcpToolsCommandsAndResources()` | ✅ MCPManager（建连编排）+ ToolManager（tool 注册） | CC 合在一起，mustang 分开 |
| `fetchToolsForClient()` | ✅ MCPManager 暴露方法，ToolManager._sync_mcp() 调用 | MCPManager 提供 `list_tools(server)`，ToolManager 消费 |
| `callMCPTool()` | ✅ MCPManager | MCPAdapter.call() 委托给 MCPManager.call_tool() |
| `reconnectMcpServerImpl()` | ✅ MCPManager | 清缓存 → 重连 → emit signal |
| `MCPTool` wrapper | ❌ 不在 MCPManager | → `tools/mcp_adapter.py` (MCPAdapter)，属 ToolManager 领域 |
| `buildMcpToolName()` | ❌ 不在 MCPManager | → MCPAdapter 使用的 naming util |
| Config loading + policy | ✅ MCPManager | `getClaudeCodeMcpConfigs` + `filterMcpServersByPolicy` |
| `useManageMCPConnections` (React hook) | ❌ 不移植 | 前端状态管理，kernel 不需要 |

---

## 3. 连接状态机

对标 Claude Code `types.ts` 的 `MCPServerConnection` union：

```
                    ┌───────────┐
          connect   │  Pending  │  reconnect attempt
          ┌────────►│           │◄────────────┐
          │         └─────┬─────┘             │
          │               │                   │
          │          success / fail            │
          │           ┌───┴───┐               │
          │           ▼       ▼               │
     ┌────┴────┐ ┌─────────┐ ┌────────┐      │
     │Connected│ │  Failed  │ │NeedsAuth│     │
     │         │ │          │ │         │     │
     └────┬────┘ └────┬─────┘ └─────────┘    │
          │           │ health check          │
          │ error     └───────────────────────┘
          │
          ▼
     ┌─────────┐
     │  Failed  │───health check───► reconnect
     └─────────┘

     ┌──────────┐
     │ Disabled │  (policy gated, no action)
     └──────────┘
```

```python
# kernel/mcp/types.py

@dataclass(frozen=True)
class ConnectedServer:
    """活跃连接。"""
    name: str
    client: McpClient          # JSON-RPC session
    capabilities: dict[str, Any]
    server_info: dict[str, Any] | None
    config: ScopedServerConfig
    instructions: str | None   # server 返回的 instructions 元数据

@dataclass(frozen=True)
class FailedServer:
    """连接失败。"""
    name: str
    config: ScopedServerConfig
    error: str

@dataclass(frozen=True)
class PendingServer:
    """重连中。"""
    name: str
    config: ScopedServerConfig
    reconnect_attempt: int
    max_reconnect_attempts: int

@dataclass(frozen=True)
class NeedsAuthServer:
    """等待用户授权（OAuth）。"""
    name: str
    config: ScopedServerConfig

@dataclass(frozen=True)
class DisabledServer:
    """被 policy 禁用。"""
    name: str
    config: ScopedServerConfig

MCPServerConnection = (
    ConnectedServer | FailedServer | PendingServer
    | NeedsAuthServer | DisabledServer
)
```

---

## 4. 配置

### 4.1 配置格式

对标 Claude Code `config.ts` 的 `McpServerConfig`，只支持 stdio 和 SSE 两种 transport（覆盖 >95% 使用场景）：

```python
# kernel/mcp/config.py

class StdioServerConfig(BaseModel):
    """stdio transport — 启动本地子进程。"""
    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

class SSEServerConfig(BaseModel):
    """SSE transport — 连接远程 HTTP SSE 端点。"""
    type: Literal["sse"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)

class HTTPServerConfig(BaseModel):
    """Streamable HTTP transport — 新一代远程 MCP 传输。"""
    type: Literal["http"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)

class WebSocketServerConfig(BaseModel):
    """WebSocket transport — 全双工远程连接。"""
    type: Literal["ws"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)

ServerConfig = Annotated[
    StdioServerConfig | SSEServerConfig | HTTPServerConfig | WebSocketServerConfig,
    Field(discriminator="type"),
]

class ScopedServerConfig(BaseModel):
    """ServerConfig + 来源 scope。"""
    config: ServerConfig
    scope: Literal["global", "project", "local", "mcp_json"]

class MCPConfig(BaseModel):
    """ConfigManager section: file='mcp', section='mcp'。"""
    servers: dict[str, ServerConfig] = Field(default_factory=dict)
```

### 4.2 配置来源与优先级

走 ConfigManager 三层体系 + `.mcp.json` 兼容：

| 优先级 | 来源 | 路径 |
|--------|------|------|
| 1（最高） | local | `<cwd>/.mustang/config/mcp.local.yaml` |
| 2 | project | `<cwd>/.mustang/config/mcp.yaml` |
| 3 | global | `~/.mustang/config/mcp.yaml` |
| 4（最低） | .mcp.json | `<cwd>/.mcp.json`（Claude Code 约定，转换合入） |

ConfigManager 的 `deep_merge` 处理前三层。`.mcp.json` 在 MCPManager startup 时单独加载，转换为 `ServerConfig`，name 不冲突时合入。

### 4.3 Policy 过滤

对标 Claude Code 的 `allowedMcpServers` / `deniedMcpServers`：

```python
class MCPPolicyConfig(BaseModel):
    """ConfigManager section: file='config', section='mcp_policy'。"""
    allowed_servers: list[str] | None = None  # None = allow all
    denied_servers: list[str] = Field(default_factory=list)
```

- `denied_servers` 绝对优先
- `allowed_servers` 为 `None` → 全部允许；为 `[]` → 全部禁止
- 匹配按 server name

### 4.4 .mcp.json 格式

兼容 Claude Code 项目约定，JSON 格式：

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@some/mcp-server"],
      "env": { "API_KEY": "${API_KEY}" }
    }
  }
}
```

环境变量展开：`$VAR` 和 `${VAR}` 语法，对标 Claude Code 的 `expandEnvVarsInString()`。

---

## 5. Transport 层

### 5.1 Transport Protocol

```python
# kernel/mcp/transport/base.py

class Transport(Protocol):
    """MCP 传输层抽象。"""

    async def connect(self) -> None:
        """建立连接。"""
        ...

    async def send(self, message: bytes) -> None:
        """发送一帧 JSON-RPC 消息。"""
        ...

    async def receive(self) -> bytes:
        """阻塞读取下一帧 JSON-RPC 消息。EOF → raise TransportClosed。"""
        ...

    async def close(self) -> None:
        """优雅关闭。"""
        ...

    @property
    def is_connected(self) -> bool: ...
```

### 5.2 StdioTransport

对标 Claude Code 使用 `@modelcontextprotocol/sdk` 的 `StdioClientTransport`：

- `asyncio.create_subprocess_exec()` 启动子进程
- LSP `Content-Length` 帧分割（`Content-Length: N\r\n\r\n<body>`）
- stderr 后台读取，上限 64MB（对标 CC，防内存泄漏）
- 环境变量展开（`$VAR`、`${VAR}`）
- 优雅关闭：stdin EOF → 5s 等待 exit → SIGTERM → 2s → SIGKILL

### 5.3 SSETransport

对标 Claude Code 的 `SSEClientTransport`：

- HTTP GET 建立 SSE 长连接，POST 发送请求
- Endpoint 动态发现：监听首个 SSE `endpoint` 事件获取 POST URL
- 请求超时：POST 60s（对标 CC 的 `wrapFetchWithTimeout`）
- SSE 流不设读超时（长连接）
- HTTP client：`httpx.AsyncClient`（async、连接池、超时控制）

### 5.4 HTTPTransport (Streamable HTTP)

对标 Claude Code 的 `StreamableHTTPClientTransport`：

- MCP spec 中 SSE 的替代方案，新 MCP server 优先支持此 transport
- 单个 HTTP endpoint，请求/响应都走 POST
- 支持 server-initiated SSE 推送（双向通信）
- 同样使用 `httpx.AsyncClient`
- 超时策略同 SSE：请求 60s，流式响应不设超时

### 5.5 WebSocketTransport

对标 Claude Code 的 `WebSocketTransport`：

- 标准 WebSocket 全双工连接
- 每帧一个 JSON-RPC 消息（text frame），无需 Content-Length framing
- Python 侧使用 `websockets` 库
- 连接超时 15s
- 支持自定义 headers

### 5.6 不实现

- **SDK**：CC 内部路由模式（把 tool call 路由回父应用），kernel 不需要。

---

## 6. Client 层

### 6.1 McpClient

JSON-RPC 2.0 协议层，对标 Claude Code 使用 `@modelcontextprotocol/sdk` 的 `Client`：

```python
# kernel/mcp/client.py

class McpClient:
    """Transport-agnostic MCP client session。"""

    def __init__(self, transport: Transport, *, server_name: str) -> None: ...

    async def connect(self) -> ServerCapabilities:
        """执行 MCP handshake (initialize → initialized)。"""

    async def close(self) -> None:
        """优雅关闭 transport。"""

    async def list_tools(self) -> list[McpToolDef]:
        """请求 tools/list。"""

    async def call_tool(
        self, name: str, arguments: dict[str, Any],
        *, timeout: float | None = None,
    ) -> McpToolResult:
        """请求 tools/call。"""

    async def list_resources(self) -> list[McpResourceDef]: ...
    async def read_resource(self, uri: str) -> McpResourceResult: ...

    @property
    def is_connected(self) -> bool: ...
    @property
    def capabilities(self) -> dict[str, Any]: ...
    @property
    def server_info(self) -> dict[str, Any] | None: ...
```

**Protocol 细节**：
- JSON-RPC correlation：递增 request ID → `asyncio.Future` map
- Request timeout：30s 默认（对标 CC）
- Tool call timeout：独立配置，默认极大值（对标 CC 的 ~27.8h）
- Handshake：发送 `{"method": "initialize", "params": {...}}`，存储 `capabilities` 和 `server_info`，发送 `initialized` notification

### 6.2 连接管理函数

对标 Claude Code `client.ts` 的顶层函数：

```python
async def connect_to_server(
    name: str,
    config: ScopedServerConfig,
) -> MCPServerConnection:
    """建立单个 MCP server 连接。
    
    1. 创建 transport (create_transport)
    2. 创建 McpClient, 执行 handshake
    3. 成功 → ConnectedServer
    4. 认证失败 → NeedsAuthServer
    5. 其他异常 → FailedServer
    """

async def reconnect_server(
    name: str,
    config: ScopedServerConfig,
) -> MCPServerConnection:
    """清缓存 → 重连。对标 CC reconnectMcpServerImpl。"""
```

### 6.3 Error 类型

```python
class McpError(Exception):
    """通用 MCP 协议错误。"""
    code: int | None
    message: str

class TransportClosed(Exception):
    """Transport EOF / 连接断开。"""

class McpAuthError(McpError):
    """401 认证失败。"""
    server_name: str

class McpSessionExpiredError(McpError):
    """404 + JSON-RPC code -32001。对标 CC 的 isMcpSessionExpiredError()。"""

class McpToolCallError(McpError):
    """tool call 返回 isError: true。"""
```

---

## 7. MCPManager Subsystem

### 7.1 接口

```python
# kernel/mcp/__init__.py

class MCPManager(Subsystem):
    """MCP server 连接生命周期管理。"""

    # --- 公开 API ---

    @property
    def on_tools_changed(self) -> Signal:
        """ToolManager.startup() 连接此 signal。
        
        在以下时机 emit：
        - startup 完成所有 server 建连后
        - 健康检查成功重连 failed server 后
        - config hot-reload 导致 server 增删后
        """

    def get_connections(self) -> dict[str, MCPServerConnection]:
        """返回所有连接的当前状态快照。"""

    def get_connected(self) -> list[ConnectedServer]:
        """便捷方法：只返回 connected 状态的 server。"""

    async def list_tools(self, server_name: str) -> list[McpToolDef]:
        """从指定 connected server 获取 tool 列表。"""

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpToolResult:
        """调用指定 server 上的 tool。MCPAdapter.call() 委托到此。"""

    async def reconnect(self, server_name: str) -> MCPServerConnection:
        """手动触发指定 server 重连。"""
```

### 7.2 Startup 流程

```python
async def startup(self) -> None:
    # 1. Bind config section
    self._section = self._module_table.config.bind_section(
        file="mcp", section="servers", schema=MCPConfig,
    )
    cfg = self._section.get()

    # 2. 加载 .mcp.json（如果存在），合入 cfg
    mcp_json_servers = load_mcp_json(Path.cwd() / ".mcp.json")
    all_servers = merge_configs(cfg.servers, mcp_json_servers)

    # 3. Policy 过滤
    allowed, disabled = filter_by_policy(all_servers)

    # 4. 记录 disabled servers
    for name, config in disabled.items():
        self._connections[name] = DisabledServer(name=name, config=config)

    # 5. 并发建连（分 local/remote 两批，不同并发度）
    #    对标 CC: local(stdio) concurrency=3, remote(sse) concurrency=20
    await self._connect_batch(allowed)

    # 6. Emit signal 通知 ToolManager
    await self._on_tools_changed.emit()

    # 7. 启动健康检查
    self._health_task = asyncio.create_task(self._health_loop())

    # 8. 订阅 config 变更（热更新）
    self._disconnect_config = self._section.changed.connect(
        self._on_config_changed,
    )
```

### 7.3 并发建连

对标 Claude Code `getMcpToolsCommandsAndResources()` 的 `pMap` 模式：

```python
async def _connect_batch(
    self,
    servers: dict[str, ScopedServerConfig],
) -> None:
    """按 transport 类型分批并发建连。"""
    local = {n: c for n, c in servers.items()
             if c.config.type == "stdio"}
    remote = {n: c for n, c in servers.items()
              if c.config.type != "stdio"}

    # asyncio.Semaphore 控制并发度
    await asyncio.gather(
        self._connect_with_limit(local, max_concurrency=3),
        self._connect_with_limit(remote, max_concurrency=20),
    )
```

### 7.4 健康检查

对标 Claude Code 的 error tracking + reconnect：

```python
async def _health_loop(self) -> None:
    """周期检查 failed servers，尝试重连。"""
    while True:
        await asyncio.sleep(60)
        changed = False
        for name, conn in list(self._connections.items()):
            if not isinstance(conn, FailedServer):
                continue
            new_conn = await connect_to_server(name, conn.config)
            self._connections[name] = new_conn
            if isinstance(new_conn, ConnectedServer):
                changed = True
                logger.info("MCPManager: reconnected %s", name)
        if changed:
            await self._on_tools_changed.emit()
```

### 7.5 Config 热更新

```python
async def _on_config_changed(
    self, old: MCPConfig, new: MCPConfig,
) -> None:
    """Config 变更时增量更新连接。"""
    old_names = set(old.servers)
    new_names = set(new.servers)

    # 关闭已移除的 server
    for name in old_names - new_names:
        conn = self._connections.pop(name, None)
        if isinstance(conn, ConnectedServer):
            await conn.client.close()

    # 连接新增的 server
    added = {n: ScopedServerConfig(config=new.servers[n], scope="project")
             for n in new_names - old_names}
    if added:
        await self._connect_batch(added)

    if old_names != new_names:
        await self._on_tools_changed.emit()
```

### 7.6 Shutdown

```python
async def shutdown(self) -> None:
    # 1. Cancel health monitor
    if self._health_task and not self._health_task.done():
        self._health_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._health_task

    # 2. Close all connected clients
    close_tasks = [
        conn.client.close()
        for conn in self._connections.values()
        if isinstance(conn, ConnectedServer)
    ]
    await asyncio.gather(*close_tasks, return_exceptions=True)

    # 3. Disconnect config signal
    if self._disconnect_config:
        self._disconnect_config()

    self._connections.clear()
```

---

## 8. MCPAdapter（ToolManager 侧）

> 此文件属 ToolManager 领域，不在 MCPManager 包内。

### 8.1 MCPAdapter

```python
# kernel/tools/mcp_adapter.py

class MCPAdapter(Tool):
    """把单个 MCP tool 包装成 kernel Tool。"""

    is_mcp: ClassVar[bool] = True
    should_defer: ClassVar[bool] = True   # MCP tools 默认 deferred
    kind: ClassVar[ToolKind] = ToolKind.mcp

    def __init__(
        self,
        server_name: str,
        tool_def: McpToolDef,
        mcp_manager: MCPManager,
    ) -> None:
        self._server_name = server_name
        self._tool_def = tool_def
        self._mcp = mcp_manager
        # 对标 CC buildMcpToolName(): mcp__{server}__{tool}
        self.name = build_mcp_tool_name(server_name, tool_def.name)
        self._original_tool_name = tool_def.name

    def to_schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self._tool_def.description[:2048],
            input_schema=self._tool_def.input_schema,
        )

    async def call(
        self, input: dict[str, Any], ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        result = await self._mcp.call_tool(
            self._server_name, self._original_tool_name, input,
        )
        yield ToolCallResult(
            llm_content=[TextContent(text=extract_text_content(result))],
        )
```

### 8.2 Tool Naming

对标 Claude Code `mcpStringUtils.ts` 的 `buildMcpToolName()`：

```python
def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """mcp__{normalized_server}__{normalized_tool}"""
    return f"mcp__{_normalize(server_name)}__{_normalize(tool_name)}"

def _normalize(s: str) -> str:
    """保留 [a-zA-Z0-9_-]，其余替换为 _。"""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)
```

### 8.3 ToolManager._sync_mcp()

对标 tool-manager.md §4.3 设计中的 `_sync_mcp`：

```python
async def _sync_mcp(self) -> None:
    """MCPManager.on_tools_changed signal handler。"""
    mcp = self._module_table.get(MCPManager)

    # 1. 清除旧 MCP tools
    self._registry.unregister_by_predicate(lambda t: t.is_mcp)

    # 2. 从每个 connected server 拉 tools，创建 adapter
    for server in mcp.get_connected():
        tools = await mcp.list_tools(server.name)
        for tool_def in tools:
            adapter = MCPAdapter(server.name, tool_def, mcp)
            self._registry.register(adapter, layer="deferred")
```

---

## 9. 与其他子系统的交互

| 子系统 | 交互方式 | 方向 |
|--------|---------|------|
| **ConfigManager** | `bind_section(file="mcp", ...)` + `changed` signal | MCPManager → ConfigManager |
| **ToolManager** | `on_tools_changed` signal → `_sync_mcp()` | MCPManager → ToolManager |
| **ToolAuthorizer** | MCP tool 名 `mcp__server__tool` 支持 server-level deny rule | 间接（通过 tool name 约定） |
| **ToolExecutor** | MCPAdapter.call() → MCPManager.call_tool() | ToolExecutor → MCPAdapter → MCPManager |
| **HookManager** | `pre_tool_use` / `post_tool_use` 正常触发（MCP tool 无特殊处理） | 不直接交互 |

依赖方向：

```
ConfigManager ←── MCPManager ←── ToolManager ←── Orchestrator
                       ↑
                  MCPAdapter (call_tool)
```

---

## 10. 分阶段实施

| Phase | 内容 | 产出文件 | 依赖 |
|-------|------|---------|------|
| **1** | Transport 层 + McpClient + jsonrpc | `transport/*`, `client.py`, `types.py` | 无 |
| **2** | Config 加载 + .mcp.json 兼容 | `config.py` | Phase 1 |
| **3** | MCPManager subsystem 实装 | `__init__.py`, `health.py` | Phase 1-2 |
| **4** | MCPAdapter + ToolManager._sync_mcp | `tools/mcp_adapter.py` | Phase 3 |
| **5** | 测试 | `tests/kernel/mcp/*` | Phase 1-4 |

Phase 1-3 是 MCPManager 自身；Phase 4 是 ToolManager 侧接线；Phase 5 贯穿。

---

## 11. Claude Code 源码映射

| mustang 模块 | Claude Code 对应 | 说明 |
|---|---|---|
| `mcp/types.py` | `services/mcp/types.ts` | 连接状态 union + error 类 |
| `mcp/config.py` | `services/mcp/config.ts` §getMcpServers | 配置加载、policy、.mcp.json |
| `mcp/transport/stdio.py` | SDK `StdioClientTransport` | Python asyncio 重写 |
| `mcp/transport/sse.py` | SDK `SSEClientTransport` | httpx + SSE 解析 |
| `mcp/transport/http.py` | SDK `StreamableHTTPClientTransport` | httpx + Streamable HTTP |
| `mcp/transport/ws.py` | CC `WebSocketTransport` | websockets 库 |
| `mcp/client.py` | `services/mcp/client.ts` §connectToServer | McpClient + 建连函数 |
| `mcp/__init__.py` | `services/mcp/client.ts` §getMcpToolsCommandsAndResources | 编排层 Subsystem 化 |
| `mcp/health.py` | `services/mcp/client.ts` §error tracking + reconnect | 健康检查提取为独立模块 |
| `tools/mcp_adapter.py` | `tools/MCPTool/MCPTool.ts` | MCP tool → Tool ABC |

---

## 12. 验证方法

### 12.1 单元测试（`tests/kernel/mcp/`）

Mock transport，不启动真实进程。覆盖：

- **JsonRPC**：response 分发、notification 忽略、超时、reject_all（`test_jsonrpc.py`）
- **McpClient**：handshake、list_tools、call_tool、isError、request timeout、close rejects pending（`test_client.py`）
- **Config**：Pydantic schema 校验、.mcp.json 加载（stdio/sse/http/ws/invalid/missing）、合并优先级、policy allow/deny（`test_config.py`）
- **MCPManager**：空配置 startup、failed server 降级、signal emit、shutdown 幂等（`test_manager.py`）
- **StdioTransport**：env var 展开、connect/close、send/receive round-trip、stderr 捕获（`transport/test_stdio.py`）
- **MCPAdapter**：tool naming、description 截断、to_schema、call 委托、default_risk（`tests/kernel/tools/test_mcp_adapter.py`）

### 12.2 端到端测试（`tests/e2e/test_mcp_e2e.py`）

**必须通过真实 kernel subprocess + probe 驱动**，不可直接实例化 subsystem。验证完整 `app.py` lifespan 启动顺序 + signal 接线。

1. **kernel 启动**：配置 MCP echo server → kernel subprocess 启动 → health endpoint 200（验证 lifespan 顺序正确，MCPManager 不崩溃）
2. **ACP 握手**：ProbeClient initialize 成功（验证 MCPManager 不破坏 protocol 层）
3. **完整 tool call**：ProbeClient prompt → LLM 看到 `mcp__echo__echo` → 调用 → PermissionRequest auto-approve → MCPAdapter → MCPManager.call_tool → echo server → 结果返回（需要 LLM 配置，无 LLM 时自动 skip）

### 12.3 手动测试（probe 交互）

配置：`~/.mustang/config/mcp.yaml`（走 ConfigManager，不依赖 `.mcp.json`）

```yaml
mcp:
  servers:
    echo:
      type: stdio
      command: python
      args:
        - /path/to/tests/e2e/mcp_echo_server.py
```

启动 + 测试：

```bash
src/run-kernel.sh          # 终端 1
uv run python -m probe     # 终端 2
# prompt: "Use the echo tool to echo hello world"
```

### 12.4 已发现并修复的 bug（e2e 抓出）

| Bug | 根因 | 修法 |
|-----|------|------|
| MCP tools 不在 LLM tool schemas 中 | `_sync_mcp` 注册到 `deferred` 层，但 ToolSearchTool 未实装，deferred tools 不出 schema | 改注册为 `core` 层 |
| ToolManager 启动时没有 MCP tools | MCPManager 先启动并 emit signal，ToolManager 后启动后才连接 signal，错过初始 emit | ToolManager 连接 signal 后立即执行一次 `_sync_mcp()` |
| Permission 弹窗阻塞 prompt | MCP tools default_risk=ask，e2e 测试未处理 PermissionRequest | e2e 测试 auto-approve PermissionRequest |
