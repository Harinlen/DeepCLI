# CLI OMP-First 重构计划

**父计划**：
retired CLI plan,
[`phase-b-tui-migration.md`](phase-b-tui-migration.md),
[`phase-b-ui-alignment-repair.md`](phase-b-ui-alignment-repair.md),
[`phase-c-permissions.md`](phase-c-permissions.md)

**范围**：`src/cli/` TypeScript / Bun 客户端

**状态**：implemented — CLI active-port refactor applied

**实现状态（2026-04-28）**：R0-R6 已落地到 CLI active-port 路径。剩余风险集中在 documented adapter seams，尤其是 OMP extension runner 的全量服务面尚未有 DeepCLI ACP backing。

**优先级**：P0，应在继续增加 CLI UI 功能之前完成

## 问题

当前 CLI 名义上是 oh-my-pi 的 active-port，但太多 UI 行为是在 DeepCLI 的 shim 代码里实现的，而不是委托给复制过来的 OMP controller/component。

最近出现的症状：

- 最终 assistant 文本已经被 kernel 持久化，但 CLI 没有渲染出来。原因是 `DeepCLIAgentSessionAdapter` 发出异步 OMP 事件时，没有串行等待 listener 完成。
- `/session list` 通过 `showStatus()` 渲染多行列表，导致它和 editor/status line 冲突。
- tool-first 的 turn 中，最终 assistant 文本显示在 tool output 上方。原因是 DeepCLI 的 event shape 让 OMP `AssistantMessageComponent` 被过早 mount。

这些不是 OMP 核心渲染 bug，而是 adapter/bridge bug：DeepCLI 把 ACP 数据翻译成了不保留 OMP 假设的 shape/order，然后又通过 patch 复制来的 UI 代码、或写定制 UI 来补偿。

## 目标

让生产 CLI 变成 OMP-first：

```text
Kernel ACP
  -> DeepCLI ACP client/session service
  -> 窄 DeepCLI adapter layer
  -> 复制来的 OMP AgentSession state/events/controllers/components
  -> 复制来的 OMP TUI runtime
```

DeepCLI 代码负责 protocol、data-source、capability adaptation。
OMP 代码负责 rendering、layout、input lifecycle、selector behaviour、hook dialog lifecycle、chat component ordering。

## 非目标

- 不移植 OMP agent loop、provider runtime、model execution 或 local tool execution。
- 不允许 CLI 读取 kernel internals、SQLite、sidecar files 或 Python modules。CLI 继续只使用 ACP/WebSocket。
- 不把完整 OMP mirror 导入本 repo。active-port 仍然是有边界的 copied import graph，并由 manifest 约束。
- 不设计新的 DeepCLI visual system。
- 不继续通过在 `builtin-registry.ts`、`agent-session-adapter.ts` 或复制来的 controllers 中增加 DeepCLI-specific render paths 来修 UI，除非该改动明确属于 adapter shim。

## Source Of Truth

当前已知 OMP 路径：

```text
/home/saki/Documents/alex/oh-my-pi
```

重要 upstream 文件：

```text
packages/coding-agent/src/modes/interactive-mode.ts
packages/coding-agent/src/modes/controllers/input-controller.ts
packages/coding-agent/src/modes/controllers/event-controller.ts
packages/coding-agent/src/modes/controllers/command-controller.ts
packages/coding-agent/src/modes/controllers/selector-controller.ts
packages/coding-agent/src/modes/controllers/extension-ui-controller.ts
packages/coding-agent/src/session/agent-session.ts
packages/coding-agent/src/session/session-manager.ts
packages/coding-agent/src/session/session-storage.ts
packages/coding-agent/src/modes/components/*
packages/tui/src/**
```

当前 reference 已注册：

```text
./resolve-ref.sh oh-my-pi -> /home/saki/Documents/alex/oh-my-pi
OMP baseline commit: c73c18a1fb3e2f2225ca685f290ec67d326689bf
```

实现时应以该路径和 commit 作为 active-port 对比 baseline。如果 OMP repo 更新，需要在本计划或 sibling implementation note 中记录新的 baseline。

