# CLI Phase B UI 对齐修复计划

**父计划**: retired; current shipped CLI map lives in [`implemented-summary.md`](implemented-summary.md)
**原 Phase B 计划**: [`phase-b-tui-migration.md`](phase-b-tui-migration.md)
**范围**: `src/cli/` TypeScript / Bun client
**状态**: implemented — R1/R2/R3/R4/R5/R6 main Phase B repair gates complete
**优先级**: P0，必须先于后续 CLI 功能开发

**2026-04-28 对齐快照**: kernel Session ACP compliance、session lifecycle
actions、CLI Phase D session/config/theme、CLI `!` / `$` kernel REPL 均已落地。本文计划只修
Phase B UI parity；不要把已经实现的 Phase D/service/kernel 功能重新设计一遍。

**2026-04-28 实现进展**: R1/R2/R3/R4/R5 已落地。R1 已恢复 copied upstream
`StatusLineComponent`；R2 新增 `DeepCLIAgentSessionAdapter`，集中处理 ACP
`sessionUpdate` 并输出 oh-my-pi-like session state/events；R3 让生产
`InteractiveMode` wrapper 动态加载 copied upstream `InteractiveMode` /
`InputController` / `CommandController` / `EventController` / `SelectorController`。
R4 已把 copied submit/key path 接到 DeepCLI 的 ACP shell/python/session/model/theme
桥，并补本地行为测试覆盖 `!`/`!!`、`$`/`$$`、Escape、Ctrl+C、slash command
delete confirm guard。
R5 已新增自动化 golden frame suite，覆盖 Welcome、status/editor、slash
autocomplete、no-model warning、assistant/thinking、bash/tool 状态和 permission
overlay。R6 已新增真实 CLI PTY/TUI E2E probe，通过 fake ACP kernel 驱动真实
CLI 进程、发送真实按键、截取 terminal transcript，并记录 shell/python/session
delete/permission 等 ACP closure calls。部分重型 OMP selector 子 UI 在 DeepCLI ACP mode 下暂时以
dependency stub 降级，直到对应 backing services 进入本 repo。

## 目标

修复 Phase B TUI 迁移，让 DeepCLI CLI 的主交互路径在视觉和交互行为上对齐
oh-my-pi `omp`。

这不是重新设计。目标是现有 oh-my-pi UI：搬运后的 runtime、搬运后的
editor/input 行为、搬运后的 status line 渲染、搬运后的组件契约，以及边界处的
DeepCLI ACP adapter。

## 问题陈述

当前 CLI 可以使用，但不满足最初 Phase B 的完成标准。

| 区域 | 当前状态 | 为什么这是 bug |
|---|---|---|
| Runtime 入口 | `main.ts` 启动 DeepCLI 自写的 `src/cli/src/modes/interactive.ts` | Phase B 要求使用 copied oh-my-pi `InteractiveMode`，或至少用 adapter 保留它的组件/input 语义 |
| Status line | `active-port/.../status-line.ts` 是 48 行 stub | upstream oh-my-pi status line 约 500 行，负责渲染 model/path/git/context/session 等 segment |
| Slash autocomplete | 当前只有基础 slash command 补全，缺少 `omp` 的两列候选列表、选中态、描述列和子命令候选 | Phase B 要求 input/autocomplete 行为和视觉以 `omp` 为准，不只是有一个可用补全 |
| Model warning | 当前没有截图中 `Warning: No models available...` 这种 first-viewport 状态提醒 | `omp` 会在无可用模型时把 warning 放在输入区上方，DeepCLI 也应由 CLI state/status adapter 驱动同类提示 |
| ACP 映射 | `sessionUpdate` 处理直接写在 DeepCLI `InteractiveMode` 里 | Phase B 要求 ACP → oh-my-pi-like event 的转换放在 adapter 层 |
| 组件状态 | `InteractiveMode` 用临时 object literal 构造 copied components 的状态 | Phase B 要求通过 builder/adapter 保证组件状态匹配 copied component contract |
| 测试 | Phase B 报告主要依赖 smoke probe | 缺少和 oh-my-pi render output 的 golden/snapshot 对照，尤其是 status line/editor |

用户看到的症状就是 input/status 区域不像 `omp`，包括截图里的 status line。

## 非目标

