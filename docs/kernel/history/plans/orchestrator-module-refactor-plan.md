# Orchestrator Module Refactor Plan

## Background

本计划细化 `docs/plans/kernel-file-length-refactor-plan.md` 中的
**Batch C — orchestrator module**。目标是把
`src/kernel/kernel/orchestrator/` 从少数大文件拆为按功能分层的模块，同时
降低重复逻辑、收紧状态边界，并保持所有既有功能和 public import path 兼容。

**Implementation status**: implemented.  The final layout keeps all
Orchestrator Python files below 300 lines and preserves the compatibility
imports listed below.  The implementation uses `tools_exec/`, `compact/`,
`history/`, `events/`, and `loop/` packages, plus small prompt/reminder/
notification/hook/sub-agent helper modules.

当前 Orchestrator 约 **4,422** 行，超限文件包括：

| Lines | File |
|---:|---|
| 1285 | `orchestrator/orchestrator.py` |
| 976 | `orchestrator/tool_executor.py` |
| 490 | `orchestrator/compactor.py` |
| 353 | `orchestrator/history.py` |
| 341 | `orchestrator/types.py` |
| 340 | `orchestrator/__init__.py` |
| 329 | `orchestrator/events.py` |

## Non-Goals

- 不改 Session ↔ Orchestrator 协议语义。
- 不减少 tool execution、plan mode、compaction、sub-agent、hook、task
  notification、MCP instructions、language section 等现有能力。
- 不要求一次性重写 query loop；按兼容 shim 迁移，逐批验证。
- 不把跨子系统逻辑抽成全局 util；共享代码只放在 Orchestrator 模块内部。

## Compatibility Contract

这些 import path 必须继续可用：

- `from kernel.orchestrator import Orchestrator, OrchestratorConfig, OrchestratorDeps`
- `from kernel.orchestrator.orchestrator import StandardOrchestrator`
- `from kernel.orchestrator.tool_executor import ToolExecutor, partition_tool_calls`
- `from kernel.orchestrator.compactor import Compactor, create_skill_attachment`
- `from kernel.orchestrator.history import ConversationHistory`
- `from kernel.orchestrator.events import ...`
- `from kernel.orchestrator.types import ...`

做法：先把实现移到新文件，再在旧文件保留薄 re-export。等全仓 import
收敛后，旧文件仍可作为稳定兼容层保留。

## Functional Classification

拆分后的代码先按功能域分类，而不是按行数切块。

### 1. Public API And Schemas

目标：`__init__.py` 只导出 package API；schema 按面向调用者的概念分组。

```
orchestrator/
  __init__.py                  # thin public exports only
  api.py                       # Orchestrator Protocol
  config.py                    # OrchestratorConfig / Patch
  deps.py                      # OrchestratorDeps / LLMProvider Protocol
  stop.py                      # StopReason + stop/budget helpers
  permissions.py               # PermissionRequest/Response/Option/Callback
  tool_kinds.py                # ToolKind
  events/
    __init__.py                # public event exports + OrchestratorEvent
    streaming.py               # TextDelta / ThoughtDelta
    tools.py                   # ToolCall* events
    session.py                 # PlanUpdate / ModeChanged / ConfigOptionChanged / ...
    agents.py                  # SubAgentStart / SubAgentEnd
    housekeeping.py            # CompactionEvent / QueryError / Cancelled / History*
```

Simplification:

- Move `OrchestratorConfig` out of `__init__.py` so implementation code no longer imports
  from its own package root.
- Keep `types.py` as compatibility export, backed by smaller schema modules.
- Keep `events.py` as compatibility export, backed by `events/`.

### 2. Runtime Facade

目标：`StandardOrchestrator` 只保留 session-scoped state、public methods、
component assembly，不承载主循环细节。

```
orchestrator/
  standard.py                  # StandardOrchestrator actual implementation
  orchestrator.py              # compatibility re-export
  state.py                     # OrchestratorRuntimeState / TurnUsage
  constants.py                 # retry limits, context defaults, plan-mode counters
  config_runtime.py            # set_config/set_mode validation and compactor refresh
```

