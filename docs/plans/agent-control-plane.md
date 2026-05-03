# Agent Control Plane 计划

状态: landed — single-Primary Agent Control Plane 主路径已实现
创建: 2026-04-29
相关:

- [`acp-acpx-schema-alignment-plan.md`](acp-acpx-schema-alignment-plan.md)
- [`../kernel/history/plans/agent-control-plane-notes.md`](../kernel/history/plans/agent-control-plane-notes.md) — 详细讨论记录、参考实现发现和取舍理由。
- [`full-system-test-plan.md`](full-system-test-plan.md) — Agent Control Plane、Runtime、Permission、CLI/Probe 的全系统测试矩阵。

## 目标

DeepCLI 已从“单个 CLI 直接控制一个 Kernel/Primary session”演进到 Supervisor 监护的
Agent Control Plane 主路径：CLI/Probe/Platform Adapter 进入 Access Agent，Access Agent
通过 Agent Hub Router 绑定 Primary Agent Runtime，Primary Runtime 内部运行真实
SessionManager/Orchestrator/LLM/tool 工作流。所有新结构继续为未来平级 durable Agents
做准备。

核心模型:

```text
Supervisor
  -> Agent Hub
       Router
       Manager
       GlobalResourceMonitor
  -> Access Agent
  -> Primary Agent
  -> peer Session Agents

Durable Agents communicate through Router.
Ephemeral Child Agents stay private to their parent AgentTool.
```

Primary Agent 是默认 user-facing durable Agent，不是所有 durable Agents 的父级。
平级 Session Agents 可以被用户创建、管理和路由访问。`AgentTool` 只保留为每个
durable Agent 内部的私有 Ephemeral Child Agent 机制。

## 当前落地状态

截至 2026-05-01，新的单 Primary 产品架构已落地：

- `scripts/run-kernel.sh` 默认通过 Supervisor 启动 Hub、Access Agent、Primary Runtime。
- Access Agent 默认 `--prompt-backend router`，`session/new`、`session/resume`、
  `session/prompt`、`session/close` 经 Hub 路由到 Primary Runtime。
- Primary Runtime 拥有真实 SessionManager/SessionStore/Orchestrator/LLM/tool 路径。
- `clientTurnId` 贯穿 CLI/Probe、Access、Hub、Runtime、SessionManager、SessionStore，
  支持 active/queued/completed/incomplete turn 去重和 completed replay。
- Primary Runtime 发起的 `session/request_permission` 已通过 Runtime -> Hub ->
  Access -> CLI/Probe 或 Platform Adapter reply sink 隧道返回决策。
- Gateway/Discord adapter 在 router backend 下不再直接调用 SessionManager；平台 inbound
  message 生成稳定 `clientTurnId` 并经 Agent Hub prompt Primary Agent。
- `--prompt-backend compat` 只作为显式回退路径保留，不再是默认产品路径。

仍未宣称完成的范围：用户可见的多 peer durable Agent routing/binding UI、真实 child
kernel spawn/attach 闭环、真实 third-party ACP runtime 产品化接入。这些属于后续扩展，
不阻塞当前单 Primary Agent Control Plane 主路径。

## 最终术语

| 术语 | 含义 |
|---|---|
| Supervisor | 最外层进程监护者。负责启动、重启和监护 Agent Hub、Access Agent、Primary Agent、peer Session Agents、Child Kernels，以及被 Access Agent 请求为独立进程的 Platform Adapters。 |
| Agent Hub | 不依赖 FastAPI 的调度通信进程，包含 Router、Manager、GlobalResourceMonitor。它不运行 agent loop。 |
| Router | Agent Hub 内部消息面，负责 user-to-agent、agent-to-agent、agent-to-user。不能调用 Manager，不能创建/删除 agents。 |
| Manager | Agent Hub 内部控制面，读取 AgentDefinitions，维护 AgentRuntimeRecords，负责 runtime backend selection、queue/status/cancel view 和 management operations。 |
| GlobalResourceMonitor | Agent Hub 内部全局资源监视器，负责 global config/skills/memory/MCP/hooks/tool policy 的跨 Agent 写入协调、锁、revision、`resource.changed` 和 `current_revisions()`。 |
| Access Agent | 独立 FastAPI 进程，唯一 user-facing edge。承载 CLI/Probe/Web UI WebSocket、Platform Adapter ingress、version、startedAt、health/readiness。 |
| Platform Adapter | Discord/Telegram 等平台 adapter。处理平台协议和 reply sink，由 Access Agent 拥有 registry/reply sink，不直接连接 Agent Hub。 |
| Primary Agent | 默认 user-facing durable Agent。 |
| Session Agent | DeepCLI 管理的平级 durable Agent，拥有自己的 session、state、queue、policy 和 lifecycle。 |
| Ephemeral Child Agent | Durable Agent 通过 AgentTool 创建的私有任务级 child。不能进入 Router，不能被外部绑定。 |
| Child Kernel | 独立 DeepCLI kernel 实例，可托管 durable Agents。Manager 负责请求/记录/状态投影，Supervisor 负责实际 spawn/monitor/restart。 |
| AgentDefinition | ConfigManager-owned 声明式 agent 配置：id/name/workspace/state_dir/runtime/policy/bindings/memory scopes/skills scopes/capabilities/metadata。 |
| AgentRuntimeRecord | Manager/Supervisor-owned live state：pid、websocket endpoint、heartbeat、status、restart count、queue depth、active turn、last exit/error。 |
| clientTurnId | Client 生成的 prompt 幂等 ID。用于断线重连后识别“同一轮 prompt”，避免重复执行。不同于 JSON-RPC request id。 |

不要把 DeepCLI 自己的控制面叫 ACPX runtime。ACPX 只作为 ACP 缺失 runtime 语义的参考；
实现归 DeepCLI 自己所有，不依赖 `acpx` CLI 或 ACPX runtime API。

## 协议定位

ACP 仍是基础 wire protocol：`initialize`、`session/new`、`session/load`、
`session/list`、`session/prompt`、`session/cancel`、`session/close`、
`session/resume`、`session/update`、`session/request_permission`。

DeepCLI 需要补齐 ACP 缺少的 runtime 语义：

- named sessions
- queue ownership / queued prompts
- status reporting
- cooperative cancel
- soft close / release
- identity layering
- machine-readable JSON stream
- permission requirements

能映射到官方 ACP 的用官方 ACP；ACP 没有对应方法的，用 `_mustang.agent/*` 或
internal-only surface。第一版不把 `send_message` 稳定成公开 ACP extension；它保持为
Router internal operation + agent tool surface。

## 拓扑

### 启动顺序

```text
+------------------------+
| Supervisor             |
+-----------+------------+
            |
            | 1. start first
            v
+------------------------+
| Agent Hub              |
| Router + Manager +     |
| GlobalResourceMonitor  |
+-----------+------------+
            |
            | 2. after Agent Hub ready
            v
+------------------------+
| Access Agent           |
| FastAPI access edge    |
| process_ready only     |
+-----------+------------+
            |
            | 3. start/register first agent
            v
+------------------------+
| Primary Agent          |
| websocket server       |
+-----------+------------+
            |
            | 4. after Primary registered
            v
+------------------------+
| default_route_ready    |
| platform bindings may  |
| become active          |
+------------------------+
```

Access Agent 的 `process_ready` 和 `default_route_ready` 必须分开。Primary Agent 未注册时，
Access Agent 可以提供 version/startedAt/health/readiness，但 prompt 必须返回明确的
`kernel_starting` / `primary_agent_starting` 或受控 buffering 状态，不能静默吞消息。

### 结构图

