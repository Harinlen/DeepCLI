# CLI Phase B — oh-my-pi TUI 迁移计划

**父计划**: [`../../plans/cli-plan.md`](../../plans/cli-plan.md)
**范围**: `src/cli/` TypeScript / Bun client
**状态**: planned

## 目标

迁移 oh-my-pi 的 TUI runtime、组件和交互控制器，让 DeepCLI CLI 的视觉效果和
输入行为与 oh-my-pi `omp` 完全一致。

## 核心原则

Phase B 不是“重新设计 UI”，而是 **按需、保留式迁移**。默认动作是复制
当前阶段闭环需要的 oh-my-pi 代码，而不是整包导入；只有以下情况才允许改写：

1. 原逻辑直接调用 oh-my-pi 的 agent loop / provider / tool execution，而 DeepCLI
   必须通过 kernel ACP。
2. 原逻辑依赖 Phase B 不做的外设能力，例如 LSP、STT、OAuth、extensions、
   MCP/SSH command controllers，需要 stub 或裁剪入口。
3. 原 import 指向 oh-my-pi package，需要改到 DeepCLI 的 active-port source 或
   compat shim。

必须优先保留 UI component render 逻辑、input controller 状态机、快捷键处理、
markdown 渲染、thinking 折叠、tool 展示结构。如果实现时必须偏离 upstream，
完成报告必须列出：改了哪个 upstream 文件、为什么 shim/copy 不够、保留了哪些行为。

推荐数据流：

```text
DeepCLISession
  -> ACP session/update
  -> DeepCLIAgentSessionAdapter
  -> oh-my-pi-like AgentSessionEvent / AssistantMessage / AgentTool
  -> 按需 port 的 InteractiveMode / controllers / components
```

## B0 — Port 管理脚手架

先建立按需迁移的管理机制，不搬 UI。

### Port 内容

- `src/cli/active-port-manifest.json`
- `src/cli/scripts/check_active_port.ts`
- `src/cli/scripts/copy_oh_my_pi_file.ts`（可选，但推荐）
- `tsconfig.json` 确认只 include `src/**/*` 和 `tests/**/*`，不 include 任何 bulk vendor 目录

### 交付物

一个可检查的 active-port 边界。后续每 port 一个文件，都必须被 manifest 管住。

### 你能看到什么

仓库里还没有大量 oh-my-pi 代码，只有 manifest 和检查脚本。

### 验收命令

```bash
bun run src/cli/scripts/check_active_port.ts
bunx tsc -p src/cli/tsconfig.json --noEmit
```

成功标准：

- check 脚本通过。
- TypeScript 仍然只编译现有 CLI，不被 oh-my-pi bulk source 影响。
- `copy_oh_my_pi_file.ts` 必须自动按 upstream package 映射目标路径：
  `packages/tui/src/**` 只能进入 `src/cli/src/active-port/tui/**`，
  `packages/coding-agent/src/**` 只能进入 `src/cli/src/active-port/coding-agent/**`。
- `check_active_port.ts` 必须扫描 `src/cli/src/active-port/**`，发现未登记 copied 文件时失败。

## B1 — TUI 主路径闭包复制与模块化激活

这一阶段合并原 B1-B7。不要先做一个“最小 runtime”，也不要把 assistant、tool、
editor、adapter、interactive 拆成互相缺上下文的小碎片；先把 oh-my-pi 交互式 TUI
主路径需要的 runtime + UI 源码闭包复制到 DeepCLI active port，然后按功能模块逐个
激活和验收。

这样做的目标是：**最大化复用 oh-my-pi 原实现，最小化 DeepCLI 自写 UI 代码**。B1
仍然不是整包 port；manifest 只允许登记 interactive TUI 主路径闭包，禁止把 Phase B
不用的外设、agent loop、provider、真实 tool runtime 一起带进来。

### 目录结构原则

复制文件时必须保留 oh-my-pi package 内部的相对目录结构：

- `packages/tui/src/**` -> `src/cli/src/active-port/tui/**`
- `packages/coding-agent/src/**` -> `src/cli/src/active-port/coding-agent/**`

例如：

- `packages/tui/src/components/editor.ts`
  -> `src/cli/src/active-port/tui/components/editor.ts`
- `packages/coding-agent/src/modes/components/assistant-message.ts`
  -> `src/cli/src/active-port/coding-agent/modes/components/assistant-message.ts`
- `packages/coding-agent/src/modes/interactive-mode.ts`
  -> `src/cli/src/active-port/coding-agent/modes/interactive-mode.ts`

