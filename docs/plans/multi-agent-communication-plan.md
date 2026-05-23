# 多 Agent 通信与外部 Agent 接入计划

状态: active architecture reference — partially implemented; agent-visible tool
surface superseded by
[`agent-tool-openclaw-tools-separation-plan.md`](agent-tool-openclaw-tools-separation-plan.md)
创建: 2026-05-11
参考:

- [`../kernel/architecture.md`](../kernel/architecture.md)
- [`../kernel/architecture/history/agent-control-plane.md`](../kernel/architecture/history/agent-control-plane.md)
- [`../kernel/subsystems/session.md`](../kernel/subsystems/session.md)
- [`../kernel/subsystems/tasks.md`](../kernel/subsystems/tasks.md)
- [`../kernel/interfaces/protocol.md`](../kernel/interfaces/protocol.md)
- [`../kernel/references/acpx/docs/2026-02-27-acpx-session-model.md`](../kernel/references/acpx/docs/2026-02-27-acpx-session-model.md)
- OpenClaw: `docs/concepts/multi-agent.md`
- OpenClaw: `docs/concepts/system-prompt.md`
- OpenClaw: `docs/tools/subagents.md`
- OpenClaw: `docs/tools/acp-agents.md`
- OpenClaw: `docs/reference/templates/`
- OpenClaw: `src/commands/agents.commands.add.ts`
- OpenClaw: `src/commands/agents.commands.list.ts`
- OpenClaw: `src/commands/agents.commands.delete.ts`
- OpenClaw: `src/commands/agents.commands.bind.ts`
- OpenClaw: `src/commands/agents.commands.identity.ts`
- OpenClaw: `src/commands/agents.config.ts`
- OpenClaw: `src/agents/system-prompt.ts`
- OpenClaw: `src/agents/subagent-announce.ts`
- OpenManus: `app/flow/base.py`
- OpenManus: `app/flow/planning.py`
- OpenManus: `app/tool/planning.py`
- OpenManus: `protocol/a2a/app/`
- OpenManus: `app/agent/base.py`
- OpenManus: `app/agent/toolcall.py`

## 2026-05-24 当前 Kernel 对照

这份计划不再是从零开始的 proposed plan。当前 Kernel 已经落地了
**operator / management 侧的 durable Agent 通信 hot path**：

- `/agents`、`/agent send`、`/gateways` 已进入 Kernel CommandManager catalog
  和 ACP 管理面。
- `/agent send <agent-id> <message>` 通过 `AccessRouter.deliver_turn()` 进入目标
  runtime；这是当前 durable agent message hot path。
- `/agents bind` 和 `/gateways bind` 都写 `access_channel_bindings`；
  `agent_bindings` 保持 reserved/deferred，不启用、不双写。
- Access Router 已有 route freshness / stale rejection / reconnect freshness，
  AgentManager health 能区分 unavailable/stale/fresh。
- CLI slash dispatch 已通过真实 Access Router `/session` 路径覆盖 `/agents`、
  `/agent send`、`/gateways` 等管理命令。

仍未完成的是 **agent-visible Agent Network 产品面**，也就是让普通 agent 在
对话中安全地发现、创建和联系 durable agents：

- `AgentDirectory`、`AgentSession`、`AgentMessage` 尚未作为默认
  `BUILTIN_TOOLS` 注册。
- 旧 `agents_list` / `sessions_spawn` / `sessions_send` / `subagents` 工具壳存在，
  但不是最终主路径，也未进入默认 tool snapshot。
- 尚无 spawned run registry、agent-visible directory policy、
  `tools.agentToAgent` gate、durable `AgentSession(runtime="agent")` 实现。
- `runtime="acp"` / Codex / Claude Code 外部 ACP runtime controller 仍未闭合。
- Platform Adapter inbound 到 bound agent/session 的完整路径仍未按本计划验收。

因此，本文件保留为多 Agent 架构参考和剩余能力边界；普通 agent 可见工具的
命名、实现顺序和验收以
[`agent-tool-openclaw-tools-separation-plan.md`](agent-tool-openclaw-tools-separation-plan.md)
为准。

## Implementation Driver

按当前 Kernel 状态，这个计划可以指导实现的前提是：**先做 agent-visible
Agent Network 工具闭环，再做外部 ACP runtime 和完整 Platform Adapter。**
不要从最终拓扑图倒推一次性大重构；当前已有 `/agents`、`/agent send`、
`/gateways` 管理面，应复用这些已经验过的 seams。

### Source of Truth

| 领域 | 当前真相 | 新实现必须复用 |
|---|---|---|
| Durable agent 管理 | `AgentCommandService` + `AgentManager` | 不从 tool 直接写 ResourceStore；创建/删除/bind 仍走管理面。 |
| Durable message hot path | `AccessRouter.deliver_turn()` | `AgentMessage(agentId=...)` 必须复用这个 hot path 或它的服务封装。 |
| Gateway/channel binding | `AccessRouterRepository.access_channel_bindings` | `agent_bindings` 继续 reserved/deferred，不启用、不双写。 |
| Session runtime | `AgentSessionRuntimeService` + `SessionManager` | spawned session 执行仍由目标 runtime/session queue 拥有。 |
| Local subtask compatibility | `AgentTool` + `TaskRegistry` | 只在 `AgentSession(runtime="local")` 显式使用，不能作为 durable fallback。 |

当前 `multi_agent.py` 和 `OrchestratorDeps.route_agent_message` 只适合作为历史
compatibility seam：它们通过 `module_table.agent_hub.router.route_message()` 返回
route result，并不等同于已经 E2E 证明的 `AccessRouter.deliver_turn()` hot path。
新的 `AgentMessage` 主路径不能以这个旧 closure 为验收依据。

### Implementation Slices

#### Slice 1 — Agent Network service and tool names

目标：把 agent-visible 工具名、服务边界和默认 tool snapshot 立住，但只覆盖当前
Kernel 已有能力。

改动：

- 新增 `kernel/agents/mustang/runtime/agent_network_service.py`，集中实现：
  `list_visible_agents()`、`send_message()`、`spawn_session()`、`list_runs()`、
  `stop_run()`、`steer_run()`。
- 重写或替换 `kernel/agents/mustang/tools/builtin/multi_agent.py`，主类改为
  `AgentDirectoryTool`、`AgentMessageTool`、`AgentSessionTool`。
- `BUILTIN_TOOLS` 注册 `AgentDirectory`、`AgentMessage`、`AgentSession`；
  旧 `agents_list` / `sessions_send` / `sessions_spawn` / `subagents` 若保留，
  只能作为 deprecated wrappers，不出现在推荐 prompt/tool description 主路径。
- `AgentDirectory` 第一版只返回 policy 允许的 durable agents 和 route status；
  不返回 operator-only grants、secret-like config。
- `AgentMessage(agentId=...)` 调服务层，服务层复用
  `AccessRouter.deliver_turn()` / 等价 Access Router seam；无 route 或 stale route
  返回 typed unavailable。
- `AgentSession(runtime="acp")` 在 backend 未实现时返回 typed unsupported；
  `runtime="agent"` 在 durable spawned session 未实现前也必须返回 typed
  unsupported，不能偷偷调用 `AgentTool`。
- `AgentSession(runtime="local")` 才允许投影/调用 `AgentTool`，并在结果中标记
  `compatibility=true`。

