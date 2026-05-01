# Kernel — Architecture

Kernel 的整体结构 —— 有哪些子系统、它们如何接入外部流量、
如何按顺序启动和关闭。每个子系统的细节在
[`subsystems/`](subsystems/) 下独立文档里展开。

## 子系统清单

Kernel 由以下子系统构成。每个子系统是独立模块，有自己的
配置、初始化、持久化和清理逻辑。

| 子系统 | 职责 | 文档 |
|--------|------|------|
| **Flag** | 功能开关管理。Bootstrap 服务，最优先加载。每个子系统注册自己的 flag section（Pydantic schema），`kernel` section 由 FlagManager 自己内置，管理"哪些子系统启用"。 | [flags.md](subsystems/flags.md) |
| **Secrets** | Bootstrap 服务，第二个加载（在 Config 之前）。SQLite 凭证存储（`~/.mustang/secrets.db`，0600 权限），CRUD API + `${secret:name}` config 展开 + OAuth token 便捷方法。LLM 隔离：不暴露 tool，不进 prompt。`/auth` CLI 命令通过 ACP `secrets/auth` 路由。设计参考 [secrets.md](subsystems/secrets.md)。 | [secrets.md](subsystems/secrets.md) |
| **Config** | Bootstrap 服务，第三个加载。管理所有子系统的业务配置。分层加载：defaults → user → project → local → env → cli。通过 `secret_resolver` 在 bind 时展开 `${secret:name}` 引用。其他子系统通过 `bind_section` / `get_section` 取自己的段。 | [config.md](subsystems/config.md) |
| **ConnectionAuthenticator** | 连接接入认证（AuthN）。传输层在 accept 后、进入协议层前调用 `authenticate()` 验证**"这个 WS 连接是谁"**，产出只读的 `AuthContext`。Token（本机文件，本机客户端用）和 password（scrypt 哈希，CLI 管理，远程客户端经反代用）两种凭证。Kernel 只 bind loopback。scope 刻意窄：不管 provider API key、不管 MCP OAuth、不管工具授权——后者由 `ToolAuthorizer` 负责。 | [connection_authenticator.md](subsystems/connection_authenticator.md) |
| **ToolAuthorizer** | 工具调用授权（AuthZ）。orchestrator 层在每个 tool call 执行前调用 `authorize(tool, input, ctx)`，结合 4 层 Rule（user/project/local/flag）+ session 内 `allow_always` grant 缓存 + Bash 命令分类器（含真实 LLMJudge，走 `LLMManager.model_for("bash_judge")`），产出 `allow / deny / ask` 决策。`ask` 时通过 `on_permission` 回调抛给 Session 层走 `session/request_permission` 往返。**已实装**；设计见 [tool_authorizer.md](subsystems/tool_authorizer.md)。 | [tool_authorizer.md](subsystems/tool_authorizer.md) |
| **Provider** | LLM provider 管理（`LLMProviderManager`）。按凭证 `(type, api_key, base_url)` 去重缓存 Provider 实例（`AnthropicProvider` / `OpenAICompatibleProvider` / `BedrockProvider`），管理连接池生命周期。不做路由，不读 model 配置。 | [llm_provider.md](subsystems/llm_provider.md) |
| **LLM** | Model 管理（`LLMManager`）。读取用户定义的 model 配置（`llm:` config section），实现 `LLMProvider` Protocol。alias 解析 + `current_used` 角色表（`model_for(role)`）+ model 路由 → 调 LLMProviderManager 取 Provider 实例 → 委托 `stream()`。作为 `OrchestratorDeps.provider` 注入 Orchestrator。 | [llm.md](subsystems/llm.md) |
| **Tools** | 工具系统（`ToolManager`）。Tool ABC + ToolContext + FileStateCache + core/deferred 两层 registry（ToolSearchTool 解锁 deferred 层） + `snapshot_for_session` 与 ToolAuthorizer `filter_denied_tools` 协同；27 个内置工具 + 5 memory tools：Bash / PowerShell / REPL / FileRead（含图片+PDF） / FileEdit / FileWrite / Glob / Grep / Skill / Agent / SendMessage / TodoWrite / TaskOutput / TaskStop / Monitor / ToolSearch / EnterPlanMode / ExitPlanMode / AskUserQuestion / WebFetch / WebSearch / CronCreate / CronDelete / CronList / EnterWorktree / ExitWorktree / McpAuth + 5 memory tools。EnterWorktree / ExitWorktree 由 GitManager 动态注册（按 git 可用性）。McpAuth 由 MCPManager 按 OAuth 需求动态注册。**已实装**；设计见 [tools.md](subsystems/tools.md)。 | [tools.md](subsystems/tools.md) |
| **Skills** | 技能系统（`SkillManager`）。SKILL.md frontmatter（Claude Code + Hermes 全字段），multi-layer 递归发现（.mustang/ + .claude/ compat），三池 registry（static/conditional/dynamic），lazy body load，SkillTool（LLM 调用），PromptBuilder listing 注入，dynamic discovery + conditional activation，compaction preservation，bundled skills framework，snapshot cache。**已实装**；设计见 [skills.md](subsystems/skills.md)。 | [skills.md](subsystems/skills.md) |
| **Hooks** | 钩子系统（`HookManager`）。事件驱动，16 event 枚举（POST_SAMPLING + PRE_CRON_FIRE + POST_CRON_FIRE）、`HookEventCtx` 可变上下文、`HookBlock` 异常、user/project 发现、manifest 解析、`fire(ctx)` API、boundary safety。已接入 ToolExecutor（`pre/post_tool_use`、`post_tool_failure`）+ ToolAuthorizer（`permission_denied` / `permission_requested`）+ CronExecutor（`pre_cron_fire` / `post_cron_fire`）的 fire-sites；system-reminder 通过 `queue_reminders` / `drain_reminders` 闭包进入 `Session.pending_reminders` 并在下一轮 prompt 前 drain。**已实装**；设计见 [hooks.md](subsystems/hooks.md)。 | [hooks.md](subsystems/hooks.md) |
| **MCP** | MCP 服务管理（`MCPManager`）。4 种 transport（stdio / SSE / HTTP / WebSocket），连接生命周期管理，health monitor 后台检测，指数退避重连。向 ToolManager 暴露 `on_tools_changed` signal + live connections，由 ToolManager 通过 `MCPAdapter` 发现并注册 proxy tools。配置支持 ConfigManager 三层 + `.mcp.json` 兼容。**已实装**；设计见 [mcp.md](subsystems/mcp.md)。 | [mcp.md](subsystems/mcp.md) |
| **Memory** | 长期记忆管理（`MemoryManager`）。global + project 两个 scope，markdown + YAML frontmatter 存储。4 分类目录树（profile/semantic/episodic/procedural），BM25(jieba)+LLM scoring，hot/warm/cold 排名，5 memory tools（write/append/delete/list/search），后台 agent（三层提取+去重+矛盾检测），双通道注入（Channel A index + Channel C strategy）。**已实装**；设计见 [memory/design.md](subsystems/memory/design.md)。 | [memory/design.md](subsystems/memory/design.md) |
| **SessionManager** | 会话管理。SQLite 持久化（`~/.mustang/sessions/sessions.db`：`sessions` ORM 表 + `session_events` Core 表），通过 `PRAGMA user_version` 做启动期 auto-migration；每次写一次事务，WAL 并发。`SessionHandler` 实现 ACP 7 个方法。串行 + FIFO queue 处理 prompt turn，cancel 清空整个队列。多连接 broadcasting。每个 session 长期持有一个 Orchestrator 实例。长期保留无自动清理。token 统计按 turn 累积到 `TurnCompletedEvent.input_tokens` / `output_tokens`。 | [session.md](subsystems/session.md) |
| **Orchestrator** | 对话引擎核心（`StandardOrchestrator`）。每个 Session 一个实例。接收用户消息 → 调 LLM → 执行工具 → 喂回结果 → 循环直到 LLM 不再调工具。持有 conversation（消息历史）、prompt builder（system prompt 构建）、compactor（context 压缩）、plan mode 状态。`ToolExecutor` 走 7-step flow（validate → authorize → `pre_tool_use` → call → `post_tool_use` → emit），`post_tool_failure` 在 call raise 时替代 `post_tool_use`。纯粹的"消息进 → 事件出"异步生成器，不管 WebSocket、不管 auth、不管持久化。对外接口：`query(prompt, *, on_permission) -> AsyncGenerator[OrchestratorEvent, StopReason]` + `close()`。 | [orchestrator.md](subsystems/orchestrator.md) |
| **CommandManager** | 命令目录提供者（非执行者）。维护 `CommandDef` 注册表（名称、描述、用法、对应 ACP 方法），预置 9 个内置命令（`/help`、`/model`、`/plan`、`/compact`、`/session`、`/cost`、`/memory`、`/cron`、`/auth`）。WS 客户端通过 `commands/list` 拉取目录后自行解析并调用对应 ACP 原语；GatewayAdapter 走 `_execute_for_channel` 直调 SessionManager / LLMManager。没有 dispatch，没有执行逻辑。**已实装**；设计见 [commands.md](subsystems/commands.md)。 | [commands.md](subsystems/commands.md) |
| **GatewayManager** | 外部消息渠道管理。按配置实例化 `GatewayAdapter` 子类，管理各渠道的生命周期。每个 Adapter 持有自己的 peer→session 映射，负责完整的消息来回（接收 → normalize → orchestrator → 发回）。已落地 `DiscordAdapter`（Discord Gateway WS + REST，self-message filter、2000-char 分块、权限 round-trip over chat、per-session `asyncio.Lock`、`_get_or_load` 透明加载 evicted session）。与 WS `/session` 入口平行，均汇聚于 Session 层。**已实装**；设计见 [gateways.md](subsystems/gateways.md)。 | [gateways.md](subsystems/gateways.md) |
| **ScheduleManager** | 定时调度子系统（`kernel/schedule/`）。CronStore（SQLite `kernel.db`，durable/non-durable 双层持久化）、CronScheduler（event-driven asyncio 定时器，multi-instance claim via `running_by` + heartbeat，startup catch-up，max_age expiry）、CronExecutor（isolated session spawn，heartbeat loop，auto-approve permissions）、DeliveryRouter（session/acp/gateway 三种投递目标，transient retry + idempotency cache + silent pattern + failure alerts）。4 种 schedule 格式（cron/every/at/delay）、RepeatConfig（count/duration/until 三维限制）、5 级指数退避（OpenClaw 模式）。3 个 deferred 工具：CronCreateTool / CronDeleteTool / CronListTool。`/loop` bundled skill。HookEvent +2（PRE_CRON_FIRE / POST_CRON_FIRE → 16 total）。**已实装**（Phase 14）；设计见 [schedule.md](subsystems/schedule.md)。 | [schedule.md](subsystems/schedule.md) |
| **GitManager** | Git 操作子系统（`kernel/git/`）。startup 永不失败（`_available` 标志），git binary 解析（用户配置 > PATH > 不可用），ConfigManager signal 热重载 `git.binary`，动态工具注册（EnterWorktree / ExitWorktree 按 git 可用性注册/注销到 deferred 层）。Git Context Injection（5 路并行 git 查询，session 级缓存，CC 格式 gitStatus 注入 system prompt）。WorktreeStore（SQLite `kernel.db`，崩溃恢复 GC + session resume cwd 恢复）。context_modifier 管线（ToolExecutor 消费 → Orchestrator 回调更新 cwd）。ACP worktree startup mode（`_meta.worktree`）。**已实装**（Phase 15）；设计见 [git.md](subsystems/git.md)。 | [git.md](subsystems/git.md) |

