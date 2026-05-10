# CLI `!` / `$` Kernel REPL 迁移计划

**父计划**: [`../../plans/cli-plan.md`](../../plans/cli-plan.md)
**范围**: `src/kernel/` + `src/cli/`
**状态**: implemented
**目标**: 完整复制 oh-my-pi 的 `!` shell command mode 与 `$` python mode 行为，但执行必须发生在 kernel 侧。

## 背景

Welcome Tips 里提示：

- `!` to run bash
- `$` to run python

oh-my-pi 的完整语义不是把这些输入发给 agent，而是把它们作为用户主动触发的本地
REPL 执行：

- `!cmd`: 执行 shell command。
- `!!cmd`: 执行 shell command，但输出不进入对话上下文。
- `$code`: 在共享 Python kernel/session 中执行 Python。
- `$$code`: 执行 Python，但输出不进入对话上下文。

DeepCLI 当前 CLI 是 thin client；执行不能落在 CLI 进程里。必须新增 kernel ACP
能力，由 CLI 发请求，kernel 执行并通过 `session/update` 流式回传。

## 非目标

- 不在 CLI 侧直接 `spawn("bash")` 或 `spawn("python")`。
- 不把 `!` / `$` 当普通 prompt 发给 agent。
- 不复用 LLM tool-call 权限流来伪装用户主动执行；这是用户输入层 REPL 行为。
- 不在本计划内实现 Welcome Tips 的 `?` hotkeys 完整迁移。

## 行为目标

### Shell

```text
!ls -la
!!cat debug.log
```

目标行为：

- 输入以 `!` 开头时，CLI 进入 shell command mode 视觉状态。沿用 oh-my-pi UI
  文案可叫 bash mode，但 DeepCLI 内部不应假设所有平台都有 bash。
- 提交 `!cmd` 后，CLI 调 kernel shell 执行 API。
- kernel 在 session 的当前工作目录执行命令，并按平台 / 配置选择 shell。
- stdout / stderr 流式显示在 CLI 聊天区。
- 退出码显示为 success / error。
- 执行中按 Esc / Ctrl+C 触发 cancel，kernel 终止进程树。
- `!!cmd` 与 `!cmd` 都执行并显示；区别是 `!!` 的执行记录不进入后续 agent context。

Shell 选择策略：

- POSIX 默认：`/bin/bash`，缺失时 fallback `/bin/sh`。
- Windows 默认：优先 PowerShell 7 (`pwsh`)；缺失时 Windows PowerShell
  (`powershell.exe`)；再缺失时 `cmd.exe`。
- 用户可在 kernel config 覆盖：

```yaml
repl:
  shell:
    default: auto          # auto | bash | sh | pwsh | powershell | cmd
    command: null          # 可选绝对路径，例如 C:\Program Files\PowerShell\7\pwsh.exe
```

这意味着 `!` 的用户语义是“执行 shell command”，不是严格“执行 bash”。Welcome Tips
最终文案也应考虑改成 `! to run shell`，或者按当前平台显示 `bash` / `PowerShell` /
`cmd`。

### Python

```text
$x = 42
$print(x)
$$print(secret_value)
```

目标行为：

- 输入以 `$` 开头且不是 `${...}` 时，CLI 进入 python mode 视觉状态。
- 提交 `$code` 后，CLI 调 kernel Python 执行 API。
- kernel 使用 per-session 共享 Python runtime；同一 session 内变量状态可复用。
- stdout / stderr / 表达式结果流式显示在 CLI 聊天区。
- exception 显示 traceback，并标记 error。
- 执行中按 Esc / Ctrl+C 触发 cancel。
- `$$code` 不进入后续 agent context。

## 协议设计

新增 ACP request / notification。

### `session/execute_shell`

Request:

```json
{
  "sessionId": "...",
  "command": "ls -la",
  "excludeFromContext": false,
  "shell": "auto"
}
```

Response:

```json
{
  "exitCode": 0,
  "cancelled": false
}
```

### `session/execute_python`

Request:

```json
{
  "sessionId": "...",
  "code": "print(1 + 1)",
  "excludeFromContext": false
}
```

Response:

```json
{
  "exitCode": 0,
  "cancelled": false
}
```

### `session/cancel_execution`

Request or notification:

```json
{
  "sessionId": "...",
  "kind": "shell"
}
```

`kind` 可为 `shell` / `python` / `any`。

### `session/update`

新增 update variants：

