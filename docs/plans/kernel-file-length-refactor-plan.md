# Kernel File-Length Refactor Plan

## Background

根据 `docs/workflow/code-quality.md` 的编码规范：

- **`< 300 lines per file` — split if longer**
- **One primary class per file**

本计划先完成盘点和拆分设计，不在本文件中直接实施代码改动。

## Scope

- 扫描范围：`src/kernel/kernel/**/*.py`
- 统计时间：2026-04-26
- 结论：共有 **40** 个 Python 文件超过 300 行上限

## Over-limit Inventory

| Lines | File |
|---:|---|
| 2095 | `src/kernel/kernel/session/__init__.py` |
| 1285 | `src/kernel/kernel/orchestrator/orchestrator.py` |
| 939 | `src/kernel/kernel/orchestrator/tool_executor.py` |
| 749 | `src/kernel/kernel/tools/builtin/bash.py` |
| 565 | `src/kernel/kernel/tool_authz/authorizer.py` |
| 562 | `src/kernel/kernel/mcp/__init__.py` |
| 543 | `src/kernel/kernel/skills/__init__.py` |
| 511 | `src/kernel/kernel/schedule/scheduler.py` |
| 496 | `src/kernel/kernel/schedule/store.py` |
| 491 | `src/kernel/kernel/llm/__init__.py` |
| 490 | `src/kernel/kernel/orchestrator/compactor.py` |
| 488 | `src/kernel/kernel/memory/tools.py` |
| 486 | `src/kernel/kernel/protocol/acp/routing.py` |
| 476 | `src/kernel/kernel/mcp/client.py` |
| 472 | `src/kernel/kernel/mcp/oauth.py` |
| 457 | `src/kernel/kernel/protocol/acp/session_handler.py` |
| 438 | `src/kernel/kernel/gateways/base.py` |
| 424 | `src/kernel/kernel/tools/builtin/file_read.py` |
| 418 | `src/kernel/kernel/memory/background.py` |
| 413 | `src/kernel/kernel/memory/selector.py` |
| 408 | `src/kernel/kernel/memory/store.py` |
| 383 | `src/kernel/kernel/protocol/acp/event_mapper.py` |
| 383 | `src/kernel/kernel/git/__init__.py` |
| 376 | `src/kernel/kernel/skills/manifest.py` |
| 365 | `src/kernel/kernel/tools/builtin/powershell.py` |
| 353 | `src/kernel/kernel/orchestrator/history.py` |
| 348 | `src/kernel/kernel/tools/builtin/repl.py` |
| 342 | `src/kernel/kernel/tools/__init__.py` |
| 338 | `src/kernel/kernel/orchestrator/__init__.py` |
| 333 | `src/kernel/kernel/secrets/__init__.py` |
| 329 | `src/kernel/kernel/orchestrator/events.py` |
| 321 | `src/kernel/kernel/session/events.py` |
| 316 | `src/kernel/kernel/schedule/__init__.py` |
| 315 | `src/kernel/kernel/orchestrator/types.py` |
| 309 | `src/kernel/kernel/tools/tool.py` |
| 308 | `src/kernel/kernel/tasks/registry.py` |
| 308 | `src/kernel/kernel/schedule/delivery.py` |
| 307 | `src/kernel/kernel/skills/loader.py` |
| 306 | `src/kernel/kernel/session/store.py` |
| 303 | `src/kernel/kernel/tool_authz/bash_classifier.py` |

## Refactor Principles

1. 按子系统文档边界拆分，不跨子系统抽公共“万能 util”。
2. `__init__.py` 只保留 package API export，不承载核心实现。
3. 大类按职责拆为“协调层 + 纯函数/策略层 + IO 层”。
4. 先保持 public API 稳定，再做内部迁移，避免一次性大破坏。
5. 每个拆分批次都补齐：unit + e2e + closure-seam probe（按 workflow gate）。
6. 拆分单位是“模块/子系统”，不是单个文件长度排名；进入某个模块后，处理该模块内所有超限文件和相关 `__init__.py` 出口。

## Module Work Packages

Line-count inventory 是风险信号，不是执行顺序。实际拆分按模块成组推进，避免同一模块在多个批次里反复移动 API、测试和 closure seam。

