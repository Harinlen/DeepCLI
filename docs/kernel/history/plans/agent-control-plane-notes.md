# Agent Control Plane 详细记录

状态: reference notes
创建: 2026-04-29
主计划: [`../../../plans/agent-control-plane.md`](../../../plans/agent-control-plane.md)

这个文件保存 `agent-control-plane.md` 精简时移出的讨论背景、参考实现发现和取舍理由。
实现时优先读主计划；当模块边界、数据归属或迁移顺序不清楚时再查这里。

## 设计演进摘要

最初的问题是 ACP/ACPX 对齐之后，DeepCLI 不只是被 CLI 控制的一端，也会成为控制
其他 Agent/Kernel 的 client。因此不能只把当前 Kernel 当作单个被控 runtime。

最终模型确定为：

```text
Supervisor
  starts/monitors:
    Agent Hub
      Router
      Manager
      GlobalResourceMonitor
    Access Agent
    Primary Agent
    peer Session Agents
    Child Kernels
    Platform Adapter processes requested by Access Agent
```

主计划中的 ownership 边界优先：Access Agent 拥有 Platform Adapter registry/reply sink；
Supervisor 只在 adapter 是独立进程时负责监护，不决定 binding 或 target route。

关键修正：

- Primary Agent 是默认 user-facing durable Agent，不是所有 durable Agents 的父级。
- Durable Session Agents 和 Primary Agent 平级，通过 Router 通信。
- `AgentTool` 只负责父 Agent 私有的 Ephemeral Child Agents。
- SubAgent 不进入 Router，不被 Platform Adapter 绑定，不和别的 durable Agent 通信。
- FastAPI 不在 Agent Runtime 内部运行，只在 Access Agent 中作为用户/平台接入层。
- Agent Hub 不基于 FastAPI，避免污染后续新建 Agent 进程。
- 入口统一：CLI、Probe、Web UI、Discord、Telegram 都先进入 Access Agent，再进 Router。

## 完整结构图

```text
Single-node DeepCLI agent network

+--------------------------+
| Supervisor               |
| process lifecycle        |
| start / stop / restart   |
+------------+-------------+
             |
             | starts first
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
|  | GlobalResourceMonitor                                 | |
|  | global resource writes                                | |
|  | revisions / current_revisions()                       | |
|  | resource.changed events                               | |
|  +-------------------------------------------------------+ |
+------------+-----------------------+------------------------+
             ^                       ^
             | user/agent frames     | management/resource calls
             |                       |
+------------+-------------+         |
| Access Agent             |         |
| FastAPI access edge      |         |
| - native WebSocket       |         |
| - adapter ingress        |         |
| - reply sinks            |         |
| - health/readiness       |         |
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
| Agent Runtime         |                              | Agent Runtime        |
| - ConfigView          |                              | - ConfigView         |
| - SessionManager      |                              | - SessionManager     |
| - Orchestrator        |                              | - Orchestrator       |
| - Skill/Tool/MCP      |                              | - Skill/Tool/MCP     |
| - Memory/Hook/LLM     |                              | - Memory/Hook/LLM    |
+-----------+-----------+                              +------------+---------+
            |                                                       |
            | private AgentTool                                     | private AgentTool
            v                                                       v
+-----------+-----------+                              +------------+---------+
| Ephemeral Child Agent |                              | Ephemeral Child Agent|
| private to parent     |                              | private to parent    |
| no Router route       |                              | no Router route      |
+-----------------------+                              +----------------------+

Southbound backends controlled by Hub.Manager:

+--------------------+   ACP + _mustang.agent/*    +-----------------------+
| Hub.Manager        |---------------------------->| Child Mustang Kernel  |
|                    |                             | hosts durable agents  |
|                    |   ACP stdio JSON-RPC        +-----------------------+
|                    |---------------------------->| External ACP Runtime  |
+--------------------+                             | Adapter               |
                                                   +-----------------------+
```

图中边界：

- User/third-party clients 不直接进入 Agent Hub；全部经 Access Agent。
- Platform Adapter 只负责平台协议和 reply sink；不决定 target agent。
- Router 只做消息路由；不能创建/删除 Agent，不能调用 Manager。
- Manager 只做控制面；不执行 prompt turn，不持有 Orchestrator。
- GlobalResourceMonitor 只管全局资源写入和 revision；不运行 Agent Runtime Managers。
- Durable Agents 之间只能通过 Router 通信。
- Ephemeral Child Agents 只能和父 Agent 通过 AgentTool 私有通道通信。

## 命名取舍