```json
{
  "sessionUpdate": "user_execution_start",
  "kind": "shell",
  "executionId": "...",
  "input": "ls -la",
  "shell": "pwsh",
  "excludeFromContext": false
}
```

```json
{
  "sessionUpdate": "user_execution_chunk",
  "kind": "shell",
  "executionId": "...",
  "stream": "stdout",
  "text": "..."
}
```

```json
{
  "sessionUpdate": "user_execution_end",
  "kind": "shell",
  "executionId": "...",
  "exitCode": 0,
  "cancelled": false
}
```

## Kernel 设计

不要另起一套 `session/repl/bash.py` / `session/repl/powershell.py` executor。Kernel
已经有：

- `kernel.agents.mustang.tools.builtin.repl.ReplTool`：LLM 侧批量执行 primitive tools 的 wrapper。
- `kernel.agents.mustang.tools.builtin.bash.BashTool`：POSIX shell executor、风险判断、后台任务支持。
- `kernel.agents.mustang.tools.builtin.powershell.PowerShellTool`：Windows shell executor、PowerShell 风险判断。
- `kernel.agents.mustang.tools.platform.use_powershell_tool()`：平台级 Bash / PowerShell 选择。
- `ToolRegistry.lookup()`：即使 REPL mode 隐藏 primitive tools，也保留 lookup 供内部 dispatch。

因此本计划的 kernel 侧新增能力应是 **user REPL façade**，而不是第二套 shell
runtime。ACP 的 `!` / `$` 请求进入 user REPL façade；façade 再通过 ToolManager /
ToolRegistry 调用现有 primitive tools。

建议新增：

```text
src/kernel/kernel/session/user_repl/
├── __init__.py
├── service.py
├── events.py
└── types.py
```

### UserReplService

职责：

- 作为 session-scoped façade，处理用户主动触发的 `!` / `$` 执行。
- 从 `module_table` 取得 `ToolManager`，通过 registry lookup 选择 primitive tool。
- 构造与 Orchestrator ToolExecutor 一致的 `ToolContext`：
  - `session_id`
  - 当前 cwd / worktree cwd
  - env
  - FileStateCache
  - TaskRegistry
  - cancel_event
- 调用 tool 的 `validate_input()` 与 `call()`，消费 `ToolCallProgress` /
  `ToolCallResult`。
- 将 tool output 转成 `user_execution_*` ACP updates。
- 根据 `excludeFromContext` 决定是否把执行结果纳入后续 agent context。

这层和 `ReplTool` 的关系：

- `ReplTool` 继续负责 LLM 的批量 tool call。
- `UserReplService` 负责用户输入层的直接执行。
- 两者必须共享 primitive tool 实现，不能复制 Bash/PowerShell/Python 执行逻辑。
- 如果发现 `ReplTool._run_one()` 中有可复用逻辑，应下沉成公共 helper（例如
  `kernel.agents.mustang.tools.dispatch.call_tool_for_repl()`），供 `ReplTool` 和 `UserReplService`
  共同使用；不要让 `UserReplService` 调 private `_run_one()`。

### Shell execution

职责：

- 不新增 shell executor。
- POSIX 走现有 `BashTool`。
- Windows 走现有 `PowerShellTool`；该 tool 已有 `aliases = ("Bash",)`，兼容通过
  `"Bash"` lookup 的调用。
- 新增 `CmdTool` 作为 Windows `cmd.exe` backend，并统一接入 ToolManager。
- shell backend 选择优先复用并扩展 `kernel.agents.mustang.tools.platform` 和 builtin registration 逻辑：
  - POSIX：注册 `BashTool`。
  - Windows + PowerShell 可用：注册 `PowerShellTool`。
  - Windows + PowerShell 不可用 + `cmd.exe` 可用：注册 `CmdTool`，并提供 `aliases = ("Bash", "PowerShell")`
    或至少 `aliases = ("Bash",)` 以保持上层 lookup 稳定。
- stdout/stderr/cancel 能力优先在 `BashTool` / `PowerShellTool` / `CmdTool` 本身增强；这样 LLM
  tool call、REPL batch、用户 `!` 都共享收益。
- 三个 shell tools 应共享一个小型内部执行 helper，而不是各自维护 subprocess/cancel/timeout
  样板。

建议结构：

```text
src/kernel/kernel/tools/builtin/shell_exec.py   # 公共 subprocess / timeout / cancel / output formatting
src/kernel/kernel/tools/builtin/bash.py         # POSIX command risk + bash-specific argv
src/kernel/kernel/tools/builtin/powershell.py   # PowerShell risk + pwsh/powershell argv
src/kernel/kernel/tools/builtin/cmd.py          # cmd.exe risk + cmd argv
```

