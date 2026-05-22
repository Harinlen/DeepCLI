# Agent Tool 与 Agent Network Tool 重设计计划

状态: active
日期: 2026-05-22

## 背景

旧计划把 DeepCLI 的多 Agent 工具命名为 `agents_list`、
`sessions_spawn`、`sessions_send`、`subagents`。这个命名直接继承
OpenClaw 词汇，但放进当前 Kernel 后会产生三层混淆：

- Claude Code-compatible `Agent` tool 已经存在，语义是当前 session 内的本地
  子任务。
- Kernel 现在已经有 durable Agent 管理命令：`/agents`、`/agent send`、
  `/gateways`，并通过 Mustang ACP extension 暴露。
- Access Router 已经把 runtime route、gateway channel binding、agent send
  hot path 落在 `AgentCommandService`、`GatewayCommandService` 和
  `AccessRouter.deliver_turn()` 上。

所以新的设计不再把 OpenClaw 原始 tool 名字作为最终命名。OpenClaw 只保留为
架构参考：session-per-agent、多入口路由、agent-to-agent policy、spawned run
registry。DeepCLI 对外使用自己的 Kernel 原生命名。

## 当前 Kernel 事实

已经实现的管理面：

- `/agents list/add/delete/set-identity/bindings/bind/unbind/start/stop/restart/health/grants/grant/revoke-grant`
- `/agent send <agent-id> <message>`
- `/gateways list/create/status/delete/enable/disable/reload/bindings/bind/unbind`
- ACP method namespace 位于 `kernel.core.protocol.acp.namespaces.MustangMethod`：
  - `_mustang.agent/agents/*`
  - `_mustang.agent/agent/send`
  - `_mustang.agent/gateways/*`

已经实现的 routing truth：

- `/agents bind` 和 `/gateways bind` 都写
  `access_channel_bindings`。
- `agent_bindings` 当前是 reserved/deferred，不启用、不双写。
- `/agent send` 通过 `AccessRouter.deliver_turn()` 进入目标 runtime。
- `AccessRouter.route_status()` 能区分 unavailable/stale/fresh。

当前未完成的普通 Agent 可调用工具面：

- `multi_agent.py` 里已有旧名工具壳，但没有进入 `BUILTIN_TOOLS`。
- `SessionsSpawnTool` 仍直接调用 `AgentTool()`，没有 durable session-agent
  runtime。
- 没有 spawned run registry。
- 没有 agent-visible directory policy。
- 没有 `tools.agentToAgent` gate。
- 没有真实 kernel probe 证明 agent-visible tool layer 闭合。

## 新命名规则

### 1. `Agent` 保留给 Claude Code 兼容层

`Agent` 是局部子任务工具，不是 durable Agent，不是 Agent Hub 管理入口。

职责：

- 在当前 session 内 spawn 临时子 Agent。
- 支持前台执行和后台 `TaskRegistry` task。
- 背景 task 只归当前 parent session 管理。

不做：

- 不创建 durable `AgentDefinition`。
- 不写 `access_channel_bindings`。
- 不调用 `/agents add/delete/bind` 管理面。
- 不作为 durable Agent Network spawn 的默认实现。

### 2. Agent Network 使用 DeepCLI 原生命名

废弃旧计划里的最终 tool 名：

- 不再把 `agents_list` 作为最终名称。
- 不再把 `sessions_spawn` 作为最终名称。
- 不再把 `sessions_send` 作为最终名称。
- 不再把 `subagents` 作为最终名称。

新增 DeepCLI-native agent-visible tools：

| Tool | 用途 | Kernel truth |
|---|---|---|
| `AgentDirectory` | 只读发现当前 caller 可见、可联系的 durable agents | `AgentManager` projection + policy filter |
| `AgentSession` | 创建、查看、停止 caller-owned spawned agent sessions | `SessionService` + spawned run registry |
| `AgentMessage` | 向已有 durable agent 或 spawned session 投递消息 | `AccessRouter.deliver_turn()` / SessionService delivery |

命名原则：

- `Agent*` 表示 DeepCLI Agent Network，不再使用 OpenClaw snake_case。
- `Agent` 单词裸名只属于 Claude Code-compatible local subagent tool。
- slash commands 仍是人类/operator 管理面，不直接变成普通 agent tool。
- tool 名不使用 `/agents`、`/agent send`、`/gateways`，避免模型把管理命令当成
  agent-visible capability。

### 3. 兼容别名只做迁移，不进提示

可以短期保留旧名 alias：

- `agents_list` -> `AgentDirectory`
- `sessions_spawn` -> `AgentSession(action="spawn")`
- `sessions_send` -> `AgentMessage`
- `subagents` -> `AgentSession(action="list"|"stop"|"steer")`

但 alias 必须满足：

- 默认不出现在 system prompt 的推荐路径里。
- tool snapshot 可以标记为 deprecated。
- alias 不能改变权限和 routing 语义。
- alias 的测试只保证兼容，不作为新功能验收主路径。

