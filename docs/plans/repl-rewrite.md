# REPL Tool 重写记录

**状态**：非 Windows 范围已完成。唯一剩余项是 Windows 真机 / Windows CI 的
PowerShell + Cmd probe。

## 当前实现

- 模型可见工具：[`ReplTool`](../../src/kernel/kernel/tools/builtin/repl_python.py)
- worker / IPC / linter：[`kernel.agents.mustang.tools.repl`](../../src/kernel/kernel/tools/repl/)
- primitive tool allowlist：
  [`REPL_PRIMITIVE_TOOLS`](../../src/kernel/kernel/tools/repl/primitives.py)
- prompt：[`tools/repl.txt`](../../src/kernel/kernel/prompts/default/tools/repl.txt)

旧 JSON batch dispatcher 已删除；不存在 `BatchTool` 迁移路径。

## 行为

`tools.repl = true` 时：

- 模型只看到 `REPL`，不直接看到 `Read` / `Write` / `Edit` / `Glob` / `Grep` /
  shell / `Python` / `Agent` 等 primitive tools。
- `REPL` 执行 Python top-level-await 脚本。
- 脚本运行在 per-session worker process，不在 Kernel / Agent Runtime 主进程内
  `exec()`。
- 同一 active session 内 worker globals 持久；session evict、runtime restart、
  timeout、cancel 或 worker crash 后会丢失。
- timeout / cancel 会终止 worker；下一次调用重建 worker，并在 result 中标记
  `reset=True`。

worker 注入的 helper：

```python
await Read(file_path="...")
await Write(file_path="...", content="...")
await Edit(file_path="...", old_string="...", new_string="...")
await Glob(pattern="...", path="...")
await Grep(pattern="...", path="...")
await Bash(command="...")
await PowerShell(command="...")
await Cmd(command="...")
await Python(code="...")
await Agent(prompt="...")

await sh("git status --short")
await cat("README.md", limit=200)
await rg("Session", "src/kernel")
await rgf("Session", "src/kernel")
await gl("**/*.py", "src/kernel")
await put("path.txt", "content")
chdir("src/kernel")
```

Linux / WSL 下 `sh()` 默认走 `Bash`。Windows 下默认走 `PowerShell`，并允许显式
`shell="cmd"`；这部分实现已存在，但还需要 Windows probe 验证。

## 安全边界

- nested primitive tool call 由 `ToolExecutor` 注入的
  `ToolContext.run_nested_tool()` 执行。
- nested call 继续走 input validation、`ToolAuthorizer`、permission callback、
  hooks、runtime kill guard、file-touch notification、result mapping。
- nested call 只能调用 `REPL_PRIMITIVE_TOOLS`，并拒绝递归调用 `REPL`。
- `chdir()` 会把 worker cwd 通过 IPC 带回 parent，使后续 nested tool call 的相对路径
  与 worker 一致。
- worker 使用 AST linter 拒绝 `import` / `from import`、`while`、`lambda`、
  `class`、`def` / `async def`、危险 builtin 和危险 dunder escape。

这不是恶意代码 sandbox；它是“Agent 生成脚本不会卡死或污染 Kernel 主进程”的执行边界。

## 已验证

- REPL runner / linter / nested dispatch 单测。
- `ToolRegistry.snapshot(repl_mode=True)` 隐藏 primitive tools。
- timeout 后 worker 被杀且下一次调用可恢复。
- `chdir()` 后 cwd 透传到 parent nested tool call。
- 真实 ACP E2E：启用 `tools.repl` 后模型通过 `REPL` 完成工具调用；错误路径不会打崩会话。

## 剩余项

- Windows 真机或 Windows CI probe：
  - `await sh("Write-Output hello")` 默认 PowerShell 返回 `hello`
  - `await sh("echo hello", shell="cmd")` 返回 `hello`
  - nested `Stop-Process` / `taskkill` 命中 runtime guard

## 明确不包含

- `haiku(prompt, schema)` 二级模型采样。
- `registerTool` / `unregisterTool` / `listTools` / `getTool` 动态工具管理。
- JavaScript runtime。
- REPL globals 跨 runtime restart 持久化。
- 面向恶意代码的强 sandbox。
