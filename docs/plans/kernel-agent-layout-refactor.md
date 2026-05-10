# Kernel Agent Layout Refactor Plan

## 原始问题

当前 `src/kernel/kernel/` 的问题不是"文件夹太多"，而是**运行时所有权
没有被目录结构表达出来**。很多目录被平铺在 Kernel 根下，读代码时无法
回答三个最基本的问题：

1. 这个模块为什么存在？
2. 它属于哪个运行时主体？
3. 谁被允许依赖它，为什么？

如果只把现有目录机械搬进 `supervisor/`、`agent_hub/`、`agent/`，问题不会
消失，只会换一个更整齐的外壳。重构目标必须是降低错误依赖的概率，而不是
制造一个看起来更漂亮的树。

## 第一性原则

DeepCLI 的 Kernel 不是一个单体服务，而是一个本地 Agent Control Plane。
目录结构应该从运行时主体出发，而不是从技术类别出发。

最小事实：

- Supervisor 只管理进程生命周期。
- Agent Hub 只管理 agent 注册、路由、状态和共享资源边界。
- Access Agent 是一个 agent，负责外部接入和认证，不是 Hub 的内部组件。
- 现在的 `primary` 不是一种 agent 类型，只是默认实例 ID。
- 真正执行 session、LLM、tool、memory、hook 的 agent 类型应称为
  Mustang Agent。
- `session` 是 Mustang Agent 持有的 durable state，不是 agent 类型。
- Mustang 从"整个 Kernel 的代号"收窄为"执行 agent loop 的 agent 代号"；
  Kernel 本身以后直接叫 Kernel。
- 跨 agent 的 Hub 控制协议属于 Agent Hub 对外发布的内部 API，不属于
  `core`，也不属于某个具体 agent。
- `core` 不是"共享垃圾桶"。能进 `core` 的东西必须离开任何具体运行时主体
  仍然成立。

## 目标结构

```text
src/kernel/kernel/
├── core/
├── supervisor/
├── agent_hub/
│   ├── contracts/
│   ├── manager/
│   ├── resource_revisions/
│   ├── router/
│   ├── hub.py
│   ├── server.py
│   └── __main__.py
└── agents/
    ├── access/
    └── mustang/
```

这不是按"代码功能"分类，而是按"谁拥有这段行为"分类。

## 目录责任地图

这一节回答"每个文件夹放什么，以及为什么这样分类"。判断标准只有一个：
这个目录是否能用一句 owner 语言说清楚。说不清楚，就说明它要么太宽，要么
只是历史耦合。

### `core/`

放什么：

- 用户侧 wire protocol 的 schema / codec / method namespace。
- 配置、flag、secret、路径解析等底层 primitive。
- 不表达具体运行时主体的基础生命周期和 signal 工具。

为什么这样分：

`core` 是 Kernel 的底座，不是共享代码仓库。它里面的代码离开
Supervisor、Agent Hub、Access Agent、Mustang Agent 仍然成立。比如
`core/protocol` 描述"客户端如何和 Kernel 说话"，不是"某个 agent 如何
执行任务"。

不放什么：

- 不放 `SessionManager`，因为 session 是 Mustang Agent 的内部状态。
- 不放 `ConnectionAuthenticator` 的完整 subsystem，除非它被拆成更小的
  credential primitive；当前认证动作发生在 Access Agent。
- 不放 `ModuleTable`，因为它当前注册的是 Mustang Agent runtime 内部服务。

### `supervisor/`

放什么：

- 子进程启动命令。
- restart budget / crash handling。
- runtime file 写入。
- control socket。

为什么这样分：

Supervisor 的本质是进程保姆。它只关心"哪些进程应该活着"，不关心这些进程
内部如何路由请求、如何调用 LLM、如何执行工具。

不放什么：

- 不放 Hub routing。
- 不放 Access routes。
- 不放 Mustang Agent subsystem。
- 不放任何 tool / memory / LLM 逻辑。

