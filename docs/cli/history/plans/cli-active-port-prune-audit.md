# CLI Active-Port Prune Audit

Status: prune applied; active-port runtime assets removed
Date: 2026-04-29

Applied: 2026-04-29

Result:

- Removed CLI-owned prompt markdown from `src/cli/src/active-port/coding-agent/prompts/`.
- Removed local edit/export/plugin/discovery/capability/memory/IPython/STT/runtime assets
  identified by this audit.
- Kept only renderer/UI-facing stubs where the active-port TUI still expects a
  shape, with local runtime behavior disabled or redirected to kernel-owned
  responsibility.
- Reduced `src/cli/active-port-manifest.json` to 243 registered files.
- Verification: `bun run scripts/check_active_port.ts`, `bunx tsc -p
  tsconfig.json --noEmit`, `bun run tests/run_all.ts`, and `bun run
  src/cli/tests/probe_phase_b_pty.ts` all passed.

## Goal

Identify OMP-copied CLI files that are likely redundant now that DeepCLI CLI is
defined as a pure ACP/TUI client and all model/runtime behavior belongs in the
kernel.

This document is intentionally an audit, not a deletion patch. The next change
should delete in small batches and run the real CLI probe after each batch.

## Boundary

Desired ownership:

- CLI owns terminal UI, keyboard/input handling, ACP connection, session picker,
  and rendering of ACP events.
- Kernel owns model calls, prompts, tools, memory, compaction, planning, title
  generation, and any local agent runtime behavior.

Therefore, copied OMP files are suspicious when they implement local model
runtime, local tools, prompt text, plugin/runtime discovery, local memory, or
local export/debug systems unrelated to the current ACP UI.

## Scan Method

Entrypoints used:

- `src/cli/src/main.ts`
- `src/cli/src/active-port/coding-agent/modes/interactive-mode.ts`

The second entrypoint is required because `src/cli/src/modes/interactive.ts`
loads it indirectly with `new Function("specifier", "return import(specifier)")`.

Original resolver assumptions:

- TS/Bun static imports and exports.
- Literal dynamic imports.
- The then-current `tsconfig` aliases for `@oh-my-pi/*`.

Scan result:

- Active-port files total: 458
- Static reachable active-port files: 295
- Static unreachable active-port files: 163
- All four compat shims are still reachable.

First correction applied:

- Removed `@oh-my-pi/*` path aliases from `src/cli/tsconfig.json`.
- Renamed compat shim files from `pi-*.ts` to DeepCLI-owned names:
  `agent-core.ts`, `ai.ts`, `natives.ts`, `utils.ts`.
- Rewrote source imports from `@oh-my-pi/*` to `@/...` paths.
- Remaining `@oh-my-pi/*` import specifiers in `src/cli/src` and
  `src/cli/tests`: 0.

Second correction applied:

- Kept the OMP event-controller ordering model explicit: block placement is
  determined by first creation, not by later lifecycle updates.
- `message_update` now processes existing `toolCall` blocks before mounting
  visible assistant text. This preserves the order where `tool_call` starts
  before the final answer streams.
- `tool_call_update` continues to update the already-created tool block instead
  of creating another block below the assistant answer.
- The PTY probe now rejects both stale `pending <tool>` and stale
  `success <tool>` blocks after the answer marker.

Important limitation: this is a static graph. Deletion still requires a CLI PTY
probe because the OMP port contains some dynamic UI flows.

## First-Pass Redundant Candidates

These files were not reachable from the current CLI runtime graph.

### Local Edit Runtime

The kernel owns editing tools. These OMP local edit implementations should not
live in the CLI unless a UI component directly needs a renderer-only helper.