Simplification:

- 用 `OrchestratorRuntimeState` 聚合 `_mode`、plan-mode counters、
  `_cwd`、usage、stop reason，减少散落字段。
- `set_config()` 只更新 state，并通过小工厂重建依赖 model 的组件
  `Compactor`；避免构造逻辑散在 facade 内。

### 3. Query Loop And Turn Engine

目标：把 6-step query loop 拆为可读的单轮状态机。

```
orchestrator/
  loop/
    __init__.py
    engine.py                  # QueryLoopEngine: outer async generator
    setup.py                   # STEP 0 prompt/reminder/hook/user append
    prepare.py                 # STEP 1 compaction pipeline
    prompt_sections.py         # STEP 2 dynamic prompt augmentation
    stream.py                  # STEP 3 provider streaming
    commit.py                  # STEP 4 assistant commit + orphan-safe branch
    stop.py                    # STEP 5 stop handling, budget, max_tokens recovery
    tools.py                   # STEP 6 tool execution + history append
    cancellation.py            # CancelledError orphan tool_result repair
    turn_state.py              # TurnState / StreamAccumulator / RetryState
```

Simplification:

- 引入 `TurnState`：
  `turn_index`、`reactive_retries`、`max_tokens_override`、
  `max_tokens_retries`、`last_stop_reason`、`token_budget`。
- 引入 `StreamAccumulator`：
  收集 text/thought/tool/usage，并提供 `has_sampled_output`、`assistant_text`。
  这样 streaming 分支不再用多个局部 list 到处传。
- 把 `PromptTooLongError`、`MediaSizeError`、`StreamError`、`ProviderError`
  收束到 `ProviderRecoveryPolicy` 或 `stream.py` 内部，query loop 只处理
  “retry / emit error / continue” 三种结果。
- 把 max-output-tokens withhold 逻辑放入 `loop/stop.py`，避免 STEP 4/5
  互相穿透。

### 4. Dynamic Prompt Augmentation

目标：把 plan mode、session guidance、deferred tool listing 等动态 prompt
注入从 `StandardOrchestrator` 中拆出，并做成顺序明确的 pipeline。

```
orchestrator/
  prompt/
    dynamic.py                 # DynamicPromptPipeline
    tool_snapshot.py           # snapshot_for_session + deferred listing
    session_guidance.py        # CC getSessionSpecificGuidanceSection port
    plan_mode.py               # PlanModePromptInjector
    system_dump.py             # MUSTANG_DUMP_SYSTEM_PROMPT support
```

Simplification:

- `DynamicPromptPipeline.apply(system_prompt, turn_context)` 固定注入顺序：
  deferred tool listing → session guidance → plan-mode reminders。
- `PlanModePromptInjector` 拥有 plan-mode counters，或读取/写入
  `OrchestratorRuntimeState.plan` 子状态；不再让主循环直接管理计数。
- `tool_snapshot.py` 统一产出 `ToolTurnSnapshot(tool_schemas, visible_names)`，
  后续代码不再重复处理 schemas/deferred_names。

### 5. Hooks, Reminders, And Notifications

目标：消除 Orchestrator 与 ToolExecutor 中重复的 hook fire/drain 逻辑。

```
orchestrator/
  hooks.py                     # HookBridge: fire + AmbientContext + queue_reminders
  reminders.py                 # drain/format prompt reminders
  notifications.py             # task + monitor XML formatting/draining
```

Simplification:

- `HookBridge.fire_query(...)` 和 `HookBridge.fire_tool(...)` 共享
  AmbientContext 构造、`deps.hooks is None` 降级、`ctx.messages`
  drain 逻辑。
- `_format_reminders()`、`_format_task_notification()`、
  `_format_monitor_notification()` 移出主循环；测试可以直接覆盖格式化模块。
- `TaskNotificationDrainer.drain(agent_id)` 返回 reminder strings；
  STEP 6 只负责把 strings 交给 `deps.queue_reminders`。