验收：

- `BUILTIN_TOOLS` 同时包含 `Agent` 和 `AgentDirectory` / `AgentMessage` /
  `AgentSession`，名称不冲突。
- 旧 snake_case 名字不是默认推荐主路径。
- policy deny 时 `AgentDirectory` 不显示目标，`AgentMessage` 拒绝发送。
- `AgentSession(runtime="agent"|"acp")` 未实现 backend 时 typed unsupported，
  不 fallback 到 `AgentTool`。
- 真实 probe `tests/probe/probe_agent_network_tools.py` 至少证明 tool snapshot、
  `AgentDirectory`、policy deny、`AgentMessage(agentId)` 走真实 Access Router
  hot path、`agent_bindings=0`。

#### Slice 2 — Spawned run registry and durable AgentSession

目标：让 `AgentSession(runtime="agent")` 创建 caller-owned durable spawned run，
并能 list/status/stop/steer。

改动：

- 新增 ResourceStore-backed spawned run registry，记录 `run_id`、
  `parent_session_id`、`requester_agent_id`、`target_agent_id`、`runtime`、`mode`、
  `session_id`、`status`、timestamps。
- `AgentSession(action="spawn", runtime="agent")` 创建目标 session/run metadata；
  发送首条 prompt 仍通过 Access Router / target runtime seam。
- `AgentMessage(sessionId|runId)` 投递到已有 spawned session；不创建新 session。
- `AgentSession(action="list"|"status"|"stop"|"steer")` 只能操作 caller-owned runs。

验收：

- 创建两个 durable agents 后，caller 只能看到 policy 允许目标。
- `AgentSession(runtime="agent", mode="session")` 返回 `runId/sessionId`。
- `AgentMessage(runId|sessionId)` 可继续投递到该 spawned session。
- stop/steer 权限按 owner 检查。
- 真实 probe 覆盖 spawn -> message -> list -> stop。

#### Slice 3 — External ACP runtime backend

目标：Codex / Claude Code 等 ACP harness 变成可管理 runtime，而不是普通 tool call。

改动：

- 新增 `kernel/agent_hub/manager/runtime_backends/base.py`、`mustang.py`、`acp.py`
  或等价 controller 层。
- 将现有 `ExternalAcpRuntimeAdapter` 作为底层 stdio client 种子，上移到 Hub
  runtime backend 或由 backend 包装。
- `AgentSession(runtime="acp")` 通过 `AcpRuntimeController` 创建 / resume /
  prompt / cancel / close。
- ACP runtime-initiated `session/request_permission` 通过 Runtime -> Hub -> Access
  tunnel 回 CLI/Platform；`fs/*`、`terminal/*` 默认 fail closed。

验收：

- fake ACP runtime probe：initialize/new/prompt/cancel/close/status。
- process crash 返回 typed failed/unavailable，不污染 run registry。
- permission request round trip 走真实 Access path。

#### Slice 4 — Platform Adapter inbound and bindings

目标：外部平台消息也走同一 Agent Network route truth。

改动：

- 在 `kernel/agents/access/platforms/` 或当前 `access_router` adapter 体系下补齐
  fake platform adapter。
- inbound envelope 根据 `access_channel_bindings` route 到 target agent/session；
  most-specific binding 和 idempotency 必须可测。
- 出站 reply sink 从 runtime result/update 回平台 adapter，不直连 SessionManager。

验收：

- fake inbound -> bound agent -> reply sink E2E。
- duplicate platform message id 不重复 prompt。
- missing binding 返回 typed unavailable。

#### Slice 5 — Main/primary naming and per-agent resources

目标：把单 `primary` 兼容路径收窄为默认 `main` 的别名，并为多 Mustang runtime
实例准备资源边界。

改动：

- 对外文档、command output、AgentDirectory 默认展示 `main`；内部 `primary`
  只作为兼容 alias。
- Agent creation/startup 拒绝复用 `agentDir` 或 session store。
- 每个 agent 的 workspace、agentDir、session DB/transcript、prompt/bootstrap
  scope 明确落到 `AgentResources`。

验收：

- `primary` alias 仍兼容已有 runtime path。
- 新 agent 资源目录不串线。
- layout/import tests 证明 Mustang runtime 多实例不共享 mutable singleton。

## 结论

DeepCLI 要实现的是 OpenClaw 的核心模型，不是照搬 `acpx`：

```text
平台 / CLI / Home Screen
  -> Access Agent
  -> Agent Hub.Router
  -> agent session

agent A
  -> Agent Hub.Router
  -> agent B

agent
  -> AgentSession(runtime="agent")
  -> sub-agent session

ACP agent / ACP session(Codex / Claude Code / Gemini CLI)
  -> ACP Runtime Controller
  -> Agent Hub 统一 agentId / session key / queue / bindings
```

内部多 Agent 通信的真相是 **agentId + per-agent sessions + Hub Router +
per-session queue + agent-visible Agent Network tools**。早期草案使用
OpenClaw 原名 `sessions_*`，当前 Kernel 计划改为 DeepCLI-native
`AgentDirectory` / `AgentSession` / `AgentMessage`。`acpx` 只作为外部 ACP
harness 的参考实现和可选兼容目标；DeepCLI 不把 `acpx` 当内部总线，也不让
Access/Gateway 直接绕过 Hub。

## 原始问题

现在 DeepCLI 已经有 Supervisor、Agent Hub、Access Agent、Primary Runtime。
但产品上仍主要是单 `primary` agent；对外术语需要迁移到 OpenClaw 的
default agent / `main` 语义：

- 平台入口尚未变成真正的 Access Agent Platform Adapter。
- Agent Hub Router 只支持默认 route / 显式 agent id 的基础解析。
- `AgentTool` 创建的是私有 child run，不是 OpenClaw 意义上的
  `sub-agent session`，也不能被 thread bindings 绑定。
- `external_acp.py` 已有最小 ACP stdio adapter，但还没有纳入 Hub 管理、
  session identity、queue、状态和权限闭环。

如果直接给 Gateway adapter 加“找某个 SessionManager 调用”的逻辑，会退回旧
GatewayManager 设计，破坏当前 Agent Control Plane 架构。正确落点必须是：
平台只做 ingress/reply sink，路由和 session-to-session 统一进 Agent Hub。

## 借鉴 OpenClaw 的设计点

OpenClaw 的多 Agent 不是靠 ACPX 互调，而是靠这些结构：

| OpenClaw 机制 | DeepCLI 对应 |
|---|---|
| `agentId` 是一个完整 brain | `AgentDefinition.id`，对应 `workspace`、`agentDir`、`sessions`、资源 scope |
| 每个 agent 独立 workspace / agentDir / sessions | `~/.deepcli/agents/<agentId>/` + `AgentResources` |
| gateway bindings 决定 inbound 到哪个 agent | Access Platform Adapter 归一化 ingress，Hub RoutingSnapshot 决定 target |
| `sessions_spawn` 创建 sub-agent 或 ACP session | DeepCLI 当前目标为 `AgentSession`，内部复用 Hub / Access Router route truth |
| `sessions_send` 跨 session 投递 | DeepCLI 当前目标为 `AgentMessage`，经 Access Router / Hub-owned route truth 进入目标 session queue |
| per-session queue 防止同一会话并发 turn 碰撞 | 复用 `SessionManager` 单 session FIFO；Hub 只暴露 queue 状态，不替 SessionManager 执行 turn |
| ACPX 只接外部 harness | `AgentRuntimeKind.acp` + `AcpRuntimeController` |