```text
src/active-port/coding-agent/edit/apply-patch/index.ts
src/active-port/coding-agent/edit/apply-patch/parser.ts
src/active-port/coding-agent/edit/diff.ts
src/active-port/coding-agent/edit/line-hash.ts
src/active-port/coding-agent/edit/modes/apply-patch.lark
src/active-port/coding-agent/edit/modes/apply-patch.ts
src/active-port/coding-agent/edit/modes/chunk.ts
src/active-port/coding-agent/edit/modes/hashline.ts
src/active-port/coding-agent/edit/modes/patch.ts
src/active-port/coding-agent/edit/modes/replace.ts
src/active-port/coding-agent/edit/normalize.ts
src/active-port/coding-agent/edit/read-file.ts
src/active-port/coding-agent/edit/renderer.ts
src/active-port/coding-agent/edit/streaming.ts
```

### Local Export And Debug Adjacent Runtime

```text
src/active-port/coding-agent/export/html/index.ts
src/active-port/coding-agent/export/html/template.css
src/active-port/coding-agent/export/html/template.generated.ts
src/active-port/coding-agent/export/html/template.html
src/active-port/coding-agent/export/html/template.js
src/active-port/coding-agent/export/html/template.macro.ts
src/active-port/coding-agent/export/html/vendor/highlight.min.js
src/active-port/coding-agent/export/html/vendor/marked.min.js
src/active-port/coding-agent/export/ttsr.ts
```

### Local Plugin Runtime

Kernel/plugin architecture should own this, not the CLI active-port copy.

```text
src/active-port/coding-agent/extensibility/plugins/doctor.ts
src/active-port/coding-agent/extensibility/plugins/git-url.ts
src/active-port/coding-agent/extensibility/plugins/index.ts
src/active-port/coding-agent/extensibility/plugins/installer.ts
src/active-port/coding-agent/extensibility/plugins/loader.ts
src/active-port/coding-agent/extensibility/plugins/manager.ts
src/active-port/coding-agent/extensibility/plugins/parser.ts
src/active-port/coding-agent/extensibility/plugins/types.ts
```

### Internal URL Runtime

Only `internal-urls/index.ts` is statically reachable. The rest looks like OMP
local resource handling and should be removed or replaced by ACP resource
handling.

```text
src/active-port/coding-agent/internal-urls/agent-protocol.ts
src/active-port/coding-agent/internal-urls/artifact-protocol.ts
src/active-port/coding-agent/internal-urls/jobs-protocol.ts
src/active-port/coding-agent/internal-urls/json-query.ts
src/active-port/coding-agent/internal-urls/local-protocol.ts
src/active-port/coding-agent/internal-urls/mcp-protocol.ts
src/active-port/coding-agent/internal-urls/memory-protocol.ts
src/active-port/coding-agent/internal-urls/parse.ts
src/active-port/coding-agent/internal-urls/pi-protocol.ts
src/active-port/coding-agent/internal-urls/router.ts
src/active-port/coding-agent/internal-urls/rule-protocol.ts
src/active-port/coding-agent/internal-urls/skill-protocol.ts
src/active-port/coding-agent/internal-urls/types.ts
```

### Local Python / IPython Runtime

```text
src/active-port/coding-agent/ipy/cancellation.ts
src/active-port/coding-agent/ipy/executor.ts
src/active-port/coding-agent/ipy/kernel.ts
src/active-port/coding-agent/ipy/modules.ts
src/active-port/coding-agent/ipy/prelude.py
src/active-port/coding-agent/ipy/prelude.ts
src/active-port/coding-agent/ipy/runtime.ts
```

### Prompt Assets

Most prompt assets are unreachable. More importantly, CLI should not own model
prompt text. The reachable prompt files are a stronger smell, tracked below in
"reachable but should be removed".

