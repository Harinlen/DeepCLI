# Plan: Align DeepCLI Prompts with Claude Code Main

## Context

DeepCLI's system prompt 已完成 7 个 static sections 和核心工具描述的 CC 对齐。
剩余差距分布在三个层面：系统提示词动态 section、工具描述、基础设施。
本 plan 按影响力排序，将剩余差距全部补齐。

---

## Phase 1: Tool Descriptions + PromptManager Migration (✅ Landed)

Scope expanded from "replace description strings" to four infrastructure
changes plus a full migration of every built-in tool description into
`prompts/default/tools/*.txt` files managed by `PromptManager`.  This
removes the "tool descriptions may live in .py" exception from the
prompts policy — all prompt text now lives under `prompts/default/`.

### A. Infrastructure (landed)

| Item | Change |
|------|--------|
| A.1 | `Tool.description_key` ClassVar + `get_description()` hook; ToolManager injects PromptManager into every registered tool; `to_schema()` resolves description via the hook |
| A.2 | `TodoWriteTool` schema adds required `activeForm` field (CC parity: imperative + present-continuous task forms); `validate_input` rejects malformed items |
| A.3 | `LLMManager.model_for_or_default("compact")` (graceful fallback to default); WebFetch uses it for CC-style secondary-model post-processing via `ctx.summarise`; Compactor resolves compact role at construction |
| A.4 | Two new `HookEvent`s (`WORKTREE_CREATE`, `WORKTREE_REMOVE`, 18 total); EnterWorktree/ExitWorktree dispatch to git path when available, fall back to hook path when not (CC parity); worktree tools now always register |

### B. Prompt migration + CC alignment (landed)

Every built-in tool description moved to `prompts/default/tools/<name>.txt`.
The Python `description` ClassVar survives only as a minimal fallback
(one sentence).  `description_key = "tools/<name>"` routes the LLM-visible
description through PromptManager at `to_schema()` time.

| Tool | File | CC alignment |
|------|------|--------------|
| B.1 | `web_search.txt` | CC full text with `{month_year}` template; US-only line dropped (DeepCLI is multi-backend) |
| B.2 | `enter_plan_mode.txt` | CC full text — 7 When-to-Use criteria, What Happens, GOOD/BAD examples |
| B.3 | `exit_plan_mode.txt` | CC full text including 3-example vim/yank/auth Examples section |
| B.4 | `exit_worktree.txt` | CC full text minus tmux bullet (DeepCLI has no tmux UX) |
| B.5 | `cron_create.txt` | CC full text + off-minute rationale + Durability + jitter specifics + 7-day auto-expire; `kernel.db` substituted for `.claude/scheduled_tasks.json`; DeepCLI-specific addenda (skills/model/delivery/repeat_count/repeat_duration/repeat_until) |
| B.6 | `todo_write.txt` | CC full text — 4+4 worked examples with `<reasoning>`, Task Breakdown, activeForm requirement |
| B.7 | 17 others | `agent/bash/ask_user_question/cron_delete/cron_list/file_edit/file_read/file_write/glob/grep/monitor/powershell/send_message/skill/task_output/task_stop/web_fetch/enter_worktree` — content unchanged, moved to files |

### C. Canary tests (landed)

`tests/kernel/tools/test_cc_alignment.py` — 22 canary assertions spread
across TodoWrite / CronCreate / WebSearch / EnterPlanMode / ExitPlanMode
/ ExitWorktree / EnterWorktree.  Each one guards a critical CC idea
(e.g. `activeForm` taught, off-minute rationale present, tmux absent)
so a future cleanup that silently drops the text fails CI.

No fixture snapshots — when CC itself upgrades a maintainer reads
the CC `prompt.ts` directly and updates DeepCLI's `.txt` + canaries
together.

---

## Phase 2: Session-Specific Guidance Section (✅ Landed)

**CC 来源**: `getSessionSpecificGuidanceSection()` (prompts.ts:352-400)
**实装位置**: `orchestrator.py::_build_session_guidance` (per-turn, not per-session)

### 与原设计的偏差（均有合理性）

