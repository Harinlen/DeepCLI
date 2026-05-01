# SessionManager

## Purpose

SessionManager 是 Kernel 的会话子系统。它实现
[`SessionHandler` 接口](../interfaces/protocol.md#sessionhandler-接口)
（7 个方法），由协议层在完成 JSON-RPC 反序列化后直接调用。

**层间契约**：SessionManager 的每个方法都接收 Pydantic 参数对象、返回
Pydantic 结果对象。它**不接触** ACP JSON 线格式、JSON-RPC id、或 WebSocket ——
那些全部由协议层处理完后才到这里。

职责边界：

- ✅ `SessionHandler` 7 个方法的业务实现（new / load / list / prompt / set_mode / set_config_option / cancel）
- ✅ Session 持久化（JSONL append-only）
- ✅ 多连接 broadcasting（一个 session 可以被多个 WebSocket 连接观看）
- ✅ Prompt turn 的串行 + queue 机制
- ✅ Cancel 任务跟踪（按 session_id → in-flight task）
- ✅ 每个 session 持有一个 Orchestrator 实例的生命周期管理
- ✅ Session metadata（title / updated_at / ...）的维护
- ❌ **不实现** Orchestrator 内部逻辑（LLM 调用、tool 执行、prompt 构建）—— 那是 [Orchestrator](orchestrator.md) 的事
- ❌ **不接触** JSON-RPC 帧、WebSocket IO、ACP 方法路由 —— 那是协议层的事

## Session vs Orchestrator 的边界

这两个东西经常被混在一起说，但它们**是两个独立的设计**：

| Session（本文档） | Orchestrator（独立文档）|
|---|---|
| 持久化 conversation 到 JSONL | 在内存里跑 conversation 主循环 |
| 多连接广播 | 不知道 WebSocket 的存在 |
| In-flight task 跟踪（cancel）| 接收 CancelledError 自行清理 |
| ACP 协议方法实现 | 接受 prompt 输入、产事件输出 |
| Session lifecycle (create/load/destroy) | 一个 Session 一个实例，跟随 Session 生死 |
| 不知道 Provider / Tools 怎么工作 | 全部依赖 Provider / Tools / PromptBuilder / Memory / Hooks |

**清晰的 seam**：Orchestrator 对 Session 只暴露一个最小接口：

```python
class Orchestrator(Protocol):
    def query(
        self,
        prompt: list[ContentBlock],
        *,
        on_permission: PermissionCallback,
    ) -> AsyncGenerator[OrchestratorEvent, StopReason]:
        """Run a prompt turn, yielding events until LLM stops calling tools.
        Generator return value is the StopReason."""

    async def close(self) -> None:
        """Cancel any pending tasks, release resources."""
```

Session 拿到 Orchestrator 后只调 `Orchestrator` Protocol 上定义的方法，
不知道也不关心 Orchestrator 内部用什么 Provider、跑什么 tool、怎么压缩 context。

注意 `HandlerContext`（含 WebSocket 句柄）**不进 `query()`**。需要往
client 回传的唯一通道是 permission 请求，由 `on_permission` callback
承载——Session 实现这个 callback，内部通过 `ProtocolAPI` 发出
`session/request_permission`，Orchestrator 对此一无所知。

`OrchestratorEvent` 完整类型族和 `StopReason` 定义见
[orchestrator.md](orchestrator.md)，与 ACP `session/update` 的映射见
[protocol.md 的事件映射表](../interfaces/protocol.md#会话层事件--sessionupdate-映射)。

这个 seam 让 Session 可以**先于** Orchestrator 完成设计 —— 我们不需要等 Provider / Tools / Memory 那些子系统都设计完才能动 Session。

## Design Decisions

### 1. 每个 Session 长期持有一个 Orchestrator

**Session 创建时构造 Orchestrator，整个 session 生命周期共享同一个实例**。Session 进入"空闲"状态时（最后一个连接断开、当前 turn 结束、queue 为空）Orchestrator 也保留在内存里，等待下一个 turn 或新连接。

理由：
- Conversation history 自然地跟着 Orchestrator 走，不用反复在 Session 和 Orchestrator 之间搬家
- Plan mode 状态、context compactor 的中间状态、provider 连接池等都能复用
- Session 销毁时（kernel 关闭 or 用户显式 delete）才一并销毁 Orchestrator

只有在 **kernel 进程关闭**或**用户显式 delete session** 时 Orchestrator 才被销毁。Session 自己的生命周期就是 Orchestrator 的生命周期。

### 2. 串行 Prompt Turn + FIFO 队列

**ACP 没规定**同一个 session 能否并发处理多个 prompt。我们的决定是**串行处理 + 允许排队**：

- 同一时间一个 session 最多有一个 turn 在跑
- 新 `session/prompt` 到达时若已有 turn 在跑，则**进入队列**
- 队列按 FIFO 顺序处理，每个排队的 prompt 等前面的 turn 完成后**自动开始**
- Client 看到的是：每个 `session/prompt` request 的 response 在它**实际处理完**那一刻才发回去（response delay = queue 等待时间 + turn 实际运行时间）

详细机制见 [Prompt Queue](#prompt-queue) 小节。

### 3. JSONL Append-Only 持久化

每个 session 一个 `.jsonl` 文件，append-only 写入。和 Claude Code 同款做法。

理由：
- **崩溃安全**：每个事件即时落盘，kernel 进程随时被 kill 都最多丢失最后一个未完整写入的行
- **写入性能**：不管文件多大，每次 append 都是 O(1)
- **流友好**：`tail -f` 能直接看 session 在发生什么
- **schema 灵活**：每行一个 typed event，无需预定义全局 schema
- **生态兼容**：grep / jq / awk 直接可用

不用 Jupyter notebook (`.ipynb`) 作为存储格式，因为 ipynb 是 whole-blob-rewrite（每次保存重写整个文件），既慢又对崩溃不安全。Jupyter 自己在 live 场景下也没解决"崩溃丢失"的问题，它的设计假设是"用户是显式 save 的源代码文件作者"，这和我们 kernel 持续写入的场景完全不同。

**导出**到 ipynb / markdown / html 等渲染友好的格式作为**独立 CLI 命令**实现，不影响存储格式。见 [Future CLI Commands](#future-cli-commands)。

### 4. 长期保留，无自动清理

Session 一旦创建就**永久保留**（直到用户显式删除）。

- **无 TTL** —— 不会因为太久没用而过期
- **无 LRU 淘汰** —— 不会因为内存压力被淘汰
- **无启动清理** —— kernel 重启不会删任何 session
- **手动删除** —— `mustang session delete <id>` CLI 命令是唯一删除入口

理由：用户的对话历史是有价值的，删除应该是用户主动行为，kernel 不应该替用户做这个决定。Claude Code 也是同样的策略（用户的 `~/.claude/projects/` 目录里的 session 文件会一直累积到用户手动清理）。

### 5. 内存 vs 磁盘的边界

Session 的状态分两类：

| 类别 | 存什么 | 在哪 |
|---|---|---|
| **持久状态** | conversation history、metadata（cwd / title / created_at / ...）、mode、config options | JSONL 文件 |
| **运行期状态** | Orchestrator 实例、in-flight prompt task、prompt queue、绑定的 connection set | 内存 |

**Kernel 重启后**：所有运行期状态丢失。Session 不主动 reload —— 当 client 调 `session/load` 或 `session/prompt` 时才从 JSONL 重建到内存。`session/list` 不需要把 session 加载到内存，只读 metadata。

这意味着 SessionManager 的状态有两层：
- **磁盘上有但内存里没有**的 session：合法状态，下次被引用时按需 load
- **磁盘上有内存里也有**的 session：活跃状态，可以接 prompt

session/list 同时报告这两类（统一从磁盘扫描出来）。

## File Layout

```
~/.mustang/sessions/                                # 顶层目录，不在 state/ 下
  index.json                                        # 全局索引（加速 list）
  <session-uuid>.jsonl                              # 主 session 文件
  <session-uuid>/                                   # 同名 aux 目录
    subagents/
      agent-<short-hash>.jsonl                      # 每个 sub-agent 一个文件
      agent-<short-hash>.meta.json                  # sub-agent metadata sidecar
    tool-results/
      <hash>.txt                                    # 大 tool 输出 spillover
```

**为什么不在 `state/` 下**：`state/` 是 kernel 实现细节（auth_token、password.hash 之类用户**不应该手碰**的运行时产物），而 session 是**用户可见的内容**（用户的对话历史，可能想 backup / sync / share）。Sessions 应该在顶层，路径短、可发现性强。

**和 Claude Code 的对比**：Claude Code 用 `~/.claude/projects/<project-path-encoded>/...`，把 cwd 编码到目录名里。我们**不**这么做 —— cwd 已经存在事件 metadata 里，再用目录结构表达是冗余，而且 session 多 cwd 的情况（用户在多个目录跑同一个 session）会变成两个目录。一级 flat 目录更简单。

**`<session-uuid>` 格式**：标准 UUID4，带连字符，例如 `a1b2c3d4-e5f6-4789-abcd-1234567890ab`。

- 用 `uuid.uuid4()` 生成，122 bit 熵
- 标准格式工具齐全（grep / 显示 / parse 都通用）
- 文件名直接用 UUID 字符串（带 `-`），不去掉连字符

**Aux 目录的作用**：一个 session 不只有一个 .jsonl 文件。同名目录 `<session-uuid>/` 存放与该 session 相关但又**不适合塞进主 JSONL** 的东西：

- **`subagents/`** —— 每个 sub-agent 一个独立的 .jsonl + .meta.json sidecar。理由：sub-agent 内部可能产生大量事件，混在主 JSONL 里既污染主对话流，又让 replay 主对话变慢。详见 [Sub-agent Storage](#sub-agent-storage)
- **`tool-results/`** —— 大的 tool 输出（读大文件、grep 一万行、子进程 stdout）spillover 到独立 .txt 文件。主 JSONL 只存引用。详见 [Tool Result Spillover](#tool-result-spillover)

文件 + 同名目录在 POSIX 上完全合法（`<uuid>.jsonl` 是文件，`<uuid>` 是目录，不冲突）。

### Sub-agent Storage

学 Claude Code 的设计：每个 sub-agent 在 `<session-uuid>/subagents/` 下有**两个文件**：

```
<session-uuid>/subagents/
  agent-<short-hash>.jsonl          # 该 sub-agent 的事件流
  agent-<short-hash>.meta.json      # 轻量元数据 sidecar
```

**`agent-<short-hash>.jsonl`** 是这个 sub-agent 的完整事件流，schema 和主 JSONL 一致（同样的 `event_id` / `parent_id` / `type` / metadata），但 `agent_depth >= 1`。

**`agent-<short-hash>.meta.json`** 是 sidecar，存"不需要 replay 但 list / 调试时有用"的信息：

```json
{
  "agent_id": "uuid",
  "agent_type": "Explore" | "general-purpose" | ...,
  "description": "Analyze ACP cancellation flow",
  "spawned_at": "2026-04-14T...",
  "spawned_by_event": "ev_...",        // 主 JSONL 里的 tool_call event id
  "parent_session": "<session-uuid>",
  "depth": 1,
  "completed_at": "...",                // null = 还在跑
  "stop_reason": "end_turn"              // null = 未结束
}
```

为什么用 sidecar 而不是放进 jsonl 第一行 / 最后一行：list sub-agents 时可以直接 `os.listdir` + 读 sidecar，不用打开每个 .jsonl 解析事件。

**`<short-hash>` 格式**：`secrets.token_hex(8)`（16 hex chars）。够区分同 session 内的多个 sub-agents，不需要全局唯一。

**链接关系**：

- 主 JSONL 里有一个 `tool_call` 事件（kind=`agent`），它的 `raw_input` 字段记录 sub-agent 的 `agent_id`
- 主 JSONL 里有一个 `sub_agent_spawned` 事件（一种新的 type），记录 sub-agent 的文件路径和 agent_id
- Sub-agent 的 .jsonl 里第一个事件的 `parent_id` **跨文件**指向主 JSONL 里的 `sub_agent_spawned` 事件 id
- Sub-agent 跑完后，主 JSONL 写一个 `tool_call_update` (status=completed) 事件总结结果

这样 replay 主对话**不需要打开 sub-agent 文件** —— 主 JSONL 已经有 sub-agent 的 tool_call 起点和 result 终点，足够呈现"调用了一个 sub-agent，得到了这个结果"。要看 sub-agent 内部细节才打开对应的 .jsonl。

### Tool Result Spillover

大的 tool 输出（读 100KB 文件、grep 一万行、`ls -R /` 之类）如果直接塞进 JSONL 会让主 session 文件膨胀到几十 MB，replay 慢、grep 难、内存占用大。

学 Claude Code：**输出超过阈值时 spillover 到独立文件**。

**触发条件**：tool result 的文本大小 `> SessionFlags.tool_result_inline_limit`（默认 8 KiB）。

**Spillover 流程**：

1. SessionManager 在写 `tool_call_update` 事件（status=completed）时检测 content 大小
2. 如果超阈值：
   - 生成 `result_hash = secrets.token_hex(8)`
   - 写到 `<session-uuid>/tool-results/<result_hash>.txt`
   - JSONL 事件里的 content 字段改成引用：
     ```json
     {
       "type": "tool_call_update",
       "tool_call_id": "...",
       "status": "completed",
       "content": [{
         "type": "spilled",
         "path": "<session-uuid>/tool-results/<result_hash>.txt",
         "size": 153214,
         "preview": "First 200 chars of the actual content..."
       }]
     }
     ```
3. 不超阈值则正常 inline

**Replay 时的处理**：SessionManager 读到 `type: "spilled"` 的 content block，按需读 `.txt` 文件、还原成正常的 ACP `ContentBlock { type: "text", text: <full content> }` 后再发给 client。Client 看到的是完整 content，不知道有 spillover。

**ACP `_meta` 处理**：spilled content 是 mustang 内部约定，**不**直接发 ACP `type: "spilled"`（client 不认识）。spillover 完全是 SessionManager 的内部存储优化。

### `index.json` 全局索引

`~/.mustang/sessions/index.json` 缓存所有 session 的 metadata，加速 `session/list` —— 不需要扫描 / 解析每个 .jsonl 文件。

**Schema**（学 Claude Code 的 `sessions-index.json`）：

```json
{
  "version": 1,
  "entries": [
    {
      "session_id": "a1b2c3d4-e5f6-...",
      "cwd": "/home/user/project",
      "title": "Implement session list API",
      "first_prompt": "Can you help me design...",  // 截断的预览
      "message_count": 12,
      "created": "2026-04-14T05:39:03.887Z",
      "modified": "2026-04-14T06:12:45.123Z",
      "git_branch": "main",
      "kernel_version": "0.1.0"
    },
    ...
  ]
}
```

每个 entry 是 ACP `SessionInfo` 的超集 —— 包含 ACP 要求的字段（`session_id` / `cwd` / `title` / `updated_at`）加上 mustang 自己的扩展字段（`message_count` / `git_branch` / `created` 等用于 list UI）。

**更新策略**：

- **写时同步更新** —— SessionManager 每写一个会改变 metadata 的事件（`session_created` / `user_message` / `turn_completed` / `session_info_changed`）后**立即**更新 index.json 对应 entry
- **原子写**：先写 `index.json.tmp`，然后 `os.replace()` 到 `index.json`
- **写竞争**：单 SessionManager 进程 + 一个 `asyncio.Lock` 保护 index 写入

**损坏 / 缺失时的恢复**：

如果 `index.json` 不存在 / parse 失败 / version 不识别 / 内容明显损坏，SessionManager **启动时** rebuild：

1. 扫描 `~/.mustang/sessions/*.jsonl`
2. 对每个文件读取首行（`session_created`）和末行（`turn_completed` / `session_info_changed`）
3. 重建 entry
4. 原子写新 `index.json`

Rebuild 是 O(N) 文件操作，但只在异常时触发。正常运行下 index 是增量维护的。

**Sub-agent 不进 index**：`<session-uuid>/subagents/*.jsonl` 这些文件**不**写到 `index.json`。索引只列主 session。Sub-agent 的发现走"先 list session → 进入某个 session 的 aux 目录 → list subagents/" 的层级路径，由 `mustang session show <id> --include-subagents` 这种调试命令处理，不走 ACP 协议路径。

## JSONL Event Schema

每条事件是一行独立的 JSON 对象，包含两类字段：**通用 metadata**（每个事件都有）和 **type-specific fields**（按 `type` 字段决定）。

### 通用 metadata

```python
{
  "event_id": "ev_<uuid4 hex>",       # 该事件的唯一 id（uuid4().hex 格式）
  "parent_id": "ev_..." | null,       # 链表前一项；首事件为 null
  "type": "<event type>",             # discriminator
  "timestamp": "2026-04-14T12:34:56.789Z",  # UTC ISO 8601
  "session_id": "<session-uuid>",     # UUID4 标准格式，冗余但便于 grep / 调试
  "agent_depth": 0,                   # 0 = 主 session；>= 1 = sub-agent 文件
  "kernel_version": "0.1.0",          # 写入时的 kernel 版本
  "cwd": "/abs/path",                 # 写入时的 session cwd
  "git_branch": "main" | null,        # 写入时的 git branch（可选）
  ...                                 # type-specific fields
}
```

每个事件都重复这些 metadata 是**有意冗余**。学 Claude Code，目的是用户报 bug 时不需要追问"你当时在哪个目录、什么 git 分支、什么版本的 kernel"，直接看 jsonl 一目了然。

**没有 `is_sidechain` 字段**：sub-agent 事件存在独立的 `<session-uuid>/subagents/agent-<hash>.jsonl` 文件里，不和主 session 事件混在一起。一个事件文件里所有事件**要么全是主 session（`agent_depth == 0`）要么全是同一个 sub-agent（`agent_depth >= 1`）**。区分靠**它在哪个文件里**，不需要 per-event 标记。

`parent_id` 形成链表 —— 同一个文件内的链表是连续的；**跨文件链接**（sub-agent 第一个事件 → 主 session 的 `sub_agent_spawned` 事件）是合法的，replay 时如果遇到 parent_id 不在当前文件内，就当作"外部根"处理。

### 事件类型

| `type` | 触发 | 主要字段 | 对应 ACP `session/update` |
|---|---|---|---|
| `session_created` | `session/new` 成功 | `mcp_servers` / `initial_mode` | —— |
| `session_loaded` | `session/load` 成功 | `loaded_at` | —— |
| `user_message` | `session/prompt` 入站 | `content: ContentBlock[]` / `request_id` | `user_message_chunk`（仅 replay 时）|
| `agent_message` | LLM 文本输出 | `content: ContentBlock[]` | `agent_message_chunk` |
| `agent_thought` | LLM reasoning 输出 | `content: ContentBlock[]` | `agent_thought_chunk` |
| `plan` | Orchestrator plan 更新 | `entries: PlanEntry[]` | `plan` |
| `tool_call` | Tool 开始 | `tool_call_id` / `title` / `kind` / `raw_input` | `tool_call` |
| `tool_call_update` | Tool 状态 / 结果变化 | `tool_call_id` / `status` / `content` / `locations` | `tool_call_update` |
| `sub_agent_spawned` | AgentTool 启动子 agent | `agent_id` / `agent_type` / `description` / `subagent_file` | —— (`_meta.mustang/agent_start`) |
| `sub_agent_completed` | 子 agent 结束 | `agent_id` / `stop_reason` / `duration_ms` | —— (`_meta.mustang/agent_end`) |
| `permission_request` | Tool 请求权限 | `tool_call_id` / `options` | —— (走 outgoing request) |
| `permission_response` | 用户决策 | `tool_call_id` / `outcome` | —— |
| `mode_changed` | mode 切换 | `mode_id` / `from` | `current_mode_update` |
| `config_option_changed` | 配置项切换 | `config_id` / `value` / `full_state` | `config_option_update` |
| `available_commands_changed` | skill 列表变更 | `commands: AvailableCommand[]` | `available_commands_update` |
| `session_info_changed` | metadata 变更 | `title?` / `_meta?` | `session_info_update` |
| `turn_started` | Prompt turn 开始 | `request_id` / `queue_position` | —— (`_meta.mustang/turn_state`) |
| `turn_completed` | Prompt turn 结束 | `request_id` / `stop_reason` / `duration_ms` | —— (响应 session/prompt) |
| `turn_cancelled` | Turn 被 cancel | `request_id` / `cancelled_at_event` | —— |

**注意**：

- `agent_message` / `agent_thought` 在 JSONL 里是**整个 turn 的累积文本**，不是流式 chunk。流式 chunk 只在 emit 给 client 时存在；写盘时合并成完整一段
- `permission_request` / `permission_response` 持久化是为了 replay session 时能恢复"哪些 tool 走了哪条权限路径"，但 replay 时**不**重新触发权限请求（client 不会再被问一次）
- `turn_started` / `turn_completed` 是 session-level lifecycle 标记，不直接对应 ACP update —— 它们用来追踪 prompt 的开始 / 结束 / cancel 时间，便于诊断和分析
- `sub_agent_spawned` / `sub_agent_completed` 只出现在主 JSONL 里 —— 主 session 知道"调用了 sub-agent 并得到结果"就够了，sub-agent 内部的事件流在独立的 `<session-uuid>/subagents/agent-<hash>.jsonl` 文件
- **`tool_call_update.content` 可能含 `type: "spilled"` 的 block**，引用 `<session-uuid>/tool-results/<hash>.txt`。这是 SessionManager 内部约定，不进 ACP 通知 —— replay 时会被还原成正常 `type: "text"` 后再发给 client。详见 [Tool Result Spillover](#tool-result-spillover)

### 写入语义

#### 不调用 `fsync`

每条事件 write 后**不**调用 `fsync()`。这是个值得展开说的决定。

**`fsync` 的作用**：强制把**这个文件的所有 buffered write 同步到物理磁盘**后才返回。不调 `fsync` 时，`write()` 只是把数据写到 OS 的 page cache，OS 内核会在自己合适的时机（Linux 默认 ~30 秒，或 dirty page 达阈值时）把脏页刷到磁盘。

**`fsync` 的延迟成本**：

| 介质 | 单次 `fsync` 延迟 |
|---|---|
| NVMe SSD | 0.1-1 ms |
| SATA SSD | 1-5 ms |
| HDD | 5-50 ms |
| 网盘 / 云盘 | 10-100+ ms |

一个 prompt turn 可能产生几百到几千个事件（流式 text delta、每个 tool call、每个 tool result、plan update、permission 往返……）。如果每个事件都 `fsync`：

- SSD 上：每事件 1-5 ms × 千事件 = 1-5 秒额外延迟，**用户感知到的"AI 反应慢"全是 fsync 等的**
- HDD / 云盘：直接不可用

**不 `fsync` 时丢失的具体是什么**：

- **正常异常**（Python 异常抛到顶 / `kill -TERM` / `Ctrl+C` / main return）—— OS page cache **完全不受影响**。下次启动读 JSONL 看到的就是最后一次 `write()` 的内容
- **进程被 SIGKILL**（OOM killer / `kill -9`）—— **page cache 仍在**。Linux 把 page cache 和进程内存分开管理，进程死了页缓存还在内核里
- **OS 内核 panic / 系统断电 / 硬件故障** —— 这才是真的丢东西。最多丢失 ~30 秒内的写入

所以"省掉 fsync 真正会丢的"只在 **OS 级灾难**情况下发生，量级是"最后一个 in-flight turn 的部分事件"。

**这种丢失的语义后果**：

- 那个 turn 还**没写到 `turn_completed`** → SessionManager 还没对 `session/prompt` 响应 → client 看来"请求超时 / 连接断开"
- Client 重连后看到 session 处于"上一个 turn 不完整"状态
- 这等价于"client 在 turn 跑到一半时网线被拔了" —— **本来就要处理的常见场景**，不是 fsync 防得住的额外问题

**结论**：省掉 `fsync` 性能收益巨大（千倍以上），代价仅在 OS 级灾难下显现，且这个代价被我们的 turn 语义**本来就允许**（未确认完成 = 可丢失）。

#### 写入并发

单 SessionManager 进程，单 session 写入用一个 `asyncio.Lock` 保护，避免两个 task 同时往同一个文件 append 时交错。POSIX 单次 `write` < 4KB 通常原子，但 ACP 的 update 事件可能超过 4KB（比如带大 plan 或长 text chunk），加锁保险。

每个 session 一把锁，session 间互不影响。

## Session Data Model

```python
@dataclass
class Session:
    """In-memory representation of an active session."""

    session_id: str
    cwd: Path
    created_at: datetime
    updated_at: datetime              # 最后一个事件的时间戳
    title: str | None                 # 自动生成或用户设置
    
    # 持久化的会话状态
    mode_id: str | None               # 当前 mode（如果使用 modes API）
    config_options: dict[str, str]    # 当前 config option values
    mcp_servers: list[McpServerConfig]  # 创建时的 MCP server 列表
    
    # 运行期状态（不持久化）
    orchestrator: Orchestrator        # 长期持有，session 销毁时才 close
    connections: set[ConnectionContext]  # 当前绑定的 WS 连接
    in_flight_turn: TurnState | None  # 正在跑的 turn（None = 空闲）
    queue: deque[QueuedTurn]          # 排队等待的 turn
    
    # 写入控制
    _jsonl_path: Path
    _write_lock: asyncio.Lock
    _last_event_id: str | None        # 用于设置下一个事件的 parent_id


@dataclass
class TurnState:
    """A prompt turn currently being processed."""
    request_id: str | int             # ACP request id
    task: asyncio.Task                # the asyncio task running this turn
    started_at: datetime
    user_message_event_id: str        # the user_message event written for this turn


@dataclass
class QueuedTurn:
    """A prompt turn waiting in the queue."""
    request_id: str | int
    params: PromptRequest             # ACP params, includes content
    queued_at: datetime
    response_future: asyncio.Future[PromptResponse]
    """ProtocolAPI 等这个 future resolve 后才把 response 发给 client。
    Cancel 时 set_result(PromptResponse(stopReason='cancelled'))。
    正常完成时 set_result(PromptResponse(stopReason=...))."""

    # Gateway-only fields (None for normal WS-originated turns)
    text_collector: asyncio.Future[str] | None = None
    """If set, _run_turn_internal collects all TextDelta content and
    set_result(joined_text) when the turn ends. Used by run_turn_for_gateway
    to retrieve the assistant reply without a WS connection."""
    on_permission: PermissionCallback | None = None
    """If set, used instead of the session's default WS permission round-trip.
    GatewayAdapter passes a closure that sends a message to the platform user
    and awaits their yes/no reply."""
```

## Prompt Queue

### 入队和处理流程

```python
async def prompt(self, ctx: HandlerContext, params: PromptRequest) -> PromptResponse:
    session = self._get_or_load_session(params.sessionId)
    
    if session.in_flight_turn is None and not session.queue:
        # 空闲，直接跑
        return await self._run_turn(session, ctx, params)
    
    # 有正在跑的 turn，入队
    if len(session.queue) >= self._flags.max_queue_length:
        raise InternalError("session prompt queue full")

    queued = QueuedTurn(
        request_id=ctx.request_id,
        params=params,
        queued_at=datetime.now(UTC),
        response_future=asyncio.Future(),
    )
    session.queue.append(queued)
    
    # 通知 client UI: 你的 prompt 被排队了
    await ctx.protocol.notify(
        "session/update",
        SessionNotification(
            sessionId=params.sessionId,
            update=AgentMessageChunk(
                content=ContentBlock(type="text", text=""),
                _meta={
                    "mustang/turn_state": "queued",
                    "mustang/turn_request_id": ctx.request_id,
                    "mustang/queue_position": len(session.queue),
                },
            ),
        ),
    )
    
    # 等待这个 turn 实际完成（由 _run_turn 在轮到时 set_result）
    return await queued.response_future
```

**SessionManager 的"消费循环"**：每个 session 维护一个**消费 task**（在 session 创建时启动），它专门负责按顺序跑 in-flight turn 和队列里的 turn：

```python
async def _consume_loop(self, session: Session) -> None:
    """长期运行的 task，处理 session 的 turn queue。"""
    while True:
        queued = await self._next_queued(session)  # 阻塞等队列里有东西
        try:
            response = await self._run_turn_internal(session, queued)
            queued.response_future.set_result(response)
        except asyncio.CancelledError:
            queued.response_future.set_result(
                PromptResponse(stopReason="cancelled")
            )
            raise  # 让消费 task 也被 cancel
        except Exception as exc:
            queued.response_future.set_exception(exc)
```

实际实现可能更简单 —— 不需要独立的消费 task，直接让 `_run_turn_internal` 在 finally 里检查 queue 然后递归 / 循环跑下一个就行。具体写法实装时定，关键是**串行 + queue + Future-based response**这个语义。

### Cancel 命中带 queue 的 session

`session/cancel` notification 的语义是"停止这个 session 的所有处理"：

```python
async def cancel(self, ctx, params: CancelNotificationParams) -> None:
    session = self._sessions.get(params.sessionId)
    if session is None:
        return  # 不存在的 session 直接忽略，符合 ACP 通知语义

    # 1. Cancel 当前正在跑的 turn
    if session.in_flight_turn is not None:
        session.in_flight_turn.task.cancel()
        # task 的 finally 块会把 in_flight_turn 清空，
        # 并且 Future 会 set_result(stopReason='cancelled')

    # 2. 清空整个队列，所有排队的 turn 都标记为 cancelled
    while session.queue:
        queued = session.queue.popleft()
        queued.response_future.set_result(
            PromptResponse(stopReason="cancelled")
        )
        # protocol.md 强调：cancelled 走 stopReason 而不是 error
```

注意 cancel **不写持久化**直到 turn 实际收到 CancelledError 后由 turn 自己写一个 `turn_cancelled` 事件。Cancel notification 本身不立即写盘，避免"写了 cancelled 但实际 turn 还没真停"的不一致状态。

### 队列长度上限作为 Flag

队列上限是一个 `SessionFlags.max_queue_length` flag（**不是** Config，因为是启动期决定运行期不变 —— 见 [flags.md](flags.md) 的 Flag vs Config 边界）：

```python
class SessionFlags(BaseModel):
    """Session subsystem flags. Runtime-immutable."""
    max_queue_length: int = Field(50, ge=1, le=10000)
    enable_auto_title: bool = Field(True, description="Auto-generate title from first turn")
```

`max_queue_length` 默认 50，对单用户场景宽松。改这个值需要重启 kernel。超出上限的 prompt 入队失败，client 收到 `-32603 Internal error` 带 message `"session prompt queue full"`。

## SessionHandler 实现

每个方法对应 [protocol.md 的 SessionHandler 接口](../interfaces/protocol.md#sessionhandler-接口)。这里描述每个方法在 Session 层的具体行为。

### `new(ctx, params: NewSessionRequest) -> NewSessionResponse`

1. 生成 `session_id = str(uuid.uuid4())`（标准 UUID4 格式带连字符）
2. 创建 `Session` 对象，cwd / mcp_servers 来自 params
3. 通过 `self._module_table` 构造 Orchestrator 实例（传入 cwd、mcp_servers、其他依赖）
4. 在磁盘创建：
   - 主文件 `~/.mustang/sessions/<session_id>.jsonl`
   - aux 目录 `~/.mustang/sessions/<session_id>/{subagents,tool-results}/`（按需创建，第一次写 sub-agent 或 spillover 时才建子目录）
5. 写入 `session_created` 事件到主 JSONL
6. 在 `index.json` 增加该 session 的 entry
7. 把 session 注册到 `self._sessions: dict[str, Session]`
8. 把 `ctx.conn` 加入 `session.connections`，更新 `ctx.conn.bound_session_id = session_id`
9. 返回 `NewSessionResponse(sessionId=session_id)`

### `load(ctx, params: LoadSessionRequest) -> LoadSessionResponse`

1. 检查主文件是否存在（`~/.mustang/sessions/<session_id>.jsonl`），不存在抛 `-32002 Resource not found`
2. 如果 session 已在内存（被之前的连接 load 过），跳到步骤 4（不需要重新读 JSONL）
3. 否则从磁盘重建：
   1. 读主 JSONL → 反序列化成事件序列
   2. 遇到 `tool_call_update.content` 里 `type: "spilled"` 的 block → 读对应 `tool-results/<hash>.txt` 文件，把 spilled block 还原成正常的 `type: "text"` block
   3. 重建 `Session` 对象（cwd / title / mode / config_options 从事件里恢复）
   4. **构造 Orchestrator 实例**，把还原后的 conversation history 通过构造参数（`initial_history=...`）一次性灌入。Orchestrator **不需要**知道这是 replay —— 它只看到"我开局就有 N 条历史消息"
   5. 注册到 `self._sessions`
4. **回放历史给 client**：按 ACP 规范遍历事件，按事件类型 → ACP `sessionUpdate` variant 转换 → 通过 `ctx.protocol.notify("session/update", ...)` 一条条发出去
5. 把 `ctx.conn` 加入 `session.connections`
6. 所有事件发完后才返回 `LoadSessionResponse(_meta=None)`

**关键约束**：步骤 4 必须**完整发完**才能在步骤 6 响应 LoadSessionRequest。ACP 规范明确要求"agent **MUST** replay the entire conversation"。

**Sub-agent 不展开 replay**：步骤 4 遍历主 JSONL 时，遇到 `sub_agent_spawned` / `sub_agent_completed` 事件**不**打开 sub-agent 文件展开。主 JSONL 里有 `tool_call` (kind=agent) 起点和 `tool_call_update` (status=completed) 终点，已经表达了"调用了一个 sub-agent，得到了这个结果"，对 client 来说足够。要看 sub-agent 内部细节是 `mustang session show <id> --include-subagents` 这种调试命令的事。

**Replay 期间不重复写盘**：回放时产生的 `session/update` notifications 是从已有事件读出来再发出去的，**不**应该 append 回 JSONL（不然每次 load 都翻倍）。实现上 SessionManager 通过一个内部参数路径区分"replay 发送" vs "新事件发送"，replay 路径只调 `notify`，跳过 `_append_event`。

### `list(ctx, params: ListSessionsRequest) -> ListSessionsResponse`

**直接读 `index.json`**，不扫描 JSONL 文件：

1. 读 `~/.mustang/sessions/index.json`（如果不存在或损坏，触发 [rebuild](#indexjson-全局索引) 后再读）
2. 应用 `cwd` filter（如果 params 里有）
3. 按 `modified` desc 排序
4. **Cursor pagination**：见下方 [Cursor 实现](#cursor-实现)
5. 取一页（`SessionFlags.list_page_size`，默认 50）
6. 对每个 entry 转成 ACP 的 `SessionInfo`：

   ```python
   SessionInfo(
       sessionId=entry["session_id"],
       cwd=entry["cwd"],
       title=entry["title"],
       updatedAt=entry["modified"],
       _meta={
           "mustang/message_count": entry["message_count"],
           "mustang/git_branch": entry["git_branch"],
           "mustang/created": entry["created"],
       },
   )
   ```
7. 返回 `ListSessionsResponse(sessions=[...], nextCursor=...)`

`index.json` 是增量维护的（见 [`index.json` 全局索引](#indexjson-全局索引)），所以 list 不需要 IO 多个 .jsonl 文件，O(1) 文件读 + O(N) 内存过滤排序。N 通常是几十到几千，性能完全够用。

### Cursor 实现

ACP 明确规定 cursor 是 **opaque token**，client `MUST NOT` parse / modify / persist。所以我们的具体实现完全自由。

**方案**：cursor 是 base64 编码的 `(updated_at_iso, session_id)` 对。

```python
def encode_cursor(updated_at: datetime, session_id: str) -> str:
    raw = f"{updated_at.isoformat()}|{session_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()

def decode_cursor(token: str) -> tuple[datetime, str]:
    raw = base64.urlsafe_b64decode(token.encode()).decode()
    iso, sid = raw.split("|", 1)
    return datetime.fromisoformat(iso), sid
```

分页查询语义："拿 updated_at < cursor.updated_at OR (== AND session_id < cursor.session_id) 的下 N 条"。`session_id` 作为 tiebreaker 防止同毫秒事件造成跳过 / 重复。

页大小：固定 `SessionFlags.list_page_size`（默认 50）。

### `prompt(ctx, params: PromptRequest) -> PromptResponse`

完整流程见上面 [Prompt Queue](#prompt-queue) 小节。简要：

1. 找到 session（不存在抛 `-32002`）
2. 如果空闲且队列空，直接执行（`_run_turn`）
3. 否则入队（达到 max 抛 `-32603`），等待 future resolve
4. 返回 future 的 result（可能是 `stopReason: end_turn` 或 `cancelled`）

`_run_turn` 内部：

```python
async def _run_turn(
    self, session: Session, ctx: HandlerContext, params: PromptRequest
) -> PromptResponse:
    request_id = ctx.request_id
    task = asyncio.current_task()
    
    # 写 user_message 事件
    user_event_id = await self._append_event(
        session,
        type="user_message",
        content=params.prompt,
        request_id=request_id,
    )
    
    # 标记 in-flight
    session.in_flight_turn = TurnState(
        request_id=request_id,
        task=task,
        started_at=datetime.now(UTC),
        user_message_event_id=user_event_id,
    )
    await self._append_event(session, type="turn_started", request_id=request_id)
    
    try:
        # 跑 orchestrator,遍历事件流
        async for event in session.orchestrator.query(params.prompt, ctx):
            # 1. 写盘
            await self._append_event_from_orchestrator(session, event)
            
            # 2. 映射成 ACP session/update 并 broadcast 给所有 connections
            acp_update = self._map_to_acp(event)
            for conn in session.connections:
                await conn.protocol.notify(
                    "session/update",
                    SessionNotification(
                        sessionId=session.session_id,
                        update=acp_update,
                    ),
                )
        
        # 正常结束
        stop_reason = "end_turn"
    except asyncio.CancelledError:
        stop_reason = "cancelled"
        await self._append_event(
            session, type="turn_cancelled", request_id=request_id,
        )
    except MaxTokensError:
        stop_reason = "max_tokens"
    except MaxTurnRequestsError:
        stop_reason = "max_turn_requests"
    except RefusalError:
        stop_reason = "refusal"
    finally:
        await self._append_event(
            session, type="turn_completed",
            request_id=request_id, stop_reason=stop_reason,
        )
        session.in_flight_turn = None
        # 触发下一个 queued turn（如果有）
        self._maybe_dispatch_next(session)
    
    return PromptResponse(stopReason=stop_reason)
```

**Cancel 的 stopReason 通过正常 return 路径返回**，不抛异常 —— protocol.md 强调"cancelled 走 success response 不走 error"，这条由 SessionManager 实现，protocol layer 不需要特殊处理。

### `set_mode(ctx, params: SetSessionModeRequest) -> SetSessionModeResponse`

1. 找到 session
2. 校验 `params.modeId` 在 session 已知的 `availableModes` 里
3. 更新 `session.mode_id`
4. 写 `mode_changed` 事件
5. 通过所有 connections broadcast `current_mode_update` notification
6. 返回 `SetSessionModeResponse(_meta=None)`

如果在 mode change 期间有 in-flight turn，**允许中途切**（ACP 规范说 "current mode can be changed at any point during a session, whether the agent is idle or generating a response"）。Orchestrator 自己决定怎么响应中途的 mode 切换 —— Session 只负责更新状态、广播通知、通知 Orchestrator。

### `set_config_option(ctx, params: SetSessionConfigOptionRequest) -> SetSessionConfigOptionResponse`

类似 `set_mode`：

1. 找到 session
2. 校验 `configId` 和 `value` 合法
3. 更新 `session.config_options`
4. 写 `config_option_changed` 事件
5. Broadcast `config_option_update` notification（包含**完整** config 状态，ACP 要求）
6. 返回 `SetSessionConfigOptionResponse(configOptions=[...complete state...])`

注意 ACP 要求 `set_config_option` 的响应**必须**返回完整 config state（而不是 diff），因为某些 config 改变可能影响其他 config 的可选项。

### `cancel(ctx, params: CancelNotificationParams) -> None`

完整逻辑见 [Cancel 命中带 queue 的 session](#cancel-命中带-queue-的-session)。Cancel 是 notification 没有响应。

## Multi-connection Broadcasting

一个 session 可以被多个 WebSocket 连接同时观看（用户在两个不同 client 上 load 同一个 session）。

### 连接绑定

- `session/new` 成功时把 `ctx.conn` 加入 `session.connections`
- `session/load` 成功时同上
- WebSocket 连接关闭时（[传输层](../architecture.md#websocket-接入-session)负责通知 SessionManager）从所有 session 的 connections 集合里移除该 conn

### 事件 fanout

每条 `session/update` notification 都通过 session 当前所有 connection 发出去：

```python
for conn in session.connections:
    try:
        await conn.protocol.notify("session/update", update)
    except Exception:
        logger.exception("failed to notify connection %s", conn.connection_id)
        # 单个连接发送失败不影响其他连接，也不影响 session 本身
```

**异常隔离**：一个连接挂了不能让 session 的 turn 也挂。失败只 log。

### 单连接断开 ≠ Session 销毁

最后一个连接断开时 session 不会被销毁，它进入"空闲 + 0 connections"状态，等待下次 `session/load` 把新连接绑回来。In-flight turn（如果有）继续跑 —— 用户可能开了第二个客户端来观察。

但如果**所有连接都断开了且 in-flight turn 完成**，turn 期间产生的事件**都已经写到 JSONL**，且**没有 client 收到 session/update 流**。这对于"你打开 client 看到了你不在场时跑完的 turn"是 OK 的 —— client 重新 load 时会从 JSONL 回放看到所有事件。

## Subsystem Lifecycle

SessionManager 是一个普通 Subsystem（继承 `Subsystem` ABC，不是 bootstrap 服务），见 [architecture.md 的生命周期顺序](../architecture.md#启动顺序)：在所有可选子系统之后、作为最后一个加载的核心子系统。

### `startup()`

1. 通过 `self._module_table.flags.register("session", SessionFlags)` 拿 flag 实例
2. **不** bind 任何 ConfigManager section（一期没有运行期可调的 session 配置）
3. 确保目录 `~/.mustang/sessions/` 存在
4. 加载 / rebuild `~/.mustang/sessions/index.json`：
   - 如果存在且 `version` 识别 → 直接读到内存 `self._index`
   - 如果不存在 / 损坏 / version 不识别 → 扫描 `~/.mustang/sessions/*.jsonl` rebuild（详见 [`index.json` 全局索引](#indexjson-全局索引)）
5. 初始化 `self._sessions: dict[str, Session] = {}`（空 —— session 按需 load）
6. **不**主动扫描磁盘加载所有 session 的完整状态到内存（kernel 启动应该快；index 是轻量元数据，full session 只在 client 调 `session/load` 时才进内存）

### `shutdown()`

1. 对所有 `self._sessions` 里活跃 session 调 `session.orchestrator.close()`，等待清理完成
2. **不**删任何 JSONL 文件
3. 内存里 `_sessions` 清空
4. **不需要主动 fsync** —— 普通进程退出时 OS 会把 page cache 刷到磁盘；只有 OS 级灾难才会丢失最近写入，详见 [写入语义 / 不调用 fsync](#不调用-fsync)

如果 shutdown 期间某个 orchestrator close 失败，按 [Subsystem.unload 的契约](../architecture.md#三种失败处理策略)只 log 不阻塞，继续清理其他 session。

## Flags

```python
class SessionFlags(BaseModel):
    """Session subsystem flags. Runtime-immutable."""

    max_queue_length: int = Field(
        50,
        ge=1,
        le=10000,
        description="Max number of prompts that can be queued per session",
    )
    list_page_size: int = Field(
        50,
        ge=1,
        le=500,
        description="Default page size for session/list",
    )
    tool_result_inline_limit: int = Field(
        8 * 1024,
        ge=512,
        le=1024 * 1024,
        description=(
            "Tool result content above this byte size gets spilled to "
            "<session>/tool-results/<hash>.txt instead of inlined into JSONL"
        ),
    )
    enable_auto_title: bool = Field(
        True,
        description="Generate session title automatically from first turn's content",
    )
```

没有 ConfigManager section（一期没有运行期可调的会话配置）。

## Gateway Internal API

These two methods are **not** part of the `SessionHandler` ACP interface.
They exist solely for `GatewayAdapter` subclasses, which have no WebSocket
connection and cannot call the public `new` / `prompt` methods that require
`HandlerContext`. See [gateways.md](gateways.md) for full context.

### `create_for_gateway(instance_id, peer_id) -> str`

Creates a new session without binding a WS connection. Equivalent to `new()`
minus the `ctx.conn` step:

```python
async def create_for_gateway(
    self,
    instance_id: str,   # metadata label, e.g. "discord:main-discord"
    peer_id: str,       # platform user id, for metadata only
) -> str:
    # 1. uuid4 session_id
    # 2. cwd = Path.home() — gateway sessions have no project directory
    # 3. construct Session + Orchestrator
    # 4. write session_created to JSONL
    # 5. update index.json
    # 6. register in self._sessions
    # 7. ensure consumer task is running
    # 8. return session_id
    # NOTE: session.connections starts empty (no WS conn)
```

### `run_turn_for_gateway(session_id, text, on_permission) -> str`

Enqueues a turn through the **normal FIFO consumer loop** and blocks until
the turn completes, returning the assistant's full text reply.

```python
async def run_turn_for_gateway(
    self,
    session_id: str,
    text: str,
    on_permission: PermissionCallback,
) -> str:
    session = self._get_or_load_session(session_id)
    text_collector: asyncio.Future[str] = asyncio.Future()
    queued = QueuedTurn(
        request_id=_new_request_id(),
        params=PromptRequest(sessionId=session_id, content=[...]),
        queued_at=datetime.now(UTC),
        response_future=asyncio.Future(),
        text_collector=text_collector,
        on_permission=on_permission,
    )
    session.queue.append(queued)
    await queued.response_future
    return await text_collector
```

**`_run_turn_internal` changes needed:**
- If `queued.on_permission` is set, pass it to `orchestrator.query()` instead
  of the default WS permission callback.
- If `queued.text_collector` is set, accumulate `TextDelta.text` strings
  during the event loop and call `text_collector.set_result(joined)` after
  the generator ends.
- **Ordering constraint**: `text_collector.set_result()` must be called
  **before** `response_future.set_result()`. The caller in
  `run_turn_for_gateway` awaits `response_future` first and then immediately
  awaits `text_collector`; if `text_collector` is not yet resolved when
  `response_future` fires, the second await would hang.

Turn serialization, JSONL persistence, and broadcasting to any WS connections
that happen to be watching the session all happen as normal — Gateway turns
are fully visible in `session show` and concurrent WS observers.

---

## Future CLI Commands

不在协议层范围，但 SessionManager 需要的 CLI 入口点（这些命令将来由 mustang CLI 实现，调 SessionManager 提供的内部 API）：

| 命令 | 用途 |
|---|---|
| `mustang session list` | 列出所有 session（本机的 SessionManager 状态扫描）|
| `mustang session show <id>` | pretty-print 单个 session（从 JSONL 读取并渲染）|
| `mustang session export <id>` | 导出 markdown（默认）|
| `mustang session export <id> --format ipynb` | 导出 Jupyter notebook 用于分享 / commit |
| `mustang session export <id> --format html` | 导出静态网页 |
| `mustang session delete <id>` | 删除 session（删 JSONL 文件 + 清内存）|
| `mustang session vacuum` | 删除被标记为 corrupted / orphaned 的文件 |

这些命令本身的设计在 CLI 子项目里做，SessionManager 只需要暴露读写 JSONL 的纯函数 API（`load_session_metadata(id)` / `iter_events(id)` / `delete_session(id)` 之类）。

## Open Questions

设计层面只剩**一个**真正悬而未决的问题。实装阶段会遇到细节但不阻塞设计：

1. **跨多 connection 的 update fanout 如何处理慢 client** —— 一个慢的 connection 拖慢整个 session？需要 per-connection send queue + drop 策略？这和 [protocol.md 里 backpressure](../interfaces/protocol.md#流式通知的批处理batching) 是同一个问题，留给传输层 / 协议层一起解决，**Session 子系统不单独处理**

实装阶段才决定的细节：

- **Auto-title 的具体 LLM 调用**：用哪个 provider？用 fast model 还是当前 session 的 model？哪个时机触发（首 turn 完成后立即 / 异步后台 / 用户首次 `session/list` 时按需）？这是 Provider 子系统设计完之后再定
- **Sub-agent 的 `agent-<short-hash>` 命名细节**：hash 用什么作为 input（agent_id？description？随机？）？这只是个 cosmetic 决定，实装时随便选

### 已解决的问题（设计决策历史）

通过查阅 Claude Code 实际实现解决的问题：

- ~~**Replay 时怎么把历史塞回 Orchestrator**~~ —— Orchestrator 构造时接受 `initial_history` 参数，SessionManager 读 JSONL 重建 history 然后构造时一次性灌入。Orchestrator 不需要"replay 模式"概念
- ~~**Auto-title 的存储位置**~~ —— 双写：JSONL 里 append 一个 `session_info_changed` 事件作为单一真相，`index.json` 同步更新作为查询缓存。索引可以从 JSONL rebuild
- ~~**`session/list` 的 metadata 缓存**~~ —— `~/.mustang/sessions/index.json` 全局索引文件，写时增量维护，损坏时启动 rebuild。学 Claude Code 的 `sessions-index.json`
- ~~**Replay 期间 Orchestrator 是否需要"被告知"这是 replay**~~ —— 不需要。SessionManager 负责 replay 通知，Orchestrator 只在构造时收到 history。Replay 和 query 是两条独立路径
- ~~**Title / metadata 更新写回方式**~~ —— append-only `session_info_changed` 事件 + `index.json` 同步更新。两者解决不同问题：事件是真相，索引是查询性能
- ~~**Sub-agent 拆文件 vs 同文件 + `is_sidechain`**~~ —— 拆文件。学 Claude Code 的 `<session>/subagents/agent-<hash>.{jsonl,meta.json}` 结构。主 JSONL 干净，sub-agent 隔离调试
- ~~**大 tool 输出 spillover**~~ —— 学 Claude Code 的 `<session>/tool-results/<hash>.txt` 模式。超过 `tool_result_inline_limit`（默认 8 KiB）的 tool result content 写独立 .txt，主 JSONL 只存引用

## Related

- [../interfaces/protocol.md](../interfaces/protocol.md) —— SessionHandler Protocol、ACP method dispatch、事件映射表、cancel 协议、ProtocolAPI
- [../architecture.md](../architecture.md) —— 子系统加载顺序、生命周期管理、协作式取消纪律
- [../references/acp/protocol/session-setup.md](../references/acp/protocol/session-setup.md) —— ACP `session/new` / `session/load`
- [../references/acp/protocol/session-list.md](../references/acp/protocol/session-list.md) —— ACP `session/list` 和 cursor pagination
- [../references/acp/protocol/prompt-turn.md](../references/acp/protocol/prompt-turn.md) —— ACP prompt turn 流程和 cancel 语义
- [flags.md](flags.md) —— Flag vs Config 边界
- [connection_authenticator.md](connection_authenticator.md) —— ConnectionContext 定义（多连接广播用 `connection_id` 跟踪）
- _(待设计)_ orchestrator.md —— Orchestrator 子系统的内部设计


---

## Appendix: SQLite Migration Spec

# Session Storage — SQLite Migration

Status: **pending** (design complete, not yet implemented)

Replaces the dual-storage model (`*.jsonl` files + `index.json`) with a
single SQLite database (`sessions.db`).  All session events and index
metadata live in one place; SQLite transactions eliminate the consistency
problem between the two previous systems.

---

## Motivation

Current model has two independent I/O paths that must stay in sync:

```
write event  → append to *.jsonl          ─┐
update index → full rewrite of index.json  ─┘  can diverge on failure
```

Deleting a session requires deleting a file AND a JSON entry — no
atomicity guarantee.

New model collapses both into one SQLite transaction:

```
BEGIN
  INSERT INTO session_events  ← event
  UPDATE sessions             ← index update
COMMIT                        ← all-or-nothing
```

Delete becomes two statements in one transaction: remove all events for
the session, then remove the session row.

---

## File Layout

```
~/.mustang/
└── sessions/
    ├── sessions.db                        ← single SQLite file (new)
    └── <session_id>/
        └── tool-results/
            └── <hash>.txt                 ← large tool outputs (unchanged)
```

Tool-result spillover files are kept as-is — large blobs do not belong
in SQLite.

---

## ORM Models

Tables are defined as SQLAlchemy 2.0 mapped classes.  DDL is generated by
`Base.metadata.create_all()` on `SessionStore.open()`; no hand-written SQL
DDL is needed.

```python
from sqlalchemy import Index, Integer, String, Text, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationRecord(Base):
    """ORM model for the `sessions` table.

    Named `ConversationRecord` (not `Session`) to avoid confusion with
    SQLAlchemy's `AsyncSession` and the runtime `Session` dataclass.
    """
    __tablename__ = "sessions"

    session_id:               Mapped[str]      = mapped_column(String,  primary_key=True)
    cwd:                      Mapped[str]      = mapped_column(String,  nullable=False)
    title:                    Mapped[str|None] = mapped_column(String)
    created:                  Mapped[str]      = mapped_column(String,  nullable=False, default=_now_iso)
    modified:                 Mapped[str]      = mapped_column(String,  nullable=False, default=_now_iso, onupdate=_now_iso)
    total_input_tokens:       Mapped[int]      = mapped_column(Integer, default=0)
    total_output_tokens:      Mapped[int]      = mapped_column(Integer, default=0)


session_events = sa.Table(
    "session_events",
    Base.metadata,
    sa.Column("session_id", sa.String, nullable=False),
    sa.Column("timestamp",  sa.String, nullable=False),   # ISO-8601 UTC
    sa.Column("context",    sa.Text,   nullable=False),   # serialised SessionEvent JSON
    sa.Index("idx_events_session", "session_id"),
)

```

### Field notes

- `title` — set to the first 200 chars of the first user message at
  record creation; updated later via `update_title()` when a better
  AI-generated title is available.  Always non-null after creation.
- `git_branch` and `kernel_version` are intentionally **omitted**: no
  query value; both are available in `SessionCreatedEvent` if ever needed.
- `session_events` is a Core `Table` (not an ORM mapped class) — it is
  append-only and never queried by PK.  An index on `session_id` is
  sufficient.  Replay uses `ORDER BY timestamp`; same-millisecond ordering
  is guaranteed by the fact that a session runs one turn at a time.
- `created` / `modified` — `default=_now_iso` auto-fills both on INSERT.
  `onupdate=_now_iso` fires automatically on any UPDATE where `modified`
  is **not** explicitly listed in `.values()` — SQLAlchemy injects it into
  the SET clause before emitting SQL.  `TokenUsageUpdate` therefore omits
  `modified`; every update gets a fresh timestamp for free.
- `context` — stored as a JSON string.  `SessionStore` calls
  `serialize_event()` on write and `parse_event()` on read; the column
  itself is plain `Text` with no TypeDecorator magic.
- No foreign-key constraints — avoids per-insert integrity checks.
  Orphan cleanup is handled explicitly in `delete_session()`.
- WAL mode is enabled via a SQLAlchemy engine `connect` event listener
  (`PRAGMA journal_mode = WAL`) so it is set once per connection, not per
  statement.

### Delta updates with SQLAlchemy

`TokenUsageUpdate` token deltas map cleanly to SQLAlchemy column arithmetic —
no raw SQL string needed:

```python
stmt = (
    sa.update(ConversationRecord)
    .where(ConversationRecord.session_id == session_id)
    .values(
        total_input_tokens=ConversationRecord.total_input_tokens + tokens.input_tokens_delta,
        total_output_tokens=ConversationRecord.total_output_tokens + tokens.output_tokens_delta,
        # modified is set automatically via onupdate
    )
)
await db.execute(stmt)
```

---

## SessionStore API Changes

### Removed

| Old method | Reason |
|---|---|
| `create_session_file()` | No file to create |
| `append_event(path, lock, event)` | Replaced by DB insert |
| `read_events(session_id)` | Replaced by DB query |
| `write_index(entries, lock)` | Gone — index updated per-event in same transaction |
| `read_index()` | Gone — no index.json |
| `rebuild_index()` | Gone — events are in DB, no JSONL to scan |
| `jsonl_path()` | No JSONL files |
| `write_spilled()` / `read_spilled()` | Unchanged (still file-based) |

### Added

```python
async def open(self) -> None:
    """Open (or create) sessions.db, apply PRAGMA settings."""

async def close(self) -> None:
    """Close the SQLAlchemy engine."""

async def create_session_with_events(
    self,
    record: ConversationRecord,
    events: list[SessionEvent],
) -> None:
    """INSERT ConversationRecord + initial events in one transaction.

    Always called with [SessionCreatedEvent, UserMessageEvent].
    All three writes are atomic — no orphan events possible on crash.
    """

async def append_event(
    self,
    session_id: str,
    event: SessionEvent,
    tokens: TokenUsageUpdate | None = None,
) -> None:
    """Insert event + optionally update token counters in one transaction.

    None = event only, no sessions row update.
    """

async def update_title(self, session_id: str, title: str) -> None:
    """UPDATE sessions SET title = ? WHERE session_id = ?."""

async def read_events(self, session_id: str) -> list[SessionEvent]:
    """SELECT context FROM session_events WHERE session_id=?
    ORDER BY timestamp."""

async def list_sessions(self) -> list[ConversationRecord]:
    """SELECT * FROM sessions ORDER BY modified DESC.

    Objects are expunged from the session before return so callers can
    safely access attributes after the DB session closes.  Use
    expire_on_commit=False on the session factory, or call
    session.expunge_all() explicitly before closing.
    """

async def get_session(self, session_id: str) -> ConversationRecord | None:
    """SELECT one session row by primary key.  Same expunge contract as
    list_sessions()."""

async def delete_session(self, session_id: str) -> None:
    """Delete all events then the session row in one transaction."""
```

### `TokenUsageUpdate` — token delta struct

```python
@dataclass
class TokenUsageUpdate:
    """Token counters to add to the sessions row alongside an event write.

    `modified` is intentionally absent — `ConversationRecord.modified`
    carries `onupdate=_now_iso`, so SQLAlchemy sets it automatically on
    every UPDATE.
    """
    input_tokens_delta: int = 0
    output_tokens_delta: int = 0
```

Deltas avoid a read-modify-write cycle.  Only non-zero fields are
included in the UPDATE statement:

```python
values: dict = {}
if tokens.input_tokens_delta:
    values["total_input_tokens"] = ConversationRecord.total_input_tokens + tokens.input_tokens_delta
if tokens.output_tokens_delta:
    values["total_output_tokens"] = ConversationRecord.total_output_tokens + tokens.output_tokens_delta
if values:
    await db.execute(sa.update(ConversationRecord).where(...).values(**values))
```

---

## Session Dataclass Changes

Fields removed from `Session`:

```python
jsonl_path: Path          # ← removed (no JSONL file)
write_lock: asyncio.Lock  # ← removed (SQLite handles concurrency)
```

`SessionManager._index_lock` and `_index` dict also removed.

---

## Event Changes

### `TurnCompletedEvent` — add token fields

```python
class TurnCompletedEvent(_EventBase):
    type: Literal["turn_completed"] = "turn_completed"
    stop_reason: str
    duration_ms: int | None = None
    input_tokens: int = 0            # ← new
    output_tokens: int = 0           # ← new
```

These are per-turn values.  Cumulative totals live in `sessions` table.


---

## Token Flow Wiring

Currently token data is not persisted: the orchestrator receives a
`UsageChunk` from the LLM provider and passes `input_tokens` /
`output_tokens` to `ConversationHistory.update_token_count()` for
compaction threshold decisions, but `SessionManager` never sees the
values and nothing is written to disk.

Two changes are needed:

### 1. Orchestrator — stash token values, populate `TurnCompletedEvent`

`orchestrator.py` already handles `UsageChunk` in its turn loop.  Extend it
to stash the values and pass them when constructing `TurnCompletedEvent`:

```python
# inside the turn loop (already exists, extend it)
case UsageChunk() as u:
    self._history.update_token_count(u.input_tokens, u.output_tokens)
    turn_tokens = u  # stash for TurnCompletedEvent

# when yielding TurnCompletedEvent (already exists, extend it)
yield TurnCompletedEvent(
    stop_reason=stop_reason,
    duration_ms=duration_ms,
    input_tokens=turn_tokens.input_tokens if turn_tokens else 0,
    output_tokens=turn_tokens.output_tokens if turn_tokens else 0,
)
```

`UsageChunk` is emitted exactly once per turn (after the stream ends), so
there is no accumulation needed — just stash the single chunk.

### 2. SessionManager — read token fields from `TurnCompletedEvent`, build `TokenUsageUpdate`

`SessionManager` already handles `TurnCompletedEvent` in its event loop.
Extend that handler to construct the `TokenUsageUpdate`:

```python
case TurnCompletedEvent() as ev:
    usage = TokenUsageUpdate(
        input_tokens_delta=ev.input_tokens,
        output_tokens_delta=ev.output_tokens,
    )
    await self._store.append_event(session_id, ev, tokens=usage)
```

No new event types are needed.  The token data flows through the existing
`TurnCompletedEvent` — the event record in the DB carries the per-turn
breakdown, and the `sessions` row carries the cumulative totals via SQL
`UPDATE ... SET total_input_tokens = total_input_tokens + ?`.

---

## `SessionManager` Write Patterns

### New session

`SessionCreatedEvent`, `UserMessageEvent`, and the `ConversationRecord`
row are all written in a single transaction — no orphan events possible.

```python
record = ConversationRecord(
    session_id=session_id,
    cwd=cwd,
    title=text[:200],   # initial title = first message; overwritten later by AI-generated title
)
await self._store.create_session_with_events(
    record,
    [SessionCreatedEvent(...), UserMessageEvent(...)],
)
```

Sessions with no user messages yet do not appear in `list_sessions()` —
there is nothing to resume.

### Turn completed

```python
usage = TokenUsageUpdate(
    input_tokens_delta=turn_input,
    output_tokens_delta=turn_output,
)
await self._store.append_event(session_id, TurnCompletedEvent(...), tokens=usage)
# ConversationRecord.modified updated automatically via onupdate — no manual sync needed
```

### Title update

```python
await self._store.append_event(session_id, SessionInfoChangedEvent(title=new_title, ...))
await self._store.update_title(session_id, new_title)
```

### Session delete

```python
await self._store.delete_session(session_id)
self._sessions.pop(session_id, None)
# Clean up tool-result spillover files (best-effort)
shutil.rmtree(self._store.aux_dir(session_id), ignore_errors=True)
```

---

## Queued Message Ordering

The kernel supports message queuing: while turn A is in-flight, the user
can send messages B and C which are held in `Session.queue`.

**Rule: `UserMessageEvent` for a queued message is written only when that
turn actually begins execution, not when the message is enqueued.**

The queue is a purely in-memory structure.  This guarantees that the event
log always reflects logical conversation order:

```
t1  UserMessageEvent(A)
t2  [streaming events for A]
t3  TurnCompletedEvent(A)    ← LLM response to A
t4  UserMessageEvent(B)      ← written now, when turn B starts
t5  [streaming events for B]
...
```

If `UserMessageEvent(B)` were written at enqueue time it would appear
before the LLM response to A in timestamp order, making replay produce a
conversation that never happened.

---

## Sub-agent Events

Sub-agent events previously wrote to separate
`<session_id>/subagents/<agent_id>.jsonl` files.

With SQLite, sub-agent events go into the same `session_events` table.
The `agent_depth` field already in `_EventBase` distinguishes them.
`SubAgentSpawnedEvent.subagent_file` field is removed (no file).

No schema change needed — `agent_depth > 0` rows sit alongside
main-session events, ordered by `timestamp`.

---

## No In-memory Cache

`SessionManager` does **not** maintain an in-memory index cache.  All
reads (`session/list`, `session/get`) query SQLite directly via
`SessionStore`.

Local SQLite + WAL makes `SELECT * FROM sessions` fast enough for
interactive use.  Avoiding a cache eliminates the stale-read problem that
would occur when sub-agents or future multi-process kernels write to the
same DB.

---

## Dependency

Add to `src/kernel/pyproject.toml`:

```toml
dependencies = [
    ...
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",   # async driver used by SQLAlchemy for SQLite
]
```

SQLAlchemy 2.0 async engine with the `asyncio+aiosqlite` dialect.  No
C extensions beyond stdlib `_sqlite3`.

---

## Consistency Model

| Operation | Guarantee |
|---|---|
| Write event + update index | Single `async with session.begin()` block covering both the Core INSERT into `session_events` and the ORM UPDATE on `ConversationRecord` — atomic |
| Delete session + all its events | Core DELETE on `session_events` + ORM DELETE on `ConversationRecord` in one `session.begin()` block — atomic |
| Kernel crash mid-write | WAL rollback — DB stays in last committed state |
| Concurrent sub-agent writes | WAL serialises writers — no corruption |

The previous dual-write consistency problem (JSONL vs index.json) is
eliminated by design.

---

## What Does NOT Change

- `SessionEvent` union type and all event classes (except `TurnCompletedEvent`)
- `parse_event()` / `serialize_event()` helpers (still used for `context` column)
- Tool-result spillover files (`tool-results/<hash>.txt`)
- Session public API from the perspective of upper layers
  (`SessionManager` hides the store completely)

**Removed**: `IndexEntry` Pydantic model and `SessionManager._index` cache —
replaced by direct DB queries via `ConversationRecord`.

**`read_events()` behaviour change**: previously only returned main-session
events (sub-agent events lived in separate JSONL files).  After migration,
`read_events(session_id)` returns **all** events for the session including
sub-agent events, ordered by `timestamp`.  Callers that need to isolate
one agent's events filter on `agent_depth` or `agent_id` in the parsed
event.

---

## Implementation Order

```
1. Add sqlalchemy[asyncio] + aiosqlite to pyproject.toml
2. Define ConversationRecord ORM model + session_events Core Table; add TokenUsageUpdate dataclass
3. Rewrite SessionStore (open/close/create_session_with_events/append_event/
   update_title/read_events/list_sessions/get_session/delete_session)
4. Extend TurnCompletedEvent with token fields
5. Update SessionManager:
   - remove jsonl_path/write_lock/_index_lock/_index
   - replace IndexEntry usage with ConversationRecord
   - update all append_event call sites with appropriate TokenUsageUpdate
6. Remove IndexEntry Pydantic model
7. Remove SubAgentSpawnedEvent.subagent_file field
8. Update tests
```

Estimated effort: **1.5–2 days**.  Can be a single PR independent of
CommandManager work.

> **Note**: Existing session data (legacy JSONL files) is not migrated.
> New installs start fresh with `sessions.db`.  Old data is ignored and
> can be deleted manually.
