# Codex CLI vs DeepCLI Kernel 对比

> 调研对象：Codex CLI 本地源码 `/home/saki/Documents/alex/codex`，
> 重点看 `codex-rs/`；DeepCLI 当前实现看 `src/kernel/kernel/` 和
> `docs/kernel/subsystems/`。
>
> 结论先行：Codex CLI 不是 DeepCLI Kernel 的直接替代品。它是一个
> Rust-first agent harness + TUI/app-server/exec-server 产品栈；DeepCLI
> Kernel 是 Python runtime，核心价值在 supervised Agent Control Plane、
> ACP 边界、可替换 subsystem、PDS 长期软件库。两者重叠在 agent loop、
> tools、skills、memory、prompts；Codex 独有或明显更强的区域主要是
> execpolicy/sandbox/app-server/plugin/cloud-task/observability。

## 源码位置

Codex CLI 的路径由 `.mustang-refs.yaml` 记录：

```text
codex: /home/saki/Documents/alex/codex
```

本次对比主要读了这些 Codex 区域：

| 领域 | Codex 路径 |
|---|---|
| Agent loop / session | `codex-rs/core/src/session/`, `core/src/client.rs`, `core/src/codex_thread.rs` |
| Tools | `codex-rs/core/src/tools/`, `codex-rs/tools/`, `codex-rs/apply-patch/`, `codex-rs/execpolicy/` |
| Skills | `codex-rs/core-skills/`, `core/src/skills.rs`, `core/src/skills_watcher.rs` |
| Memory | `codex-rs/memories/`, `core/src/memories/`, `thread-store/`, `state/` |
| Prompts | `codex-rs/core/*.md`, `core/templates/`, `core/src/context/` |
| App / server | `app-server/`, `app-server-protocol/`, `exec-server/`, `mcp-server/` |
| Product subsystems | `core-plugins/`, `cloud-tasks/`, `collaboration-mode-templates/`, `agent-graph-store/` |

DeepCLI 对照区域：

| 领域 | DeepCLI 路径 |
|---|---|
| Runtime topology | `src/kernel/kernel/supervisor/`, `agent_hub/`, `access_agent/`, `agent_runtime/` |
| Tools | `src/kernel/kernel/tools/`, `tool_authz/`, `orchestrator/tool_executor.py` |
| Skills | `src/kernel/kernel/skills/`, `tools/builtin/skill_tool.py` |
| Memory | `src/kernel/kernel/memory/` |
| Prompts | `src/kernel/kernel/prompts/`, `orchestrator/prompt_builder.py` |
| Protocol | `src/kernel/kernel/protocol/`, `docs/kernel/interfaces/protocol.md` |

## 总体差异

| 维度 | Codex CLI | DeepCLI Kernel |
|---|---|---|
| 语言和进程模型 | Rust-first，多 crate；TUI、app-server、exec-server、mcp-server 都在同一产品树 | Python Kernel；Supervisor 启动 Hub / Access / Primary Runtime；CLI 是 thin ACP client |
| Wire boundary | Codex 自有 app-server protocol + exec-server + MCP server | ACP/JSON-RPC over WebSocket；DeepCLI 扩展走 `_mustang.agent/*` |
| Agent 形态 | Root session + multi-agent tools + external agent sessions；更接近同产品内多 harness | 当前单 durable `primary`，Hub 为未来 peer Session Agents 预留；`AgentTool` 仍是父 runtime 内部工具 |
| 配置 | `config` crate 管 `config.toml`、profiles、permissions、skills、MCP、plugins 等 | `ConfigManager` + typed section ownership；flags/config/secrets 拆开 |
| 安全 | execpolicy DSL、sandboxing crate、Linux/macOS/Windows sandbox、network guardian | ToolAuthorizer rules + Bash classifier + session grants；尚无同等级 OS sandbox/execpolicy DSL |
| 记录与调试 | rollout/thread-store/state DB/response-debug-context/otel/analytics | SQLite session store + usage snapshot + probes；benchmarking 方向明确但记录粒度不如 Codex 全 |