```text
src/active-port/coding-agent/prompts/agents/*
src/active-port/coding-agent/prompts/compaction/*
src/active-port/coding-agent/prompts/tools/*
src/active-port/coding-agent/prompts/ci-green-request.md
src/active-port/coding-agent/prompts/review-request.md
src/active-port/coding-agent/prompts/system/agent-creation-architect.md
src/active-port/coding-agent/prompts/system/agent-creation-user.md
src/active-port/coding-agent/prompts/system/auto-handoff-threshold-focus.md
src/active-port/coding-agent/prompts/system/btw-user.md
src/active-port/coding-agent/prompts/system/commit-message-system.md
src/active-port/coding-agent/prompts/system/custom-system-prompt.md
src/active-port/coding-agent/prompts/system/eager-todo.md
src/active-port/coding-agent/prompts/system/file-operations.md
src/active-port/coding-agent/prompts/system/handoff-document.md
src/active-port/coding-agent/prompts/system/plan-mode-active.md
src/active-port/coding-agent/prompts/system/plan-mode-reference.md
src/active-port/coding-agent/prompts/system/plan-mode-subagent.md
src/active-port/coding-agent/prompts/system/plan-mode-tool-decision-reminder.md
src/active-port/coding-agent/prompts/system/subagent-submit-reminder.md
src/active-port/coding-agent/prompts/system/subagent-system-prompt.md
src/active-port/coding-agent/prompts/system/subagent-user-prompt.md
src/active-port/coding-agent/prompts/system/summarization-system.md
src/active-port/coding-agent/prompts/system/system-prompt.md
src/active-port/coding-agent/prompts/system/ttsr-interrupt.md
src/active-port/coding-agent/prompts/system/web-search.md
```

### Speech-To-Text Runtime

Only `stt/index.ts` is statically reachable. The implementation files are not.
If STT is not a DeepCLI CLI requirement, remove the whole feature path.

```text
src/active-port/coding-agent/stt/downloader.ts
src/active-port/coding-agent/stt/recorder.ts
src/active-port/coding-agent/stt/setup.ts
src/active-port/coding-agent/stt/stt-controller.ts
src/active-port/coding-agent/stt/transcribe.py
src/active-port/coding-agent/stt/transcriber.ts
```

### Misc Unreachable UI/Support Files

```text
src/active-port/coding-agent/modes/components/plugin-settings.ts
src/active-port/coding-agent/modes/components/settings-defs.ts
src/active-port/coding-agent/modes/components/status-line/index.ts
src/active-port/coding-agent/modes/components/theme-selector.ts
src/active-port/coding-agent/modes/components/welcome-logo.txt
src/active-port/coding-agent/modes/theme/theme-schema.json
src/active-port/coding-agent/priority.json
src/active-port/coding-agent/tui/code-cell.ts
src/active-port/coding-agent/tui/file-list.ts
src/active-port/coding-agent/tui/output-block.ts
src/active-port/coding-agent/tui/status-line.ts
src/active-port/coding-agent/tui/tree-list.ts
src/active-port/coding-agent/tui/types.ts
src/active-port/coding-agent/tui/utils.ts
src/active-port/coding-agent/utils/commit-message-generator.ts
src/active-port/coding-agent/utils/edit-mode.ts
src/active-port/coding-agent/utils/file-display-mode.ts
src/active-port/coding-agent/utils/file-mentions.ts
src/active-port/coding-agent/utils/markit.ts
src/active-port/coding-agent/utils/shell-snapshot.ts
src/active-port/coding-agent/utils/tool-choice.ts
src/active-port/coding-agent/utils/tools-manager.ts
```

## Reachable But Architecturally Suspicious

These files are still reachable today, but they violate the desired boundary or
keep the `@oh-my-pi/*` compatibility layer alive.

### Local Model/Prompt/Memory

```text
src/active-port/coding-agent/memories/index.ts
src/active-port/coding-agent/memories/storage.ts
src/active-port/coding-agent/prompts/memories/consolidation.md
src/active-port/coding-agent/prompts/memories/read-path.md
src/active-port/coding-agent/prompts/memories/stage_one_input.md
src/active-port/coding-agent/prompts/memories/stage_one_system.md
src/active-port/coding-agent/prompts/system/plan-mode-approved.md
src/active-port/coding-agent/prompts/system/title-system.md
src/active-port/coding-agent/utils/title-generator.ts
```

Action: remove or redirect these flows to kernel before deleting the files.

### Local Discovery/Capabilities