因此 DeepCLI 的核心策略是：

1. `agentId` 是 Hub 可见的路由身份；默认 agent 对外叫 `main`。
2. `sub-agent session` 是 `agent:<agentId>:subagent:<uuid>`，可 one-shot，
   也可通过 thread bindings 变成可继续对话的 session。
3. 外部 Claude Code / Codex 是 `ACP agent / ACP session`，不是内部协议。
4. Gateway/Platform Adapter 永远不直接调用 Mustang `SessionManager`。

## Gateway 边界

OpenClaw 的 Gateway 很重要，但它不是 agent 间通信的消息总线。

OpenClaw Gateway 负责：

- 长驻 daemon，承载 WhatsApp/Telegram/Discord/Slack/WebChat 等 messaging surfaces。
- 暴露 typed WebSocket API，CLI、Web UI、automation、nodes 都连 Gateway。
- 拥有 session state；UI clients 查询 Gateway，而不是直接读本地 transcript。
- 通过 `bindings[]` 把外部 inbound message 路由到某个 `agentId` / session。
- 执行 Gateway RPC，例如 `agent`、`agent.wait`，并推送 stream / session events。

OpenClaw agent 间通信依靠：

- `AgentMessage`：把消息送进另一个 durable agent 或 session，可等待结果；transcript 标记
  `message.provenance.kind = "inter_session"`，并有 reply-back loop / announce step。
- `AgentSession`：创建 `agent:<agentId>:subagent:<uuid>` 或
  `agent:<agentId>:acp:<uuid>`，completion 通过 announce 回到 requester。
- session routing + per-session queue：同一目标 session 串行处理 turn，避免并发碰撞。

因此 DeepCLI 现在已有 Gateway 层时，正确对齐方式是：

- Gateway/Access 层负责外部入口、WS/RPC、reply sink、bindings、session state projection。
- Agent Hub/Session 层负责 `AgentMessage`、`AgentSession`、queue、completion、
  provenance、policy。
- 不让 Gateway adapter 直接调用某个 agent 的 `SessionManager`；它只能把 inbound
  规范化后交给 Hub/Router。

## 目标能力

目标不是做一个缩水的 MVP，而是让 DeepCLI/Mustang 在多 Agent 能力上与
OpenClaw 对齐。完成态必须支持：

- 多个 agents 并存：`main`、`research`、`coding` 等。
- 每个 agent 有自己的 `workspace`、`agentDir`、`sessions`、资源 scope。
- Gateway / CLI / Probe / Home Screen 消息可以按 bindings 或显式 target 路由到不同 agent。
- `AgentMessage` 能把消息投递到另一个 durable agent/session，支持 wait、timeout、accepted、
  reply-back loop、announce step、inter-session provenance。
- `AgentSession` 能创建 durable spawned session 或 `ACP session`，支持 run/session
  mode、thread bindings、cleanup、timeout、sandbox inheritance guard、allowlist。
- `AgentDirectory` 能发现当前 session 允许通信和 spawn 的 durable agents。
- `AgentSession` 控制面能 list/info/log/stop/steer 当前 session 的 spawned runs。
- Codex / Claude Code 等外部 ACP-compatible harness 可以作为 `ACP runtime`
  被创建、prompt、cancel、status、close。
- 所有权限请求、stream update、最终回复都仍从 Runtime -> Hub -> Access -> client/reply sink
  走同一条闭环。

## 非目标

- 不把 `acpx` 作为 DeepCLI 内部 Agent Hub 协议。
- 不复活 Mustang 内部 `GatewayManager` 作为新平台入口。
- 不让外部 ACP agent 直接访问 DeepCLI 内部 Python API。
- 不把 Claude Code/Codex 当作 `AgentTool` 的子进程黑盒；它们必须走 runtime
  controller，具备身份、状态、queue、cancel 和关闭语义。
- 不在第一版实现跨机器 federation。所有 agent 先在同一本地 Supervisor 下。

## 术语

本计划复用 OpenClaw 的长期 agent / session / binding 概念，但 agent-visible
工具名采用 DeepCLI-native `AgentDirectory` / `AgentMessage` / `AgentSession`。
DeepCLI 内部已有 `primary` / `AgentTool` / `external_acp` 等兼容名可以保留为
实现细节，但当前文档、配置和 prompt 要尽量使用下表。

| 术语 | 定义 |
|---|---|
| `agentId` | 一个完整 brain 的稳定 id；拥有自己的 `workspace`、`agentDir` 和 `sessions`。 |
| `main` / default agent | OpenClaw 默认 agent。DeepCLI 当前 `primary` 应作为兼容别名映射到 `main`。 |
| `workspace` | agent 的默认工作目录；不是硬 sandbox。 |
| `agentDir` | agent 的状态目录，存 auth profiles、model registry、per-agent config。 |
| `sessions` | per-agent session store 和 transcript 目录。 |
| `binding` / `bindings[]` | 按 `(channel, accountId, peer)` 等上下文把 inbound route 到某个 `agentId`。 |
| `accountId` | 某个 channel 的账号实例，例如 WhatsApp `"personal"` / `"biz"`。 |
| `peer` | channel 内的 DM、group、thread、room 等对话目标。 |
| `session key` | OpenClaw 风格 session 主键，例如 `agent:main:main`。 |
| `sub-agent` / `sub-agent session` | 由 `AgentSession` 创建的后台 agent run，key 形如 `agent:<agentId>:subagent:<uuid>`。 |
| `ACP agent` / `ACP session` | Codex / Claude Code / Gemini CLI 等 ACP harness，key 形如 `agent:<agentId>:acp:<uuid>`。 |
| `AgentMessage` | 向另一个 durable agent 或 spawned session 投递消息的目标工具名。 |
| `AgentSession` | 创建/查看/停止/steer spawned agent 或 ACP session 的目标工具名。 |
| `AgentDirectory` | 发现当前 session 可见/可发送/可 spawn 的 agents。 |
| Agent Directory | Hub 维护的 policy-filtered agent 目录；agent 只能看到被允许通信的目标。 |

## 目标拓扑

```text
Supervisor
  |-- Agent Hub
  |     |-- Manager
  |     |-- Router
  |     `-- ResourceRevisionMonitor
  |
  |-- Access Agent
  |     |-- ACP WebSocket: CLI / Probe / Home Screen
  |     `-- Platform Adapters: Discord / Telegram / Webhook
  |
  |-- Mustang Runtime: main
  |-- Mustang Runtime: research
  |-- Mustang Runtime: coding
  `-- ACP Runtime: codex / claude-code
```

消息路径：

```text
用户消息:
Access ingress -> RouterFrame(USER_MESSAGE) -> Router -> target runtime

session-to-session:
source runtime tool -> RouterFrame(AGENT_MESSAGE) -> Router -> target runtime

Agent-to-user:
runtime update/reply -> RouterFrame(AGENT_UPDATE) -> Access reply_sink

