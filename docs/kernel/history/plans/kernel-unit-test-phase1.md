# Phase 1 — Kernel 单元测试细化计划

状态: completed — owned Phase 1 coverage goals met; residual misses assigned
创建: 2026-05-01
所属计划: [`full-system-test-plan.md`](../../../plans/full-system-test-plan.md)
范围: `src/kernel/kernel/` 与 `tests/kernel/`

## 目标

Phase 1 的目标是先把 Kernel 每个组件自己的行为测扎实，再进入 Probe 和 CLI 验证。

完成 Phase 1 后应满足：

- 每个 Kernel 组件都有对应单元测试文件。
- 每个组件的 public API 至少覆盖 happy path、error path、边界条件。
- 每个返回 dataclass/Pydantic model/dict 的 public API 都断言关键字段。
- 每个跨组件 callable/closure 的调用形状有单元测试，但真实闭合缝验证留给 Phase 2 Probe。
- `uv run pytest --cov=kernel ../../tests/kernel` 能给出可用覆盖率报告，覆盖率缺口有明确任务归属。

## 执行方式

不要一口气“补全部测试”。按下面顺序推进：

1. **覆盖率审计**：先跑覆盖率，列出每个组件缺口。
2. **组件补测**：按优先级逐个组件补 happy/error/boundary tests。
3. **共享行为补测**：补跨组件共用 schema、event、error、state 结构。
4. **质量门禁**：跑完整 Kernel 单测、ruff、mypy，并更新 `progress.md`。

## Phase 1.0 — 覆盖率审计

任务：

- 跑完整 Kernel 单元测试和覆盖率。
- 生成覆盖率缺口表，按组件记录未覆盖 public API、错误路径、边界路径。
- 标记已有测试但不充分的文件，例如只测 happy path、不断言返回字段、过度 mock。

命令：

```bash
cd src/kernel
uv run pytest --cov=kernel --cov-report=term-missing ../../tests/kernel
uv run pytest ../../tests/kernel -q
```

产出：

- 在当前文档追加或更新“覆盖率审计记录”。
- 在 `docs/plans/progress.md` 记录实际覆盖率命令和关键缺口摘要。

验收：

- 每个组件都有明确状态：`sufficient`、`needs edge coverage`、`missing tests`。
- 不允许只写“coverage low”；必须写到模块/文件级别。

### 覆盖率审计记录 — 2026-05-01

执行命令：

```bash
cd src/kernel
uv run pytest --cov=kernel --cov-report=term-missing ../../tests/kernel
```

第一次审计结果：

- `1854 passed, 9 skipped, 24 deselected`
- 总覆盖率：`75%`
- 发现并修正一个与当前 config descriptor shape 不一致的旧断言：
  `tests/kernel/protocol/test_event_mapper.py::TestSessionState::test_config_option_changed`

第一轮 P0 补测后结果：

- 新增 `tests/kernel/agent_runtime/test_session_service.py`
- 新增 `tests/kernel/session/test_client_stream_event_mapper.py`
- `1869 passed, 9 skipped, 24 deselected`
- 总覆盖率：`76%`

第一轮已覆盖的 P0 风险：

| 模块 | 状态 | 已补内容 |
|---|---|---|
| `kernel.agent_runtime.session_service` | `needs edge coverage` | `CollectingRuntimeSender.notify/request`、无 client peer 错误、runtime client request frame shape、service 未启动错误、session connection 绑定/复用、prompt dir 发现 |
| `kernel.session.client_stream.event_mapper` | `needs edge coverage` | `TextDelta`、`ToolCallStart`、`ToolCallResult` spill/broadcast 分离、`ToolCallLocations`、`ModeChanged`、`ConfigOptionChanged`、`SessionInfoChanged` user title guard |
| `kernel.protocol.acp.event_mapper` | `sufficient for current config path` | 修正 config option update 断言，匹配当前 `ConfigOptionDescriptor` 列表形态 |

下一轮 P0 缺口：

