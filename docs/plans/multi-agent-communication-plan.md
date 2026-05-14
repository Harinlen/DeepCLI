# 多 Agent 通信与外部 Agent 接入计划

状态: ready-for-implementation
日期: 2026-05-14

参考:

- OpenClaw: `docs/concepts/multi-agent.md`
- OpenClaw: `docs/tools/subagents.md`
- OpenClaw: `docs/tools/acp-agents.md`
- OpenClaw: `src/commands/agents.commands.*.ts`
- OpenClaw: `src/agents/system-prompt.ts`
- OpenClaw: `src/agents/subagent-announce.ts`
- OpenClaw: `docs/reference/templates/`
- OpenManus: `app/flow/planning.py`, `app/tool/planning.py`
- DeepCLI: [`../kernel/architecture.md`](../kernel/architecture.md)
- DeepCLI: [`../kernel/subsystems/session.md`](../kernel/subsystems/session.md)

## 结论

DeepCLI 要交付 OpenClaw-style 的多 Agent 能力：

```text
Access/Gateway -> Agent Hub Router -> agent session
agent session  -> sessions_send/spawn -> Agent Hub Router -> agent session
agent session  -> sessions_spawn(runtime="acp") -> Codex / Claude Code / other ACP runtime
```

关键点：

- OpenClaw 的 Agent 间通信不是靠 Gateway 当总线，也不是靠 ACPX 互调。
- Gateway/Access 只负责外部入口、bindings 和 reply sink。
- Agent 间通信靠 `sessions_send` / `sessions_spawn`，统一经过 Agent Hub。
- ACPX 只作为外部 ACP harness 的兼容参考，不做 DeepCLI 内部总线。

## 1. 准备交付什么

### 1.1 OpenClaw-style Agent 模型

交付多个可长期存在的 `agentId`：

- 默认 agent 叫 `main`；现有 `primary` 只作为兼容别名。
- 每个 `agentId` 都有独立 `workspace`、`agentDir`、session store、transcripts、
  prompt/profile/model/tool/memory scope。
- session key 使用 OpenClaw 形态：
  - `agent:<agentId>:main`
  - `agent:<agentId>:subagent:<uuid>`
  - `agent:<agentId>:acp:<uuid>`

### 1.2 Agent Directory 与管理面

交付 agent 的增删改查能力：

- create: 创建 `AgentDefinition`、workspace、`agentDir`、sessions、prompt bootstrap；
  支持非交互 `--workspace`、`--model`、`--agent-dir`、`--bind`。
- read/list: 查询当前可见 agents、identity、workspace、`agentDir`、model、
  bindings、routes、providers、runtime status、queue status。
- update: 修改 identity、workspace、runtime、model/profile、policy、bindings、
  per-agent tool/sandbox/subagent overrides。
- delete: 禁止删除 `main`；停止 runtime；删除 config/bindings/allowlist；把
  workspace、`agentDir`、sessions 移到 trash。

Agent 自己默认只能通过 `agents_list` 查询 policy-filtered Agent Directory。
创建、修改、删除 agent 属于管理能力，需要用户授权或 operator scope。

OpenClaw 管理面还包含这些必须对齐的细节：

- 默认 agent 规则：`agents.list[].default` 可标记默认；多个默认时第一个生效；
  未显式设置时第一个 agent 是默认；DeepCLI 对外默认名仍是 `main`。
- `agents list --bindings --json` 不只是列 id，还显示 `IDENTITY.md` / config identity、
  route summaries、provider/channel status 摘要，并提示 live health 走 channels probe。
- `agents bind/unbind/bindings` 是独立管理面；`unbind --all` 可移除某 agent 的全部
  route bindings。
- binding 写入要检测冲突：同一 match 不能被两个 agents 同时 claim；同一 agent 的
  channel-only binding 可升级成 account-scoped binding。