## 目标架构

### 允许 DeepCLI 拥有的层

| Layer | Responsibility |
|---|---|
| `src/cli/src/acp/client.ts` | JSON-RPC/ACP transport、response routing、kernel-initiated requests |
| `src/cli/src/session.ts` | 很薄的 ACP session wrapper：`prompt`、`execute_shell`、`execute_python`、cancel |
| `src/cli/src/sessions/service.ts` | ACP session list/load/create/rename/archive/delete |
| `src/cli/src/models/service.ts` | ACP model profile/provider/default operations |
| `src/cli/src/session/agent-session-adapter.ts` | 把 ACP stream reduce 成 OMP-like `AgentSession` state/events |
| `src/cli/src/permissions/*` | ACP permission request/result mapping，不负责 UI layout |
| `src/cli/src/config/*` and `startup/*` | CLI config、auth token resolution、kernel startup/session startup |

### 复制来的 OMP 拥有的层

| Layer | Responsibility |
|---|---|
| `active-port/tui/**` | Terminal runtime、components、editor/select-list primitives |
| `active-port/coding-agent/modes/interactive-mode.ts` | Main TUI layout/lifecycle |
| `active-port/coding-agent/modes/controllers/*` | Input、command、event、selector、extension UI lifecycles |
| `active-port/coding-agent/modes/components/*` | Assistant、tool、hook、selector、status line rendering |
| `active-port/coding-agent/slash-commands/*` | Command metadata 和 dispatch shape，前提是 upstream 本身拥有这部分 |

除非 upstream code path 会直接执行 OMP-only side effects，并且这些 side effects 必须替换成 ACP adapters，否则 DeepCLI 不应该向复制来的 OMP controllers 添加 layout policy。

## 当前 Drift Inventory

这张表是第一轮实现 checklist。编码前需要逐项和 upstream 对齐确认。

| Surface | Current DeepCLI state | Desired OMP-first state |
|---|---|---|
| `DeepCLIAgentSessionAdapter` | 手动构造 messages/events；已有 async listener ordering fix | 保留为 reducer，但它应输出 OMP 期望的 event/state sequence，让复制来的 controllers 不需要 DeepCLI ordering patches |
| `builtin-registry.ts` `/session list` | 手写 session list 到 chat/status | 委托给 OMP selector/command path；DeepCLI 只提供 ACP-backed session service |
| `EventController` | 本地 patch：避免过早 mount 空 assistant component | 优先通过 adapter event semantics 让 upstream controller 不改即可工作；只有 upstream 也有相同 lazy-mount invariant 时才保留 local patch |
| Permission UI | custom controller 与复制来的 hook components 混用 | Permission controller 只做 ACP request/result mapping；component hosting 使用 OMP `ExtensionUiController` lifecycle |
| Session selector | 有 `SessionSelectorComponent`，但它是 DeepCLI adapter，不是 upstream selector flow | 使用 OMP selector controller flow，并接 ACP session data source；storage/session file assumptions 放到 service interface 后面适配 |
| `src/cli/src/modes/interactive.ts` | 旧 DeepCLI interactive wrapper 仍存在 | 当复制来的 OMP mode 成为权威生产路径后，将其降级为 bootstrap/fallback 或移除 |
| Status output | 某些 command 用 `showStatus()` 显示 durable/multiline content | status 只用于一行 transient state；durable output 使用 OMP chat components/selectors |
| Tests | PTY probe 和 golden frames 覆盖了一部分问题 | 增加 OMP parity tests，覆盖 component ordering 和 selector/dialog ownership |

### R0 Baseline Inventory

状态：已生成并自动化验证。OMP baseline：

```text
/home/saki/Documents/alex/oh-my-pi
c73c18a1fb3e2f2225ca685f290ec67d326689bf
```

新增验证脚本：

```bash
bun run src/cli/scripts/check_omp_parity.ts
```

该脚本把以下文件锁定为“除 leading `ts-nocheck` 外必须和 OMP baseline 一致”：

