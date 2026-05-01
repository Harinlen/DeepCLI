# Claude Code Comparison

Detailed feature and prompt comparison between DeepCLI and Claude Code
CLI. Written 2026-04-06, updated 2026-04-25 with CC new tools (Task CRUD,
Team, Sleep, Brief, SyntheticOutput) and DeepCLI fixes (AgentTool kind,
PromptBuilder ordering, PromptManager override layers).

**Purpose**: identify gaps, borrowable patterns, and prioritised
improvements.

---

## 1. Prompt System (highest-value gap)

### 1.1 Structure

| Aspect | DeepCLI | Claude Code |
|--------|---------|-------------|
| Build method | `PromptSection` dataclass + `cacheable` flag | Registry design, named sections with compute functions |
| Total length | ~100-150 lines | **350+ lines**, extremely detailed |
| Caching | Section-level cacheable boolean | Anthropic API `cache_control` injection, max 4 breakpoints, breakpoint merging |
| Dynamic sections | Environment + git + memory + skills | Same + conditional tool guidance + model-specific info |

### 1.2 Content DeepCLI is missing

#### High priority

| Section | Claude Code | DeepCLI | Lines |
|---------|-------------|---------|-------|
| **Tool usage guidance** | "Use Read not cat", "Use Edit not sed", "Use Grep not grep/rg", detailed when-to-use rules for every tool | Simple tool list only | ~100 |
| **Code quality / safety** | "Don't introduce XSS/SQL injection", "Don't over-engineer", "Don't add unnecessary error handling", "3 similar lines > premature abstraction" | 15 lines of basics | ~80 |
| **Git safety** | Full commit flow (status + diff + log → stage + commit), PR flow, "never amend unless asked", "never force push", HEREDOC commit message format | None | ~60 |
| **Cautious operations** | Reversible vs irreversible, "measure twice cut once", confirm before delete/force-push, don't bypass safety checks | None | ~40 |
| **Output efficiency** | "Go straight to the point", "If one sentence, don't use three", "Lead with the answer, not the reasoning" | None | ~15 |
| **Model self-info** | Knowledge cutoff date, model family, "Fast mode uses same model" | Model name only | ~10 |

#### Medium priority

| Section | Claude Code | DeepCLI |
|---------|-------------|---------|
| **Conditional tool guidance** | Dynamic based on enabled tool set (MCP tools, IDE tools) | Static |
| **Environment detail** | 120+ lines: platform, shell, OS version, git repo status, main branch, working directory | 30 lines |
| **Memory teaching** | 200+ lines: types, examples, when-to-save, when-not-to-save, verification before acting on memory | Basic injection |
| **Tone / style** | "No emojis unless asked", "Use markdown links for file refs", "GitHub links in owner/repo#N format" | Minimal |

### 1.3 Prompt patterns to adopt

```
Priority 1 (prompt template changes only, zero code):
  - Tool usage guidance (dedicated tools > Bash)
  - Code quality: no over-engineering, no speculative abstractions
  - Git safety: commit/PR complete flow, no amend, no force push
  - Cautious operations: reversible vs irreversible distinction
  - Output efficiency: concise, direct, answer-first

Priority 2 (minor code changes):
  - Model info injection: knowledge cutoff, model family
  - Conditional tool sections: based on enabled extensions
  - Richer environment: OS version, shell, main branch name
```

---

## 2. Tool System

### 2.1 Inventory

