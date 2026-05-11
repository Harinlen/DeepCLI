# Context Compression Layers (1a–1d) — Design

> **Quick header**
> - **Role**: context compaction design for the Mustang orchestrator.
> - **Current code**: `kernel.agents.mustang.orchestrator.compact.*`, `history/*`, `tool_executor.py`.
> - **Owns**: snip, microcompact, LLM summary/autocompact, and deferred context-collapse shape.
> - **Boundary**: prompt assembly and memory retrieval stay in their owning docs.

Status: **landed** — 1a (tool-result budget), 1b (snip), 1c (microcompact)
已实装并集成到 STEP 1 调用链。1d (context collapse) 仍为 deferred。

---

## 动机

Orchestrator STEP 1 (PREPARE) 设计了 5 层 context 压缩，按成本从低到高
排列（见 `docs/kernel/subsystems/orchestrator.md` § STEP 1）：

```
1a  tool-result budget   ← O(1)，无 LLM 调用
1b  snip                 ← O(1)，无 LLM 调用
1c  microcompact         ← O(n) 扫描，无 LLM 调用
1d  context collapse     ← O(1) 投影，无 LLM 调用（读预计算结果）
1e  autocompact          ← 1 次 LLM 调用（昂贵）
```

1a–1c 已实装，1d deferred（flag-gated，代码未写）。
1e（`Compactor.compact()`）在设计前已存在。

**参照**：Claude Code `query.ts:369–453` 的 5 层顺序。

---

## 目标

1. 实现 1a（tool-result budget）、1b（snip）、1c（microcompact）
2. 1d（context collapse）写清设计但 flag-gate，一期不实现
3. 在 STEP 1 中按 1a → 1b → 1c → 1d → 1e 顺序调用
4. 每层独立可测，互不依赖（除执行顺序外）

## 非目标

- 不改 1e autocompact 的现有实现
- 不实现聚合预算（单轮所有 tool_result 总和上限）
- 不改 ACP wire format
- 1d 一期不实现代码（仅留 design + flag gate）

---

## 1a. Tool-Result Budget

### 归属

`ToolExecutor`（截断发生在 tool 执行后、写入 history 前）。

### 设计

每个 `Tool` 声明自己的结果大小上限。`ToolExecutor` 在 `_execute_one`
返回结果时检查并截断。

**新增 Tool 类属性**：

```python
# kernel/agents/mustang/tools/tool.py
class Tool(ABC):
    max_result_size_chars: ClassVar[int] = 50_000
```

**截断逻辑**（在 `ToolExecutor._execute_one` 中）：

```python
# After tool.call() returns result:
budget = tool.max_result_size_chars
if isinstance(result_text, str) and len(result_text) > budget:
    original_size = len(result_text)
    result_text = (
        result_text[:budget]
        + f"\n\n[tool result truncated — {original_size} chars, "
        f"kept first {budget} chars]"
    )
```

**常量**：

| 名称 | 值 | 说明 |
|------|-----|------|
| `DEFAULT_MAX_RESULT_SIZE_CHARS` | 50,000 | Tool 默认值 |
| `BASH_MAX_RESULT_SIZE_CHARS` | 50,000 | Bash 工具 |
| `FILE_READ_MAX_RESULT_SIZE_CHARS` | 100,000 | FileRead 可以更大 |
| `GREP_MAX_RESULT_SIZE_CHARS` | 50,000 | Grep 工具 |

各 built-in tool 可以 override `max_result_size_chars` 到合适的值。

**不做**：
- 磁盘持久化（CC 把溢出写到 `<sessionDir>/tool-results/`，我们直接截断）
- 聚合预算（CC 的 `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200K`）

---

## 1b. Snip

### 归属

`Compactor`（新增 `snip()` 方法）。

### 设计

遍历 history 中非尾部的 `UserMessage`，对其中的 `ToolResultContent`，
如果对应的 tool 是 read-only，将 content 替换为 placeholder。保留
`ToolUseContent` 块（LLM 仍然知道调了什么工具）。

**read-only 判定**：需要知道 `tool_use_id` 对应的 `ToolKind`。

**方案**：在 `ConversationHistory` 上维护一个 `tool_use_id → ToolKind` 映射。
`ToolExecutor` 执行工具时写入映射，`Compactor.snip()` 读取映射。