### 6. Tool Execution Package

目标：保留 `ToolExecutor` 门面，但把调度、单工具 pipeline、权限、上下文、
结果映射分开。

```
orchestrator/
  tool_executor.py             # compatibility re-export
  tools_exec/
    __init__.py
    executor.py                # ToolExecutor facade
    partition.py               # partition_tool_calls
    scheduler.py               # serial/concurrent batch execution + queue merge
    pipeline.py                # one tool's 7-step flow
    authorization.py           # authorize + on_permission round-trip
    context.py                 # ToolContext / AuthorizeContext builders
    hooks.py                   # tool hook calls via HookBridge
    result_mapping.py          # ToolCall* events + ToolResultContent mapping
    budgets.py                 # _apply_result_budget
    permissions.py             # permission options projection
    file_touch.py              # SkillManager on_file_touched bridge
```

Simplification:

- `ToolExecutor` 只维护 queue/finalized/discarded/in-flight 状态，调用
  `ToolBatchScheduler`。
- `ToolPipeline.run_one()` 只表达 7-step flow；权限 round-trip、
  hook firing、context construction 不在同一文件展开。
- `_build_tool_context()` 和 `_build_authorize_context()` 进入 `context.py`，
  后续新增工具上下文字段时有单点修改。
- `_coerce_content()`、`_apply_result_budget()`、unknown-tool error
  映射进入 `result_mapping.py`/`budgets.py`，避免测试继续依赖大文件私有函数。

### 7. History Package

目标：ConversationHistory 保持无 I/O，但拆出消息构造、token estimate、
tool-pair 查询。

```
orchestrator/
  history.py                   # compatibility re-export
  history/
    __init__.py
    conversation.py            # ConversationHistory
    builders.py                # append_user/assistant/tool_result helpers
    thinking.py                # ThoughtAccumulator + ThinkingContent assembly
    tokens.py                  # estimate/update helpers
    pairs.py                   # pending_tool_use_ids + tool kind lookup helpers
```

Simplification:

- `ConversationHistory` 保持主类，委托纯函数处理 thinking assembly、
  token estimation、pending tool-use scanning。
- `MessageBuilder` 风格的纯函数让 cancellation repair 和 tests 可复用，
  不需要在多个地方手写 `ToolResultContent(is_error=True)` 结构。

### 8. Compaction Package

目标：Compactor 保持主入口，内部按 budget、message selection、LLM summary、
skill attachment 分组。

```
orchestrator/
  compactor.py                 # compatibility re-export
  compact/
    __init__.py
    compactor.py               # Compactor facade
    media.py                   # strip_media
    snip.py                    # read-only tool result snipping
    microcompact.py            # read-only assistant/result pair removal
    summarize.py               # LLM summary call + prompt fallback
    render.py                  # _render_messages and content char count
    skill_attachment.py        # create_skill_attachment
    classifiers.py             # read-only assistant/tool-result predicates
```

Simplification:

- Message walking helpers shared by `strip_media`, `snip`, and render code。
- `CompactionResult` dataclass 可统一返回 `changed/tokens_before/tokens_after`
  给 STEP 1，避免主循环手动计算 before/after。
- LLM summary prompt loading封装到 `SummarizerConfig`，减少 `Compactor.__init__`
  中的 fallback 文本噪音。

### 9. Sub-Agent Support

目标：把 `spawn_subagent` 闭包和 orphan notification drain 从主 orchestrator
拆出。

```
orchestrator/
  agents/
    __init__.py
    spawner.py                 # SubAgentSpawner
    transcript.py              # transcript capture / resume shape helpers
```

Simplification:

- `SubAgentSpawner` 接收 parent runtime state + deps + orchestrator factory，
  避免闭包直接访问大量 parent 私有字段。
- 子 agent 默认 permission callback、default max turns、SubAgentStart/End
  bracketing 在一处维护。

## Proposed Target Layout

最终目标是所有 Python 文件低于 300 行，且每个文件只有一个主要职责。