## 1. Tools 差异

### Codex 的 Tools

Codex 的工具系统由两层组成：

1. `codex_tools::ToolSpec` 描述暴露给模型的 schema。
2. `core/src/tools/registry.rs` 的 `ToolHandler` 负责实际执行、hook payload、
   streamed argument diff、mutating 判断和 response item 转换。

核心特征：

- 工具 schema 和 handler 分离；`spec_plan.rs` 按 `ToolsConfig` 动态组装工具面。
- 支持多种 shell surface：`shell`、`local_shell`、`container.exec`、
  `shell_command`、`exec_command`、`write_stdin`。
- `apply_patch` 是一等工具，既有 freeform 也有 JSON 形态，配合独立
  `codex-rs/apply-patch/` parser/runtime。
- `request_permissions` 是模型可调用工具，用来请求额外 filesystem/network
  权限；这和 Codex sandbox 权限模型是一体的。
- `ToolSearch` 同时能发现 deferred MCP、dynamic tools、discoverable tools。
- multi-agent 工具有 v1/v2：`spawn_agent`、`send_input/send_message`、
  `wait_agent`、`close_agent`、`resume_agent`、`list_agents`、
  `followup_task` 等。
- 有 `code_mode` 工具面：把嵌套工具整理成代码模式执行/等待工具。
- 有产品/测试型工具：`goal` tools、`test_sync_tool`、`request_plugin_install`、
  `view_image`。
- MCP resources 是内建工具面的一部分：list/read resource/templates。
- Tool execution 直接嵌入 pre/post hooks、otel/analytics、tool dispatch trace、
  output truncation、sandbox policy tag。

### DeepCLI 的 Tools

DeepCLI 的工具系统由 `ToolManager` + `ToolRegistry` + `Tool` ABC 组成：

- `Tool` 本体同时提供 identity、schema、risk 信息、display payload 和
  `call()`。
- `ToolAuthorizer` 独立做授权仲裁：tool 只提供 `default_risk()`、
  `prepare_permission_matcher()`、`is_destructive()` 等领域判断。
- `ToolRegistry` 有 `core/deferred` 两层，snapshot 会按 plan mode、REPL mode、
  sub-agent whitelist、denied tools 过滤。
- 内置工具包括文件、搜索、shell、web、skill、task、todo、MCP resources、
  schedule、worktree、restart、REPL 等。
- REPL 是 DeepCLI 独有的高层工具面：隐藏 primitive tools，让模型用
  Python worker 中的 helper 调用 `Read/Bash/Grep/...`。
- MCP tool 通过 `MCPAdapter` 注册到 ToolManager；auth server 会生成
  `McpAuthTool`。
- 工具描述文本已集中到 PromptManager 的 `prompts/default/tools/*.txt`。

### Built-in Tool 清单对照

Codex 的 built-in tool 不是一个固定列表，而是由
`core/src/tools/spec_plan.rs` 根据 `ToolsConfig` 条件组装；DeepCLI 的
基础 built-in 列表来自 `src/kernel/kernel/tools/builtin/__init__.py`，
再由 feature flags、MCP、MemoryManager、REPL 等动态补充。