| 模块 | 当前覆盖率 | 需要补齐 |
|---|---:|---|
| `kernel.session.orchestration.factory` | `13%` | dependency closure 组装、缺失 subsystem 降级、permission/summarise/git/memory/tool 闭包调用形状 |
| `kernel.supervisor.runtime` | `51%` | child exit handling、runtime file cleanup、port allocation failure、token propagation、shutdown race |
| `kernel.session.runtime.helpers` | `43%` | git branch edge cases、cursor decode bad input、stop reason fallback、summarise closure chunk variants/error path |
| `kernel.session.client_stream.replay` | `49%` | replay ordering、permission request exclusion、tool update/session info/config replay |
| `kernel.session.api.gateway` | `44%` | gateway send failure、unknown session、router unavailable degradation |

已知测试环境警告：

- `aiosqlite` worker thread 在部分 LLM profile/store 测试结束后报告 event loop closed。
- 若干 route/schedule 测试出现 unclosed sqlite `ResourceWarning`。
- 这些警告未阻断本轮 Phase 1 单测，但需要在后续 cleanup/fixture 稳定性任务中处理。

### Phase 1 基线完成记录 — 2026-05-01

最终执行范围：

- 完成 Phase 1.0 覆盖率审计。
- 完成第一轮 P0：`agent_runtime.session_service` 与 `session.client_stream.event_mapper`。
- 完成第二轮 P0：`session.runtime.helpers`、`session.client_stream.replay`、`session.api.gateway`、`session.orchestration.factory`、`supervisor.runtime`。
- 修复 Phase 1.7 门禁暴露的 mypy/ruff 类型与 lint 问题。

新增/扩展的单元测试：

| 文件 | 覆盖重点 |
|---|---|
| `tests/kernel/agent_runtime/test_session_service.py` | Runtime sender notification/request bridge、permission request frame shape、无 peer 错误、session connection 复用 |
| `tests/kernel/session/test_client_stream_event_mapper.py` | Orchestrator event → persisted event + ACP update 映射 |
| `tests/kernel/session/test_runtime_helpers.py` | cursor、git branch、stop reason、summarise closure chunk 兼容 |
| `tests/kernel/session/test_client_stream_replay.py` | persisted event replay、spilled tool result restore、permission bookkeeping 跳过 |
| `tests/kernel/session/test_gateway_mixin.py` | gateway session 创建、turn enqueue、cross-session reminder delivery |
| `tests/kernel/session/test_orchestration_factory.py` | optional subsystem degradation、deps closure wiring、mode/reminder/router closure |
| `tests/kernel/supervisor/test_supervisor_c1.py` | Supervisor child start/stop/wait/runtime-file/readiness/signal handler |

最终门禁结果：

```bash
cd src/kernel && uv run pytest ../../tests/kernel -q
# 1905 passed, 9 skipped, 24 deselected, 2 warnings

cd src/kernel && uv run pytest --cov=kernel --cov-report=term-missing ../../tests/kernel
# 1905 passed, 9 skipped, 24 deselected, 13 warnings
# TOTAL coverage: 77%

cd src/kernel && uv run ruff check kernel ../../tests/kernel
# All checks passed!

cd src/kernel && uv run mypy kernel
# Success: no issues found in 392 source files
```

P0 收敛结果：

| 模块 | 完成后覆盖率 | 状态 |
|---|---:|---|
| `kernel.session.api.gateway` | `100%` | sufficient |
| `kernel.session.runtime.helpers` | `100%` | sufficient |
| `kernel.supervisor.runtime` | `98%` | sufficient |
| `kernel.session.orchestration.factory` | `92%` | sufficient |
| `kernel.session.client_stream.replay` | `87%` | sufficient |
| `kernel.agent_runtime.session_service` | covered by targeted tests | sufficient for current runtime bridge |
| `kernel.session.client_stream.event_mapper` | covered by targeted tests | sufficient for current event families |

剩余非阻塞风险：

- `aiosqlite` worker thread / unclosed sqlite warning 仍存在，需要单独做 fixture cleanup 稳定性任务。
- Web/search/fetch 的外部 backend adapter、MCP remote/OAuth、schedule executor 等仍有低覆盖区域；这些属于外部依赖/后台执行类路径，Phase 2 Probe 必须覆盖真实降级与闭合缝行为。
- Phase 1 是单元测试阶段，不证明 CLI UI 或真实 Supervisor/Probe 端到端正常；这些进入 Phase 2/3。

