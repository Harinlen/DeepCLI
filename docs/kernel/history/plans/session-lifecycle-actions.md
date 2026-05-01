# Session 生命周期操作计划 — Delete、Rename、Archive

**范围**: kernel session subsystem + ACP protocol extensions
**状态**: implemented
**消费者**: CLI Phase D session picker / `/session` commands、未来 Web / IDE clients

> 这是更大计划 [`session-acp-compliance-refactor.md`](session-acp-compliance-refactor.md)
> 的一部分。本文是用户可见 lifecycle actions 的详细批次计划。

## 目标

给 kernel 增加用户可见的 session lifecycle 操作：

- **Rename**：显式设置 session 标题。
- **Archive / unarchive**：从默认 recent list 中隐藏 session，但保留历史。
- **Delete**：永久删除 session 和它的 sidecar 文件。

这些动作必须由 kernel 拥有，通过 ACP 暴露，由 thin client 消费。CLI 不允许直接读取或修改
`sessions.db`。

## 当前状态

2026-04-28 核验结果：

| 能力 | 当前状态 | 证据 |
|---|---|---|
| Delete storage primitive | 内部已存在 | `SessionStore.delete_session()` 删除 SQLite rows；`SessionManager.delete_session()` 先关闭 active runtime，再调用 store |
| Delete ACP route | 已实现 | `REQUEST_DISPATCH` 已有 `session/delete`；active session 默认需要 `force=true` |
| Rename storage primitive | 部分存在 | `SessionStore.update_title()` 更新 `title` column；`SessionInfoChangedEvent` 可以广播 title change |
| Rename ACP route | 已实现 | `session/rename` 设置 user-owned title 并广播 `session_info_update` |
| Archive storage | 已实现 | `ConversationRecord` 有 `archived_at` / `title_source`，schema v2 migration |
| Archive ACP route | 已实现 | `session/archive` 通过 `archived` bool 归档/取消归档 |
| List summaries | 已扩展 | `AcpSessionInfo` 有 `updatedAt`、`archivedAt`、`titleSource` 和 `_meta` |

## 语义

### Delete

Delete 表示永久移除：

- 如果 session 当前已加载，先关闭 runtime。
- 从 SQLite 中删除 session row 和所有 event rows。
- 删除 per-session sidecars，例如 `tool-results/`。
- 关闭 runtime 时顺带清理 per-session permission grants / task state。
- 之后 `session/load` 必须以 `ResourceNotFoundError` 失败。
- 默认 list 和 archived list 都不再显示该 session。

实现时的开放问题：是否允许删除当前请求连接所绑定的 session。保守默认：
只有 `force=true` 时允许；否则返回 `InvalidRequest`，要求 client 先切换到别的 session。

### Rename

Rename 表示用户拥有的标题覆盖：

- 把 session title 更新为非空字符串，trim 后限制长度。
- 持久化到 `sessions.title`。
- append `SessionInfoChangedEvent(title=...)`，让 replay/history 能看到这次变化。
- 向该 session 的 connected clients 广播 `session_info_update`。
- 防止 auto-title 逻辑覆盖用户手动 rename。

最后一点需要显式 title source。否则现有 first-message / AI-title 路径可能覆盖用户标题。
推荐实现：增加 `title_source` column，取值 `auto | user`。如果短期想避免 schema 变更，
就把 rename 放到 archive schema batch 之后一起做。

### Archive / Unarchive

Archive 表示保留历史，但从默认列表中收起：

- 设置 `sessions.archived_at` 为 ISO-8601 UTC timestamp。
- 默认 `session/list` 排除 archived sessions。
- `session/list(include_archived=true)` 返回 active + archived。
- 可选 `session/list(archived_only=true)` 支撑后续 Archive 视图。
- archived session 仍允许 `session/load`。
- 对 archived session 执行 `session/prompt` 默认不自动 unarchive；只有 client 显式请求时才恢复。

Unarchive 清空 `archived_at`。

## ACP 表面

新增 DeepCLI extension methods：