### `agent_hub/`

放什么：

- agent registry。
- runtime record。
- router。
- routing snapshot。
- resource revision boundary。
- Hub 内部 websocket server。
- `contracts/`：Hub 对 agent 发布的内部控制协议。

为什么这样分：

Agent Hub 的本质是控制面中枢。它回答"哪个 agent 存在、状态是什么、这个
请求应该去哪个 agent"。它不执行用户任务，也不处理外部用户连接的细节。

不放什么：

- 不放 Access Agent。Access Agent 是一个接入 agent，不是 Hub 的子对象。
- 不放 Mustang Agent 的 session / orchestrator / tools。
- 不放具体 agent 的 memory、skills、MCP 连接。

### `agent_hub/contracts/`

放什么：

- Hub frame envelope。
- agent registration payload。
- route request / route result。
- runtime status snapshot。
- readiness / health contract。

为什么这样分：

这些 contract 是 Agent Hub 发布给 agents 的 southbound API。它们离开 Hub
没有独立意义，所以不进 `core`；但 Access Agent 和 Mustang Agent 都需要
依赖它们，所以也不能藏在 Hub 的私有实现文件里。

不放什么：

- 不放 ACP user-facing schema；那属于 `core/protocol`。
- 不放 Mustang Agent 内部 command/session/tool schema。
- 不放 Hub manager/router 的实现细节。

### `agent_hub/manager/`

放什么：

- agent definition registry。
- runtime record CRUD。
- agent 状态快照。
- routing snapshot 的数据来源。

为什么这样分：

Manager 管"有哪些 agent 和它们当前状态"，不负责"如何选择路由"。这样
registry 和 route policy 可以独立演进。

不放什么：

- 不放 route algorithm。
- 不放 websocket transport。
- 不放具体 agent runtime 调用逻辑。

### `agent_hub/router/`

放什么：

- route message。
- target resolution。
- route miss / default route 策略。

为什么这样分：

Router 只回答"这条消息应该送到谁"。它消费 Manager 产生的 snapshot，但
不拥有 agent registry。

不放什么：

- 不放 agent 注册。
- 不放 session prompt execution。
- 不放 Access Agent websocket accept 逻辑。

### `agent_hub/resource_revisions/`

放什么：

- 跨 agent 的 resource revision boundary。
- resource key -> revision 的当前值。
- resource revision bump event。
- 后续基于 revision 的 snapshot invalidation。

为什么这样分：

它不保存资源本体，只保存 Hub 视角下的资源修订线。目录名必须直接说出这一点：
`resource_revisions/` 比 `global_resources/` 更准确，也比 `version/` 更少歧义。
`version` 容易和产品版本、SQLite schema version 混在一起；这里跟踪的是多个
resource key 的 monotonic revision。

这个目录应保持很小，只有真正跨 agent 的 resource revision 协调才能进入。
当前实现里的 `GlobalResourceMonitor` 也应随目录一起改名为
`ResourceRevisionTracker`，因为它跟踪的是 revision map，不是资源本体，也
不是通用 monitor。

不放什么：

- 不放 agent memory。
- 不放 tool registry。
- 不放 session-local resource。
- 不放资源内容本身。

### `agents/`

放什么：

- 具体 agent 类型的实现目录。
- 目前包括 `access/` 和 `session/`。

为什么这样分：

DeepCLI 的外层运行时不是一个单 agent 程序。Access Agent 和 Mustang Agent
都是 agent，但职责不同。把它们放在 `agents/` 下，是为了表达"这是 agent
类型集合"，而不是把所有东西塞进一个抽象 `agent/`。

不放什么：

- 不放 Hub contract；那属于 Hub 发布的 API。
- 不放 core primitive。
- 不放 Supervisor lifecycle。

### `agents/access/`

放什么：