- delete 必须 prune `bindings[]` 和 `tools.agentToAgent.allow`，并报告 removed counts。
- `agents set-identity` 可从 workspace `IDENTITY.md` 读取，也可显式写 name/emoji/theme/avatar；
  未给 `--agent` 时可通过 workspace 反查唯一 agent。
- per-agent config 不止 runtime/model：还包括 thinking/reasoning/fast defaults、stream
  params、skills、memorySearch、heartbeat、groupChat mention patterns、sandbox、
  tools profile/allow/deny/elevated、`subagents.allowAgents`。

### 1.2.1 DeepCLI Agent 管理命令

对用户暴露两层入口：CLI slash command 给人用，operator JSON-RPC 给 Home Screen /
automation 用。两者必须调用同一组 management service，不能各自改 config。

CLI slash commands：

```text
/agents list [--bindings] [--json]
/agents add <name> --workspace <dir> [--model <id>] [--agent-dir <dir>] [--bind <channel[:accountId]>]
/agents set-identity <agentId> [--from-identity] [--identity-file <path>] [--name <name>] [--emoji <emoji>] [--theme <text>] [--avatar <path-or-url>]
/agents bindings [--agent <agentId>] [--json]
/agents bind <agentId> --bind <channel[:accountId]> [...]
/agents unbind <agentId> (--bind <channel[:accountId]> [...] | --all)
/agents delete <agentId> [--force] [--json]
```

Channel/platform commands（本计划新增；当前 DeepCLI 没有等价命令）：

```text
/channels list [--no-usage] [--json]
/channels status [--probe] [--timeout <ms>] [--json]
/channels capabilities [--channel <name>] [--account <accountId>] [--target <dest>] [--json]
/channels resolve <entries...> --channel <name> [--account <accountId>] [--kind auto|user|group|channel] [--json]
/channels logs [--channel <name>|all] [--lines <n>] [--json]
/channels add --channel <name> [--account <accountId>] [--name <display>] [channel-specific credentials/options]
/channels remove --channel <name> [--account <accountId>] [--delete]
/channels login [--channel <name>] [--account <accountId>] [--verbose]
/channels logout [--channel <name>] [--account <accountId>]
/channels setup [<channel>] [--account <accountId>]  # interactive alias for /channels add
```

当前实现只有 legacy `gateways:` config、`GatewayManager` 生命周期加载和
`POST /gateways/{adapter_id}/webhook` webhook 入口；它们不是 OpenClaw 的
`channels add/status/onboarding` 管理面。`/channels *` 必须作为 operator-facing
命令新增，并接到同一组 management service。命名以 OpenClaw 为准：主写入口叫
`channels add/remove/login/logout`，`setup` 只是交互式 alias，内部语义仍是
`channels add` + `setupChannels()`。

DeepCLI 对齐 OpenClaw 的 channel 命令职责：

- `list`: 从 Channel Registry + Auth/Profile store 读取配置账号，输出 configured /
  enabled / linked / token source / usage 摘要；JSON 输出给 Home Screen 复用。
- `status`: 优先经 Gateway RPC `channels.status` 取 live snapshot；Gateway 不可达时
  fallback 到 config-only status。`--probe` 允许做凭证/连接探测，但必须有超时。
- `capabilities`: 展示 channel/account 的 intents、scopes、可用动作、风险开关，用于
  配置前确认权限面。
- `resolve`: 调 channel plugin resolver，把人类输入的 user/group/channel 名称解析为
  route binding 可使用的稳定 id。
- `logs`: 读取 Gateway/channel 日志，按 channel 和行数过滤，debug 外部通信闭合缝。
- `add`: 非交互模式写 `channels.<channel>` 或 `channels.<channel>.accounts.<accountId>`；
  交互模式运行 `setupChannels()`，按 plugin 的 setup adapter 收集 token、webhook、
  auth dir、allowlist 等字段，写入 SecretManager / ConfigManager，并运行 post-write hook。
