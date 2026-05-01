# Prompt Engineering

Prompt text is split across `.txt` files loaded by `PromptManager` and
tool-description strings embedded in `tools/builtin/*.py`.  Both
closely track Claude Code's prompt structure.

**CC reference**: `src/constants/prompts.ts` (section generators) and
`src/tools/*/prompt.ts` (tool descriptions).

## System Prompt Layout

Mirrors CC's `getSystemPrompt()` (prompts.ts:444-577).  Static
(cacheable) prefix, dynamic (per-query) suffix:

```
[STATIC]                                      ← single PromptSection, cache=True
1. identity.txt         (security posture, URL prohibition)
2. system.txt           (# System — markdown, permissions, tags,
                         hooks, context compression)
3. doing_tasks.txt      (# Doing tasks — software eng, OWASP,
                         no over-engineering, no premature abstraction)
4. actions_with_care.txt (# Executing actions with care —
                          reversibility, blast radius, examples)
5. using_tools.txt      (# Using your tools — dedicated tools > Bash,
                         TodoWrite, parallel vs sequential)
6. tone_and_style.txt   (# Tone and style — emojis, file refs,
                         GitHub links)
7. output_efficiency.txt (# Output efficiency — concise, answer-first)

[DYNAMIC]                                     ← per-query PromptSections
8.  Environment context    (built at runtime — cwd, platform, shell,
                            git repo, date/time; when a model is pinned
                            the active model id is appended using CC's
                            null-marketing-name fallback phrasing)
9.  Git context            (from GitManager, session-level cache)
10. AGENTS.md contents     (TODO — project + user + global instructions)
11. Memory index + strategy (from MemoryManager)
12. Skills listing         (from SkillManager)
13. git_commit_pr.txt      (# Committing + # Creating PRs — full workflow)
14. summarize_tool_results.txt (tool-result clearing reminder)
15. Plan mode instructions (injected by Orchestrator when active)
```

All `.txt` files live in `src/kernel/kernel/prompts/default/orchestrator/`.
Loaded by `PromptManager`, assembled by `PromptBuilder`.

## Prompt File Index

Source column:
- **Verbatim** — character-for-character from Claude Code (external user path). Do not edit without noting drift.
- **Adapted** — based on CC but with DeepCLI-specific names/changes.
- **DeepCLI** — no direct CC equivalent.

### System prompt sections (orchestrator/)

| File | CC equivalent | Source |
|------|---------------|--------|
| `identity.txt` | `getSimpleIntroSection()` + `CYBER_RISK_INSTRUCTION` | **Verbatim** — identical to CC external user path |
| `system.txt` | `getSimpleSystemSection()` | **Verbatim** — all 6 bullet points |
| `doing_tasks.txt` | `getSimpleDoingTasksSection()` | **Verbatim** — CC external user path (no ant-only items) |
| `actions_with_care.txt` | `getActionsSection()` | **Verbatim** |
| `using_tools.txt` | `getUsingYourToolsSection()` | **Adapted** — hardcodes tool names (FileRead, FileEdit, etc.) |
| `tone_and_style.txt` | `getSimpleToneAndStyleSection()` | **Verbatim** — all 5 items |
| `output_efficiency.txt` | `getOutputEfficiencySection()` | **Verbatim** — CC external user path |
| `git_commit_pr.txt` | `getCommitAndPRInstructions()` (BashTool/prompt.ts) | **Verbatim** — full commit + PR workflow |
| `summarize_tool_results.txt` | `SUMMARIZE_TOOL_RESULTS_SECTION` | **Verbatim** |
| `base.txt` | *(legacy, superseded)* | **Deprecated** — kept for backward compat, not loaded by PromptBuilder |
| `plan_mode.txt` | `EnterPlanModeTool.ts` | **Verbatim** |
| `plan_mode_exit.txt` | `ExitPlanModeTool.ts` | **Adapted** |
| `plan_mode_reentry.txt` | — | **DeepCLI** |
| `plan_mode_sparse.txt` | — | **DeepCLI** |
| `compact_system.txt` | — | **DeepCLI** |
| `compact_prefix.txt` | — | **DeepCLI** |
| `compact_fallback.txt` | — | **DeepCLI** |

### Tool descriptions (prompts/default/tools/*.txt)

Every built-in tool description lives in this directory.  Subclasses
set `description_key = "tools/<name>"`; the `Tool.get_description()`
hook resolves the text from PromptManager at `to_schema()` time.