- Access Agent 进程入口。
- FastAPI app / routes。
- `/session` websocket accept。
- `/access/*` readiness / metadata。
- connection authentication。
- 用户侧 ACP/JSON-RPC edge 到 Hub frame 的 adapter。

为什么这样分：

Access Agent 的本质是外部入口。它面对 CLI、Probe、未来 Home Screen 和
platform adapter。它不执行 agent loop，只把通过认证的请求转给 Hub。

不放什么：

- 不放 SessionManager。
- 不放 Orchestrator。
- 不放 ToolManager。
- 不放 LLMProviderManager。
- 不直接读写 session SQLite。

### `agents/access/security/`

放什么：

- loopback token / password validation。
- AuthContext 构造。
- access-facing credential checks。

为什么这样分：

连接认证发生在 Access Agent 边界。把它放在 Access 下，能防止认证 subsystem
被误认为是所有 agent 都能随便调用的 core service。

不放什么：

- 不放 tool authorization。tool authorization 属于 Mustang Agent 的 tool
  execution path。
- 不放 provider/MCP secret store primitive；credential storage 属于
  `core/secrets`。

### `agents/mustang/`

放什么：

- Mustang Agent 进程入口。
- runtime bootstrap。
- session lifecycle。
- orchestrator / query loop。
- LLM routing 和 provider lifecycle。
- tools / tool authorization。
- skills / MCP / hooks / memory / tasks。
- commands / schedule / git / prompts。

为什么这样分：

这是 DeepCLI 真正执行用户任务的 agent 类型。当前默认实例叫 `primary`，
但未来 `researcher`、`planner` 等长期 agent 都应该复用同一套
Mustang Agent 实现。目录名必须表达类型，而不是表达当前默认实例。

不放什么：

- 不放 Access routes。
- 不放 Hub routing。
- 不放 Supervisor process management。

### `agents/mustang/runtime/`

放什么：

- Mustang Agent runtime service。
- 与 Hub 注册的 runtime websocket。
- runtime client peer。
- Mustang Agent bootstrap 和 shutdown。

为什么这样分：

`runtime/` 是 Mustang Agent 作为一个可启动 agent 实例的外壳。它负责把
内部 subsystem graph 装起来，并向 Hub 注册自己。

不放什么：

- 不放具体 tool 实现。
- 不放 Hub router。
- 不放 Access HTTP app。

### `agents/mustang/sessions/`

放什么：

- SessionManager。
- SessionStore / SQLite persistence。
- prompt queue。
- replay / resume / load / close。
- permission futures。
- session event model。

为什么这样分：

Session 是一个 Mustang Agent 的 durable state，不是 Kernel 全局状态。
多 Mustang Agent 以后，每个 agent 都应该有自己的 session scope 和 state
root。

不放什么：

- 不放 Hub agent registry。
- 不放 Access websocket accept。
- 不放 generic protocol codec。

### `agents/mustang/orchestrator/`

放什么：

- LLM/tool loop。
- prompt assembly。
- compaction。
- tool execution wiring。
- permission callback bridge。
- history/runtime event handling。

为什么这样分：

Orchestrator 是 Mustang Agent 的思考和执行循环。它消费 tools、LLM、memory、
hooks，但不应该成为跨 agent 的共享控制面。

不放什么：

- 不放 Hub routing。
- 不放 Access auth。
- 不放 provider SDK 之外的进程生命周期。

### `agents/mustang/tools/`

放什么：

- Tool ABC。
- Tool registry。
- builtin tools。
- tool context。
- file state / REPL / web tool helpers。

为什么这样分：

工具是 Mustang Agent 的动作表面。不同 Mustang Agent 可以拥有不同 tool
policy 或 registry，所以 tools 不应位于 Kernel 根。

不放什么：

- 不放 Hub routing。
- 不放 user-facing ACP transport。
- 不放 tool permission policy 之外的 Access authentication。

### `agents/mustang/tool_authz/`

放什么：