`shell_exec.py` 只放机械执行能力：

- spawn argv
- cwd / env
- stdout / stderr streaming
- timeout
- cancel / process-tree cleanup
- exit-code result formatting

不要把 Bash / PowerShell / cmd 的风险判断塞进公共 helper；风险判断仍留在各自 Tool
contract 上。

安全：

- 这是用户主动执行，不走 LLM tool permission。
- 仍应复用现有 BashClassifier / destructive warning 能力作为可选提示或日志，但不能阻塞基础迁移。
- 不绕过 Tool 的 `validate_input()`、`default_risk()`、`is_destructive()` 等信息源。
- BashClassifier / PowerShell risk 判断已经挂在 tool contract 上；UserReplService 可以
  选择只记录 warning，或未来加“用户确认后执行”的 UI，但不能复制判断逻辑。
- `CmdTool` 需要自己的 conservative risk 判断：
  - 明确危险：`del` / `erase` / `rmdir` / `rd` / `format` / `diskpart` / `reg delete` 等。
  - read-only allowlist：`dir` / `type` / `echo` / `where` / `ver` / `whoami` / `cd` 等。
  - compound tokens（`&` / `&&` / `||` / `|`）默认 ask / warning，不要复用 POSIX
    compound classifier。

### Python primitive tool

职责：

- 新增 builtin `PythonTool`，而不是在 session 目录里写孤立 executor。
- `PythonTool` 加入 ToolManager registry；REPL mode 下加入 `REPL_HIDDEN_TOOLS`。
- 每个 session 维护一个共享 Python worker / namespace。
- 使用 kernel 侧 Python 执行，不启动 CLI 本地 Python。
- 捕获 stdout / stderr。
- 支持 statement 和 expression：
  - statement 用 `exec()`。
  - expression 可尝试 `eval()` 并显示 repr，或统一 exec，先以 oh-my-pi 行为为准。
- exception 捕获 traceback 并标记 error。
- cancel 通过 tool 的 `interrupt_behavior = "cancel"` 和 worker lifecycle 实现。

建议采用 worker process 而不是同进程 `exec()`：

- 避免用户代码污染 kernel 主进程。
- 更容易 cancel。
- 仍可做到 per-session shared runtime：每个 session 一个 Python worker。

### 上下文记录

新增 session history message 类型或复用 custom message：

- `ShellExecutionMessage`
- `PythonExecutionMessage`

记录内容：

- kind
- input
- stdout
- stderr
- exitCode
- cancelled
- startedAt / endedAt
- excludeFromContext

`excludeFromContext=true` 时：

- 可以持久化到 session audit / transcript。
- 不注入 orchestrator 后续 prompt context。

## CLI 设计

当前实际入口是：

```text
src/cli/src/modes/interactive.ts
```

需要迁移 oh-my-pi 输入层行为：

- onChange 检测 `!` / `$` mode 并切换 editor border color。
- submit 前拦截：
  - `!cmd` -> `DeepCLISession.executeShell(cmd, false)`
  - `!!cmd` -> `DeepCLISession.executeShell(cmd, true)`
  - `$code` -> `DeepCLISession.executePython(code, false)`
  - `$$code` -> `DeepCLISession.executePython(code, true)`
- `${...}` 不触发 python mode。
- shell/python 正在执行时禁止重复提交，并提示用户。
- Esc / Ctrl+C cancel 当前 execution。
- 渲染 `user_execution_*` update：
  - start: 创建 execution component，并显示实际 shell backend。
  - chunk: append stdout/stderr。
  - end: 标记成功、失败或 cancelled。

组件可以先复用 `Text` 简洁渲染，后续再迁移 active-port
`ShellExecutionComponent` / `PythonExecutionComponent`，或保留 upstream 组件名但显示实际 shell backend。

## 实施批次

### A — Kernel schema 与路由

- 扩展 ACP schema。
- Session handler 增加 `session/execute_shell`、`session/execute_python`、`session/cancel_execution`。
- 添加 `user_execution_*` event mapper。
- 单测覆盖 request validation 与 event wire shape。

### B — Shell tool refactor + CmdTool

- 抽出 `shell_exec.py` 公共 helper，合并 Bash / PowerShell 里重复的 subprocess、
  timeout、cancel、stdout/stderr formatting 样板。