- 本计划不重新实现 Phase D 的 session picker/config/theme 行为；这些能力已在
  `src/cli/src/config/`、`src/cli/src/sessions/`、`src/cli/src/startup/` 落地。
- 本计划不重新实现 kernel session delete/rename/archive；这些 ACP route 已存在。
  Phase B 修复只负责让 `/session` autocomplete 和 copied UI path 正确调用现有
  `SessionService` / ACP 方法。
- 本计划不搬运 oh-my-pi 的 provider/runtime/tool execution 逻辑。
- 本计划不设计一套新的 DeepCLI 视觉风格。
- 在 golden 对照和真实 PTY probe 通过前，不允许把 Phase B 标记为已修复。

## 参考表面

| 来源 | 用途 |
|---|---|
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/interactive-mode.ts` | upstream layout、containers、生命周期、input controller wiring |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/status-line.ts` | canonical status line 实现 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/status-line/` | segment renderer、preset、separator、token-rate helper |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/custom-editor.ts` | app-level editor 快捷键和 border 集成 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/controllers/input-controller.ts` | submit、escape、Ctrl+C、history、command 行为 |
| `src/cli/src/modes/interactive.ts` | 当前 DeepCLI bridge，目标是退役或缩薄 |
| `src/cli/src/active-port/coding-agent/session/agent-session.ts` | 当前过薄的 `AgentSession` facade |
| `src/cli/src/session.ts` | DeepCLI ACP session wrapper |
| `src/cli/src/sessions/service.ts` / `mapper.ts` / `types.ts` | 已实现的 ACP session list/load/rename/archive/delete 边界 |
| `src/cli/src/config/loader.ts` / `schema.ts` | 已实现的 `~/.mustang/client.yaml` 配置加载 |
| `src/cli/src/startup/session-startup.ts` / `theme.ts` | 已实现的 session startup 和 theme 注入 |
| `src/cli/src/acp/client.ts` | JSON-RPC pump 和 permission request handling |
| `/home/saki/Documents/alex/oh-my-pi/packages/tui/src/autocomplete.ts` | autocomplete provider / select-list 数据流 |
| `/home/saki/Documents/alex/oh-my-pi/packages/tui/src/components/select-list.ts` | slash autocomplete 候选列表渲染、选中态、描述列 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/prompt-action-autocomplete.ts` | slash command、子命令、文件/action autocomplete 入口 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/controllers/command-controller.ts` | slash command 执行与子命令候选行为 |
| `src/kernel/kernel/protocol/acp/schemas/session.py` | 当前 session ACP schema：`updatedAt`、`archivedAt`、`titleSource`、lifecycle actions、user execution |
| `src/kernel/kernel/protocol/acp/schemas/model.py` | 当前 model ACP schema：`model/profile_list`、`model/provider_list`、`model/set_default` |

## 当前实现快照

这个计划必须按当前代码状态执行，而不是按旧 Phase B/D 草案执行。

| 能力 | 当前状态 | 本计划如何使用 |
|---|---|---|
| Session ACP summary | 已有 `updatedAt`、`archivedAt`、`titleSource`、`_meta` | adapter/status line 可用这些字段构造 session/title/token 状态 |
| Session lifecycle | `session/rename`、`session/archive`、`session/delete` 已在 kernel 暴露 | `/session` 子命令 autocomplete 可列出真实可执行项；执行继续走 `SessionService` |
| SessionService | CLI 已有 `list/load/create/rename/archive/delete` | 不再新增平行 session API，只把 copied UI/command controller 接到它 |
| Config/theme/startup | CLI 已有 `~/.mustang/client.yaml` loader、theme 注入和 startup resolution | Phase B repair 不改配置语义，只保证 copied UI 消费这些结果 |
| User REPL | `session/execute_shell`、`session/execute_python`、`session/cancel_execution` 已实现 | copied input path 必须继续把 `!` / `$` 路由到这些 ACP 方法 |
| Model list | kernel 有 `model/profile_list` / `model/provider_list` / `model/set_default` | no-model warning 和 `/model` autocomplete 应以这些真实 ACP 结果为准 |
| Per-session MCP | 非空 `mcpServers` 目前 fail-fast unsupported | adapter 继续发送 `mcpServers: []`；不要在 UI repair 中声明 session-scoped MCP |
| Active-port status line | `status-line/` helper 文件存在，但主 `status-line.ts` 仍是 48 行 stub | R1 必须恢复 upstream 主组件，而不是再补 stub |
| Phase D terminal picker | 已有 readline fallback，不是 omp TUI picker | 本计划只修主交互内的 omp parity；启动前 selector 视觉不作为 Phase B repair gate |

**2026-04-28 R1/R2/R3 drift inventory**:

| Surface | Classification | Current resolution |
|---|---|---|
| `modes/components/status-line.ts` | visual simplification fixed | Replaced stub with copied upstream implementation plus DeepCLI compatibility shims. |
| `modes/controllers/input-controller.ts` | behavior simplification fixed | Copied upstream controller is now loaded on the production main TUI path. |
| `modes/controllers/command-controller.ts` | behavior simplification fixed | Copied upstream controller is loaded; DeepCLI command data enters through adapter/local command conversion. |
| `modes/controllers/event-controller.ts` | behavior simplification fixed | Copied upstream event controller is loaded; ACP stream events are translated before reaching it. |
| `modes/controllers/selector-controller.ts` | behavior simplification fixed for main path | Copied upstream selector controller is loaded; service-heavy selector components are dependency stubs where DeepCLI has no backing service. |
| `modes/interactive-mode.ts` | behavior/visual simplification fixed for main path | Production wrapper dynamically imports copied upstream mode and passes `DeepCLIAgentSessionAdapter`. |
| `model/oauth/plugin/settings/tree/user-message selectors` | dependency stub | Hidden/degraded in DeepCLI ACP mode until corresponding provider/plugin/worktree/message services are implemented. |
| `sessionUpdate` handling in UI | boundary adaptation fixed | ACP update switch is isolated in `src/cli/src/session/agent-session-adapter.ts`. |

## 设计原则

修复后的形状应当是：

```text
DeepCLISession / AcpClient
  -> DeepCLIAgentSessionAdapter
  -> oh-my-pi-like AgentSession events/state
  -> copied InteractiveMode / InputController / components
  -> copied TUI runtime