- tool permission rules。
- session grant cache。
- bash classifier。
- runtime guard。

为什么这样分：

Tool authorization 发生在 tool execution path。它回答"这个 Mustang Agent
这次是否可以执行这个 tool call"，不是"这个客户端是否可以连接 Kernel"。

不放什么：

- 不放 connection authentication。
- 不放 Hub registration permission。

### `agents/mustang/llm/` 和 `agents/mustang/llm_provider/`

放什么：

- model config / alias / role routing。
- provider instance lifecycle。
- provider-specific request/stream adapters。

为什么这样分：

LLM 是 Mustang Agent 执行 loop 的依赖。Hub 和 Access 不应该知道 provider
细节；它们最多知道某个 agent 是否 ready。

不放什么：

- 不放 route-to-agent policy。
- 不放 Access connection auth。

### `agents/mustang/skills/`, `mcp/`, `hooks/`, `memory/`

放什么：

- Skill discovery / lazy loading。
- MCP server connection / resource / tool adapter。
- hook config / registry / fire-sites。
- long-term memory store / selector / memory tools。

为什么这样分：

这些是 Mustang Agent 的可扩展能力层。它们直接影响 agent loop 的上下文、
工具和行为，不属于 Hub，也不是 generic core。

不放什么：

- 不放 Hub runtime record。
- 不放 Access route。
- 不放 Supervisor control socket。

### `agents/mustang/tasks/`

放什么：

- background task registry。
- AgentTool task state。
- task output collection。
- monitor/task output/stop tool support。

为什么这样分：

Task 是 Mustang Agent 内部执行状态。它可能被 tool surface 暴露，但 owner
仍然是执行该任务的 Mustang Agent。

不放什么：

- 不放 Hub process supervision。
- 不放 Access connection state。

### `agents/mustang/commands/`

放什么：

- slash command catalog。
- command definitions。
- command registry。

为什么这样分：

当前 command 语义读取 Mustang Agent state，并最终通过 ACP 暴露给客户端。
命令目录属于 agent 能力，不属于 Access edge。

不放什么：

- 不放 CLI rendering。
- 不放 Hub routing。

### `agents/mustang/schedule/`

放什么：

- cron store。
- cron scheduler。
- cron executor。
- delivery router。

为什么这样分：

Schedule 当前最终投递到 Mustang Agent / gateway 的执行路径。它是 agent
能力的一部分。若未来出现全局调度器，再单独提炼，不提前放进 core。

不放什么：

- 不放 Supervisor restart logic。
- 不放 Hub routing snapshot。

### `agents/mustang/git/`

放什么：

- git context injection。
- worktree enter/exit store。
- worktree tools backing logic。

为什么这样分：

Git context 是 Mustang Agent 对当前 workspace 的认知和工具能力，不是 Hub
控制面。

不放什么：

- 不放 launcher install git logic。
- 不放 Hub registry。

### `agents/mustang/gateways/`

放什么：

- 当前 gateway adapter 和 GatewayManager。

为什么暂放这里：

当前实现依赖 SessionManager 和 CommandManager，所以 owner 更接近
Mustang Agent。若未来 gateway 变成真正 platform ingress，职责可能转向
`agents/access/`，到时再按依赖和数据流重新归类。

不放什么：

- 不放 Access Agent 的 WebSocket `/session`。
- 不放 Hub routing contract。

## 当前目录迁移表