- `BashTool` 改为使用公共 helper。
- `PowerShellTool` 改为使用公共 helper。
- 新增 `CmdTool`，使用 `cmd.exe /d /s /c <command>`。
- 扩展 `kernel.agents.mustang.tools.platform`：
  - `has_cmd()`
  - shell tool selection 覆盖 Bash / PowerShell / cmd。
- 更新 builtin tool registration。
- 更新 `REPL_HIDDEN_TOOLS`，加入 `Cmd`。
- 如当前 shell tools 只能 `communicate()` 后一次性返回，则在 tool 层增加 streaming
  progress，而不是在 UserReplService 里重写 subprocess。
- 保证 `ReplTool`、LLM shell tool call、用户 `!` 共享同一 shell 执行路径。

### C — PythonTool + Python worker

- 新增 builtin `PythonTool`。
- 将 `PythonTool` 注册到 ToolManager，并纳入 REPL primitive tool 集合。
- 实现 per-session Python worker / namespace。
- 支持 shared namespace。
- 支持 stdout/stderr/result/traceback。
- 支持 cancel。
- worker 跟随 session shutdown 清理。

### D — UserReplService + tool dispatch

- 新增 session-scoped `UserReplService`。
- 通过 ToolManager / ToolRegistry lookup 调用 `BashTool` / `PowerShellTool` / `CmdTool` /
  `PythonTool`。
- 抽出公共 tool dispatch helper，避免依赖 `ReplTool._run_one()` private 方法。
- 支持 stdout/stderr/result 转换为 `user_execution_*` updates。
- 支持 cancel_event。
- 防止同 session 并发同类 user execution。
- 记录 execution message。

### E — Context inclusion / exclusion

- 将 execution message 接入 session persistence。
- Orchestrator history 构建时尊重 `excludeFromContext`。
- `!` / `$` 进入 context；`!!` / `$$` 不进入 context。

### F — CLI 接线

- `DeepCLISession` 增加 execute / cancel wrapper。
- `InteractiveMode` 增加 mode detection、submit interception、cancel handling。
- 渲染 execution update。
- Welcome Tips 中 `!` / `$` 才算真实可用。

### G — Closure seam probes

必须有真实 subsystem probe，不能只跑 unit tests：

1. CLI 输入 `!printf hi` -> ACP -> kernel shell -> stream -> CLI render。
2. CLI 输入 `$x = 41` 后 `$print(x + 1)` -> 输出 `42`，证明 shared Python runtime。
3. CLI 输入 `!!echo secret` 后追问 agent，确认 secret 不进入 agent context。
4. 长运行 bash / python cancel，确认 kernel process / worker 被清理。
5. session shutdown 后 Python worker 不泄漏。

## 测试清单

Kernel unit tests：

- ACP schema validation。
- shell executor success / stderr / nonzero / cancel。
- shell backend resolver：POSIX bash/sh、Windows pwsh/powershell/cmd。
- python worker shared state / exception / cancel。
- context inclusion / exclusion。
- concurrent execution rejection。

CLI tests：

- `!` / `!!` submit mapping 到 `execute_shell`。
- `$` / `$$` submit mapping。
- `${...}` 不触发 python mode。
- execution updates render。
- cancel notification sent。

E2E / probes：

- `session/execute_shell` real subprocess。
- `session/execute_python` real worker。
- CLI-to-kernel closure seam。

## 风险与决策点

- **Python cancel**: 同进程 `exec()` 难以可靠中断 CPU-bound code；建议 worker process。
- **安全边界**: 用户主动执行的 shell/python 等价于本机代码执行；需要在文档和 UI 上明确。
- **context 体积**: 大输出进入 context 会膨胀 token；应限制注入长度，完整输出留 transcript。
- **并发策略**: 初期每 session 同时最多一个 bash、一个 python；后续再考虑队列。
- **worktree cwd**: bash 必须使用 session 当前 cwd，包括 worktree context modifier 后的 cwd。
- **Windows**: `!` 必须走 PowerShell/cmd shell backend，不应硬编码 bash。测试要覆盖 argv
  生成，即使 CI 不是 Windows 也至少有 resolver 单测。

## Definition of Done

- `!` / `!!` / `$` / `$$` 行为与 oh-my-pi 用户体验一致。
- 执行实际发生在 kernel，不在 CLI。
- CLI Welcome Tips 中 `!` / `$` 不再是虚假承诺。
- Unit tests + E2E + closure seam probes 全部通过。
- probe 输出粘贴到最终实现报告。
- `docs/plans/progress.md` 和必要的 lessons learned 在实现完成后更新。
