# CLI Phase C — 权限交互 UI 迁移计划

**父计划**: retired; current shipped CLI map lives in [`implemented-summary.md`](implemented-summary.md)
**范围**: `src/cli/` TypeScript / Bun client
**状态**: implemented — 2026-04-27

## 目标

处理 kernel 发来的 `session/request_permission` request，用 oh-my-pi 现有的
selector/input overlay 体系展示工具授权、结构化问题和取消流程，并把用户选择作为
JSON-RPC response 回给 kernel。

Phase C 不实现工具执行，也不改变 kernel 的授权策略。CLI 只负责用户交互和 ACP
wire mapping。

## 2026-04-27 实现前审阅快照

实现前审阅对照了 `src/cli/`、`src/cli/active-port-manifest.json` 和
`docs/plans/progress.md`。结论：Phase C 依赖的 kernel contract 已经大多落地；
CLI 侧 permission UI / mapper / queue 尚未实现，当前生产入口仍有隐式
`allow_once` handler，需要作为 C5 的首要修正。

| 项 | 当前状态 | 证据 / 备注 |
|---|---|---|
| C0 upstream surface | 部分完成 | `hook-selector.ts`、`hook-input.ts`、`dynamic-border.ts`、`extension-ui-controller.ts`、`modes/types.ts` 已登记并存在于 active-port；`countdown-timer.ts` 未登记，当前代码树也未见该文件。 |
| C0.5 kernel contract | 已实现，仍需 probe | progress 已记录 dynamic ACP options 与 AskUserQuestion text question；仍需跑真实 closure seam probe：destructive ask 隐藏 `allow_always`、text question round-trip。 |
| C1 mapper/model/queue | 未开始 | `src/cli/src/permissions/` 目录不存在。 |
| C2 overlay 接入 | 未开始 | copied upstream controller/components 存在，但 DeepCLI `InteractiveMode` 未持有 permission controller，也未调用 `showHookSelector()` / `showHookInput()`。 |
| C3 工具授权 UI | 未开始 | 真实 permission request 未渲染 overlay；`main.ts` 当前直接选择 `allow_once` 或第一个 option。 |
| C4 structured question UI | 未开始 | kernel 支持 choice/text；CLI 尚未识别 `toolInput.questions` 或生成 `updatedInput.answers`。 |
| C5 ACP plumbing | 待修正 | `AcpClient` 无 handler 时默认 `allow_once`；`main.ts` 默认安装隐式 auto-allow handler。Phase C 要改成 fail-closed + 交互 handler。 |
| C6 tests | 未开始 | `src/cli/tests/` 只有 Phase A 测试，没有 `run_phase_c.ts` 或 permission 测试。 |

## 2026-04-27 实现结果

Phase C 已在 CLI 侧实现，保留 oh-my-pi 组件主体，DeepCLI 自写代码集中在 ACP
边界和状态转换层。

| 项 | 实现状态 | 文件 |
|---|---|---|
| C1 mapper/model/queue | 已实现 | `src/cli/src/permissions/types.ts`、`mapper.ts`、`queue.ts` |
| C2 hook dialog 接入 | 已实现 | `src/cli/src/permissions/controller.ts`，`InteractiveMode` 安装 permission handler，并复用 copied OMP `showHookSelector()` / `showHookInput()` / `showHookEditor()` 生命周期 |
| C3 工具授权 UI | 已实现 | 普通工具授权用 `HookSelectorComponent`，selector label 映射回原始 `optionId` |
| C4 structured question UI | 已实现 | choice 用 `HookSelectorComponent`，Other/text 用 `HookInputComponent`，multiline text 用 `HookEditorComponent` |
| C5 ACP plumbing | 已实现 | `AcpClient` 无 handler 或 handler 抛错时 fail closed；`main.ts` 不再安装隐式 auto-allow |
| C6 tests | 已实现一组本地回归测试 | `src/cli/tests/run_phase_c.ts` |

验证结果：

- `/home/saki/.bun/bin/bunx tsc -p src/cli/tsconfig.json --noEmit` 通过。
- `BUN_BIN=/home/saki/.bun/bin/bun /home/saki/.bun/bin/bun run src/cli/tests/run_phase_c.ts`
  通过：6 passed。
- `BUN_BIN=/home/saki/.bun/bin/bun /home/saki/.bun/bin/bun run src/cli/scripts/check_active_port.ts`
  通过：255 files。