讨论过 “ManagerManager”、“GlobalManager”、“ResourceRegistry”、“ResourceMonitor”。
最终选择 `GlobalResourceMonitor`：

- `Global`：只管全局资源。
- `Resource`：管 config/skills/memory/MCP/hooks/tool policy 这类资源。
- `Monitor`：强调并发控制、revision、change event，有 Ada monitor 的语义。
- 避免被误解成“管理所有 Manager 的上帝对象”。

`GlobalResourceMonitor` 不持有 `SkillManager`、`MemoryManager` 等 Agent Runtime
Managers 的实例。代码复用应通过 shared schema/parser/loader/store backend/validation
helper 实现。

## OpenClaw 参考发现

OpenClaw 更接近 DeepCLI 需要的 durable multi-agent model。

### Agent 定义和作用域

- Agent 配置事实在 `agents.list[]`。
- `agents.list[]` 包含 `id`、`default`、`name`、`workspace`、`agentDir`、
  model/tool/sandbox/runtime/identity 等配置。
- routing binding 在 `bindings[]`，把 inbound channel/account/peer 映射到 agent。
- WebChat 可以选择 agent，并默认进入该 agent main session。
- CLI 也支持 `openclaw sessions --agent <id>` 和 `--all-agents` 聚合多个 agent store。

相关路径：

- `/home/saki/Documents/alex/openclaw/src/agents/agent-scope.ts`
- `/home/saki/Documents/alex/openclaw/src/routing/resolve-route.ts`
- `/home/saki/Documents/alex/openclaw/src/config/sessions/paths.ts`
- `/home/saki/Documents/alex/openclaw/docs/channels/channel-routing.md`
- `/home/saki/Documents/alex/openclaw/docs/cli/agents.md`
- `/home/saki/Documents/alex/openclaw/docs/cli/sessions.md`

### 路由模型

OpenClaw 的 Discord/Telegram/Slack 等外部入口不是自由直连任意 agent，而是通过 Gateway
routing binding 选择目标 agent。

路由优先级：

1. exact peer match
2. parent peer match
3. Discord guild + roles
4. Discord guild
5. Slack team
6. account match
7. channel match
8. default agent

`resolveAgentRoute` 会解析出 `agentId`，并生成 `agent:<agentId>:...` session key。

DeepCLI 吸收：

- 外部入口默认到 Primary Agent。
- 显式 binding/policy 可以路由到 durable peer Agent。
- Platform Adapter 不绕过 Access Agent/Router。
- SubAgent 不成为外部可路由目标。

### 数据隔离

OpenClaw 每个 agent 有独立 `agentDir`，默认：

```text
~/.openclaw/agents/<agentId>/agent
```

它明确禁止多个 agents 共享同一个 `agentDir`，避免 auth/session state collisions 和
token invalidation。

Session store 默认：

```text
~/.openclaw/agents/<agentId>/sessions/sessions.json
```

Workspace 和 runtime state 分离：

- workspace 是 agent 的 home/default cwd 和长期上下文位置。
- config、credentials、sessions、managed skills 不放进 workspace。
- workspace 可包含 `AGENTS.md`、`SOUL.md`、`USER.md`、`IDENTITY.md`、`TOOLS.md`、
  `HEARTBEAT.md`、`memory/`、`skills/` 等 agent 可读写上下文。

DeepCLI 吸收：

- `AgentDefinition` 集中保存声明式事实。
- per-agent state dir 严格隔离。
- per-agent session SQLite。
- global/project/agent-private memory and skills 分层。

### Manager 作用域

OpenClaw 并不是“每个 agent 一整套互相独立的 Manager 类”，而是大量 resolver 接受
`agentId`：

```text
resolveAgentWorkspaceDir(cfg, agentId)
resolveAgentDir(cfg, agentId)
resolveSessionTranscriptsDirForAgent(agentId)
resolveMemoryBackendConfig({ cfg, agentId })
resolveAgentSkillsFilter(cfg, agentId)
resolveSandboxConfigForAgent(cfg, agentId)
resolveDefaultModelForAgent({ cfg, agentId })
```

DeepCLI 对应做法：

- Agent Runtime 内部有 managers/views。
- 每个 manager/view 基于 `AgentContext` 解析最终可见资源。
- 全局 Manager/Monitor 不承担 agent loop execution logic。

## Hermes 参考发现

Hermes 不像 OpenClaw 那样在同一个 runtime 内管理多个 durable peer agents。它更像：

```text
one HERMES_HOME/profile = one complete runtime home
```

### Profile/Home 隔离

`HERMES_HOME` 是完整边界，包含：

- `config.yaml`
- `.env`
- memory
- sessions
- skills
- gateway service
- cron
- logs