ACP session:
Router -> AcpRuntimeController -> AcpRuntimeAdapter -> ACP stdio process
```

## 核心设计确认

这一节直接回答四个必须闭合的问题：怎么复用 Mustang、agent 间怎么通信、
agent 怎么通过 Gateway 对外通信、agent 怎么被增删改查。

### 1. 通过复用 Mustang 实现多 Agent

OpenClaw 的做法是让每个 `agentId` 成为一个完整 brain：独立 `workspace`、
`agentDir`、auth profiles、model config、skills、session store 和 transcripts。
DeepCLI 不复制 Mustang 代码，也不把多个 agents 塞进同一个 `primary`
SessionManager；正确做法是把现有 Mustang Agent runtime 作为可多实例化 runtime。

DeepCLI 落地规则：

- Agent Hub Manager 从 `AgentDefinition` 启动/注册多个 Mustang runtime instance。
- 每个 Mustang runtime instance 绑定一个 `agentId` 和一套 `AgentResources`。
- `AgentResources` 至少包含 `workspace`、`agentDir`、session DB/transcript dir、
  config/profile scope、prompt/bootstrap files、tool/skill/memory scope。
- 现有 Mustang 的 `SessionManager`、orchestrator、tool registry、tool authz、
  LLM/model routing、MCP、skills、hooks、memory、tasks 都复用，但它们必须从当前
  runtime 的 resource scope 取依赖。
- `main` 是默认 agent；现有 `primary` 只作为兼容别名映射到 `main`。
- 启动期必须拒绝两个 agents 复用同一个 `agentDir` 或 session store，避免 auth 和
  transcript 串线。

这和 OpenClaw 的 `~/.openclaw/agents/<agentId>/agent`、
`~/.openclaw/agents/<agentId>/sessions` 对齐；DeepCLI 对应目录使用
`~/.deepcli/agents/<agentId>/agent` 和 per-agent sessions。

### 2. 多 Agent 之间怎么通信

OpenClaw 不是 agent object 互调，也不是 Gateway adapter 转发。它是
session-to-session messaging：

- `AgentMessage` 向已有 durable agent/session 投递消息，不创建新 session。
- `AgentSession` 创建新的 spawned agent session 或 `ACP session`。
- Hub/Router 做 target 解析、policy、correlation/status projection。
- 目标 runtime 的 per-session queue 负责 FIFO 和 turn execution。
- transcript 记录 inter-session provenance，便于 UI、审计和调试区分外部用户输入与
  agent 间指令。

DeepCLI 落地规则：

- Agent 可见入口只暴露 DeepCLI-native 工具名：`AgentDirectory`、
  `AgentMessage`、`AgentSession`。
- `AgentMessage` 支持 `agentId`/`sessionId`/`runId`、accepted、wait、timeout、
  reply-back loop、announce step、max ping-pong turns。
- `AgentSession` 支持 `runtime: "agent" | "acp" | "local"`、`mode: "run" | "session"`、
  `thread`、cleanup、timeout、sandbox inheritance guard、attachments。
- Agent Hub 是 control plane bus：统一路由、权限、状态和事件；它不持久化对话队列，
  不执行 LLM turn，不替代 SessionManager。

### 3. 多 Agent 怎么通过 Gateway 对外通信

OpenClaw Gateway 承载外部 messaging surfaces，并通过 `bindings[]` 把 inbound
route 到 agent。它不是 agent 间通信总线。

DeepCLI 落地规则：

- Gateway/Access 负责外部入口：WS/RPC、Webhook、platform event、authn、
  `InboundEnvelope` normalization、`reply_sink`。
- Gateway/Access 维护或投影 platform conversation state，但最终 target 解析交给
  Hub Router。
- `bindings[]` 支持 OpenClaw 语义：`channel`、`accountId`、`peer`、
  `parentPeer`、guild/team/role 等匹配，most-specific wins。
- 支持 config bindings、current-conversation bindings、thread bindings、
  persistent ACP bindings。
- 出站回复从 runtime update 走 Hub -> Access -> reply sink；platform adapter 不直接
  调用 Mustang `SessionManager`。
- 同一外部 conversation 的 follow-up 如果已有 binding，就继续路由到绑定 session。

### 4. Agent 的增加、删除、修改、查询

OpenClaw 的管理面是 `openclaw agents`：

- `agents add`：创建或更新 agent config，设置 `agentId`、`workspace`、
  `agentDir`、model/auth、channel accounts、bindings，并 bootstrap workspace/sessions。
- `agents list --bindings --json`：查询 agent summary、identity、workspace、
  `agentDir`、model、bindings、providers 和 default 标记。
- `agents bind` / `agents unbind` / `agents bindings`：管理 routing bindings。
- `agents set-identity`：从 `IDENTITY.md` 或显式参数写入 `agents.list[].identity`。
- `agents delete`：禁止删除 `main`，确认后 prune config，移除相关 bindings 和
  `tools.agentToAgent.allow`，并把 workspace、`agentDir`、sessions 移到 trash。

DeepCLI 要提供同等能力，但入口可以同时有 CLI、Home Screen 和 ACP/JSON-RPC：

| 操作 | OpenClaw 参考 | DeepCLI 行为 |
|---|---|---|
| Create | `agents add` | `AgentDefinition` upsert；创建 `workspace`、`agentDir`、sessions；可复制 auth；可配置 model/runtime；可添加 bindings。 |
| Read/List | `agents list`, `agents bindings` | 返回 Agent Directory / management summary；支持 JSON；可显示 identity、routes、providers、status、queue。 |
| Update | `agents add` update path, `set-identity`, `bind`, `unbind` | 修改 name、workspace、agentDir、model/runtime、identity、policy、bindings；需要重启/重载 runtime 时由 Hub Manager 执行。 |
| Delete | `agents delete` | 禁止删除 `main`；停止 runtime；prune config、bindings、allowlists；trash workspace/agentDir/sessions；发布 deletion event。 |

Agent 自己是否能增删改查 agents 必须受 policy 控制。默认只允许查询
policy-filtered Agent Directory；创建、修改、删除 agent 属于管理操作，需要用户授权或
operator scope，不能让普通 agent 靠 prompt 自行扩权。

## OpenClaw Agent 创建 prompts 调查

OpenClaw 创建 agent 的“prompt”分三类：CLI wizard 提问、workspace bootstrap
模板注入、运行时 system/sub-agent prompt。DeepCLI 要复用这三层结构，而不是只
做一条 `create_agent()` API。

### `openclaw agents add` wizard

源码位置：OpenClaw `src/commands/agents.commands.add.ts`。

交互式创建流程的用户可见 prompts：

- intro: `Add OpenClaw agent`
- `Agent name`
- 若输入被规范化：`Normalized id to "<agentId>".`
- 若已存在：`Agent "<agentId>" already exists. Update it?`
- `Workspace directory`
- 可选复制默认 agent 认证：`Copy auth profiles from "<defaultAgentId>"?`
- 可选配置模型/认证：`Configure model/auth for this agent now?`
- channel setup 后可选创建 bindings：`Route selected channels to this agent now? (bindings)`
- 如果不创建 bindings：提示 routing 未改变，并指向 multi-agent docs
- outro: `Agent "<agentId>" ready.`

非交互模式要求 `--workspace` 和 agent name；会 normalize `agentId`，禁止保留
id `main`，写入 `agents.list[]`，然后调用 workspace/session bootstrap。

DeepCLI 落地：

- CLI/ACP/Home Screen 创建 agent 时也保留这组概念字段：
  `agentId`、`workspace`、`agentDir`、model/auth、channel accounts、bindings。
- 当前 `primary` 只做兼容别名；新建和展示默认用 `main`。
- 创建完成前必须完成 workspace bootstrap 和 per-agent session store 初始化。

### Workspace bootstrap templates

源码位置：OpenClaw `docs/reference/templates/`，由
`src/agents/workspace.ts` 写入缺失文件。

| 文件 | OpenClaw 用途 | DeepCLI 决策 |
|---|---|---|
| `AGENTS.md` | workspace 总规则：home、first run、startup、memory、red lines、group chat、tools、heartbeats。 | 作为每个 `workspace` 的主入口模板；DeepCLI 项目已有 AGENTS 规则时只追加/合并，不覆盖。 |
| `SOUL.md` | agent persona、tone、boundaries。 | 保留为 per-agent persona 文件，让不同 `agentId` 可以有不同人格。 |
| `IDENTITY.md` | name、nature、vibe、emoji/avatar。 | 用于 Home Screen 和平台 reply prefix 的身份展示。 |
| `USER.md` | 用户称呼、pronouns、timezone、notes。 | per-agent 用户偏好；共享用户资料必须显式引用，不默认跨 agent 共享。 |
| `TOOLS.md` | 本地工具/设备/平台格式等 notes。 | 放本地工具说明；工具可用性仍由 tool policy 决定。 |
| `HEARTBEAT.md` | heartbeat 主动任务清单。 | 未来 cron/heartbeat 复用；空文件也可存在。 |
| `BOOTSTRAP.md` | 首次唤醒 ritual：确认 agent 是谁、用户是谁，写 IDENTITY/USER/SOUL，完成后删除。 | 新 agent 首次进入 main session 时注入；完成后删除或归档。 |
| `MEMORY.md` / `memory.md` | long-term curated memory。 | main/direct session 才加载；group/shared/ACP session 默认不加载。 |

OpenClaw 的 `AGENTS.md` 还明确要求 session startup 先读 `SOUL.md`、`USER.md`、
today/yesterday daily memory，main direct chat 再读 `MEMORY.md`。DeepCLI 应把这
个启动顺序写进 per-agent system prompt，而不是依赖模型猜。

### System prompt 结构

源码位置：OpenClaw `docs/concepts/system-prompt.md` 和
`src/agents/system-prompt.ts`。

OpenClaw 拥有 system prompt，固定组合这些 section：Tooling、Safety、
Skills、Self-Update、Workspace、Documentation、Workspace Files、Sandbox、
Current Date & Time、Reply Tags、Heartbeats、Runtime、Reasoning。

关键行为：

- base identity 是“运行在 OpenClaw 内的 personal assistant”。DeepCLI 应改成
  “运行在 DeepCLI/Mustang kernel 内”，但保留 section 结构。
- Tooling section 会提示：复杂/较长任务用 `AgentSession` 创建 spawned agent session；
  如果用户说“用 codex / claude code / cursor / gemini 做”，应视为 ACP harness
  intent，调用 `AgentSession` 且 `runtime: "acp"`。
- ACP harness 不走 `Agent` local subtask 或本地 shell 逃逸启动；它必须通过
  `AgentSession(runtime="acp")` 进入 Hub/ACP controller。
- 不要 busy-poll `AgentSession(action="list")`；completion 是 push-based。
- Safety 是 advisory，硬边界仍靠 tool policy、exec approvals、sandbox、channel
  allowlists。
- Runtime section 应带 `agent=<agentId>`、host、repo、OS、model、channel、
  capabilities、thinking 等一行诊断信息。

Prompt modes：

- `full`：默认 agent session。
- `minimal`：sub-agent 使用，省掉 Skills、Memory Recall、Self-Update、User
  Identity、Reply Tags、Messaging、Heartbeats 等重 section。
- `none`：只保留 base identity。

OpenClaw 文档说 sub-agent sessions 只注入 `AGENTS.md` 和 `TOOLS.md`；实现里有
更宽的 minimal allowlist。DeepCLI 要显式选择文档语义：sub-agent 默认只注入
`AGENTS.md` + `TOOLS.md`，除非 spawn 请求明确要求 persona/memory scope。

### Sub-agent prompt

源码位置：OpenClaw `src/agents/subagent-announce.ts` 的
`buildSubagentSystemPrompt`。

DeepCLI 的 `AgentSession(runtime="agent")` 应注入一个 `# Subagent Context`
块，包含这些规则：