| 能力 | Codex built-in / 条件工具 | DeepCLI built-in / 条件工具 | 差异判断 |
|---|---|---|---|
| Shell / command | `shell`、`local_shell`、`container.exec`、`shell_command`、`exec_command`、`write_stdin` | 平台选择 `Bash` / `PowerShell` / `Cmd`；另有 `Python`、`REPL` | Codex shell 面更细，有 stdin continuation 和环境/sandbox 绑定；DeepCLI 有 Python/REPL 高层入口 |
| 文件读写搜索 | 主要通过 shell/apply_patch/MCP/hosted surface；没有 DeepCLI 同名 `Read/Edit/Write/Glob/Grep` built-in 组合 | `Read`、`Edit`、`Write`、`Glob`、`Grep` | DeepCLI 更像 Claude Code 的文件工具面；Codex 更偏 shell + patch + sandbox |
| Patch | `apply_patch` freeform 或 function，两种 schema | 无独立 `apply_patch` tool；用 `Edit` / `Write` | Codex 明显更强，值得引入 |
| Plan | `update_plan` | `TodoWrite`、`EnterPlanMode`、`ExitPlanMode` | Codex 是工具化计划更新；DeepCLI 把 todo 和 plan mode 分开 |
| User input | `request_user_input` | `AskUserQuestion` | 语义接近；Codex 的 schema 更强约束 Plan mode 可用性 |
| Permission request | `request_permissions` | 无同名 tool；走 ToolAuthorizer permission ask / session grants | Codex 能让模型主动请求额外 sandbox 权限；DeepCLI 当前授权由 executor 触发 |
| Tool discovery | `tool_search`，查 deferred MCP/dynamic/discoverable tools | `ToolSearch`，查 deferred registry | 同类能力；Codex source 类型更多 |
| Plugin install | `request_plugin_install` | 无 | Codex 独有，关联 plugin marketplace |
| MCP resources | `list_mcp_resources`、`list_mcp_resource_templates`、`read_mcp_resource`；MCP tools 动态 namespaced | `ListMcpResources`、`ReadMcpResource`；MCP tools 动态 `mcp__*`；另有 `McpAuthTool` 条件注册 | DeepCLI 缺 resource templates tool；DeepCLI 多 MCP auth pseudo-tool |
| Web / hosted | `web_search` hosted tool；`image_generation` hosted tool；`view_image` | `WebSearch`、`WebFetch` | Codex 有 image/view-image hosted surface；DeepCLI 有 explicit WebFetch |
| Image | `image_generation`、`view_image` | 无内建 image tool | Codex 更完整 |
| Multi-agent | v1: `spawn_agent`、`send_input`、`resume_agent`、`wait_agent`、`close_agent`；v2: `spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`close_agent`、`list_agents` | `Agent`、`SendMessage`、`TaskOutput`、`TaskStop` | Codex agent lifecycle 工具更完整；DeepCLI 当前更接近父 session 内部 task/sub-agent |
| Agent jobs | `spawn_agents_on_csv`、worker-only `report_agent_job_result` | 无 | Codex 独有，适合批处理 agent work |
| Goals | `get_goal`、`create_goal`、`update_goal` | 无 | Codex 独有，和 budget/goal runtime 绑定 |
| Code mode | `exec`、`wait` | `REPL` | 两者都是“高层执行面”；Codex 偏 JS/code cell，DeepCLI 偏 Python worker + tool helpers |
| Memory | memory read/write pipeline 不是普通 model tool；read path 可经 memory MCP surface | `memory_write`、`memory_append`、`memory_delete`、`memory_list`、`memory_search` 条件注册 | DeepCLI 把 memory 管理显式暴露成 tools；Codex 主要后台化/系统化 |
| Skills | structured skill mention + injection；不是普通 `Skill` tool | `Skill` | DeepCLI 有显式 Skill tool；Codex 更偏 input mention/injection |
| Schedule | 无同类内建 tool | `CronCreate`、`CronDelete`、`CronList` | DeepCLI 独有 |
| Worktree/session control | 无同名 built-in | `EnterWorktree`、`ExitWorktree`、`RestartSelf`、`Monitor` | DeepCLI 独有或更 kernel 运维化 |
| Test / sync | `test_sync_tool` 实验工具 | 无 | Codex 测试/实验用 |

按名称看，DeepCLI 当前基础 built-in 是：

```text
Bash/PowerShell/Cmd, AskUserQuestion, EnterPlanMode, ExitPlanMode,
Edit, Read, Write, Glob, Grep, ListMcpResources, ReadMcpResource,
Monitor, Python, RestartSelf, Skill, Agent, SendMessage, TaskOutput,
TaskStop, TodoWrite, CronCreate, CronDelete, CronList, WebFetch,
WebSearch
```

条件注册：

```text
ToolSearch, REPL, MCP dynamic tools, McpAuthTool,
memory_write, memory_append, memory_delete, memory_list, memory_search
```

Codex 当前从 `spec_plan.rs` 可见的 built-in / 条件工具全集大致是：

```text
shell, local_shell, container.exec, shell_command, exec_command,
write_stdin, apply_patch, update_plan, request_user_input,
request_permissions, tool_search, request_plugin_install,
list_mcp_resources, list_mcp_resource_templates, read_mcp_resource,
web_search, image_generation, view_image,
spawn_agent, send_input, send_message, followup_task, resume_agent,
wait_agent, close_agent, list_agents,
get_goal, create_goal, update_goal,
exec, wait,
spawn_agents_on_csv, report_agent_job_result, test_sync_tool,
dynamic MCP tools, dynamic tools, unavailable-tool placeholders
```

### 核心差距

| 主题 | Codex 更强 | DeepCLI 更强 / 更清晰 |
|---|---|---|
| 权限和 sandbox | execpolicy DSL + OS sandbox + request_permissions tool | ToolAuthorizer 子系统边界清楚，rule/session grant 更 Kernel 化 |
| Patch editing | 独立 parser/runtime/freeform tool，安全性高 | 目前主要是 FileEdit/FileWrite；没有 Codex 级 apply_patch 语法工具 |
| Shell execution | unified exec、PTY-ish process management、stdin continuation、sandbox permission request | Bash/PowerShell/Cmd + REPL helper，接口更简单 |
| Tool discoverability | deferred/dynamic/discoverable/MCP/plugin search 更完整 | `ToolSearchTool` 已有，但产品生态较小 |
| Display contract | response item + TUI/app server 深度绑定 | `ToolDisplayPayload` 把 LLM content 和 UI display 分离，更适合多前端 |
| Agent tools | 多 agent lifecycle 工具更完整 | 未来 Hub peer agents 的架构边界更明确，但尚未完全产品化 |

**判断**：DeepCLI 不应该照搬 Codex 的工具注册方式；现有 `Tool` ABC +
`ToolAuthorizer` 边界更适合 Python Kernel。但 `apply_patch`、execpolicy
DSL、sandbox permission request、unified exec continuation 是值得直接借鉴的
缺口。

## 2. Skill 系统差异

### Codex 的 Skills

Codex 的 skill 系统主要在 `codex-rs/core-skills/`：

- `SkillsManager` 按 cwd/config/plugin roots 加载 skills，并按 config cache。
- 支持 `SkillScope`：system/user/project/plugin 等来源。
- bundled system skills 会安装到 `codex_home/skills/.system`。
- skill metadata 比 Claude Code/DeepCLI 更产品化：`interface` 包含
  display name、short description、icon、brand color、default prompt。
- 有 `dependencies.tools`，能声明 MCP/tool dependency。
- 有 `policy`：implicit invocation、product gating。
- 支持 disabled skill paths、config rules、product restriction。
- 支持 structured skill selection 和文本 `$skill-name` mention；会解析
  `skill://`、`plugin://`、`mcp://`、`app://` 等 path。
