# Backlog — 延迟功能

从已实装的设计文档中提取的延迟/未实现功能项。
每项都有完整设计，但尚未编码。

---

## 1. Context Collapse (1d)

**来源**: `kernel/subsystems/compaction.md` §1d

Commit-log 式 landmark 日志 + 读时投影：维护 boundary index + 摘要，
STEP 1 时把旧消息替换为预计算的 landmark 摘要，O(1) 无 LLM 调用。
Feature flag `CONTEXT_COLLAPSE`（默认 off），代码未写。
Orchestrator 中有明确 TODO: `# 1d. Context collapse — TODO (feature-flagged, deferred)`

**前置条件**: 1a–1c 已上线，视 autocompact 触发频率决定是否需要。

---

## 2. HookManager 缺失 Fire-sites

**来源**: `kernel/subsystems/hooks.md`

以下 hook 事件已定义但未在任何位置触发：
- `pre_compact`
- `post_compact`
- `subagent_start`（事件 yield 了 `SubAgentStart`，但没有 fire hook）
- `session_start`
- `session_end`
- `file_changed`

---

## 3. Agent Progress Tracking (Background)

**来源**: `kernel/subsystems/tasks.md`

`_run_agent_background` 中两个 TODO：
1. 写 output 文件供 TaskOutputTool 读取
2. 更新 AgentProgress 实时指标（目前无增量进度上报）

---

## 4. PromptManager User-defined Override

**来源**: `kernel/subsystems/prompts.md`

`default/` 命名已为用户自定义覆盖层预留位置。当前只从 `default/`
目录加载，未实现用户自定义 override 发现逻辑，后续按需开放。

---

## 5. LLM compact/vision 角色

**来源**: `kernel/subsystems/llm.md` (appendix: current_used refactor)

角色系统基础已实装（`bash_judge`、`memory`、`embedding` 角色可用），
但 `compact`（Compactor 用更便宜的模型）和 `vision` 角色尚未定义，
compaction 仍用 `default` 模型。等实际需求出现时再加。

---

## 6. Config BackupRotator

**来源**: gap review（OpenClaw `src/config/backup-rotation.ts`）

`config.yaml` 每次 `update()` 写盘前备份到 `config.yaml.bak.N`，保留最近 N 份（默认 5）。
`deepcli config rollback <N>` 一键回滚。Config 内部小组件，不是独立 subsystem。
runtime mutations（`set_config_option` 改 model/temperature）如果不备份就没法回滚。

---

## 7. Session BlobStore

**来源**: gap review（Claude Code `tool-results/` 模式）

超大 tool_result（>200KB）和图片溢写到磁盘 + 返回 preview + 懒加载。
存储位置在 `<session_uuid>/tool-results/`（session.md 已预留）。
生命周期跟随 Session，session 删除时级联清理。
Tools 通过 `ToolContext.blobs.put(content) -> BlobHandle` 写入。
tools.md 已引用 BlobStore 接口，但 session.md 还没有设计小节。

---

## 8. Session UsageLedger

**来源**: gap review（Claude Code `/cost` 命令）

按 model 汇总 input/output token 数 + 金额。数据来源是每轮 `TurnCompletedEvent`。
Session SQLite 里加 `usage_by_model` 聚合表。CommandManager 的 `/cost` 只读。
目前 per-turn 统计已在 `TurnCompletedEvent.input_tokens` / `output_tokens` 里，
缺的是按 model 分维度的聚合视图。

---

## 9. Session DiagnosticBuffer

**来源**: gap review（OpenClaw `src/logging/diagnostic-session-state.ts`）

session 运行期的临时诊断 state——最近 N 次 LLM 调用的请求/响应、最近 M 次
hook fire 的输入输出、最近 K 次工具调用的时间线。内存 ring buffer，不进 SQLite。
供 `deepcli diag <session-id>` 命令 dump。Session 销毁时直接丢弃。
优先级中等——可以等 Session 稳定后再加。

---

## 10. ConnectionAuthenticator RateLimiter

**来源**: gap review（OpenClaw `src/gateway/auth-rate-limit.ts`）

限制单 credential / 单 IP 的连接建立速率和失败尝试次数。内存 token bucket，
kernel 重启重置 ok。策略草案：credential hash 连续失败 3 次 lockout 10s、10 次
lockout 5min；per remote_addr 每秒最多 10 次 `authenticate()`；成功后重置。
和 ConfigManager `auth` section 对接（参数可配）。
优先级中等——kernel 只 bind loopback，远程走反代一般反代自己有 rate limit。

---

## 11. kernel/logging/ Utility Module

**来源**: gap review（OpenClaw `src/logging/`）

跨子系统的 cross-cutting concern，不是 subsystem（无 startup/shutdown 生命周期、
无 `bind_section`、不依赖其他子系统）。`kernel/logging/` 作为普通工具模块：
- 统一 formatter + `~/.deepcli/logs/` 文件 handler + 大小上限轮转
- 日志级别从 ConfigManager 读
- 各子系统 `startup()` 里 `self._log = get_logger(__name__)`
- 与 DiagnosticBuffer（session 维度专用诊断）正交

---

## 12. REPL Tool Follow-ups

非 Windows 范围已实现，记录见 [`repl-rewrite.md`](./repl-rewrite.md)。

剩余事项：
- 在 Windows 真机或 Windows CI 上补 PowerShell / Cmd probe。
- 若要动态工具管理（`registerTool` / `listTools` 等），另起设计，不并入 v1。