```text
+--------------------------+
| Supervisor               |
| process lifecycle        |
+------------+-------------+
             |
             | starts / monitors
             v
+------------------------------------------------------------+
| Agent Hub                                                  |
|                                                            |
|  +-------------------+   +-------------------+             |
|  | Router            |   | Manager           |             |
|  | user-to-agent     |   | AgentDefinitions  |             |
|  | agent-to-agent    |   | RuntimeRecords    |             |
|  | agent-to-user     |   | status/control    |             |
|  +---------+---------+   +---------+---------+             |
|            |                       |                       |
|  +---------+---------------------------------------------+ |
|  | GlobalResourceMonitor                                | |
|  | global writes / revisions / resource.changed events   | |
|  +-------------------------------------------------------+ |
+------------+-----------------------+------------------------+
             ^                       ^
             | user/agent frames     | management/resource calls
             |                       |
+------------+-------------+         |
| Access Agent             |         |
| FastAPI WS / adapters    |         |
| health / readiness       |         |
+------+-------------+-----+         |
       ^             ^               |
       |             |               |
+------+-----+  +----+-------------+ |
| CLI/Probe |  | Platform Adapters | |
| Web UI    |  | Discord/Telegram  | |
+------------+  +------------------+ |
                                      |
                                      |
       +------------------------------+-----------------------------+
       |                                                            |
       v                                                            v
+------+----------------+                              +------------+---------+
| Primary Agent         |                              | Peer Session Agent   |
| websocket server      |<-------- Router -----------> | websocket server     |
| Agent Runtime Managers|                              | Agent Runtime Managers|
+-----------+-----------+                              +------------+---------+
            |                                                       |
            | private AgentTool                                     | private AgentTool
            v                                                       v
+-----------+-----------+                              +------------+---------+
| Ephemeral Child Agent |                              | Ephemeral Child Agent|
| private to parent     |                              | private to parent    |
+-----------------------+                              +----------------------+
```

### 运行期消息流

```text
CLI / Probe / Web UI
  -> Access Agent
  -> Agent Hub.Router
  -> target durable Agent (default: Primary Agent)

Discord / Telegram
  -> Platform Adapter
  -> Access Agent
  -> Agent Hub.Router
  -> target durable Agent (default: Primary Agent)

Durable Agent A
  -> Router
  -> Durable Agent B

Durable Agent
  -> Router
  -> Access Agent
  -> native client or Platform Adapter reply sink

Durable Agent
  -> AgentTool
  -> Ephemeral Child Agent (private, parent-owned)
```

所有 Agent Runtime 内部使用 `websockets` 建 server，不使用 FastAPI。FastAPI 只属于
Access Agent。

## 模块边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| ConfigManager | AgentDefinitions、routing bindings、workspace/state_dir paths、runtime declarations、policy defaults、memory/skills scopes。 | 不保存 pid/heartbeat/queue depth，不连接 runtime，不执行 management。 |
| Agent Hub.Manager | AgentRuntimeRecords、runtime backend selection、status/policy runtime view、create/delete/list/control 执行面。 | 不拥有声明式配置 truth，不执行 prompt turn，不持有 Orchestrator，不写 session log。 |
| Agent Hub.Router | user-to-agent、agent-to-agent、agent-to-user message delivery。 | 不创建/删除 Agents，不调用 Manager，不变更 session state。 |
| Agent Hub.GlobalResourceMonitor | 全局资源写入、并发控制、revision、`resource.changed`、`current_revisions()`。 | 不运行 agent loop，不持有 Agent Runtime Managers，不构建 prompt/tool/memory/skill snapshots。 |
| Access Agent | FastAPI/WS、Connection Auth、Platform Adapter ingress、reply sink、readiness。 | 不解析最终 target agent，不调用 Manager 做拓扑变更，不直接执行 prompt。 |
| SessionManager | Per-agent ACP session lifecycle、SessionStore/event log、per-session FIFO、turn cancel、broadcast/replay、Orchestrator construction。 | 不选择 agent backend，不保存 durable agent truth，不管理 peer-agent topology。 |
| AgentTool | 单个 durable Agent 内部的私有 Ephemeral Child task。 | 不获得 durable agent id，不进入 Router，不承担 durable lifecycle。 |
| Orchestrator | 单个 session turn 的 agent loop。 | 不感知 Router/Manager/peer topology。 |

## 代码落点和迁移边界

当前 `src/kernel/kernel` 仍是 FastAPI-centered kernel。重构后，FastAPI 只能属于
Access Agent；Agent Hub 和 Agent Runtime 都不能 import FastAPI。

建议目标目录：

```text
src/kernel/kernel/
  supervisor/          # 启动顺序、进程监护、restart policy、pid/runtime file
  agent_hub/           # Router、Manager、GlobalResourceMonitor
    router/
    manager/
    global_resources/
  access_agent/        # FastAPI edge、native WS、Platform Adapter ingress/reply sink
  agent_runtime/       # durable Agent websocket runtime、runtime controllers
  agents/              # 共享 agent identity/control-plane 类型
  protocol/            # ACP/JSON-RPC schema and codec
```

过渡规则：

- `kernel.app` / `routes.session` 第一阶段可以保留兼容入口，但新主路径必须逐步迁移到
  `access_agent`。
- 现有 `gateways/manager.py` 不能继续直接 delivery 到 SessionManager；迁移后它的职责进入
  Access Agent 的 Platform Adapter registry/reply sink。
- `session/` 保留为 per-agent SessionManager/SessionStore 实现，不升级成 durable agent
  topology manager。
- `module_table.py` 仍可用于旧路径和 Agent Runtime 内部依赖装配，但 Agent Hub 不通过它取得
  FastAPI subsystem。

## Wire Contracts

Batch B/C 开工前必须先写出最小 schema，避免 Router/Manager/Access Agent 互相穿透。

内部传输约束：

- Agent Hub 不使用 FastAPI。第一版内部控制/消息通道使用 `websockets` library 提供
  loopback WebSocket endpoint；未来可替换为 UDS，但 contract 不变。
- Access Agent 是 Hub 的内部 WebSocket client，同时也是外部 FastAPI WebSocket server。
- Agent Runtime 使用 `websockets` server 注册自己的 endpoint；Router 作为 client 连接该
  endpoint。
- Supervisor 负责分配/传递 Hub endpoint、runtime endpoint、registration token 和进程 env。
- 禁止 Access Agent/Agent Runtime 通过 Python import 直接调用 Agent Hub 对象；测试可以用
  in-process fake，但产品 Probe 必须跨进程走内部 transport。

第一版需要这些 contracts：

| Contract | 方向 | 最小内容 |
|---|---|---|
| Hub readiness | Supervisor/Access Agent -> Agent Hub | hub status、startedAt、schema version、registered agents count。 |
| Agent registration | Agent Runtime -> Agent Hub.Manager | agent_id、runtime_kind、websocket endpoint、capabilities、heartbeat interval、supervisor-issued registration token。 |
| Router frame | Access Agent/Agent Runtime -> Agent Hub.Router | source、target、conversation/session context、message kind、payload、correlation id、reply sink、caller identity。 |
| Management call | management-capable durable Agent or explicit future admin channel -> Agent Hub.Manager | create/list/get/delete/status/pause/close/cancel，带 caller identity、auth context 和 capability。 |
| Binding plan | Agent Hub.Manager -> Access Agent | adapter_id/account/platform context、target policy、enabled/disabled、revision。 |
| Routing snapshot | Agent Hub.Manager -> Agent Hub.Router | durable agent ids、native defaults、platform binding rules、policy revision；Router 本地只读查询。 |
| Resource monitor call | Agent Runtime Manager -> GlobalResourceMonitor | resource key、expected revision、write intent、new revision/current revisions。 |

禁止规则：

- Router frame 不能包含 create/delete/status 等 management operation。
- Router 不能同步调用 Manager 解析 target；Manager 通过 routing snapshot 更新 Router 的只读路由表。
- Access Agent 不解析最终 target truth，只携带 routing context。
- Access Agent 可以承载 management frames，但默认用户入口仍路由到 Primary Agent；它本身不决定、
  不执行 create/delete/status。
- Manager 可以 materialize binding plan，但不能直接处理 platform ingress。
- GlobalResourceMonitor 不构建 prompt/tool/memory/skill snapshot，只维护全局写入和 revision。

### AuthN/AuthZ 边界

第一版是单用户/单节点，但进程间边界仍必须带身份，不能用“本机可信”糊过去：

