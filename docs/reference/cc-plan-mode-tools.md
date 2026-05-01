# CC Plan Mode — Tool Availability

**Source**: `src/tools/*/` — `isReadOnly()` method + `src/utils/messages.ts:3227`

CC does **not** filter tools from the schema in plan mode.
All tools remain visible to the LLM. Restriction is purely prompt-level:

> "Plan mode is active. The user indicated that they do not want you to execute yet
> -- you MUST NOT make any edits (with the exception of the plan file mentioned
> below), run any non-readonly tools (including changing configs or making commits),
> or otherwise make any changes to the system."

The `isReadOnly()` method on each tool determines whether it is considered
read-only. Default (not defined) is `false`.

---

## Read-only (`isReadOnly` = true)

LLM is allowed to call these in plan mode.

| Tool | Notes |
|------|-------|
| `FileRead` | |
| `Glob` | |
| `Grep` | |
| `WebFetch` | |
| `WebSearch` | |
| `ToolSearch` | |
| `TaskList` | |
| `TaskGet` | |
| `CronList` | |
| `BriefTool` | |
| `LSPTool` | |
| `ReadMcpResource` | |
| `ListMcpResources` | |
| `SyntheticOutput` | |
| `EnterPlanMode` | |
| `RemoteTrigger` | `true` when `action === 'list' \| 'get'` |
| `Config` | `true` when reading (no `value` arg) |
| `SendMessage` | `true` when `message` is a string |

---

## Non-readonly (`isReadOnly` = false or not defined)

Schema still visible in plan mode; prompt instructs LLM not to call these.
Permission check at call-time provides defense-in-depth.

| Tool | `isReadOnly` |
|------|-------------|
| `Bash` | not defined (default `false`) |
| `FileEdit` | not defined (default `false`) |
| `FileWrite` | not defined (default `false`) |
| `NotebookEdit` | not defined (default `false`) |
| `TodoWrite` | not defined (default `false`) |
| `Agent` | not defined (default `false`) |
| `AskUserQuestion` | not defined (default `false`) |
| `TaskCreate` | not defined (default `false`) |
| `TaskUpdate` | not defined (default `false`) |
| `TaskOutput` | not defined (default `false`) |
| `TaskStop` | not defined (default `false`) |
| `Skill` | not defined (default `false`) |
| `EnterWorktree` | not defined (default `false`) |
| `ExitWorktree` | not defined (default `false`) |
| `ExitPlanMode` | `false` (writes plan file to disk) |
| `REPL` | not defined (default `false`) |
| `TeamCreate` | not defined (default `false`) |
| `TeamDelete` | not defined (default `false`) |

---

## DeepCLI vs CC

### 核心差异

CC 在 plan mode 下**不过滤 schema**，所有工具对 LLM 可见，靠 system message 约束行为。
DeepCLI 在 plan mode 下**从 schema 移除** `_MUTATING_KINDS`（`edit`/`delete`/`move`/`execute`），LLM 完全看不到这些工具。

### DeepCLI plan mode 下可用工具（在 schema 中）

`_MUTATING_KINDS = {edit, delete, move, execute}` — 这些 kind 的工具被移除。

| 工具 | kind | plan mode |
|------|------|-----------|
| `FileRead` | `read` | ✓ 可见 |
| `Glob` | `search` | ✓ 可见 |
| `Grep` | `search` | ✓ 可见 |
| `WebFetch` | `read` | ✓ 可见 |
| `WebSearch` | `read` | ✓ 可见 |
| `ToolSearch` | `think` | ✓ 可见 |
| `TaskOutput` | `read` | ✓ 可见 |
| `CronList` | `read` | ✓ 可见 |
| `memory_list` | `search` | ✓ 可见 |
| `memory_search` | `search` | ✓ 可见 |
| `Agent` | `orchestrate` | ✓ 可见（不在 _MUTATING_KINDS）|
| `AskUserQuestion` | `other` | ✓ 可见 |
| `EnterPlanMode` | `other` | ✓ 可见 |
| `ExitPlanMode` | `other` | ✓ 可见 |
| `Skill` | `other` | ✓ 可见 |
| `TodoWrite` | `other` | ✓ 可见 |
| `Bash` | `execute` | ✗ 移除 |
| `FileEdit` | `edit` | ✗ 移除 |
| `FileWrite` | `edit` | ✗ 移除 |
| `CronCreate` | `execute` | ✗ 移除 |
| `CronDelete` | `execute` | ✗ 移除 |
| `Monitor` | `execute` | ✗ 移除 |
| `SendMessage` | `execute` | ✗ 移除 |
| `TaskStop` | `execute` | ✗ 移除 |
| `EnterWorktree` | `execute` | ✗ 移除 |
| `ExitWorktree` | `execute` | ✗ 移除 |
| `REPL` | `execute` | ✗ 移除 |
| `memory_write` | `edit` | ✗ 移除 |
| `memory_append` | `edit` | ✗ 移除 |
| `memory_delete` | `edit` | ✗ 移除 |

### 与 CC 的对比

| 工具 | CC plan mode | DeepCLI plan mode | 差异 |
|------|-------------|-------------------|------|
| `FileRead`/`Glob`/`Grep` | ✓ | ✓ | 一致 |
| `WebFetch`/`WebSearch` | ✓ | ✓ | 一致 |
| `ToolSearch` | ✓ | ✓ | 一致 |
| `CronList` | ✓ | ✓ | 一致 |
| `Agent` | ✓（schema visible, isReadOnly=false）| ✓（kind=orchestrate）| 一致 |
| `AskUserQuestion` | ✓（isReadOnly=false, but visible）| ✓（kind=other）| 一致 |
| `ExitPlanMode` | ✓（isReadOnly=false, but visible）| ✓（kind=other）| 一致 |
| `Skill` | ✓（isReadOnly=false, but visible）| ✓（kind=other）| 一致 |
| `TodoWrite` | ✓（schema visible）| ✓（kind=other）| CC 可见但 prompt 约束；DeepCLI 可见且无约束 |
| `Bash` | ✓（schema visible，prompt 约束不调用）| ✗ 从 schema 移除 | 差异 |
| `FileEdit`/`FileWrite` | ✓（schema visible，prompt 约束不调用）| ✗ 从 schema 移除 | 差异 |
| `SendMessage` | ✓（isReadOnly 部分 true）| ✗ 从 schema 移除 | 差异 |
| `EnterWorktree`/`ExitWorktree` | ✓（schema visible）| ✗ 从 schema 移除 | 差异 |
| `TaskOutput`/`TaskStop` | ✓（schema visible）| `TaskOutput` ✓ / `TaskStop` ✗ | 差异 |