```

`sessionUpdate` 必须在 adapter/session 边界处理，不能出现在 copied UI component
里，也不能散落在 `InteractiveMode` 中。

## R0 — 当前漂移清单

改代码前，先在实现 notes 或本文档里生成一个简短 drift inventory。

必须检查：

- 将 copied 文件与 upstream 做 diff：
  - `modes/interactive-mode.ts`
  - `modes/components/status-line.ts`
  - `modes/components/custom-editor.ts`
  - `modes/controllers/input-controller.ts`
  - `modes/components/welcome.ts`
  - `modes/components/tool-execution.ts`
- 同时核对 DeepCLI 已有边界代码，避免重做：
  - `src/cli/src/sessions/service.ts`
  - `src/cli/src/config/*`
  - `src/cli/src/startup/*`
  - `src/cli/src/session.ts`
  - `src/cli/src/acp/client.ts`
- 对每个差异分类：
  - `boundary adaptation`：如果它只是用 ACP 替代 provider/agent-loop side effect，可以接受。
  - `dependency stub`：只有在截断 LSP/STT/OAuth/MCP/SSH 等排除能力时才可接受。
  - `visual simplification`：除非明确证明不可见，否则是 bug。
  - `behavior simplification`：除非明确记录为 out of scope，否则是 bug。
- 如果 copied 文件缺失或多余，更新 `active-port-manifest.json`。

验收：

- drift table 存在。
- `check_active_port.ts` 通过。
- visual/behavior simplification 被列为修复项，而不是隐藏掉。

## R1 — 恢复 Status Line 实现

**状态 2026-04-28**: implemented for main path. Upstream main component and
segment helpers are present under `active-port`; DeepCLI supplies an
oh-my-pi-like session object through the adapter/status test fixture. Full
R5 golden matrix is still pending.

用 upstream 实现加最小 DeepCLI 兼容 facade，替换 stub 掉的
`StatusLineComponent`。

必须保留的行为：

- 保留 upstream segment pipeline：presets、left/right segments、separators、
  token rate、path、git、model、context，以及数据可用时的 hooks/subagent/plan status。
- DeepCLI 暂时没有的数据必须降级为空/neutral segment value，不能重写组件。
  当前能从 kernel/CLI 取得的真实数据应接入：`session.summary.title`、`cwd`、
  `updatedAt`、`archivedAt`、`totalInputTokens`、`totalOutputTokens`、当前 mode、
  model profile/default model。
- upstream 需要 git/path lookup 时，保留 CLI 本地 lookup。
- 增加一个薄的 `DeepCLIStatusSession` / builder object，让组件收到
  oh-my-pi-like session shape。
- `ui.status_line=false` 配置必须保留；隐藏 status line 时 editor border 仍要按
  upstream 规则退化，不得破坏输入框布局。

可能涉及文件：

```text
src/cli/src/active-port/coding-agent/modes/components/status-line.ts
src/cli/src/active-port/coding-agent/modes/components/status-line/*
src/cli/src/session/state-builders.ts
src/cli/tests/test_status_line_golden.ts
```

验收：

- status line render tests 用 fixtures 对比 DeepCLI output 和 upstream oh-my-pi
  output，覆盖 no model、model、cwd path、git repo、plan mode、token counters。
- 截图级别的 status 区域使用与 `omp` 相同的 segment 顺序和 separator 规则。
- 覆盖 `ui.status_line=false` 的退化渲染。

## R2 — 建立 DeepCLIAgentSessionAdapter

**状态 2026-04-28**: implemented for main path in
`src/cli/src/session/agent-session-adapter.ts`. It owns the ACP
`sessionUpdate` switch, translates streaming text/thinking/tool lifecycle
updates to copied OMP-style events, exposes prompt/cancel/shell/python
wrappers, and feeds status/session state to copied components. This is not a
provider/runtime port; service-heavy OMP features remain explicit stubs.

新增 adapter，把 ACP updates 和 DeepCLI session 操作转换成 copied oh-my-pi UI
期望的 state/events。

职责：

- 独占所有 `sessionUpdate` switch。
- 维护每轮 assistant text/thinking state。
- 维护 tool call lifecycle state。
- 维护给 status line 用的 session title/mode/model/context/token/status 数据。
- 暴露 `AgentSession`-like object，包含 `on` / `subscribe`、`prompt`、`abort`、
  shell/python execution wrappers，以及 copied controllers 需要的 state flags。
- 继续通过现有 `PermissionController` 处理 permission。
- 复用现有 `SessionService` 做 session list/load/lifecycle；不要新增第二套
  session client。
- 复用现有 config/theme/startup 的结果，不在 adapter 内读 `~/.mustang/client.yaml`。
- 增加 model state 子路径：启动或需要时调用 `model/profile_list`，如果 profile
  为空则驱动 no-model warning；设置默认模型继续走 `model/set_default`。

可能涉及文件：

```text
src/cli/src/session/agent-session-adapter.ts
src/cli/src/session/events.ts
src/cli/src/session/state-builders.ts
src/cli/src/session/history-storage.ts
src/cli/src/models/service.ts
src/cli/tests/test_agent_session_adapter.ts
```

验收：

- `rg "sessionUpdate" src/cli/src/active-port src/cli/src/tui` 没有结果。
- adapter tests 覆盖 streaming text、thinking chunks、tool start/progress/result、
  failed tools、mode updates、title updates、command updates，以及 user
  shell/python execution events。
- adapter tests 覆盖 model profile 为空/非空两种状态，并验证 warning state。

## R3 — 缩薄或替换 DeepCLI InteractiveMode

**状态 2026-04-28**: implemented through fallback facade. The exported
DeepCLI `InteractiveMode` now dynamically imports copied upstream
`active-port/coding-agent/modes/interactive-mode.ts`, initializes it with
`DeepCLIAgentSessionAdapter`, and delegates input/layout/event behavior to
the copied OMP path. The old custom implementation remains as an internal
legacy class only, not the exported production path.

移除当前自写 visual/layout implementation 作为主路径的地位。

优先路径：

- `main.ts` 启动 copied `active-port/coding-agent/modes/interactive-mode.ts`，
  并传入 `DeepCLIAgentSessionAdapter`。
- DeepCLI 特有的 startup/connect/session bootstrap 留在 copied UI 之外。

如果 copied `InteractiveMode` 的 excluded surface 仍然过宽，则走 fallback：

- 保留 `src/cli/src/modes/interactive.ts`，但只能作为薄 subclass/facade。
- 它必须把 layout、editor、status line、input handling、component updates
  委托给 copied oh-my-pi classes/controllers。
- 它不能再用 ad-hoc object literal 构造 copied component state。

验收：

- `main.ts` 不再把当前 custom bridge 作为主 UI path 启动。
- copied `InputController` 和 `CustomEditor` 拥有 app-level input behavior。
- copied status/editor/welcome/tool components 通过 adapter events 或 state builders 更新。

## R4 — Editor 和 Input 行为对齐

**状态 2026-04-28**: implemented for local main-path behavior. Copied
`InputController` owns submit/key handling; DeepCLI's builtin slash registry
now bridges `/session`, `/model`, and `/theme` to ACP-backed adapter methods.
Local R4 tests cover bang/dollar execution routing, mode border transitions,
Escape cancel/clear/abort behavior, Ctrl+C clear/exit semantics, and
`/session delete` confirm guard. Real PTY proof remains part of R6.

对照 `omp` 验证 editor、快捷键、border/status 集成。

必须保留的行为：

- status line/editor border composition 匹配 upstream。
- Enter submit；Shift+Enter 插入换行。
- Escape 按 upstream 行为取消 stream/overlay。
- Ctrl+C interrupt 和 double-press exit 可用。
- Ctrl+L clear/redraw 行为匹配 upstream。
- Ctrl+R history search 要么激活，要么明确记录为 remaining bug。
- `!` / `!!` 和 `$` / `$$` 仍然走 kernel ACP execution。
- Slash autocomplete 通过 copied autocomplete/editor path 渲染。
- 输入 `/` 后展示 `omp` 风格 select-list：左列 command/subcommand，右列描述，
  当前候选高亮，颜色/缩进/宽度截断规则和 upstream `SelectList` 保持一致。
- `/session` 必须提供 `info`、`delete` 等子命令候选，并按截图形态显示描述列。
  当前 kernel 已实现 delete/rename/archive，因此这些候选必须接到真实
  `SessionService` / ACP 调用；危险项如 delete 仍必须保留现有 confirm guard。
- `/session` 子命令至少覆盖当前 CLI 已实现的本地命令：
  `list`、`switch`、`new`、`load`、`current`/`info`、`rename`、`archive`、
  `unarchive`、`delete`。
- slash autocomplete 候选来源必须合并 kernel `available_commands_update` 和 CLI 本地命令，
  但视觉和键盘交互仍走 copied oh-my-pi provider/select-list。
- 无可用模型时，在 editor/status 区域上方显示 `omp` 风格 warning：
  `Warning: No models available. Use /login or set an API key environment variable. Then use /model to select a model.`
  DeepCLI 当前认证入口是 `/auth`，不是 `/login`；文案可以替换成 `/auth`，但必须
  写入 allowlist，并保持视觉位置、颜色和生命周期按 `omp` 对齐。
- `/model` autocomplete 应基于真实 `model/profile_list` / `model/provider_list`
  结果；没有 profile 时只显示 warning 和可执行的 auth/help 路径，不伪造模型。

验收：

- keyboard tests 使用 `TestTerminal` 或等价 fake terminal。
- PTY probe 证明真实 terminal 渲染和 teardown 正常。
- autocomplete tests 覆盖 `/`、`/session`、上下键选中、Enter 选中、Esc 关闭、
  描述列宽度截断，以及 no-model warning 与 autocomplete 同屏显示。
- `/session delete` 测试必须证明仍需要 confirm guard；不能因为 autocomplete 对齐
  绕过现有安全行为。

## R5 — Golden 视觉回归套件

**状态 2026-04-28**: implemented as local deterministic golden frames in
`src/cli/tests/test_ui_golden_r5.ts`, wired into `run_phase_b.ts`. The suite
ANSI-strips rendered frames and asserts stable structure/content for Welcome,
status/editor borders, `/session` autocomplete selection, no-model warning
with autocomplete, assistant thinking/markdown, bash execution, generic tool
pending/completed/failed states, and permission selector overlay. R6 remains
the required real terminal/kernel proof.

增加对照测试，防止这块再次漂移。

这里的目标是**主交互界面全量对齐**，不是只覆盖几个抽样截图。下面的列表是
第一批必须落地的 golden 基线；实现时还必须根据 R0 drift inventory 把所有被
active-port 到主交互路径的可见 UI 表面补进 golden/parity 证据。换句话说：
只要用户能在主 TUI 路径看到、聚焦、选择、展开、折叠或输入，就必须有
对齐证据，不能用“最小覆盖”关闭剩余差异。

必须覆盖的 golden 基线：

- Welcome first screen。
- 空 editor/status 区域。
- editor 中有短 prompt。
- editor 中有 multiline prompt。
- slash autocomplete 展开。
- slash autocomplete 展开到 `/session` 子命令列表，包含 `info` / `delete` 两列候选和描述。
- no-model warning + status line + autocomplete 同屏的 first viewport。
- `/model` 在 no-profile 状态下的 warning/autocomplete 组合。
- assistant markdown/thinking。
- tool pending/running/completed/failed。
- status line with model/path/git/context fixtures。
- UI path 修改后 permission overlay 仍然可用。

扩展 parity 矩阵必须由 R0 drift inventory 生成，至少包含：

- 主 layout：chat container、pending tool/message 区域、status/editor 排列、resize 后重排。
- Editor chrome：border、top border/status integration、prompt gutter、cursor、selection、
  paste/multiline、disabled/busy state。
- Autocomplete：slash commands、subcommands、description column、file/action candidates、
  empty state、过滤、滚动、宽度截断、取消/提交。
- Assistant rendering：plain text、markdown、code block、list/table、thinking fold、
  streaming 增量更新。
- Tool rendering：bash/python/user execution、普通 tools、diff/edit previews、errors、
  partial output、expanded/collapsed state。
- Overlay rendering：permission selector、AskUserQuestion choice/text/editor、
  history search、session/theme selectors if they appear inside the main TUI path。
- Status/warnings：no-model warning、mode/status changes、model/path/git/context/token
  segments、theme/symbol preset differences。

对比策略：

- 优先用同一组 fixture 同时运行 upstream component 和 DeepCLI active-port component，
  比较 ANSI-aware rendered lines。
- 如果测试 runtime 无法直接 import upstream，则存储从 upstream commit 生成的明确
  golden snapshots，并记录 source commit / file timestamp。
- 每个 R0 标记为 `visual simplification` 或 `behavior simplification` 的差异，都必须
  要么被修掉并加入 parity 测试，要么进入明确 allowlist。allowlist 只能记录
  DeepCLI 架构导致的不可避免差异，不能记录“还没搬完”。

验收：

- golden tests 纳入 `run_phase_b.ts`。
- 任何有意差异都进入小型 allowlist，并写明原因。
- 不能因为“DeepCLI 不一样”就接受视觉差异。
- `run_phase_b.ts` 输出 UI parity coverage summary，列出每个主 UI 表面：
  `covered` / `allowlisted` / `missing`。存在 `missing` 时 Phase B repair 不得完成。

## R6 — 真实 CLI PTY/TUI E2E Probe

**状态 2026-04-28**: implemented in
`src/cli/tests/probe_phase_b_pty.ts`. The probe starts a fake ACP WebSocket
kernel, launches the real CLI inside a pseudo-terminal, sends real keyboard
input, captures the ANSI terminal transcript, and asserts both visible UI
frames and ACP closure calls. This is the UI proof; no standalone ACP-only
probe is used as a substitute.

Phase B repair 只有在通过真实 terminal 驱动的 CLI TUI E2E 后才算完成。

这里的核心证明对象是 **CLI 界面**，不是 kernel 协议本身。普通 ACP probe 只能证明
后端方法可用，不能证明 copied OMP UI 真的被加载、真实键盘输入真的走 copied
editor/input path、ANSI frame 真的匹配 `omp` 结构。因此 R6 必须使用伪终端
启动真实 CLI 进程、发送真实按键、截取 terminal frame，并在必要时连接真实或测试
kernel 形成闭环。

R6 由两层组成：

- **PTY/TUI probe（必须）**：证明 UI 行为。通过 pseudo-terminal 驱动真实
  `deepcli` CLI，截取 ANSI frame，检查 first viewport、status/editor、
  autocomplete、mode border、overlay、Ctrl+C/Escape 等真实终端行为。
- **ACP/kernel probe（辅助）**：证明 UI 操作背后的后端 closure seam 可用，例如
  `session/execute_shell`、`session/execute_python`、`session/delete`、
  `model/profile_list`。它不能替代 PTY/TUI probe。

PTY/TUI probe 必须证明：

- CLI 能启动并连接 kernel。
- first viewport 的 status/editor 区域匹配 `omp` 结构。
- streaming text 能显示。
- tool call start/update/result 能显示。
- thinking chunk 能显示并正确折叠/渲染。
- slash autocomplete 能展开，并能展示 `/session` 子命令的两列候选列表。
- 无模型配置时能显示 `omp` 风格 no-model warning；有模型后 warning 消失。
- `/session rename/archive/delete confirm` 继续调用真实 kernel ACP lifecycle actions。
- prompt request pending 时 permission overlay 仍能出现。
- Ctrl+C 能取消 active work。
- `!` 和 `$` execution 仍然走 kernel ACP。

ACP/kernel 辅助 probe 必须证明：

- `session/execute_shell`、`session/execute_python`、`session/cancel_execution`
  在真实 kernel 上可用，并能被 CLI 操作触发。
- `/session rename/archive/delete confirm` 对应的 ACP lifecycle action 被真实调用。
- `model/profile_list` 空/非空状态能驱动 no-model warning 生命周期。
- permission request 能从 kernel 进入 CLI overlay，并把选择结果回写给 kernel。

推荐命令形状：

```bash
bun run src/cli/tests/run_phase_b.ts
bun run src/cli/tests/probe_phase_b_pty.ts
```

验收：

- completion report 粘贴 PTY/TUI probe output；ACP/kernel probe output 作为辅助证据。
- screenshot 或 captured ANSI frame 只有在必要时才保存为 test artifact；
  除非是刻意保持很小的 golden fixture，否则不要提交笨重的 terminal recording。
- 如果 PTY/TUI probe 没覆盖某个可见 UI 表面，不能用 ACP probe 声称该表面已验证。

## Closure-Seam Inventory

实现时预计会触碰的 closure seams：

| Seam | Caller | Callee | Required probe |
|---|---|---|---|
| ACP update adapter | `AcpClient` / `DeepCLISession` | `DeepCLIAgentSessionAdapter` | fake ACP adapter tests + real kernel streaming probe |
| Permission request handler | `AcpClient` | `PermissionController` / copied overlay | existing Phase C tests + PTY overlay probe + ACP permission closure probe |
| Prompt submit | copied `InputController` | `DeepCLIAgentSessionAdapter.prompt()` | keyboard test + PTY prompt submit probe + ACP prompt closure probe |
| Cancel | copied editor/input controller | `DeepCLISession.cancel()` / `cancelExecution()` | PTY cancel probe |
| Status state builder | adapter | copied `StatusLineComponent` | golden status tests |
| Shell/Python execution | copied input path | kernel `session/execute_*` ACP methods | PTY `!`/`$` probe + ACP shell/python closure probe |
| Session lifecycle commands | copied command/autocomplete path | `SessionService` -> kernel `session/*` lifecycle ACP | PTY `/session` command probe + ACP lifecycle closure probe |
| Model warning/model commands | adapter/model service | kernel `model/profile_list` / `model/set_default` | PTY no-model warning probe + fake empty-profile test + ACP model-list closure probe |

## 验证矩阵

报告完成前必须运行：

```bash
bunx tsc -p src/cli/tsconfig.json --noEmit
bun run src/cli/scripts/check_active_port.ts
bun run src/cli/tests/run_all.ts
bun run src/cli/tests/run_phase_b.ts
bun run src/cli/tests/run_phase_c.ts
bun run src/cli/tests/run_phase_d.ts
bun run src/cli/tests/probe_phase_b_pty.ts
```

如果触碰 shell/python、permission、session lifecycle 或 model 状态路径，还要跑
kernel-side targeted checks：

```bash
uv run pytest tests/e2e/test_ask_user_question_e2e.py -q -m e2e
uv run pytest tests/kernel/session/test_permission_options.py -q
uv run pytest tests/kernel/protocol/test_routing.py tests/kernel/protocol/test_session_handler.py -q
```

## 完成标准

只有满足以下条件，repair 才算完成：

- production CLI entry 使用修复后的 oh-my-pi-compatible path。
- `StatusLineComponent` 不再是 visual stub。
- `sessionUpdate` 被隔离在 adapter/session code。
- golden tests 和 parity coverage summary 证明主交互路径所有可见 UI 表面都已
  render/interaction parity against `omp`，不存在未解释的 missing 项。
- 真实 PTY probe 证明 first-screen/status/editor behavior 在真实 terminal 中正确。
- progress docs 明确更正此前 "first usable Phase B" 状态为 partial，而不是 complete。