- `remove`: 默认 disable channel account，`--delete` 才删除 config；同时触发 channel
  lifecycle hook。
- `login/logout`: 面向 WhatsApp/Matrix/Slack 等需要运行态登录的 channel，走 Gateway RPC
  或 plugin auth adapter，不直接让普通 agent 处理密钥。
- `setup`: 只作为用户友好的交互式 alias，避免计划里出现一套和 OpenClaw 不同的主术语。

Operator JSON-RPC methods：

```text
_mustang.agent/agents/list
_mustang.agent/agents/create
_mustang.agent/agents/update
_mustang.agent/agents/delete
_mustang.agent/agents/set_identity
_mustang.agent/agents/list_bindings
_mustang.agent/agents/bind
_mustang.agent/agents/unbind
_mustang.agent/channels/list
_mustang.agent/channels/status
_mustang.agent/channels/capabilities
_mustang.agent/channels/resolve
_mustang.agent/channels/logs
_mustang.agent/channels/add
_mustang.agent/channels/remove
_mustang.agent/channels/login
_mustang.agent/channels/logout
```

`_mustang.agent/agents/update` 是 Home Screen / automation 的 PATCH 型接口，
不对应 CLI slash command；人的 CLI 入口拆成 `set-identity`、`bind/unbind` 等
可解释的具体动作。

Agent-visible tools：

- `agents_list`：普通 agent 可用，只返回 policy-filtered Agent Directory。
- 管理写操作不是普通 tool。agent 若要创建/修改/delete/bind/channel add，必须发起
  management request，经 operator permission / Home Screen 确认后由 management
  service 执行。

### 1.3 Agent 间通信工具

交付 OpenClaw 命名的工具面：

- `agents_list`: 返回当前 session 允许看到、发送、spawn 的 agents。
- `sessions_send`: 向已有 session 投递消息，不创建新 session。
- `sessions_spawn`: 创建 `sub-agent session` 或 `ACP session`。
- `subagents`: list/info/log/kill/send/steer 当前 session 创建的 sub-agent runs。

这些工具取代产品语义上的 `AgentTool`。`AgentTool` 可以保留为内部兼容层，
但不作为多 Agent 产品入口。

### 1.4 Gateway/Access 外部通信

交付外部平台消息进入多 Agent 的路径：

- Access/Gateway 规范化外部消息为 `InboundEnvelope`。
- `bindings[]` 按 `channel`、`accountId`、`peer`、`parentPeer`、guild/team/thread
  匹配到 `agentId` 或 session。
- 出站回复从 runtime 走 Hub -> Access -> reply sink。
- 平台 adapter 不直接调用 Mustang `SessionManager`。

外部通信配置分两层交付：

- `channels.*` / `platforms.*`：平台账号、token/webhook、DM/group policy、
  allowlist、streaming、media、proxy 等 channel runtime 配置。
- `bindings[]`：把已配置 channel/account/peer/guild/team/thread route 到某个
  `agentId` 或 persistent ACP session。
- `bindings[]` 支持 `type: "route"` 和 `type: "acp"`；`type` 缺省等同 route。
- 某些 channel 还支持 topic/thread 级 `agentId` override；它要被归一化进同一套
  Hub routing snapshot，不能成为 adapter 私有捷径。
- 外部 conversation 默认是持久 session：Access/Hub 必须从
  `channel + accountId + peer/guild/team/thread + agentId` 生成稳定 session key；
  已存在则恢复并追加 transcript，只有没有历史 session 时才创建。除非显式配置
  `oneshot` / 临时模式，否则 Discord/Telegram/Slack 等入口不能每条消息新建对话。

这两层都由 operator-facing 管理面完成：CLI、Home Screen 或 operator JSON-RPC。
普通 agent 默认不能直接写外部通信配置；它最多可以提出配置建议，或在获得
operator scope / 用户确认后调用受控 management method。