| 原计划 | 实际 | 原因 |
|------|------|------|
| 单个 static `.txt` | 6 个 per-bullet `.txt` + Python 条件化 | Tools 按 turn 动态 snapshot（例如 EnterWorktree 由 GitManager 运行时决定注册与否），静态版本会让已卸载工具的 bullet 误留；per-bullet 条件化与 CC 的 enabledTools 分支一一对应 |
| 注入点在 `prompt_builder.py`，`cache=True` | 注入点在 `orchestrator.py`，`cache=False` | 依赖 per-turn 的 `snapshot.lookup`（在 prompt_builder 里拿不到）；内容随工具集变化，不宜缓存 |

### 实际落地

- `prompts/default/orchestrator/session_guidance/*.txt` — 6 条 bullet，每条一个文件，文本**verbatim from CC main prompts.ts:356-389**（替换 `${AGENT_TOOL_NAME}` 等模板变量为 DeepCLI 的工具名）
  - `deny_ask.txt` · `interactive_shell.txt` · `agent_tool.txt` · `search_direct.txt` · `search_explore_agent.txt` · `skill_invoke.txt`
- `Orchestrator._build_session_guidance(enabled_tools, has_skills)` — 条件化拼装，缺失文件安全降级
- `Orchestrator._inject_session_guidance(...)` — 在 tool snapshot 之后、plan mode 之前追加 `PromptSection(cache=False)`
- 不移植的 CC 分支（DeepCLI 无对应特性）：fork subagent / VERIFICATION_AGENT / DISCOVER_SKILLS / Bash-based search tools

### 测试

- **Canary**: `tests/kernel/orchestrator/test_session_guidance_alignment.py` — 17 断言（bullet 内容、条件组合、禁止漏入 fork/verification/discover_skills 文案）
- **Probe**: `scripts/probe_session_guidance.py` — 覆盖全部 3 个 closure seam：
  1. 真实 `PromptManager.load()` 扫 6 个 `.txt` + `interactive_shell` 与 CC 逐字节对比
  2. 真实 `ToolManager.startup()` + `snapshot_for_session()` 确认 tool-name 集合里有 `Agent` / `AskUserQuestion` / `Skill`（gate 依赖的名字与 registry 实际名字对齐）
  3. 真实 `Orchestrator.query()` 走完一轮，`_CapturingProvider.calls[0]["system"]` 中确实含 `# Session-specific guidance` PromptSection 且 6 个 bullet 都在

---

## Phase 3: Environment Context 补全 (✅ Landed)

**CC 来源**: `computeSimpleEnvInfo()` (prompts.ts:651-710)
**实装位置**: `prompt_builder.py::_build_env_context`

### 与原设计的偏差（均有合理性）

| 原计划 | 实际 | 原因 |
|------|------|------|
| 加 marketing-name 查表 + `named X. The exact model ID is Y.` | **砍掉**，走 CC 的 null-marketing fallback：`You are powered by the model {id}.` | 每次 Claude 发版要手动加一行；DeepCLI multi-provider 还要给 OpenAI/Qwen/DeepSeek 维护，很快失控；ROI 太低 |
| 加 `_KNOWLEDGE_CUTOFF` dict + `Assistant knowledge cutoff is Z.` | **砍掉**（整行不输出） | 只对 Claude 有意义，与 multi-provider 定位冲突；WebSearch 覆盖 "post-cutoff" 场景 |
| 签名 `model_id: str \| None` | `model: ModelRef \| None` | 直接复用 orchestrator 已有的 `self._config.model`，信息量更大 |
| `_build_env_context` 改普通方法 | 保留 `@staticmethod`，只加 `model` kwarg | 不需要 `self`，改动更小 |

### 实际落地

新增 **一行**（CC prompts.ts:627 的 null-marketing-name fallback 原句）：

```
 - You are powered by the model {model.model}.
```

`model is None` 时整行不输出（degraded 模式 / 早期 bootstrap 兼容）。

主动不移植的 CC 分支（与 DeepCLI 冲突）：
- `getMarketingNameForModel` 查表 + `(with 1M context)` 后缀
- `getKnowledgeCutoff` 查表
- 3 行产品营销（`most recent Claude model family` / `Claude Code is available` / `Fast mode`）——CC 在 `computeSimpleEnvInfo` 里 ship 给外部用户，但 DeepCLI ≠ Claude Code

### 测试