- Access Agent 继续使用 ConnectionAuthenticator 验证 CLI/Probe/Web UI，并把 AuthContext
  转成 RouterFrame 的 caller identity。
- Platform Adapter 的 platform identity 由 Access Agent 归一化成 caller identity 和 routing
  context；Adapter 自己不获得 management capability。
- Supervisor 为 Agent Runtime 生成短期 registration token；Agent Runtime 注册到 Agent Hub
  时必须携带 token。
- Agent Hub.Manager 校验 management capability；Access Agent 只能转发 management frame，
  不能自己授予 capability。
- Agent Runtime 调 GlobalResourceMonitor 写 global resource 时必须带 agent_id 和 capability。
- Ephemeral Child Agent/SubAgent 不能拿到 Hub registration token，也不能拿到 management
  capability。

## Manager 分层

多 Agent 后，Manager 分三层：

| 层级 | 放置位置 | 内容 |
|---|---|---|
| Network/global control | Agent Hub | Router、Manager、GlobalResourceMonitor。 |
| User/platform access | Access Agent | FastAPI/WS、Connection Auth、Platform Adapter registry、reply sink。 |
| Agent execution | 每个 Agent Runtime | SessionManager、Orchestrator、ConfigView、SkillManager、ToolManager、MCPManager、MemoryManager、HookManager、LLMManager、PromptManager。 |

### ConfigManager 放置

进程拆分后，global ConfigManager 不能留在 FastAPI/Access Agent 里。目标放置：

- Agent Hub 持有 global ConfigManager/resource backend 实例，作为 AgentDefinitions、global
  config、global resource durable truth 的进程内访问点。
- GlobalResourceMonitor 是这些 backend 的跨 Agent 写入协调门面。
- Agent Runtime 不持有 global ConfigManager 实例；它只持有 `ConfigView(agent_id)` 和
  `AgentResourceView` 缓存，通过 Hub 拉取 revision/snapshot。
- Access Agent 只读取自身启动所需的 bootstrap/access 配置，例如 bind address、auth token
  path、adapter process command；不能成为 AgentDefinitions 或 global resources 的 truth。
- 代码复用通过 shared schema/backend helper 完成，不把 Agent Runtime 的 scoped Manager
  实例塞进 Agent Hub。

关键原则：

- ConfigManager 和各资源 backend 是 durable storage truth；GlobalResourceMonitor 是跨
  Agent 的写入协调者，不另存一份 durable truth。
- ConfigManager 是 global config 和 AgentDefinitions 的权威 store。
- 每个 Agent Runtime 使用 `ConfigView(agent_id)`，解析 global config + AgentDefinition
  overrides + session/runtime overrides。
- SessionManager 是 per-agent owned；每个 durable Agent 默认有自己的 SQLite。
- Skill/Tool/MCP/Memory/Hook/LLM/Prompt Managers 运行在 Agent Runtime 内部，基于
  `AgentContext` 构建 agent-scoped view。
- Agent-private 资源写自己的 `state_dir`；workspace 资源写 workspace 并通知
  GlobalResourceMonitor；global 资源必须走 Agent Hub.GlobalResourceMonitor。
- 代码复用靠 shared schema/parser/loader/store backend/validation helper，不把
  SkillManager/MemoryManager 等实例塞进 Agent Hub。

推荐 AgentContext：

```text
AgentContext
  agent_id
  workspace
  state_dir
  session_store_path
  model_profile
  prompt_profile
  tool_policy
  memory_scopes
  skill_scopes
  mcp_scopes
  hook_profile
```

Agent Runtime Managers 应提供 view API：

```text
SkillManager.snapshot_for_agent(agent_context)
ToolManager.snapshot_for_agent(agent_context, session_id)
MemoryManager.search(agent_context, query)
MCPManager.tools_for_agent(agent_context)
HookManager.enabled_hooks_for_agent(agent_context)
LLMManager.model_for(agent_context, role)
PromptManager.system_prompt_for(agent_context)
```

## 全局资源同步

全局资源的跨 Agent 写入入口是 GlobalResourceMonitor：

```text
Agent Runtime Manager
  -> Agent Hub.GlobalResourceMonitor
       validate
       serialize write / lock
       atomic write
       bump revision
       emit resource.changed
```

GlobalResourceMonitor 负责 lock、validation、atomic write、revision 和事件；它是写入协调者，
不是新的配置数据库。实际 durable truth 仍写入 ConfigManager 或对应 resource backend。
Manager 可以作为 management operation executor 调用 ConfigManager API 更新 AgentDefinition，
但不能绕过 ConfigManager，也不能在 AgentRuntimeRecord 里复制声明式 truth。

第一版至少维护：

```text
config.global.revision
agent_definitions.revision
skills.global.revision
memory.global.revision
mcp.global.revision
hooks.global.revision
tool_policy.global.revision
```

每个 Agent Runtime 保存 seen revisions。每轮 turn start 通过门面刷新，避免污染
Orchestrator：

```text
Orchestrator turn start
  -> AgentResourceView.check_and_refresh_before_turn()
       compare local seen revisions with GlobalResourceMonitor.current_revisions()
       reload changed Config/Skills/Tools/MCP/Memory/Hooks views
  -> continue turn
```

一致性规则：

- config/tool policy/permission policy：turn start 必须刷新；tool call 前也要检查
  policy revision。
- skills/hooks：turn start 刷新。
- memory：turn start 刷新或标记 index dirty；search/write 时按 revision 同步索引。
- MCP：turn start reconcile connection/tool list；正在执行的 MCP tool call 不强制中断。
- 不能只依赖事件；Agent Runtime 启动、重连或错过 `resource.changed` 时必须拉
  `current_revisions()` 对账。

## 数据保存

每个 durable Agent 的数据按职责分层：

- `AgentDefinition`：ConfigManager-owned 声明式配置。
- `AgentRuntimeRecord`：Manager/Supervisor-owned live state。pid/endpoint/heartbeat/
  restart count/last error 不能写回 ConfigManager。
- per-agent state directory：agent-local memory、agent-local skills、hooks enablement、
  runtime metadata、可选 auth profile references。
- per-agent SessionStore/SQLite：conversation/session transcript 和 event log。默认路径：
  `~/.mustang/agents/<agent_id>/sessions/sessions.db`。
- Access Agent transient state：client connections、adapter reply sink、route readiness；
  不作为 durable truth。

Memory/Skills 分层：

```text
global layer:
  ~/.mustang/memory
  ~/.mustang/skills

agent-private layer:
  ~/.mustang/agents/<agent_id>/memory
  ~/.mustang/agents/<agent_id>/skills

project/workspace layer:
  <workspace>/.mustang/memory
  <workspace>/.mustang/skills
```

读取顺序建议：`built-in < global < project/workspace < agent-private`。权限上：
agent-private 默认只给该 agent；global 所有 agents 共享；project/workspace 只在当前或显式授权
workspace 中可见。

Session search 第一版采用方式 A：枚举调用方可访问 agents，逐个打开对应 SQLite 查询，
再聚合、排序、分页。不做全局 search index。

### AgentDefinition 最小 Schema

Batch B 至少需要以下字段，后续可以扩展：

```text
AgentDefinition
  id
  name
  role                  # primary | session
  workspace
  state_dir
  runtime:
    kind                # in_process_session_agent | child_kernel | external_acp
    command/env/endpoint/profile
  policy:
    management_capabilities
    tool_policy_profile
    platform_binding_policy
  bindings:
    native_default
    platforms[]
  resources:
    memory_scopes
    skill_scopes
    mcp_scopes
    hook_profile
    model_profile
    prompt_profile
  metadata
```

Primary Agent 必须由 ConfigManager seed 一个默认 AgentDefinition。缺省路径：

```text
~/.mustang/agents/primary/
  sessions/sessions.db
  memory/
  skills/
  runtime/
```

过渡规则：Batch B1 不搬迁现有 single-agent session DB；它只让 SessionManager 支持可注入
per-agent path。Primary Agent 的目标路径在 AgentDefinition seed 后用于新 runtime/new install。
旧路径兼容可以保留到 Access Agent/Primary runtime 切换完成后再删除。