| Category | DeepCLI (19) | Claude Code (43+) | Gap |
|----------|-------------|-------------------|-----|
| File ops | 5 (read/write/edit/glob/grep) | 5 + NotebookEdit | ⛔ NotebookEdit 不在范围 |
| Shell | Bash + PowerShell + REPL | Bash + PowerShell + REPL | ✅ Parity |
| Agent | AgentTool + SendMessage | Agent + TeamCreate + TeamDelete + SendMessage | Missing Team; SendMessage aligned (+ ACP cross-session) |
| Tasks | TodoWrite + TaskOutput + TaskStop | TodoWrite + TaskCreate + TaskGet + TaskList + TaskUpdate + TaskOutput + TaskStop | Partial gap — CC 新增 Task CRUD for in-process teammates |
| Memory | 5 tools (write/append/delete/list/search) | SDK-integrated | DeepCLI more structured |
| Web | WebFetch + WebSearch (deferred) | 2 (fetch + search) | ✅ Parity |
| Plan | EnterPlanMode + ExitPlanMode + EnterWorktree + ExitWorktree | Same | ✅ Parity (Phase 15) |
| Discovery | ToolSearch (deferred loading) | ToolSearch (deferred loading) | ✅ Parity |
| IDE | None | LSPTool | Missing LSP integration |
| Config | None | ConfigTool | Missing config management tool |
| User interaction | AskUserQuestion | AskUserQuestion | ✅ Parity |
| Scheduling | CronCreate/Delete/List + /loop | ScheduleCronTool (同 3 功能) | ✅ Parity — CC 改名为 ScheduleCronTool，语义等价 |
| MCP Resources | None | ListMcpResourcesTool + ReadMcpResourceTool | ❌ 缺失；CC 中对 async agent 仍 TBD |
| Async/proactive | None | SleepTool + BriefTool + SyntheticOutputTool | ⛔ BriefTool/Synthetic 不在范围；SleepTool 低优先级 |

### 2.2 Design pattern differences

| Aspect | DeepCLI | Claude Code | Recommendation |
|--------|---------|-------------|----------------|
| **Description** | Single string field | Async function + separate prompt.ts file with DESCRIPTION + PROMPT constants | Separate description from detailed usage prompt |
| **Permission** | Static enum (`DANGEROUS`) | Dynamic `checkPermissions(input)` function | Move to input-based dynamic decisions |
| **File state tracking** | None | `readFileState` Map (timestamp + content, race condition prevention) | **High value** — prevent overwriting external changes |
| **Error handling** | `is_error: bool` | Error codes + `behavior: 'ask'\|'allow'\|'deny'` | Add error code system |
| **Bash safety** | Basic exit code check | Destructive command detection + sed→FileEdit conversion + readonly mode validation | Add command safety analysis |
| **Large results** | Truncate at `max_result_chars` | Disk storage + preview + lazy loading | Spill oversized results to disk |
| **File history** | None | `fileHistoryTrackEdit()` backup before edit | Consider for undo support |
| **Validation pipeline** | 1 layer (Pydantic) | 3 layers (Zod + validateInput + checkPermissions) | Add pre-execution validation |
| **Concurrency** | `ConcurrencyHint` enum + `concurrency_key()` | `isConcurrencySafe()` predicate | Consider input-based predicate |

### 2.3 Per-tool comparison (key tools)

#### FileEdit

| Feature | DeepCLI | Claude Code |
|---------|---------|-------------|
| Method | old_string / new_string | Same + quote normalisation |
| Uniqueness | Enforced (fail without replace_all) | Same + finer error messages |
| **Timestamp validation** | None | **readFileState**: checks file unmodified since last read |
| Settings file validation | None | `validateInputForSettingsFileEdit()` |
| File size limit | None | 1 GB (prevent OOM) |
| UTF-16 support | None | BOM detection |
| LSP notification | None | didChange / didSave |
| Git diff | None | `fetchSingleFileGitDiff()` |

#### Bash

| Feature | DeepCLI | Claude Code |
|---------|---------|-------------|
| Platforms | bash / cmd auto-select | bash + cmd + PowerShell |
| Background execution | None | `run_in_background` param + task tracking |
| Safety | Basic exit code | Destructive command detection, sed parsing, readonly validation |
| Progress | None | `renderToolUseProgressMessage()` real-time |
| Image output | None | `isImageOutput()` + resize |

#### Agent

| Feature | DeepCLI | Claude Code |
|---------|---------|-------------|
| Background | None | `run_in_background` + notification |
| Isolation | None (reserved) | worktree / remote |
| Model override | None | `model` parameter |
| Naming | `name` param + `SendMessage()` addressing | `name` param + `SendMessage()` addressing | Aligned |
| Team support | None | `team_name` + TeamCreate/Delete + Task CRUD |
| kind in plan mode | ✅ `orchestrate` — survives plan-mode filter | Agent visible in plan mode | ✅ 对齐（2026-04-25 修正）|
| Coordinator mode | None | `COORDINATOR_MODE_ALLOWED_TOOLS` (Agent+TaskStop+Send+SyntheticOutput) | CC 新增 multi-agent 协调者角色 |

---

## 3. Engine / Orchestrator