- `BUN_BIN=/home/saki/.bun/bin/bun KERNEL_URL=ws://127.0.0.1:8200 /home/saki/.bun/bin/bun run src/cli/tests/run_all.ts`
  通过：4 passed。
- `uv run pytest tests/kernel/session/test_permission_options.py tests/kernel/tools/test_ask_user_question.py -q`
  通过：41 passed。
- `uv run pytest tests/e2e/test_ask_user_question_e2e.py -q -m e2e`
  通过：3 passed。

未自动化覆盖：真实人工 TUI 按键截图/录屏式验证仍需手动跑 CLI 触发一次
permission overlay。当前本地测试覆盖 mapper、queue、ACP fail-closed response、
AskUserQuestion updatedInput shape；真实 kernel AskUserQuestion text/choice path 由
existing e2e 覆盖。

## 核心原则

继续 Phase B 的 active-port 策略：尽量原封不动复制 oh-my-pi 的 UI / controller
源码。oh-my-pi 没有一个单独命名为 "tool permission dialog" 的组件；可复用面是：

- `packages/coding-agent/src/modes/components/hook-selector.ts`
- `packages/coding-agent/src/modes/components/hook-input.ts`
- `packages/coding-agent/src/modes/components/dynamic-border.ts`
- `packages/coding-agent/src/modes/components/countdown-timer.ts`
- `packages/coding-agent/src/modes/controllers/extension-ui-controller.ts`
- `packages/coding-agent/src/modes/types.ts` 中的 `showHookSelector` /
  `showHookInput` / `showHookConfirm` contract

如果这些文件还没有完整 active-port，Phase C 先从
`/home/saki/Documents/alex/oh-my-pi` 复制它们，保留 upstream 相对路径和 render /
input 逻辑。DeepCLI 自写代码只放在 ACP 边界和状态转换层。

## C0 — Upstream Surface Inventory

当前审阅结果：**部分完成**。Phase B active-port 已经把主要 selector/input
surface 带入 `src/cli/src/active-port/`，但还缺一个明确的 Phase C inventory 表，
也缺 `countdown-timer.ts` 是否仍需要的判断。

先写一个小 inventory，可以放在本文档里，也可以作为实现 notes 表格，列出 Phase C
允许进入编译面的 upstream 文件，以及每个偏离 upstream 的原因。

必须确认：

- `hook-selector.ts`、`hook-input.ts`、`dynamic-border.ts` 已登记在
  `active-port-manifest.json`。
- `countdown-timer.ts` 当前未登记；实现时要么补 port，要么记录 Phase C 不需要它的
  upstream 偏离原因。
- `extension-ui-controller.ts` 已登记；如果它会把 extension runtime、MCP、OAuth、
  STT、LSP 或其他非 Phase C 功能拉进编译面，就加 same-path facade，只保留
  selector / input / confirm 方法。
- copied upstream 文件内部不直接 import DeepCLI ACP types。

## C0.5 — Kernel Contract Verification

当前审阅结果：**kernel 侧已实现，closure seam probe 待跑**。

Phase C 开始写 UI 前，先用现有 kernel 代码和一个小 probe 固定协议事实。不要把
未实现的 kernel 行为当作 CLI 已可验证能力。

已在 kernel 实现：

- `session/request_permission` wire schema 已支持 nested outcome：
  `selected` / `cancelled`。
- `RequestPermissionRequest.toolInput` 已下发原始 tool input。
- `PermissionOutcomeSelected.updatedInput` 已能回传到
  `PermissionResponse.updated_input`，并由 `ToolExecutor` 传入 tool call。
- `AskUserQuestionTool` 已通过 permission channel 收集答案。这是 DeepCLI 在
  ACP `session/request_permission` 上定义的扩展通道：问题放在
  `toolInput.questions`，答案通过 `updatedInput` 回传。
- 当前 `AskUserQuestionTool` 已支持 choice question：每个问题必须带 `options`。
  它期望 `updatedInput` 是原 input 的扩展对象，至少包含原 `questions` 和
  `answers: Record<questionText, string>`。
- `AskUserQuestionTool` 已支持 text-only question：`type: "text"` 的问题不需要
  `options`，可携带 `placeholder`、`multiline`、`maxLength`，答案仍通过
  `updatedInput.answers` 以 string 回传。
- kernel 已实现 permission mode `auto`，且生产环境允许用户显式使用它：
  `auto` 会自动允许 low-risk ask，不经过 CLI 弹窗。