## WebSocket 接入 (`/session`)

`/session` 是用户唯一的 IO 接入点。分三层，职责严格隔离：

```
传输层 (Transport)          ← FastAPI websocket method
  │  accept / recv / send / close
  │  accept 后立即调 ConnectionAuthenticator.authenticate()，失败则关闭连接
  │  不做消息解析，不做业务
  ↓
协议层 (Protocol)           ← ProtocolCodec / SessionDispatcher
  │  JSON-RPC 2.0 帧 ↔ Pydantic 对象（反序列化 / 序列化）
  │  initialize 握手 + capability 协商
  │  REQUEST_DISPATCH / NOTIFICATION_DISPATCH 方法路由
  │  错误到 JSON-RPC error 帧的映射
  │  不知道 session 业务，不知道 orchestrator
  ↓
  ── 层间 seam：Pydantic params in / Pydantic result out ──
  ↓
会话层 (Session Handler)    ← SessionManager 实现
     Session 生命周期（创建 / 恢复 / 绑定连接 / 解绑）
     Prompt turn 串行处理 + FIFO queue
     Cancel 任务跟踪（per-session in-flight task）
     多连接 broadcasting
     Permission 往返（Future 创建 / 等待 / resolve）
     清理（断开时 cancel task、移除连接、fire hooks）
     不知道 JSON-RPC 线格式，不知道 WebSocket
```