| File | CC equivalent | Source |
|------|---------------|--------|
| `tools/bash.txt` | `BashTool/prompt.ts:getSimplePrompt()` | **Verbatim** — full instructions, tool preference, git, sleep |
| `tools/file_read.txt` | `FileReadTool/prompt.ts:renderPromptTemplate()` | **Verbatim** |
| `tools/file_write.txt` | `FileWriteTool/prompt.ts` | **Verbatim** |
| `tools/file_edit.txt` | `FileEditTool/prompt.ts` | **Verbatim** |
| `tools/glob.txt` | `GlobTool/prompt.ts:DESCRIPTION` | **Verbatim** |
| `tools/grep.txt` | `GrepTool/prompt.ts:getDescription()` | **Verbatim** |
| `tools/agent.txt` | `AgentTool/prompt.ts:getPrompt()` | **Verbatim** — includes "Writing the prompt" section |
| `tools/skill.txt` | `SkillTool/prompt.ts` | **Verbatim** |
| `tools/ask_user_question.txt` | `AskUserQuestionTool/prompt.ts` | **Verbatim** |
| `tools/todo_write.txt` | `TodoWriteTool/prompt.ts` | **Verbatim** — 4+4 examples + Task Breakdown + activeForm |
| `tools/cron_create.txt` | `ScheduleCronTool/prompt.ts:buildCronCreatePrompt()` | **Adapted** — path (`kernel.db`) + `DEFAULT_MAX_AGE_DAYS` (7) substituted; DeepCLI-specific parameters appendix |
| `tools/cron_delete.txt` | `buildCronDeletePrompt()` | **Verbatim (short)** |
| `tools/cron_list.txt` | `buildCronListPrompt()` | **Verbatim (short)** |
| `tools/enter_plan_mode.txt` | `EnterPlanModeTool/prompt.ts` | **Verbatim** — 7 criteria + What Happens + GOOD/BAD examples |
| `tools/exit_plan_mode.txt` | `ExitPlanModeTool/prompt.ts` | **Verbatim** — 3 Examples |
| `tools/enter_worktree.txt` | `EnterWorktreeTool/prompt.ts` | **Verbatim** — hooks fallback mentioned |
| `tools/exit_worktree.txt` | `ExitWorktreeTool/prompt.ts` | **Adapted** — tmux bullet removed (DeepCLI has no tmux UX) |
| `tools/web_search.txt` | `WebSearchTool/prompt.ts:getWebSearchPrompt()` | **Adapted** — US-only line dropped (multi-backend); `{month_year}` template rendered via `WebSearchTool.get_description()` |
| `tools/web_fetch.txt` | `WebFetchTool/prompt.ts:DESCRIPTION` | **Verbatim** |
| `tools/send_message.txt` | `SendMessageTool` | **DeepCLI** |
| `tools/monitor.txt` | — | **DeepCLI** |
| `tools/powershell.txt` | `PowerShellTool` | **DeepCLI** (thin shim) |
| `tools/task_output.txt` / `tools/task_stop.txt` | `TaskOutputTool` / `TaskStopTool` | **DeepCLI** |

### Memory prompts (memory/prompts/)

| File | CC equivalent | Source |
|------|---------------|--------|
| `memory_strategy.txt` | `memdir/memdir.ts` | **Adapted** |
| `extraction.txt` | `extractMemories/prompts.ts` | **Adapted** |
| `selection.txt` | — | **DeepCLI** |
| `consolidation.txt` | — | **DeepCLI** |

## Parity Status vs Claude Code

**System prompt**: 10 verbatim sections, 1 adapted, 4 DeepCLI-original.
**Tool descriptions**: 13 verbatim, 4 adapted, 5 DeepCLI-original — all migrated out of `.py` into `prompts/default/tools/*.txt` (Phase 1 CC alignment).
**Overall**: ~85% inherited from Claude Code.

### CC sections DeepCLI ports verbatim

Every section below uses CC's text character-for-character (module
DeepCLI variable substitution).  Drift is gated by a canary test under
`tests/kernel/orchestrator/` and/or a live probe in `scripts/probe_*.py`.