- **Canary**: `tests/kernel/orchestrator/test_env_context_alignment.py` — 8 断言（model 行条件化、git 检测、provider-agnostic、禁止漏入 cutoff/marketing/产品营销文案）
- **Probe**: `scripts/probe_env_context.py` — 一条 closure seam：真实 `Orchestrator.query()` → `_CapturingProvider.calls[0]["system"]` 里找到 env section，`cache=False`，model 行按预期，CC-only 文本零泄漏

---

## Phase 4: MCP Instructions 注入 (✅ Landed)

**影响**: 让 LLM 知道每个 MCP server 的使用指引。
**CC 来源**: `getMcpInstructions()` (prompts.ts:579-604)，由 `getMcpInstructionsSection()` 包一层 null-guard 调用。

### 与原设计的偏差（均有合理性）

| 原计划 | 实际 | 原因 |
|------|------|------|
| 伪代码里只有 `# MCP Server Instructions\n\n` + blocks | 加回 CC 原文的一句引导语 "The following MCP servers have provided instructions for how to use their tools and resources:" | 用户要求"prompts 从 CC main 照搬"；`prompts.ts:599-603` 原文就是 header + intro + blocks |
| header/intro 文案 inline 在 Python 里 | 迁到 `prompts/default/orchestrator/mcp_instructions.txt` + `PromptManager.render(blocks=...)` | 与 Phase 1 / Phase 5 "所有 prompt 文案都走 PromptManager" 一致；per-server block 的循环拼接仍留在 Python 层（数据层，不属文案） |
| 在 env context **之后**注入 | 在 **language section 之后、git context 之前**注入 | Phase 5 已 land，把 language 放在 env 之后；CC 顺序是 `env_info_simple → language → output_style → mcp_instructions`（prompts.ts:499-520），DeepCLI 没有 output_style，因此 MCP 紧跟 language |
| 签名 `Callable[[], list[tuple[str, str]]]` | 保持原签名（sync） | `MCPManager.get_connected()` 是同步的；`ConnectedServer.instructions` 是 initialize handshake 时就已缓存的字段，调闭包没有 I/O |

### 实际落地

1. **模板** — `prompts/default/orchestrator/mcp_instructions.txt`（CC `getMcpInstructions` 原文 verbatim，`{blocks}` 替代循环 join 结果）：

   ```
   # MCP Server Instructions

   The following MCP servers have provided instructions for how to use their tools and resources:

   {blocks}
   ```

2. **deps 字段** — `orchestrator/types.py::OrchestratorDeps` 新增
   ```python
   mcp_instructions: Callable[[], list[tuple[str, str]]] | None = field(default=None)
   ```
   docstring 明确：sync；返回 `(server_name, instructions)`；只包含已 connected 且 `instructions` 为 truthy 的 server（对齐 CC `getMcpInstructions` 的 `.filter(client => client.instructions)`）。`None` → PromptBuilder 跳过 MCP section。

3. **SessionManager 接线** — `session/__init__.py::_make_orchestrator` 照 GitManager 的 `try / KeyError / ImportError` pattern 从 `module_table.get(MCPManager)` 取 manager，封装一个 `_mcp_instructions()` 闭包，在 `OrchestratorDeps(...)` 构造里传入。MCPManager 未注册 / subsystem 禁用时闭包返回 `[]`，PromptBuilder 自然跳过。

4. **注入点** — `prompt_builder.py::build()` 在 Phase 5 的 language section 之后、git context 之前追加
   ```python
   if prompts is not None and prompts.has("orchestrator/mcp_instructions"):
       pairs = (deps.mcp_instructions() if deps.mcp_instructions else [])
       pairs = [(n, i) for n, i in pairs if i]
       if pairs:
           blocks = "\n\n".join(f"## {name}\n{instructions}" for name, instructions in pairs)
           sections.append(PromptSection(
               text=prompts.render("orchestrator/mcp_instructions", blocks=blocks),
               cache=False,
           ))
   ```
   `cache=False` 对齐 CC 的 `DANGEROUS_uncachedSystemPromptSection('mcp_instructions', ..., 'MCP servers connect/disconnect between turns')`（prompts.ts:513-520）——server 可能在两轮之间断开 / 重连，cache=True 会让 LLM 读到过期指引。

### 主动不移植的 CC 分支

- `isMcpInstructionsDeltaEnabled()` delta-attachment 旁路（prompts.ts:481-483 / 516-518）——CC 的 attachments.ts 持久化 delta 避免"中途连上新 MCP server"触发 cache bust。DeepCLI 没有 attachment 持久化层，也没遇到 cache bust 痛点；本来就 `cache=False`，不引入 delta 无额外代价。