**传输层** 就是 `@router.websocket("/session")` 函数本身。
详细设计见 [transport.md](subsystems/transport.md)。高阶职责：

1. accept 连接
2. 调用 ConnectionAuthenticator 验证身份（在进入协议层之前）
3. 通过 `TransportFlags.stack` 查出当前 ProtocolStack
4. 驱动固定的 `recv → decode → dispatch → encode → send` 循环
5. 断开时关 socket（session layer 到位后会扩展清理逻辑）

协议层和会话层分别通过 `ProtocolCodec` / `SessionDispatcher`
两个 Protocol 接口接入 transport，绑成一个 `ProtocolStack` 由
`TransportFlags.stack` 选择。两套 stack 都已登记在
`kernel.routes.stack.create_stack`：

- `dummy` —— `DummyCodec` + `DummyDispatcher`（identity pass-through），
  裸 echo，仅用来验证 transport 循环本身；`TransportFlags.stack`
  默认值。
- `acp` —— 真正的 ACP codec + `SessionDispatcher`（见
  `kernel.protocol.build_protocol_stack`），已落地并用于生产路径。

两条 stack 走的是完全一样的 `recv → decode → dispatch → encode → send`
循环。

**协议层** 直接采用 **ACP (Agent Client Protocol)**，不重复
发明轮子。ACP 是标准化 IDE ↔ AI agent 通信的协议（类似 LSP
之于语言服务器）。

