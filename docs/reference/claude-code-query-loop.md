# Claude Code Main — Query Loop 工作流

> Anthropic 官方 Claude Code CLI main 分支的 orchestrator 每轮做什么。
> 源码位置：[`/home/saki/Documents/projects/claude-code-main/src/`](../../../../projects/claude-code-main/src/)
> （见 [`.mustang-refs.yaml`](../../.mustang-refs.yaml)）。

## 入口

`query()` 在 6 个地方被调用，全部走同一个 `queryLoop`：

```
REPL (交互)       REPL.tsx:2793          用户按 Enter → onSubmit → onQuery → onQueryImpl → query()
SDK / --print     QueryEngine.ts:675     ask() → new QueryEngine → submitMessage() → query()
AgentTool         runAgent.ts:748        LLM 调 Task/Agent 工具 → 递归进新一轮 query()
Forked agent     forkedAgent.ts:545     非工具路径的 agent fork（命令执行/review 等）
Hook agent       execAgentHook.ts:167   hook 脚本声明自己要跑一段 agent prompt
Main session     LocalMainSessionTask   后台/sidecar session task
```

调用链（以 REPL 为例）：

```
用户 stdin → ink <TextInput> → onSubmit()          [UI 层，组装 user message]
           → onQuery()  →  onQueryImpl()            [REPL.tsx]
           → 构建 systemPrompt / userContext / systemContext / toolUseContext
           → for await (const event of query({...}))  ← 单次用户提交 = 单次 query()
                query()                               [query.ts:219]
                 └─ queryLoop()                       [query.ts:241]
                      while (true) { ... }            ← 这里才是 LLM ↔ tool 多轮循环
```

**一次用户提交 = 一次 `query()` 调用；`while(true)` 迭代 = LLM 与 tool 之间的来回。** `queryLoop` 是 `async function*`，**只 return 不 throw**，错误通过带 `reason` 的返回值传出。

## 每轮 6 步

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. PREPARE     裁剪 / 压缩 4 层（每轮都跑，便宜→贵）             │
│    ├─ tool-result budget       单条 tool_result 截尺寸            │
│    ├─ snip                     删自动化内容、保尾                 │
│    ├─ microcompact             按 tool_use_id 合并已缓存编辑      │
│    ├─ context collapse         commit-log 式持久化摘要            │
│    └─ autocompact (proactive)  超阈值时 LLM 生成摘要              │
├──────────────────────────────────────────────────────────────────┤
│ 2. BUILD PROMPT                                                   │
│    拼 fullSystemPrompt = base + systemContext                     │
│    memory/skills 走 userContext 在 messages 前 prepend，不塞 system│
├──────────────────────────────────────────────────────────────────┤
│ 3. STREAM LLM                                                     │
│    callModel({messages, systemPrompt, tools, signal, options})    │
│    边 stream 边分流：                                              │
│      · 文本/思考 → 直接 yield                                      │
│      · tool_use → 送进 streamingToolExecutor（可边 stream 边跑）   │
│      · recoverable error → 先 withhold，留给 step 5 处理           │
│    → Post-sampling hook（stop 判断前触发）                         │
│    → Abort 检查 ①（stream 结束时）                                 │
├──────────────────────────────────────────────────────────────────┤
│ 4. 有 tool_use？                                                   │
│      ├─ 否 → 进 STOP 分支（见下）                                  │
│      └─ 是 → 进 TOOLS                                              │
├──────────────────────────────────────────────────────────────────┤
│ 5. STOP（仅当没 tool_use）                                         │
│    a. 三种 recoverable 错误恢复：                                  │
│       · prompt_too_long   → collapse drain → reactive compact      │
│       · media_size        → reactive compact + strip media         │
│       · max_output_tokens → cap 8k → 64k → multi-turn retry × 3    │
│       API error 走 stop_failure_hooks，避免 death spiral           │
│    b. handleStopHooks() —— 6 个子 hook 链式跑，可阻断 continuation │
│    c. Token budget 检查                                            │
│    d. return { reason: 'completed' }                               │
├──────────────────────────────────────────────────────────────────┤
│ 6. TOOLS                                                           │
│    · streamingToolExecutor.getRemainingResults() 或 runTools()    │
│    · 每个 tool 先过 canUseTool（permission）                       │
│    · 并发上限 CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY，默认 10        │
│    → Abort 检查 ②（tools 执行完）                                  │
│    → 起异步 Haiku 总结本轮 tool calls（下轮 LLM 前 await）         │
│    → yield attachments（queued commands + memory）                 │
│    → turnCount++，maxTurns 检查，continue                          │
└──────────────────────────────────────────────────────────────────┘
```

## 关键设计特征

1. **压缩 3+2 层**：3 层 iteration-开头的廉价裁剪 + autocompact（proactive）+ reactive compact。每轮都跑前几层，不走 LLM。
2. **生成器做 orchestrator**：`async function*` 永不 throw，错误路径全部通过 `yield { type: 'error' }` + return `reason` 传出，`.return()` / `.throw()` 可驱动任何分支。
3. **State 两级**：
   - turn 快照 `const config`（不可变）
   - 会话实时 Zustand `AppState`，每轮 `getAppState()` 读——plan mode / model 切换靠它生效，不发事件。
4. **Tool 可以边 stream 边跑**：`streamingToolExecutor` 在 LLM 还在喷 token 时启动工具，turn 末尾汇总喂回。
5. **没有全局 maxTurns**：由调用方（REPL / SDK / AgentTool）决定；`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3` 是唯一硬常量。
6. **Sub-agent 就是普通工具**：`AgentTool.run()` 内部递归调 `queryLoop`，靠 `toolUseContext.agentId` 过滤 queued commands。
7. **Hook 三节点**：`post_sampling`（stream 结束）→ `stop`（6 子 hook）→ `stop_failure`（API error 分流）。`user_prompt_submit` 在 submitMessage 外层，不在 queryLoop 里。
8. **取消两道检查**：stream 结束时 + tools 完成时；保证不出现没有 tool_result 的 orphan tool_use。

## 关键常量

| 常量 | 值 | 位置 |
|---|---|---|
| `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT` | 3 | `query.ts:164` |
| `turnCount` 初值 | 1 | `query.ts:276` |
| `maxTurns` | **调用方传入**，无默认硬上限 | `submitMessage` 参数 |
| `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` | 10 | env |
| `ESCALATED_MAX_TOKENS` | 64k | `utils/context.js` |

## 延伸

- **详版 walkthrough**（逐步 + file:line）：[claude-code-query-loop-walkthrough.md](./claude-code-query-loop-walkthrough.md)
- 与 mustang 对比：[claude-code-comparison.md § 3](./claude-code-comparison.md#3-engine--orchestrator)
- 覆盖度：[claude-code-coverage.md](./claude-code-coverage.md)
