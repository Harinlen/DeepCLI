# CLI 交互输入契约与快捷键差距

**父计划**: [`cli-plan.md`](cli-plan.md)
**范围**: `src/cli/` interactive TUI 输入行为
**状态**: active remediation — 2026-05-11

## 摘要

Phase B/C 已经迁移了 oh-my-pi 的大量 TUI 视觉组件。最初的问题是 DeepCLI
渲染了 upstream 快捷键提示，但没有完整迁移 `InputController` 的交互行为。
截至 2026-05-11，DeepCLI active-port 已经有自己的 `InputController`，并已接入
大量 OMP 风格的 app keybindings。

当前问题不再只是“缺几个快捷键”，而是 prompt editor、autocomplete、slash command
dispatcher、kernel prompt submission 之间缺少一份稳定输入契约。最近暴露出的症状：

- Enter 在 autocomplete 打开时不应接受补全；Tab 才能接受补全。
- Slash command argument completion 只能替换当前 token，不能吞掉前面的 subcommand。
- `/plan enter` 是命令，不是发送给 plan-mode agent 的 prompt。
- `/plan <text>` 在 plan mode 已经 active 时应作为正常 prompt 继续发送，而不是 toggle。
- Mode 的事实源必须是 Kernel/session 的 current mode；CLI 本地 `planModeEnabled` 只能是
  UI / 工具状态缓存，不能作为 permission mode 的判定来源。

因此本文档从“快捷键差距调查”升级为“CLI 输入契约与快捷键 parity 跟踪”。

## 参考范围

oh-my-pi 参考文件：

- `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/config/keybindings.ts`
- `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/controllers/input-controller.ts`
- `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/custom-editor.ts`

DeepCLI 当前相关文件：

- `src/cli/src/active-port/coding-agent/config/keybindings.ts`
- `src/cli/src/active-port/coding-agent/modes/controllers/input-controller.ts`
- `src/cli/src/active-port/coding-agent/slash-commands/builtin-registry.ts`
- `src/cli/src/active-port/coding-agent/modes/components/tool-execution.ts`
- `src/cli/src/active-port/tui/components/editor.ts`
- `src/cli/src/active-port/tui/autocomplete.ts`

## 输入契约

DeepCLI 应保留 OMP 的分层架构，但不盲目照抄 OMP 的全部按键语义。

1. `Editor` 只负责文本编辑、autocomplete 展示、Tab 接受补全、Enter 提交当前
   buffer。
2. `InputController` 负责 app-level keybindings、运行态 cancel / queue / follow-up、
   slash / bash / python / normal prompt 的提交分发。
3. `AutocompleteProvider` 必须只替换当前 completion token；多参数 slash command
   不能因为补全 backend/model/session id 而丢掉 subcommand。
4. `executeBuiltinSlashCommand()` 必须显式表达命令语义。Command subcommand 不能被
   隐式解释成 prompt，除非该命令明确设计为接受自由文本。
5. OMP 当前行为中“slash autocomplete 打开时 Enter 会补全并提交”不适用于 DeepCLI。
   DeepCLI 的用户契约是：**Tab 接受补全，Enter 执行/提交当前输入框原文。**
6. Permission mode 以 Kernel 为准。CLI 收到 `current_mode_update` 后必须同步本地 plan
   UI 状态；`/plan enter` 也必须检查 kernel current mode，而不是只看本地
   `planModeEnabled`。

## DeepCLI 当前覆盖

已实现或部分实现：

| 动作 | oh-my-pi 绑定 | DeepCLI 状态 |
|---|---:|---|
| 提交 prompt | Enter | 已通过 `Editor.onSubmit` 实现 |
| autocomplete 接受补全 | Tab | 已实现；Enter 不接受补全 |
| slash argument completion | Tab | 已实现当前 token 替换；覆盖 `/webfetch install c` |
| 取消 / 关闭临时 UI | Escape | 已接入 `InputController`；按状态 cancel autocomplete / running turn / mode |
| 清空 / 双击退出 | `Ctrl+C` | 已接入；单击清空，双击 shutdown |
| 干净退出 | `Ctrl+D` | 已接入；空 editor 时退出 |
| 挂起应用 | `Ctrl+Z` | 已接入 POSIX suspend / resume |
| 展开工具输出 | `Ctrl+O` | 发现 gap 后已实现；包含 raw `\x0f` fallback |
| 本地帮助 | `/help` | 小型本地命令子集 |
| Plan mode | `/plan enter`、`/plan exit`、`/plan status` | 已显式分发；避免把 subcommand 当 prompt |
| Permission mode cycle | `Shift+Tab` | DeepCLI 用于 permission mode cycle |
| Thinking block toggle | `Ctrl+T` | 已接入 |
| Model selector | `Ctrl+L` | 已接入 model selector |
| Model cycle | `Ctrl+P` / `Shift+Ctrl+P` | 已接入 |
| 临时 model selector | `Alt+P` | 已接入 |
| 外部编辑器 | `Ctrl+G` | 已接入 |
| Follow-up / queue | `Ctrl+Enter` | 已接入 `handleFollowUp()` |
| 取回 queued message | `Alt+Up` | 已接入 |
| 粘贴图片 | `Ctrl+V` 或 `Alt+V` | 已接入 clipboard image path |
| 复制当前行 | `Alt+Shift+L` | 已接入 |
| 复制 prompt | `Alt+Shift+C` | 已接入 |
| 历史搜索 | `Ctrl+R` | 已接入 |
| 切换 plan mode | `Alt+Shift+P` | 已接入 plan toggle |
| 观察 subagent sessions | `Ctrl+S` | 已接入 observer 入口 |
| 退出 | `/quit`、`/exit`、双击 `Ctrl+C` | DeepCLI 自定义行为 |
| 基础文本编辑 | TUI editor bindings | 继承 active-port `Editor` |