- 有 implicit skill indexes：scripts/doc path 能反向映射到 skill，用于隐式触发。
- explicit injection 会读取 SKILL.md 原文，记录 analytics/otel。

### DeepCLI 的 Skills

DeepCLI 的 `SkillManager` 是 Kernel subsystem：

- skill 文件格式是 `skill-name/SKILL.md`，兼容 Claude Code。
- 扫描层级：project `.mustang/skills/`、可选 `.claude/skills/`、external dirs、
  user `~/.deepcli/skills/`、bundled、MCP。
- frontmatter 是 Claude Code 字段 + Hermes 扩展的超集：`setup`、`config`、
  `fallback-for`、`requires.tools/toolsets` 等。
- `SkillTool` 激活 skill；用户 `/skill-name` 通过 Kernel command catalog 和
  `_mustang.agent/session/activate_skill`。
- 支持 user-invocable/model-invocable 分离、dynamic discovery、supporting files
  listing、argument substitution、`${SKILL_DIR}`。
- Skill 是“Markdown 指令”，不是可执行插件；可执行逻辑应走 tools/hooks/MCP。

### 核心差距

| 主题 | Codex 更强 | DeepCLI 更强 / 更清晰 |
|---|---|---|
| 产品集成 | plugin skill roots、UI metadata、icons、brand color、app/plugin mentions | Kernel-owned slash activation，CLI 不扫描文件，router mode 更一致 |
| 隐式触发 | scripts/doc path index 更完整 | `paths`/dynamic discovery 已有，但隐式索引较弱 |
| 配置缓存 | config-key cache 避免同 cwd 不同 session 串味 | ConfigManager section ownership 更统一 |
| Skill 扩展字段 | policy/product/dependencies 偏 Codex 产品生态 | Hermes setup/config/fallback-for 更适合 PDS 自配置 |
| 格式兼容 | Codex 有自己的 `SKILLS.md`/plugin root 模型 | DeepCLI 对 Claude Code `SKILL.md` 兼容更明确 |