### 测试

- **Canary**: `tests/kernel/orchestrator/test_mcp_instructions_alignment.py` — 断言包含
  1. 模板加载 / header + intro 与 CC prompts.ts:599-603 **逐字节**一致
  2. `{blocks}` 占位符存在、Python 填充正确
  3. `mcp_instructions=None` / `getter()=[]` / 所有 server 的 `instructions` 都为空 → section 不注入
  4. 单 server / 双 server（`## <name>\n<instr>` 由空行分隔）
  5. `cache=False` 且位置紧跟 language section / env context（无 language 时）
  6. 反向守卫：`isMcpInstructionsDeltaEnabled` / `mcp_instructions_delta` / `attachments` 等 CC feature-flag 文案零泄漏
- **Probe**: `scripts/probe_mcp_instructions.py` — 覆盖 2 个 closure seam：
  1. 真实 `PromptManager.load()` 扫 `mcp_instructions.txt`，`render(blocks="## foo\n...")` 结果与手工拼接字符串 byte-equal
  2. 真实 `SessionManager._make_orchestrator` + stub MCPManager（注入一个 `ConnectedServer(name="fake", instructions="do X")`）→ 真实 `Orchestrator.query()` → `_CapturingProvider.calls[0]["system"]` 含 `# MCP Server Instructions` section 且 `cache=False`；degraded 模式（MCPManager 未注册）走完同样一轮、section 缺席

---

## Phase 5: Language Section (✅ Landed)

**CC 来源**: `getLanguageSection()` (prompts.ts:142-149)
**实装位置**: `prompt_builder.py::build(language=...)`; bind 点在 `SessionManager.startup`

### 与原设计的偏差（均有合理性）

| 原计划 | 实际 | 原因 |
|------|------|------|
| `flags.yaml` 或 config 的 `general` section | `config.yaml` 的 `orchestrator` section（owner = SessionManager） | flags 是开关不是字符串；复用既有 `config.yaml` 与其他用户偏好（`permissions`/`skills`/`git`）同层；owner 必须是创建 orchestrator 的组件 |
| 文案 inline 在 Python 代码里 | 迁到 `prompts/default/orchestrator/language.txt` + `PromptManager.render()` | 与 Phase 1 "所有 prompt 文案都走 PromptManager" 的政策一致；CC 自己的 `${languagePreference}` 模板也是 string.format 语义 |
| `OrchestratorDeps` 不改 | `OrchestratorConfig` + `OrchestratorConfigPatch` 加 `language: str | None` | 与 `model` 字段对称（Phase 3 已经把 `model` 放在 config 里，而不是 deps 上），sub-agent 通过继承父 config 自动带上 language |

### 实际落地

1. **模板** — `prompts/default/orchestrator/language.txt`（CC 原文 verbatim，`{language}` 替代 `${languagePreference}`；出现两次必须都替换）。
2. **Config schema** — `orchestrator/config_section.py::OrchestratorPrefs(language: str | None = None)`。
3. **Owner** — `SessionManager.startup` 调用 `bind_section(file="config", section="orchestrator", schema=OrchestratorPrefs)`；绑定失败降级为"无 language 偏好"。
4. **数据流** — `SessionManager._make_orchestrator` 当 `config=None` 时读取 prefs，写入默认 `OrchestratorConfig.language`；caller 提供 config 时不注入，尊重 caller 意图。
5. **注入点** — `prompt_builder.py::build()` 多一个 `language: str | None` kwarg，在 env context section 之后、git context 之前追加 `PromptSection(cache=True)`（CC prompts.ts:499-504 的顺序，语义上是"稳定的用户偏好可缓存"，与 env context 的 timestamp-driven `cache=False` 对比）。
6. **Orchestrator 接线** — `orchestrator.py::_run_query` 把 `self._config.language` 透传到 `build()`；`set_config` patch 合并时也一并处理。Sub-agent 通过继承 `parent._config` 自动带上。

### 测试