| Upstream file | Local status |
|---|---|
| `packages/coding-agent/src/modes/interactive-mode.ts` | strict parity |
| `packages/coding-agent/src/modes/controllers/input-controller.ts` | strict parity |
| `packages/coding-agent/src/modes/controllers/command-controller.ts` | strict parity |
| `packages/coding-agent/src/modes/components/assistant-message.ts` | strict parity |
| `packages/coding-agent/src/modes/components/tool-execution.ts` | strict parity |
| `packages/coding-agent/src/modes/components/hook-selector.ts` | strict parity |
| `packages/coding-agent/src/modes/components/hook-input.ts` | strict parity |
| `packages/coding-agent/src/modes/components/status-line.ts` | strict parity |
| `packages/coding-agent/src/modes/components/session-selector.ts` | strict parity; restored from upstream OMP |

允许保留的 documented adapter seams：

| Upstream file | Diff class | Reason |
|---|---|---|
| `packages/coding-agent/src/modes/controllers/event-controller.ts` | ACP adapter seam | DeepCLI adapter 可能产生 tool-first turn；本地 lazy-mount guard 防止空 assistant component 早于 tool output 出现。 |
| `packages/coding-agent/src/modes/controllers/selector-controller.ts` | ACP adapter seam | Session delete/resume 通过 ACP session service，而不是 OMP `FileSessionStorage` side effects。 |
| `packages/coding-agent/src/modes/controllers/extension-ui-controller.ts` | unsupported OMP service stub | Extension runner 全量服务尚无 DeepCLI ACP backing；生产 permission prompt 只使用 OMP hook dialog host subset。 |
| `packages/coding-agent/src/slash-commands/builtin-registry.ts` | ACP adapter seam / degraded service surface | Builtin dispatch 走 ACP-backed session/model/theme service；缺失的 OMP service hidden/degraded。 |
| `packages/coding-agent/src/session/agent-session.ts` | ACP adapter seam | OMP local agent loop 不移植；DeepCLI 拥有 ACP-backed `AgentSession` contract。 |
| `packages/coding-agent/src/session/session-manager.ts` | ACP adapter seam | OMP `SessionInfo` 由 ACP session summaries 映射，而不是读取本地 JSONL session files。 |

## Blockers

### B1 — OMP Reference 已注册（已解除）

状态：已解除。`./resolve-ref.sh oh-my-pi` 现在返回：

```text
/home/saki/Documents/alex/oh-my-pi
```

当前 OMP baseline commit：

```text
c73c18a1fb3e2f2225ca685f290ec67d326689bf
```

`.mustang-refs.example.yaml` 也已加入 `oh-my-pi` 模板项，避免新机器缺少该 reference。

### B2 — OMP Session 是 File-Based，DeepCLI Session 是 ACP-Based

OMP selectors/controllers 假设存在本地 session files、artifacts、history storage，有时还会直接删除/恢复文件。DeepCLI 的 CLI 不能直接触碰 kernel persistence。

需要的 adapter：

```text
OMP SessionManager/SessionStorage expectations
  -> DeepCLISessionService adapter
  -> ACP session/list/load/new/rename/archive/delete
```

不要把 file-storage side effects 复制进 DeepCLI。应该只实现或 stub 复制来的 selector/controller 实际读取的精确 interface。

### B3 — OMP Feature Surface 比 DeepCLI Backing Services 更宽

OMP 包含 branch、history jump、export/share、extensions、plugin management、OAuth、STT、LSP、MCP dashboards、file-artifact views 等能力。DeepCLI 不一定都有 ACP backing。

每条路径需要分类：

- **enabled**：已有 ACP/service backing。
- **degraded**：UI 保持可见，但显示 unavailable status。
- **hidden**：command/selector entry 在 service 存在前省略。
- **stubbed**：只用于 compile/runtime shim，不对用户可见。

不要因为缺失 service 就被迫写 custom rendering。

### B4 — Active-Port File Ownership

复制来的 OMP 文件应尽量贴近 upstream。`active-port/coding-agent/modes/controllers/*` 内的本地修改应该是例外，并且必须文档化：

- upstream file path
- OMP 代码为什么不能不改就运行
- 未来理想的移除路径
- 能抓住 DeepCLI-specific seam 的测试