- 你是 parent orchestrator 为特定任务创建的 **subagent**，不是 parent。
- 聚焦完成 task；最终消息会自动回传给 requester/main agent。
- 不做 heartbeat、proactive side quest、长期状态维护。
- descendant completion 是 push-based；不要用 sleep/list/history 轮询。
- 如果工具输出被截断，只重读需要的片段。
- 输出要简洁说明完成了什么、发现了什么、相关细节。
- 除非 task 明确要求，不和用户/外部平台对话，不伪装 parent。
- 如果允许嵌套 spawn，子 session 仍用 `AgentSession`；ACP harness 子任务必须
  用 `runtime: "acp"` 和明确 `agentId`。

### ACP agent prompt guidance

源码位置：OpenClaw `docs/tools/acp-agents.md`。

OpenClaw 区分：

- ACP session：`agent:<agentId>:acp:<uuid>`，spawn tool 是
  `AgentSession({ runtime: "acp" })`。
- Sub-agent session：`agent:<agentId>:subagent:<uuid>`，spawn tool 是
  `AgentSession({ runtime: "agent" })`。

DeepCLI 也按这个模型接入 Codex / Claude Code：

- `agentId` 是 ACP harness target，例如 `codex`、`claude`。
- `mode: "run"` 是一次性任务；`mode: "session"` 需要 current-conversation 或
  thread binding。
- ACP session 不支持 required sandbox；如果 caller 在 sandbox 内，默认拒绝或只允许
  `sandbox: "inherit"` 的安全降级。
- persistent ACP conversation 使用 `bindings[].type="acp"` 和
  `bindings[].acp.*` override，而不是让平台 adapter 直接启动外部进程。

## OpenManus 调研

OpenManus 的多 agent 实现不是 OpenClaw-style 的 session-per-agent 架构。它主要是
单进程 `Flow` 编排多个 `BaseAgent` 实例：planner 创建 plan，然后按 step type
选择 executor agent 执行当前 step。它没有 OpenClaw 那种 `agentId` / `agentDir` /
per-agent `sessions` / `bindings[]` / thread-bound session 的长期运行模型。

### 可借鉴部分

