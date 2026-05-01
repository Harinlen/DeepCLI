# Claude Code Main — Query Loop 详版 Walkthrough

> 对应精简版：[claude-code-query-loop.md](./claude-code-query-loop.md)。本文
> 是**逐步源码级**展开，每一步都指向 Claude Code main 的文件和行号，
> 便于你跳过去直接读代码。
>
> 源码仓库：`/home/saki/Documents/projects/claude-code-main/`
> （2026-04 快照，~1,884 files / 512k+ LOC；路径见 [`.mustang-refs.yaml`](../../.mustang-refs.yaml)）。

---

## 1. 入口 / 顶层结构

### 1.1 `query()` 的六个 caller

`query()` 是所有调用者共用的核心生成器，目前共 **6 个调用点**：

| 调用点 | 文件:行 | 触发场景 |
|---|---|---|
| REPL（交互 TUI） | [`screens/REPL.tsx:2793`](../../../../projects/claude-code-main/src/screens/REPL.tsx#L2793) | 用户在 ink TUI 里按 Enter 提交 prompt |
| `QueryEngine.submitMessage()` | [`QueryEngine.ts:675`](../../../../projects/claude-code-main/src/QueryEngine.ts#L675) | 被 `ask()` 包，SDK / `--print` 非交互路径 |
| `AgentTool.runAgent` | [`tools/AgentTool/runAgent.ts:748`](../../../../projects/claude-code-main/src/tools/AgentTool/runAgent.ts#L748) | LLM 在主循环里调 Task / Agent 工具 → 递归起一个新的 queryLoop |
| `forkedAgent` | [`utils/forkedAgent.ts:545`](../../../../projects/claude-code-main/src/utils/forkedAgent.ts#L545) | 非工具路径的 agent fork（slash command / review 等） |
| `execAgentHook` | [`utils/hooks/execAgentHook.ts:167`](../../../../projects/claude-code-main/src/utils/hooks/execAgentHook.ts#L167) | Hook 脚本声明要跑一段 agent prompt |
| `LocalMainSessionTask` | [`tasks/LocalMainSessionTask.ts:383`](../../../../projects/claude-code-main/src/tasks/LocalMainSessionTask.ts#L383) | 后台/sidecar session task |

重要观察：**REPL 并不走 `QueryEngine.submitMessage`**，它直接 new 一套 `toolUseContext` 然后调 `query()`。`QueryEngine` 是 SDK/headless 独占的包装层，给 `ask()` 用。

### 1.2 REPL 路径（最主要的入口）

```
用户 stdin
  → ink <TextInput>
  → onSubmit()                [REPL.tsx]
  → onQuery()                 [REPL.tsx:2855]
  → onQueryImpl()             [REPL.tsx:2661]
      └─ 组装 systemPrompt / userContext / systemContext / toolUseContext
      └─ for await (const event of query({...})) { onQueryEvent(event); }
            query()            [query.ts:219]
             └─ queryLoop()    [query.ts:241]
                  while (true) { ... }   ← LLM ↔ tool 多轮
```

### 1.3 SDK / `--print` 路径

```
CLI --print / SDK session
  → ask({prompt, ...})         [QueryEngine.ts:1186]
      └─ const engine = new QueryEngine({...})
      └─ yield* engine.submitMessage(prompt, {uuid, isMeta})
            submitMessage()    [QueryEngine.ts:209]
             └─ 组装 wrappedCanUseTool、userContext、skills、memory、agent 定义等
             └─ for await (const message of query({...}))  [QueryEngine.ts:675]
                  queryLoop()  [query.ts:241]
                   while (true) { ... }
```

### 1.4 Sub-agent 路径（LLM 调 Task 工具时）

```
LLM 在主 queryLoop 里 yield tool_use(Task/Agent, input={prompt, ...})
  → runTools() 触发 AgentTool.run()
  → AgentTool.run() 调 runAgent()            [tools/AgentTool/runAgent.ts]
  → 构造独立 toolUseContext（新 agentId，隔离的 messages[]）
  → for await (const message of query({...}))  [runAgent.ts:748]
       ← 这里是递归！新一层 queryLoop 跑完后把 tool_result 回给外层
```

### 1.5 关键不变量

- **一次用户提交 = 一次 `query()` 调用**。`query()` 本身不循环"多次用户提交"——多轮对话是外层 REPL / ask() 每次按用户 Enter 再发一次 `query()`。
- **`queryLoop` 的 `while (true)` = LLM 与 tool 之间的来回**。LLM 给 tool_use → 跑工具 → tool_result 喂回 → 再调 LLM → … 直到 LLM 不再出 tool_use（step G 的 stop 分支）。
- **Sub-agent 是 query() 的递归调用**，不是新增分支。`AgentTool` 就是普通工具。

整个 `queryLoop` 是一个约 1500 行的 `async function*`，**不 throw、只 return**：
错误路径全部通过 `yield { type: 'error' }` + 带 `reason` 的 return 对象传出，
好处是上层 `for await` 的 `.return()` / `.throw()` 能可测试地驱动任何
分支。

---

## 2. 每轮 `while (true)` 内部 10 个阶段

> 行号以 [`query.ts`](../../../../projects/claude-code-main/src/query.ts) 为准。

### A. Setup

1. **State 解构** ([`query.ts:311`](../../../../projects/claude-code-main/src/query.ts#L311)) — 读 cross-iteration 可变状态：`messages`、`turnCount`、`autoCompactTracking`、`stopHookActive`、`pendingToolUseSummary`、`streamingToolExecutor`。
2. **Skill prefetch 启动** ([`query.ts:331`](../../../../projects/claude-code-main/src/query.ts#L331)) — 发射异步 skill 发现，和后面的 model stream **并行**跑。
3. **`stream_request_start` 事件** ([`query.ts:337`](../../../../projects/claude-code-main/src/query.ts#L337)) — 通知上层即将开始新 request。
4. **Query tracking chain** ([`query.ts:346`](../../../../projects/claude-code-main/src/query.ts#L346)) — 维护 `chainId` / `depth`，用来在多级 sub-agent 调用里追踪链路。

### B. Prepare —— 四层压缩/裁剪

5. **Tool-result budget** ([`query.ts:379`](../../../../projects/claude-code-main/src/query.ts#L379)) — `applyToolResultBudget()` 给每条 tool_result 截尺寸上限。
6. **Snip compaction** ([`query.ts:401`](../../../../projects/claude-code-main/src/query.ts#L401)) — `HISTORY_SNIP` feature 开时走 `snipCompactIfNeeded`，删除受保护尾之外的内部自动化内容。
7. **Microcompact** ([`query.ts:413`](../../../../projects/claude-code-main/src/query.ts#L413)) — 按 `tool_use_id` 合并被缓存的 tool-call 编辑，实现在 [`services/compact/microCompact.ts:253`](../../../../projects/claude-code-main/src/services/compact/microCompact.ts#L253)。
8. **Context collapse** ([`query.ts:440`](../../../../projects/claude-code-main/src/query.ts#L440)) — feature-gated，commit-log 式持久化摘要。
9. **Autocompact** ([`query.ts:453`](../../../../projects/claude-code-main/src/query.ts#L453)) — 超过阈值时用 LLM 生成摘要。实现在 [`services/compact/sessionMemoryCompact.ts`](../../../../projects/claude-code-main/src/services/compact/sessionMemoryCompact.ts)。注意：**压缩完会重置 `turnCounter = 0`**（[`query.ts:524`](../../../../projects/claude-code-main/src/query.ts#L524)）。

### C. System prompt 组装

10. **拼 `fullSystemPrompt`** ([`query.ts:449`](../../../../projects/claude-code-main/src/query.ts#L449))：
    ```ts
    fullSystemPrompt = asSystemPrompt(
      appendSystemContext(systemPrompt, systemContext)
    )
    ```
    Memory / skills 不是塞进 system prompt 而是走 `userContext` / `systemContext`，在 `callModel()` 的 messages 前面 prepend。

### D. 调 LLM（streaming + fallback）

11. **API stream loop** ([`query.ts:654–863`](../../../../projects/claude-code-main/src/query.ts#L654)) — 调 `deps.callModel({messages, systemPrompt, tools, signal, options})`，`options` 里带：
    - `thinkingConfig`（`adaptive` / `disabled` / `enabled`）
    - `model`（fallback 时会被替换）
    - `maxOutputTokensOverride`（默认 8k，被升级时改 64k）
    - `fastMode`、`queryTracking`、`taskBudget`

    流里的每条 message：
    - 如果是 recoverable error（`prompt_too_long` / `max_output_tokens` / media size）→ **withhold**，不 yield
    - 否则 yield 给 caller，推进 `assistantMessages[]`
    - `tool_use` block 同时送进 `streamingToolExecutor`（能边 stream 边跑工具）或 `toolUseBlocks[]`
12. **Fallback on streaming failure** ([`query.ts:894`](../../../../projects/claude-code-main/src/query.ts#L894)) — 触发 fallback 时：orphaned assistant 消息加 tombstone、`assistantMessages/toolUseBlocks` 清空、`streamingToolExecutor` 重建（[`query.ts:913`](../../../../projects/claude-code-main/src/query.ts#L913)）。

### E. Post-sampling hook

13. **`executePostSamplingHooks()`** ([`query.ts:1001`](../../../../projects/claude-code-main/src/query.ts#L1001)) — LLM stream 结束、stop 判断**之前**触发。`assistantMessages.length === 0` 时跳过。

### F. Abort 检查（stream 结束时）

14. **`signal.aborted` 第一道检查** ([`query.ts:1015`](../../../../projects/claude-code-main/src/query.ts#L1015))：
    - 消费 `streamingToolExecutor.getRemainingResults()`
    - 发 interruption 消息（除非 reason 是 `'interrupt'`，那是用户主动）
    - chicago MCP 的 computer-use 清理
    - `return { reason: 'aborted_streaming' }`

### G. 无 tool_use 分支 —— 进入 stop 流程

15. **`!needsFollowUp` 大分支** ([`query.ts:1062–1357`](../../../../projects/claude-code-main/src/query.ts#L1062))，优先级：
    - **`prompt_too_long` 恢复**：先尝试 collapse drain，再走 reactive compact（[`query.ts:1085–1183`](../../../../projects/claude-code-main/src/query.ts#L1085)）
    - **Media-size error 恢复**：reactive compact + strip media（[`query.ts:1082`](../../../../projects/claude-code-main/src/query.ts#L1082)）
    - **`max_output_tokens` 恢复**：先把 cap 从 8k → 64k；仍不够则 multi-turn retry，上限 `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3` 次（[`query.ts:164`](../../../../projects/claude-code-main/src/query.ts#L164) + [`query.ts:1223`](../../../../projects/claude-code-main/src/query.ts#L1223)）
    - **`handleStopHooks()`** ([`query.ts:1267`](../../../../projects/claude-code-main/src/query.ts#L1267) → [`query/stopHooks.ts:65`](../../../../projects/claude-code-main/src/query/stopHooks.ts#L65))：是一个子 generator，依次跑 `executeStopHooks` / `executeTaskCompletedHooks` / `executeTeammateIdleHooks` / `executeAutoDream` / `executePromptSuggestion`，返回 `{ preventContinuation, blockingErrors }`
    - **Token budget 检查**（如启用）→ 决定继续还是停（[`query.ts:1308–1355`](../../../../projects/claude-code-main/src/query.ts#L1308)）
    - **`return { reason: 'completed' }`** ([`query.ts:1357`](../../../../projects/claude-code-main/src/query.ts#L1357))

    > **API error 分流**：rate limit / auth / prompt_too_long 都走
    > `executeStopFailureHooks()` 而**不是**正常 stop hooks，避免
    > death-spiral 重试（[`query.ts:1174`](../../../../projects/claude-code-main/src/query.ts#L1174)）。

### H. Tool 执行

16. **Streaming vs serial** ([`query.ts:1366–1408`](../../../../projects/claude-code-main/src/query.ts#L1366))：
    - `streamingToolExecutor` 活着 → 直接消费 `getRemainingResults()`（工具在 stream 期就开始跑了）
    - 否则 → `runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext)` 串行块
    - **权限检查**：每个 tool 调用先过 `canUseTool()`（由 `QueryEngine.submitMessage` 注入，承担交互审批）
    - **并发上限**：`CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` 环境变量，默认 **10**
17. **Abort 第二道检查**（tools 执行中）([`query.ts:1485`](../../../../projects/claude-code-main/src/query.ts#L1485))：消费剩余结果、清理、检查 maxTurns、`return { reason: 'aborted_tools' }`
18. **Hook 阻断检查** ([`query.ts:1519`](../../../../projects/claude-code-main/src/query.ts#L1519))：若前置 hook 置 `shouldPreventContinuation` → `return { reason: 'hook_stopped' }`

### I. 后处理

19. **Tool-use 摘要**（异步）([`query.ts:1411`](../../../../projects/claude-code-main/src/query.ts#L1411)) — 后台开一个 Haiku 调用总结本轮 tool calls，下一轮 LLM 前才 await，不阻塞当前 yield。
20. **`turnCounter` 递增** ([`query.ts:1523`](../../../../projects/claude-code-main/src/query.ts#L1523)) — 仅当已 compacted 时维护。
21. **Attachment 消息** ([`query.ts:1580`](../../../../projects/claude-code-main/src/query.ts#L1580)) — 把 queued commands（notifications）、memory attachments 打包成 `AttachmentMessage` yield 出去。
22. **Memory prefetch consume** — 等 memory 加载完（如已 settled）。

### J. 循环判定

23. **`maxTurns` 检查** ([`query.ts:1705`](../../../../projects/claude-code-main/src/query.ts#L1705)) — `nextTurnCount > maxTurns` → yield 'max_turns' attachment + return。
24. **State 推进 + `continue`** ([`query.ts:1715`](../../../../projects/claude-code-main/src/query.ts#L1715)) — 更新 `messages` / `toolResults` / `summaries`，`turnCount++`，回到 step A。

---

## 3. 关键常量 / 上限

| 常量 | 位置 | 值 | 作用 |
|---|---|---|---|
| `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT` | [`query.ts:164`](../../../../projects/claude-code-main/src/query.ts#L164) | `3` | 触发 max-output-tokens 后最多 multi-turn 重试次数 |
| `turnCount` 初值 | [`query.ts:276`](../../../../projects/claude-code-main/src/query.ts#L276) | `1` | 每轮 `continue` 前 +1 |
| `maxTurns` | `submitMessage` 调用方传入 | **caller 决定** | 没有全局硬上限 |
| `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` | env | `10` | 并行跑的 tool 上限 |
| `ESCALATED_MAX_TOKENS` | `utils/context.js` | 64k | max_output_tokens 8k 被打爆后升到 64k |

---

## 4. Hook 触发点

| Hook | 何时触发 | 位置 |
|---|---|---|
| `executePostSamplingHooks()` | LLM stream 结束、进 stop 判断前 | [`query.ts:1001`](../../../../projects/claude-code-main/src/query.ts#L1001) |
| `handleStopHooks()`（6 个子 hook）| 确认无 tool_use 后 | [`query.ts:1267`](../../../../projects/claude-code-main/src/query.ts#L1267) |
| `executeStopFailureHooks()` | API error 路径（替代正常 stop hooks） | [`query.ts:1174/1181/1263`](../../../../projects/claude-code-main/src/query.ts#L1174) |
| Pre-tool-use / `canUseTool` | 每个 tool 执行前 | `runTools()` + `toolOrchestration` |

`user_prompt_submit` 不在 queryLoop 里——它是 `submitMessage` 更外层的
事情（prompt 进 `messages[]` 之前）。

---

## 5. Sub-agent / Task 分发

- Sub-agent 不是 queryLoop 内置分支，而是**通过 `AgentTool` 作为一个普通工具调**——`AgentTool.run()` 内部会再调 `runForkedAgent()`，后者又跑一个新的 `queryLoop()`。
- 每个 sub-agent 有独立的 `toolUseContext.agentId`；queryLoop 内部靠 `agentId` 过滤 queued commands（主线程 `agentId === undefined`，只 drain 主 prompts；sub-agent 只 drain addressed-to-me 的 task-notifications）([`query.ts:1570`](../../../../projects/claude-code-main/src/query.ts#L1570))。
- **Sleep tool** 特例：Sleep 跑后 queued commands 保持 later 模式，turn 末尾才 drain。

---

## 6. State 物理位置

| 状态 | 类型 | 位置 | 生命周期 |
|---|---|---|---|
| `messages` | array | `state.messages`（[`query.ts:268`](../../../../projects/claude-code-main/src/query.ts#L268)） | 每个 iteration **不可变快照**；continue 时整体替换 |
| `toolUseContext` | object | `state.toolUseContext` | 携带 tools、permissions、`getAppState`、`abortController` |
| `autoCompactTracking` | `{compacted, turnId, turnCounter}` | `state.autoCompactTracking` | 每次 compact 重置 |
| `stopHookActive` | bool | `state.stopHookActive` | 防止 blocking hook 被重跑 |
| `turnCount` | number | `state.turnCount` | 每轮 +1 |
| `pendingToolUseSummary` | Promise | `state.pendingToolUseSummary` | 跨 turn，下轮 await |
| **Plan mode / model / fastMode** | AppState (Zustand) | `toolUseContext.getAppState()` | **实时读**，每轮重新 get——设计上等价于 `AppState` |
| Config snapshot | object | iteration 开头 const | 不可变：streaming_tool_execution、emit_summaries、isAnt、fastMode |

> 关键模式：**长期可变状态靠 Zustand (`getAppState()`)，每 iteration 快照状态靠 closure 里的 const `config`**。Plan mode 切换不是事件——loop 下一次读 AppState 就生效。

---

## 7. 取消路径

- **两个检查点**：stream 结束时（[`query.ts:1015`](../../../../projects/claude-code-main/src/query.ts#L1015)）和 tools 执行完时（[`query.ts:1485`](../../../../projects/claude-code-main/src/query.ts#L1485)）。
- **Signal reasons**：`'interrupt'`（用户 /stop）、`'model_error'`（API 故障）等。
- **语义保证**：部分 tool_result 也会 yield 出去，**不会**出现没 tool_result 的 orphan `tool_use`（对 LLM API 格式的 hard constraint）。
- 返回 `{ reason: 'aborted_streaming' | 'aborted_tools' }`——上层用 reason 区分清理路径。

---

## 8. 压缩恢复分层

Claude Code 在 mustang 的"proactive + reactive"两层之上还多了两层：

| 层 | 触发 | 作用 |
|---|---|---|
| **Tool-result budget** | 每 iteration 开头 | 单条 tool_result 截尺寸，非压缩 |
| **Snip compaction** | 每 iteration 开头，feature-gated | 删自动化内容、保尾 |
| **Microcompact** | 每 iteration 开头 | 按 `tool_use_id` 合并已缓存的 tool-call 编辑 |
| **Context collapse** | 每 iteration 开头，feature-gated | Commit-log 持久化摘要 |
| **Autocompact**（proactive） | 超阈值 | LLM 生成摘要（Claude Code vs mustang 唯一共通的一层） |
| **Reactive compact** | API 返 `prompt_too_long` / media size | 先试 collapse drain、再摘要 |
| **Max-output-tokens recovery** | API 返 `max_output_tokens` | 8k → 64k → multi-turn retry × 3 |

---

## 9. 设计特征小结

1. **生成器做 orchestrator**：`async function*` + 只 return 不 throw，可测性强。
2. **状态两级**：turn 快照（const）+ 会话实时（Zustand）。`config` 改变靠 `AppState` 异步生效，不走事件。
3. **压缩路径 3+2 层**：3 层 iteration-开头的廉价裁剪 + autocompact（proactive）+ reactive compact。
4. **Hook 多节点而非单 stop**：`post_sampling` → `stop`（六个子 hook）→ `stop_failure` 分流。
5. **无全局 max_turns**：每个调用方（REPL / SDK / AgentTool）自己决定 `maxTurns`。
6. **Tool 可以边 stream 边跑**：`streamingToolExecutor` 在 LLM 流仍在喷 token 时启动 tool 执行，tool 结果只在 turn 末尾汇总喂回。
7. **Sub-agent 就是普通工具**：没有专门的 sub-agent 分支，`AgentTool` 内部递归调 `queryLoop`。

---

## 延伸阅读

- 精简版（流程总览）：[claude-code-query-loop.md](./claude-code-query-loop.md)
- 差异对比表：[claude-code-comparison.md § 3](./claude-code-comparison.md#3-engine--orchestrator)
- 覆盖度评估：[claude-code-coverage.md](./claude-code-coverage.md)