### B5 — Dirty Worktree / Parallel Changes

当前 CLI 文件可能已经包含无关修改。进入实现阶段前：

```bash
git status --short
git diff -- src/cli
```

把用户/并行修改和本次重构 write set 分开。不要 revert 无关修改。

## Implementation Batches

### R0 — Reference And Diff Inventory

交付物：

- 注册或记录 OMP reference path/commit。
- 对复制文件和 OMP upstream 生成 diff inventory：
  - `interactive-mode.ts`
  - `input-controller.ts`
  - `event-controller.ts`
  - `command-controller.ts`
  - `selector-controller.ts`
  - `extension-ui-controller.ts`
  - `assistant-message.ts`
  - `tool-execution.ts`
  - `hook-selector.ts`
  - `hook-input.ts`
  - `status-line.ts`
- 对每个 diff 分类：
  - 仅 import alias
  - ACP adapter seam
  - unsupported OMP service stub
  - accidental DeepCLI UI rewrite

退出条件：

- 在本文档或 sibling implementation note 中提交 inventory table。
- 暂不修改代码。

### R1 — Adapter Contract First

状态：已完成。`DeepCLIAgentSessionAdapter` 是 ACP stream 的唯一 semantic reducer；`sessionUpdate` 只在 adapter/client/test 边界出现。

让 `DeepCLIAgentSessionAdapter` 成为唯一 ACP-to-OMP semantic translation point。

职责：

- 维护每个 turn 的 state：user message、assistant text/thinking、tool calls、tool results、failures、mode/title/session updates。
- 按复制来的 OMP controllers 期望的顺序发出 OMP-like events。
- 串行化 async event delivery。
- 避免 UI policy，例如 “append chat block” 或 “mount this component”。

退出条件：

- `rg "sessionUpdate" src/cli/src/active-port src/cli/src/tui` 找不到 adapter/client 边界外的直接 UI handling。
- Unit tests 覆盖：
  - tool-first turn
  - text-first turn
  - multiple tool calls
  - failed tool call
  - trailing final answer after tools
  - prompt response arriving before trailing UI work finishes

### R2 — Session Selector Uses OMP Flow

状态：已完成。`/session list` 现在打开 OMP `SessionSelectorComponent`，session rows 由 ACP-backed provider 映射成 OMP `SessionInfo`。

用 ACP session data 支撑复制来的 OMP selector flow，替换 bespoke `/session list` rendering。

职责：

- `/session` 和 `/session list` 打开/渲染 OMP session selector path，而不是手写 status/chat string。
- Session rows 来自 `SessionService.list()`。
- `switch/load/new/delete/rename/archive` 调用 `SessionService` / ACP。
- 编号选择、键盘导航、取消行为尽量遵循 OMP selector behaviour。

退出条件：

- 从 `builtin-registry.ts` 移除 `appendChatBlock()` 风格的列表渲染。
- PTY probe 验证 `/session list` 进入 selector/list UI，并且不会向 `statusContainer` 写入多行文本。
- `/session switch <number>` 仍然能通过 OMP-backed list state 或 explicit selector action 工作。

### R3 — Permission UI Is OMP Hook Dialog Only

状态：已完成。生产 permission prompt 通过 OMP hook selector/input/editor host；旧 TUI overlay 仅作为 non-OMP fallback。

把 permission rendering 收敛到 OMP hook dialog lifecycle。

职责：

- `PermissionController` 把 ACP `session/request_permission` 映射到 OMP hook selector/input/editor state。
- `ExtensionUiController` 拥有 mount/focus/restore 行为。
- Tool permission、AskUserQuestion choice、AskUserQuestion text、multiline editor 全部使用同一条 OMP host path。

退出条件：

- 生产 permission path 不再有 bespoke bottom overlay rendering。
- PTY probe 验证 permission UI 出现在 OMP hook-dialog region，并在需要时返回 ACP `updatedInput`。

### R4 — Copied Controllers Back To Upstream Shape