| OpenManus 机制 | 借鉴方式 |
|---|---|
| `BaseFlow` 持有 `agents: Dict[str, BaseAgent]` 和 `primary_agent_key` | DeepCLI 可在 `main` agent 内实现“flow/orchestrator session”，用 `AgentDirectory` 发现可用 agent，再用 `AgentMessage`/`AgentSession` 执行 step。 |
| `PlanningFlow` 用 `PlanningTool` 创建 plan、标记 `in_progress/completed/blocked` | 可作为 DeepCLI 的 session-level planning tool 参考，让 orchestrator session 对跨 agent 工作有可观测 plan 状态。 |
| plan step 支持 `[AGENT_NAME]` 选择 executor | 可映射为 OpenClaw-style agent selection：plan step metadata 里记录目标 `agentId`，执行时调用 `AgentMessage` 或 `AgentSession`。 |
| `_execute_step` 给 executor 注入当前 plan status 和当前 step | 值得保留：跨 agent 委派时应带 compact plan context、当前 step、验收标准，而不是只发一句 task。 |
| `ToolCollection` 有 tool map、重复工具名跳过、统一 `execute` 错误包装 | 可借鉴为 session tools registry 的冲突检测和 tool result 规范化。 |
| `BaseAgent.is_stuck()` 检测重复 assistant content 并插入 anti-stuck prompt | 可作为 session loop 的轻量 stuck detector；但应做成可观测 event，不要静默改 prompt。 |
| `protocol/a2a/app` 把 Manus 暴露为 A2A AgentCard + skills | 对“外部 Agent 接入”有参考：DeepCLI 可以把 A2A agent 视为一种 external runtime backend，类似 ACP runtime。 |

### 不应照搬部分

- OpenManus 的 Flow agents 是同一进程里的对象引用，不是独立 `agentId` 生命周期；
  不具备 per-agent auth、workspace、session store、bindings。
- `PlanningFlow` 串行执行 step，executor 直接 `agent.run(step_prompt)`；这不能替代
  OpenClaw 的 queue 和 transcript provenance；DeepCLI 对应工具名是
  `AgentMessage` / `AgentSession`。
- `PlanningTool.plans` 是内存 dict；DeepCLI 需要持久化 plan/session 状态，并能在
  Gateway/Home Screen 展示。
- OpenManus A2A adapter 当前非 streaming、cancel 未实现、task store 是 in-memory，
  每次请求创建 agent；只能作为外部协议接入参考，不能作为完成态。
- OpenManus prompt 比较单体化；DeepCLI 仍应采用 OpenClaw 的 workspace bootstrap
  和 system prompt sections。

### 对本计划的调整

OpenManus 不改变核心目标：DeepCLI 多 agent 通信仍对齐 OpenClaw，靠
`AgentMessage` / `AgentSession`、session routing、per-session queue、bindings。
它补充的是一个“orchestrator flow”层：

- 在某个 agent session 内创建 durable plan。
- plan step 可以声明目标 `agentId`、runtime、mode、验收标准。
- 执行 step 时，对已有长期 session 用 `AgentMessage`。
- 执行 step 时，对一次性委派或 ACP harness 用 `AgentSession`。
- 每次 step completion 回写 plan 状态和 notes。
- Home Screen / Gateway session view 能看到 plan、step、target session、runId。

## 数据模型

### AgentDefinition

现有 `AgentDefinition` 已经有正确骨架。需要补齐运行时语义：

```python
AgentDefinition(
    id="codex",
    role="session",
    workspace="~/work/repo",
    agent_dir="~/.deepcli/agents/codex/agent",
    runtime={
        "kind": "acp",
        "command": ("codex", "acp"),
        "profile": "codex",
    },
    bindings={...},
    resources={...},
)
```

规则：

- `id` 对外命名为 `agentId`，是 Hub 路由身份，不等同于 provider session id。
- `agent_dir` 对外命名为 `agentDir`，永远 per-agent，不能复用 `main`。
- `runtime.kind` 决定 Manager 选择哪个 `AgentRuntimeController`。
- `bindings` 只声明入口映射；具体 session id 由目标 runtime 创建/恢复。

### AgentIdentity

现有 `AgentIdentity` 应成为所有 runtime 的统一身份投影：

- 原生 Mustang agent：`mustang_session_id`
- 外部 ACP agent：`acp_session_id`、`provider_session_id`
- 兼容 acpx：保留 `acpx_record_id`，但只作为 metadata，不作为内部主键

内部主键必须是：

```text
agent_id + deepcli_session_id
```

外部 runtime 返回的 id 只能用于恢复/诊断。

### Conversation Binding

需要新增 Access/Hub 共享的 binding store：

```text
~/.deepcli/state/access/bindings.db
```

最小字段：

- `binding_id`
- `platform`
- `account_id`
- `conversation_id`
- `target_agent_id`
- `target_session_id`
- `runtime_kind`
- `mode`: `persistent | oneshot`
- `created_at`
- `last_activity_at`
- `idle_expires_at`
- `max_expires_at`
- `metadata`

为什么在 Access state：平台 conversation 的规范化、thread id、reply sink 都属于
Access/Platform Adapter 领域。Hub 只消费 materialized binding snapshot，不保存
平台协议细节。

## Hub Router 计划

### Route Resolution

Router 需要从现在的 “default / explicit agent id” 扩展为四层解析：

1. 显式 `target.agent_id`
2. conversation binding：`platform/account/conversation -> agent/session`
3. config binding：`AgentDefinition.bindings.platforms`
4. `native_default`

解析结果不是只有 `target_agent_id`，还应包含目标 session hint：

```python
RoutedRouterFrame(
    target_agent_id="coding",
    target_session_id="...",
    frame=...
)
```

如果没有 session hint，目标 runtime 自行使用 main session 或创建新 session。

### RouterFrame Payload

`RouterFrame.payload` 需要约定最小消息形态：

```json
{
  "text": "...",
  "attachments": [],
  "clientTurnId": "...",
  "mode": "prompt | message | announce",
  "delivery": {
    "replySink": "...",
    "platform": "...",
    "conversationId": "..."
  }
}
```

`mode` 的含义：

- `prompt`: 用户或 agent 要求目标 agent 开始一个 turn。
- `message`: 投递上下文，不一定要求目标 agent 对用户可见回复。
- `announce`: runtime-generated completion event，要求接收方总结/转述或静默。

### Queue 边界

Hub 不运行 LLM turn，也不替 SessionManager 串行 prompt。Hub 只做：

- route
- task id / correlation id
- status projection
- cancel routing
- queue depth snapshot

同一 session 的 FIFO 仍由目标 runtime 持有：

- Mustang runtime: `SessionManager` queue
- ACP runtime: `AcpRuntimeController` 的 per-session actor queue

## Access Platform Adapter 计划

旧 `kernel.agents.mustang.gateways` 只保留为迁移参考。新入口在 Access Agent：

```text
kernel/agents/access/platforms/
  base.py
  bindings.py
  registry.py
  webhook_routes.py
  discord/
  telegram/
```

Adapter 只负责：

- 认证平台 webhook / gateway event
- 规范化 `InboundEnvelope`
- 生成稳定 `conversation_id`
- 创建 `reply_sink`
- 把消息交给 Hub Router
- 根据 Hub/Runtime update 向平台发送 reply / reaction / typing

Adapter 不负责：

- 选择最终 agent
- 创建 Mustang session
- 调用 `SessionManager`
- 执行 slash command 业务逻辑

## Agent 工具面

本节最初直接复用 OpenClaw 的 `agents_list` / `sessions_send` /
`sessions_spawn` / `subagents` 命名。当前 Kernel 已经有 `/agents`、`/agent send`
和 `/gateways` 管理面，为避免把管理命令、Claude Code-compatible `Agent` 工具、
以及 durable Agent Network 混在一起，最终 agent-visible 工具名以
[`agent-tool-openclaw-tools-separation-plan.md`](agent-tool-openclaw-tools-separation-plan.md)
为准：