## 缺失或不完整的动作

这些动作或风险仍需要按输入契约补测试 / 补实现，而不是继续临时加 listener。

| 项目 | DeepCLI 状态 | 备注 |
|---|---:|---|
| Enter / Tab raw terminal matrix | partial tests | 需要覆盖 `\r`、`\n`、Kitty protocol、Shift/Ctrl modifiers。 |
| Slash command subcommand grammar | partial tests | `/plan`、`/webfetch` 已补；`/model`、`/session` 还需要类似 regression matrix。 |
| Unknown slash command fallback | unresolved | 需要决定未知 `/foo` 是发给 kernel、报错，还是走 extension command。 |
| Extension input handler ordering | needs audit | 当前 extension input handlers 在 builtin slash 之前执行；需确认是否符合 DeepCLI 语义。 |
| Session selector-local keybindings | partial | toggle path/sort/rename/delete 随 selector UI 验证。 |
| Tree fold/unfold | partial | `Ctrl/Alt+Left/Right` 需随 tree selector 做 raw-key smoke。 |
| Speech-to-text toggle | out of active scope | `Alt+H` keybinding 已定义；STT runtime 不在当前 active scope。 |

## 优先级切分

当前最高优先级不是继续搬快捷键，而是冻结输入契约：

1. 为 `Editor` 增加 Enter / Tab / Escape / newline raw-sequence 测试矩阵。
2. 为 `executeBuiltinSlashCommand()` 增加每个 builtin command 的 subcommand regression。
3. 为 `InputController` 增加 submit pipeline ordering 测试：extension、builtin slash、
   skill、bash、python、compaction、streaming steer、normal prompt。
4. 对照 OMP 再跑一次 keybinding parity audit，记录“同步”和“有意偏离”两类。

依赖其它功能，应随所属阶段安排：

- Session selector / tree / resume / rename / delete。
- STT。

## 实现备注

- 优先从 oh-my-pi `InputController` 迁移行为，不要继续散落 ad-hoc listeners。
  DeepCLI 现在已有 `InputController`，后续应继续收敛到这里，而不是回到
  `InteractiveMode` 或组件内部加 app-level 逻辑。
- 小心 raw control bytes。`Ctrl+O` gap 的根因是 active-port native parser 中
  `matchesKey("\x0f", "ctrl+o")` 返回 false。新增快捷键时，应同时验证
  `matchesKey()` 和真实终端发来的 raw byte / escape sequence。
- 如果 UI 渲染了快捷键提示，就必须接线该快捷键；否则应隐藏提示。组件提示本身是
  用户可见契约的一部分。
- OMP 行为不是绝对来源。DeepCLI 已明确偏离 OMP 的 slash autocomplete Enter 语义：
  Enter 提交当前 buffer，Tab 接受补全。
- Slash command handler 不应复用 toggle API 来表达显式 subcommand；例如 `/plan enter`
  和 `Alt+Shift+P` 不是同一个语义。

## 建议后续计划

在 Phase D 前或 Phase D 内新增 / 继续一个小阶段：

```text
Phase D0.5 — CLI Input Contract and Keybinding Parity
```

交付项：

- 对照 oh-my-pi `InputController` 审计当前 prompt editor key handling，标注同步项和
  有意偏离项。
- 固化输入契约测试：Enter 不补全、Tab 补全、Escape cancel、slash subcommand 不被
  当成 prompt。
- 为 builtin slash commands 建立 grammar regression table。
- 增加 raw key sequence handling 测试（类似 `Ctrl+O`）、queued prompt protection 和
  本地 shortcut dispatch 测试。
- 完成 session/tree selector-local keybindings 的 parity smoke。

## 2026-05-11 更新记录

- 对照 OMP 后确认：同步架构边界，不同步 OMP 的 slash autocomplete Enter 语义。
- 修复 `/plan enter` 被解释成 plan prompt 的问题。
- 修复 `/plan enter` 在 plan mode 已 active 时可能反向 toggle 的风险；`/plan enter`
  现在是显式、幂等的进入命令，不是 toggle。
- 对齐 plan mode 与 permission mode 语义：进入 plan mode 时 session mode 切到
  `plan`，`/plan exit` 显式退出后恢复到默认 Ask (`default`)。
- 修复本地 plan 状态与 Kernel mode 分叉的问题：`current_mode_update` 会同步 CLI 本地
  plan UI 状态；当 status bar 已是 Auto 时，`/plan enter` 会以 Kernel mode 为准重新进入
  Plan，而不会因为本地 stale flag 提示 already active。
- 保留 `/plan <text>` 的启动 plan mode + 初始 prompt 能力；当 plan mode 已 active 时，
  `/plan <text>` 退回为正常 prompt 发送。
- 对齐 prompt editor 的 raw Enter 契约：`\r` 和 `\n` 都提交当前 buffer；显式
  Shift/Ctrl+Enter escape sequence 才插入新行 / 触发 follow-up 语义。
- 对齐 slash argument autocomplete：slash command 参数上下文同步刷新补全列表，Tab
  只替换当前 argument token，不会吞掉 subcommand。
- 增加 keybinding wiring regression，确保 `InputController` 注册的 app actions 和
  custom shortcut keys 不再静默漂移。
- 已增加相关 regression tests：
  - `src/cli/tests/test_editor_slash_argument_autocomplete.ts`
  - `src/cli/tests/test_autocomplete_sort.ts`
  - `src/cli/tests/test_input_controller_r4.ts`
  - `src/cli/tests/test_agent_session_adapter.ts`