Profile 模式下路径类似：

```text
~/.hermes/profiles/<name>
```

DeepCLI 不用 profile 替代 durable Agent。Profile 级隔离可以作为未来更外层能力；
一个 DeepCLI Supervisor 下仍然可以有多个 durable Agents。

相关路径：

- `/home/saki/Documents/alex/hermes-agent/hermes_constants.py`
- `/home/saki/Documents/alex/hermes-agent/hermes_state.py`
- `/home/saki/Documents/alex/hermes-agent/gateway/session.py`
- `/home/saki/Documents/alex/hermes-agent/run_agent.py`

### SQLite / SessionDB

Hermes `SessionDB` 是集中 SQLite store：

- WAL
- application-level jitter retry
- FTS index
- sessions/messages/source/parent/title/model_config/cost/usage
- search/export/prune/lineage

DeepCLI 当前 session schema 是：

- `sessions`
- `session_events`

尚无 FTS/search 表。第一版 multi-agent search 不建全局 index，采用方式 A：

```text
enumerate accessible agents
  -> open each agent's sessions.db
  -> query
  -> aggregate/sort/page
```

Hermes 的 SQLite/WAL/retry/FTS 经验后续可借鉴，但不改变第一版 per-agent SQLite 决策。

### Gateway Context

Hermes `SessionSource` / `SessionContext` 保存：

- platform
- chat_id/chat_name/chat_type
- user_id/user_name
- thread_id
- connected platforms
- home channels

这些 context 会注入 agent prompt。DeepCLI 的 Access Agent / Platform Adapter 可以借鉴
这个 context shape，但 routing truth 应保留在 Agent Hub/ConfigManager/Router。

## 已决设计

### ConfigManager 和 AgentDefinition

ConfigManager 是 global config 和 AgentDefinitions 的权威 store。每个 Agent Runtime
不运行一个可写 ConfigManager 副本，而是使用：

```text
ConfigView(agent_id)
  = global config
  + AgentDefinition / agent overrides
  + session/runtime overrides
```

Agent-specific overrides 优先于 global，但安全相关配置应默认只能收紧，不能随便放宽。

### SessionManager

SessionManager 是 per-agent owned。

逻辑模型：

```text
SessionManager(agent_id, sessions_db_path)
```

可以先实现为同一类的多个 view/instance，不要求每个 Agent 一个独立 Python 进程。但隔离边界
必须是 per-agent SQLite path。

### Skill / Tool / MCP / Memory / Hook / LLM / Prompt Managers

这些属于 Agent Runtime execution layer。它们可以共享全局 registry 或全局 store，但每个
Agent loop 使用 agent-scoped view。

典型结构：

```text
SkillManager
  global skill dirs
  workspace skill dirs
  agent-private skill dirs
  snapshot_for_agent(agent_context)

ToolManager
  shared builtin definitions
  MCP-derived tools
  agent tool policy
  snapshot_for_agent(agent_context, session_id)

MCPManager
  global MCP server definitions
  agent enabled/disabled scopes
  tools_for_agent(agent_context)

MemoryManager
  global memory
  workspace memory
  agent-private memory
  search/write with scope

HookManager
  global hooks
  workspace hooks
  agent-private hooks
  enabled_hooks_for_agent(agent_context)
```

### GlobalResourceMonitor 同步

问题：Agent A 的 Manager 修改了 global resource，Agent B 的 Manager 怎么及时知道？

决策：

- global resources 的跨 Agent 写入入口是 GlobalResourceMonitor；durable truth 仍在
  ConfigManager 或对应 resource backend。
- 每个 global resource 有 revision。
- 写入 global resource 必须走 Hub。
- Agent Runtime 缓存的是 view。
- 每轮 turn start 通过 `AgentResourceView.check_and_refresh_before_turn()` 对账。
- 事件只是加速；启动/重连/错过事件时必须拉 `current_revisions()`。

第一版 revision 集合：

```text
config.global.revision
agent_definitions.revision
skills.global.revision
memory.global.revision
mcp.global.revision
hooks.global.revision
tool_policy.global.revision
```

### Platform Adapter

Platform Adapter 放在 Access Agent 前面，复用 Access Agent ingress/egress。它不直接连接
Agent Hub，也不决定 target agent。

Adapter 生命周期由 AgentDefinitions 中的 platform bindings 经 Manager materialized
binding plan 驱动。

入站：

```text
Discord/Telegram
  -> Platform Adapter
  -> Access Agent
  -> Router
  -> target durable Agent
```

出站：