ACP 采用的部分：

| ACP 方法 / 通知 | 用途 |
|-----------------|------|
| `initialize` | 握手 + capability 协商 |
| `session/new` | 创建新 session |
| `session/load` | 恢复已有 session |
| `session/list` | 列出所有 session |
| `session/prompt` | 发送用户消息，开始一轮对话 |
| `session/cancel` | 取消进行中的操作 |
| `session/update` | streaming 通知（text chunk、tool call 进度、plan 更新） |
| `session/request_permission` | 权限请求往返（allow_once / allow_always / reject） |
| `session/set_mode` | 切换模式（plan mode 等） |
| `session/set_config_option` | 运行时配置变更（model 切换等） |
| Tool call 状态模型 | pending → in_progress → completed / failed |
| Content blocks | text / image / resource |
| JSON-RPC 2.0 | 消息格式（method + params + id / notification） |

ACP 不采用的部分（跟我们架构不符）：

- `fs/*`、`terminal/*` client 方法 — 我们的工具在 kernel
  侧执行，不需要 client 提供文件系统和终端
- stdio 传输 — ACP 默认用 stdio（编辑器 spawn 子进程），
  我们用 WebSocket 承载同样的 JSON-RPC 消息

ACP 需要扩展的部分（用 `_meta` 扩展字段）：

- Memory 系统消息（memory read / write / extract）
- Context compaction 通知
- Kernel 特有的 session 元数据（token 累计、context 使用率）

好处：以后 IDE 如果原生支持 ACP，可以直接接入 kernel，
不用写专门的扩展。Web 等前端也用同一套协议。

**会话层** 是业务核心。拿到类型化的 JSON-RPC 消息后做分发：
`session/prompt` → run_query、`session/request_permission` 回复
→ resolve Future、`session/cancel` → cancel task 等。协议层
持有 `ConnectionContext`（包含 AuthContext + 协议协商结果 +
当前绑定的 session_id），传给会话层作为每次请求的上下文。

## 生命周期

### Bootstrap 服务 vs Subsystem

**FlagManager 和 ConfigManager 是 bootstrap 服务**，不继承
`Subsystem`。它们的公共 API（`register` / `bind_section` / signal
通知）比统一的 startup/shutdown 契约丰富，而且所有别的子系统都
依赖它们已经启动。lifespan 直接构造并特判管理这两个服务，
**启动失败立即 abort kernel 进程**。它们以 `flags` / `config`
字段挂在 `KernelModuleTable` 上，但**不进入** table 内部的
subsystem dict —— bootstrap 和普通 subsystem 的差异在类型上直接
体现出来。

除 Flag / Config 外的所有子系统继承 `kernel.subsystem.Subsystem`
ABC，实现两个 async 钩子：

- `startup()` —— 获取资源、向 FlagManager/ConfigManager 注册自己的
  section，使子系统进入可服务状态
- `shutdown()` —— `startup` 的逆操作：释放资源、drain 后台任务、
  持久化状态。必须容忍 `startup` 失败后的部分状态

基类的 `__init__(module_table)` 把 `KernelModuleTable` 保存到
`self._module_table` —— 这是子系统访问 `FlagManager` / `ConfigManager`
和其他子系统的**唯一**通道，没有模块级单例、没有隐式全局状态，
想知道一个子系统依赖什么直接 grep `self._module_table` 就够了。
基类提供统一的 `load(name, module_table)` 类方法和 `unload()` 实例
方法，封装了错误处理策略（失败降级），lifespan 只需按顺序调用
这两个入口，不用重复 try/except 样板。子类不直接调用 `load` /
`unload`；如果子类要自定义 `__init__`，**必须**接受 `module_table`
并通过 `super().__init__` 转发。

### 启动顺序

子系统按四组顺序加载，在 FastAPI lifespan 中管理：

