# Session ACP 兼容性重构计划

**范围**: `src/kernel/kernel/session/` + `src/kernel/kernel/protocol/acp/`
**状态**: implemented（A/B/C + inbound `$/cancel_request` 已完成；per-session MCP 明确 fail-fast unsupported）
**动机**: 让 Session 子系统更完整地符合 ACP session 语义，支撑 CLI Phase D、
未来 Web / IDE 客户端，以及长期 session 管理。

## 目标

把 kernel 的 Session ACP 表面从“主对话循环可用”整理成“session setup、list、
config、mode、MCP、cancel 和 lifecycle actions 都语义一致且 ACP 兼容”。

这是 kernel 侧计划。CLI 仍然必须保持 thin client，只通过 ACP 消费这些能力。

## 当前缺口

2026-04-28 对照本地 ACP 镜像 `docs/kernel/references/acp/protocol/` 和当前
kernel 代码后的核验结果：

| 领域 | 当前状态 | 缺口 |
|---|---|---|
| `session/new` / `session/load` | 已实现，并通过 `loadSession: true` 声明 | response schema 有 `configOptions` / `modes`，但 handler 没返回初始状态 |
| `session/list` | 已实现，并通过 `sessionCapabilities.list: {}` 声明 | `SessionInfo` 返回 `createdAt`；ACP 期望 `updatedAt` 和可选 `_meta` |
| `session_info_update` | 已支持 title | 缺 `updatedAt`；有 `_meta` 字段但没有结构化 metadata 约定 |
| `session/set_config_option` | request / update 路径存在 | config option 形状只是 `{configId, value}`；ACP 期望完整 selector 描述：`id/name/type/currentValue/options/category` |
| `session/set_mode` | request / update 路径存在 | `session/new/load` 不返回 `modes`；`set_mode` 不校验 `modeId` 是否在 available modes 中 |
| session setup 的 `mcpServers` | 空数组 `[]` 支持；非空数组 fail-fast `InvalidParams` | MCPManager 当前只有 global connection registry，session-scoped MCP 暂不宣称支持 |
| `cwd` | 直接传给 `Path(params.cwd)` | ACP 要求 `cwd` 必须是绝对路径；schema / handler 边界没有 fail-fast 校验 |
| `session/list` cursor | malformed cursor 只 warning，然后从第一页重新返回 | ACP 说 agent SHOULD 对 invalid cursor 返回 error |
| Prompt content | 支持 text / image / resource / resource_link | 需要确保 `_meta` 被保留；如果 capability 不支持某内容类型，不应静默丢弃 |
| `session/cancel` | 已实现 | inbound `$/cancel_request` 已支持，按 request id 取消当前 kernel 正在处理的 JSON-RPC request |
| Lifecycle actions | 已实现 | `session/delete`、`session/rename`、`session/archive` 已通过 ACP 暴露；archive/list filtering 已有 schema v2 |

## 非目标

- 本重构不实现 client-side `fs/*` 或 `terminal/*`；它们已在
  `docs/kernel/interfaces/protocol.md` 中延期。
- 不把业务逻辑移到 CLI。
- 不改变 ACP transport stack 或 WebSocket auth 模型。
- 不重写 orchestrator loop。

## 设计原则

- ACP wire schema 继续放在 `protocol/acp/schemas/*`；session 业务 contract
  继续放在 `protocol/interfaces/contracts/*`；`SessionManager` 不 import ACP wire type。
- 只要 capability 声明某功能可用，对应 response 就必须给 generic ACP client 足够的结构化数据来渲染。
- 会影响持久化或过滤的 metadata 应进入 SQLite，不只藏在 `_meta`。
- schema 变更要集中处理，因为 D21 规定 session `SCHEMA_VERSION` 和 kernel major version 绑定。
- 当前 `src/kernel/kernel/session/` 已完成文件长度重构和 readability 审阅，是已审阅过的结构。
  本计划的所有实现必须沿用现有分层：ACP handler 只放 request 编排，lifecycle 只管 runtime
  创建/加载/关闭，store 只管持久化，runtime helpers/types 放纯函数和状态类型，client_stream
  只管 replay/broadcast/event mapping。不要把新逻辑重新堆回 `manager.py` 或 `api/handlers.py`；
  新增能力如果超过当前文件职责，应按现有目录结构拆到对应模块。

## 目标协议表面

### SessionInfo

收敛到 ACP 形状：

```python
class SessionSummary(BaseModel):
    session_id: str
    cwd: str
    updated_at: str | None
    title: str | None = None
    meta: dict[str, Any] | None = None
```

实现说明：