| 旧草案名 | 当前目标名 | 说明 |
|---|---|---|
| `agents_list` | `AgentDirectory` | 只读发现当前 caller 可见、可联系的 durable agents。 |
| `sessions_send` | `AgentMessage` | 向已有 durable agent 或 spawned session 投递消息。 |
| `sessions_spawn` | `AgentSession(action="spawn")` | 创建 caller-owned spawned agent session；不得隐式回退到 `AgentTool`。 |
| `subagents` | `AgentSession(action="list"|"stop"|"steer")` | 控制 caller-owned spawned runs；local `Agent` task 只能作为 compatibility projection。 |

下方 OpenClaw 命名保留为历史语义参考，不再作为最终用户可见工具名。

### Agent 如何选择通信对象

Agent 不应该靠猜名字或广播来找目标。选择目标必须走这条决策链：

1. 用户显式指定优先：例如“让 `coding` 看一下”“交给 Codex”“问 research agent”，
   直接解析为 `agentId`、`sessionKey` 或 ACP target。
2. 当前 conversation/thread binding 优先：如果当前对话已绑定到某个 session，
   follow-up 继续发给该 session，不重新选择。
3. Agent 调 `AgentDirectory` 获取 Hub 过滤后的 Agent Directory。目录只返回当前
   session 被 policy 允许看到、发送或 spawn 的目标。
4. Orchestrator flow / durable plan 可以在 step metadata 里显式记录目标：
   `targetAgentId`、`targetSessionKey`、`runtime`、`mode`、`acceptance`。
5. 如果用户意图和目录信息仍然歧义，agent 必须问用户；不能擅自把任务发给多个
   agent 试错。

`AgentDirectory` 返回的信息必须足够支持选择，而不只是 id 列表：

- `agentId`
- `name`
- `description`
- `role`
- `capabilities`
- `labels`
- `runtimeKind`
- `workspaceHint`
- `status`
- `canSend`
- `canSpawn`
- `allowedModes`
- `whenToUse`
- `examples`

目录必须由 Hub 按 `tools.agentToAgent`、`agentSession.allowAgents`、
`acp.allowedAgents`、当前 session scope、sandbox 状态过滤。Agent 不能先看到全局
所有 agents 再靠 prompt 自律。

### `AgentDirectory`（原 `agents_list`）

返回当前 session 可见、可发送、可 spawn 的 agents。

输出字段：

- `agentId`
- `name`
- `description`
- `role`
- `capabilities`
- `runtimeKind`
- `status`
- `labels`
- `canSend`
- `canSpawn`
- `allowedModes`
- `whenToUse`
- `examples`

### `AgentMessage`（原 `sessions_send`）

发送消息到另一个 session。

输入：

- `agentId | sessionKey | sessionId | label`
- `message`
- `mode`: `message | prompt`
- `timeoutSeconds`

行为：

- 通过 Access Router / Hub-owned route truth 投递 agent message
- 默认不直接对用户发消息
- 如果需要用户可见结果，返回目标 agent 的最新 assistant reply 或 accepted 状态

### `AgentSession(action="spawn")`（原 `sessions_spawn`）

创建新的 `sub-agent session` 或 `ACP session`。

输入：

- `agentId?`
- `runtime`: `agent | acp | local`
- `task`
- `workspace`
- `mode`: `run | session`
- `thread`: `false | true`
- `bind`: `none | current_conversation | new_thread`
- `model/profile`

行为：

- durable agent spawn 走 Agent Network service / Hub-owned route truth 创建
  caller-owned run metadata 和目标 session。
- ACP spawn 走 Hub Manager 创建 `agent:<agentId>:acp:<uuid>` 并启动/复用 ACP controller。
- `mode=run` 是 one-shot，完成后 close/release runtime，但保留 transcript。
- `mode=session` 需要 `thread=true` 或 current-conversation binding，创建可继续对话的
  persistent binding。

### 与现有 `AgentTool` 的关系

`AgentTool` 保持为 Claude Code-compatible local subtask 工具；对 agent 暴露的
durable Agent Network 能力统一迁移到 `AgentSession`。

新增工具面用于跨 agent / 跨 session 通信：

- `Agent`: 父 session 内部兼容实现，快、私有、不可被平台绑定。
- `AgentSession`: Hub-visible / Access Router-visible session path，慢一点，但有
  `agentId`、session/run identity、状态和 bindings。

不要把两者合并。合并会让工具权限、生命周期、持久化边界变混。

## 外部 Agent 接入计划

### Runtime Controller

新增：

```text
kernel/agent_hub/manager/runtime_backends/
  base.py
  mustang.py
  acp.py
```

`acp.py` 实现 `AgentRuntimeController`：

- `create`: 启动 ACP process，initialize，`session/new`
- `load/resume`: 根据 `AgentIdentity.acp_session_id` 恢复
- `prompt`: per-session actor queue 串行 `session/prompt`
- `send_message`: 映射为 `session/prompt`，但 payload 标记为 inter-agent message
- `cancel`: `session/cancel`
- `status`: process + session identity + active turn + queue depth
- `close`: `session/close` + process release
- `delete`: 只删除 DeepCLI identity/binding，不删除外部工具私有历史，除非 runtime 明确支持

现有 `kernel.agents.mustang.runtime.external_acp.ExternalAcpRuntimeAdapter` 可作为底层
stdio client 的种子，但它需要上移或被 Manager runtime backend 包装；不要让
Mustang Runtime 私有模块成为 Hub 外部 runtime 的长期依赖。

### ACP 权限请求

外部 ACP runtime 可能向 client 发起：

- `session/request_permission`
- `fs/*`
- `terminal/*`
- 其他 client authority request

第一版策略：

- `session/request_permission`：转成 DeepCLI permission tunnel，经 Hub -> Access -> 用户。
- `fs/*`：默认拒绝，除非该 external agent 被授予 workspace proxy capability。
- `terminal/*`：默认拒绝；外部 Codex/Claude Code 应自己管理 shell，DeepCLI 不代理 terminal。
- 未知 request：fail closed，并记录诊断事件。

这比现在 `external_acp.py` 的“全部 runtime-initiated request 拒绝”更可用，但仍安全。

### Codex / Claude Code Profile

配置示例：

```yaml
agents:
  list:
    - id: codex
      name: Codex
      workspace: ~/work/current
      agentDir: ~/.deepcli/agents/codex/agent
      runtime:
        kind: acp
        command: ["codex", "acp"]
        profile: codex
      policy:
        tool_policy_profile: external-coding

    - id: claude-code
      name: Claude Code
      workspace: ~/work/current
      agentDir: ~/.deepcli/agents/claude-code/agent
      runtime:
        kind: acp
        command: ["claude", "code", "--acp"]
        profile: claude-code
```

如果某个工具没有 native ACP server，可通过可选 compatibility backend 接入：

```text
AcpRuntimeController -> acpx CLI -> target harness
```

但这是外部 runtime compatibility，不是 DeepCLI 内部协议。

## 安全与权限

### Session-to-session Policy

新增配置：

```yaml
tools:
  agentToAgent:
    enabled: true
    allow:
      main: ["research", "coding", "codex"]
      research: ["main"]
      coding: ["main", "codex"]
```