```python
# kernel/agents/mustang/orchestrator/history.py
class ConversationHistory:
    def __init__(self, ...):
        ...
        self._tool_kinds: dict[str, ToolKind] = {}

    def record_tool_kind(self, tool_use_id: str, kind: ToolKind) -> None:
        self._tool_kinds[tool_use_id] = kind

    def tool_kind_for(self, tool_use_id: str) -> ToolKind | None:
        return self._tool_kinds.get(tool_use_id)
```

**Snip 算法**：

```python
# kernel/agents/mustang/orchestrator/compactor.py
def snip(self, history: ConversationHistory) -> int:
    """Replace read-only tool results in non-tail messages with placeholders.
    Returns chars freed."""
    boundary = history.find_compaction_boundary(self._keep_recent)
    freed = 0
    for msg in history.messages[:boundary]:
        if not isinstance(msg, UserMessage):
            continue
        for i, block in enumerate(msg.content):
            if not isinstance(block, ToolResultContent):
                continue
            kind = history.tool_kind_for(block.tool_use_id)
            if kind is not None and kind.is_read_only:
                old_size = _content_size(block.content)
                msg.content[i] = ToolResultContent(
                    tool_use_id=block.tool_use_id,
                    content=f"[result snipped — {old_size} chars]",
                    is_error=block.is_error,
                )
                freed += old_size
    if freed > 0:
        history._token_count -= freed // 4  # rough estimate
    return freed
```

**Protected tail**：复用 `find_compaction_boundary(keep_recent_turns)`，
只处理 boundary 之前的消息。

**Placeholder 格式**：`[result snipped — {n} chars]`

---

## 1c. Microcompact

### 归属

`Compactor`（新增 `microcompact()` 方法）。

### 设计

在 snip 之后，如果 token_count 仍高于阈值，进一步删除非尾部的整个
read-only assistant + tool_result 对。

**识别 read-only 对**：`AssistantMessage` 的 content 中只有
`ToolUseContent`（没有 `TextContent`），且所有 tool_use 的 kind 都是
`is_read_only`。

**算法**：

```python
def microcompact(self, history: ConversationHistory) -> int:
    """Remove entire read-only assistant+tool_result pairs from non-tail.
    Returns number of message pairs removed."""
    boundary = history.find_compaction_boundary(self._keep_recent)
    removed = 0
    # Scan backwards from boundary to find removable pairs.
    # An assistant msg at index i followed by a user msg at i+1
    # (tool_results) forms a pair.
    indices_to_remove: list[int] = []
    i = 0
    while i < boundary - 1:
        msg = history.messages[i]
        if isinstance(msg, AssistantMessage) and _is_read_only_assistant(msg, history):
            next_msg = history.messages[i + 1]
            if isinstance(next_msg, UserMessage) and _is_tool_result_only(next_msg):
                indices_to_remove.extend([i, i + 1])
                i += 2
                continue
        i += 1

    if not indices_to_remove:
        return 0

    # Replace removed pairs with a single marker.
    n_pairs = len(indices_to_remove) // 2
    marker = UserMessage(content=[
        TextContent(text=f"[{n_pairs} read-only tool calls removed]")
    ])
    # Remove indices in reverse order, insert marker at first position.
    kept = [m for j, m in enumerate(history.messages[:boundary])
            if j not in set(indices_to_remove)]
    kept.insert(0 if not kept else indices_to_remove[0], marker)  # simplified
    history._messages = kept + history.messages[boundary:]
    history._token_count = history._estimate_tokens_for(history._messages)
    return n_pairs
```

**Marker 格式**：`[{n} read-only tool calls removed]`

---

## 1d. Context Collapse（deferred）

### 归属

`Compactor` + `FlagManager` + `SessionManager`（持久化）。

### 设计概要

维护一个 commit-log 风格的 landmark 日志。每当对话增长到一定长度时，
记录一个 landmark（boundary index + 本段摘要）。在 STEP 1 时，通过
"读时投影" 把旧消息替换为预计算的 landmark 摘要，无需实时 LLM 调用。

**关键数据结构**：

```python
@dataclass
class CollapseLandmark:
    boundary_index: int       # 原始 messages 中的 boundary 位置
    summary: str              # 该段的摘要文本
    created_at: float         # 创建时间戳
```