### AgentRuntimeRecord 最小 Schema

RuntimeRecord 是 Manager/Supervisor live state，不写回 ConfigManager：

```text
AgentRuntimeRecord
  agent_id
  runtime_kind
  process_id
  websocket_endpoint
  status
  heartbeat_at
  started_at
  restart_count
  queue_depth
  active_turn_id
  last_exit_code
  last_error
```

Supervisor 可以把 runtime state 写入临时 runtime file 方便 probe/debug，但它不是 durable truth。

## Platform Adapter 和绑定

Platform Adapter 不是独立 agent 入口，不拥有 routing truth。它由 durable Agent 的
platform binding 启动或登记到 Access Agent。

绑定 truth 在 ConfigManager.AgentDefinitions；Manager materialize binding plan；
Access Agent 根据 binding plan 启动/停止/登记 adapters。
Router 使用 Manager 发布的只读 routing snapshot 做本地 target resolution；Router 不在消息路径上
同步调用 Manager。

Ownership 边界：

- Manager 只产生 binding plan，不处理 platform ingress/reply。
- Access Agent 拥有 Platform Adapter registry 和 reply sink，并按 binding plan 执行
  register/unregister/start/stop。
- 如果某个 Platform Adapter 需要独立进程，Supervisor 只负责 spawn/monitor/restart；
  启停决策仍来自 binding plan，执行入口仍在 Access Agent。

解析优先级：

1. explicit thread binding
2. explicit channel binding
3. guild/account binding
4. adapter/account default binding
5. Primary Agent fallback

规则：

- Platform Adapter 只知道 `adapter_id/account_id/platform context/reply sink`。
- Access Agent 保存 reply sink，不决定 target agent。
- Router 根据 routing context 解析 target durable Agent。
- 删除 Agent 或解绑 platform binding 时，Manager 通过 ConfigManager 更新 AgentDefinition，
  再发布新 binding plan；Access Agent 停止或解绑对应 adapter。
- binding 指向 Primary 但 Primary 未注册时，Adapter 可以注册，但 delivery 必须暂停或返回
  `primary_agent_starting`。
- Ephemeral Child Agent 不允许 platform binding。

## 控制词汇

一等操作：

| Operation | 含义 |
|---|---|
| create | 创建 agent/session record。 |
| load | 加载已有 state，可 replay。 |
| resume | 重新附着，不 replay。 |
| prompt | 对目标 Agent 启动一轮 user prompt。 |
| send_message | 向已有 Agent 投递 message，不伪装成 user prompt。第一版 internal/tool surface。 |
| cancel | cooperative cancel active turn。 |
| pause | 暂停接收或消化新 queued work。 |
| status | idle/running/queued/canceling/closed/error。 |
| close | 释放 runtime resources，保留 durable state。 |
| delete | 明确删除 DeepCLI-owned durable state。 |

`create/delete/status/pause/close` 等管理操作不由 Router 触发。Primary Agent 和平级
Session Agents 只有在具备 management capability 时才能调用 Agent Hub，Hub 内部 dispatch
到 Manager。Ephemeral Child Agent/SubAgent 不能触发。

## Reconnect-safe Prompt Turn

CLI/Probe/Web UI 会遇到真实断线场景：client 已发送 `session/prompt`，Kernel 已经开始执行，
但 WebSocket 在 response 返回前断开。此时 client 不能盲目重发 prompt，否则同一条用户消息可能
被执行两次。因此需要把“prompt turn 幂等 ID”作为 Agent Control Plane 的一等设计。

### ID 分层

| ID | 生成方 | Scope | 用途 | 可否跨连接复用 |
|---|---|---|---|---|
| JSON-RPC `id` / `request_id` | 当前连接的 JSON-RPC client | 单条 WebSocket 连接 | 匹配 request/response，写入现有 turn lifecycle 事件用于诊断。 | 不可。重连后 request id 会重新分配。 |
| `clientTurnId` | CLI/Probe/Web UI / Platform Adapter | 单个 session 的一次用户 prompt | 幂等识别同一轮 prompt。断线恢复、retry、queue 去重、result recovery 都靠它。 | 可以。 |
| event id | SessionStore | 单个 session event log | 持久化事件链和 replay。 | 不由 client 使用。 |
| `active_turn_id` | AgentRuntimeRecord projection | Agent runtime live state | status/debug 展示当前 active turn。建议投影为 `clientTurnId`，没有则回退 request id/event id。 | 只读状态。 |

第一版 `clientTurnId` 使用 `_meta["mustang.agent/clientTurnId"]` 传递，值必须是 UUID 字符串。
如果 client 没有提供，Kernel 可以生成一个内部 ID 并写盘，但这种 turn 不能被 client 安全重试。
Router、Agent Hub、Access Agent、Primary Agent Runtime 必须透传 `_meta`，不能丢弃
`clientTurnId`。

### SessionManager 语义

`session/prompt` 进入 SessionManager 时必须先解析 `clientTurnId`：

1. 如果没有 `clientTurnId`：沿用现有行为，不提供 retry 幂等保证。
2. 如果 session 当前 `in_flight_turn.client_turn_id == clientTurnId`：
   - 不创建第二个 turn。
   - 新 request 可选择等待同一 turn 的 completion future，或第一版返回明确
     `turn_in_progress`/`retry_later` error。推荐第一版等待同一 future，保持 CLI 简单。
3. 如果 queue 中已有同 `clientTurnId`：
   - 不重复入队。
   - 新 request 绑定到已有 queued turn 的 response future。
4. 如果 event log 中已有同 `clientTurnId` 的 `turn_completed`：
   - 不重跑 Orchestrator。
   - 返回已完成 stopReason，并让 client 通过 `session/load`/未来 `turn/result` 补 replay。
5. 如果 event log 中有 `user_message`/`turn_started` 但没有 `turn_completed`：
   - 视为 previous attempt incomplete。
   - 第一版返回明确 `turn_incomplete`，要求用户决策或 client load 后展示状态。
   - 不自动重跑，避免重复 tool side effects。

为了做到这些，运行时需要新增：

```text
TurnState
  client_turn_id
  completion_future

QueuedTurn
  client_turn_id

Session
  turn_waiters_by_client_turn_id
  completed_turn_results_by_client_turn_id  # 可懒加载/查询 store
```

持久化事件需要扩展：

```text
user_message.client_turn_id
turn_started.client_turn_id
turn_completed.client_turn_id
turn_cancelled.client_turn_id
```

SQLite 查询需要支持按 `session_id + client_turn_id` 查找最近 turn lifecycle：

```text
find_turn_by_client_turn_id(session_id, client_turn_id)
  -> absent | queued/in_flight projection | completed(stop_reason, event_ids) | incomplete
```

不要求第一版新增全局 turn table；可以先基于 event log JSON 查询/索引实现。但如果查询成本过高，
允许新增轻量 `turn_index` 表，作为 event log 的派生索引。

### Protocol / Router Contract

ACP 官方 schema 当前没有稳定 `turnId` 字段，所以 DeepCLI 第一版使用 namespaced `_meta`：

```json
{
  "sessionId": "...",
  "prompt": [{ "type": "text", "text": "..." }],
  "_meta": {
    "mustang.agent/clientTurnId": "uuid"
  }
}
```

返回结果也带回同一个 ID：

```json
{
  "stopReason": "end_turn",
  "_meta": {
    "mustang.agent/clientTurnId": "uuid",
    "mustang.agent/replayedTurnResult": false
  }
}
```

当返回的是已完成结果时：

```json
{
  "stopReason": "end_turn",
  "_meta": {
    "mustang.agent/clientTurnId": "uuid",
    "mustang.agent/replayedTurnResult": true
  }
}
```

内部 Agent Hub frame 不另造字段，直接把 ACP params 的 `_meta` 传给 Agent Runtime。Manager
的 status projection 可以读取 runtime record 的 `active_turn_id=clientTurnId`，但 Router 不负责
幂等判断。

### Client 行为

