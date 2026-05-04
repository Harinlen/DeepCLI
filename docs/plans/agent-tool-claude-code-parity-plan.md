# AgentTool Claude Code Parity Plan

Status: **future plan** — split from the 2026-05-04 practical stats slice.

## Current Practical Slice

DeepCLI currently keeps foreground sub-agent transcript events private and
returns the child final text through the parent `Agent` tool result.  The
practical stats slice adds structured Agent result metadata:

- child tool-use count
- child input / output / total tokens
- child wall-clock duration

The CLI consumes that metadata only inside the `Agent` tool card, rendering a
collapsed line like:

```text
Done (2 tool uses · 15.7k tokens · 13s) (ctrl+o to expand)
```

This does not attempt to reproduce Claude Code's full sub-agent progress UI.

## Claude Code References

Reference root: `./resolve-ref.sh claude-code`

- `src/tools/AgentTool/AgentTool.tsx`
  - sync sub-agent lifecycle
  - progress forwarding via `onProgress`
  - token counting from assistant messages
  - SDK task notification usage payload
- `src/tools/AgentTool/UI.tsx`
  - `renderToolResultMessage(...)`
  - `renderToolUseProgressMessage(...)`
  - `calculateAgentStats(...)`
  - `renderGroupedAgentToolUse(...)`
  - `extractLastToolInfo(...)`
- `src/tools/AgentTool/agentToolUtils.ts`
  - lifecycle helper functions and final result shaping
- `src/tools/AgentTool/runAgent.ts`
  - child event filtering and transcript collection

## Target Behavior

### Running Agent Card

While the child is still running, the parent UI should render only the `Agent`
tool row, not child messages as top-level parent output.  The row should show:

- current state: initializing / in progress
- child tool-use count
- latest known token count when available
- latest child tool activity, e.g. `WebSearch`, `FileRead`, `Bash`
- compact fallback when terminal height is constrained

### Completed Agent Card

The collapsed completed card should match Claude Code's semantic output:

```text
Done (N tool uses · X tokens · Ys)
```

Expanded mode should show the captured child transcript and final child answer
inside the Agent tool UI, not as parent assistant content.

### Grouped / Multiple Agent Cards

If multiple `Agent` tool calls are active in the same parent turn, the CLI
should be able to group them and show one progress line per child, with:

- agent type / name / description
- resolved / running / failed state
- tool-use count
- token count
- latest activity

### Background Agent Compatibility

Foreground, background, and mid-flight backgrounded Agent paths should expose
the same progress summary shape so future SDK / Probe / Home Screen consumers
do not need per-mode special cases.

## Kernel Work

1. Introduce a structured `AgentProgress` event payload or ACP `_meta` update
   for child progress frames.
2. Count child `ToolCallStart` events and assistant usage updates during the
   child query, not only at `SubAgentEnd`.
3. Include cache read / cache write token categories once provider usage
   normalization exposes them for child turns.
4. Preserve child transcript as structured progress data for expanded UI,
   while keeping it out of parent LLM-visible history except through the final
   Agent tool result.
5. Emit consistent summary metadata for foreground and background Agent tasks.

## CLI Work

1. Store `progressMessagesForMessage`-style data per parent `Agent` tool call.
2. Render running progress from child activity instead of only final stats.
3. Add expanded Agent transcript mode behind the existing `ctrl+o` affordance.
4. Implement multiple-Agent grouped rendering, using the active-port tool
   renderer conventions instead of importing kernel internals.
5. Keep parent assistant transcript clean: child text, thoughts, and child tool
   calls must not become top-level parent messages.

## Verification

- Kernel unit tests for progress metadata, final stats, and transcript privacy.
- ACP mapper tests for start/progress/end/result metadata shapes.
- CLI adapter tests for private child event suppression plus progress storage.
- CLI golden frames for:
  - running Agent
  - completed collapsed Agent
  - expanded transcript Agent
  - multiple grouped Agents
- Real closure-seam probe:
  - live Kernel
  - CLI prompt that forces `Agent`
  - child uses at least one real tool
  - parent window shows only Agent card + parent summary
  - Agent card reports non-zero tool uses, tokens, and duration