| 当前路径 | 目标路径 | 理由 |
|---|---|---|
| `supervisor/` | `supervisor/` | 已经是独立进程生命周期 owner。 |
| `agent_hub/` | `agent_hub/` | 保留 Hub 核心，继续瘦身。 |
| `agent_hub/global_resources/` | `agent_hub/resource_revisions/` | 当前只跟踪 resource key 的 revision，不拥有资源本体；旧名太泛。 |
| `GlobalResourceMonitor` | `ResourceRevisionTracker` | 类名应表达 revision tracking，而不是泛化的 resource monitoring。 |
| `agents/` | `agent_hub/contracts/` | 当前是控制面 schema / frame / transport vocabulary，属于 Hub 发布的内部 API。 |
| `access_agent/` | `agents/access/` | Access 是 agent 类型，不是 Hub 子模块。 |
| `agent_runtime/` | `agents/mustang/runtime/` | 当前 primary runtime 是 Mustang Agent runtime 实现。 |
| `app.py` | 拆分到 `agents/access/app.py` 和 `agents/mustang/runtime/bootstrap.py` | 当前 FastAPI app 同时承担 Access edge 和 in-process session bootstrap，职责混合。 |
| `routes/` | `agents/access/routes/` | HTTP / WS edge 属于 Access Agent。 |
| `session/` | `agents/mustang/sessions/` | Durable session 是 Mustang Agent 内部能力。 |
| `orchestrator/` | `agents/mustang/orchestrator/` | Agent loop 属于 Mustang Agent。 |
| `llm/` | `agents/mustang/llm/` | Model routing 服务 agent loop。 |
| `llm_provider/` | `agents/mustang/llm_provider/` | Provider lifecycle 服务 agent loop。 |
| `tools/` | `agents/mustang/tools/` | Tool registry / builtin tools 属于 Mustang Agent execution surface。 |
| `tool_authz/` | `agents/mustang/tool_authz/` | Tool authorization 发生在 tool execution 前，属于 Mustang Agent loop。 |
| `skills/` | `agents/mustang/skills/` | Skill discovery / SkillTool 属于 Mustang Agent capability surface。 |
| `mcp/` | `agents/mustang/mcp/` | MCP tool/resource 接入服务 Mustang Agent。 |
| `hooks/` | `agents/mustang/hooks/` | Hook fire-sites 当前围绕 session / tool / orchestrator。 |
| `memory/` | `agents/mustang/memory/` | Memory injection 和 tools 服务 Mustang Agent。 |
| `tasks/` | `agents/mustang/tasks/` | AgentTool / background task state 属于 Mustang Agent。 |
| `commands/` | `agents/mustang/commands/` | Slash command catalog 当前依赖 session state。 |
| `schedule/` | `agents/mustang/schedule/` | Cron execution 当前投递到 session / gateway。 |
| `git/` | `agents/mustang/git/` | Git context / worktree tools 服务 agent loop。 |
| `gateways/` | `agents/mustang/gateways/`（暂定） | 当前依赖 SessionManager + CommandManager；若未来成为 platform ingress，可重新归 Access。 |
| `prompts/` | `agents/mustang/prompts/` | 当前 prompt text 主要服务 orchestrator / tools / memory。 |
| `plans.py` | `agents/mustang/plans.py` | Plan mode tool 的持久文件逻辑属于 Mustang Agent。 |
| `module_table.py` | `agents/mustang/module_table.py` | 当前 module table 是 Mustang Agent runtime 内部 registry。 |
| `config/` | `core/config/` | 配置 primitive。 |
| `flags/` | `core/flags/` | 启动期 flag primitive。 |
| `secrets/` | `core/secrets/` | Credential primitive。 |
| `protocol/` | `core/protocol/` | 用户侧 wire protocol。 |
| `paths.py` | `core/paths.py` | DeepCLI 路径 primitive。 |
| `subsystem.py` | `core/lifecycle.py`（暂定） | 生命周期 ABC；后续若只被 Mustang Agent 用，再下沉。 |
| `signal.py` | `core/signal.py` | 基础 Signal/Slot primitive。 |
| `uvicorn_runtime.py` | 待定：`agents/access/` 或 `core/` | 若只是 FastAPI/uvicorn helper，归 Access；若多个进程复用，再进 core。 |

## 依赖规则

允许：

```text
supervisor       -> core
agent_hub        -> core
agent_hub        -> agent_hub.contracts
agents.access    -> core
agents.access    -> agent_hub.contracts
agents.mustang   -> core
agents.mustang   -> agent_hub.contracts
```