**持久化**：需要 session-level store（可以用 SQLite 的 session 附属表，
或 JSONL 文件）。

**Feature flag**：`CONTEXT_COLLAPSE`（默认 off），一期不实现代码。

**实现时机**：在 1a–1c 上线后，根据实际 autocompact 触发频率决定是否
需要 1d。如果 1a–1c 已经足够减少 autocompact，1d 可以继续 defer。

---

## STEP 1 调用顺序

在 `StandardOrchestrator._run_query` 的 STEP 1 区域：

```python
# ── STEP 1: PREPARE ─────────────────────────────────────
# 1a. tool-result budget — already enforced at ToolExecutor level,
#     no action needed here (截断在 STEP 6 执行时已生效).

# 1b. snip
self._compactor.snip(self._history)

# 1c. microcompact
threshold = self._compaction_threshold()
if self._history.token_count > threshold:
    self._compactor.microcompact(self._history)

# 1d. context collapse — TODO (feature-flagged, deferred)

# 1e. autocompact (proactive)
if self._history.token_count > threshold:
    before = self._history.token_count
    await self._compactor.compact(self._history)
    ...
```

**注意**：1a 在 STEP 6 的 `ToolExecutor._execute_one` 中已经生效，
不需要在 STEP 1 中再跑。1b 每轮都跑（O(1) 成本可忽略）。
1c 只在 snip 后仍超阈值时触发。

---

## 实现顺序

| PR | 内容 | 估时 | 依赖 |
|----|------|------|------|
| **PR 1** | 1a：`Tool.max_result_size_chars` + `ToolExecutor` 截断逻辑 + 测试 | 1d | 无 |
| **PR 2** | `ConversationHistory.tool_kinds` 映射 + `ToolExecutor` 写映射 | 0.5d | 无（可与 PR 1 并行） |
| **PR 3** | 1b：`Compactor.snip()` + Orchestrator 调用 + 测试 | 1d | PR 2 |
| **PR 4** | 1c：`Compactor.microcompact()` + Orchestrator 调用 + 测试 | 1d | PR 2 |
| **PR 5** | 1d：design doc 完善 + flag gate 占位（不写实现） | 0.5d | — |

---

## 涉及文件

| 文件 | 改动 |
|------|------|
| `kernel/agents/mustang/tools/tool.py` | 新增 `max_result_size_chars: ClassVar[int]` |
| `kernel/agents/mustang/tools/builtin/bash.py` | Override `max_result_size_chars` |
| `kernel/agents/mustang/tools/builtin/file_read.py` | Override `max_result_size_chars` |
| `kernel/agents/mustang/orchestrator/tool_executor.py` | 结果截断（1a）+ `history.record_tool_kind()` 调用 |
| `kernel/agents/mustang/orchestrator/compactor.py` | 新增 `snip()` + `microcompact()` |
| `kernel/agents/mustang/orchestrator/history.py` | 新增 `_tool_kinds` 映射 + 辅助方法 |
| `kernel/agents/mustang/orchestrator/orchestrator.py` | STEP 1 调用 snip → microcompact → compact |
| `tests/kernel/orchestrator/test_compactor.py` | snip / microcompact 单元测试 |
| `tests/kernel/orchestrator/test_tool_executor.py` | 截断测试 |

---

## 设计决议

| # | 问题 | 决议 | 理由 |
|---|------|------|------|
| D1 | 1a 溢出持久化还是截断 | **直接截断** | CC 持久化到磁盘是为了 resume 时恢复，DeepCLI 目前无需此复杂度 |
| D2 | read-only 判定方式 | **`ToolKind.is_read_only`** | 已有 `is_read_only` 属性（read/search/fetch/think），不引入新分类 |
| D3 | tool_use_id → kind 映射存在哪 | **`ConversationHistory`** | History 是 per-session 内存对象，生命周期匹配；不需要持久化 |
| D4 | snip 每轮都跑还是阈值触发 | **每轮都跑** | O(1) 成本，提前释放空间，减少后续层的压力 |
| D5 | microcompact 的 marker 放在哪 | **替换到原位** | 保持 messages 列表的逻辑顺序，避免 boundary 计算偏移 |
| D6 | 1d 一期是否实现 | **不实现** | 需要持久化 + SessionManager 配合，等 1a–1c 上线后再评估必要性 |