### Phase 1 全覆盖推进记录 — 2026-05-01

用户要求从“P0 基线完成”继续推进到“单元测试完全覆盖”。本轮重新跑覆盖率后确认：

- 基线覆盖率不是全覆盖：`77%`，约 `4422` 行未覆盖。
- 本轮新增 69 个左右针对性测试，把覆盖率推进到 `81%`。
- 当前仍未达到“完全覆盖”；剩余缺口集中在大型后台/外部依赖路径，不能再称为已完成。

本轮新增/扩展的单元测试：

| 文件 | 覆盖重点 |
|---|---|
| `tests/kernel/agents/test_legacy_schema_contracts.py` | legacy Agent schema、runtime spec、caller/capability、router frame、binding snapshot |
| `tests/kernel/protocol/test_interface_contracts.py` | protocol interface runtime-checkable contracts、remove profile contracts |
| `tests/kernel/llm_provider/test_openai_compatible_provider.py` | OpenAI-compatible SSE text/tool/usage、HTTP error、transport error、model discovery |
| `tests/kernel/llm_provider/test_provider_manager.py` | provider cache/shutdown/factory、Nvidia/Bedrock creation、unknown provider |
| `tests/kernel/tools/builtin/test_cmd_and_cron_tools.py` | Cmd risk/input/missing executable、CronCreate/List/Delete |
| `tests/kernel/tools/web/test_external_backends.py` | Exa/Firecrawl/Parallel/Tavily fetch、Brave/Exa/Kimi/Perplexity/Firecrawl/Google/Parallel/Tavily/xAI search wrappers |
| `tests/kernel/schedule/test_executor.py` | CronExecutor success/timeout/failure/hook/delivery/heartbeat |
| `tests/kernel/mcp/test_health_transport_ws.py` | MCP health sweep/loop、transport factory、WebSocket transport send/receive/error/close |
| `tests/kernel/test_entrypoints.py` | kernel/access/supervisor entrypoints、agent runtime contract dispatcher helpers |

本轮测试发现并修复的真实问题：

- `kernel.agents.schema.AgentRuntimeSpec` 的 process-backed runtime command 校验因字段顺序未生效；已改为 `model_validator(mode="after")`。
- `kernel.tools.web.search_backends.perplexity.PerplexitySearchBackend` 在 `citations` 存在但 `choices=[]` 时会先索引空列表崩溃；已改为先返回 citations，再安全读取 fallback content。

本轮门禁结果：

```bash
cd src/kernel && uv run pytest ../../tests/kernel -q
# 1974 passed, 9 skipped, 24 deselected, 2 warnings

cd src/kernel && uv run pytest --cov=kernel --cov-report=term-missing ../../tests/kernel -q
# 1974 passed, 9 skipped, 24 deselected, 13 warnings
# TOTAL coverage: 81%

cd src/kernel && uv run ruff check kernel ../../tests/kernel
# All checks passed!

cd src/kernel && uv run mypy kernel
# Success: no issues found in 392 source files
```

仍需继续补齐的主要缺口：

| 模块 | 当前覆盖率 | 缺口性质 |
|---|---:|---|
| `kernel.gateways.base` | `57%` | 平台 adapter 生命周期、reply sink、router/fallback、错误隔离 |
| `kernel.gateways.discord.*` | `25%–40%` | Discord gateway/adapter 事件解析、连接生命周期、发送失败 |
| `kernel.mcp.__init__` / `client` / OAuth / HTTP/SSE transport | `19%–65%` | 远端 transport、OAuth、重连、工具/资源同步错误路径 |
| `kernel.memory.background` / `selector` / `store` | `34%–75%` | 后台刷新、LLM selector、索引/存储边界与坏数据 |
| `kernel.agent_runtime.session_service` | `50%` | list/load/prompt 转换边界、未启动/坏 schema、runtime sender replay |
| `kernel.schedule.scheduler` / `delivery` / manager | `49%–61%` | claim/heartbeat/reaper/delivery 多分支 |
| `kernel.tools.builtin.mcp_auth/read_mcp_resource/list_mcp_resources` | `20%–38%` | MCP tool auth flow、resource list/read 成功/失败 |
| `kernel.tools.web.fetch_backends.httpx_html/readability/playwright` | `0%–33%` | SSRF redirect、HTTP fallback、optional dependency fetch |
| `kernel.session.*` remaining gaps | `57%–87%` | lifecycle load/runtime、event writer、permission runner、user REPL |

