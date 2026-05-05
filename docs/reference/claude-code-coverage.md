# DeepCLI vs Claude Code CLI 功能覆盖评估

> 评估时间：2026-04-25，基于 kernel 重写后的实际代码状态。
> 最后更新：PromptBuilder 缓存排序修正 + PromptManager 用户覆盖层 + AgentTool kind=orchestrate + CC 新工具追踪。

---

## 已实现的核心功能

| 功能领域 | 状态 | 说明 |
|---|---|---|
| **多轮对话 + 工具循环** | ✅ 完成 | Orchestrator 查询循环，max_turns 由调用方控制（默认 0 = 无限制，子 agent 默认 200 轮），与 Claude Code main 对齐 |
| **文件操作工具** | ✅ 完成 | FileRead（含图片 PNG/JPEG/WebP/GIF + PDF via PyMuPDF）, FileWrite, FileEdit, Glob, Grep + 文件状态追踪 (FileStateCache, mtime+hash) |
| **Bash 执行** | ✅ 完成 | BashTool + 动态权限 + 破坏性命令警告 |
| **PowerShell** | ✅ 完成 | PowerShellTool（Windows 支持） |
| **权限系统** | ✅ 完成 | 4 种模式 + 4 层规则引擎 (user/project/local/flag) + SessionGrantCache + Bash LLMJudge 分类 |
| **MCP 集成** | ✅ 完成 | 4 种传输 (stdio/SSE/WebSocket/HTTP) + MCPAdapter + 健康检查 |
| **Skills 系统** | ✅ 完成 | 5 来源 (project/external/user/bundled/MCP) + SkillTool + 懒加载 |
| **Hooks 系统** | ✅ 完成 | 16 事件 + JSON 结构化输出 + modified_input/permission |
| **Session 持久化** | ✅ 完成 | SQLite (WAL) + sidecar 大对象溢出 + 恢复 |
| **Context 压缩** | ✅ 完成 | 3 层级联: snip → micro-compact → LLM 摘要 + 反应性压缩 (PromptTooLongError → 重试) |
| **多 Provider** | ✅ 完成 | Anthropic + OpenAI-compatible + Bedrock + NVIDIA NIM |
| **Extended Thinking** | ✅ 完成 | Anthropic provider thinking 参数 + ThoughtChunk 流式事件 |
| **Prompt Caching** | ✅ 完成 | PromptSection cache 标志 + Anthropic cache_control 注入 |
| **配置分层** | ✅ 完成 | 2 层: global (`~/.mustang/config/`) + project (`.mustang/config/`) |
| **Probe CLI** | ✅ 完成 | WebSocket REPL 客户端 + `--test` 机器可读模式 |
| **Gateway 系统** | ✅ 完成 | Discord adapter 已实现 |
| **max_output_tokens 恢复** | ✅ 完成 | 8k→64k 自动升级 + 最多 3 次重试 |
| **多模态输入** | ✅ 完成 | 图片 (base64 ImageContent) + PDF (PyMuPDF 渲染) |
| **Memory 系统** | ✅ 完成 | 4 分类目录树 (profile/semantic/episodic/procedural) + BM25(jieba)+LLM scoring + hot/warm/cold 排名 + 5 memory tools + 后台 agent (三层提取+去重+矛盾检测) + 双通道注入 + 策略规则。超越 Claude Code：结构化 scoring（CC 是 binary），BM25 pre-filter（CC 无），分类存储（CC 平铺），时间衰减+evergreen 豁免（CC 无），后台幻觉过滤（CC 无） |
| **AgentTool + Sub-agent** | ✅ 完成 | AgentTool (含 `name` 参数 + transcript 捕获) + SendMessageTool (queue/resume/cross-session 三路径) + spawn_subagent closure + SubAgentStart/SubAgentEnd 事件 |
| **TaskManager** | ✅ 完成 | TaskRegistry (per-session) + TodoWriteTool + TaskOutputTool + TaskStopTool + 后台任务通知 drain + GC |
| **ToolSearch / 延迟加载** | ✅ 完成 | ToolSearchTool（3 种查询模式：select/+prefix/freetext）+ ToolRegistry.promote() + deferred_listing 注入 system-prompt + ToolManager 自动按 should_defer 分层注册 |
| **EnterPlanMode / ExitPlanMode** | ✅ 完成 | 两个 deferred 工具，LLM 通过 ToolSearch 加载后调用。EnterPlanMode 触发 plan mode 切换，ExitPlanMode 携带 plan 文本退出。完整 CC 对齐：5 阶段提示词 + full/sparse 节流 + plan 文件管理 (plans.py) + plan 文件写例外 + Session 事件广播 + prePlanMode 恢复 + re-entry/exit 通知 + non-interactive 禁用 + 用户确认 |
| **Web 工具** | ✅ 完成 | WebFetchTool + WebSearchTool（deferred tools，通过 ToolSearch 加载） |
| **AskUserQuestion** | ✅ 完成 | AskUserQuestionTool — permission channel hijack 实现结构化提问，客户端通过 updated_input 回传答案 |
| **Git 上下文** | ✅ 完成 | GitManager 注入完整 CC 格式 git context（branch/main_branch/user/status/log）+ EnterWorktree/ExitWorktree 工具 |
| **Credential Store** | ✅ 完成 | SecretManager（SQLite + 0600 权限）+ `${secret:name}` config 展开 + `/auth` CLI 命令 + ACP `secrets/auth` 方法 |
| **MCP OAuth** | ✅ 完成 | OAuth 2.1 Authorization Code + PKCE + RFC 9728/8414 discovery + Dynamic Client Registration + McpAuthTool 伪工具 + token refresh + NeedsAuth 状态机 |
| **PromptBuilder 缓存排序** | ✅ 完成 | cacheable 节（identity→system→memory→skills→git_commit_pr）置于 volatile 节（MCP instructions→Git context→Environment）之前。Environment（含时间戳）正确放在最后，与 CC `getSystemPrompt()` 顺序对齐 |
| **PromptManager 用户覆盖** | ✅ 完成 | PromptManager 支持用户级 prompt 文件覆盖 builtin 默认值（`~/.mustang/prompts/` 覆盖层） |
| **AgentTool kind=orchestrate** | ✅ 完成 | AgentTool `kind` 由 `execute` 改为 `orchestrate`，plan mode 下正确保留 Agent 工具（plan mode 过滤仅移除 mutating kinds），与 CC plan mode 行为对齐 |
| **Memory strategy 指导扩展** | ✅ 完成 | `memory_strategy.txt` 从 17 行扩展到 125 行，包含完整的 4 类型 (profile/semantic/episodic/procedural) 说明、衰减规则、使用时机 |

