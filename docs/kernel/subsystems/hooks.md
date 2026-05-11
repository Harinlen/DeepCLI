# HookManager — Design

> **Quick header**
> - **Role**: event-driven hooks for Mustang runtime lifecycle and tool/session events.
> - **Current code**: `kernel.agents.mustang.hooks.*`, plus fire-sites in sessions, orchestrator, tools, schedule.
> - **Runtime owner**: Mustang runtime after SkillManager.
> - **Boundary**: hook discovery/execution/reminders only; caller subsystems own event timing.

Status: **landed**.  本文先以
[OpenClaw](#3-openclaw-internal-hooks)、[Hermes](#4-hermes-hookregistry)、
[Claude Code blueprint](#5-claude-code-blueprint) 三家 hook 实现为参照
回答 [§2 开放问题](#2-开放问题决议) 后，把决议落到 [§7 设计骨架](#7-设计骨架)。
每一条决策都标注"为什么照抄 / 为什么改造 / 为什么放弃"以备后人质询。

**核心设计 TL;DR**：

- **In-process Python hook**（OpenClaw / Hermes 路线，不走 subprocess）
- **Manifest = OpenClaw style**：`HOOK.md` + frontmatter（`events` / `requires` / `os`）
- **控制流 = OpenClaw mutation**：handler 改 `HookEventCtx` 字段产生副作用，
  `raise HookBlock("reason")` 是唯一的拦截信号
- **多源 + eligibility filter + boundary safety** 全照搬 OpenClaw（user/project 两层）
- **不支持 declared-async**：所有 hook 默认同步 await，慢 hook 自己背锅

> 前置阅读：
> - 子系统分工：[kernel/architecture.md § 子系统清单](../../kernel/architecture.md)
> - 姐妹子系统 fire 点契约：[ToolAuthorizer §14](tool_authorizer.md) +
>   [ToolManager §6.1](tools.md)
> - 旧 pre-kernel 的成品参考：[archive/daemon/daemon/extensions/hooks/](../../../archive/daemon/daemon/extensions/hooks/)
> - 参考源码（只读）：
>   - OpenClaw `src/hooks/` —— `/home/saki/Documents/alex/openclaw/src/hooks/`
>   - Hermes `gateway/hooks.py` —— `/home/saki/Documents/alex/hermes-agent/gateway/hooks.py`
>   - Claude Code `src/utils/hooks/` —— `/home/saki/Documents/projects/claude-code-main/src/utils/hooks/`

---

## 1. 已经定死的契约（不再讨论）

来自已发布的 ToolManager / ToolAuthorizer plan：

1. **可选子系统**，启动顺序 7（Tools 后、MCP 前）。flag-off 跳过；启动失败降级。
2. **不附送 bundled hooks** —— framework only。用户自己在
   `~/.deepcli/hooks/` 写。OpenClaw 的"bundled boot-md / session-memory /
   command-logger"我们不抄。
3. **system_reminder 通道必选** —— 明确否决独立的
   NotificationBus。**实现形式**：`HookEventCtx.messages: list[str]`（OpenClaw
   `event.messages` 的 mutation 风格），handler `append` 字符串，caller fire
   后 drain。⚠️ 原始设计 "HookResult 必须包含 system_reminder
   字段" 现已被本文修订为 "HookEventCtx.messages 字段"，需要在 implementation
   阶段已确认。
4. **12 种事件**对齐 pre-kernel 5.5.4E（archive 里那份 `HookEvent` enum）。
   Claude Code 的 27 种是上限不是目标。
5. **fire 点归属已经定**：
   | 事件 | fire 处 |
   |------|---------|
   | `pre_tool_use` / `post_tool_use` / `post_tool_failure` | `Orchestrator.ToolExecutor`（[tool-manager §6.1](tools.md)）|
   | `permission_requested` / `permission_denied` | `ToolAuthorizer`（[tool-authorizer §14.2](tool_authorizer.md)）|
   | `user_prompt_submit` / `stop` / `pre_compact` / `post_compact` / `subagent_start` | `Orchestrator` 对应调用点 |
   | `session_start` / `session_end` | `SessionManager` |
   | `file_changed` | 写类 Tool 的 `call()` 在返回前 emit，由 `ToolExecutor` 中转 |
6. **HookManager 不是 fire 主体**，只是 dispatch 引擎。
   ToolAuthorizer / Orchestrator / SessionManager 是 fire 点，
   HookManager 只接 `await fire(ctx)` 调用。

---

## 2. 开放问题决议

| # | 题目 | 决议 |
|---|------|------|
| **A** | hook 源类型 | **In-process Python 函数**。command / http / prompt 三种全砍 —— hook 在 kernel 进程内跑，trusted local code（参考 OpenClaw / Hermes） |
| **B** | `if_` 过滤器 | **不引入**。matcher 留给 handler 内部自判（OpenClaw 路线）。砍 DSL，不依赖 ToolAuthorizer rule parser |
| **C** | 阻塞 vs fail-open | **`raise HookBlock("reason")` 单一通道**。`HookEventSpec.can_block` 决定该事件接不接受 block。普通 `Exception` 一律 fail-open per handler（log + 继续） |
| **D** | side-effect 字段作用域 | **OpenClaw mutation 风格**：handler 直接改 `HookEventCtx` 字段（`tool_input` / `user_text` 等）+ append `messages: list[str]`。**没有 HookResult struct** |
| **E** | 并发与超时 | **Sequential await**。**框架不加 timeout**（OpenClaw / Hermes 路线）。handler crash → log + 继续，handler 卡住是用户自己的 bug |
| **F** | HookManager 对外 API | **`async def fire(ctx: HookEventCtx) -> bool`**。返回值就一个 bool（是否被 block）。所有副作用通过 ctx mutation 读回 |
| **G** | system_reminder buffer | **Session 层持有**。HookManager 无状态。caller fire 完调 `session.queue_reminders(ctx.messages)` 显式 drain |
| **H** | 测试策略 | **In-process unit + integration**。无 subprocess fixture / 无 aiohttp test server。`registry.register(event, handler); await mgr.fire(ctx); assert ctx.messages == [...]` |
| **额外** | declared-async 通道 | **不支持**。所有 hook 默认 sync await（Option A）。慢 hook 卡死主流程是用户自己的责任。CC 的 PendingAsyncHook 池留作未来扩展点，不会有 backwards-compat 包袱 |
| **额外** | 多源 / eligibility / boundary | **照搬 OpenClaw 机制，但只 user + project 两层**（无 bundled / plugin）。project 层 explicit-opt-in（`enabled: true` 才生效），user 层 default-on。boundary safety 用 realpath + path-inside check |
| **额外** | wildcard 注册（Hermes 的 `event:*`） | **不引入**。mustang 13 个事件是 flat enum，没有 namespace 层级，wildcard 无对象可匹 |
| **额外** | timeout / 长跑 handler | **框架不管 timeout**（OpenClaw / Hermes 路线）。handler 想要超时自己 `asyncio.wait_for(...)`。handler crash → log + 继续；handler 卡住 → 用户自己的 bug |
| **额外** | sync vs async handler | **两种都支持**。`asyncio.iscoroutine(result)` 自动适配（Hermes 路线） |

---

## 3. OpenClaw：internal-hooks

OpenClaw 是三家里**最完整**的 hook 系统：directory-discovery + frontmatter
manifest + 4 层 source precedence + plugin 来源 + boundary safety。
TypeScript / 无 LLM，但工程化最深。

### 3.1 关键架构概念

```
┌──────────────────────────────────────────────────────────┐
│ HOOK_SOURCE_POLICIES                                     │
│ ┌──────────┬──────────┬─────────────────┬─────────────┐ │
│ │ source   │ preced.  │ defaultEnable   │ canOverride │ │
│ ├──────────┼──────────┼─────────────────┼─────────────┤ │
│ │ bundled  │ 10       │ default-on      │ bundled     │ │
│ │ plugin   │ 20       │ default-on      │ bundled     │ │
│ │ managed  │ 30       │ default-on      │ all         │ │
│ │workspace │ 40       │ explicit-opt-in │ workspace   │ │
│ └──────────┴──────────┴─────────────────┴─────────────┘ │
└──────────────────────────────────────────────────────────┘

ResolveHookEntries: precedence 排序 + 同名 collision → canOverride 仲裁
                    （命中时 kept/ignored 通过 callback 报上层日志）
```

每个 hook 是一个**目录**，约定文件：

```
~/.openclaw/hooks/<hook_name>/
├── HOOK.md             # frontmatter manifest（events / requires / install / export）
└── handler.ts          # 默认导出 InternalHookHandler
```

**InternalHookEvent** 是单一形状 schema（[`internal-hooks.ts:174-188`](../../../../../alex/openclaw/src/hooks/internal-hooks.ts)）：

```ts
interface InternalHookEvent {
  type: InternalHookEventType;     // command | session | agent | gateway | message
  action: string;                   // "new" / "compact:before" / "received"
  sessionKey: string;
  context: Record<string, unknown>;
  timestamp: Date;
  messages: string[];               // ⚠️ hooks 往里 push 字符串就能回灌给用户
}
```

**事件 dispatch**（[`internal-hooks.ts:289-306`](../../../../../alex/openclaw/src/hooks/internal-hooks.ts)）：

```ts
async function triggerInternalHook(event) {
  // 同时收 type 级 + type:action 级 listener，按注册序串行
  const all = [...handlers.get(type), ...handlers.get(`${type}:${action}`)];
  for (const h of all) {
    try { await h(event); }
    catch (err) { log.error(...); }   // fail-open per handler
  }
}
```

**eligibility filter**（[`config.ts`](../../../../../alex/openclaw/src/hooks/config.ts) + frontmatter `requires`）在 load 时
跑一次，过滤掉：
- `os` 不匹配（mac/linux/win）
- `requires.bins` 二进制不存在
- `requires.env` 环境变量未设
- `requires.config` 配置 path 不真值
- `remote.platforms` 远程不支持

**Plugin 来源**（[`plugin-hooks.ts`](../../../../../alex/openclaw/src/hooks/plugin-hooks.ts)）：通过 plugin manifest registry
拉 hook 目录，强制 realpath boundary 检查（防 symlink 越权）。

**Boundary safety**（[`loader.ts`](../../../../../alex/openclaw/src/hooks/loader.ts)）：
- `openBoundaryFile` 防止 hook 路径越出 hookDir
- Workspace hooks 加载前 `log.warn("trusted local code")`
- Cache busting 仅对 mutable source（workspace/managed），bundled 不破缓存

### 3.2 fire 点示例

[`compaction-hooks.ts`](../../../../../alex/openclaw/src/agents/pi-embedded-runner/compaction-hooks.ts) 是个干净的 reference：

```ts
async function runBeforeCompactionHooks(params) {
  try {
    const event = createInternalHookEvent("session", "compact:before", key, ctx);
    await triggerInternalHook(event);
  } catch (err) {
    log.warn("session:compact:before hook failed", { ... });
  }
  // 同时还跑 hookRunner.runBeforeCompaction —— 双轨并存
}
```

**关键观察**：fire 点自己捕获异常，HookManager 不再加一层 try/except。

---

## 4. Hermes：HookRegistry

Hermes 是三家里**最简洁**的（170 行 Python，[`gateway/hooks.py`](../../../../../alex/hermes-agent/gateway/hooks.py)）。
单层注册表 + Python module 动态 import + sync/async 兼容。

### 4.1 完整骨架

```python
class HookRegistry:
    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}
        self._loaded_hooks: list[dict] = []         # for /hooks list

    def discover_and_load(self):
        self._register_builtin_hooks()              # builtin 直接 import
        for hook_dir in HOOKS_DIR.iterdir():
            manifest = yaml.safe_load(...)          # HOOK.yaml
            spec = importlib.util.spec_from_file_location(...)
            module = importlib.util.module_from_spec(spec)
            handle_fn = module.handle               # 唯一约定的入口
            for event in manifest["events"]:
                self._handlers.setdefault(event, []).append(handle_fn)

    async def emit(self, event_type, context):
        handlers = self._handlers.get(event_type, [])
        if ":" in event_type:
            handlers += self._handlers.get(f"{event_type.split(':')[0]}:*", [])
        for fn in handlers:
            try:
                result = fn(event_type, context)
                if asyncio.iscoroutine(result): await result
            except Exception as e:
                print(f"[hooks] Error: {e}")
```

### 4.2 Hermes 砍掉的事

- 没有 source precedence（没有 bundled vs workspace 的概念）
- 没有 `if_` filter（matcher 留给 hook 内部判断）
- 没有 `HookResult` —— 永远 fire-and-forget
- 没有 boundary check
- 没有 eligibility metadata
- builtin hook 是硬编码 `_register_builtin_hooks` 里 import 一行（[`hooks.py:54-67`](../../../../../alex/hermes-agent/gateway/hooks.py)）

**对 kernel 的设计影响**：sync/async handler 兼容（`asyncio.iscoroutine` 自动适配）
被 mustang 借鉴。`event_type:*` wildcard 注册没借 —— mustang 13 个事件是 flat enum,
没有 namespace 层级,wildcard 无对象可匹（见 §2 "额外" wildcard 行）。

---

## 5. Claude Code blueprint

CC 的 hook 系统**不是我们的目标形态**（27 个事件 + Async hook
attachment 通道远超 mustang 需要），但有几个原语值得偷：

### 5.1 退出码语义（per event）

[`hooksConfigManager.ts`](../../../../../projects/claude-code-main/src/utils/hooks/hooksConfigManager.ts) 定义了每个事件的 exit code 含义。
**精华是 exit-code 既携带 control-flow 也携带 stdout 用途**：

| 事件 | exit 0 | exit 2 | other |
|------|--------|--------|-------|
| `PreToolUse` | stdout 隐藏 | **block + stderr 给 model** | stderr 给 user，继续 |
| `PostToolUse` | stdout 给 transcript | **stderr 给 model** | stderr 给 user |
| `UserPromptSubmit` | stdout 给 model | **block + 抹除 prompt + stderr 给 user** | stderr 给 user |
| `PreCompact` | stdout 当 compact instructions | **block compaction** | stderr 给 user，继续 |
| `Stop` | 隐藏 | **stderr 给 model + 继续 turn** | stderr 给 user |
| `SubagentStop` | 隐藏 | **stderr 给 subagent + 继续** | stderr 给 user |
| `SessionStart` | stdout 给 Claude | blocking errors **ignored** | stderr 给 user |

**核心 takeaway**：Claude Code 的 hook 没有"统一的 blocked 字段"——
每个事件单独定义 exit-code → action 的 mapping。比起 archive
pre-kernel 的 `HookResult.blocked` 单字段，这种"逐事件 exit-code 表"
更清晰，但需要文档约定到位（否则 hook 作者猜不到自己的事件该返什么）。

### 5.2 Async hook attachment 通道

[`AsyncHookRegistry.ts`](../../../../../projects/claude-code-main/src/utils/hooks/AsyncHookRegistry.ts) 实现了一种特殊语义：hook 输出第一行是
`{"async": true, "asyncTimeout": 15000}` 时进入 pending 池，下一轮
主循环 `checkForAsyncHookResponses()` 收集已完成的 async hook，
作为 attachment 注入到下一个 user message 前面。

```
T=0 fire pre_tool_use → hook stdout 第一行: {"async": true, ...}
                       → register PendingAsyncHook(processId, timeout=15s)
T=0 ToolExecutor 不等，立即继续
T=N 主循环下一轮 → checkForAsyncHookResponses()
                  → 已完成的 hook 返回 SyncHookJSONOutput
                  → 拼成 attachment 塞 user message 前
```

**与 mustang 的关系**：`system_reminder` side-effect 字段就是这种通道的
mustang 版（更简化：直接同步返字符串，不做 process 跟踪），见 §6.G。

### 5.3 HookExecutionEvent 旁路事件流

[`hookEvents.ts`](../../../../../projects/claude-code-main/src/utils/hooks/hookEvents.ts) 把 hook 自身的 lifecycle（started / progress /
response）emit 到独立的 event handler，**不污染主 LLM 消息流**。
`ALWAYS_EMITTED_HOOK_EVENTS = ['SessionStart', 'Setup']` —— 其它默认静音。

**对 mustang 的启发**：是否要把 hook 执行本身作为 ACP `session/update`
事件 emit 给客户端调试？（见 §7.延伸题目）。

---

## 6. 三家答案对照 §A–H

下面把每条开放问题套到三家，看谁解了、怎么解、kernel 该怎么取舍。
下面每节末尾的"**最终决议**"行均已与 §2 对齐。**讨论过程**保留是为了让
后续维护者看到"为什么没选另一条路"。

### 6.A — Hook 源类型（command / prompt / http）

| 项目 | command | prompt | http | 备注 |
|------|:-------:|:------:|:----:|------|
| pre-kernel archive | ✅ | ⚠️ Phase 3 stub | ✅ | http 有完整实现 |
| OpenClaw | ❌ | ❌ | ❌ | 只跑**进程内 TS 模块**（动态 import）|
| Hermes | ❌ | ❌ | ❌ | 只跑**进程内 Python 模块** |
| Claude Code | ✅ | ❌ | ❌ | 100% subprocess + JSON stdin/stdout |

**观察**：
- OpenClaw / Hermes 都把 hook **跑在 kernel 进程内**（trusted local code），
  这换来零 IPC 开销但也意味着 hook 一抛异常能搞坏整个 kernel
  （所以 OpenClaw 用 `try/catch` 严格隔离每次 trigger）。
- Claude Code 走 subprocess，进程隔离更安全但每次 hook 启动都付 fork 成本。
- archive pre-kernel 是混合：command/http 走 subprocess/socket，prompt 当 stub。

**最终决议**（见 §2.A）：**只保留 in-process Python module**，command / http / prompt
全砍。原本"subprocess 隔离更安全"的论据被推翻 —— 三家中两家都跑 in-process,
mustang 也接受同样的 trust model（hook 是 trusted local code，跟 Tools 同级）。
hook crash → fail-open per handler；handler 卡住 (sync sleep / 死循环 / 漏 await)
也不兜底 —— 用户自己的 bug, 跟 OpenClaw / Hermes 选同样的 trust 边界。

### 6.B — `if_` 过滤器

| 项目 | 怎么做 |
|------|--------|
| pre-kernel archive | 自己 parse `ToolName(pattern)` 字符串，跑 `daemon.permissions.rules.matches()` |
| OpenClaw | **没有 `if_`** —— 在 hook handler 内自己判断 `event.action == "received"` |
| Hermes | **没有 `if_`** —— 同上 |
| Claude Code | 有 `matcher` 字段（`matcherMetadata.fieldToMatch` per event：tool_name / source / trigger / agent_type / mcp_server_name），不是字符串 DSL，是逐事件 schema |

**观察**：
- archive 复用 ToolAuthorizer rule parser，但实际只有
  `pre_tool_use` / `post_tool_use` 两个事件用得上 tool_name 匹配；
  其它非 tool 事件 if_ 完全无意义（[`registry.py:107-118`](../../../../mustang/archive/daemon/daemon/extensions/hooks/registry.py)）。
- Claude Code 用 per-event matcher 字段更精确：`PreCompact` 匹 `trigger ∈ {manual, auto}`，
  `SessionStart` 匹 `source ∈ {startup, resume, clear, compact}`，
  这些根本不是 ToolName DSL 能表达的。

**最终决议**（见 §2.B）：**完全不引入 `if_` DSL**。matcher 全留给 handler 内部
自判（OpenClaw / Hermes 路线）。原本"5 个 tool 事件复用 ToolAuthorizer parser"
被推翻 —— in-process Python handler 写 `if ctx.tool_name == "Bash" and ...`
比学一套 DSL 更便宜，也避免 HookManager 跨依赖 ToolAuthorizer rule parser
（两者启动顺序虽然 OK，但物理依赖能省则省）。

### 6.C — 阻塞 vs fail-open 事件矩阵

archive 没明确列表，CC 是逐事件 exit-code 语义。kernel 需要一张
**显式矩阵**，启动时 validate hook 注册的事件 + 期望返回。

| Event | blocked 真能阻断？| modified_input 生效？| permission 生效？| system_reminder 生效？|
|---|:---:|:---:|:---:|:---:|
| `pre_tool_use` | ✅ | ✅ | ❌ | ✅ |
| `post_tool_use` | ❌ | ❌ | ❌ | ✅ |
| `post_tool_failure` | ❌ | ❌ | ❌ | ✅ |
| `user_prompt_submit` | ✅ | ✅（rewrite prompt）| ❌ | ✅ |
| `pre_compact` | ✅（CC 抄过来）| ❌ | ❌ | ✅ |
| `post_compact` | ❌ | ❌ | ❌ | ✅ |
| `session_start` | ❌ | ❌ | ❌ | ✅ |
| `session_end` | ❌ | ❌ | ❌ | ❌（session 已经走了）|
| `stop` | ❌ | ❌ | ❌ | ✅（注入到下一轮）|
| `subagent_start` | ❌ | ❌ | ❌ | ✅ |
| `permission_requested` | ❌ | ❌ | ❌ | ❌（纯通知）|
| `permission_denied` | ❌ | ❌ | ❌ | ❌（决策已落）|
| `file_changed` | ❌ | ❌ | ❌ | ✅ |

**讨论点**：
- `pre_compact` 是否要让用户 veto？CC 给了（exit 2）；archive
  pre-kernel 没给。Kernel 倾向 **跟 CC**——allow 用户在压缩前 dump 状态/取消。
- `permission_*` 事件设计上是**纯审计**，不应该让 hook 改决策
  （否则 ToolAuthorizer 的"唯一仲裁者"语义就破了）。这与
  [tool-authorizer.md §14](tool_authorizer.md) 一致。
- `session_end` 时 session 已经在销毁路径上，注入 reminder 没人看，全砍。

**最终决议**（见 §2.C + §7.1 EVENT_SPECS）：上表的 `blocked` 列被简化为
`HookEventSpec.can_block: bool`，由 `raise HookBlock("reason")` 触发；
`modified_input` 列简化为 `accepts_input_mutation: bool`，由 handler 直接
mutate `ctx.tool_input` / `ctx.user_text` 实现；`system_reminder` 列在
**所有事件**上都生效（`ctx.messages.append(...)` 永远可写，没人 drain
也不出错）；`permission` 列整列删除。

### 6.D — Side-effect 字段作用域

**最终决议**（见 §2.D + §7.1 `HookEventCtx`）：**没有 `HookResult` struct**。
所有副作用走 OpenClaw mutation 风格 —— handler 直接改 `ctx.tool_input` /
`ctx.user_text` / `ctx.messages`。原本设想的 `HookResult.permission` 字段
完全删除（authorize 是 ToolAuthorizer 唯一职责，hook 不得参与）。

### 6.E — 并发与超时

| 项目 | 策略 |
|------|------|
| archive pre-kernel | 同事件内 sequential；any blocked → 短路；`async_=True` fire-and-forget；per-hook timeout=30s |
| OpenClaw | sequential；fail-open per handler；无 timeout（trusted local code） |
| Hermes | sequential；fail-open；无 timeout |
| Claude Code | 同事件内 sequential；exit code 决定 block；async via `{"async": true, "asyncTimeout"}` |

**最终决议**（见 §2.E + 额外行）：
- **Sequential** 保留 ✅
- **框架完全不加 timeout** —— OpenClaw / Hermes 都没加, mustang 跟. handler 自己想要超时就 `asyncio.wait_for(slow_thing(), 60)` 一行搞定. 框架兜底 30s 是过度防御
- **`async_=True` fire-and-forget** ❌ 删除 —— hook 想 spawn 背景 task 就自己
  `asyncio.create_task`，框架不接管 lifecycle（也不存在 archive 那种 GC 防护需求,
  因为没有 declared-async 通道）
- **CC 的 async attachment 通道** ❌ 不抄（详见 §2 "额外" 行的 declared-async 决议）
- **sync handler 也允许** —— `asyncio.iscoroutine(result)` 自动适配, Hermes 路线

### 6.F — HookManager 对外 API

候选形状：

```python
# 方案 A（archive style）
async def run_hooks(event: HookEvent, ctx: HookContext) -> HookResult: ...

# 方案 B（OpenClaw style）
async def trigger(event: InternalHookEvent) -> None: ...   # 永远 fire-and-forget

# 方案 C（kernel proposal）—— 单方法 + 矩阵决定语义
async def fire(
    self,
    event: HookEvent,
    ctx: HookContext,
) -> HookResult:
    """统一入口。HookEventSpec 决定哪些字段对此 event 生效;
    aller 只看返回的 result 即可，不用区分 blocking vs notification。"""
```

**最终决议**（见 §2.F + §7.2）：单方法 `async def fire(ctx) -> bool`。
返回值进一步**从 `HookResult` 简化为 bool**（"是否被 block"），其余 mutation
走 ctx 字段读回。candidate B（OpenClaw 路线）和 C（kernel proposal）合体后
的最终形态。

### 6.G — `system_reminder` buffer 归属

候选：

| 选项 | 谁持有 | pros | cons |
|------|--------|------|------|
| (i) HookManager 队列 | `dict[session_id, list[str]]` | 集中、易测 | HookManager 变成有状态服务，需要 session lifecycle 钩子 |
| (ii) Session 层持有 | SessionState 上加 `pending_reminders: list[str]` | session 已经管 lifecycle，自然 fit | HookManager 必须知道 session_id 才能 push |
| (iii) Orchestrator 局部 | Orchestrator query 内部局部变量 | 最简单 | 不能跨 turn / 跨 query 累积 |

**观察**：
- CC 的 [`useDeferredHookMessages.ts`](../../../../../projects/claude-code-main/src/hooks/useDeferredHookMessages.ts) 是 React-side state，
  对 mustang 没参考意义。
- `HookContext` 反正已经携带 `session_id`（archive 已经有这个字段）。
- HookManager 的 `fire()` 不持 session 状态。最不耦合的设计是
  **caller 显式 drain `ctx.messages` 后 push 给 Session**。

**最终决议**（见 §2.G + §7.3）：采 (ii)。Session 层持 `pending_reminders: list[str]`，
caller fire 后显式 `session.queue_reminders(ctx.messages)` drain。HookManager
保持无状态。这条与 [tool-authorizer.md §3.2 SessionGrantCache](tool_authorizer.md)
同模式：session-scoped state 永远归 Session 层。

### 6.H — 测试策略

archive pre-kernel / OpenClaw / Hermes 的测试都是**unit + integration 双层**。

**最终决议**（见 §2.H）：纯 in-process unit + integration，无 subprocess fixture / 无
HTTP test server。
- **Unit**：注册假 handler，验证 sequential / HookBlock 触发 / sync 与 async
  handler 都能跑 / `accepts_input_mutation` 矩阵 / `messages` append /
  普通 Exception fail-open / HookBlock 在 can_block=False 事件上被忽略
- **Integration**：
  - 真 HOOK.md 目录扫描 + boundary check 拒绝越界
  - reminder roundtrip：`HookManager.fire → ctx.messages → Session.queue_reminders →
    Orchestrator drain` 端到端
  - eligibility filter：`requires.bins` 不存在时 hook 不加载

---

## 7. 设计骨架

### 7.1 数据类型

```python
# kernel/hooks/types.py

class HookEvent(enum.Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_FAILURE = "post_tool_failure"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    POST_SAMPLING = "post_sampling"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    STOP = "stop"
    SUBAGENT_START = "subagent_start"
    PERMISSION_REQUESTED = "permission_requested"
    PERMISSION_DENIED = "permission_denied"
    FILE_CHANGED = "file_changed"


class HookBlock(Exception):
    """Handler 用 raise HookBlock("reason") 拦截当前事件。

    仅在 EVENT_SPECS[event].can_block == True 的事件上生效;
    其他事件抛 HookBlock → 框架 log warning 后忽略, 主流程继续.
    普通 Exception 一律 fail-open per handler (log + 继续).
    """
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class AmbientContext:
    """所有 hook 都能看到的共享 ambient state. Caller fill, frozen.

    字段对齐 ToolAuthorizer.AuthorizeContext (§4.2) —— mustang 整套子系统
    共用一组 ambient 概念, hook 跟 authorizer 看到的是同一份 mental model.
    """
    session_id: str
    cwd: Path
    agent_depth: int                       # 0 = root, ≥1 = subagent
    mode: Literal["default", "plan", "bypass"]
    timestamp: float


@dataclass
class HookEventCtx:
    """Mutable per-fire payload (OpenClaw 风格).

    Handler 直接修改 mutable 字段产生副作用; caller fire() 后读回 mutated 值.
    框架不对 mutation 做任何 schema 校验 —— 写错了用户自己负责.

    ⚠️ tool_input / user_text 等 mutation 不影响 audit trail —— 因为 tool_use /
    user_prompt 在 fire 前已经 append 到 SessionManager 的持久事件日志。
    内存 mutation 只对当前 turn 的下游 (tool.call /
    LLM stream) 生效, 不会回写历史. 所以 caller 不需要 deepcopy 防御.
    """
    event: HookEvent
    ambient: AmbientContext

    # Tool 类事件 (pre/post_tool_use, post_tool_failure, permission_*)
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)  # ⭐ mutable rewrite point
    tool_output: str | None = None
    error_message: str | None = None

    # User prompt (user_prompt_submit)
    user_text: str | None = None  # ⭐ mutable rewrite point

    # Compaction (pre/post_compact)
    message_count: int | None = None
    token_estimate: int | None = None

    # File (file_changed)
    file_path: str | None = None
    change_type: str | None = None  # "edit" | "write"

    # Session-specific (session_start)
    is_resume: bool | None = None

    # Stop-specific (stop)
    stop_reason: str | None = None

    # Subagent-specific (subagent_start)
    agent_description: str | None = None

    # ⭐ system_reminder 通道 — handlers append 字符串, caller drain 后塞 Session
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HookEventSpec:
    """声明每个事件的语义 (运行时校验用)."""
    can_block: bool                  # raise HookBlock 是否生效
    accepts_input_mutation: bool     # tool_input / user_text 改写是否生效


EVENT_SPECS: dict[HookEvent, HookEventSpec] = {
    HookEvent.PRE_TOOL_USE:        HookEventSpec(can_block=True,  accepts_input_mutation=True),
    HookEvent.POST_TOOL_USE:       HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.POST_TOOL_FAILURE:   HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.USER_PROMPT_SUBMIT:  HookEventSpec(can_block=True,  accepts_input_mutation=True),
    HookEvent.POST_SAMPLING:       HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.PRE_COMPACT:         HookEventSpec(can_block=True,  accepts_input_mutation=False),
    HookEvent.POST_COMPACT:        HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.SESSION_START:       HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.SESSION_END:         HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.STOP:                HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.SUBAGENT_START:      HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.PERMISSION_REQUESTED:HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.PERMISSION_DENIED:   HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.FILE_CHANGED:        HookEventSpec(can_block=False, accepts_input_mutation=False),
}
# ⚠️ messages: list[str] 在所有事件上都生效, 不需要在 spec 里声明
# (session_end 上 append 也可以, 只是没人会 drain 而已 —— 不出错)


# Handler signature (sync 或 async 都支持; iscoroutine 自动适配)
HookHandler = Callable[[HookEventCtx], Awaitable[None] | None]
```

### 7.2 HookManager

```python
# kernel/hooks/__init__.py

class HookManager(Subsystem):
    """In-process Python hook engine. 无状态 fire engine."""

    async def startup(self) -> None:
        cfg = self._module_table.config.get_section("hooks", HooksConfig)
        self._registry = HookRegistry()
        # 多源 discovery (OpenClaw 套路, 但只 user + project 两层):
        #   1. ~/.deepcli/hooks/<name>/         user-layer, default-on
        #   2. <cwd>/.mustang/hooks/<name>/     project-layer, explicit-opt-in
        # 目录不存在 → 静默跳过, 不报警 (Hermes 路线)
        for entry in await self._discover(cfg):
            if not self._eligible(entry):
                continue
            handler = self._import_handler_module(entry)  # boundary-checked import
            for event_str in entry.metadata.events:
                self._registry.register(HookEvent(event_str), handler)

    async def shutdown(self) -> None:
        # 无 background task 持有; 无需 drain
        pass

    async def fire(self, ctx: HookEventCtx) -> bool:
        """Fire all handlers for ctx.event in registration order.

        Returns:
            True if any handler raised HookBlock AND ctx.event allows blocking.
            Caller reads back ctx.tool_input / ctx.user_text / ctx.messages 后续处理.

        语义:
          - sequential: handler 按注册顺序串行 await
          - sync handler: 直接调; async handler: await result
          - 框架不加 timeout: handler 卡住是用户的 bug. 想要超时 handler 自己
            asyncio.wait_for(slow_thing(), 60). 这条决议见 §2 "额外" timeout 行
          - 普通 Exception fail-open: log + 继续下个 handler
          - HookBlock 在 can_block=False 事件上抛 → log warning 后忽略
        """
        spec = EVENT_SPECS[ctx.event]
        for handler in self._registry.get(ctx.event):
            try:
                result = handler(ctx)
                if asyncio.iscoroutine(result):
                    await result
            except HookBlock as block:
                if spec.can_block:
                    logger.info("Hook blocked %s: %s", ctx.event.value, block.reason)
                    return True
                logger.warning(
                    "HookBlock raised on non-blocking event %s, ignoring",
                    ctx.event.value,
                )
            except Exception:
                logger.exception("Hook crashed on %s — fail-open", ctx.event.value)
        return False
```

### 7.2.1 Hook 目录 / handler 模块约定

实装阶段 hard-coded 的契约（不允许 customization, 跟 Hermes 同款简化）：

| 项 | 约定 |
|----|------|
| Hook 目录命名 | 任意, 目录名作为 hook id |
| Manifest 文件 | **`HOOK.md`** (固定名), 含 frontmatter |
| Handler 文件 | **`handler.py`** (固定名), 跟 HOOK.md 同目录 |
| Handler 入口 | top-level **`handle(ctx: HookEventCtx)`** (固定名, sync 或 async 均可) |
| 多事件订阅 | 同一个 `handle` 函数 → frontmatter `events: [a, b]` 列表 |
| 不支持 | OpenClaw 的 frontmatter `export:` 自定义函数名 |

```
~/.deepcli/hooks/<hook_name>/
├── HOOK.md      ← frontmatter manifest (events / requires / os)
└── handler.py   ← async def handle(ctx) -> None
```

Handler 模块 import 时走 boundary check（`pathlib.Path.resolve()` +
`is_relative_to(hook_dir)`），防 symlink 越权 —— OpenClaw `realpath +
isPathInsideWithRealpath` 的 Python 等价物，统一封装到 `kernel/security/paths.py`.

### 7.3 Caller 模式

```python
# 所有 caller 共用的 ambient 构造 (一次 build, 多次复用)
ambient = AmbientContext(
    session_id=self._session_id,
    cwd=self._cwd,
    agent_depth=self._agent_depth,
    mode=self._mode,
    timestamp=time.time(),
)

# Orchestrator: user_prompt_submit
ctx = HookEventCtx(
    event=HookEvent.USER_PROMPT_SUBMIT,
    ambient=ambient,
    user_text=prompt_text,
)
blocked = await self._deps.hooks.fire(ctx)
if blocked:
    yield UserPromptBlocked(); return
prompt_text = ctx.user_text                    # 可能被 hook rewrite
self._session.queue_reminders(ctx.messages)    # drain system_reminders
# ⚠️ fire 后 ctx 视为 ownership 已交出, 不要再 mutate ctx.messages —
# Session 持有的是引用, caller 后续改会污染 Session 状态
```

```python
# ToolExecutor step 3 (pre_tool_use) + step 6 (post_tool_use)
ctx = HookEventCtx(
    event=HookEvent.PRE_TOOL_USE,
    ambient=ambient,
    tool_name=tc.name,
    tool_input=tc.input,  # 直接传引用, 不 copy: tool_use 已经 append 到 SessionStore 事件日志
                          # conversation history (在 ToolExecutor 调到这一步之前),
                          # audit trail 已 frozen, hook mutation 不会回写历史
)
blocked = await self._hooks.fire(ctx)
if blocked:
    yield ToolCallError(reason="blocked by pre_tool_use hook"); return
effective_input = ctx.tool_input               # rewrite 已 in-place 应用
self._session.queue_reminders(ctx.messages)
```

### 7.4 Hook 作者写起来

```
~/.deepcli/hooks/git-status-injector/
├── HOOK.md
└── handler.py
```

```yaml
---
name: git-status-injector
description: Inject git status before each user prompt
metadata:
  mustang:
    events: [user_prompt_submit]
    requires:
      bins: [git]
---
```

```python
# handler.py
import subprocess
from kernel.agents.mustang.hooks import HookEventCtx, HookBlock

async def handle(ctx: HookEventCtx) -> None:
    # OpenClaw style: handler 自己 if 判断 (无 if_ DSL)
    if ctx.event.value != "user_prompt_submit":
        return
    if not ctx.user_text or not ctx.user_text.startswith("/git"):
        return
    result = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
    if result.stdout.strip():
        ctx.messages.append(f"<git-status>\n{result.stdout}\n</git-status>")
```

```python
# 想要拦截某个危险 prompt:
async def handle(ctx: HookEventCtx) -> None:
    if ctx.event.value == "user_prompt_submit" and "DROP TABLE" in (ctx.user_text or ""):
        raise HookBlock("dangerous SQL detected in prompt")
```

---

## 8. 仍未解决 / 下一步

- [x] ~~**gap-review 回写**~~: gap-review 已删除，设计定稿为 `HookEventCtx.messages` 字段。
- [ ] **EVENT_SPECS 的 can_block 精确值**: §7.1 给的是初版, 实装前请最终
      过一遍 (尤其 `pre_compact` 是否真给阻断权 —— 当前定 True, 跟 CC 一致).
- [ ] **配置 schema 形状**: HOOK.md frontmatter 的具体字段 (events 必填,
      `requires.bins / env / config`, `os`, `disabled` 等). 留到 implementation
      阶段定细节; 跟 [tool-manager.md ConfigSchema](tools.md) 对齐风格.
- [ ] **Project 层 = `<cwd>/.mustang/hooks/`**, 无 walk-up, 无 config override.
      用户在 subdir 里跑 mustang 就在 subdir 的 .mustang/ 里找 hooks. 简单 explicit.
      跟 ConfigManager 的 project root 解析对齐 (实装阶段确认两者用同一份逻辑,
      避免 config 找到 root A 但 hooks 找到 root B 的奇怪状态).
- [ ] **Project 层的 explicit-opt-in 怎么落**: OpenClaw 走 config 里
      `hooks.entries.<name>.enabled` flag. mustang 类似 —— 项目级 hook 默认
      disabled, 用户在 ConfigManager `hooks` section 里 explicit `enabled: true`
      才生效. 防止 git clone 别人项目就被 inject hook.
- [ ] **Hook 执行可观测性**: 是否 emit `HookExecutionEvent` (started / done /
      crashed) 到 ACP `session/update`? CC 默认只 emit `SessionStart` / `Setup`,
      其它静音. DeepCLI **倾向同样默认静音 + flag 开调试通道**, 留 implementation
      阶段决定.
- [ ] **MCP elicitation hook**: CC 有, mustang MCP 子系统未实装, 先搁置.
- [ ] **Plugin hooks**: OpenClaw 有 plugin 来源, mustang 没 plugin 概念,
      暂时只支持 user + project 两层. 未来加 plugin 系统时再扩 source 层.
- [x] **进文 subsystems 时机**: ToolManager / ToolAuthorizer / Tools
      子系统已 land (Phase 7 M1/M2/M3), fire 点的实装也已接入. 本文
      已整合到 `docs/kernel/subsystems/hooks.md`（本文档）。