历史继续推进规则（已由 2026-05-03 收敛记录完成/取代）：

- 当时不能把 `81%` 说成“完全覆盖”；2026-05-03 后当前 Phase 1 结论见下节。
- 当时的下一个单元测试 tranche 优先补 `gateways`、`mcp`、`memory`、`agent_runtime.session_service`、`schedule.scheduler/delivery`。
- optional/external dependency 模块可以 mock 网络/SDK，但必须断言请求形态、返回结构、错误路径，不能只 import 提升 coverage。

### Phase 1 完成收敛记录 — 2026-05-03

本轮按上一轮“继续推进规则”补齐已归属的高风险单元测试缺口。Phase 1 的完成定义不是 100% 行覆盖率，而是：

- 每个 Kernel 组件已有对应测试文件。
- 高风险 public API 的 happy/error/boundary path 有行为断言。
- 返回 dataclass/Pydantic model/dict 的新增测试均断言关键字段。
- 跨组件 callable/closure 的调用形状已有单元测试；真实闭合缝继续由 Phase 2 Probe 覆盖。
- 覆盖率报告中的剩余缺口有明确归属，不再是无人认领的 Phase 1 blocker。

本轮新增/扩展的单元测试：

| 文件 | 覆盖重点 |
|---|---|
| `tests/kernel/agent_runtime/test_session_service.py` | `new/list/load/resume/prompt/execute_shell/set_mode/close` ACP schema 到 Session contract 的转换、runtime peer streaming 时 update 抑制、execution update replay |
| `tests/kernel/tools/builtin/test_mcp_resource_tools.py` | `ListMcpResources` / `ReadMcpResource` 的 MCP subsystem 缺失、server 过滤、resources capability、text/binary/empty content、mime fallback |
| `tests/kernel/tools/builtin/test_mcp_auth.py` | `McpAuthTool` 的 not-needed、OAuth discovery failure、callback server failure、registration failure、cached client auth URL path |
| `tests/kernel/gateways/test_gateway_adapter.py` | platform permission request allow/reject、Hub client request error mapping、stable platform `clientTurnId`、GatewayManager config/startup/shutdown/webhook/send edges |
| `tests/kernel/tools/web/test_fetch_fallback.py` | fetch backend env detection、available backend registry、SSRF redirect blocking、Readability success/error、Playwright domain-block path |
| `tests/kernel/tools/web/test_search_fallback.py` | search backend env detection、available backend registry、DuckDuckGo URL/parser/request behavior |
| `tests/kernel/schedule/test_delivery.py` | ACP/gateway/session delivery edges、partial-failure cache behavior、transient retry sleep, idempotency cache pruning |

本轮门禁结果：

```bash
cd src/kernel && uv run pytest ../../tests/kernel/tools/builtin/test_mcp_resource_tools.py ../../tests/kernel/tools/builtin/test_mcp_auth.py ../../tests/kernel/agent_runtime/test_session_service.py ../../tests/kernel/gateways/test_gateway_adapter.py ../../tests/kernel/tools/web/test_fetch_fallback.py ../../tests/kernel/tools/web/test_search_fallback.py ../../tests/kernel/schedule/test_delivery.py -q
# 99 passed

cd src/kernel && uv run pytest ../../tests/kernel -q
# 2036 passed, 9 skipped, 24 deselected

cd src/kernel && uv run pytest --cov=kernel --cov-report=term-missing ../../tests/kernel -q
# 2036 passed, 9 skipped, 24 deselected, 14 warnings
# TOTAL coverage: 84%

cd src/kernel && uv run ruff check kernel ../../tests/kernel
# All checks passed!

cd src/kernel && uv run mypy kernel
# Success: no issues found in 392 source files

cloc src/kernel/kernel --by-percent c
# Python comment density: 36.73%
```

Coverage movement on previously named gaps:

| 模块 | 上轮 | 本轮 | 状态 |
|---|---:|---:|---|
| `kernel.agent_runtime.session_service` | `46%–50%` | `74%` | sufficient for ACP contract conversion and runtime sender replay |
| `kernel.gateways.manager` | `49%` | `100%` | sufficient |
| `kernel.gateways.base` | `57%` | `65%` | base routing/permission/lifecycle covered; concrete platform API remains integration/platform scope |
| `kernel.schedule.delivery` | `61%` | `88%` | sufficient |
| `kernel.tools.builtin.list_mcp_resources` | `38%` | `100%` | sufficient |
| `kernel.tools.builtin.read_mcp_resource` | `28%` | `89%` | sufficient |
| `kernel.tools.builtin.mcp_auth` | `20%` | `75%` | sufficient for foreground OAuth branches; background token exchange/reconnect remains Probe/integration scope |
| `kernel.tools.web.fetch_backends.__init__` | `77%` | `96%` | sufficient |
| `kernel.tools.web.fetch_backends.readability_be` | `0%` | `94%` | sufficient |
| `kernel.tools.web.search_backends.__init__` | `61%` | `97%` | sufficient |
| `kernel.tools.web.search_backends.duckduckgo` | `33%` | `96%` | sufficient |

剩余已归属缺口：

| 模块 | 当前覆盖率 | 归属 |
|---|---:|---|
| `kernel.gateways.discord.*` | `25%–40%` | concrete Discord SDK/event-loop behavior；需要 platform fixture 或 adapter-level integration，不阻塞 Phase 1 owned unit goals |
| `kernel.mcp.transport.http` / `sse` | `26%–28%` | streaming remote transport/OAuth header/session-expiry matrix；属于 MCP transport integration suite |
| `kernel.memory.background` / `selector` | `34%–63%` | background refresh + LLM selector paths；已有 store/tool/index 单测，剩余进入 memory hardening tranche |
| `kernel.schedule.scheduler` | `49%` | long-running scheduler loop/claim/reaper timing branches；已有 store/executor/delivery 单测，剩余需要 scheduler-focused tranche |
| `kernel.tools.web.fetch_backends.httpx_html` / `playwright_be` | `57%` / `54%` | live HTTP response decoding and optional browser execution；真实 backend coverage belongs to external integration matrix |

闭合缝清单：

- 本轮只新增/扩展单元测试和计划记录，没有改 production callable wiring。
- 未引入或修改 `_make_*` closure、`ctx.* = fn`、adapter callback wiring、Hub/Runtime callable seam。
- 因此 Phase 4.5 无新增 real-system probe requirement；既有 Phase 2 Probe 继续覆盖真实 Supervisor/ACP/Runtime closure seams。

结论：Phase 1 owned goals 完成。后续不得再把这些剩余行覆盖称为“Phase 1 未完成”；应按上表进入对应的 platform / MCP transport / memory / scheduler / external integration hardening 任务。

## Phase 1.1 — Bootstrap / 基础服务

优先级: P0

| 组件 | 当前测试 | 需要补齐 |
|---|---|---|
| `flags` | `tests/kernel/flags/test_manager.py` | 启动失败路径、坏 section 类型、默认值不可变性 |
| `config` | `tests/kernel/config/test_loader.py`, `test_manager.py` | signal/slot 变更通知、secret resolver 失败、跨 owner 写入拒绝 |
| `secrets` | `tests/kernel/secrets/test_secret_manager.py`, `test_secret_config_integration.py` | OAuth token 更新/过期、坏 SQLite/迁移失败、并发读写 |
| `prompts` | `tests/kernel/prompts/test_prompt_manager.py` | 用户 override 优先级、项目 override 优先级、缺失/空 prompt、坏编码 |
| `connection_auth` | `tests/kernel/connection_auth/*` | token 文件权限、local trust 拒绝路径、password config edge cases |

验收命令：

```bash
cd src/kernel
uv run pytest ../../tests/kernel/flags ../../tests/kernel/config ../../tests/kernel/secrets ../../tests/kernel/prompts ../../tests/kernel/connection_auth -q
```

完成标准：

- 启动期组件的失败路径必须抛明确异常。
- 所有 config/secret/prompt 返回结构必须断言关键字段。