这样做是为了让 copied 文件之间的相对 import 尽量保持原样，并方便后续和 upstream
diff。对外暴露给 DeepCLI 的入口用薄 re-export / facade 完成，例如 `@/tui` 可以
re-export `@/active-port/tui/index`；不要把 upstream 文件改名搬平到 DeepCLI 自己的
目录结构里。

### Import 解析策略

copied upstream 文件内的 import 优先保持原样：

- copied 文件之间的相对 import 不改，靠 `active-port/tui/**` 和
  `active-port/coding-agent/**` 的镜像目录结构自然解析。
- `@oh-my-pi/pi-tui` 通过 tsconfig/bundler alias 指向
  `src/cli/src/active-port/tui/index.ts`。
- `@oh-my-pi/pi-utils`、`@oh-my-pi/pi-natives`、`@oh-my-pi/pi-ai`、
  `@oh-my-pi/pi-agent-core` 通过 alias 指向 `src/cli/src/compat/*`。
- DeepCLI 自己的代码可以 import `@/tui`，但 copied upstream 文件不要为了 DeepCLI
  风格主动改成 `@/tui`。

如果某个 copied 文件 import 了 Phase B 不做的 upstream 模块，优先在相同 upstream
相对路径下放一个 facade/stub 截断依赖。例如 `packages/coding-agent/src/config/settings.ts`
若无法完整复制，就在 `src/cli/src/active-port/coding-agent/config/settings.ts` 提供
同名 compatibility facade，并在完成报告记录偏离原因。

### 一次性复制的 oh-my-pi TUI 主路径

从 oh-my-pi 按需复制，但在 B1 开始时一次性完成，不再分散到多个阶段：

- `packages/tui/src/tui.ts`
- `packages/tui/src/terminal.ts`
- `packages/tui/src/terminal-capabilities.ts`
- `packages/tui/src/utils.ts`
- `packages/tui/src/components/text.ts`
- `packages/tui/src/components/spacer.ts`
- `packages/tui/src/components/box.ts`
- `packages/tui/src/components/loader.ts`
- `packages/tui/src/components/select-list.ts`
- `packages/tui/src/components/truncated-text.ts`
- `packages/tui/src/components/markdown.ts`
- `packages/tui/src/components/editor.ts`
- `packages/tui/src/autocomplete.ts`
- `packages/tui/src/keybindings.ts`
- `packages/tui/src/keys.ts`
- `packages/tui/src/bracketed-paste.ts`
- `packages/tui/src/kill-ring.ts`
- `packages/coding-agent/src/modes/interactive-mode.ts`
- `packages/coding-agent/src/modes/theme/`
- `packages/coding-agent/src/modes/components/assistant-message.ts`
- `packages/coding-agent/src/modes/components/tool-execution.ts`
- `packages/coding-agent/src/modes/components/diff.ts`
- `packages/coding-agent/src/modes/components/visual-truncate.ts`
- `packages/coding-agent/src/modes/components/custom-editor.ts`
- `packages/coding-agent/src/modes/components/history-search.ts`
- `packages/coding-agent/src/modes/components/status-line.ts`
- `packages/coding-agent/src/modes/components/welcome.ts`
- `packages/coding-agent/src/modes/controllers/input-controller.ts`
- `packages/coding-agent/src/modes/controllers/command-controller.ts`
- 一个 DeepCLI facade `src/cli/src/tui/index.ts`，只 re-export 主路径已 port 的 API
- 上述文件直接 import 的 UI helper 闭包，例如 `tools/json-tree`、`tools/render-utils`、
  renderer registry、hotkey/markdown/render helper。

如果复制某个 helper 会继续拉入 LSP、STT、OAuth、extensions、MCP/SSH、provider
migration、真实 agent loop 或真实 tool runtime，必须在边界处建 facade/stub 截断，
不能让依赖链继续展开。

### DeepCLI 边界代码

B1 允许新增这些 DeepCLI 代码，用来承接协议和测试，但不能重写 oh-my-pi 组件主体：

- `src/cli/src/compat/pi-natives.ts`
- `src/cli/src/compat/pi-utils.ts`
- `src/cli/src/session/agent-session-adapter.ts`
- `src/cli/src/session/events.ts`
- `src/cli/src/session/state-builders.ts`
- `src/cli/src/session/history-storage.ts`
- `src/cli/src/compat/pi-ai.ts`
- `src/cli/src/compat/pi-agent-core.ts`
- `src/cli/src/active-port/coding-agent/config/settings.ts`（same-path compatibility facade）
- `src/cli/src/active-port/coding-agent/config/keybindings.ts`（same-path compatibility facade）
- `src/cli/tests/test-terminal.ts`

职责划分：