- 内部仍保留 `created_at`，可选通过 `_meta.createdAt` 暴露。
- 用 `ConversationRecord.modified` 映射 ACP `updatedAt`。
- 只在有用时把轻量 DeepCLI metadata 放进 `_meta`：`createdAt`、
  `totalInputTokens`、`totalOutputTokens`、`archivedAt`、`titleSource`。

### SessionInfoUpdate

扩展为：

```python
title: str | None = None
updated_at: str | None = None
meta: dict[str, Any] | None = None
```

title 改变、archive/unarchive、session metadata 改变时，都应带上 `updatedAt`。

### Config Options

返回 ACP 兼容的 descriptor，而不是只有 value：

```json
{
  "id": "mode",
  "name": "Session Mode",
  "category": "mode",
  "type": "select",
  "currentValue": "default",
  "options": [
    {"value": "default", "name": "Default"},
    {"value": "plan", "name": "Plan"}
  ]
}
```

初始 MVP descriptor：

- `mode`: `default` / `plan`
- `model`: 如果 LLM subsystem 能提供稳定的真实 model/profile 状态，则暴露当前 default/current session model
- `permission_mode`: 只有在能干净映射到现有 ToolAuthorizer modes 时才暴露

如果某个 descriptor 不能从真实 subsystem 状态生成，就先不要宣称支持。

### Modes

为了兼容仍使用 `session/set_mode` 的客户端，保留 legacy `modes`，并让它和
`configOptions` 保持同步。

```json
{
  "currentModeId": "default",
  "availableModes": [
    {"id": "default", "name": "Default"},
    {"id": "plan", "name": "Plan"}
  ]
}
```

`session/set_mode` 必须拒绝未知 `modeId`。

### Per-Session MCP Servers

ACP `session/new` / `session/load` 中的 `mcpServers` 应满足二选一：

- 完整接入 MCPManager，作为 session-scoped connections；或
- 明确文档化为 unsupported，同时继续接受空数组 `[]`。

目标行为是完整支持：

- 把 ACP `mcpServers` entry 转为 MCPManager connection spec。
- connection 生命周期绑定到 session。
- ToolManager snapshot 中，session-scoped MCP tools 只对对应 session 可见。

这项可能比其他重构更大，可以在 ACP shape 修复之后单独拆出来。

## 实现批次

### A — Session Summary 形状与校验

无需 schema 变更。

文件：

- `protocol/acp/schemas/session.py`
- `protocol/acp/schemas/updates.py`
- `protocol/interfaces/contracts/list_sessions_params.py`
- `protocol/interfaces/contracts/list_sessions_result.py`
- `protocol/acp/routing.py`
- `session/api/handlers.py`

工作：

- 给 `SessionSummary` 和 ACP `AcpSessionInfo` 加 `updated_at`。
- 把 `ConversationRecord.modified` 映射成 `updatedAt`。
- 当前 `createdAt` 移到 `_meta.createdAt`，或只留在 internal contracts。
- 给 `SessionInfoUpdate` 加 `updated_at`。
- 校验 `session/new`、`session/load`、`session/list(cwd=...)` 的 `cwd` 必须是绝对路径。
- invalid `cursor` 返回 `InvalidParams`，不再从第一页重新返回。

测试：

- routing test 断言 `session/list` 发出 `updatedAt`。
- SessionManager test 断言相对路径 `cwd` 被拒绝。
- E2E 覆盖 `session/list` ACP shape 和 pagination error path。

Closure seams：

- SessionManager summary projection -> ACP routing serializer。
- Protocol validation -> SessionManager handler error mapping。

### B — Config Options 与 Modes State

除非 model / permission choices 需要持久化默认值，否则无需 schema 变更。

文件：

- `session/runtime/helpers.py`
- 如果 helper 继续膨胀，新增 `session/runtime/config_options.py`
- `protocol/interfaces/contracts/new_session_result.py`
- `protocol/interfaces/contracts/load_session_result.py`
- `protocol/interfaces/contracts/set_config_option_result.py`
- `protocol/acp/schemas/session.py`
- `protocol/acp/routing.py`
- `session/api/handlers.py`

工作：

- 新增 typed internal `ConfigOption` / `ConfigOptionChoice` contract。
- `session/new`、`session/load`、`session/set_config_option`、`config_option_update`
  都返回完整 ACP config option descriptor。
- `session/new` 和 `session/load` 返回 `modes`。
- 保持 `session/set_mode` 和 `session/set_config_option(configId="mode")` 同步。
- 校验未知 mode / config value。
- 只有在能调用真实 LLMManager model/profile 状态时，才接入 model config option；
  不要发明过期 choices。

测试：