**判断**：DeepCLI 的 skill 方向更贴 PDS，因为它把 skill 当用户软件库的一部分。
Codex 值得借鉴的是 `interface` UI 元数据、implicit doc/script indexes、
plugin skill roots 和 structured mention 解析。

## 3. Memory 系统差异

### Codex 的 Memory

Codex memory 不是简单的 “读几个 md 文件”：

- `codex-rs/memories/read` 负责 read path：developer-instruction 注入、
  memory citation parsing、read-usage telemetry。
- `codex-rs/memories/mcp` 把 memory filesystem 暴露成 built-in MCP surface。
- `codex-rs/memories/write` 负责 write path：两阶段后台 pipeline。
- Phase 1 从 state DB 中 claim eligible rollouts，抽取结构化 memory：
  `raw_memory`、`rollout_summary`、`rollout_slug`。
- Phase 2 获取全局 lock，把 stage-1 outputs 同步到 `~/.codex/memories/`
  workspace，生成 `raw_memories.md`、`rollout_summaries/`、workspace diff，
  再启动内部 consolidation agent 修改 `MEMORY.md`、`memory_summary.md`、
  `skills/` 等高层 artifact。
- memory workspace 自带 git baseline，用 diff 判断是否需要 consolidation。
- 有 lease/backoff/watermark/usage_count/last_usage/retention/pruning 等运行机制。

### DeepCLI 的 Memory

DeepCLI `MemoryManager` 当前是透明 Markdown 树：

- 全局 `~/.deepcli/memory/` + 项目 `.mustang/memory/`。
- 四类目录：`profile/semantic/episodic/procedural`。
- 每条 memory 是带 YAML frontmatter 的 `.md`，字段包括 description、
  category、source、created/updated、access_count、locked。
- `MemoryIndex` 缓存 headers 和 index.md。
- `RelevanceSelector` 做 BM25 pre-filter + LLM scoring。
- Orchestrator 双通道注入：Channel A index，Channel B per-turn relevant memories；
  另有 Channel C strategy text。
- memory tools 提供 write/append/read/search/delete 等 LLM-driven 管理能力。
- background agent 存在，但形态较轻，没有 Codex 的 rollout claim +
  state DB 两阶段 pipeline。

### 核心差距