- `DeepCLIAgentSessionAdapter` 是唯一 ACP -> oh-my-pi-like UI event/state 转换点。
- copied `active-port/coding-agent/modes/interactive-mode.ts` / components / controllers
  不直接 switch ACP `sessionUpdate`。
- `state-builders.ts` 统一构造传给 copied components 的 state，禁止在
  `InteractiveMode` 里散落 object literal。
- `TestTerminal` 实现 oh-my-pi `Terminal` interface，测试不进入 raw mode。

### 不允许复制的范围

- oh-my-pi `SessionManager`
- oh-my-pi agent loop / model resolver / provider runtime
- 真实 tool execution runtime
- bash/python interactive execution
- MCP/SSH command implementation
- OAuth / STT / LSP / extension dashboard/widgets
- approval UI（Phase C）

### 激活顺序

B1 内部按模块激活，每个模块都要保留 copied 主体，只改 import、协议边界和被排除能力入口：

1. **TUI Runtime**：激活 `TUI`、`Container`、terminal、width/ANSI helpers、Text/Spacer。
2. **Assistant / Markdown**：激活 `AssistantMessageComponent`、markdown、thinking fold。
3. **ToolExecution**：激活 tool pending/progress/completed/failed、text result、diff result。
4. **Editor / InputController**：激活 editor、history、multi-line、paste、快捷键。
5. **Status / Welcome / Commands**：激活 status line、welcome、slash autocomplete。
6. **ACP Adapter**：把 fake ACP updates 归一化成 copied UI 能消费的 state/events。
7. **InteractiveMode**：接上 TUI layout、containers、streaming message、pending tools、
   editor/status container 的原调用顺序。

### 交付物

B1 结束时，DeepCLI CLI 已经使用 copied oh-my-pi interactive TUI 主路径启动。Assistant、
tool、editor、status、welcome、slash commands 都通过同一套 copied UI 代码渲染。

### 你能看到什么

- render tests 能直接实例化 copied components。
- keyboard tests 能模拟输入、提交、history、Ctrl+C、Ctrl+L、Ctrl+R、Shift+Enter。
- fake ACP updates 能驱动 assistant/tool/status/commands。
- 手动启动 CLI 时看到完整 TUI 首屏，而不是 readline prompt。

### 验收命令

```bash
bun run src/cli/tests/test_assistant_message_render.ts
bun run src/cli/tests/test_tui_runtime.ts
bun run src/cli/tests/test_tool_execution_render.ts
bun run src/cli/tests/test_editor_input_controller.ts
bun run src/cli/tests/test_keyboard_shortcuts.ts
bun run src/cli/tests/test_status_welcome_render.ts
bun run src/cli/tests/test_command_autocomplete.ts
bun run src/cli/tests/test_acp_adapter.ts
bun run src/cli/tests/test_acp_concurrent_permission.ts
bun run src/cli/tests/test_interactive_smoke.ts
bun run src/cli/scripts/check_active_port.ts
rg "sessionUpdate" src/cli/src/active-port src/cli/src/tui
bunx tsc -p src/cli/tsconfig.json --noEmit
```

手动验收：

```bash
bun run src/cli/src/main.ts
```

成功标准：

- copied `InteractiveMode` 能启动并接管 stdin/stdout。
- copied `TUI` / `Container` / `Text` / `Spacer` / `Editor` / `Markdown` 来自同一批主路径闭包，
  不存在独立的 DeepCLI 最小 runtime 影子实现。
- copied `AssistantMessageComponent`、`ToolExecutionComponent`、`CustomEditor`、
  `InputController`、`StatusLineComponent`、`WelcomeComponent` 主体保留。
- visual/layout/input behavior 以 oh-my-pi `omp` 为准；任何偏离都必须记录为 bug，
  不能记为设计差异。
- `sessionUpdate` 只出现在 adapter/session 层，不出现在 copied UI/components。
- concurrent permission 测试证明 prompt pending 时 request 仍能被响应。
- `InteractiveMode` 没有直接 import excluded subsystems。
- active-port manifest 只登记 interactive TUI 主路径闭包，没有完整 oh-my-pi mirror。
- golden 对照测试证明同一组 fixtures 下 DeepCLI active-port 的 render output 与
  oh-my-pi `omp` 对应组件输出一致；差异必须记录为 bug。

## B2 — 真实 Kernel Probe + Phase B 汇总测试

最后把前面所有交付物串起来，对真实 kernel 验证。

### Port 内容

不再 port 新 UI，主要补测试和 probe：

- `src/cli/tests/run_phase_b.ts`
- `TestTerminal` 汇总 harness
- pseudo-TTY probe（只用于真实 terminal 启动验证，必须有 timeout/teardown）