## CC Tool 兼容策略

核心规则：`Agent` 和 Agent Network 是两个不同层，不共享默认 execution path。

### `Agent`

- 保持现有 schema 和行为，作为 Claude Code compatibility surface。
- 背景 local subagent 继续使用 `TaskRegistry`。
- `Agent` 的 background task 可以被 `AgentSession` 以
  `runtime="local"` 的只读兼容视图展示，但不能伪装成 durable session-agent。

### `AgentSession`

输入建议：

```json
{
  "action": "spawn | list | status | stop | steer",
  "runtime": "agent | acp | local",
  "mode": "run | session",
  "targetAgentId": "researcher",
  "runId": "optional-existing-run",
  "sessionId": "optional-existing-session",
  "prompt": "work to do",
  "label": "optional-label"
}
```

语义：

- `runtime="agent"`：通过 Kernel Agent runtime / SessionService 创建独立
  session-agent run，登记 spawned run metadata。
- `runtime="acp"`：通过外部 ACP adapter 创建或恢复 external agent session；
  backend 不存在时返回明确 unsupported，不回退到 `Agent`。
- `runtime="local"`：显式兼容分支，才允许调用 `AgentTool`。
- `mode="run"`：一次性任务，完成后返回结果和 run metadata。
- `mode="session"`：持久会话，返回 `runId` 和 `sessionId`，后续由
  `AgentMessage` 继续投递。

### `AgentMessage`

输入建议：

```json
{
  "target": {
    "agentId": "durable-agent-id",
    "sessionId": "spawned-session-id",
    "runId": "spawned-run-id"
  },
  "message": "text",
  "wait": false,
  "timeoutMs": 0
}
```

语义：

- `agentId` 目标走 `AccessRouter.deliver_turn()`。
- `sessionId` / `runId` 目标走 SessionService / spawned run registry。
- 不创建新 session；找不到目标返回 typed unavailable。
- 跨 durable agent 必须经过 `tools.agentToAgent` policy gate。

### `AgentDirectory`

输入建议：

```json
{
  "includeHealth": true,
  "includeBindings": false,
  "capability": "message | spawn"
}
```

语义：

- 只返回 caller 可见且 policy 允许 target 的 durable agents。
- 不等价于 `/agents list`。
- 不暴露 operator-only 字段、management grants、secret-like config。

## 实现计划

### 1. 删除旧名作为主路径

- 保留或重写 `multi_agent.py`，但主实现类改为：
  - `AgentDirectoryTool`
  - `AgentSessionTool`
  - `AgentMessageTool`
- 旧 `AgentsListTool` / `SessionsSpawnTool` / `SessionsSendTool` /
  `SubagentsTool` 只允许作为 deprecated wrappers，或先不注册。
- `BUILTIN_TOOLS` 只注册新 DeepCLI-native names。

### 2. 新增 Agent Network service

新增服务，例如：

```text
src/kernel/kernel/agents/mustang/runtime/agent_network_service.py
```

职责：

- `list_visible_agents(caller, capability)`
- `spawn_agent_session(params, caller)`
- `send_message(params, caller)`
- `list_runs(caller)`
- `stop_run(run_id, caller)`
- `steer_run(run_id, message, caller)`

为什么放 service：

- Tool class 只负责 schema、参数校验、LLM/display result。
- 复用现有 Kernel 命令和 routing truth。
- slash command、gateway、Home Screen 后续不依赖 tool class。
- policy、session id 解析、run ownership 集中处理。

### 3. Spawned run registry

新增 caller-owned run metadata：

```text
AgentNetworkRun {
  run_id
  parent_session_id
  requester_agent_id
  target_agent_id?
  runtime        # agent | acp | local
  mode           # run | session
  session_id?
  label?
  status         # starting | running | completed | stopped | failed
  created_at
  updated_at
}
```

规则：

- `runtime="agent"` 和 `runtime="acp"` 必须写 registry。
- `runtime="local"` 可以投影 `TaskRegistry`，但必须标记为 compatibility。
- `stop/steer` 只能操作当前 caller 拥有的 run。
- registry 不创建/删除 durable `AgentDefinition`。

### 4. 接入现有 Kernel route truth

必须复用当前实现：

- durable agent 管理仍由 `AgentCommandService` 和 `AgentManager` 拥有。
- gateway binding 仍由 `GatewayCommandService` /
  `AccessRouterRepository.access_channel_bindings` 拥有。
- durable agent message hot path 仍由 `AccessRouter.deliver_turn()` 拥有。
- session runtime 仍由 `AgentSessionRuntimeService` / `SessionManager` 拥有。
- `agent_bindings` 不启用、不双写。

禁止：

- Agent Network tools 直接写 ResourceStore tables。
- Agent Network tools 直接调用 `/agents add/delete/bind` 来扩大权限。
- `runtime="agent"` 隐式回落到 `AgentTool`。