### Session module

- 覆盖文件：`session/__init__.py` (2095)、`session/events.py` (321)、`session/store.py` (306)。
- 问题：会话生命周期、请求处理、权限往返、广播、队列、持久化、事件 schema 交织。
- 拆分目标：
  - `session/manager.py`（`SessionManager` 门面）
  - `session/prompt_queue.py`（FIFO 与 cancel）
  - `session/permission_roundtrip.py`（request/resolve futures）
  - `session/broadcast.py`（多连接广播策略）
  - `session/lifecycle.py`（create/load/bind/unbind）
  - `session/replay.py`（历史回放/恢复）
  - `session/events.py` 拆出 schema / mapper
  - `session/store.py` 拆出 repository / serialization

### Orchestrator module

- 覆盖文件：`orchestrator/orchestrator.py` (1285)、`tool_executor.py` (939)、`compactor.py` (490)、`history.py` (353)、`__init__.py` (338)、`events.py` (329)、`types.py` (315)。
- 问题：query loop、turn 状态机、工具执行、history、compaction、事件产出和 stop reason 耦合过高。
- 拆分目标：
  - `orchestrator/loop.py`（主循环）
  - `orchestrator/turn_engine.py`（单轮状态推进）
  - `orchestrator/stop_reason.py`（终止判定）
  - `orchestrator/emitters.py`（事件发射）
  - `orchestrator/deps_adapter.py`（deps 组装）
  - `orchestrator/tool_executor/`（core / validation / authorization / hooks / result_mapping）
  - `compactor.py` 拆为 budget / truncate / summarize
  - `history.py` 拆为 storage / projection / trim
  - `events.py` 与 `types.py` 拆为 schema / mapper
  - `__init__.py` 仅保留 package API export

### Memory module

- 覆盖文件：`memory/tools.py` (488)、`background.py` (418)、`selector.py` (413)、`store.py` (408)。
- 问题：tool 定义、后台抽取、检索排序、存储序列化分别超限；必须一次性按 MemoryManager seam 收束。
- 拆分目标：
  - `memory/tools/`（每个 memory tool 一文件 + shared validation）
  - `memory/background/`（extractor / deduper / conflict_checker / scheduler）
  - `memory/selector/`（retrieval / ranking / fusion）
  - `memory/store.py` 拆出 repository / serialization

### Schedule module

- 覆盖文件：`schedule/scheduler.py` (511)、`store.py` (496)、`__init__.py` (316)、`delivery.py` (308)。
- 问题：cron claim、heartbeat、timer、recovery、CRUD、delivery retry 分散超限。
- 拆分目标：
  - `scheduler.py` 拆为 claim / heartbeat / timer / recovery
  - `store.py` 拆为 CRUD / query
  - `delivery.py` 拆为 channel routers / retry policy
  - `__init__.py` 仅保留 package API export

### Tools and Tool AuthZ modules

- 覆盖文件：`tools/builtin/bash.py` (749)、`tool_authz/authorizer.py` (565)、`tools/builtin/file_read.py` (424)、`powershell.py` (365)、`repl.py` (348)、`tools/__init__.py` (342)、`tools/tool.py` (309)、`tool_authz/bash_classifier.py` (303)。
- 问题：工具 schema、风险判断、授权流、执行器和输出映射处在同一调用链，需要成组验证 tool search / execution / deny-ask-allow。
- 拆分目标：
  - `bash.py` 拆为 parser / risk / executor / output
  - `authorizer.py` 拆为 pipeline / decision / cache / permission_flow
  - `bash_classifier.py` 拆为 prompting / judge / postprocess
  - `file_read.py` 拆为 text / image / pdf readers
  - `powershell.py` 与 `repl.py` 拆为 parser / executor / result formatter
  - `tools/tool.py` 拆为 ABC / schema / validation helpers
  - `tools/__init__.py` 仅保留 package API export

### MCP module

- 覆盖文件：`mcp/__init__.py` (562)、`mcp/client.py` (476)、`mcp/oauth.py` (472)。
- 拆分目标：保留 `MCPManager` 导出；实现下沉至 `manager.py`、`registry.py`、`health_monitor.py`、`reconnect.py`，并把 client / OAuth 流程按 transport、session、token lifecycle 分层。