### 3.1 Compression strategy (major gap)

| Layer | DeepCLI | Claude Code |
|-------|---------|-------------|
| **Snip compression** | ✅ Replaces read-only tool results with placeholders | Removes internal automated content, preserves protected tail |
| **Micro-compact** | ✅ Removes entire read-only tool call pairs | Summarises cached tool call edits (by tool_use_id) |
| **Context collapse** | None | Feature-gated, interface only — core impl (`services/contextCollapse/`) not shipped |
| **Auto-compact** | ✅ Single LLM-driven | LLM-driven (same concept) |
| **Reactive compact** | ✅ `PromptTooLongError` → compact → retry (max 2) | API `prompt_too_long` triggers recovery loop |

Claude Code has **three progressive layers** before auto-compact,
reducing LLM calls for compression.

### 3.2 Query loop

| Aspect | DeepCLI | Claude Code |
|--------|---------|-------------|
| Max rounds | ✅ Caller-controlled `max_turns` (default unlimited) | Caller-controlled `maxTurns` (default unlimited) |
| `prompt_too_long` handling | ✅ `PromptTooLongError` → reactive compact → retry (max 2) | Reactive compression + retry |
| `max_output_tokens` handling | ✅ 8k→64k escalation + retry ×3 | Recovery loop (up to 3 retries) |
| `media_size` handling | ✅ `MediaSizeError` → strip images → compact → retry | Reactive compact + strip media |
| Concurrency model | DAG-style `ExecutionSlot` | Simple batch partitioning |
| Skill pre-discovery | None | Background parallel pre-fetch |
| Stop hooks | ✅ `HookEvent.STOP` with `stop_reason` on `HookEventCtx` | Post-LLM stop hook phase |
| Token budget | ✅ `token_budget` param on `query()` | Task budget check |

### 3.3 Streaming

| Aspect | DeepCLI | Claude Code |
|--------|---------|-------------|
| Event granularity | Fine (per-delta) | Coarser (full API response blocks) |
| Tool start event | Explicit `ToolCallStart` | Part of API response |
| Progress events | None | Per-tool progress messages |
| Hook result events | None | `AttachmentMessage` container |

---

## 4. Permissions / Config / Extensions

### 4.1 Permission system

| Aspect | DeepCLI | Claude Code |
|--------|---------|-------------|
| Modes | 6 (default / plan / bypass / accept_edits / auto / dont_ask) | 6 (+ dontAsk / bubble) |
| Auto classifier | BashClassifier + LLMJudge | Bash safety classifier |
| Rule sources | 4 layers (user / project / local / flag) | **5 layers** (+ policy) |
| Escape syntax | None | `\(` and `\)` in patterns |
| Session grants | SessionGrantCache (exact-command + destructive guard) | Temporary + permanent per-session |

### 4.2 Config system (architectural gap)

| Aspect | DeepCLI | Claude Code |
|--------|---------|-------------|
| Format | YAML | JSON |
| **Layering** | **2 layers** (global `~/.mustang/` + project `.mustang/`) | **5 layers** (user / project / local / flags / policy) |
| Project-level config | `.mustang/config/` | `.claude/settings.json` |
| Local overrides | None | `.claude-local/settings.json` |
| Admin policies | None | Policy settings |
| Feature flags | `FlagManager` (YAML) | GrowthBook integration |

**Config layering gap is narrowing.** DeepCLI supports global + project
config. Still missing: local overrides and admin policies.

### 4.3 Hooks

| Aspect | DeepCLI | Claude Code |
|--------|---------|-------------|
| Event types | **14** (pre/post_tool_use, post_tool_failure, session_start/end, user_prompt_submit, post_sampling, stop, pre/post_compact, file_changed, subagent_start, permission_requested/denied) | **8+** |
| Hook types | 2 (command / prompt) | 2 (command / prompt) + async |
| Progress events | None | HookStarted / HookProgress / HookResponse |
| Permission integration | Integrated (permission_denied/requested events + HookBlock) | Integrated (`permissionDecision` in response) |

### 4.4 MCP

| Aspect | DeepCLI | Claude Code |
|--------|---------|-------------|
| Transport types | 4 (stdio / SSE / WebSocket / in-process) | 6 (+ HTTP streamable + SDK control) |
| OAuth | None | Full OAuth flow |
| Config layers | 1 | 3 (user / project / managed) |
| Output storage | None | Binary blob storage for large results |
| Code indexing | None | Detection + optimisation |