```text
Durable Agent
  -> Router
  -> Access Agent
  -> Platform Adapter reply sink
  -> Discord/Telegram
```

Primary 未注册时，Adapter 可注册，但 delivery 必须暂停或返回 `primary_agent_starting`。

### AgentManager/Manager 权限

最初讨论过“只有 Primary Agent 能访问 Manager”。最终改成：

- Primary Agent 和与它平级的 durable Session Agents 都可以通过 management capability
  访问 Agent Hub.Manager。
- SubAgent 严格禁止访问 Manager。
- Router 不能调用 Manager。
- 外部用户/第三方平台不能直接调用 Manager，只能通过当前被路由到的 durable Agent 的
  management capability。

### send_message

第一版不把 `send_message` 稳定成公开 ACP extension method。

参考 OpenClaw：

- agent-to-agent message 主要通过 `sessions_send` tool 进入 Gateway/agent routing。
- ACP bridge 只按 session key 路由，不直接暴露“选 agent 发消息”的 ACP surface。
- HTTP gateway 显式拒绝直接调用 `sessions_send`。

DeepCLI 第一版：

- `send_message` 是 Router internal operation + agent tool surface。
- Probe 可以用 internal/private surface 验证。
- 不提前承诺外部 ACP API。

### 旧 Session 迁移

不做。DeepCLI 尚未发布，Primary Agent 可以作为新架构 seed record 直接创建。

### SubAgent Promotion

不做 Ephemeral Child Agent 到 durable Session Agent 的自动 promotion。未来如果用户要升级，
必须对照该 SubAgent 的 transcript、权限、工具、memory、workspace 和身份重新创建，并让
用户显式决策。

### Child Kernel

Child Kernel 统一由 Supervisor 启动和监护。Supervisor 的职责就是确保 Agent Hub、
Access Agent、Primary Agent、peer Session Agents、Child Kernels 和必要 Platform Adapters
存活。

### External ACP Runtime

默认最小权限：

- 不调用 ACP client filesystem methods。
- 不创建 ACP client terminals。
- 默认不给 filesystem write / terminal execution。
- write/execute/authenticate 必须显式 permission mediation。

参考 OpenClaw 的 ACP bridge 和 debug client：trusted core tools 可以 allowlist；
unknown/out-of-scope/dangerous tools 必须 prompt。

### Flow Execution

第一版不做。先稳定 Agent Hub、Router、Manager、Access Agent、queue/status/cancel 和 Probe。

## 数据保存细节

每个 durable Agent：

```text
~/.mustang/agents/<agent_id>/
  agent.json or AgentDefinition reference
  state/
  memory/
  skills/
  sessions/
    sessions.db
```

具体最终路径可在 ConfigManager schema 中定，但约束是：

- 不同 durable Agents 不能共享 state dir。
- PID/endpoint/heartbeat/restart count 不在 ConfigManager。
- AgentRuntimeRecord 可写 runtime state store 用于 crash diagnosis，但不能成为声明式配置。
- Access Agent 的 client connection、reply sink、route readiness 是 transient state。

Memory/Skills 权限：

- global：所有 agents 可见。
- project/workspace：当前 workspace 或显式授权 workspace 可见。
- agent-private：默认只给该 agent。

## 实现时易错点

- Probe 不是 CLI 的替代品。Probe 验证底层 WebSocket/API 闭环；CLI smoke 验证真实用户入口。
  改动 Access Agent / Router / Primary Agent 主路径时必须两者都跑。
- 不要让 Router 调 Manager。
- 不要让 Platform Adapter 绕过 Access Agent 或 Router。
- 不要把 `GatewayManager` 旧路径继续直接打 SessionManager。
- 不要把 AgentTool 接进 Agent Hub。
- 不要让 Orchestrator 理解 Router/Manager。通过 SessionManager deps 或 AgentResourceView 注入。
- 不要让 ConfigManager 保存 PID/live state。
- 不要让每个 Agent Runtime 直接写 global resources。
- 不要把 GlobalResourceMonitor 变成装满所有 Managers 的上帝对象。
- Batch B/C 只做 skeleton，资源系统逐步迁移。

## 后续可迁移到主计划的候选内容

当开始实现对应 batch 时，可以把以下内容从 notes 移到主计划或 subsystem docs：

- OpenClaw routing priority -> Router/binding policy doc。
- Hermes SessionDB WAL/retry/FTS -> SessionStore/search doc。
- `AgentContext` view APIs -> Agent Runtime subsystem doc。
- GlobalResourceMonitor revision schema -> Agent Hub subsystem doc。
- Platform Adapter context shape -> Access Agent subsystem doc。