禁止：

```text
supervisor     -> agents.mustang.*
supervisor     -> agents.access.*
agent_hub      -> agents.mustang.*
agent_hub      -> agents.access.*
agents.access  -> agents.mustang.*
core           -> supervisor.*
core           -> agent_hub.*
core           -> agents.*
```

需要特别审查：

```text
agents.mustang  -> agents.access.*
agents.access  -> agents.mustang.*
```

默认不允许。必须通过 `agent_hub/contracts` 或 `core/protocol` 交流。

## 迁移策略

### Phase 0 - 设计锁定

1. 确认本计划中的目录名和所有权。
2. 更新 `docs/kernel/architecture.md`，把 Runtime Topology 和 Process
   Responsibilities 改为新的 owner language。
3. 明确 `primary` 是默认 Mustang Agent 实例 ID，不再作为代码目录名。

本阶段只改文档，不搬代码。

### Phase 1 - 建立目标包和兼容导入层

1. 创建目标包骨架：
   - `kernel/core/`
   - `kernel/agents/access/`
   - `kernel/agents/mustang/`
   - `kernel/agent_hub/contracts/`
2. 先移动低风险 contract / schema：
   - `kernel/agents/*` -> `kernel/agent_hub/contracts/*`
3. 旧 import 路径保留 re-export，避免一次性修改全仓库。

完成标准：

- 旧路径和新路径都可 import。
- 单元测试无 import error。

### Phase 2 - 搬 Access Agent

1. `kernel/access_agent/*` -> `kernel/agents/access/*`
2. `kernel/routes/*` 逐步迁入 `kernel/agents/access/routes/*`
3. 拆分当前 `app.py` 中 Access edge 相关逻辑。
4. 评估 `connection_auth` 是否迁入 `agents/access/security/`。

完成标准：

- Supervisor 仍能启动 Access Agent。
- `/access/readiness` 和 `/session` 仍通过真实 Access Agent 可用。
- Hub 不 import Access Agent 内部模块。

### Phase 3 - 搬 Mustang Agent Runtime

1. `kernel/agent_runtime/*` -> `kernel/agents/mustang/runtime/*`
2. Mustang Agent bootstrap 从当前 `app.py` / `session_service.py` 中收敛到
   `agents/mustang/runtime/`。
3. `module_table.py` 移入 `agents/mustang/module_table.py`。

完成标准：

- `primary` 仍作为 Mustang Agent 实例启动。
- Agent Hub registration / prompt / session lifecycle E2E 通过。

### Phase 4 - 搬 Mustang Agent Subsystems

按依赖风险从低到高迁移：

1. `llm_provider/`, `llm/`
2. `tools/`, `tool_authz/`
3. `skills/`, `mcp/`, `hooks/`
4. `memory/`, `tasks/`, `commands/`, `schedule/`, `git/`, `gateways/`
5. `orchestrator/`, `session/`

`session/` 和 `orchestrator/` 最后搬，因为引用面最大，且 closure seam 风险最高。

完成标准：

- 每批迁移后旧路径 re-export 仍工作。
- 新代码不得新增旧路径 import。
- 与该批 subsystem 相关的 unit / E2E 通过。

### Phase 5 - 收紧 `core`

1. 移动 `config/`, `flags/`, `secrets/`, `protocol/`, `paths.py`,
   `signal.py`, `subsystem.py`。
2. 对每个进入 `core` 的模块写一句 ownership rationale。
3. 如果发现某个模块只是历史耦合导致跨区使用，不进 `core`，而是拆出更小
   contract 或下沉到 owner 目录。

完成标准：

- `core` 不 import `supervisor` / `agent_hub` / `agents`。
- `agent_hub` 和 `agents/*` 对 `core` 的依赖只使用底层 primitive。

### Phase 6 - 全 docs 术语修正

