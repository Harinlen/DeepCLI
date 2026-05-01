# CLI 交互快捷键差距调查

**父计划**: [`cli-plan.md`](cli-plan.md)
**范围**: `src/cli/` interactive TUI 输入行为
**状态**: investigation — 2026-04-27

## 摘要

Phase B/C 已经迁移了 oh-my-pi 的大量 TUI 视觉组件，但 DeepCLI 当前的
`src/cli/src/modes/interactive.ts` 还没有完整迁移 oh-my-pi
`InputController` 的交互行为。`Ctrl+O for more` 是第一个明显症状：
`ToolExecutionComponent` 已经渲染了 upstream 的快捷键提示，但 DeepCLI 没有把
对应交互动作接起来。

本文档记录 keybinding parity gap，后续 CLI 阶段应按清单成批补齐，避免一个快捷键
一个快捷键地临时发现。

## 参考范围

oh-my-pi 参考文件：

- `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/config/keybindings.ts`
- `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/controllers/input-controller.ts`
- `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/custom-editor.ts`

DeepCLI 当前相关文件：

- `src/cli/src/modes/interactive.ts`
- `src/cli/src/active-port/coding-agent/config/keybindings.ts`
- `src/cli/src/active-port/coding-agent/modes/components/tool-execution.ts`
- `src/cli/src/active-port/tui/components/editor.ts`

## DeepCLI 当前覆盖

已实现或部分实现：

| 动作 | oh-my-pi 绑定 | DeepCLI 状态 |
|---|---:|---|
| 提交 prompt | Enter | 已通过 `Editor.onSubmit` 实现 |
| 取消 / 清空 | `Ctrl+C` | 部分实现；现在会保持 busy，直到 kernel cancel 完成 |
| 展开工具输出 | `Ctrl+O` | 发现 gap 后已实现；包含 raw `\x0f` fallback |
| 本地帮助 | `/help` | 小型本地命令子集 |
| Plan mode | `/plan enter`、`/plan exit` | 小型本地命令子集 |
| 退出 | `/quit`、`/exit`、双击 `Ctrl+C` | DeepCLI 自定义行为 |
| 基础文本编辑 | TUI editor bindings | 继承 active-port `Editor` |

## 缺失或不完整的动作

这些动作出现在 oh-my-pi 的 app keybinding 层，但 DeepCLI `InteractiveMode` 尚未
完整接线。

| 动作 | 默认绑定 | DeepCLI 差距 | 备注 |
|---|---:|---|---|
| 中断 / 关闭临时 UI | `Escape` | 缺失 | 应一致处理 autocomplete、overlay、running turn。 |
| 退出应用 | `Ctrl+D` | 缺失 | 当前只有本地 slash 和双击 `Ctrl+C`。 |
| 挂起应用 | `Ctrl+Z` | 缺失 | 取决于终端 / 进程处理策略。 |
| 显示/隐藏 thinking | `Ctrl+T` | 缺失 | 需要先决定 DeepCLI UI 如何保存和切换 thinking block。 |
| 切换 thinking level | `Shift+Tab` | 缺失 | 依赖 model config / runtime thinking 支持。 |
| 切到下一个 model | `Ctrl+P` | 缺失 | 某些上下文中可能与 session selector 绑定冲突。 |
| 切到上一个 model | `Shift+Ctrl+P` | 缺失 | 需要 model-role cycling UX。 |
| Model selector | `Ctrl+L` | 冲突 | DeepCLI 目前用 `Ctrl+L` 做 redraw；oh-my-pi 用它打开 model selector。 |
| 临时 model selector | `Alt+P` | 缺失 | 需要 model selector UI 和临时覆盖语义。 |
| 外部编辑器 | `Ctrl+G` | 主编辑器缺失 | Hook editor 已支持；主 prompt editor 未接。 |
| Follow-up / queue | `Ctrl+Enter` | 缺失 | 对 streaming turn 时的输入体验很重要。 |
| 取回 queued message | `Alt+Up` | 缺失 | 需要 queued-message state。 |
| 粘贴图片 | `Ctrl+V` 或 `Alt+V` | 缺失 | 依赖 clipboard / image input path。 |
| 复制当前行 | `Alt+Shift+L` | 缺失 | active-port 有 clipboard helper，但未接线。 |
| 复制 prompt | `Alt+Shift+C` | 缺失 | active-port 有 clipboard helper，但未接线。 |
| 历史搜索 | `Ctrl+R` | 缺失 | active-port 已有 history-search component。 |
| 切换 plan mode | `Alt+Shift+P` | 缺失 | 可映射到现有 `session/set_mode`。 |
| 观察 subagent sessions | `Ctrl+S` | 缺失 | 依赖 session-agent observer UI。 |
| 新建 session | 无默认键 | 缺失 | 大概率归 Phase D / session UX。 |
| Session tree | 无默认键 | 缺失 | 依赖 session tree active-port。 |
| Fork / branch session | 无默认键 | 缺失 | 依赖 kernel/session 支持和 UI。 |
| Resume session | 无默认键 | 缺失 | Phase D / session selector owner。 |
| Session path/sort/rename/delete | selector-local keys | 缺失 | 应随 session selector port。 |
| Tree fold/unfold | `Ctrl/Alt+Left/Right` | 缺失 | 应随 tree selector port。 |
| Speech-to-text toggle | `Alt+H` | 缺失 | STT 暂不在 DeepCLI active scope。 |

## 优先级切分

高价值、低/中依赖：

1. `Escape` cancel / close 行为。
2. `Ctrl+D` 干净退出。
3. `Ctrl+G` 主 prompt editor 外部编辑器。
4. `Ctrl+R` 历史搜索。
5. 解决 `Ctrl+L`：redraw 还是 model selector。
6. `Alt+Shift+P` plan toggle。
7. `Ctrl+Enter` streaming turn 期间的 queued follow-up。

依赖其它功能，应随所属阶段安排：

- Model selector / model cycling。
- Session selector / tree / resume / rename / delete。
- Image paste。
- STT。
- Subagent observer。

## 实现备注

- 优先从 oh-my-pi `InputController` 迁移行为，不要继续散落 ad-hoc listeners。
  当前 DeepCLI `InteractiveMode` 还比较紧凑，下一步可以拆一个小型
  `InputController` facade。
- 小心 raw control bytes。`Ctrl+O` gap 的根因是 active-port native parser 中
  `matchesKey("\x0f", "ctrl+o")` 返回 false。新增快捷键时，应同时验证
  `matchesKey()` 和真实终端发来的 raw byte / escape sequence。
- 如果 UI 渲染了快捷键提示，就必须接线该快捷键；否则应隐藏提示。组件提示本身是
  用户可见契约的一部分。

## 建议后续计划

在 Phase D 前或 Phase D 内新增一个小阶段：

```text
Phase D0.5 — Interactive Keybinding Parity
```

交付项：

- 对照 oh-my-pi `InputController` 审计当前 prompt editor key handling。
- 新增 DeepCLI input-controller 模块，避免 `interactive.ts` 继续膨胀。
- 接入上面列出的高价值快捷键。
- 增加测试覆盖 raw key sequence handling（类似 `Ctrl+O`）、queued prompt
  protection 和本地 shortcut dispatch。