| 主题 | Codex 更强 | DeepCLI 更强 / 更清晰 |
|---|---|---|
| 自动写入 | rollout -> stage1 -> phase2 -> consolidation agent，闭环完整 | background agent 较轻，主要靠工具和 selector |
| 一致性 | DB claim/lease/backoff/global lock/git baseline | Markdown 透明、可人工编辑、git 友好 |
| 检索 | citation/usage telemetry/MCP read surface | BM25 + LLM scoring + category-aware ranking 更直接 |
| 用户可见性 | 高层 artifact + git diff，但机制复杂 | 文件树分类清楚，符合“人能看见的文件”原则 |
| PDS 适配 | 可从 rollouts 自动提炼 reusable memory/skills | 分类语义更贴用户画像/项目知识，但自动 consolidation 弱 |

**判断**：DeepCLI 的 memory 设计原则是对的：透明文件 + 分类 + scoring。
Codex 值得借鉴的是两阶段后台写入、state DB job leasing、workspace diff、
memory citations/usage telemetry、memory MCP read surface。

## 4. Prompts 差异

### Codex 的 Prompts

Codex prompts 分散但类型丰富：

- 模型基础 prompt：`gpt_5_codex_prompt.md`、`gpt_5_2_prompt.md`、
  `gpt-5.2-codex_prompt.md` 等。
- apply-patch prompt：`prompt_with_apply_patch_instructions.md`。
- review prompt：`review_prompt.md` + `templates/review/*.xml`。
- compaction prompt：`core/templates/compact/*`。
- model instruction templates：`templates/model_instructions/*`。
- personalities：`templates/personalities/*`。
- realtime/backend prompt、guardian policy、goals continuation/budget、
  collab experimental prompt 等。
- prompt assembly 使用 `BaseInstructions`、developer/user instructions、
  AGENTS.md resolver、skill injections、memory developer instructions、
  tool specs、sandbox context 等组合。
- 有 `prompt_debug.rs` 可以构造单 turn model-visible input，用于调试 prompt。

### DeepCLI 的 Prompts

DeepCLI 的 `PromptManager` 更集中：

- 启动期扫描 `src/kernel/kernel/prompts/default/**/*.txt`。
- project/user override：`<project>/.mustang/prompts/`、`~/.mustang/prompts/`。
- `PromptBuilder` 明确区分 cacheable sections 和 volatile sections。
- Orchestrator 静态行为、tool descriptions、tool_authz classifier、
  plan mode、MCP instructions、REPL tool surface 都在 `.txt`。
- 工具描述通过 `description_key` 从 PromptManager 读取，减少 Python 硬编码。

### 核心差距

| 主题 | Codex 更强 | DeepCLI 更强 / 更清晰 |
|---|---|---|
| 模型专用 prompt | 多模型、多 personality、review/realtime/guardian/goal/collab 模板 | 当前 prompt 更少，偏 kernel 通用 |
| Prompt 调试 | `prompt_debug.rs` 能输出真实 model input | 主要靠 probe；缺少统一 prompt debug API |
| Prompt 管理 | `include_str!` + templates，按 crate 分散 | PromptManager 集中加载、override、key lookup，D18 更干净 |
| Override | config/model instruction file 支持 | project/user prompt overlay 更直接 |
| Cache 排列 | 有 base instructions 和 prompt input builder | PromptBuilder 明确 stable prefix 和 volatile tail |

**判断**：DeepCLI 的 PromptManager 方向更适合长期维护；Codex 值得借鉴的是
model/personality/review/guardian/goal 等 prompt 类型，以及真实 prompt input
debug 工具。

## 5. Codex 独有或明显更成熟的子系统

下面这些不是 DeepCLI 当前 Kernel 的一等子系统，或成熟度明显低于 Codex：