状态：已完成到 bounded-diff 形态。`check_omp_parity.ts` 强制 strict parity files；仍有 diff 的 copied files 均记录为 adapter seam / unsupported-service stub。

完成 R1-R3 后，审查复制来的 controllers 中的本地修改。移除不再需要的 DeepCLI-specific patches。

目标：

- `event-controller.ts`
- `command-controller.ts`
- `selector-controller.ts`
- `interactive-mode.ts`

允许保留的 diff：

- import path aliases
- adapter/service injection
- explicit unsupported-service stubs
- 记录 DeepCLI ACP boundary 的 comments

退出条件：

- Diff inventory 缩小。
- 每个仍保留的 local controller change 都有明确 blocker 和对应测试。

### R5 — Parity Test Suite

状态：已完成。新增 OMP parity checker、OMP session selector component test，并扩展真实 PTY probe 覆盖 selector/dialog ownership 与 tool/answer ordering。

增加测试，专门抓住最近已经出现过的同类 regression。

必需测试：

- Tool-first turn：tool output 出现在 final answer 之前。
- Text-first turn：assistant text 出现时没有额外空白/空 component。
- `/session list`：通过 selector/list component 渲染，而不是 status。
- Permission selector：通过 hook dialog host mount。
- AskUserQuestion text 和 choice：返回预期 `updatedInput`。
- Ctrl+C/Escape：保留复制来的 OMP semantics。
- Active-port manifest：没有未声明的 copied files。

推荐 probes：

- 扩展现有 `probe_phase_b_pty.ts`，断言 ordering 和 selector/dialog ownership。
- Golden frames 包含 session selector 和 permission dialog。

### R6 — Remove Deprecated DeepCLI UI Paths

状态：已完成。`src/cli/src/modes/interactive.ts` 删除旧 DeepCLI TUI implementation，只保留 OMP `InteractiveMode` bootstrap wrapper 和 autocomplete helper exports。

当 OMP-first 路径变绿后，删除或降级旧路径。

候选：

- `src/cli/src/modes/interactive.ts` production usage
- `builtin-registry.ts` 中的 bespoke session list rendering
- copied OMP mode 激活时的 bespoke permission overlay fallback
- 如果 adapter semantics 已经让它们不再必要，则移除 local event-controller ordering patches

退出条件：

- 生产 CLI 通过复制来的 OMP `InteractiveMode` 启动。
- DeepCLI-specific UI code 只剩 adapter/service。
- Progress doc 记录被删除的路径。

## Acceptance Criteria

只有满足以下条件，重构才算完成：

- OMP reference path/commit 已记录。
- Active-port copied files 要么和 upstream 相同，要么只有文档化、最小化、有测试覆盖的 diff。
- `DeepCLIAgentSessionAdapter` 是唯一 ACP stream semantic reducer。
- `/session` UI 是 OMP selector-first。
- Permission UI 是 OMP hook-dialog-first。
- 没有 durable multiline content 通过 `showStatus()` 渲染。
- PTY probe 覆盖 tool/answer order、session selector、permission dialog、shell/python REPL、delete confirmation。
- 现有 CLI local suite 和 active-port manifest 都通过。

## Verification Commands

预期最终验证命令：

```bash
bun run src/cli/scripts/check_active_port.ts
bun run src/cli/scripts/check_omp_parity.ts
bun run src/cli/tests/run_all.ts
bun run src/cli/tests/run_phase_b.ts
bun run src/cli/tests/run_phase_c.ts
bun run src/cli/tests/probe_phase_b_pty.ts
bun run tests/test_agent_session_adapter.ts
bun run tests/test_input_controller_r4.ts
bun run tests/test_ui_golden_r5.ts
```

如果 TypeScript tooling 可用：

```bash
bunx tsc -p src/cli/tsconfig.json --noEmit
```

## Open Questions

- 在 DeepCLI 还没有 branch/history/share 的 ACP backing 前，哪些 OMP session selector actions 应该可见？
- `/session list` 默认应该打开 interactive selector，还是渲染只读 OMP list component，而 `/session` 才打开 selector？
- 复制来的 OMP 文件是否应该周期性从 upstream resync？如果是，谁负责 diff review？