### 1.5 外部 Agent 接入

交付 ACP runtime backend，让 Codex、Claude Code、Gemini CLI 等外部 harness
成为可路由的 `ACP agent / ACP session`：

```yaml
agents:
  list:
    - id: codex
      runtime:
        kind: acp
        command: ["codex", "acp"]
    - id: claude-code
      runtime:
        kind: acp
        command: ["claude", "code", "--acp"]
```

它们要有 DeepCLI 统一的 identity、session key、status、cancel、close、queue、
bindings 和 permission tunnel。

### 1.6 Prompt 与 workspace bootstrap

交付 OpenClaw 风格 agent 创建体验和启动文件：

- 创建字段复用 OpenClaw：`agentId`、`workspace`、`agentDir`、auth/model、channels、
  bindings。
- bootstrap 文件：`AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`USER.md`、`TOOLS.md`、
  `HEARTBEAT.md`、`BOOTSTRAP.md`、`MEMORY.md`。
- system prompt 复用 OpenClaw section 思路：tooling、safety、workspace、
  runtime、current date/time、reply tags、heartbeats、reasoning。
- sub-agent 注入 `# Subagent Context`：只做当前任务，completion push 回 parent，
  不轮询、不伪装 parent、不做长期状态维护。

## 2. 具体怎么交付，以及为什么

### 2.1 复用 Mustang，而不是重写 Agent

做法：

- 把现有 Mustang runtime 改成可多实例化。
- Agent Hub Manager 根据 `AgentDefinition` 启动多个 runtime instance。
- 每个 runtime instance 绑定一个 `agentId` 和独立 `AgentResources`。
- `SessionManager`、orchestrator、tool registry、tool authz、LLM、MCP、skills、
  hooks、memory、tasks 都继续复用，只是依赖从当前 agent scope 读取。

为什么：

- 这和 OpenClaw 的 `agentId + workspace + agentDir + sessions` 模型一致。
- 不复制 Mustang 代码，避免两套 agent loop。
- 启动期可拒绝多个 agents 复用同一个 `agentDir` 或 session store，避免 auth/session
  串线。

### 2.2 Agent Hub 做 control-plane bus

做法：

- Hub Router 负责 target 解析、policy、correlation、status projection。
- 目标 runtime 的 per-session queue 负责真正执行 turn。
- Hub 不执行 LLM turn，也不持久化对话队列。

为什么：

- Agent 间通信需要统一路由、权限、状态和可观测性。
- Session FIFO 必须留在各 runtime/session 内，否则会绕开现有 SessionManager 边界。

### 2.3 `sessions_send` 不创建 session

做法：

- `sessions_send` 只发送到已有 `sessionKey` / `sessionId`。
- 支持 `wait`、`timeout`、accepted、reply-back loop、announce step。
- transcript 写入 `inter_session` provenance。

为什么：

- 这是 OpenClaw 的 session-to-session messaging。
- 它解决“Agent 怎么知道跟谁通信”：先由用户显式指定、已有 binding、plan step
  metadata 或 `agents_list` 选择目标；不靠广播猜测。

### 2.4 `sessions_spawn` 创建 sub-agent 或 ACP session

做法：

- `runtime="subagent"` 创建 `agent:<agentId>:subagent:<uuid>`。
- `runtime="acp"` 创建 `agent:<agentId>:acp:<uuid>`，由 `AcpRuntimeController` 启动
  Codex/Claude Code 等进程。
- 支持 `mode="run" | "session"`、thread/current-conversation binding、cleanup、
  timeout、sandbox inheritance guard、attachments。

为什么：

- OpenClaw 区分长期 agent session、一次性 sub-agent run、外部 ACP session。
- Codex/Claude Code 需要 status/cancel/close/binding，不应被塞进私有 `AgentTool`。

### 2.5 Gateway/Access 只做外部入口

做法：