1. 更新当前有效文档中的 runtime 命名：
   - Kernel 本身直接叫 Kernel，不再把 Mustang 当作整个 Kernel 的代号。
   - Mustang 专指 `agents/mustang/` 里的 agent runtime / compatibility lineage。
   - `primary` 是默认 Mustang Agent 实例 ID，不是目录名或类型名。
   - 旧的 Primary Runtime / Session Agent 文字改为 Mustang Agent，除非上下文明确
     指 durable session state。
2. 必须优先修正：
   - `docs/README.md`
   - `docs/kernel/overview.md`
   - `docs/kernel/architecture.md`
   - `docs/kernel/interfaces/protocol.md`
   - `docs/reference/references.md`
   - `docs/reference/decisions.md`
   - 仍处于 active design status 的 `docs/kernel/subsystems/*`
   - `AGENTS.md` 是只读入口文件，不在本计划中直接修改；若入口文件需要
     同步，应通过它指向的 `docs/` 真相更新流程处理。
3. 历史归档文件不批量重写。`docs/kernel/history/` 和
   `docs/kernel/history/plans/` 中的旧术语只在会误导当前入口文档时加说明。
4. 为 docs 增加一个术语审查清单，至少 grep：

   ```bash
   rg "Mustang|Primary Runtime|Session Agent|primary agent|kernel codename" docs INIT.md
   ```

完成标准：

- 当前入口文档不再说 Mustang 是整个 Kernel codename。
- 当前架构文档使用 `Access Agent` / `Agent Hub` / `Mustang Agent`。
- `primary` 只作为默认 Mustang Agent 实例 ID 出现。
- 历史文档若保留旧术语，必须能从当前 docs 入口看出它是历史语境。

### Phase 7 - 删除兼容层

1. 全仓库 import 改为新路径。
2. 删除旧路径 re-export。
3. 增加 import-boundary 测试，防止依赖回流。

完成标准：

- `rg "from kernel\\.(session|orchestrator|tools|access_agent|agent_runtime|agents)" src tests`
  无旧路径使用。
- import-boundary 测试覆盖禁止方向。

## 验证要求

这是结构性重构，不应改变用户可见行为。验证重点是 import、启动路径、真实
进程边界和 Hub routing。

每个实现阶段至少需要：

```bash
uv run pytest tests/kernel -q
uv run pytest tests/e2e -q -m e2e
git diff --check
```

涉及 Supervisor / Access / Hub / Mustang Agent 进程边界的阶段，还必须跑真实
Supervisor E2E，证明：

1. Supervisor 启动 Hub / Access / primary Mustang Agent。
2. Access readiness 可用。
3. primary Mustang Agent 注册到 Hub。
4. CLI / Probe 通过 Access -> Hub -> Mustang Agent 发送请求成功。

涉及 closure seam 的阶段必须按
[`definition-of-done.md`](../workflow/definition-of-done.md) 做真实 subsystem
probe，不能用 mock 代替。

## 非目标

- 不改变 ACP user-facing wire protocol。
- 不改变 session SQLite schema。
- 不改变 tool 行为。
- 不改变当前只有默认 `primary` 实例的产品事实。
- 不在本计划里设计多 agent 协作语义；这里只为未来多个 Mustang Agent 实例
  清理运行时边界。

## 当前结论

最终命名：

- `agents/access/`：Access Agent，外部入口 agent。
- `agents/mustang/`：Mustang Agent，真正执行 agent loop 的长期 agent 类型。
- `primary`：默认 Mustang Agent 实例 ID。
- `agent_hub/contracts/`：Agent Hub 发布给 agents 的内部控制协议。
- `core/`：严格受限的底层 primitive，不接收业务耦合。

这套结构的判断标准不是"看起来层次清楚"，而是未来新增第二个、第三个
Mustang Agent 时，不需要复制或穿透 Access / Hub / Mustang 的私有实现。