## Phase 1.2 — Protocol / Transport / Session

优先级: P0

| 组件 | 当前测试 | 需要补齐 |
|---|---|---|
| `protocol` | `tests/kernel/protocol/*` | outbound request/response 错误细节、cancel race、bad schema alias、router backend error mapping |
| `routes` | `tests/kernel/routes/*` | WS auth fail、stack selection、disconnect cleanup、transport backpressure |
| `session` | `tests/kernel/session/*` | queue edge cases、resume 不 replay、close 清理 grants、`clientTurnId` incomplete/replayed update |
| `session.persistence` | `test_store.py`, `test_migrations.py` | turn index/event query 坏数据、migration rollback、archived/title-source edge cases |
| `session.client_stream` | 部分通过 protocol 覆盖 | replay event ordering、permission request replay exclusion、multi-sender broadcast |

验收命令：

```bash
cd src/kernel
uv run pytest ../../tests/kernel/protocol ../../tests/kernel/routes ../../tests/kernel/session -q
```

完成标准：

- 每个 session lifecycle method 都有 happy/error test。
- `session/request_permission` 的 sender request 形状有单元测试。
- `clientTurnId` active/queued/completed/incomplete 四条路径都有断言。

## Phase 1.3 — Agent Control Plane 组件

优先级: P0

| 组件 | 当前测试 | 需要补齐 |
|---|---|---|
| `agents` | `tests/kernel/agents/*` | capability 校验、invalid schema、caller identity 边界 |
| `agent_hub` | `tests/kernel/agent_hub/*` | route_not_found、runtime_not_registered、runtime error message preservation、permission tunnel frame |
| `agent_runtime` | `tests/kernel/agent_runtime/*` | `CollectingRuntimeSender.request` bridge、permission allow/reject response、runtime shutdown cleanup |
| `access_agent` | `tests/kernel/access_agent/test_access_agent_c.py` | router backend session lifecycle、readiness 状态组合、bad Hub endpoint |
| `supervisor` | `tests/kernel/supervisor/test_supervisor_c1.py` | child exit handling、runtime file cleanup、port allocation failure、token propagation |

验收命令：

```bash
cd src/kernel
uv run pytest ../../tests/kernel/agents ../../tests/kernel/agent_hub ../../tests/kernel/agent_runtime ../../tests/kernel/access_agent ../../tests/kernel/supervisor -q
```

完成标准：

- Hub/Runtime/Access 的错误不能只断言失败；必须断言错误类型和 message。
- Runtime permission tunnel 的 request/response frame shape 有单元测试。
- Supervisor 不 import FastAPI 的边界最好有 import audit 或结构测试。

## Phase 1.4 — LLM / Orchestrator / Tool 执行链

优先级: P0

| 组件 | 当前测试 | 需要补齐 |
|---|---|---|
| `llm_provider` | `tests/kernel/llm_provider/*` | provider stream malformed chunks、retry/failure mapping、format edge cases |
| `llm` | `tests/kernel/llm/*` | role fallback、profile removal edge、current_used corruption recovery |
| `orchestrator` | `tests/kernel/orchestrator/*` | permission result mapping、tool failure continuation、cancel cleanup、plan mode no-write guard |
| `tools` | `tests/kernel/tools/*` | 每个 builtin tool 的坏输入、权限 metadata、result event fields |
| `tool_authz` | `tests/kernel/tool_authz/*` | rule precedence、session grant expiry、LLM judge unavailable fallback |

验收命令：

```bash
cd src/kernel
uv run pytest ../../tests/kernel/llm_provider ../../tests/kernel/llm ../../tests/kernel/orchestrator ../../tests/kernel/tools ../../tests/kernel/tool_authz -q
```

完成标准：

- Tool result 不能只断言“不崩溃”；必须断言 tool name、status、content/error。
- Orchestrator cancel/permission/tool-error 路径都必须清理 in-flight state。

## Phase 1.5 — Extensibility: MCP / Skills / Hooks / Memory

优先级: P1