### 交付物

Phase B 可重复验收套件。

### 你能看到什么

一个命令能跑完 Phase B 所有测试；completion report 能贴出真实 kernel probe 输出。

### 验收命令

```bash
bun run src/cli/tests/run_all.ts
bun run src/cli/tests/run_phase_b.ts
```

真实 kernel probe 至少证明：

- streaming text 显示
- tool call start/update/result 显示
- thinking chunk 显示/折叠
- status line 更新
- slash autocomplete 更新
- Ctrl+C cancel 和双击退出可用
- golden 对照套件通过，覆盖 welcome、assistant markdown/thinking、tool execution、
  status line、editor/autocomplete 的 ANSI-aware 输出。

成功标准：

- 所有 Phase A + Phase B 测试通过。
- completion report 粘贴真实 kernel probe 输出。
- active-port manifest 没有未交付阶段的多余 oh-my-pi 文件。

## 完成标准

- 每个阶段只 port 当前阶段交付物需要的 oh-my-pi import graph；不得提前搬未来阶段文件。
- `active-port-manifest.json` 覆盖所有 copied oh-my-pi 文件，`check_active_port.ts`
  能阻止未登记文件进入编译面。
- B1-B2 的验收命令逐项通过，且完成报告能贴出命令输出摘要。
- `import { TUI, Text, Editor, Markdown } from "@/tui"` 在 B1 之后可用。
- 按需 port 的 `InteractiveMode` 在 B1 之后能启动并接管 stdin/stdout。
- Tool calls、streaming text、thinking fold、status line 都能正确 render。
- Slash autocomplete 来自 `available_commands_update`。
- `Ctrl+C` cancel、double-press exit、`Ctrl+L`、`Ctrl+R`、input history、
  `Shift+Enter` 都可用。
- Phase A tests 和 Phase B component/adapter/probe tests 全部通过。
- golden/snapshot 对照测试全部通过；视觉差异不能以“设计差异”关闭。

## 预期文件结构

```text
src/cli/
├── package.json
├── tsconfig.json
├── bin/
│   └── deepcli
├── active-port-manifest.json
├── scripts/
│   ├── check_active_port.ts
│   └── copy_oh_my_pi_file.ts
└── src/
    ├── main.ts
    ├── acp/
    │   └── client.ts
    ├── session.ts
    ├── compat/              # DeepCLI shims for oh-my-pi package imports
    ├── tui/
    │   └── index.ts         # thin facade / re-export to active-port/tui
    ├── active-port/
    │   ├── tui/             # mirrors packages/tui/src/**
    │   │   ├── index.ts
    │   │   ├── tui.ts
    │   │   ├── terminal.ts
    │   │   └── components/
    │   └── coding-agent/    # mirrors packages/coding-agent/src/**
    │       ├── config/
    │       ├── modes/
    │       │   ├── interactive-mode.ts
    │       │   ├── components/
    │       │   ├── controllers/
    │       │   ├── theme/
    │       │   └── utils/
    │       └── tools/
    └── session/
        ├── agent-session-adapter.ts
        ├── events.ts
        └── history-storage.ts
```

## 风险根治方案

以下问题不能只写“缓解方案”。Phase B 实现时必须把风险从结构上消掉，并提供
可验证的消除条件。