CLI/Probe/Web UI：

- 每次用户提交 prompt 时生成一个 UUID `clientTurnId`。
- 如果 WebSocket 在 response 前断开：
  - 当前 in-flight request 先失败并提示“连接断开，正在重连”。
  - 重连成功后，client 可以用相同 `clientTurnId` retry 同一 prompt。
  - Kernel 若已经完成，返回 cached completion；若仍在跑，等待同一 completion；若 incomplete，
    返回明确状态。
- 默认不自动重放 tool side-effect 不明的 prompt，除非 Kernel 已实现上述幂等响应。
- `!`/`$` execution methods 暂不复用 `clientTurnId`，除非后续为 user execution 单独设计
  `clientExecutionId`。

Platform Adapter：

- 对每个 platform inbound message 生成稳定 `clientTurnId`，优先用 platform message id
  派生 UUID/namespace UUID。
- Adapter retry webhook/event 时必须复用同一个 `clientTurnId`，避免 Discord/Telegram
  duplicated delivery 导致重复执行。

### 不做的事

- 不把 JSON-RPC request id 当幂等 key。
- 不在没有 `clientTurnId` 的情况下自动重试 prompt。
- 不自动重跑 incomplete turn。需要用户/上层策略决策。
- 不要求 Orchestrator 感知 `clientTurnId`；它仍只处理单轮 query。
- 不把 Router 变成 turn store；幂等状态属于目标 Agent Runtime 的 SessionManager/SessionStore。

## 实施约束

- 设计背景和参考细节保存在 [`../kernel/history/plans/agent-control-plane-notes.md`](../kernel/history/plans/agent-control-plane-notes.md)；
  实现时以本文为主，遇到边界不清再查 notes。
- 第一版只做单节点网络，不实现真正多用户或 profile isolation。
- Probe 是底层 WebSocket 闭环验证；CLI 是真实用户入口验证。任何改变
  Access Agent / Router / Primary Agent 主路径的 batch，都必须同时跑 Probe 和 CLI smoke。
- Batch B/C 只建立 Agent Hub、GlobalResourceMonitor、AgentResourceView 的接口和 skeleton；
  Skills/Memory/MCP/Hooks/Tool policy 的完整迁移逐个进行。
- 不改重 AgentTool；它只作为 Agent Runtime 内部 private child backend。
- 尽量不动 Orchestrator；新增 agent identity/profile/resource refresh 通过
  SessionManager factory/deps 或 AgentResourceView 注入。
- 不做旧 session 迁移；项目尚未发布。
- 不做 Ephemeral Child Agent promotion。未来升级成 durable Agent 必须重新创建并让用户决策。
- Child Kernel：Manager 负责请求/记录/状态投影；Supervisor 负责实际 spawn/monitor/restart；
  Router 只负责通信。
- External ACP runtime 默认最小权限：不授予 filesystem write / terminal execution；
  write/execute/authenticate 必须显式 mediation。
- Flow execution 不进入第一版。
- 任何 probe 都不要求安装 ACPX。
- Batch C 以后，产品路径必须真实跨进程验证。单元测试可以使用 in-process fake，但不能用
  in-process fake 代替 Probe/CLI smoke。

## 当前 Subsystem 迁移顺序

优先级按“必须先打通主路径”排序：

1. `SessionManager`：先支持 per-agent SessionStore path；保持现有 session lifecycle 和
   Orchestrator 调用方式。
2. `Access Agent`：承接现有 `/session` WebSocket 协议形状，让 CLI/Probe 先能走新入口。
3. `Agent Hub.Router`：先只路由 native user -> Primary，再扩展 peer Agent。
4. `Agent Hub.Manager`：维护 AgentDefinitions/RuntimeRecords/status，不碰 Orchestrator。
5. `GlobalResourceMonitor` + `AgentResourceView`：先做 revision/check skeleton。
6. `GatewayManager`：迁移为 Access Agent Platform Adapter registry/reply sink。
7. `Tool/Skill/Memory/MCP/Hook/LLM/Prompt`：逐个加入 global + agent-private view。
8. `ScheduleManager`：最后处理。它依赖 Session/Gateway delivery，不能在 Router/Access
   Agent 主路径稳定前重构。

## 全量执行顺序

Batch 标题不是简单的线性依赖图；真实执行顺序按下面走：

1. **Batch A 已完成**：保留现有 `kernel.agents` vocabulary 和 `AgentRuntimeController`。
2. **Batch B0 已完成**：补目录 skeleton、wire schema、baseline Probe/CLI smoke。
3. **Batch B1 已完成**：先让 SessionManager 支持 per-agent runtime context 和 per-agent
   SessionStore path，但不改变现有 `/session` 行为。
4. **Batch B2 已完成**：补官方 ACP `session/close` 和 `session/resume`，让 runtime lifecycle
   port 具备 close/release 和 reattach-without-replay 语义。
5. **Batch B 已完成**：落 Agent Hub skeleton、AgentDefinitions、RuntimeRecords、
   GlobalResourceMonitor skeleton。
6. **Batch C 已完成**：落 Access Agent readiness/metadata、Hub internal
   WebSocket registration、minimal Agent Runtime WebSocket contract 和 AgentResourceView
   turn-start refresh。
7. **Batch C1 已完成**：Supervisor Launch Wiring。把 C 已落下的 Access/Hub/Runtime
   contracts 切成真实 Supervisor 启动链路：Agent Hub process -> Access Agent process
   ready -> Primary Agent process registered -> `default_route_ready=true`。
8. **Batch C2 已完成**：Reconnect-safe Prompt Turn Idempotency。补 `clientTurnId`、turn 去重、
   completed-result recovery 和 CLI retry foundation。它必须早于把 CLI 自动 prompt retry 视为
   完整能力，也应早于 Platform Adapter 正式迁移，因为平台 webhook/event 天然可能重复投递。
9. **Batch D 已完成**：锁死 AgentTool/Ephemeral Child Agent 边界；可与 B/C 部分并行，但必须在
   Router 对外开放 peer messaging 前完成。
10. **Batch F 已完成**：先做 queue/status/cancel projection，再开放复杂 Router messaging 和
   Platform Adapter migration。否则 E 的可观测性和 cancel 语义会不稳。
11. **Batch E 已完成单 Primary 产品主路径**：Router prompt、SendMessageTool durable-agent
    route、Platform Adapter -> Router -> Primary fallback prompt/reply 已落地；多 peer durable
    Agent 用户面 routing/binding 继续作为后续扩展。
12. **Batch G 已完成 backend contract**：Child Kernel launch spec 和 durable peer backend
    建模已落地；Manager -> Supervisor spawn/attach 产品闭环后续接线。
13. **Batch H 已完成 fake-stdio adapter contract**：External ACP Runtime Adapter 结构化
    stdio JSON-RPC contract 已落地；真实外部 runtime 产品化接入后续扩展。

执行原则：

- B1/B2 必须早于 C，因为 Access Agent/Primary runtime 需要稳定的 per-agent session store
  和官方 session close/resume lifecycle。
- C1 必须早于 D/F/E 的产品化推进；否则 queue/status/cancel、peer messaging 和 platform
  migration 都会继续依赖旧 FastAPI 单进程假边界。
- C2 必须早于“断线中的 prompt 自动 retry”和正式 Platform Adapter migration；没有
  `clientTurnId` 幂等语义时，重连/平台重复投递都可能造成重复执行。
- F 必须早于 E 的完整产品化迁移；E 可以先用 C 的 fake/minimal peer runtime 做 contract
  probe，但平台迁移前要有 agent-level status/cancel projection。
- G/H 都依赖 C/F 的 runtime control 基础；它们不应该反向阻塞 Primary Agent 主路径。
- Roadmap 中旧的“ACP 跨 Session 通信”和“Team/Swarm”不再作为独立实施路线；它们被
  Batch E 及后续 durable Agent collaboration 能力吸收。

## 批次

### Batch A - Control Vocabulary and Interfaces