| 子系统 | Codex 路径 | DeepCLI 当前状态 | 价值判断 |
|---|---|---|---|
| Execpolicy DSL | `codex-rs/execpolicy/` | ToolAuthorizer rules + Bash classifier | 高价值。应作为未来 permission policy 参考 |
| OS sandbox | `sandboxing/`, `linux-sandbox/`, `windows-sandbox-rs/`, `bwrap/`, `process-hardening/` | 无同等级机制 | 高价值。尤其 shell/apply_patch/network |
| App server protocol | `app-server/`, `app-server-protocol/` | ACP/JSON-RPC + Access/Hub | 参考即可。DeepCLI 不应替换 ACP |
| Exec server | `exec-server/` | Bash/PowerShell/Cmd/REPL 在 Kernel tools 内 | 高价值。可借鉴隔离执行环境 |
| Plugin marketplace | `core-plugins/`, `plugin/`, `utils/plugins/` | PDS Plugin 概念有，运行时 plugin marketplace 未成型 | 高价值，贴 PDS |
| Cloud tasks | `cloud-tasks/` | ScheduleManager/Cron，但无云任务产品面 | 中高价值，取决于产品方向 |
| Collaboration modes | `collaboration-mode-templates/` | DeepCLI 有 plan/default 行为，但无模板包 | 中等价值 |
| Response debug context / rollout trace | `response-debug-context/`, `rollout-trace/` | SQLite session + probes | 高价值，贴 benchmarking |
| Analytics / OTEL | `analytics/`, `otel/` | 日志/probe 为主 | 中高价值，尤其多模型 benchmark |
| Agent graph store | `agent-graph-store/` | Hub runtime registry，不是图存储 | 未来 peer Session Agents 可参考 |
| External agent sessions | `external-agent-sessions/` | 尚无跨 harness session ledger | 中等价值 |
| Guardian / network approval | `core/src/guardian/`, `tools/network_approval.rs` | Web tools 有 domain/preapproval，shell network 无统一 guardian | 高价值 |
| Built-in MCP server | `mcp-server/`, `builtin-mcps/` | MCP client/manager 为主 | 可用于让 DeepCLI 反向暴露能力 |
| Model/provider auth suite | `login/`, `chatgpt/`, `ollama/`, `lmstudio/`, `aws-auth/` | LLMProviderManager + SecretManager | Codex 更产品化，DeepCLI 更 provider-agnostic |

## 6. 对 DeepCLI 的建议

按“最短路径 + 最大收益”排序：

1. **先借 execpolicy/sandbox/apply_patch，不借整体工具架构。**
   DeepCLI 的 Tool ABC 和 ToolAuthorizer 边界已经合理；缺的是更安全的
   shell/patch 执行机制。

2. **为 PromptBuilder 增加真实 prompt debug/probe。**
   Codex `prompt_debug.rs` 的价值很直接：任何 prompt 改动都能看到真实
   model-visible input。DeepCLI 现在 probe 分散，缺一个统一入口。

3. **Memory 下一步应借 Codex 两阶段后台管线。**
   保留 DeepCLI 的透明分类 Markdown 树，但用 Codex 的 job claim、lease、
   backoff、workspace diff 和 consolidation agent，解决“自动积累”的闭环。

4. **Skill 下一步借 UI metadata + implicit indexes。**
   `interface`、icons、brand color、default prompt、doc/script path indexes
   对未来 Home Screen 和 PDS library 很有用。

5. **Plugin marketplace 应单独设计，不混入 SkillManager。**
   Codex 把 plugin roots 和 skills 连接得很紧；DeepCLI 的 PDS 三形态
   要求 Plugin / Template-App / Session Agent 分层更清楚。

6. **不要把 Codex app-server protocol 替换 ACP。**
   DeepCLI 已经把 ACP 作为前端唯一边界；Codex app-server 适合作为
   lifecycle/event/error-shape 参考，而不是协议迁移目标。

## 7. 一句话总结

Codex CLI 的强项是“安全执行 + 产品化 harness”：execpolicy、sandbox、
apply_patch、plugin marketplace、cloud tasks、debug/telemetry。DeepCLI Kernel
的强项是“可演化 runtime 边界”：Supervisor/Hub/Access/Primary 分层、ACP
前端边界、typed subsystem、PDS 长期软件库方向。下一步不是重写成 Codex，
而是把 Codex 的安全执行、后台 memory 管线、prompt debug、plugin 生态能力
作为可替换 subsystem 吸收进 DeepCLI。