- **Identity / System / Doing tasks / Executing actions / Using tools / Tone and style / Output efficiency** — 7 static sections (Phase 1).
- **Session-specific guidance** — `getSessionSpecificGuidanceSection()` — per-turn bullets gated on the active tool snapshot (Phase 2).
- **Environment context** — `computeSimpleEnvInfo()` — minus marketing / cutoff lines (see below) (Phase 3).
- **Language preference** — `getLanguageSection()` — injected when `orchestrator.language` is set in `config.yaml` (Phase 5).
- **MCP dynamic instructions** — `getMcpInstructionsSection()` → `getMcpInstructions()` — per-server runtime guidance; `isMcpInstructionsDeltaEnabled()` delta-attachment branch deliberately not ported (DeepCLI has no attachment persistence layer; section is always `cache=False`) (Phase 4).

### CC sections DeepCLI does NOT yet port

- **Output styles** — `getOutputStyleSection()` — custom voice/format configurations
- **Scratchpad directory** — `getScratchpadInstructions()` — guide the model away from `/tmp`
- **Function result clearing** — `getFunctionResultClearingSection()` — microcompact system prompt
- **Verification agent** — adversarial verification subagent instruction
- **Proactive / autonomous mode** — `getProactiveSection()`

### CC env-context lines DeepCLI deliberately drops

`_build_env_context` emits a subset of CC's `computeSimpleEnvInfo`
(prompts.ts:651-710).  The following are **intentionally omitted**:

- **Marketing-name framing** (`You are powered by the model named X.
  The exact model ID is Y.` / `(with 1M context)` suffix) — low ROI,
  per-release maintenance burden, awkward under multi-provider.  We
  use CC's null-marketing-name fallback phrasing for every model.
- **Knowledge cutoff line** (`Assistant knowledge cutoff is Z.`) —
  only meaningful for Claude models; the WebSearch tool covers
  "post-cutoff" cases.
- **Product-marketing bullets** — `most recent Claude model family` /
  `Claude Code is available` / `Fast mode`.  DeepCLI is not Claude
  Code and is multi-provider.

See `docs/kernel/history/plans/prompt-alignment-with-cc.md` § Phase 3 for the full
rationale.

## AGENTS.md Discovery

Walk from cwd upward to filesystem root.  At each level look for
(in order): `AGENTS.md`, `MUSTANG.md` (legacy fallback),
`.mustang/AGENTS.md`, `.mustang/MUSTANG.md`.

Also load `~/.mustang/AGENTS.md` (user-global).

`@path/to/file.md` include directive (future): max 5 levels deep,
cycle-safe, missing files silently ignored.

## Context Compression

When the conversation nears the context window, run an LLM-summary
turn ("autoCompact") that produces a structured summary covering:

1. Primary request and intent
2. Key technical concepts
3. Files and code sections
4. Errors and fixes
5. Problem solving
6. All user messages
7. Pending tasks
8. Current work
9. Next step

The summary replaces older messages. Keep the most recent N
messages verbatim. Response must be **text only** (no tool calls).

See D15 for the full 4-layer compaction strategy.

## Adding a New Prompt

1. Create `src/kernel/kernel/prompts/default/<category>/<name>.txt`.
2. If it's a system prompt section, add the key to
   `_STATIC_SECTION_KEYS` in `prompt_builder.py`.
3. If it's a tool description, set `description_key = "tools/<name>"`
   on the `Tool` subclass; the default `Tool.get_description()` hook
   routes schema construction through `PromptManager`.
4. If it contains `{placeholders}`, override `get_description()` and
   call `self._prompt_manager.render(key, **kwargs)`  (WebSearchTool's
   `{month_year}` injection is the reference implementation).
5. Update the file index table above.

**Rule**: never write prompt text inline in `.py` files.  No
exceptions — tool descriptions used to be the exception but since the
Phase 1 CC-alignment migration every built-in tool stores its text in
`prompts/default/tools/<name>.txt`, with only a one-sentence fallback
literal in the Python class for testing / degraded-mode.

## Tool Descriptions

Every built-in tool sources its LLM-facing description from
`src/kernel/kernel/prompts/default/tools/<name>.txt` via its
`description_key` ClassVar (default path: `tools/<name>`).  The file
naming matches the Python module: `bash.py → tools/bash.txt`,
`todo_write.py → tools/todo_write.txt`, etc.

These should track CC's `src/tools/*/prompt.ts` as closely as possible —
most are verbatim or adapted with explicit DeepCLI-specific addenda
(e.g. `cron_create.txt` ends with a `## DeepCLI-specific parameters`
section documenting fields CC doesn't have).  Canary tests in
`tests/kernel/tools/test_cc_alignment.py` assert the CC ideas we care
about remain present.

Key convention: guide the LLM toward dedicated tools over BashTool
(`FileRead` instead of `cat`, `Grep` instead of `grep`, etc.).