- 新平台入口放在 Access Agent。
- adapter 只做 webhook/authn/event normalize/reply sink。
- binding resolution 产生 target hint，最终 route 仍交给 Hub Router。
- channel/account 凭证与平台策略由 Access/Platform management 写入 Config/Secrets；
  route ownership 由 Agent management 写入 `bindings[]`。

为什么：

- Gateway 如果直接调用 `SessionManager`，会形成第二套路由和权限路径。
- Home Screen、CLI、平台消息都必须看到同一套 session state projection。

OpenClaw 对照：

- `openclaw agents add` wizard 会问 `Configure chat channels now?`，调用
  `setupChannels()` 写 `channels.*`。
- 同一个 wizard 随后问 `Route selected channels to this agent now? (bindings)`，
  调 `buildChannelBindings()` / `applyAgentBindings()` 写顶层 `bindings[]`。
- 非交互模式可用 `openclaw agents add --bind <channel[:accountId]>` 或
  `openclaw agents bind/unbind/bindings` 单独管理 routing bindings。
- `bindings[]` 只声明 route/acp binding；平台凭证、allowlist、webhook 等仍在
  channel config 里。
- route binding 的确定性匹配顺序是：`peer`、`parentPeer`、`guild+roles`、`guild`、
  `team`、exact account、channel-wide、default agent；同一层第一个匹配 wins。
- `type: "acp"` binding 用于 persistent ACP conversation，按精确 conversation
  identity 解析，不走普通 route binding tier order。

DeepCLI 当前差距：

- 已有 `AgentDefinition.bindings`、`PlatformBindingSpec`、`AgentHubManager.binding_plan()`
  这类声明式骨架。
- 但 Access 进程当前仍加载 legacy `kernel.agents.mustang.gateways.GatewayManager`。
  旧 `GatewayAdapter` 会在 `_handle()` 里直接创建/运行 Mustang session。
- 还没有 operator-facing 的 `/channels add/status/login/logout/remove`、`/agents bind`、bindings conflict
  检查、SecretManager 写入和 Access binding plan hot reload。
- 所以现在的 agent 还做不到自己完成 Gateway 对外通信配置；正确实现也不应该让
  普通 agent 直接做。应交付受控管理面，再让 agent 通过授权路径请求管理动作。

### 2.6 Agent CRUD 对齐 OpenClaw

做法：

- CLI/Home Screen/JSON-RPC 都调用同一组 management method。
- `agents add/list/delete/bind/unbind/set-identity` 的语义对齐 OpenClaw。
- 写 config 前后都通过 Hub Manager reload/restart 对应 runtime。
- `agents add` 可以串起 `channels add` 交互式 onboarding，但 channel config 写入和 route binding 写入
  必须保持两层分离。
- Management methods 必须返回结构化结果，包含 added/updated/skipped/conflicts、
  removed bindings/allow entries、trash paths、identity source、route summaries。
- Slash command 只是 management method 的薄壳；Home Screen、CLI、automation
  看到的结果 schema 必须一致。
- ConfigManager 是管理面写入的权威 store；Agent Directory 是按 policy 过滤后的
  read model，不是让 agent 直接读全局 config。

为什么：

- Agent 是可管理资源，不只是 prompt 里的名字。
- CRUD 必须同时处理 config、workspace、`agentDir`、sessions、bindings、policy、
  runtime lifecycle。

### 2.7 借鉴 OpenManus 的 planning，但不照搬 Flow

做法：

- 支持 durable plan：每个 step 可记录 `targetAgentId`、session、runtime、runId、
  acceptance、status、notes。
- 执行 step 时仍调用 `sessions_send` 或 `sessions_spawn`。

为什么：

- OpenManus 的 plan/status 对跨 Agent 工作有用。
- 但 OpenManus 是同进程对象调用，不具备 OpenClaw 的 agent lifecycle、bindings、
  transcript provenance，所以只能借鉴 planning，不借鉴通信模型。