- `allow_always` grant cache 已在 `ToolExecutor` 中接入。
- `ToolAuthorizer.PermissionAsk.suggestions` 已通过 `ToolExecutor` 进入
  `PermissionRequest.options`，`SessionPermissionMixin` 会按这些 options 生成
  ACP `session/request_permission.options`；destructive ask 可以隐藏
  `allow_always`。

Phase C 仍需 probe 验证：

- 触发 destructive ask，断言真实 `session/request_permission.options` 中没有
  `allow_always`。这是 closure seam 验证，不能只依赖 mapper 单测。
- 触发 AskUserQuestion text-only path，断言真实 `session/request_permission` 下发
  `type: "text"` question，client 通过 `updatedInput.answers` 回传自由文本后 tool
  result 包含该答案。

## C1 — Permission Data Model And Mapper

当前审阅结果：**未开始**。`src/cli/src/permissions/` 尚不存在。

新增 DeepCLI 边界模块：

```text
src/cli/src/permissions/
├── types.ts
├── mapper.ts
└── queue.ts
```

职责：

- `types.ts` 定义 UI-facing model：
  - `ToolPermissionPrompt`
  - `ToolPermissionOption`
  - `PermissionDecision`
  - `StructuredQuestionPrompt`（AskUserQuestion 专用）
- `mapper.ts` 负责 ACP ↔ UI 双向映射：
  - 保留 `PermissionRequest.options[].optionId`，不要硬编码只支持三种选择。
  - 将 `allow_once`、`allow_always`、`reject_once` / `reject_always` 映射成
    selector affordance；显示文字优先使用 kernel 提供的 `option.name`。
  - 用 `toolCall.title`、`toolCall.inputSummary` 和 `toolInput` 拼出 markdown 说明。
  - 通过 `toolInput.questions` 识别 AskUserQuestion，并返回
    `outcome.updatedInput`。
- `queue.ts` 串行化 permission request。kernel 通常会等待每个 permission
  request，但 parallel tools 下 UI 层仍然不能让两个 overlay 同时抢焦点。

## C2 — 激活 Oh-My-Pi Overlay Controller

当前审阅结果：**未开始**。upstream overlay controller/components 已在
active-port 中，但 DeepCLI `InteractiveMode` 还没有 permission controller。

把 selector/input overlay 接入当前 DeepCLI `InteractiveMode`。

优先实现：

- 复制 / 激活 upstream `ExtensionUIController` 的 selector/input 子集。
- `InteractiveMode` 持有 `PermissionController`。
- `PermissionController` 调用 copied `HookSelectorComponent` 和
  `HookInputComponent`。
- 生命周期跟 upstream `showHookSelector()` 保持一致：
  添加 overlay → 抢焦点 → resolve/cancel → 移除 overlay → 恢复 editor focus →
  request render。

如果 upstream 依赖面太宽：

- 保持 `HookSelectorComponent` 和 `HookInputComponent` 原样。
- 新增 DeepCLI `PermissionOverlayController`，只复刻承载这些组件所需的 upstream
  glue。
- 不重写组件主体。

必须保留的交互行为：

- 上下键和 `j` / `k` 导航。
- Enter 选择。
- Esc 取消。
- overlay 打开时，editor 不消费普通文本输入。
- 选择或取消后，焦点恢复到 editor。
- permission 等待用户输入时，assistant / tool streaming 仍继续渲染。
- overlay 打开时按 Ctrl+C 优先取消 permission，并返回
  `{ outcome: "cancelled" }`；第二次 Ctrl+C 才处理 CLI 退出。

## C3 — 工具授权 UI

当前审阅结果：**未开始**。当前 `main.ts` 默认 handler 会隐式选择 `allow_once`
或第一个 option，没有展示 UI。

不要设计一个新的大型组件。工具授权通过 copied `HookSelectorComponent` 渲染。

示例内容：

```text
Tool Authorization

**Bash**
`rm -rf /tmp/foo`

Choose:
- Allow once
- Allow always
- Reject
```

显示规则：

- title 使用 `req.toolCall.title`，没有时 fallback 到 `toolCall.toolCallId`。
- summary 优先使用 `req.toolCall.inputSummary`。
- `toolInput` 用 Phase B 的 `json-tree` / `render-utils` 展示为折叠 / 截断 JSON
  preview；不要新增一次性的 ad-hoc JSON pretty printer。