## 未实现的功能

| 功能领域 | 状态 | 说明 |
|---|---|---|
| **Notebook 编辑** | ⛔ 不在范围 | 不支持 `.ipynb`；session 日志用 SQLite，见 [session.md § ipynb](../kernel/subsystems/session.md#为什么不用-ipynb) |
| **费用追踪** | 🟡 部分完成 | `/cost` 已通过 `_mustang.agent/session/get_usage` 显示会话 token/context/history/memory/environment；仍缺可信 pricing table 和按模型金额分解 |
| **前端体验** | ❌ 缺失 | 无状态栏、spinner、diff 渲染、自动折叠 |
| **IDE 集成** | ⛔ 不在范围 | VS Code / JetBrains 插件 |
| **MCP Resources** | ❌ 缺失 | CC 新增 ListMcpResourcesTool + ReadMcpResourceTool，但两个工具在 CC 中对 async agent 仍标注 TBD；暂不做 |
| **Task CRUD 工具** | ❌ 缺失 | CC 新增 TaskCreate/TaskGet/TaskList/TaskUpdate，用于 in-process 多 agent 团队协作（agent 互相创建/分配任务）；我们现有 TodoWriteTool 覆盖单 agent 待办列表，gap 在于 multi-agent 场景 |
| **多 agent 团队 (swarms)** | ❌ 缺失 | CC 新增 TeamCreate/TeamDelete，配合 Task CRUD 实现 agent swarm 协调。AGENT_SWARMS feature gate，目前属于实验性 |
| **SleepTool** | ❌ 缺失 | 在 async/background 执行中等待而不占用 shell 进程。CC 已有；我们 async 任务通过 MonitorTool 轮询，未来再评估 |
| **BriefTool** | ⛔ 低优先级 | CC KAIROS feature gate，用于主动异步向用户推消息（含文件附件）。属于 proactive agent 模式，不在当前内核范围 |
| **SyntheticOutputTool** | ⛔ 低优先级 | CC coordinator 模式下 agent 向协调者返回结构化输出。属于 multi-agent swarm 配套，暂不做 |
| **WorkflowTool** | ⛔ 低优先级 | CC WORKFLOW_SCRIPTS feature gate，工作流脚本执行。属于实验性功能 |
| **task_budget API 参数** | ❌ 缺失 | CC beta `task-budgets-2026-03-13`，跨压缩边界追踪 output token 预算。我们 token_budget 是调用方参数，无跨压缩持久化 |
| **Proactive / KAIROS 模式** | ⛔ 不在范围 | CC 的终端焦点感知自主模式（unfocused → 高度自主，focused → 协作）。属于 CC 产品功能，不在 DeepCLI 内核范围 |

---

## DeepCLI vs Claude Code 对比

### 一、Prompt 系统

| 方面 | DeepCLI | Claude Code |
|------|---------|------------|
| **构建方式** | `PromptBuilder` + `PromptSection` cache 标志 | 注册表设计，每个部分有名称+计算函数 |
| **内容量** | 分段 system prompt + env context + skills listing | 350+ 行，极其详细 |
| **注入内容** | identity/system/doing tasks/actions/tools/tone/output 等分段 prompt、环境 (date/platform/cwd/git branch)、skills 列表 | 工具使用指导、代码质量、Git 规范、模型信息、memory 教学等 |
| **缓存策略** | section 级 cache 标志 | Anthropic API cache_control，最多 4 个断点 |
| **Memory 注入** | Channel A (index 常驻 cacheable) + Channel B (per-turn fence 注入) + Channel C (策略规则) | 200+ 行 auto memory 指导 + MEMORY.md 常驻 + per-turn 相关文件注入 |
| **Git 上下文** | ✅ branch + main_branch + user + status + log | status + diff + recent commits |
| **Plan mode 指令** | ✅ 5 阶段工作流 + full/sparse 节流 | 完整指令集 |

### 二、工具系统

> **数据来源**：下表基于 CC 实际注入到 LLM system prompt 的工具列表（core tools
> + deferred tools），而非源码中的 class 名。只有出现在 `tools` 参数或
> deferred listing 中的才算"暴露给 LLM 的工具"。
>
> 验证方法：在 CC 会话中观察 system prompt 中的 tool schema 和
> `<system-reminder>` 里的 deferred tool 列表。

**概览**: DeepCLI 27 builtin (+ 5 Memory + MCP) / Claude Code ~26 常规 builtin — 覆盖率 ~100%（不含 CC 新增实验性工具：Task CRUD/Team/Sleep/Brief/SyntheticOutput）

#### CC 暴露给 LLM 的工具（已确认）

**Core tools**（schema 直接加载，LLM 立即可调用）：

| CC 工具名 | DeepCLI | 状态 |
|---|---|---|
| Agent | ✅ AgentTool | 对齐（含 name, transcript resume） |
| Bash | ✅ BashTool | 对齐 |
| Edit | ✅ FileEditTool | 对齐 |
| Glob | ✅ GlobTool | 对齐 |
| Grep | ✅ GrepTool | 对齐 |
| Read | ✅ FileReadTool | 对齐（含图片+PDF） |
| ScheduleWakeup | ✅ CronCreateTool (one-shot) | /loop 动态定时唤醒 — CronCreate `delay` 格式等价 |
| Skill | ✅ SkillTool | 对齐 |
| ToolSearch | ✅ ToolSearchTool | 对齐 |
| Write | ✅ FileWriteTool | 对齐 |

**Deferred tools**（仅暴露名字，LLM 通过 ToolSearch 加载 schema 后调用）：

| CC 工具名 | DeepCLI | 状态 |
|---|---|---|
| AskUserQuestion | ✅ AskUserQuestionTool | 对齐 |
| CronCreate | ✅ CronCreateTool | 定时触发器创建 |
| CronDelete | ✅ CronDeleteTool | 定时触发器删除 |
| CronList | ✅ CronListTool | 定时触发器列表 |
| EnterPlanMode | ✅ EnterPlanModeTool | 对齐 |
| EnterWorktree | ✅ EnterWorktreeTool | git worktree 隔离（含 sparse checkout） |
| ExitPlanMode | ✅ ExitPlanModeTool | 对齐 |
| ExitWorktree | ✅ ExitWorktreeTool | git worktree 退出（keep/remove） |
| Monitor | ✅ MonitorTool | 后台进程事件流监控 |
| NotebookEdit | ⛔ 不在范围 | `.ipynb` 编辑 — 主动不做（见 coverage 顶部说明） |
| RemoteTrigger | ⛔ 不需要 | DeepCLI 的 CronExecutor 已覆盖等价功能 |
| TaskOutput | ✅ TaskOutputTool | 对齐 |
| TaskStop | ✅ TaskStopTool | 对齐 |
| TodoWrite | ✅ TodoWriteTool | 对齐 |
| WebFetch | ✅ WebFetchTool | 对齐 |
| WebSearch | ✅ WebSearchTool | 对齐 |

**动态注册的 MCP 工具**（McpAuthTool 实例，按连接的 MCP server 生成）：

| CC 工具名（示例） | DeepCLI | 状态 |
|---|---|---|
| mcp__\*__authenticate | ✅ McpAuthTool | 对齐 — 返回 auth URL，后台完成 OAuth flow |

注：CC 只有 `authenticate` 一个工具，没有 `complete_authentication`。
OAuth 完成（callback → token exchange → reconnect）在后台自动进行。

**环境条件工具**（按 OS/配置有条件加载）：

| CC 工具名 | DeepCLI | 状态 |
|---|---|---|
| PowerShell | ✅ PowerShellTool | 对齐（Windows only） |

**CC 新增实验性工具**（2026-04 后，feature-gated 或 in-process only）：

| CC 工具名 | 状态 | 说明 |
|---|---|---|
| TaskCreate / TaskGet / TaskList / TaskUpdate | ❌ 暂无 | in-process teammate 用，multi-agent task 分配。CC 的 AGENT_SWARMS feature gate |
| TeamCreate / TeamDelete | ❌ 暂无 | multi-agent 团队管理。AGENT_SWARMS gate |
| Sleep | ❌ 暂无 | async 等待，不占 shell 进程。CC 主要用于 KAIROS/proactive 模式 |
| Brief | ⛔ 不在范围 | KAIROS/proactive 主动消息推送，CC 产品功能 |
| SyntheticOutput | ⛔ 不在范围 | coordinator agent 输出，multi-agent swarm 配套 |
| ListMcpResources / ReadMcpResource | ❌ 暂不做 | MCP resource 访问，CC 中对 async agent 仍标注 TBD |
| WorkflowTool | ⛔ 不在范围 | WORKFLOW_SCRIPTS gate，工作流脚本 |

**DeepCLI 独有**：

| 工具 | 说明 |
|---|---|
| Memory 5 工具 | write/append/delete/list/search |
| SendMessageTool | 跨 sub-agent 通信（CC 集成在 Agent tool 的 `to` 参数中） |
| ReplTool | 批量执行（CC 无独立 REPL tool，走 Bash） |

**主动不做**：
- **编辑器**: NotebookEdit（`.ipynb` 不在范围，Python 源码走 FileRead/Edit）

### 三、引擎/编排器

| 方面 | DeepCLI | Claude Code | 状态 |
|---|---|---|---|
| **压缩: snip** | ✅ 截断旧 tool_result | ✅ | 对齐 |
| **压缩: micro-compact** | ✅ 移除 read-only 轮次 | ✅ | 对齐 |
| **压缩: LLM 摘要** | ✅ 3 层级联 | ✅ | 对齐 |
| **压缩: context collapse** | ❌ 未实现 | ❌ CC 仅预埋 interface，核心代码未 ship（`services/contextCollapse/` 目录不存在） | 双方均未实现 |
| **反应性压缩** | ✅ PromptTooLongError → 重试 | ✅ | 对齐 |
| **max_output_tokens 恢复** | ✅ 8k→64k 升级 ×3 | ✅ | 对齐 |
| **最大轮次** | ✅ 调用者控制，0=无限制 | 调用者动态控制 | 对齐 |
| **工具并发** | DAG 式 ExecutionSlot | 简单批分区 | DeepCLI 更灵活 |
| **task_budget 跨压缩追踪** | ❌ 无 | ✅ beta `task-budgets-2026-03-13` | CC 新增，output token 预算跨 compaction 边界持久化 |
| **Prompt section 排序** | ✅ cacheable 先、volatile 后、env 最后 | cacheable first, env last | 已对齐（2026-04-25 修正） |

### 四、权限/配置/扩展

| 方面 | DeepCLI | Claude Code | 状态 |
|---|---|---|---|
| **权限模式** | ✅ 6 种 (default/plan/bypass/accept_edits/auto/dont_ask) + 动态 per-invocation | 6 种 (含 dontAsk) | ✅ 完全对齐 |
| **权限规则来源** | 4 层 (user/project/local/flag) + SessionGrantCache | 5 层 | 接近对齐 |
| **配置分层** | 2 层 (global + project) | 5 层 | 缺 local/flag/policy 层 |
| **Hook 事件** | 16 种 | 8+ 种 | DeepCLI 超越 |
| **MCP 认证** | ✅ OAuth 2.1 + PKCE + McpAuthTool | 完整 OAuth | 对齐 |
| **MCP 传输** | 4 种 (stdio/SSE/WS/HTTP) | 3 种 | DeepCLI 超越 |

---

## 总结

**功能完成度约 ~96%**（核心引擎）。核心引擎完备：对话循环、工具执行、权限、3 层压缩、hooks、MCP + OAuth、session 持久化、多 provider、Memory、AgentTool、TaskManager、ToolSearch、PlanMode、Web 工具、AskUserQuestion、REPL、ScheduleManager、SecretManager、GitManager、PromptBuilder 缓存排序修正。

工具覆盖：CC 实际暴露给 LLM 约 26 个常规 builtin tool（10 core + 16 deferred），另有约 10 个 feature-gated/实验性新工具（Task CRUD × 4、Team × 2、Sleep、Brief、SyntheticOutput、WorkflowTool）。DeepCLI 28 个 builtin（21 BUILTIN_TOOLS + ToolSearch + REPL + 5 Memory），覆盖率 ~100%（常规工具）。

**剩余差距**：
1. **费用追踪金额分解** — `/cost` 已有用量面板，但仍缺可信 pricing table、按模型金额聚合和历史费用视图
2. **前端体验** — 无专用客户端
3. **Multi-agent swarms** — Task CRUD + TeamCreate 属于 CC 新增的实验性协调层，我们有 SendMessageTool 但无结构化 task 分配
4. **task_budget 跨压缩追踪** — CC beta 功能，output token 预算在 compaction 后持续累计

注：**Prompt 内容差距已消除**。经逐文件核查，我们的 prompt 文件（doing_tasks、using_tools、system、actions_with_care、tone_and_style、output_efficiency、git_commit_pr、session_guidance/ 6 个文件）与 CC 外部用户路径完全对齐。原文档标注的"缺少工具使用指导、代码质量规范"对应 2026-04-06 的旧状态，已实现。CC 里看起来更多的内容（assertiveness/verify/faithful-reporting/comment 细则）全部是 `USER_TYPE === 'ant'` 内部 gate，外部用户也收不到。

**主动不做**：
- **NotebookEdit** — Jupyter `.ipynb` 编辑不在范围

**架构优势**（相比 Claude Code）：
- kernel/client 分离架构（ACP 协议）
- 4 种 MCP 传输（CC 3 种）
- 16 种 hook 事件（CC 8+）
- DAG 式工具并发
- Gateway 系统（Discord 等多入口）
- SecretManager 统一凭证管理（CC 用分散的 OS keychain + JSON）
- **Memory 系统超越 CC**：结构化 scoring + BM25 pre-filter + 分类目录树 + 时间衰减 + 后台幻觉过滤 + 独立 memory tools（CC 依赖 file_write，无独立工具和安全边界）