### 2.8 落地执行清单

直接按下面这些交付块实现；它们不是阶段划分，全部都是本计划完成条件的一部分。

1. Agent contracts / config schema
   - 收敛 `kernel.agent_hub.contracts.schema` 与 `kernel.agent_hub.contracts.schemas`
     的重复模型，保留一套 OpenClaw-style 命名：`AgentDefinition`、`runtime`、
     `workspace`、`agentDir/stateDir`、`bindings[]`、`identity`、`resources`。
   - 在 ConfigManager 下新增/接入 `agents.list[]` 和顶层 `bindings[]` schema；
     `main` 是对外默认 agent，`primary` 只做兼容 alias。
   - 明确 session key builder：`agent:<agentId>:main`、
     `agent:<agentId>:subagent:<uuid>`、`agent:<agentId>:acp:<uuid>`。

2. Agent management service
   - 在 `kernel.agent_hub.manager` 下实现单一 management service，负责
     `agents list/create/delete/set_identity/list_bindings/bind/unbind`。
   - 所有写操作都先更新 ConfigManager / SecretManager，再 reload Hub snapshot；
     slash command、Home Screen JSON-RPC、automation 只能调用这一个 service。
   - 实现 OpenClaw binding 语义：parse `channel[:accountId]`、冲突检测、
     同 agent account-scope upgrade、`unbind --all`、delete prune。

3. Channel management service
   - 新增 Channel Registry / ChannelAccount 抽象，现有 Discord gateway 和其他
     channel adapter 都按同一接口接入。
   - 实现 `channels list/status/capabilities/resolve/logs/add/remove/login/logout`；
     `setup` 只作为 `add` 的交互式 alias。
   - `channels add/remove/login/logout` 写 `channels.*` / secrets / lifecycle hook；
     不写 route ownership。route ownership 只由 `agents bind/unbind` 写 `bindings[]`。

4. Command / JSON-RPC surface
   - 扩展 `kernel.agents.mustang.commands.CommandManager` 的 catalog，让 `/agents *`
     和 `/channels *` 出现在可用命令里。
   - 在 ACP routing / session handler 增加 `_mustang.agent/agents/*` 与
     `_mustang.agent/channels/*` methods；这些 methods 只做鉴权、参数解析和
     service 调用，不直接改配置。

5. Hub Router / runtime delivery
   - 扩展 `AgentHubRouter`，从 `bindings[]` snapshot 按
     `peer -> parentPeer -> guild+roles -> guild -> team -> account -> channel -> default`
     解析 target。
   - 把 `sessions_send` / `sessions_spawn` 做成 model-visible tools，并接到 Hub；
     `sessions_send` 只投递已有 session，`sessions_spawn` 创建 subagent / ACP session。
   - 每次跨 agent 投递都写 correlation id、source/target session、provenance。

6. Access / Gateway rewrite seam
   - 保留现有 `GatewayManager` 作为 legacy adapter 兼容层，但禁止 adapter 直接创建
     Mustang session。
   - `routes/gateways.py` / platform webhook 只产出 `InboundEnvelope`，交给 Hub Router；
     reply 统一走 runtime -> Hub -> Access reply sink。
   - binding plan 变化后 Access 热更新，不需要重启进程才能切换 route ownership。

7. ACP external runtime
   - 实现 `AcpRuntimeController`，支持 initialize/new/prompt/cancel/close/status。
   - Codex、Claude Code 等外部进程作为 `runtime.kind=acp` agent 接入，拥有独立
     session key、status、permission tunnel、transcript provenance。

8. Prompt / bootstrap
   - `agents add` 创建 workspace bootstrap 文件：`AGENTS.md`、`SOUL.md`、
     `IDENTITY.md`、`USER.md`、`TOOLS.md`、`HEARTBEAT.md`、`BOOTSTRAP.md`、
     `MEMORY.md`。
   - system prompt composition 增加 agent identity、resource scope、tool policy、
     current date/time、reply tags、heartbeat、subagent context section。