- 新增 agent identity、runtime kind、task state、queue state、status 共享类型。
- 定义 southbound `AgentRuntimeController` 内部 Python interface。
- 映射 operation 到官方 ACP、`_mustang.agent/*` 或 internal-only。
- 定义 Agent Hub 和 SessionManager 的 session runtime port；只操作 session runtime。

验收：

- 不改变 runtime behavior。
- Types/interface docs 能解释 northbound/southbound 两种用途。
- 与 Batch B0 schema 不重复定义；Batch A 只补 runtime control interface。

### Batch B0 - Implementation Prep / Alignment

- 新增目标目录 skeleton：`supervisor/`、`agent_hub/`、`access_agent/`、`agent_runtime/`。
- 写出 Wire Contracts 的 Pydantic/typed schema，不接业务行为。
- 写出 `AgentDefinition`、`AgentRuntimeRecord`、`BindingPlan`、`RouterFrame` 最小类型。
- 写出 caller identity、registration token、management capability 的最小 schema。
- 写出 Hub internal WebSocket frame envelope；不要把 Hub 实现成 FastAPI route。
- 对齐已存在的 `agents/control_plane.py` 类型；不倒回重做已经完成的 Batch A 工作。
- 定义默认 Primary Agent seed 和 state path。
- 定义 Probe/CLI smoke 命令入口和 fixture 目录。
- Baseline 命令记录在 [`../kernel/history/plans/agent-control-plane-b0-baseline.md`](../kernel/history/plans/agent-control-plane-b0-baseline.md)。
- 确认当前 single Primary Agent 路径可作为兼容 baseline。

验收：

- 不改变 runtime behavior。
- 类型和 schema 单元测试通过。
- Auth/capability schema 单元测试覆盖：Access identity、Agent registration token、
  management capability 三类身份不能互相替代。
- Internal transport contract 测试覆盖 envelope encode/decode，不依赖 FastAPI。
- `git diff --check` 通过。
- 记录 CLI smoke baseline 和 Probe baseline 命令。

### Batch B1 - Per-Agent Session Runtime Prep

- 为 SessionManager 增加 `AgentContext` / runtime context 注入点。
- 支持 per-agent SessionStore path，默认 Primary 仍映射到现有 single-agent 行为。
- 保持 `SessionHandler` public API 和现有 `/session` 行为不变。
- 不迁移 Orchestrator；只把 store path、agent_id、state_dir 变成可注入依赖。
- 为后续 Access Agent/Primary runtime 预留 factory，不引入 Router/Manager coupling。

验收：

- 现有 session lifecycle、archive/rename/delete、prompt/cancel tests 通过。
- Probe/CLI baseline 行为不变。
- 单元测试证明不同 AgentContext 会落到不同 SessionStore path。
- SessionManager 不保存 durable agent truth，只使用传入的 AgentContext。

### Batch B2 - ACP Session Lifecycle Parity

- 实现官方 ACP `session/close`：释放 active runtime resources，保留 durable session state。
- 实现官方 ACP `session/resume`：重新附着已有 session，不 replay 历史；需要 replay 时仍用
  `session/load`。
- 在 `SessionHandler`、ACP schemas/routing、SessionManager lifecycle 中补齐 close/resume。
- `close` 必须按 ACP 语义等同先 `session/cancel` 当前工作，再释放 active runtime resources；
  然后清理 active sender、permission grants、in-flight bookkeeping。
- `resume` 必须只恢复 attachment/runtime view，不重复发送历史 transcript。

验收：

- Unit/integration 覆盖 close active session 后可 load/resume。
- Unit/integration 覆盖 resume 不 replay，load 仍 replay。
- `initialize` capabilities 只在实现完成后声明 `sessionCapabilities.close/resume`。
- Probe 覆盖 `session/new -> session/close -> session/resume -> session/prompt`。
- CLI smoke 确认 close/resume 不破坏现有 session picker/load 行为。

### Batch B - Agent Hub, AgentDefinitions, Runtime Records

- 新增不依赖 FastAPI 的 Agent Hub skeleton：Router、Manager、GlobalResourceMonitor。
- 在 ConfigManager 中新增 AgentDefinitions schema。
- Manager 读取 AgentDefinitions，维护 AgentRuntimeRecords 和 runtime backend selection。
- GlobalResourceMonitor 提供 revision/current revisions/write skeleton 和
  `resource.changed` event skeleton。
- Agent Hub 持有 global ConfigManager/resource backend 实例；Access Agent 不拥有
  AgentDefinitions/global resource truth。
- 定义 AgentDefinition 与 per-agent SessionStore/SQLite 的绑定关系。
- 定义 Manager -> Access Agent 的 platform binding plan。
- 定义 Manager -> Router 的只读 routing snapshot。
- Seed/resolve 当前 Primary Agent，不改变现有 CLI 行为。
- create/get/list/delete AgentDefinition operations materialize/evict AgentRuntimeRecord。

验收：

- Agent Hub 可单独 readiness probe，不要求 Access Agent/Primary/Adapters 已运行。
- 单元测试覆盖 create/list/get/delete AgentDefinitions。
- restart/reload 保留 AgentDefinitions；runtime-only pid/heartbeat/restart count 不写
  ConfigManager。
- GlobalResourceMonitor 可返回 current revisions，write skeleton 能 bump revision 并产生
  `resource.changed`。
- 现有 CLI 启动和连接路径不回退；如果 Batch B 尚未接入 Access Agent，则至少跑现有
  CLI smoke，确认 single Primary Agent 行为不变。
- Hub readiness probe 不 import FastAPI。
- Manager/Router/GlobalResourceMonitor 模块边界测试覆盖：Router 不能调用 Manager，
  Manager 不能处理 platform ingress，GlobalResourceMonitor 不构建 resource snapshot。
- Router 使用 routing snapshot 本地解析 default Primary route，不同步调用 Manager。
- Management calls 和 GlobalResourceMonitor writes 必须校验 caller identity/capability。

### Batch C - Access Agent and Durable WebSocket Runtime

- Supervisor 启动顺序：Agent Hub -> Access Agent process ready -> Primary Agent registered
  -> `default_route_ready=true`。
- Supervisor 通过 env 或启动参数传递 Hub endpoint、registration token、agent_id、
  state_dir/session_store_path。
- 新增 Access Agent 进程：FastAPI WS、version、startedAt、health/readiness、adapter ingress。
- Access Agent 区分 `process_ready`、`hub_ready`、`primary_registered`、
  `default_route_ready`、`platform_bindings_active`。
- Access Agent 根据 Manager binding plan 管理 Platform Adapter registry/reply sinks。
- 新增 durable Agent websocket runtime backend；Agent 内部用 `websockets` server。
- Router 连接 Agent websocket server，并转发 user/agent/output frames。
- 内置 DeepCLI runtime 通过 SessionManager 窄 port 复用 prompt/queue/cancel。
- 新增 `AgentResourceView.check_and_refresh_before_turn()` skeleton；Orchestrator 只调用门面。

验收：

- Probe 观察启动顺序和 readiness 状态。
- Primary 未注册时 prompt 返回明确 starting/error 或受控 buffering。
- Probe 可通过 Access Agent 读 metadata/readiness 并向 Primary prompt。
- CLI 走同一 Access Agent WebSocket 路径，完成 initialize/session/new/session/prompt
  的真实 smoke。
- Probe 可用 fake/minimal websocket peer runtime 验证 Router/Runtime contract；
  真正用户可见的 peer Session Agent routing/binding 留到 Batch E。
- 单元测试证明 revision 变化触发 AgentResourceView refresh，未变化不 reload。
- Agent Hub、Access Agent、Primary Agent 至少在 Probe 中以真实进程边界运行。
- Agent Runtime registration 必须使用 Supervisor-issued token；无 token/错 token 的注册被拒绝。
- Probe 验证 Access Agent 到 Agent Hub 走 internal WebSocket，而不是 Python import。
- FastAPI imports 只出现在 Access Agent 相关模块和旧兼容入口中。

### Batch C1 - Supervisor Launch Wiring