```
src/kernel/kernel/orchestrator/
  __init__.py
  api.py
  config.py
  config_section.py
  constants.py
  deps.py
  orchestrator.py
  standard.py
  state.py
  stop.py
  permissions.py
  tool_kinds.py
  prompt_builder.py
  hooks.py
  reminders.py
  notifications.py
  loop/
  prompt/
  tools_exec/
  history/
  compact/
  events/
  agents/
```

Compatibility files retained:

- `orchestrator.py`
- `tool_executor.py`
- `history.py`
- `compactor.py`
- `events.py`
- `types.py`

这些文件只做 re-export，必要时附一行 module docstring。

## Incremental Execution Plan

### Batch C1 — Public API Split

- 新增 `api.py`、`config.py`、`deps.py`、`permissions.py`、`tool_kinds.py`、
  `stop.py`。
- `__init__.py` 改为 thin exports。
- `types.py` 保持兼容 re-export。

验证：

- `tests/kernel/orchestrator/test_orchestrator.py`
- `tests/kernel/protocol/test_event_mapper.py`
- `tests/kernel/session/test_session_manager.py`
- import smoke: package root、`types.py`、Session imports。

### Batch C2 — Events Split

- 建立 `events/` package，按 streaming/tools/session/agents/housekeeping 分类。
- `events.py` 保持兼容 re-export。
- 不改变任何 dataclass 字段名或 union 成员。

验证：

- `tests/kernel/protocol/test_event_mapper.py`
- `tests/kernel/orchestrator/test_tool_executor_*`
- ACP event mapper closure seam probe。

### Batch C3 — ToolExecutor Package

- 先移动纯函数：partition、permission option projection、content coercion、
  result budget。
- 再拆 context builders、authorization round-trip、single-tool pipeline。
- 最后拆 scheduler/concurrent queue merge，保留 `ToolExecutor` public surface。

验证：

- `tests/kernel/orchestrator/test_tool_executor_parallel.py`
- `tests/kernel/orchestrator/test_tool_executor_hooks.py`
- `tests/kernel/orchestrator/test_context_modifier.py`
- `tests/kernel/orchestrator/test_permission_modes_e2e.py`
- `tests/kernel/session/test_permission_options.py`
- Closure seam: real ToolManager + ToolAuthorizer deny/ask/allow through
  `session/request_permission`。

### Batch C4 — History And Compaction Packages

- 拆 `ConversationHistory` helpers，保持 `ConversationHistory` class API。
- 拆 Compactor 的 media/snip/microcompact/summarize/render/skill attachment。
- 保持 `Compactor.snip()`、`microcompact()`、`compact()`、`strip_media()` 调用兼容。

验证：

- `tests/kernel/orchestrator/test_history.py`
- `tests/kernel/orchestrator/test_compactor.py`
- `tests/kernel/skills/test_compaction.py`
- Closure seam: history compaction after real provider stream + skill attachment
  preservation。

### Batch C5 — Query Loop Extraction

- 新增 `loop/turn_state.py`、`loop/stream.py`、`loop/prepare.py`、`loop/stop.py`。
- `StandardOrchestrator._run_query()` 先委托给 `QueryLoopEngine.run()`。
- 保持 cancellation、HistoryAppend/HistorySnapshot、token usage、stop_reason
  行为完全一致。

验证：

- `tests/kernel/orchestrator/test_orchestrator.py`
- `tests/kernel/orchestrator/test_reminder_injection.py`
- `tests/kernel/orchestrator/test_task_notifications.py`
- prompt-too-long、media-size、stream-error、max-output-tokens recovery cases。

### Batch C6 — Dynamic Prompt And Sub-Agent Extraction

- 拆 `prompt/session_guidance.py`、`prompt/plan_mode.py`、`prompt/tool_snapshot.py`、
  `prompt/system_dump.py`。
- 拆 `agents/spawner.py`。
- `StandardOrchestrator` 只装配 pipeline/spawner。

验证：