## 3. 我们准备怎么测试

### 3.1 单元测试

- AgentDefinition validation：`agentId`、`agentDir`、session store 唯一性。
- Contract convergence：只允许一套 AgentDefinition / BindingPlan contract 被业务代码导入。
- Default agent resolution：`main` fallback、first default wins、no default uses first agent。
- Agent Directory policy filtering：只返回当前 session 允许看到的 agents。
- Agent summaries：identity source、providers、route summaries、bindings count。
- Management command parity：slash command 与 JSON-RPC method 走同一 service，
  返回同一 added/updated/skipped/conflicts/removed schema。
- Router resolution：显式 target、conversation binding、config binding、default route；
  peer/parentPeer/guild+roles/guild/team/account/channel/default 顺序。
- Binding priority：most-specific wins。
- Channel add / binding split：channel credentials 不进入 `bindings[]`；
  binding conflict 时不能覆盖其他 agent。
- Channel command parity：`channels list/status/capabilities/resolve/logs/add/remove/login/logout`
  的 slash command 与 JSON-RPC method 走同一 service；`setup` 只测为 `add` 的交互式 alias。
- Command catalog：`/agents *`、`/channels *` 都能从 `CommandManager` / ACP
  `commands/list` 看到。
- Binding management：add/update/skip/conflict、unbind exact、unbind all、account-scope upgrade。
- Delete pruning：`bindings[]`、`tools.agentToAgent.allow`、workspace/agentDir/sessions trash。
- Identity update：从 `IDENTITY.md` 读取、显式字段覆盖、workspace 反查 agent。
- `sessions_send` payload mapping、timeout、wait、provenance。
- `sessions_spawn` runtime selection、mode、cleanup、depth/concurrency guard。
- `tools.sessions.visibility` 与 `tools.agentToAgent` 共同限制跨 session / 跨 agent targeting。
- ACP permission request：已知 request tunnel，未知 request fail closed。

### 3.2 集成测试

- 启动 `main` + `research` 两个 Mustang runtime，确认各自 session DB/transcript 独立。
- `main` 调 `agents_list`，只能看到 policy 允许的 targets。
- `main` 调 `sessions_send` 到 `research`，`research` FIFO 执行，返回结果并写 provenance。
- `main` 调 `sessions_spawn(runtime="subagent")`，生成 sub-agent session，completion
  announce 回 parent。
- fake platform inbound 通过 Access -> Hub -> bound agent session，reply sink 收到回复。
- 同一 fake platform conversation 连续发两条消息，必须命中同一个 session key，
  transcript 连续追加；不同 thread/peer/account 才分配不同 session key。
- `/channels add` 写 channel config/secret；`/agents bind` 写 route binding；
  Access 收到新的 binding plan 后热更新。
- Legacy gateway adapter inbound 不能直接调用 `SessionManager`；必须走 Hub Router。
- fake ACP runtime 通过 `AcpRuntimeController` 完成 initialize/new/prompt/cancel/close。

### 3.3 E2E / 闭合缝 probe

Definition of Done 必须跑真实闭合缝：

- CLI -> Access -> Hub -> Mustang Runtime -> reply。
- Agent -> `sessions_send` -> Hub -> Agent -> reply。
- Agent -> `sessions_spawn(subagent)` -> Hub -> Mustang Runtime -> announce。
- Agent -> `sessions_spawn(acp)` -> Hub -> ACP process -> announce。
- Runtime permission request -> Hub -> Access -> user decision -> runtime。
- Platform fake inbound -> Access binding -> Hub -> runtime -> reply sink。

只跑 unit tests 不能算完成。

## 4. 你实现后我怎么测试

### 4.1 测 Agent CRUD

你可以运行或通过 Home Screen 做等价操作：