| 问题 | 根因 | 根本解决方案 | 验证门槛 |
|---|---|---|---|
| `@oh-my-pi/pi-tui` 依赖 `@oh-my-pi/pi-natives` Rust bindings | Active port 如果继续 import native package，就会依赖 oh-my-pi 的 Rust/Bun FFI 构建产物。 | 在 DeepCLI active port 中彻底禁止 `@oh-my-pi/pi-natives` import。新增 `src/cli/src/compat/pi-natives.ts`，完整实现 active port 实际用到的 native surface：`sliceWithWidth`、`truncateToWidth`、`wrapTextWithAnsi`、`extractSegments`、`matchesKey`、`parseKey`、`parseKittySequence`、`fuzzyFind`、`sanitizeText`、`encodeSixel`。其中 Sixel 若 Phase B 不实现真实编码，则 terminal capability 必须从源头禁止选择 Sixel path，而不是运行时碰到再 fallback。 | `rg "@oh-my-pi/pi-natives" src/cli/src` 无结果；`test_pi_natives_compat.ts` 覆盖 width/slice/truncate/key parsing/fuzzy/sanitize；`test_tui_import.ts` 不需要任何 native package。 |
| `session/prompt` streaming 时可能同时收到 `session/request_permission` | 如果 request/notification 处理绑定在某个 prompt await 上，permission request 会被 prompt response 阻塞。 | 将 `AcpClient` 定义为常驻 JSON-RPC protocol pump：WebSocket `message` handler 永远独立路由 response、notification、kernel-initiated request。`session/prompt` 只是在 pending map 中等待自己的 response；permission request 按 id 进入 permission handler 并立即回 JSON-RPC response。Phase B 的 allow-once handler 必须写入 TUI 状态/adapter event，不允许裸写 stdout/stderr。 | 新增并通过 `test_acp_concurrent_permission.ts`：在 prompt pending 时注入 `session/request_permission`，断言 client 响应该 request 且 prompt updates 继续到达。真实 kernel tool probe 也要覆盖 permission + streaming 同时发生。 |
| oh-my-pi `interactive-mode.ts` dependency surface 很宽 | 直接边编译边补 shim 会把 LSP/STT/extensions/OAuth/MCP/SSH 等非 Phase B 能力拖进 active port。 | 在复制/修改 `active-port/coding-agent/modes/interactive-mode.ts` 前生成并提交 required-surface inventory。Active port 只允许 import inventory 中列出的主路径依赖；LSP/STT/extensions/OAuth/MCP/SSH/btw 必须通过明确 facade/stub 模块截断，不能任由原依赖链继续展开。 | 如需新增未实现工作，放到 `docs/plans/cli-phase-b-surface-inventory.md` 或实现 notes 表格；`rg`/脚本检查 active port 禁止 import excluded subsystems；TypeScript 编译不能依赖任何 excluded subsystem 文件。 |
| Compat types 和真实 oh-my-pi types 漂移 | 手写 compat type 容易只满足编译，运行时缺字段时 component 才爆。 | 不把 compat type 当作自由手写接口。所有传给 copied components 的对象必须由 adapter/state builder 统一构造，例如 `makeAssistantMessageState`、`makeToolExecutionState`、`makeStatusLineState`。这些 builder 以 copied component 实际读取字段为契约，并在 render tests 中直接喂给 copied components。 | `test_component_render.ts` 覆盖 assistant/tool/status/welcome 主路径；测试必须调用 builder，再调用 copied component `render()`，而不是只测类型。禁止在 InteractiveMode 中散落 object literal 伪造 component state。 |
| ACP update 和 oh-my-pi event semantics 不是 1:1 | ACP 是 chunk/update 流，oh-my-pi UI 预期的是 AgentSession/AgentMessage/ToolExecution 风格事件和状态。 | `DeepCLIAgentSessionAdapter` 成为唯一语义转换点。它内部维护 per-turn reducer：assistant text/thinking block、tool call lifecycle、diff/location/result、mode/title/commands/plan 状态都在 reducer 中归一化。InteractiveMode 和 components 不能直接 switch ACP update。 | `rg "sessionUpdate" src/cli/src/active-port src/cli/src/tui` 无结果；`test_acp_adapter.ts` 覆盖乱序/连续 chunk、tool start->progress->diff->complete、failed、mode/title/commands/plan；真实 kernel smoke 输出证明 reducer 驱动 copied UI。 |
| 过多 oh-my-pi 文件进入 active port | `tsconfig` include `src/**/*`，只要无关文件进入 `src/cli/src` 就会被编译，迫使 shim 爆炸。 | 不在 DeepCLI repo 内保存完整 oh-my-pi mirror。oh-my-pi 只作为外部 source path；`src/cli/src/active-port/**` 只放当前阶段 import graph 需要的文件，并保留 upstream package 内相对目录结构。新增 active-port manifest，列出允许进入编译面的 copied 文件；新增脚本检查未登记 copied 文件。 | `test ! -d src/cli/vendor/oh-my-pi`；`src/cli/active-port-manifest.json` 存在；检查脚本发现 `src/cli/src/active-port/**` 中未登记 copied 文件时失败。 |
| TUI raw-mode test hang | 真实 `ProcessTerminal.start()` 会接管 stdin raw mode；普通 test 若无人消费输入会卡死并污染终端。 | 测试层禁止直接使用 `ProcessTerminal`。新增 `TestTerminal` 实现 oh-my-pi `Terminal` interface，提供内存输出、可控输入、固定尺寸、无 raw mode。需要验证真实终端启动时，使用 pseudo-TTY probe，并设置超时和 teardown。 | `test_interactive_smoke.ts` 默认使用 `TestTerminal`；pseudo-TTY probe 必须有超时、kill、terminal restore。测试中直接 new `ProcessTerminal` 视为失败。 |

这些根治方案完成前，不允许把对应风险标记为关闭。