| Method | Params | Result |
|---|---|---|
| `session/rename` | `{ sessionId, title }` | `{ session: AcpSessionInfo }` |
| `session/delete` | `{ sessionId, force?: boolean }` | `{ deleted: boolean }` |
| `session/archive` | `{ sessionId, archived?: boolean }` | `{ session: AcpSessionInfo }` |

扩展 `session/list` params：

```python
include_archived: bool = False
archived_only: bool = False
```

扩展 session summary schemas：

```python
archived_at: str | None = None
title_source: Literal["auto", "user"] | None = None
```

Wire format 保持 camelCase：

- `sessionId`
- `includeArchived`
- `archivedOnly`
- `archivedAt`
- `titleSource`

## 存储计划

如果 archive 和 title-source 一起做，需要 session DB schema migration：

- `sessions.archived_at TEXT NULL`
- `sessions.title_source TEXT NULL`

因为 D21 规定 `SCHEMA_VERSION == kernel major`，这是 kernel major schema bump：

- `kernel.__version__` 从 `1.0.0` bump 到 `2.0.0`
- `SessionStore` `SCHEMA_VERSION` 从 `1` bump 到 `2`
- 在 `src/kernel/kernel/session/migrations.py` 增加 `_migrate_to_2()`

Migration SQL：

```sql
ALTER TABLE sessions ADD COLUMN archived_at TEXT;
ALTER TABLE sessions ADD COLUMN title_source TEXT;
```

Fresh install 由 `ConversationRecord` 创建新 columns。

## 实现批次

### S1 — Delete ACP Route

无需 schema 变更。

文件：

- `src/kernel/kernel/protocol/interfaces/contracts/delete_session_params.py`
- `src/kernel/kernel/protocol/interfaces/contracts/delete_session_result.py`
- `src/kernel/kernel/protocol/interfaces/session_handler.py`
- `src/kernel/kernel/protocol/acp/schemas/session.py`
- `src/kernel/kernel/protocol/acp/routing.py`
- `src/kernel/kernel/session/api/handlers.py`
- `src/kernel/kernel/session/lifecycle/runtime.py`

工作：

- 把现有 `delete_session()` 提升为 `SessionHandler` public contract。
- 增加 `session/delete` ACP schemas 和 routing。
- 确保 `SessionManager.delete_session()` 通过 `SessionStore.aux_dir()` 删除 sidecar files；
  当前 store doc 明确说 sidecar 由 caller 负责。
- 决定 active-session 行为：
  - 无 `force`：如果请求连接绑定在该 session，或 session 有 active senders / in-flight turn，则拒绝。
  - `force=true`：cancel/close runtime，unbind senders，删除 rows 和 sidecars。
- `{deleted: false}` 只用于 row 已经不存在；unsafe state 应 raise。

测试：

- Unit/integration：delete evicted session 会删除 DB rows 和 sidecars。
- Unit/integration：delete active session without force 会拒绝。
- Unit/integration：delete active session with force 会关闭 runtime 并删除 row。
- E2E：`session/new -> session/delete -> session/load` 返回 not found。

Closure seams：

- ACP routing -> 真实 `SessionManager.delete_session()`。
- Runtime close -> store delete -> sidecar cleanup。

### S2 — Archive Schema And List Filtering

需要 schema 变更。

文件：

- `src/kernel/kernel/__init__.py`
- `src/kernel/kernel/session/models.py`
- `src/kernel/kernel/session/migrations.py`
- `src/kernel/kernel/protocol/interfaces/contracts/list_sessions_params.py`
- `src/kernel/kernel/protocol/interfaces/contracts/list_sessions_result.py`
- `src/kernel/kernel/protocol/acp/schemas/session.py`
- `src/kernel/kernel/protocol/acp/routing.py`
- `src/kernel/kernel/session/store.py`
- `src/kernel/kernel/session/api/handlers.py`

工作：

- 增加 `archived_at` 和 `title_source` columns。
- 增加 `SessionStore.archive_session(session_id, archived_at)` 和 summary projection。
- 扩展 `list_sessions()` filtering：
  - 默认排除 archived
  - `include_archived` 返回 active + archived
  - `archived_only` 只返回 archived
  - `archived_only && !include_archived` 合法，含义是 only archived
- 在 list results 中返回 archive metadata。