### 4.5 Memory

| Aspect | DeepCLI | Claude Code |
|--------|---------|-------------|
| Scopes | 2 (global + project) | SDK-managed |
| Types | 6 (user / feedback / project / reference / task / context) | SDK-defined |
| Storage | Filesystem (markdown) | Possibly cloud |
| Relevance | LLM side-query selector | Prefetch + attachment |
| Access tracking | Yes | Unclear |
| Auto-extraction | Background mixin | extractMemories service |

DeepCLI's memory system is **more explicit and structured** — this is
a strength, not a gap.

---

## 5. Priority Matrix

### Immediate (prompt-only, zero code)

| # | Item | Effort | Value |
|---|------|--------|-------|
| 1 | ~~Tool usage guidance in system prompt~~ ✅ Done | — | — |
| 2 | ~~Code quality / safety guidance~~ ✅ Done | — | — |
| 3 | ~~Git safety rules (commit/PR flow)~~ ✅ Done | — | — |
| 4 | ~~Cautious operations guidance~~ ✅ Done | — | — |
| 5 | ~~Output efficiency instructions~~ ✅ Done | — | — |
| 6 | ~~Model self-info injection~~ ✅ Done | — | — |

### Short-term (moderate effort, high value)

| # | Item | Effort | Value |
|---|------|--------|-------|
| 7 | ~~File modification detection (readFileState)~~ ✅ Done | — | — |
| 8 | ~~Reactive compression (prompt_too_long recovery)~~ ✅ Done | — | — |
| 9 | ~~Bash destructive command detection~~ ✅ Done | — | — |
| 10 | ~~Project-level config~~ ✅ Done | — | — |

### Medium-term

| # | Item | Effort | Value |
|---|------|--------|-------|
| 11 | ~~Multi-layer compression (snip + micro-compact)~~ ✅ Done | — | — |
| 12 | ~~Hook event expansion (SessionStart, FileChanged)~~ ✅ Done | — | — |
| 13 | ~~AskUserQuestion tool~~ ✅ Done | — | — |
| 14 | ~~ToolSearch / deferred loading~~ ✅ Done | — | — |
| 15 | ~~Background task execution (Bash run_in_background)~~ ✅ Done | — | — |
| 21 | ~~AgentTool kind=orchestrate (survives plan mode)~~ ✅ Done | — | — |
| 22 | ~~PromptBuilder cache ordering fix (cacheable first, env last)~~ ✅ Done | — | — |
| 23 | ~~PromptManager user override layers~~ ✅ Done | — | — |
| 24 | ~~Memory strategy prompt expansion (17 → 125 lines)~~ ✅ Done | — | — |

### Long-term

| # | Item | Effort | Value |
|---|------|--------|-------|
| 16 | Multi-agent team support (TaskCreate/Team swarms) | High | Medium |
| 17 | Git worktree isolation for agents | ✅ Landed (Phase 15) | — |
| 18 | ~~NotebookEdit tool~~ | ⛔ Out of scope | — |
| 19 | LSP integration | High | Low |
| 20 | Feature flag framework | Medium | Low |
| 25 | SleepTool (async wait without shell) | Low | Low — useful when KAIROS-style async needed |
| 26 | task_budget cross-compaction tracking | Medium | Low — CC beta, not essential yet |
| 27 | MCP ListMcpResources / ReadMcpResource | Low | Low — CC still TBD for async agents |

---

## 6. DeepCLI Advantages

Areas where DeepCLI's design is ahead or different by intent:

- **Daemon/client split** — independent upgrade, process isolation,
  multi-frontend (CLI + future Web)
- **Multi-provider** — OpenAI-compatible + MiniMax + Anthropic from
  day one; Claude Code is Anthropic-only
- **Explicit memory system** — structured types, scopes, relevance
  ranking; more transparent than SDK-managed
- **MCP transport breadth** — 4 transports including in-process and
  WebSocket; Claude Code has more but DeepCLI covers the key ones
- **ConcurrencyHint + concurrency_key()** — more expressive than
  binary `isConcurrencySafe()`
- **Plugin architecture** — everything is extensible via config;
  Claude Code hard-codes most tools
