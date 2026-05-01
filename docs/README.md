# DeepCLI Docs Index

Single navigation hub for all project documentation.  Agents should
consult this file first, then go directly to the file(s) they need.

> **Active codebase**: `src/kernel/` only.
> `archive/daemon/` is **read-only reference code** — no new development,
> no bug fixes, no modifications.
> All new work targets `src/kernel/`.  See [`archive/README.md`](../archive/README.md).

> **Bootstrap 服务 vs Subsystem**：FlagManager / ConfigManager 是
> bootstrap 服务（启动失败即 abort kernel，不继承 `Subsystem`）；
> 其余管理器都继承 `Subsystem` ABC，支持"失败降级继续"。详见
> [`kernel/architecture.md`](kernel/architecture.md#生命周期)。

> **Frontend / CLI 边界（硬规则）**：CLI 和未来所有前端只能通过
> WebSocket 上的 ACP/JSON-RPC 与 kernel 通信。不得 import `src/kernel`
> 内部模块，不得直接读写 kernel SQLite / state / sidecar 文件，不得绕过
> ACP 调用 kernel 子系统。需要的新能力必须先在 kernel 暴露为 ACP 方法或
> notification，再由 CLI 消费。

---

## Kernel Design Docs

代码在 [`../src/kernel/kernel/`](../src/kernel/kernel/)，实装按文档走。

### 核心

| 文档 | 内容 |
|---|---|
| [kernel/overview.md](kernel/overview.md) | 项目目标、设计原则、技术栈 |
| [kernel/architecture.md](kernel/architecture.md) | 子系统清单、WebSocket 三层分工（transport / protocol / session）、ACP 采用情况、lifespan 启动顺序、失败处理策略、Subsystem ABC |
| [kernel/history.md](kernel/history.md) | 已落地 kernel milestone 摘要；从 `plans/progress.md` 拆出的长期历史记录 |
### 子系统

每个子系统都有独立的设计文档，包含接口定义、内部架构、设计决策和实装细节。

| 文档 | 内容 |
|---|---|
| [flags.md](kernel/subsystems/flags.md) | FlagManager —— 启动期开关（FuseBox），运行期不可变 |
| [config.md](kernel/subsystems/config.md) | ConfigManager —— 运行期可变业务配置 + Signal/Slot + 拥有权模型 |
| [secrets.md](kernel/subsystems/secrets.md) | SecretManager —— SQLite 凭证存储 + `${secret:name}` config 展开 + MCP OAuth |
| [connection_authenticator.md](kernel/subsystems/connection_authenticator.md) | ConnectionAuthenticator —— 连接接入认证（AuthN） |
| [tool_authorizer.md](kernel/subsystems/tool_authorizer.md) | ToolAuthorizer —— 工具调用授权（AuthZ），4 层 Rule + session grant + Bash 分类器 |
| [llm_provider.md](kernel/subsystems/llm_provider.md) | LLMProviderManager —— Provider 实例生命周期管理 |
| [llm.md](kernel/subsystems/llm.md) | LLMManager —— model 配置管理、alias 解析、路由、`current_used` 角色表 |
| [mcp.md](kernel/subsystems/mcp.md) | MCPManager —— 4 种 transport、连接生命周期、health monitor、MCPAdapter |
| [tools.md](kernel/subsystems/tools.md) | ToolManager —— Tool ABC、core/deferred 两层 registry、28+ 内置工具 |
| [skills.md](kernel/subsystems/skills.md) | SkillManager —— SKILL.md frontmatter 技能发现、SkillTool、bundled skills |
| [hooks.md](kernel/subsystems/hooks.md) | HookManager —— 16 事件枚举、fire-sites、system-reminder drain |
| [memory/design.md](kernel/subsystems/memory/design.md) | MemoryManager —— 4 分类目录树 + BM25+LLM scoring + 后台 agent + 双通道注入 |
| [prompts.md](kernel/subsystems/prompts.md) | PromptManager —— bootstrap 服务、.txt prompt 文件加载 |
| [session.md](kernel/subsystems/session.md) | SessionManager —— SQLite 持久化、`SessionHandler`、串行 turn 处理、多连接广播 |
| [orchestrator.md](kernel/subsystems/orchestrator.md) | Orchestrator —— LLM ↔ tool 循环、history、compaction、plan mode、`ToolExecutor` 7-step flow |
| [compaction.md](kernel/subsystems/compaction.md) | Compactor —— snip / microcompact / LLM 摘要（1a–1c），1d context collapse deferred |
| [tasks.md](kernel/subsystems/tasks.md) | TaskManager —— AgentTool + TodoWrite + TaskOutput/TaskStop + 后台任务通知 |
| [commands.md](kernel/subsystems/commands.md) | CommandManager —— 命令目录（`CommandDef` + `CommandRegistry`），纯 catalog 无 dispatch |
| [gateways.md](kernel/subsystems/gateways.md) | GatewayManager —— `GatewayAdapter` ABC + `DiscordAdapter` |
| [schedule.md](kernel/subsystems/schedule.md) | ScheduleManager —— CronStore + CronScheduler + CronExecutor + DeliveryRouter |
| [git.md](kernel/subsystems/git.md) | GitManager —— git context injection + EnterWorktree/ExitWorktree 工具 |
| [transport.md](kernel/subsystems/transport.md) | Transport 层 —— WS `/session` accept/auth/loop、ProtocolStack 抽象 |

所有注册的 Subsystem 均有完整的 `startup`/`shutdown` 实现，无骨架残留。

Protocol 层：两套 stack 都已在 `kernel.routes.stack.create_stack`
注册——`dummy`（identity pass-through，默认值，仅用于验证 transport 循环）
和 `acp`（`kernel.protocol.build_protocol_stack`，生产路径）——由
`flags.yaml` 的 `[transport] stack` 选择。

### 接口层

| 文档 | 内容 |
|---|---|
| [kernel/interfaces/protocol.md](kernel/interfaces/protocol.md) | Protocol 层 —— ACP 采纳 profile、multi-target dispatch、事件映射、`_meta` 扩展 |

## CLI Docs

代码在 [`../src/cli/`](../src/cli/)。CLI 是 thin ACP/WebSocket client；
所有 agent runtime、model、tools、memory、session truth 都留在 kernel。

| 文档 | 内容 |
|---|---|
| [cli/README.md](cli/README.md) | CLI 文档索引、当前状态、工作分类 |
| [cli/design.md](cli/design.md) | CLI 客户端设计文档：ACP 边界、TUI active-port、运行时约束 |
| [cli/history/implemented-summary.md](cli/history/implemented-summary.md) | 已落地 CLI milestone 摘要 |
| [cli/history/](cli/history/) | 已实现/历史 CLI 记录，保留用于追溯实现决策 |

### ACP / ACPX 规范镜像

[kernel/references/acp/](kernel/references/acp/) —— ACP 官方规范本地快照
（`protocol/*.md` + `rfds/*.md`）。所有关于 ACP 线上格式 / 方法语义 /
枚举值的真相都在这里，kernel 设计文档不应重复 ACP 的定义，而应链接过去。

[kernel/references/acpx/](kernel/references/acpx/) —— OpenClaw ACPX 本地快照。
ACPX 提供 ACP 上层的 session / queue / status / identity / flow 语义参考；
DeepCLI 会直接实现需要的控制平面能力，不依赖 `acpx` CLI/runtime，也不要把它
当成 ACP 的替代协议。

---

## Reference Docs

| Purpose | File | When to read |
|---|---|---|
| **Design decisions** | [`reference/decisions.md`](reference/decisions.md) | Before reinterpreting invariants |
| **Reference patterns** | [`reference/references.md`](reference/references.md) | Planning new features |
| **Claude Code comparison** | [`reference/claude-code-comparison.md`](reference/claude-code-comparison.md) | Gap analysis |
| **Claude Code coverage** | [`reference/claude-code-coverage.md`](reference/claude-code-coverage.md) | Coverage assessment |
| **Claude Code query loop** | [`reference/claude-code-query-loop.md`](reference/claude-code-query-loop.md) | Per-turn `queryLoop` overview |
| **Claude Code query loop (detailed)** | [`reference/claude-code-query-loop-walkthrough.md`](reference/claude-code-query-loop-walkthrough.md) | Step-by-step walkthrough |
| **System-prompt** | [`reference/prompts.md`](reference/prompts.md) | Touching prompt assembly |

---

## Process & Workflow

| Purpose | File | When to read |
|---|---|---|
| **Agent bootstrap** | `../AGENTS.md` | Session start |
| **Definition of Done** ⚠️ | [`workflow/definition-of-done.md`](workflow/definition-of-done.md) | **Before claiming any implementation complete** |
| **Entry-file policy** | `entry-files-policy.md` | Before editing any entry file |
| **Dev env + deploy** | `setup.md` | Fresh machine, or when adding deps |
| **Gotchas + lessons** | `lessons-learned.md` | Before hitting the same bug twice |
| **Dev workflow** | `workflow/workflow.md` | Before every implementation step (6 phases + Phase 4.5 closure-seam inventory) |
| **Post-impl checklist** | `workflow/code-quality.md` | After writing any code |
| **Full-repo audit** | `workflow/code-review.md` | When user says "Code Review" |
| **Future phases** | `plans/roadmap.md` | Planning what's next |
| **ACP/ACPX alignment** | [`plans/acp-acpx-schema-alignment-plan.md`](plans/acp-acpx-schema-alignment-plan.md) | Before changing protocol schemas, custom methods, or ACPX-compatible control semantics |
| **Agent Control Plane** | [`plans/agent-control-plane.md`](plans/agent-control-plane.md) | CLI/Probe/Home Screen -> Kernel and Kernel -> child Kernel / Session Agent / external ACP agent control symmetry |
| **Full-system test plan** | [`plans/full-system-test-plan.md`](plans/full-system-test-plan.md) | 覆盖单元测试、闭合缝、真实 Supervisor E2E、CLI/PTY、Probe 验收矩阵 |
| **Kernel unit-test Phase 1** | [`plans/kernel-unit-test-phase1.md`](plans/kernel-unit-test-phase1.md) | Kernel 每个组件的单元测试补齐任务、优先级、验收命令 |
| **CLI pending work** | [`plans/cli-plan.md`](plans/cli-plan.md) | CLI future work, reconnect, audits |
| **CLI docs** | [`cli/README.md`](cli/README.md) | CLI implemented design facts and history |
| **Kernel completed plans archive** | [`kernel/history/plans/`](kernel/history/plans/) | 已完成的 Kernel 计划归档，例如 Session ACP、Session module split、Orchestrator split、prompt alignment |
| **CLI completed plans archive** | [`cli/history/plans/`](cli/history/plans/) | 已完成的 CLI 计划/审计归档，例如 active-port prune |
| **Backlog** | `plans/backlog.md` | Deferred features from design docs |
| **Current progress** | `plans/progress.md` | Short current status + recent log; detailed history lives in `kernel/history.md` and `cli/history/implemented-summary.md` |

> ⚠️ **"Done" means five gates green, not four.**  Unit tests passing
> is necessary but not sufficient.  Every closure seam (callable wired
> across subsystem boundaries) needs a probe against the **real**
> subsystem — mocks cannot catch protocol / payload / arg-arity bugs.
> Violated three times; read `workflow/definition-of-done.md` before
> making it four.

---

## How to Use

- **New chat?** Read `AGENTS.md` → `plans/progress.md` → confirm with user.
- **Implementing a subsystem?** Read its `kernel/subsystems/<name>.md` first.
- **Starting a new feature?** Check `plans/backlog.md` or `plans/roadmap.md`.
- **Finishing a subsystem?** Work the five gates in
  [`workflow/definition-of-done.md`](workflow/definition-of-done.md)
  before reporting complete.
- **Hit a bug?** Check `lessons-learned.md` first.