- 如果 `option.kind` 是 `reject_once`，UI label 仍然使用 kernel 提供的 `name`。
- selector 选择后通过保存的 `optionId` 回传，不要通过 label 反查。

选择后的 response：

```json
{ "outcome": { "outcome": "selected", "optionId": "<chosen>" } }
```

取消后的 response：

```json
{ "outcome": { "outcome": "cancelled" } }
```

## C4 — Structured Question Path

当前审阅结果：**kernel 已支持，CLI 未开始**。CLI 还没有识别
`toolInput.questions`，也不会生成 `updatedInput.answers`。

DeepCLI 的 `AskUserQuestionTool` 复用 permission channel，并通过 `updatedInput`
把答案送回 kernel。Phase C 必须支持这条路径，避免结构化提问被误渲染成普通
approve/deny prompt。

分层约定：

- ACP 标准层只提供 `session/request_permission`：agent 给出 `options`，client
  返回 `selected(optionId)` 或 `cancelled`。
- DeepCLI 扩展层在这个 request 上额外约定 `toolInput.questions` 和
  `outcome.updatedInput`。CLI 只有在识别到 AskUserQuestion shape 时，才把该
  permission request 渲染成问题 UI。
- 普通工具授权仍只按 ACP `options` 渲染；不要让 AskUserQuestion 的 UI 规则污染
  Bash/FileEdit/MCP 等普通 permission prompt。

识别条件：

- `req.toolInput?.questions` 是数组；或
- `req.toolCall.title` / `inputSummary` 指向 AskUserQuestion。

### C4a — Choice Questions（已支持的 kernel shape）

- 单选和多选问题使用 copied `HookSelectorComponent`。
- 每个问题自动追加一个 `Other` 选项；选择 `Other` 后使用 copied
  `HookInputComponent` 收集自由文本。
- 多个问题串行展示，并聚合到 `updatedInput`。
- `updatedInput` 必须保留原始 `questions`，并写入
  `answers: Record<questionText, string>`；多选答案用稳定字符串格式回填，因为
  kernel 当前 schema 要求 answer value 是 string。
- 中途 Esc / Ctrl+C 返回 `{ outcome: { outcome: "cancelled" } }`。
- 成功完成后选择一个 allow option（通常是 `allow_once`），并返回聚合后的
  `updatedInput`。

### C4b — Text-Only Questions（kernel 已支持，CLI 待接入）

为了让 LLM 可以直接问“项目名叫什么？”这类无预设选项的问题，kernel
`AskUserQuestionTool` 已支持 text question；Phase C 需要把 CLI UI 接上。

kernel contract：

- `type?: "choice" | "text"`，缺省为 `"choice"`，保持兼容。
- `choice` question 继续要求 `options` 2–4 个。
- `text` question 不要求 `options`，可选字段：
  `placeholder?: string`、`multiline?: boolean`、`maxLength?: number`。
- 所有 question 仍要求 `question` 和 `header`。
- `answers` 仍以 question text 为 key，value 仍是 string。

CLI 计划：

- mapper 将 `question.type === "text"` 转成 `StructuredQuestionPrompt` 的
  input prompt，而不是 selector prompt。
- text question 直接使用 copied `HookInputComponent`。
- 若 `multiline` 为 true，允许多行输入；否则 Enter 提交。
- 若设置 `maxLength`，输入端做软限制 / 错误提示，返回前仍保证 string。
- choice question 的 `Other` 仍保留，作为 choice path 的客户端增强；不要把它和
  text question 混为同一种 kernel shape。

## C5 — ACP Client Plumbing

当前审阅结果：**待修正**。`AcpClient.handlePermission()` 无 handler 时默认
`allow_once`，`main.ts.setupPermissions()` 默认安装隐式 auto-allow handler；这与
Phase C 完成标准冲突。

替换当前生产路径里的临时隐式 auto-allow，同时保留用户显式选择的 auto mode。

- 保留 `AcpClient.setPermissionHandler()` 作为底层 JSON-RPC hook。
- `main.ts` 默认交互模式不能偷偷 auto-approve。handler 通过
  `InteractiveMode.installPermissionHandler()` 或构造参数绑定。
- 允许生产环境显式 auto mode：例如 CLI 参数 / config / kernel mode
  `auto`。这是用户选择的 permission mode，不是缺省 handler 的静默放行。
- `AcpClient` 没有 permission handler 时必须 fail closed（返回
  `cancelled`，或选择请求中实际存在的 reject option），不能默认
  `allow_once`。