测试：

- 从 v1 DB migration 会增加两个 columns 且不丢数据。
- Fresh DB 有两个新 columns。
- 默认 list 排除 archived。
- include / only archived 组合按文档工作。

Closure seam：

- migration -> ORM model -> store list filtering -> ACP list response。

### S3 — Archive / Unarchive ACP Route

依赖 S2。

文件：

- `archive_session_params.py`
- `archive_session_result.py`
- `SessionHandler`
- ACP schemas/routing
- `SessionManager.archive_session()`

工作：

- `session/archive` 默认设置 `archived_at = now`。
- 传 `archived=false` 时清空 `archived_at`。
- unknown session 返回 `ResourceNotFoundError`。
- 如果 archived session 当前 active，不关闭 runtime；archive 只是 list state，不是 runtime shutdown。
- 广播带扩展 meta 的 `session_info_update`，或引入更丰富的 list refresh event。
  MVP：response 返回 updated summary；client 按需刷新 list。

测试：

- E2E：archive 后默认 `session/list` 隐藏该 session。
- E2E：`includeArchived` 能看到它。
- E2E：unarchive 后它回到默认 list。
- E2E：archived session 仍可 `session/load`。

Closure seam：

- ACP route -> store archive state -> `session/list` filtering。

### S4 — Rename ACP Route

可复用 S2 schema，因为 `title_source` 能阻止用户 rename 被自动标题覆盖。

文件：

- `rename_session_params.py`
- `rename_session_result.py`
- `SessionHandler`
- ACP schemas/routing
- `SessionManager.rename_session()`
- `session/turns/runner.py` 中的 auto-title path
- `session/client_stream/event_mapper.py`

工作：

- 校验 title：
  - trim whitespace
  - reject empty
  - 初始 cap 200 chars，与 first-message title seed 保持一致
- 更新 store：`title`、`title_source="user"`、`modified=now`。
- append `SessionInfoChangedEvent(title=title)`。
- broadcast `session_info_update`。
- Auto-title path 必须跳过 `title_source="user"` 的 sessions。
- load session 时要从 DB 恢复 `title_source`，或如果 event/meta 中新增了来源信息则从那里推导。

测试：

- Rename unknown session 返回 not found。
- Empty title 被拒绝。
- Rename active session 会广播 update。
- Rename evicted session 会更新 DB 和 list result。
- 用户 rename 不被 first-message / AI title 覆盖。
- E2E：`session/rename -> session/list` 显示新 title。

Closure seam：

- ACP route -> store update -> event append -> broadcast/replay。

## 文档更新

实现时同步更新：

- `docs/kernel/interfaces/protocol.md`
- `docs/kernel/subsystems/session.md`
- `docs/kernel/subsystems/commands.md`
- `docs/cli/history/phase-d-session-config-theme.md`
- `docs/plans/progress.md`

在 ACP routes 真正落地前，CLI Phase D 应把这些能力视为 optional kernel capabilities。
S1/S3/S4 落地后，CLI selector 才能显示 Delete / Rename / Archive，而不是隐藏它们。

## 验证命令

最低实现验证：

```bash
uv run pytest tests/kernel/session -q
uv run pytest tests/kernel/protocol -q
uv run pytest tests/e2e/test_session_lifecycle_actions_e2e.py -q -m e2e
uv run ruff format src/kernel/kernel/session src/kernel/kernel/protocol tests/kernel tests/e2e
uv run ruff check src/kernel/kernel/session src/kernel/kernel/protocol tests/kernel tests/e2e
uv run mypy src/kernel
```

最终完成仍然按 `docs/workflow/definition-of-done.md`：closure seam inventory、
真实 subsystem probes、E2E 输出贴进报告、docs 同步。

## 建议顺序

1. **S1 delete**：最先做，立即有用且无 schema bump。
2. **S2 archive schema**：一次有意识的 schema/version migration。
3. **S3 archive route**：schema 存在后再接 route。
4. **S4 rename route**：最后做，复用同一次 schema bump 的 `title_source`。

如果短期更想避免 kernel major bump，就只做 S1，把 archive/rename 用户覆盖推迟到计划内的
schema window。