目标：把 Batch C 的 contract/readiness/runtime probe 从“兼容 FastAPI 进程内 wiring”推进到
真实 Supervisor 启动链路。C1 结束后，默认开发启动应由 Supervisor 监护 Agent Hub、
Access Agent、Primary Agent 三类进程；旧 `kernel.app` / `routes.session` 只保留为兼容入口或
内部复用模块，不再是概念上的 Kernel 本体。

范围：

- 新增 Supervisor CLI/entrypoint，例如 `python -m kernel.supervisor` 或等价脚本。
- Supervisor 启动顺序必须固定为：
  1. start Agent Hub process，等待 Hub readiness。
  2. start Access Agent process，等待 `process_ready=true` 和 `hub_ready=true`。
  3. start Primary Agent runtime process，传入 `agent_id`、`state_dir`、
     `session_store_path`、Hub endpoint、registration token。
  4. 等待 Primary Agent 通过 Hub `agent.register` 成功，Access Agent readiness 变成
     `primary_registered=true`、`default_route_ready=true`。
- Supervisor 负责生成 registration token，并只通过 env/argv 传给目标 Primary Agent。
- Supervisor 负责 pid/runtime file，例如 `~/.mustang/state/supervisor.json` 或临时 test path，
  记录 Hub/Access/Primary pid、endpoint、startedAt、lastExit/error。
- Agent Hub 进程必须不 import FastAPI。
- Primary Agent runtime 进程内部使用 `websockets` server，不使用 FastAPI。
- Access Agent 是唯一 user-facing FastAPI 进程，提供 `/access/metadata`、
  `/access/readiness` 和兼容 `/session` WebSocket。
- Access Agent 到 Agent Hub 必须走 internal WebSocket，不允许 Python import Hub 对象作为产品路径。
- Primary Agent runtime 到 Agent Hub 必须走 internal WebSocket registration。
- `kernel.app` 当前兼容入口可以继续存在，但 Supervisor path 的 Probe/CLI smoke 必须成为
  C1 的验收主路径。
- 不在 C1 做 peer Session Agent、Platform Adapter migration、AgentTool 改造、Queue/Status/Cancel
  projection；这些仍属于 D/F/E。

验收：

- Probe 观察完整启动顺序：Hub ready -> Access process ready -> Primary registered ->
  `default_route_ready=true`。
- Probe 在 Primary 未注册窗口访问 Access readiness，能看到明确 `primary_agent_starting`；
  若发送 prompt，必须返回明确 starting/error 或受控 buffering，不能静默吞消息。
- Probe 通过 Supervisor 启动的 Access Agent 完成 metadata/readiness 读取。
- Probe 通过 Supervisor path 完成 `initialize -> session/new -> session/prompt` 到 Primary Agent。
- CLI 通过 Supervisor path 完成 connect、session/new、session/prompt smoke。
- Agent Runtime registration 无 token/错 token 被拒绝；正确 token 成功。
- Supervisor 进程退出时能清理/停止 Hub、Access、Primary 子进程。
- pid/runtime file 写入和清理有单元测试或 probe 验证。
- `git diff --check`、目标单元测试、Probe、CLI smoke 通过。
- FastAPI import audit：Agent Hub / Agent Runtime / Supervisor core 不能 import FastAPI；FastAPI
  只允许在 Access Agent 和旧兼容入口中出现。

### Batch C2 - Reconnect-safe Prompt Turn Idempotency

状态: landed

目标：让 CLI/Probe/Web UI/Platform Adapter 在 WebSocket 断开、Kernel 重启或平台重复投递时，
可以用同一个 `clientTurnId` 安全恢复同一轮 prompt，避免重复执行。

范围：

- 在 CLI ACP client 的 `session/prompt` params 中生成并发送
  `_meta["mustang.agent/clientTurnId"]` UUID。
- Probe 同步支持发送固定 `clientTurnId`，用于 E2E 验证 retry/recovery。
- ACP schema/contract 接受并透传 `_meta["mustang.agent/clientTurnId"]`。
- Access Agent -> Agent Hub -> Router -> Primary Agent Runtime 必须完整透传 `_meta`。
- `AgentSessionRuntimeService` 和外部 ACP adapter 都不能丢弃 `clientTurnId`。
- SessionManager 解析 `clientTurnId`，并写入 `user_message`、`turn_started`、
  `turn_completed`、`turn_cancelled` event。
- `TurnState` / `QueuedTurn` 增加 `client_turn_id` 和 completion future 复用能力。
- SessionManager 入站 `session/prompt` 前做幂等检查：
  - active 同 ID：复用 active completion future，不创建第二个 turn。
  - queued 同 ID：复用 queued completion future，不重复入队。
  - completed 同 ID：返回已有 stopReason，`_meta.replayedTurnResult=true`，不重跑。
  - incomplete 同 ID：返回明确 `turn_incomplete`，不重跑。
  - absent：正常创建 turn。
- SessionStore 增加按 `session_id + client_turn_id` 查 turn lifecycle 的查询能力；实现可先用
  event log 查询，必要时新增派生 `turn_index` 表。
- `AgentRuntimeRecord.active_turn_id` 投影优先使用 `clientTurnId`；没有时回退 request id。
- CLI 重连后允许用同一个 `clientTurnId` retry 上次未确认 prompt；只有 C2 完成后才能把
  prompt auto-retry 视为 supported。
- Platform Adapter migration 前必须定义 platform message id -> `clientTurnId` 的稳定映射；
  第一版可用 namespace UUID。

验收：

- Unit：同 `clientTurnId` 的第二个 prompt 不会创建第二个 `user_message`。
- Unit：active 同 ID 的 request 复用同一个 completion future。
- Unit：queued 同 ID 不重复入队。
- Unit：completed 同 ID 返回 cached stopReason，并标记 `replayedTurnResult=true`。
- Unit：incomplete 同 ID 返回明确 `turn_incomplete`，不自动重跑。
- Unit：没有 `clientTurnId` 的旧 client 保持现有行为。
- Store test：event log/turn index 可按 `clientTurnId` 查 completed/incomplete 状态。
- Router/runtime test：`_meta["mustang.agent/clientTurnId"]` 从 Access request 穿过 Hub 到
  Primary Agent Runtime。
- CLI test：断线后 retry 同 prompt 复用相同 `clientTurnId`。
- Probe E2E：`session/prompt(clientTurnId=X)` 断开/重连/重发，Kernel 不重复执行 turn。
- Probe E2E：completed retry 返回 cached result 或可恢复 result，不产生第二条 user message。
- `git diff --check`、目标 unit tests、CLI smoke、Probe smoke 通过。

不属于 C2：

- 不实现自动重放没有 `clientTurnId` 的 prompt。
- 不自动重跑 incomplete turn。
- 不为 shell/python execution 复用 prompt `clientTurnId`；后续如需要，另设
  `clientExecutionId`。
- 不修改 Orchestrator；幂等逻辑停留在 SessionManager/SessionStore 层。

### Batch D - AgentTool Ephemeral Child Boundary

状态: landed

- 保留现有 AgentTool/spawn_subagent 行为，不接入 Agent Hub.Manager/Router/AgentDefinitions。
- 测试证明 child 结果只回父 Agent，不能被其他 Agents 寻址。
- child deletion/GC 和 durable records 分开。

验收：

- 现有 AgentTool tests 继续通过。
- 新测试证明 Ephemeral Child 无 durable agent id，不进入 Router。

### Batch F - Queue, Status, Cancel

状态: landed

- 必须在 Batch E 完整迁移前完成。
- 增加 agent-level queue/status projection，不在 SessionManager 复制第二套 durable queue。
- Prompt 进入目标 Agent 绑定 session queue；message 先走 Router，再由目标 runtime 决定处理方式。
- cancel 复用内置 `session/cancel` 或外部 ACP `session/cancel`。

验收：

- Probe 展示 queued work 按顺序 drain。
- Probe 展示 Manager agent status 随 session queue/in-flight 变化。
- Probe 展示 cancel 干净完成 active request。

### Batch E - Router Messaging and Platform Adapter Migration

状态: landed for single-Primary product path; multi-peer Agent routing remains future.