- **Canary**: `tests/kernel/orchestrator/test_language_alignment.py` — 13 断言（模板加载/CC verbatim/双占位符/语言=None 时不注入/set_config patch/cache=True/紧跟 env context/多语种中文/CC-only 文案未泄漏）
- **Probe**: `scripts/probe_language.py` — 覆盖全部 3 个 closure seam：
  1. 真实 `PromptManager.load()` 扫 `language.txt`，中英渲染双方验证
  2. 真实 `ConfigManager` 从 YAML 载入 `orchestrator.language: English`，SessionManager 构 orchestrator 时 `OrchestratorConfig.language == "English"`
  3. 真实 `Orchestrator.query()` 走完一轮，`_CapturingProvider.calls[0]["system"]` 中含 `# Language` PromptSection 且 cache=True、位置紧跟 env context；language=None 分支不泄漏

### 不移植的 CC 逻辑

- `getInitialSettings()` 自身的 settings.json 加载机制 —— DeepCLI 走 ConfigManager
- Output Style section（下一个 CC 分支，但独立于 Phase 5 scope）

---

## Phase 6: Scratchpad Directory (可 defer)

**影响**: 引导 LLM 用 session 专属临时目录代替 `/tmp`。
**依赖**: 需要 session 级生命周期管理。

### 6.1 在 session 启动时创建 temp dir
### 6.2 通过 `build()` 参数传入 path
### 6.3 在 `prompt_builder.py` 注入 CC 的 scratchpad instructions

可 defer — DeepCLI 目前没有 sandbox，`/tmp` 可用。

---

## Phase 7: Function Result Clearing (可 defer)

**依赖**: microcompact 实现。
**当前状态**: Compactor 存在但 microcompact 策略未确认。
可 defer 到 microcompact 完成后。

---

## 不移植的部分 (CC feature-gated / ant-only)

- Proactive/autonomous section (KAIROS feature)
- BriefTool/SendUserMessage (KAIROS)
- Token budget section
- Numeric length anchors (ant-only)
- Sandbox section (DeepCLI 无 sandbox)
- NotebookEditTool (主动不做 — `.ipynb` 不在范围)
- Verification agent (ant-only A/B)
- ScheduleWakeupTool (CC SDK runtime 注入)

---

## 执行顺序

```
Phase 1 (tool descriptions)  ✅ Landed
  ↓
Phase 2 (session guidance)   ✅ Landed (dynamic, per-bullet .txt)
  ↓
Phase 3 (env context)        ✅ Landed (one line: You are powered by the model <id>.)
  ↓
Phase 5 (language section)   ✅ Landed (config.yaml `orchestrator.language` + prompt section)
  ↓
Phase 4 (MCP instructions)   ✅ Landed (`mcp_instructions.txt` + deps.mcp_instructions + session 接线)
  ↓
Phase 6-7 (defer)
```

## 验证

每个 Phase 完成后:
1. `python -m pytest tests/ -x -q` — 全量测试通过
2. 检查 PromptManager 能加载所有新 .txt: `pm.keys()`
3. 人工审查生成的 system prompt 是否与 CC 原文对齐

## 文件变更清单

| Phase | 新建 | 修改 |
|-------|------|------|
| 1 | — | 10 个 `tools/builtin/*.py` |
| 2 | `prompts/default/orchestrator/session_guidance/{deny_ask,interactive_shell,agent_tool,search_direct,search_explore_agent,skill_invoke}.txt`, `tests/kernel/orchestrator/test_session_guidance_alignment.py`, `scripts/probe_session_guidance.py` | `orchestrator.py::_build_session_guidance` |
| 3 | `tests/kernel/orchestrator/test_env_context_alignment.py`, `scripts/probe_env_context.py` | `prompt_builder.py`, `orchestrator.py::_run_query` |
| 4 | `prompts/default/orchestrator/mcp_instructions.txt`, `tests/kernel/orchestrator/test_mcp_instructions_alignment.py`, `scripts/probe_mcp_instructions.py` | `orchestrator/types.py` (OrchestratorDeps), `orchestrator/prompt_builder.py`, `session/__init__.py` (_make_orchestrator) |
| 5 | `prompts/default/orchestrator/language.txt`, `orchestrator/config_section.py`, `tests/kernel/orchestrator/test_language_alignment.py`, `scripts/probe_language.py` | `orchestrator/__init__.py` (OrchestratorConfig + Patch), `orchestrator.py` (set_config / build), `orchestrator/prompt_builder.py`, `session/__init__.py` (startup + _make_orchestrator) |
| 最后 | — | `docs/reference/prompts.md` (更新 parity 表) |