```text
/agents list
/agents add research --workspace /tmp/deepcli-research
/agents set-identity research --name Research --theme "Finds sources and summarizes"
/agents bind research --bind cli
/agents bindings --agent research --json
/agents unbind research --all
/agents list --bindings --json
/agents delete research
```

期望：

- `main` 永远存在且不能删除。
- `research` 有独立 `workspace`、`agentDir`、sessions。
- list 能看到 identity、bindings、routes、providers、runtime status、queue status。
- binding 冲突会被拒绝，不会静默抢走其他 agent 的 route。
- delete 后 runtime stopped，bindings 和 `tools.agentToAgent.allow` 被 prune，状态目录进入 trash。

### 4.2 测 Agent 怎么知道跟谁通信

在 `main` 对话里问：

```text
列出你现在可以通信的 agents，并说明什么时候该用它们。
```

期望：

- agent 调 `agents_list`。
- 返回的是 policy-filtered Agent Directory。
- 每个 target 有 `agentId`、description、capabilities、whenToUse、canSend、
  canSpawn、runtimeKind。
- 如果目标不明确，agent 会问你，不会广播给所有 agents。

### 4.3 测 `sessions_send`

对 `main` 说：

```text
把这个问题发给 research agent，让它找 3 个来源，然后把结果总结给我。
```

期望：

- `main` 使用 `sessions_send`，不是创建新 agent。
- `research` 的 session transcript 出现 `inter_session` provenance。
- `main` 收到结果后总结给你。
- UI/日志能看到 source session、target session、correlation id、timeout/wait 状态。

### 4.4 测 `sessions_spawn(subagent)`

对 `main` 说：

```text
开一个 sub-agent 检查当前 repo 的测试结构，完成后告诉我结论。
```

期望：

- 创建 `agent:main:subagent:<uuid>`。
- sub-agent prompt 包含 `# Subagent Context`。
- sub-agent 不假装自己是 parent，不主动和外部平台对话。
- completion 通过 announce 回到 `main`。

### 4.5 测 Codex / Claude Code ACP 接入

配置一个 external agent，例如：

```yaml
agents:
  list:
    - id: codex
      workspace: /path/to/repo
      runtime:
        kind: acp
        command: ["codex", "acp"]
```

然后对 `main` 说：

```text
让 codex agent 看一下这个 repo 的入口文件，并返回它认为的架构摘要。
```

期望：

- `main` 使用 `sessions_spawn(runtime="acp", agentId="codex")`。
- Hub 创建 `agent:codex:acp:<uuid>`。
- ACP process 有 status、cancel、close。
- 返回结果进入 transcript，且 provenance 标记为 ACP session completion。

### 4.6 测 Gateway/Access binding

先由 operator 配置 channel 和 binding：

```text
/channels add --channel telegram --account personal
/agents bind research --bind telegram:personal
/agents bindings --json
```

用 fake platform adapter 或测试 webhook 发送一条消息到绑定 conversation：

```text
channel=telegram accountId=personal peer=chat-123 message="hello"
```

期望：

- Access 只规范化 inbound，不直接调用 `SessionManager`。
- Hub Router 根据 binding 路由到目标 `agentId/session`。
- 回复从 runtime -> Hub -> Access -> reply sink。
- follow-up 继续进入同一 binding session。
- 普通 agent 如果尝试直接改 channel token/binding，会触发 management permission，
  未授权时拒绝。

## 验收标准

完成时必须同时满足：

- 多个 Mustang agents 可并存，资源和 sessions 不串线。
- `agents_list` / `sessions_send` / `sessions_spawn` / `subagents` 可用。
- Gateway/Access external inbound 统一经 Hub Router。
- Codex/Claude Code 等 ACP runtime 可作为 `ACP agent` 接入。
- Agent CRUD 能管理 config、workspace、`agentDir`、sessions、bindings、policy、
  runtime lifecycle。
- 所有跨 subsystem 闭合缝有真实 probe 记录。