默认：

- `main` 可以发送到显式允许的 agents。
- 非 `main` agent 默认不能跨 agent，除非 allowlist。
- ACP agent 默认不能创建新的 agents。

### Spawn Policy

`AgentSession` 需要独立 policy：

- `allow_runtimes`: `agent | acp | local`
- `allow_agents`: agent id allowlist
- `max_children_per_session`
- `max_spawn_depth`
- `max_concurrent`
- `workspace_access`: `inherit | configured_only | denied`

### Platform Binding Policy

平台绑定必须可审计：

- 谁创建
- 创建来源：config / slash command / Home Screen
- 绑定到哪个 agent/session
- 何时过期
- 是否允许当前 conversation 继续发消息给目标 agent

## Final Acceptance Scope

Implementation Driver 里的 slices 是推荐实施顺序；本节是最终完成态验收清单。
实现可以按 slice 分 PR 交付，但不能在最终声明完成时把下面任一项定义成
“以后再说”的产品缺口。

### Agent 与 Session Scope

- 支持多个 OpenClaw-style agents：每个 `agentId` 都有独立 `workspace`、
  `agentDir`、auth profiles、model config、skills、session store、transcripts。
- 默认 agent 对外是 `main`；DeepCLI 现有 `primary` 只作为兼容别名。
- session key 使用 OpenClaw 形态：`agent:<agentId>:<mainKey>`、
  `agent:<agentId>:subagent:<uuid>`、`agent:<agentId>:acp:<uuid>`。
- 启动期拒绝多个 agents 复用同一个 `agentDir` 或 session store。

### Gateway / Access / Bindings

- Gateway/Access 承载外部入口和 WS/RPC，拥有 session state projection。
- `bindings[]` 支持 OpenClaw 的 routing 语义：`channel`、`accountId`、`peer`、
  `parentPeer`、guild/team 等匹配，most-specific wins。
- 支持 config bindings、current-conversation bindings、thread bindings、
  persistent ACP bindings。
- Gateway adapter 不直接调用 `SessionManager`；所有 inbound 都经 Hub/Router。

### Agent Network Tools

- `AgentDirectory` 返回当前 session 可见、可发送、可 spawn 的 agents，并按
  policy 过滤 operator-only 字段。
- `AgentMessage` 支持 durable agent / spawned session 投递：timeout 0
  fire-and-forget、wait completion、timeout/error 返回、inter-session provenance、
  reply-back loop、announce step、max ping-pong turns。
- `AgentSession` 支持 `runtime: "agent" | "acp" | "local"`、`mode: "run" |
  "session"`、run registry、thread/current-conversation binding、cleanup、
  timeout、sandbox inheritance guard、attachments。
- Deprecated aliases (`agents_list`、`sessions_send`、`sessions_spawn`、
  `subagents`) 如保留，只能调用同一 Agent Network service 和同一 policy path；
  默认 prompt/tool snapshot 不推荐旧名。

### Runtime 与 Queue

- Mustang runtime controller 支持多个 agent 实例，每个 target session 有 FIFO。
- `AcpRuntimeController` 支持 ACP process lifecycle/cache、initialize/new/prompt、
  cancel/close/status、process crash recovery path。
- sub-agent completion、ACP completion、session-to-session reply 都通过统一 announce
  和 event 路径回到 requester。

### Orchestrator Flow / Planning

- 支持 OpenManus-style flow，但实现为某个 agent session 内的 durable plan，不是
  同进程 agent object 互调。
- plan step 可记录目标 `agentId`、目标 session、runtime、runId、状态、notes。
- 执行 plan step 必须走 `AgentMessage` 或 `AgentSession`，保留 queue、
  transcript provenance、policy 和 observability。
- plan 状态必须持久化，并能被 Gateway/Home Screen 查询。

### Prompt / Workspace Bootstrap

- 新 agent creation 复用 OpenClaw wizard concepts：`agentId`、`workspace`、
  `agentDir`、auth/model、channels、bindings。
- workspace bootstrap 写入/维护 `AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`USER.md`、
  `TOOLS.md`、`HEARTBEAT.md`、`BOOTSTRAP.md`、`MEMORY.md`。
- system prompt 复用 OpenClaw section 结构和 prompt modes：`full`、`minimal`、`none`。
- sub-agent 注入 `# Subagent Context`，遵守 push-based completion、不轮询、不伪装
  parent、不做长期状态维护。

### Policy / Observability / DoD

- `tools.agentToAgent`、`agentSession.allowAgents`、`acp.allowedAgents`、
  spawn depth/concurrency、sandbox inheritance guard 全部生效。
- status/queue/active turn/last error/agentDir/session key/binding source 可观测。
- legacy Mustang GatewayManager 不能成为新功能路径。
- 每条闭合缝必须有真实 probe：Access -> Hub -> Mustang Runtime、
  `AgentMessage`、`AgentSession`、Access -> Hub -> ACP process、
  Runtime -> Hub -> Access permission request。

## 测试矩阵

| 层 | 必测 |
|---|---|
| Router | target 解析、binding 优先级、route miss、snapshot revision |
| Access Platform | fake inbound、reply sink、permission round trip、binding persistence |
| Mustang Multi-agent | 独立 `agentDir`/session DB、`AgentMessage`、`AgentSession`、FIFO |
| ACP Runtime | fake ACP runtime、Codex/Claude Code smoke、cancel、process crash |
| Policy | `tools.agentToAgent` allow/deny、spawn deny、ACP runtime capability deny |
| E2E | CLI -> `main` -> spawned agent；platform fake -> bound ACP agent |

Definition of Done 必须包含真实闭合缝 probe：

- Access -> Hub -> Mustang Runtime
- Agent -> Access Router / Hub-owned route truth -> Agent
- Access -> Hub -> ACP process
- Runtime -> Hub -> Access permission request

只跑单元测试不能算完成。

## 关键取舍

### 为什么不让 Gateway 直接调用 SessionManager

因为当前架构的根问题是所有外部入口必须先进入 Access/Hub，才能保留统一认证、
路由、权限、状态和未来 Home Screen 视图。Gateway 直连 SessionManager 会让平台入口
变成第二套产品路径。

### 为什么不把外部 Codex/Claude Code 做成 AgentTool

AgentTool 是父 session 私有任务，适合短期分工；Codex/Claude Code 需要自己的持久
workspace、session identity、cancel/status、平台绑定和恢复能力。它们是 runtime
backend，不是一个普通 tool call。

### 为什么不依赖 acpx

ACPX 解决的是 ACP runtime 管理缺失的一部分问题。DeepCLI 已经有 Agent Hub 和
Supervisor，因此内部控制面应该由自己的 Manager/Router 承担。兼容 acpx 可以降低接入
某些 harness 的成本，但不能成为内部架构中心。

## 风险

- 外部 ACP runtimes 的权限语义不一致；需要 fail closed。
- Claude Code/Codex 的 ACP 启动命令可能随版本变化；profile 必须可配置。
- Platform conversation id 的规范化一旦错，会造成串线；binding store 必须可审计。
- 多 agent `agentDir` 如果允许复用，会造成 auth/session 污染；启动期必须拒绝。
- Session-to-session ping-pong 可能无限循环；需要 max turns / max depth。