### 5. Policy gate

新增或接入 config section：

```yaml
tools:
  agentToAgent:
    enabled: false
    allow:
      - source: primary
        targets: ["researcher", "reviewer"]
```

规则：

- `AgentDirectory` 用 policy 过滤可见 agents。
- `AgentMessage(agentId=...)` 必须检查 `tools.agentToAgent`。
- `AgentSession(runtime="agent")` 必须检查 spawn policy。
- sandboxed/local child agent 默认不能扩大父 session 可见范围。
- primary/operator management ACP 仍保持 `_require_primary()`；agent-visible tools
  不绕过这个管理保护。

### 6. Prompt 和 description

更新 session guidance：

- `Agent`：用于当前会话内的 Claude Code-style local subtask。
- `AgentDirectory`：发现可联系的 durable agents。
- `AgentSession`：创建或控制独立 agent session run。
- `AgentMessage`：向已有 durable agent 或 spawned session 投递消息。
- 不要把 `/agents`、`/gateways` 管理命令当成普通 agent tool。
- 不要把 `Agent` 当成 durable agent 网络入口。

## 测试计划

### 单元测试

必须覆盖：

- `AgentTool` 仍注册为 `Agent`，schema 不被 Agent Network 改坏。
- `BUILTIN_TOOLS` 包含 `AgentDirectory`、`AgentSession`、`AgentMessage`。
- 默认 tool snapshot 不推荐旧 OpenClaw snake_case names。
- `AgentDirectory` 不返回 policy 不允许的 agent。
- `AgentSession(runtime="agent")` 不调用 `AgentTool`。
- `AgentSession(runtime="local")` 才允许调用 `AgentTool`。
- `AgentSession(runtime="acp")` 在 backend 缺失时返回 typed unsupported。
- `AgentMessage` 不创建 session，只投递已有 target。
- `AgentMessage(agentId=...)` 在 `tools.agentToAgent.enabled=false` 时拒绝。
- `AgentSession stop/steer` 只能操作 caller-owned run。
- `runtime="local"` background task 只作为 compatibility projection。

### 集成测试

必须覆盖：

- tool snapshot 同时看到 `Agent` 和新的 Agent Network tools，名称不冲突。
- 创建两个 durable agents 后，`AgentDirectory` 只显示 policy 允许目标。
- `AgentSession(runtime="agent", mode="session")` 返回 `runId/sessionId`。
- `AgentMessage(sessionId=...)` 能投递到该 spawned session。
- `AgentMessage(agentId=...)` 使用 `AccessRouter.deliver_turn()`，不走 Agent Hub
  旧 message hot path。
- `AgentSession(runtime="acp")` 无 backend 时不 fallback。
- deprecated alias 如果注册，必须指向同一 service 和同一 policy path。

### Probe

新增真实 closure probe，例如：

```text
tests/probe/probe_agent_network_tools.py
```

必须跑真实 Kernel seam：

1. 启动可用的 ToolManager / AgentManager / AccessRouter / SessionService。
2. 调 `_mustang.agent/session/tool_snapshot`，确认：
   - `Agent`
   - `AgentDirectory`
   - `AgentSession`
   - `AgentMessage`
3. 确认旧 snake_case 名字不是默认推荐主路径。
4. 通过真实 tool invocation 跑：
   - `AgentDirectory`
   - `AgentSession(runtime="agent", mode="session")`
   - `AgentMessage` 给 spawned session
   - `AgentSession(action="list")`
   - `AgentSession(action="stop")`
5. 验证：
   - `runtime="agent"` 没有调用 `AgentTool`
   - `runtime="local"` 才调用 `AgentTool`
   - `agent_bindings` 仍为 0
   - `access_channel_bindings` 仍是 gateway/channel routing truth
   - policy deny 会阻断 cross-agent message

## 用户验收方式

实现后，用户仍用管理命令准备 durable agents：

```text
/agents list
/agents add researcher --workspace ...
/agents start researcher
```

普通对话中，模型应该使用 Agent Network tools：

```text
用 AgentDirectory 看一下你能联系哪些 agent。
```

```text
用 AgentSession 开一个独立 researcher session，总结 docs/plans。
```

```text
用 AgentMessage 给刚才的 run 追加一句：只输出三条结论。
```

预期：

- 模型不会用 `Agent` 来冒充 durable agent session。
- durable routing 走 Access Router。
- spawned session 有 `runId/sessionId`。
- 管理命令和 agent-visible tools 权限分离。

## 不做什么

- 不删除 `AgentTool`。
- 不把 `/agents add/delete/bind` 暴露成普通 agent tool。
- 不启用或写入 `agent_bindings`。
- 不让 `runtime="agent"` 调用 `AgentTool`。
- 不让 external gateway 直接创建 Mustang session；外部入口仍走 Access Router /
  Hub-owned path。