- handler 必须在 `session.prompt()` 仍 pending 时响应
  `session/request_permission`。
- handler 抛错时 fail closed：返回 `cancelled`，或选择请求中实际存在的 reject
  option，并在 TUI 显示错误。
- 测试和用户显式 auto mode 可以继续使用 headless auto-allow handler；生产默认
  交互路径不能隐式 auto-approve。

## C6 — 测试

当前审阅结果：**未开始**。`src/cli/tests/` 目前只有 Phase A 的 connect /
session / prompt / multiturn 测试。

新增或更新 `src/cli/tests/`：

- `test_permission_mapper.ts`
  - ACP options 映射到 UI options 时保留 `optionId`。
  - missing title / summary fallback 正常。
  - cancel 映射为 cancelled outcome。
- `test_permission_queue.ts`
  - 两个 permission request 串行展示；第二个不会抢第一个 overlay。
- `test_permission_overlay.ts`
  - 使用 `TestTerminal` / fake TUI 模拟上下键、Enter 和 Esc。
  - 断言 overlay add/remove、focus restore 和 render request。
- `test_acp_permission_response.ts`
  - 注入 kernel-initiated `session/request_permission` frame。
  - 断言 JSON-RPC response id 与 request id 一致。
- `test_ask_user_question_permission.ts`
  - 单选、多选、Other 自由文本都能产生 kernel 期望的 `updatedInput`：
    `{ questions, answers }`。
  - text-only question 使用 input overlay，产生同样的 `{ questions, answers }`
    shape。
- `test_acp_concurrent_permission.ts`
  - prompt request pending 时，permission request 能被回答，后续
    `session/update` frame 仍然到达。
- `test_permission_kernel_probe.ts`
  - 对真实 kernel 触发一个需要权限的工具请求。
  - 自动按键选择 allow_once，断言工具继续执行。
  - 再跑 reject path，断言工具返回 rejection/failure output。
  - 再跑 allow_always path：同一 session 内第二次相同匹配工具调用不再收到
    `session/request_permission`。
  - 触发 destructive ask，断言真实 ACP options 是否已隐藏 `allow_always`；如果
    失败，说明 kernel suggestions → ACP options seam 回归。
  - 使用 AskUserQuestion choice question 真实 kernel path，断言 tool result 中
    包含用户答案。
  - 使用 AskUserQuestion text question 真实 kernel path，断言 tool result 中包含
    用户自由文本。
- `test_permission_auto_mode.ts`
  - 显式切换 kernel permission mode 为 `auto` 后，low-risk ask 不打开 overlay 且
    tool 继续执行。
  - 默认交互模式下没有 handler 时 fail closed，不会隐式 allow_once。

Phase C 测试入口：

```bash
bun run src/cli/tests/run_phase_c.ts
```

它应该串行运行 mapper、queue、overlay、ACP 和 probe tests。

## 完成标准

- 默认交互生产路径不再隐式 auto-allow permissions；用户显式选择 `auto` mode 时
  仍可在生产环境使用 auto behavior。
- 真实 kernel `session/request_permission` 会打开 oh-my-pi 风格的 selector/input
  hook dialog：清空 editor container、挂载 hook component、聚焦 component，关闭后恢复 editor。
- `allow_once` 后工具继续执行，并渲染 tool completed。
- `allow_always` 后，同一 session 内匹配工具不再重复询问（由 kernel grant cache
  证明）。
- `reject` / cancel 后返回 rejection/failure output，CLI 不 hang。
- AskUserQuestion 通过 permission channel 收集答案，并返回 kernel 期望的
  `updatedInput` shape。
- AskUserQuestion choice question 和 text-only question 都可用；text-only 支持
  是 DeepCLI 扩展层能力，不宣称为 ACP 标准原生接口。
- prompt streaming 与 permission request 不死锁。
- 完成报告明确列出 C0.5 的 kernel contract 验证结果；未实现的 kernel seam
  必须标为 follow-up，不能算作 CLI 已完成能力。
- `bun run src/cli/scripts/check_active_port.ts` 通过。
- `bunx tsc -p src/cli/tsconfig.json --noEmit` 通过。
- `bun run src/cli/tests/run_all.ts` 通过。
- `bun run src/cli/tests/run_phase_c.ts` 通过。
- 完成报告列出 copied upstream 文件、copy 后改动过的文件，以及每个偏离 upstream
  的原因。