- `session/new` response 包含 `configOptions` 和 `modes`。
- `session/load` response 包含恢复后的 current values。
- `set_config_option` 返回完整 descriptor，不只是 changed value。
- `set_mode` 拒绝未知 mode，并在需要时同时广播 mode/config updates。

Closure seams：

- 如果实现 model option：SessionManager -> LLMManager model option provider。
- `set_mode` -> orchestrator mode setter -> config option snapshot。

### C — Session Lifecycle Actions

详细计划见 [`session-lifecycle-actions.md`](session-lifecycle-actions.md)。

状态：已实现。`session/delete` 支持 active-session `force` 保护并删除 sidecar；
`session/rename` 写入 `title_source=user`，自动标题不会覆盖用户标题；
`session/archive` 写入 `archived_at`，`session/list` 支持默认隐藏、`includeArchived`
和 `archivedOnly`。

建议在 A/B 之后整合：

- `session/delete` 可以先落地，不需要 schema bump。
- `session/archive` 和健壮的用户 `session/rename` 应共享一次 schema bump，
  新增 `archived_at` 和 `title_source`。
- A 中的 `session/list` filtering 继续扩展 `includeArchived` / `archivedOnly`。
- A 中的 `SessionInfo.updatedAt` 复用于 lifecycle 变化。

### D — Per-Session MCP Servers

这很可能需要改 MCPManager 和 ToolManager，不只是 SessionManager。

状态：当前明确为 unsupported。`session/new` / `session/load` 继续接受空 `mcpServers: []`；
如果客户端传入非空 session-scoped MCP server，会返回 `InvalidParams`，避免之前“接收并持久化但
工具不可见”的半支持状态。完整支持需要 MCPManager 增加 session-scoped connection registry，
ToolManager snapshot 也要按 session 过滤 MCP tools。

实现前规划任务：

- 阅读 `docs/kernel/subsystems/mcp.md`。
- 确认 MCPManager 当前是否有 session-scoped connection registry，还是只有 global configured servers。
- 决定 ToolManager snapshot 如何过滤 session-scoped MCP tools。
- 定义 session delete、eviction、disconnect 时的 cleanup。

实现目标：

- `session/new/load` 携带非空 `mcpServers` 时连接这些 servers。
- 这些 servers 的 tools/resources 只在对应 session 中可见。
- session close/delete 会关闭对应 scoped server。

测试：

- E2E：带临时 stdio MCP server 的 session 能看到对应 tool/resource。
- 第二个没带该 server 的 session 看不到对应 tool/resource。
- 删除 session 会关闭 scoped server。

### E — 可选 `$/cancel_request`

这是 ACP RFD 支持，不是 CLI Phase D blocker。

计划：

- 在 protocol 层新增 `$/cancel_request` notification dispatch。
- 按 JSON-RPC id 追踪 incoming request task。
- 对 `session/prompt` 的 cancellation 映射到现有 cancel path。
- 对未知 id 按 JSON-RPC / LSP-style 语义忽略。

测试：

- 对 active `session/prompt` 发 `$/cancel_request`，返回 cancelled stop reason。
- 未知 request id 被忽略。

## 文档更新

实现时同步更新：

- `docs/kernel/interfaces/protocol.md`
- `docs/kernel/subsystems/session.md`
- 如果 lifecycle action 顺序有变化，更新 `docs/kernel/history/plans/session-lifecycle-actions.md`
- `docs/cli/history/phase-d-session-config-theme.md`
- `docs/plans/progress.md`

## 验证矩阵

最低命令：

```bash
uv run pytest tests/kernel/protocol -q
uv run pytest tests/kernel/session -q
uv run pytest tests/e2e/test_session_acp_compliance_e2e.py -q -m e2e
uv run pytest tests/e2e/test_session_lifecycle_actions_e2e.py -q -m e2e
uv run ruff format src/kernel/kernel/protocol src/kernel/kernel/session tests/kernel tests/e2e
uv run ruff check src/kernel/kernel/protocol src/kernel/kernel/session tests/kernel tests/e2e
uv run mypy src/kernel
```

所有 closure seam 仍按 `docs/workflow/definition-of-done.md` 要求，在完成报告里贴出
probe / E2E 输出。

## 建议顺序

1. **A**：先修 CLI Phase D session picker 需要的数据形状和校验。
2. **B**：让 generic ACP client 能渲染 modes / config。
3. **C/S1 delete**：无 schema bump，先给实用 session action。
4. **C archive/rename schema batch**：集中做一次 session DB version bump。
5. **D per-session MCP**：较大的跨子系统工作，放后面。
6. **E `$/cancel_request`**：除非 IDE/client 集成提前需要，否则最后做。