All files under these reachable groups should be questioned because a pure ACP
client should not discover Claude/Codex/Gemini/OpenCode config and plugins for
the model runtime:

```text
src/active-port/coding-agent/capability/*
src/active-port/coding-agent/discovery/*
src/active-port/coding-agent/config/model-*.ts
src/active-port/coding-agent/extensibility/slash-commands.ts
```

Action: decide which are UI-only conveniences, if any. Everything else belongs
in kernel or should be deleted.

### `@oh-my-pi/*` Shims

Still reachable:

```text
src/compat/pi-agent-core.ts
src/compat/pi-ai.ts
src/compat/pi-natives.ts
src/compat/pi-utils.ts
```

Action: remove `pi-ai` and `pi-agent-core` first by deleting local model/runtime
flows. Then rename `pi-tui`, `pi-utils`, and `pi-natives` imports to DeepCLI
owned modules or inline the small utility surface.

## Import Review

The file-level prune list is not enough. The original import graph carried OMP
ownership through module specifiers, aliases, and test dependencies.

Original import scan totals across `src/cli/src` and `src/cli/tests`:

```text
@oh-my-pi/* imports:                  209
DeepCLI imports into active-port app:  12
DeepCLI imports into active-port TUI:   2
DeepCLI @/* imports:                  49
relative imports:                    988
external/node imports:               191
```

After the first correction, `@oh-my-pi/*` imports are at 0. The remaining
problem is semantic, not namespace-only: many imports now point at
`@/compat/*` or `@/active-port/coding-agent/*`, which still need pruning or
promotion into DeepCLI-owned modules.

Post-correction import scan:

```text
@oh-my-pi/* imports:                   0
@/compat/* imports:                  148
DeepCLI imports into active-port app:  12
DeepCLI imports into active-port TUI:   2
```

### Import Policy Target

Allowed final-state imports:

```text
node:*
bun:* only where Bun runtime APIs are intentionally required
package deps declared in src/cli/package.json
@/acp/*
@/config/*
@/modes/*
@/permissions/*
@/session*
@/sessions/*
@/startup/*
@/tui/*
relative imports within an owned DeepCLI module
```

Disallowed final-state imports:

```text
@oh-my-pi/*
@/active-port/coding-agent/*
../src/active-port/coding-agent/*
direct imports from tests into copied OMP app internals
CLI imports of local model/runtime/prompt/tool/memory/discovery code
```

Transitional imports:

```text
@/active-port/tui/*
```

These are acceptable only while the TUI package is being renamed into a DeepCLI
owned module. They should not keep the `active-port` name indefinitely.

### Top-Level DeepCLI Files Importing Copied OMP App Code

These imports are higher priority than imports inside copied files, because they
make the DeepCLI-owned CLI depend directly on the OMP app tree.

```text
src/permissions/controller.ts
  @/active-port/coding-agent/modes/components/hook-editor.js
  @/active-port/coding-agent/modes/components/hook-input.js
  @/active-port/coding-agent/modes/components/hook-selector.js

src/session/agent-session-adapter.ts
  @/active-port/coding-agent/config/settings.js
  @/active-port/coding-agent/session/session-manager.js
  @/active-port/coding-agent/session/agent-session.js

src/startup/theme.ts
  @/active-port/coding-agent/modes/theme/theme.js

src/modes/interactive.ts
  dynamic import: ../active-port/coding-agent/modes/interactive-mode.ts

src/tui/index.ts
  @/active-port/tui/index.js
```

Action: introduce DeepCLI-owned facades or move the needed UI components under
`src/cli/src/tui` / `src/cli/src/modes`. The final top-level CLI should not
import `active-port/coding-agent` at all.

### Former `@oh-my-pi/pi-ai` And `@oh-my-pi/pi-agent-core`

These are the most important imports to remove because they imply local model
and agent-runtime ownership in the CLI.

Current module names after the first correction:

```text
@/compat/ai.js
@/compat/agent-core.js
```

Current reachable examples:

```text
src/active-port/coding-agent/modes/interactive-mode.ts
src/active-port/coding-agent/modes/controllers/input-controller.ts
src/active-port/coding-agent/modes/controllers/event-controller.ts
src/active-port/coding-agent/modes/controllers/selector-controller.ts
src/active-port/coding-agent/modes/theme/theme.ts
src/active-port/coding-agent/session/agent-session.ts
src/active-port/coding-agent/session/messages.ts
src/active-port/coding-agent/memories/index.ts
src/active-port/coding-agent/utils/title-generator.ts
```

Action: replace these types with DeepCLI ACP-facing types or local UI-only view
models. Delete any code path that calls `completeSimple` or constructs local
agent/model requests.

### Former `@oh-my-pi/pi-tui`

Most `pi-tui` imports are UI primitives. This dependency is conceptually
allowed, but the namespace is wrong for an independent project.

Action: rename the module boundary:

```text
@/tui/index.js    ->  @/tui
src/active-port/tui -> src/tui
```

Do this after runtime imports have been reduced; otherwise the rename will move
too much copied OMP app code into the DeepCLI-owned tree.

### Former `@oh-my-pi/pi-utils`

This shim mixes harmless utilities with app identity and local runtime paths.
Every import needs classification.

Likely safe to move into DeepCLI utility modules:

```text
logger
isEnoent
tryParseJson
parseFrontmatter
formatNumber / formatDuration / formatBytes
getProjectDir / setProjectDir, if still CLI-owned
```

Likely runtime smell:

```text
getAgentDir
getAgentDbPath
getMemoriesDir
getPluginsDir
getToolsDir
prompt.render
postmortem
TempDir / ptree for local tool execution
```

Current module name after the first correction: `@/compat/utils.js`.

Action: split the utils shim into explicit DeepCLI modules instead of one broad
compat shim. Remove app-runtime path helpers from CLI.

### Former `@oh-my-pi/pi-natives`

Some native helpers are terminal/UI oriented and can stay after renaming:

```text
visible-width/string slicing helpers
keyboard parsing helpers
terminal image helpers, if still supported
clipboard/image helpers, if still supported
```

But imports used only by local runtime features should disappear with those
features:

```text
glob for file mention discovery
image conversion for local prompt attachments, unless ACP UI still needs it
work profiling/debug helpers
```

Current module name after the first correction: `@/compat/natives.js`.

Action: move terminal-only helpers under a DeepCLI native/TUI module; remove
runtime-only native helpers.

### Test Import Coupling

Several tests import copied OMP internals directly. This makes cleanup harder
because tests can keep dead modules artificially alive.

```text
tests/test_autocomplete_sort.ts
tests/test_input_controller_r4.ts
tests/test_session_selector_omp.ts
tests/test_status_line.ts
tests/test_theme_config.ts
tests/test_ui_golden_r5.ts
```

Action: rewrite tests to target DeepCLI-owned facades and user-visible CLI
behavior. Keep OMP parity tests only for the shrinking TUI renderer surface.

## Safe Deletion Order

1. Delete unreachable prompt/tool prompt assets and run `bunx tsc -p
   tsconfig.json --noEmit` plus the CLI PTY probe.
2. Delete unreachable local edit/export/ipy/internal-url/plugin/STT files in
   separate batches, with the same probe after each batch.
3. Remove reachable local model/title/memory/plan prompt flows by either
   deleting the UI path or routing to kernel.
4. Remove `@oh-my-pi/pi-ai` and `@oh-my-pi/pi-agent-core` aliases.
5. Rename remaining TUI/utility imports away from `@oh-my-pi/*`.

## Required Verification

For every deletion batch:

```bash
cd src/cli
bunx tsc -p tsconfig.json --noEmit
bun run tests/run_all.ts
bun run tests/probe_phase_b_pty.ts
```

Also manually exercise:

- cold CLI launch
- prompt submission
- tool lifecycle rendering
- session list/picker
- permission prompt
- Ctrl+C / cancel path