| 组件 | 当前测试 | 需要补齐 |
|---|---|---|
| `mcp` | `tests/kernel/mcp/*` | remote 406/5xx 降级、OAuth refresh failure、resource listing malformed response |
| `skills` | `tests/kernel/skills/*` | malformed frontmatter、conditional skill activation edge、snapshot stale state |
| `hooks` | `tests/kernel/hooks/*` | handler timeout、bad manifest、fire task cleanup、multiple hook order |
| `memory` | `tests/kernel/memory/*` | corrupted memory file、project/global precedence、background failure isolation |

验收命令：

```bash
cd src/kernel
uv run pytest ../../tests/kernel/mcp ../../tests/kernel/skills ../../tests/kernel/hooks ../../tests/kernel/memory -q
```

完成标准：

- 外部依赖失败必须被转换成明确错误或降级状态。
- Hook/MCP/Memory 后台任务不能泄漏未等待 task。

## Phase 1.6 — User Software / Platform / Background

优先级: P1

| 组件 | 当前测试 | 需要补齐 |
|---|---|---|
| `gateways` | `tests/kernel/gateways/test_gateway_adapter.py` | Discord adapter config errors、reply sink failure、router mode clientTurnId stability |
| `schedule` | `tests/kernel/schedule/*` | executor failure、delivery route unavailable、cron store corruption |
| `git` | `tests/kernel/git/*` | non-git workspace、worktree cleanup failure、tool registration disabled |
| `tasks` | `tests/kernel/tasks/*` | task output ordering、stop unknown task、registry cleanup |
| `commands` | `tests/kernel/commands/test_command_manager.py` | duplicate command metadata、catalog serialization |
| `plans.py` / `signal.py` / lifecycle | `tests/kernel/test_plans.py`, `test_signal.py`, `test_lifespan.py` | malformed plan path、signal handler cleanup、lifespan degraded subsystem |

验收命令：

```bash
cd src/kernel
uv run pytest ../../tests/kernel/gateways ../../tests/kernel/schedule ../../tests/kernel/git ../../tests/kernel/tasks ../../tests/kernel/commands ../../tests/kernel/test_plans.py ../../tests/kernel/test_signal.py ../../tests/kernel/test_lifespan.py -q
```

完成标准：

- Gateway/Schedule/Git 失败不能影响 Kernel 核心 startup。
- Background/registry 类组件必须有 cleanup/unload 测试。

## Phase 1.7 — 覆盖率收敛与门禁

优先级: P0, 最后执行

任务：

- 跑完整 Kernel 单测。
- 跑覆盖率报告。
- 跑 ruff/mypy。
- 对照覆盖率缺口，补最后一轮 public API 和 error path。
- 更新 `progress.md`，记录覆盖率命令和结果。

命令：

```bash
cd src/kernel
uv run pytest ../../tests/kernel -q
uv run pytest --cov=kernel --cov-report=term-missing ../../tests/kernel
uv run ruff check kernel ../../tests/kernel
uv run mypy kernel
```

验收：

- 所有 `tests/kernel` 通过。
- 覆盖率报告中没有无人认领的关键 public API 缺口。
- 新增测试不依赖真实外部网络或真实 LLM key。
- 若某组件需要真实系统验证，必须在 Phase 2 Probe 计划里登记，不能用单元测试报告替代。

## 任务执行模板

每补一个组件测试，在 completion report 使用这个格式：

```text
组件:
新增/修改测试:
覆盖的 public API:
Happy path:
Error path:
Boundary/concurrency path:
剩余缺口:
运行命令:
结果:
```

## 当前优先级建议

先补 P0，因为它们是后续 Probe/CLI 的基础：

1. `protocol` / `session` / `agent_hub` / `agent_runtime` / `access_agent`
2. `orchestrator` / `tools` / `tool_authz`
3. `supervisor`
4. Bootstrap 服务: `flags` / `config` / `secrets` / `prompts` / `connection_auth`
5. P1 扩展组件: `mcp` / `skills` / `hooks` / `memory` / `gateways` / `schedule` / `git`

## 非目标

Phase 1 不做以下事情：

- 不启动真实 Supervisor 做 E2E。
- 不用 Probe 替代单元测试。
- 不验证 CLI UI。
- 不调用真实外部 LLM 或真实网络服务。
- 不把 mock 测试包装成闭合缝 Probe。