- `tests/kernel/orchestrator/test_session_guidance_alignment.py`
- `tests/kernel/orchestrator/test_language_alignment.py`
- `tests/kernel/orchestrator/test_mcp_instructions_alignment.py`
- `tests/kernel/tools/builtin/test_agent.py`
- probe: language、MCP instructions、session guidance。

### Batch C7 — Final Cleanup

- 全仓替换内部 imports 到新模块；保留兼容文件。
- 删除重复 helper、死代码、循环 import workaround。
- 重新扫描 line count，确保 orchestrator 模块内 0 个文件超过 300 行。

验证：

- `uv run ruff format src/kernel/kernel/orchestrator tests/kernel/orchestrator`
- `uv run ruff check src/kernel/kernel/orchestrator tests/kernel/orchestrator`
- `uv run pytest tests/kernel/orchestrator -q`
- 相关 session/protocol/tool tests。
- 至少一条 real subsystem closure-seam probe：SessionManager → StandardOrchestrator
  → provider.stream → ToolExecutor → ToolAuthorizer → Session permission callback。

## Simplification Rules During Implementation

1. **Prefer dataclass state over long local-variable trails.**
   `TurnState`、`StreamAccumulator`、`RetryState` 应让主循环读起来像流程，而不是
   临时变量清单。

2. **One bridge for hooks.**
   Query hooks 和 tool hooks 共享 `HookBridge` 的降级、AmbientContext、reminder
   drain 逻辑，避免两个文件维护相同行为。

3. **One bridge for reminders.**
   Hook reminders、SendMessage parent messages、task notifications、monitor lines
   最终都变成 reminder string；format 与 drain 分开，STEP 0/6 不拼 XML 细节。

4. **Keep orchestration code declarative.**
   `QueryLoopEngine` 应表达 “prepare → build prompt → stream → commit → stop/tools”，
   细节放进策略类或小模块。

5. **Do not invent generic abstractions.**
   抽象只服务 Orchestrator 内现有重复点；不要引入跨 kernel 的万能 pipeline/util。

6. **Compatibility shims are allowed, hidden rewrites are not.**
   每批迁移后旧 import path 必须继续通过 tests；行为差异必须先写测试再改。

## Risks And Guardrails

- **Circular imports**：`events` 需要 `ToolKind/StopReason`，`tools` 子系统也引用
  `ToolKind`。先拆 `tool_kinds.py` 和 `stop.py`，再拆 events。
- **Async generator cancellation**：`CancelledError` handler 和 orphan tool_result
  repair 必须保持在 loop 层统一处理，不分散到 stream/tools 层。
- **Streaming tools**：当前 eager dispatch 仍是 facade shape，拆 scheduler 时不能改变
  `streaming_tools=True/False` 的 observable event order。
- **Plan mode counters**：迁移到 injector/state 时要保持 full/sparse reminder
  throttling和 exit/reentry one-shot 行为。
- **Prompt cache sections**：dynamic prompt 注入顺序和 `cache` 标记不能漂移。
- **Private helper tests**：现有测试引用 `_apply_result_budget`、
  `_format_task_notification`、`_MAX_*` 常量。迁移时在旧模块 re-export，或同步更新测试。

## Acceptance Criteria

- `src/kernel/kernel/orchestrator/**/*.py` 中 0 个文件超过 300 行。
- `__init__.py`、`types.py`、`events.py`、`orchestrator.py`、`tool_executor.py`、
  `history.py`、`compactor.py` 都是薄兼容层。
- `StandardOrchestrator` 文件只包含 facade/state wiring，不包含完整 6-step loop。
- `ToolExecutor` 文件只包含 public executor facade，不包含完整 7-step pipeline。
- 所有现有 Orchestrator、Session、Protocol、ToolAuthZ 相关测试通过。
- 至少记录一条 real subsystem closure-seam probe 输出到实施报告。

## Line-Count Re-scan Command

```bash
find src/kernel/kernel/orchestrator -name '*.py' -type f -print0 \
  | xargs -0 wc -l \
  | sort -nr
```