- 依赖 Batch F 的 agent-level queue/status/cancel projection。
- Router 路由 primary-to-session、session-to-session messages。
- durable-agent `send_message` 接入 Router semantics。
- user-facing routing binding/policy surface 预留；SubAgent 不能成为目标。
- 现有 Gateway/Discord adapter 迁移为 Platform Adapter：平台消息 -> Access Agent ->
  Router；回复经原 adapter 通道写回。
- Platform Adapter 在当前单 Primary 产品路径下走 Agent Hub Router；后续多 Agent 绑定时再由
  Manager binding plan 驱动启停，不能 Access Agent 启动时无差别拉起所有账号。
- `SessionManager.deliver_message()` 降级为 session reminder primitive 或兼容桥。
- `SendMessageTool` 保持 UX，但调用 Router routing。

验收：

- Probe 从 Primary 向 peer Session Agent 发送 message。
- CLI 主路径仍可对 Primary Agent 正常 prompt，并能看到 Router/Access Agent 转发后的
  streamed update。
- 现有 cross-session SendMessage 兼容。
- Probe 模拟 binding plan 变化，观察 adapter register/unregister 和 stale fallback。

当前落地边界：

- Router 显式只接受 routing snapshot 中的 durable Agent target；Ephemeral Child Agent 不进
  snapshot，因此不能被 Router 寻址。
- `SendMessageTool` 保留原有 session/task UX，并新增 `agent:<id>` durable-agent route。
- 当前兼容 kernel 中，`agent:<id>` 已通过 `ToolContext.route_agent_message` 调用 Agent Hub
  Router 做解析。
- Access Agent 默认使用 `--prompt-backend router`：Probe 的
  `session/new -> session/prompt` 已走 Router -> Primary Runtime。
- Primary Runtime 发起的 `session/request_permission` 会通过 Runtime -> Hub -> Access/Platform
  tunnel 透传到 CLI/Probe 或 Platform Adapter reply sink。
- Gateway/Discord adapter 在 router backend 下不再直接调用 `SessionManager.run_turn_for_gateway`；
  平台 inbound message 会生成稳定 `clientTurnId` 并通过 Hub prompt Primary Agent，回复经原
  adapter 通道写回。
- `session/prompt` 通过 Access WebSocket -> Hub.Router -> Primary Agent Runtime
  WebSocket -> Hub -> Access 返回 structured `session/update` 与 `stopReason`。
- `--prompt-backend compat` 保留为显式回退；默认 router backend 由 Primary Agent Runtime
  内部的真实 SessionManager/Orchestrator/LLM 工作流承载。
- `SessionManager.deliver_message()` 保持为 active session reminder primitive/兼容桥。

### Batch G - Child Kernel Backend

状态: landed backend contract

- 用 ACP + `_mustang.agent/*` 控制 child DeepCLI kernel。
- Child Kernel 托管的 Agents 是 durable peers，不是 Primary children。
- Manager 负责 child Kernel 请求/记录/状态投影；Supervisor 负责实际 spawn/monitor/restart；
  Router 只负责通信。
- 映射 child Kernel session/status 到 DeepCLI agent/task ids。
- Permission mediation 保持显式。

验收：

- Probe 启动或 attach child kernel，prompt、observe updates、cancel turn。

当前落地边界：

- Supervisor 提供 `ChildKernelLaunch` -> `ChildSpec`，用于以 nested `kernel.supervisor`
  方式启动 child DeepCLI kernel。
- Child Kernel 被建模为 durable peer backend；不会复用 AgentTool，也不会进入 parent task
  registry。
- 实际 Manager operation 到 Supervisor spawn/attach 的闭环仍待后续接线。

### Batch H - External ACP Runtime Adapter

状态: landed fake-stdio adapter contract

- 实现 third-party ACP stdio client support，不调用 `acpx`。
- 支持 initialize、session setup、prompt、updates、cancel、close。
- 在开放 authority 前处理 `fs/*`、`terminal/*`、`authenticate` client calls。
- Probe 仍然作为外部客户端走 Access Agent WebSocket；fake ACP stdio server 只是
  External ACP Runtime Adapter 的后端测试 fixture。

验收：

- Probe 通过 Access Agent WebSocket 控制一个 backed by fake ACP stdio server 的
  External ACP Runtime Adapter，证明 adapter 使用 structured JSON-RPC parsing，不依赖
  PTY scraping。
- 后续可选：同一 Probe WebSocket 路径控制真实 external ACP-compatible runtime。

当前落地边界：

- `ExternalAcpRuntimeAdapter` 使用 Content-Length framed stdio JSON-RPC，支持
  `initialize`、`session/new`、`session/prompt`、`session/cancel` notification、
  `session/close`。
- fake ACP stdio server 测试覆盖 structured `session/update` notification collection。
- runtime-initiated `session/request_permission` 已通过 Hub/Access tunnel 转发。
- runtime-initiated `fs/*` 仍 fail closed 为 JSON-RPC `-32601`；这不是 prompt/permission
  主路径，后续开放 file authority 前另行设计。

## Probe 清单

Probe 验证底层 WebSocket/API 闭包；CLI smoke 验证用户真实入口。二者不能互相替代。

1. Baseline current single Primary Agent WebSocket path。
2. Per-agent SessionStore path isolation。
3. ACP `session/close` / `session/resume` lifecycle parity。
4. Agent Hub readiness。
5. Manager AgentDefinition/RuntimeRecord store。
6. GlobalResourceMonitor revision/current_revisions/resource.changed skeleton。
7. Supervisor startup order：Hub ready -> Access process ready -> Primary registered ->
   `default_route_ready=true`。
8. Access Agent metadata/readiness，覆盖 `process_ready=true` 且 `default_route_ready=false`。
9. Access Agent pre-primary prompt returns starting/error/buffering。
10. Access Agent -> Hub -> Primary default-route prompt。
11. Supervisor pid/runtime file records and cleanup。
12. Registration token rejection/acceptance across process boundary。
13. WebSocket Session Agent backend internal interface。
14. AgentResourceView revision refresh。
15. Ephemeral AgentTool child boundary。
16. Queue/status/cancel。
17. Manager binding plan -> Access Agent adapter registration。
18. Explicit binding/policy -> peer Session Agent prompt。
19. Primary <-> peer Session Agent Router message。
20. Platform Adapter -> Access Agent -> Router -> Primary fallback prompt/reply。
21. Platform Adapter explicit binding -> peer Session Agent prompt/reply。
22. Child Kernel ACP。
23. Probe over Access Agent WS controls External ACP Runtime Adapter backed by fake ACP stdio server。
24. Optional: same Probe WS path controls real external ACP-compatible runtime。

## CLI Smoke 清单

1. CLI can connect to Access Agent WebSocket and complete initialize.
2. CLI can create/load a Primary Agent session.
3. CLI can send `session/prompt` to Primary Agent and receive streamed updates.
4. CLI reports clear startup/readiness errors when `default_route_ready=false`.
5. CLI can run the same smoke against Supervisor-launched Access Agent.
6. CLI behavior remains compatible with the existing single-Primary-Agent workflow after each batch.

## 参考实现要点

OpenClaw:

- `agents.list[]` 是 named agent definitions。
- `bindings[]` 把 inbound channel/account/peer 映射到 agent。
- session key 使用 `agent:<agentId>:...`。
- session store 默认 `~/.openclaw/agents/<agentId>/sessions/sessions.json`。
- workspace、agentDir、auth、memorySearch、skills、sandbox、tools、model 都通过
  `agentId` 解析 agent-scoped view。

Hermes:

- `HERMES_HOME`/profile 是完整 runtime home 隔离。
- `SessionDB` 是集中 SQLite + WAL + FTS，可借鉴实现细节。
- Gateway `SessionSource`/`SessionContext` 记录 platform/chat/user/thread，可借鉴
  Access Agent/Platform Adapter context。

DeepCLI 采用 OpenClaw 的 multi-agent scoping，吸收 Hermes 的 Python runtime、
SQLite/WAL、gateway context 经验。