### Skills module

- 覆盖文件：`skills/__init__.py` (543)、`skills/manifest.py` (376)、`skills/loader.py` (307)。
- 拆分目标：拆为 `manager.py`、`discovery.py`、`activation.py`、`snapshot_cache.py`；manifest / loader 拆为 parse / normalize / load。

### LLM module

- 覆盖文件：`llm/__init__.py` (491)。
- 拆分目标：拆为 `manager.py`、`routing.py`、`model_registry.py`、`resolution.py`，`__init__.py` 仅保留 package API export。

### Protocol ACP module

- 覆盖文件：`protocol/acp/routing.py` (486)、`session_handler.py` (457)、`event_mapper.py` (383)。
- 拆分目标：
  - `routing.py` 拆为 request_router / notification_router / error_mapper
  - `session_handler.py` 拆为 methods_by_domain（session / task / mode / config）
  - `event_mapper.py` 拆为 streaming / tool / task mappers

### Remaining modules

- `gateways/base.py` (438)：拆为 protocol / base lifecycle / chunking helpers。
- `git/__init__.py` (383)：拆为 `manager.py`、`context_probe.py`、`tool_registration.py`。
- `secrets/__init__.py` (333)：拆为 `manager.py`、`store.py`、`resolver.py`、`auth_routes.py`。
- `tasks/registry.py` (308)：拆为 state store / output collector。

## Execution Plan (Incremental)

### Batch A — session module
- 目标：完成 `src/kernel/kernel/session/` 内所有超限文件拆分，并保持 `SessionManager` public import path 稳定。
- 验证：`session/prompt`、permission roundtrip、多连接 broadcast、session replay/store probe。

### Batch B — memory module
- 目标：完成 `src/kernel/kernel/memory/` 内所有超限文件拆分，按 MemoryManager 的 tools / background / selector / store seam 验收。
- 验证：memory tool 调用、background extraction、selection/ranking、store read/write probe。

### Batch C — orchestrator module
- 目标：完成 `src/kernel/kernel/orchestrator/` 内所有超限文件拆分，包括 tool executor、compactor、history、events/types。
- 验证：query loop、tool hooks、stop reason、history compaction、event emission closure-seam probe。

### Batch D — tools + tool_authz modules
- 目标：完成 `tools/` 与 `tool_authz/` 成组拆分，保证工具注册、权限判断、执行结果映射仍在同一回归范围内。
- 验证：tool search / execution / deny-ask-allow flow、bash classifier、file read/powershell/repl probe。

### Batch E — schedule module
- 目标：完成 `schedule/` 内 scheduler/store/delivery/init 拆分。
- 验证：cron claim、timer fire、delivery retry、recovery probe。

### Batch F — mcp + skills modules
- 目标：完成 `mcp/` 与 `skills/` 的 manager/init/client/loader/manifest 拆分。
- 验证：MCP connect/health/reconnect/OAuth seam；skill discovery/load/activation probe。

### Batch G — llm + protocol/acp modules
- 目标：完成 `llm/` 与 `protocol/acp/` 拆分，保持 model resolution 与 ACP routing/event mapping 兼容。
- 验证：model alias resolution/routing probe；ACP request/notification routing、session method、event mapper probe。

### Batch H — remaining modules and final cleanup
- 目标：完成 gateways、git、secrets、tasks 等剩余模块；全量清零 300+ 文件；统一 package exports。
- 验证：全量 lint/type/test + 关键 e2e 探针 + line-count re-scan。

## Acceptance Criteria

- `src/kernel/kernel/**/*.py` 中 **0 个文件** 超过 300 行。
- 每个 package 的 `__init__.py` 只承担 export，不含主逻辑。
- 关键闭包 seam 都有 probe/e2e 证据。
- `docs/plans/progress.md` 记录每批次完成状态。

## Re-scan Command

```bash
find src/kernel/kernel -name '*.py' -type f -print0 \
  | xargs -0 wc -l \
  | sort -nr
```