```
启动 (_lifespan enter)
  │
  ├─ 0. Flag          FlagManager —— 最早加载，决定哪些可选子系统启用
  │                   [bootstrap] 失败则 abort kernel
  │
  ├─ 1. Config        ConfigManager —— 所有其他子系统依赖它读配置
  │                   [bootstrap] 失败则 abort kernel
  │
  │  ── 核心子系统（always on，失败降级继续） ──
  ├─ 2. ConnAuthN     ConnectionAuthenticator —— WS accept 前置门
  ├─ 3. ToolAuthZ     ToolAuthorizer —— 工具调用授权，必须早于 Tools / Orchestrator
  ├─ 4. Provider      LLMProviderManager
  │
  │  ── 可选子系统（KernelFlags 控制，禁用时整个子系统直接跳过） ──
  ├─ 5. MCP           MCPManager (before Tools — ToolManager connects to its signal)
  ├─ 6. Tools         ToolRegistry
  ├─ 7. Skills        SkillManager
  ├─ 8. Hooks         HookManager
  ├─ 9. Memory        MemoryManager
  │
  │  ── 尾部核心子系统（必须最后） ──
  ├─ 10. Session      SessionManager —— 必须在 tools/skills/hooks/mcp/memory 之后
  ├─ 11. Commands     CommandManager —— 命令目录，Session 之后（命令可查询 session 状态）
  ├─ 12. Gateways     GatewayManager —— 依赖 Session + Commands
  └─ 13. Schedule     ScheduleManager —— 最后启动，依赖 Session + Gateways（投递目标）

  yield ── 服务运行中，处理请求 ──

退出 (_lifespan exit)
  │
  └─ 反向遍历已加载的 subsystem 列表，逐个 unload
     每个 unload 独立捕获异常，不因单个失败影响其他清理
```

### 三种失败处理策略

1. **致命**（Flag / Config）—— bootstrap 服务。lifespan 直接
   try/except 包 `startup`，失败立即重新抛出，kernel 进程退出
2. **降级**（ConnectionAuthenticator / ToolAuthorizer / Provider / Session 及所有可选子系统）——
   `Subsystem.load(name)` 内部 try/except，异常 log 后返回 None，
   该子系统不加入注册表，kernel 继续启动，依赖它的功能自然失效
3. **跳过**（Tools / Skills / Hooks / MCP / Memory 被 KernelFlags
   关闭时）—— 完全不实例化，不走 startup 路径

### 模块注册表（`KernelModuleTable`）

Kernel 里所有活着的模块都登记在一张表里 ——
`kernel.module_table.KernelModuleTable`。它是 Linux kernel module
list 的等价物：一个权威的地方记录"现在 kernel 里装了什么"。
lifespan 在 bootstrap 服务起来之后构造这张表，挂到
`app.state.module_table`，之后所有路由和 handler 都从它出发查东西，
不再往 `app.state` 上散落 `setattr`。

表的结构刻意让 bootstrap 和普通 subsystem 差异可见：

```python
class KernelModuleTable:
    flags: FlagManager                                # 类型明确的字段
    config: ConfigManager                             # 类型明确的字段
    _subsystems: dict[type[Subsystem], Subsystem]     # 按类 key 的 dict
```

- **Bootstrap 服务**（`flags` / `config`）是专用字段，因为它们在
  任何 Subsystem 之前就存在、API 更丰富、每个 Subsystem 都依赖
  它们 —— 给它们独立字段让这种不对称在类型系统里显式化，查找也
  更便宜更有类型。
- **普通 Subsystem** 走 `_subsystems` dict，**key 是 Subsystem 的类
  本身**，不是字符串名字。配合泛型的 `get[T](cls: type[T]) -> T`，
  调用点 `module_table.get(ConnectionAuthenticator)` 被 IDE 直接识别为
  `ConnectionAuthenticator`，没有字符串魔法、没有 `cast`、没有 `Any`。`dict`
  的插入顺序用来做反向 unload，lifespan 不用额外维护 list。

Subsystem `load` 成功返回实例后，lifespan 调 `module_table.register(instance)`
登记入表；`load` 失败返回 `None`，该 subsystem 就不会出现在表里，
依赖它的功能自然降级。

### 退出顺序

反向遍历 `module_table.subsystems()` 返回的列表逐个 `unload`（顺序
由 `dict` 插入顺序保证），然后 lifespan 直接退出 ——
**bootstrap 服务无需 teardown**：FlagManager 运行期只读，
ConfigManager 的 `update()` 写盘是同步的，两者在内存里都没有待
drain 的状态。`Subsystem.unload()` 自身吞掉 shutdown 异常只做
log，保证即使某个子系统清理失败其他子系统也能继续清理、不产生
资源泄漏。
