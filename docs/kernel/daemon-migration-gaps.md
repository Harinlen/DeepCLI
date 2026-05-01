# Kernel vs Daemon — 核心功能差距分析

> **Date**: 2026-04-21
>
> 对比 `src/kernel/` (当前实现) 与 `archive/daemon/` (已归档的前代实现)，
> 梳理 kernel 尚未迁移或未完成的核心功能。

---

## 关键差距

### 1. ACP EventMapper — stub，客户端收不到流式事件

| | Daemon | Kernel |
|---|---|---|
| 传输层 | WebSocket 直推 `text_delta` / `tool_call_start` 等事件 | ACP (JSON-RPC 2.0) |
| 实现状态 | 完整 | **`AcpEventMapper.map()` 是 stub — log 后丢弃所有 Orchestrator 事件** |

**影响**: TUI / 任何客户端无法接收实时流式输出（text delta、thinking delta、
tool call start/result、permission request 等）。这是 kernel 可用的前提条件。

**修复方向**: 实现 `AcpEventMapper`，将 Orchestrator 事件映射为 ACP
`session/update` notification 推送给已连接的客户端。

---

### 2. Session resume 历史回放不完整

| | Daemon | Kernel |
|---|---|---|
| 持久化 | Append-only JSONL + meta.json | SQLite (WAL mode) + sidecar spillover |
| Resume | `rebuild.py` 从 JSONL 完整重建 Conversation 对象 | **`load_session` 创建 orchestrator 时传入空历史** (TODO ~line 1510) |

**影响**: 断线重连或 resume session 后 LLM 丢失全部上下文，等于新开一个对话。

**修复方向**: `load_session` 需要从 `SessionStore.read_events()` 重建
conversation history 并传入 orchestrator。

---

### 3. ~~BashClassifier (LLM 安全判定) — stub~~ ✅ CLOSED

| | Daemon | Kernel |
|---|---|---|
| 实现 | `bash_safety.py` 对危险命令做分类 | **已修复**: 复合命令只读分类 + LLM judge 路径完整 + 配置可用 |

**已完成**:
- `_COMPOUND_SAFE_COMMANDS` + `_GIT_READ_ONLY` 移植自 daemon `bash_safety.py`
- `_is_compound_safe()` 对 `&&`/`||`/`;`/`|` 复合命令做子命令级只读分类
- `bash_safe_commands` 用户配置字段（`PermissionsSection`）+ hot-reload
- `destructive_warning()` 移植 daemon destructive pattern warnings
- LLM judge 代码已完整（`BashClassifier`），用户配置 `bash_judge` model role 即可启用
- Sub-shell（`$()` / 反引号）保守处理为 ask

---

## 次要差距

### 4. MiniMax Provider

Daemon 有独立的 `MiniMaxProvider`。Kernel 用 `NvidiaProvider`
(OpenAI-compatible 变体) 替代，但没有 MiniMax 专用适配。

**优先级**: 低。除非有 MiniMax 使用需求。

### 5. Agent Browser (Headless 自建浏览器)

Daemon 通过 `agent_browser_cli.py` 与 Rust 编写的 `agent-browser` daemon
通信，管理内置 Chrome 实例，支持 `PageFetchTool` / `BrowserTool`。

Kernel 的 `WebFetch` 走 httpx / Playwright / 第三方 API (Exa, Firecrawl,
Tavily 等)，没有自建 browser daemon。

**优先级**: 中。功能覆盖面不同，但第三方 API 方案在多数场景够用。

### 6. Image Cache

Daemon 有 content-addressed image cache (`sessions/image_cache.py`)，
按 SHA256 存储工具返回的图片，JSONL 只记引用。

Kernel 用 tool-result spillover (大结果写 sidecar 文件) 替代，没有独立的
image cache。

**优先级**: 低。spillover 机制已覆盖大文件场景。

### 7. File State Cache (防 stale-write)

Daemon 的 `file_state_cache.py` 跟踪每个文件最后写入内容，防止 LLM 在
外部修改后基于过期内容做 edit。

Kernel 未见等价机制。

**优先级**: 中。可能导致 LLM edit 覆盖用户在编辑器里的修改。

### 8. Tool Denial Counter

Daemon 跟踪连续拒绝次数，3 次后提示用户 "考虑使用 Always Allow"。

Kernel 没有这个 UX 优化。

**优先级**: 低。纯 UX 改善。

### 9. Skills Config Binding

Kernel 有 TODO — per-skill config override 未接线。

**优先级**: 低。当前没有 skill 需要运行时配置覆盖。

---

## 两边都没实现

| 功能 | 说明 |
|------|------|
| Context Collapse (Layer 1d) | 两边都 feature-flagged 但未实现，不算差距 |

---

## 优先级建议

```
P0  ACP EventMapper          — 没有它客户端完全不可用
P0  Session resume 历史回放  — 没有它 resume 等于新开对话
✅  BashClassifier           — 已关闭 (复合命令分类 + LLM judge + destructive warnings)
P1  File State Cache         — 数据安全，可能覆盖用户修改
P2  Agent Browser            — 架构选择差异，按需决定
P3  其余                     — UX 优化或无需求驱动
```
