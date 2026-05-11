# TaskManager + AgentTool 统一设计

> **Quick header**
> - **Role**: in-memory task registry, AgentTool background runs, task output/stop tools, and TodoWrite state.
> - **Current code**: `kernel.agents.mustang.tasks.*`, `tools/builtin/agent.py`, `task_output.py`, `task_stop.py`, `todo_write.py`.
> - **Runtime owner**: Mustang runtime tools/orchestrator path.
> - **Boundary**: task state and notifications only; durable session event storage lives in `sessions/`.

Status: **landed** — 参考 Claude Code main 源码设计，当前实现已迁入 `kernel.agents.mustang.tasks`.

> 蓝图来源：
> - Claude Code `src/Task.ts`, `src/tasks.ts`, `src/utils/tasks.ts`
> - Claude Code `src/tasks/LocalShellTask/`, `src/tasks/LocalAgentTask/`
> - Claude Code `src/tools/AgentTool/`, `src/tools/TaskOutputTool/`, `src/tools/TaskStopTool/`
> - Claude Code `src/utils/task/framework.ts`, `src/utils/task/TaskOutput.ts`, `src/utils/task/diskOutput.ts`
> - DeepCLI `src/kernel/kernel/agents/mustang/orchestrator/orchestrator.py` (query loop task-notification drain)
> - DeepCLI `src/kernel/kernel/agents/mustang/tools/context.py` (`ToolContext.tasks` integration)
> - DeepCLI `docs/kernel/subsystems/orchestrator.md` (Sub-agent 章节)

---

## 0. 动机

coverage doc 里 TaskManager 和 AgentTool 分别标为 ❌ 缺失：

```
| TaskManager / 后台任务 | ❌ 缺失 | 无 TodoWriteTool、无后台 ShellTask 管理、无 run_in_background |
| AgentTool              | ❌ 缺失 | 无法生成子代理；事件框架已就位但无工具类                     |
```

在 Claude Code 里，这两个不是独立系统——**AgentTool 是 task framework
的一等消费者**。`local_agent` 和 `local_bash` 共享同一套 register →
notify → evict 生命周期、同一个 `TaskOutputTool`、同一套 notification
管道。拆成两个独立设计会导致数据模型不兼容、通知管道重复、后期返工。

本文档统一设计：

1. **Task framework** — 数据模型、生命周期、输出收集、通知、GC
2. **BashTool `run_in_background`** — framework 的第一个消费者
3. **AgentTool** — framework 的第二个消费者 + 子 Orchestrator 管理
4. **LLM 交互工具** — TaskOutputTool、TaskStopTool
5. **TodoWriteTool** — 独立的计划条目系统（与 task framework 无关）

### 与 tool 并发的关系

**无关。** Tool 并发（`partition_tool_calls` + 批内 `asyncio.gather`）是
同一轮内的同步并发；task 后台是跨轮次的异步脱耦。BashTool 的
`run_in_background=true` 分支仍参与当前轮的并发分批，只是它的 `call()`
内部选择"spawn task + 立即 return task_id"。

---

## 1. Task Framework

### 1.1 数据模型

```python
# kernel/agents/mustang/tasks/types.py

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class TaskType(str, enum.Enum):
    """All supported task types."""
    local_bash = "local_bash"
    local_agent = "local_agent"


class TaskStatus(str, enum.Enum):
    """Unified lifecycle states for all task types."""
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    killed = "killed"

    @property
    def is_terminal(self) -> bool:
        return self in {TaskStatus.completed, TaskStatus.failed, TaskStatus.killed}


@dataclass
class TaskStateBase:
    """Fields shared by all task types."""
    id: str
    type: TaskType
    status: TaskStatus
    description: str
    tool_use_id: str | None = None    # 关联的 tool_use_id（LLM 发起调用的 id）
    owner_agent_id: str | None = None # 发起此 task 的 agent_id（None = 根 agent）
    start_time: float = 0.0           # time.time()
    end_time: float | None = None
    output_file: str = ""             # TaskOutput 文件路径
    output_offset: int = 0            # 已读取到的字节偏移量
    notified: bool = False            # 是否已向 LLM 推送完成通知


@dataclass
class ShellTaskState(TaskStateBase):
    """后台 shell 命令状态。"""
    type: TaskType = field(default=TaskType.local_bash, init=False)
    command: str = ""
    exit_code: int | None = None
    # asyncio.subprocess.Process — running 时持有引用, completed 后置 None
    process: Any = field(default=None, repr=False)


@dataclass
class AgentTaskState(TaskStateBase):
    """后台子 agent 状态。"""
    type: TaskType = field(default=TaskType.local_agent, init=False)
    agent_id: str = ""
    agent_type: str = ""               # "Explore", "general-purpose", ...
    prompt: str = ""
    model: str | None = None
    result: str | None = None          # 最终 text 回复
    error: str | None = None
    progress: AgentProgress | None = None
    is_backgrounded: bool = False      # False=前台同步, True=已后台化
    pending_messages: list[str] = field(default_factory=list)  # SendMessage 排队
    # 取消控制 — running 时持有, completed 后置 None
    cancel_event: Any = field(default=None, repr=False)


@dataclass
class AgentProgress:
    """子 agent 实时进度快照。"""
    tool_use_count: int = 0
    token_count: int = 0
    last_activity: str | None = None   # 最近一次 tool 的描述


# Union type for runtime dispatch
TaskState = ShellTaskState | AgentTaskState
```

**对齐 Claude Code 的关键字段**：

| CC `TaskStateBase` | DeepCLI `TaskStateBase` | 说明 |
|---|---|---|
| `id` (带类型前缀) | `id` | 同：`b` + random 8 chars (bash), `a` + ... (agent) |
| `type: TaskType` | `type: TaskType` | 同 |
| `status: TaskStatus` | `status: TaskStatus` | 同 5 种 |
| `description` | `description` | 同 |
| `toolUseId` | `tool_use_id` | 同 |
| `outputFile` | `output_file` | 同 |
| `outputOffset` | `output_offset` | 同 |
| `notified` | `notified` | 同 |

**不引入的 CC 字段**：
- `totalPausedMs` — DeepCLI 不支持暂停/恢复
- `retain` / `evictAfter` / `diskLoaded` / `messages` — 这些是 CC 的
  前端 panel 显示需求，DeepCLI 的 ACP 客户端自行管理显示

### 1.2 ID 生成

```python
# kernel/agents/mustang/tasks/id.py

import secrets

_PREFIXES = {
    TaskType.local_bash:  "b",
    TaskType.local_agent: "a",
}

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def generate_task_id(task_type: TaskType) -> str:
    prefix = _PREFIXES.get(task_type, "x")
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return prefix + suffix
```

对齐 CC `Task.ts:98-106`：类型前缀 + 8 位随机字符。

### 1.3 TaskRegistry

```python
# kernel/agents/mustang/tasks/registry.py

from __future__ import annotations

import asyncio
import time
from typing import Callable

from kernel.agents.mustang.tasks.types import TaskState, TaskStatus


class TaskRegistry:
    """Session 级后台任务注册表。

    存储在 Orchestrator 的 ToolContext.tasks 里，每个 session 一个实例。
    纯内存——task 生命周期不超过 session，不做磁盘持久化。

    对齐 CC 的 AppState.tasks dict，但独立封装为类（CC 直接用 dict +
    散布的工具函数，不够内聚）。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._listeners: list[Callable[[], None]] = []
        # (task_id, owner_agent_id) — agent_id=None 表示根 agent
        self._notification_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

    # -- 注册 / 更新 / 查询 -----------------------------------------

    def register(self, task: TaskState) -> None:
        """注册新 task，status 应为 running。
        task.owner_agent_id 应由调用方预先设置（从 ToolContext.agent_id 获取）。
        """
        task.start_time = time.time()
        self._tasks[task.id] = task
        self._notify_listeners()

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def get_all(self) -> list[TaskState]:
        return list(self._tasks.values())

    def get_running(self) -> list[TaskState]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.running]

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        exit_code: int | None = None,
        result: str | None = None,
        error: str | None = None,
    ) -> TaskState | None:
        """更新 task 状态。terminal 状态自动设置 end_time。"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = status
        if status.is_terminal:
            task.end_time = time.time()
        # 类型特化字段
        if isinstance(task, ShellTaskState) and exit_code is not None:
            task.exit_code = exit_code
        if isinstance(task, AgentTaskState):
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
        self._notify_listeners()
        return task

    # -- 完成通知管道 -----------------------------------------------

    def enqueue_notification(self, task_id: str) -> None:
        """把 task_id 推入 notification 队列，Orchestrator 下一轮 drain。"""
        task = self._tasks.get(task_id)
        if task is None or task.notified:
            return
        task.notified = True
        self._notification_queue.put_nowait((task_id, task.owner_agent_id))

    def drain_notifications(self, *, agent_id: str | None = None) -> list[str]:
        """非阻塞地取出属于 agent_id 的待推送 task_id。

        agent_id=None 表示根 agent。不匹配的通知留在队列里，
        等对应的 Orchestrator drain。

        对齐 CC query.ts:1570 — 主线程只 drain agentId===undefined，
        子 agent 只 drain addressed-to-me 的通知。
        """
        matched: list[str] = []
        requeue: list[tuple[str, str | None]] = []
        while not self._notification_queue.empty():
            try:
                item = self._notification_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            tid, owner = item
            if owner == agent_id:
                matched.append(tid)
            else:
                requeue.append(item)
        # 不匹配的放回队列
        for item in requeue:
            self._notification_queue.put_nowait(item)
        return matched

    # -- GC ---------------------------------------------------------

    def evict_terminal(self) -> list[str]:
        """清除所有已 notified 的 terminal task，返回被清除的 id。"""
        evicted: list[str] = []
        for task_id, task in list(self._tasks.items()):
            if task.status.is_terminal and task.notified:
                del self._tasks[task_id]
                evicted.append(task_id)
        if evicted:
            self._notify_listeners()
        return evicted

    # -- 观察者 (UI 刷新等) -----------------------------------------

    def on_change(self, listener: Callable[[], None]) -> Callable[[], None]:
        """注册变更监听器，返回 unsubscribe 函数。"""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify_listeners(self) -> None:
        for fn in self._listeners:
            try:
                fn()
            except Exception:
                pass
```

**与 CC `AppState.tasks` 的区别**：

| 维度 | CC | DeepCLI |
|------|-----|---------|
| 存储 | `AppState.tasks` plain dict + 散布工具函数 | `TaskRegistry` 封装类 |
| 通知 | `enqueuePendingNotification()` 全局函数 | `TaskRegistry.notification_queue` 实例队列 |
| GC | `generateTaskAttachments` + `evictTerminalTask` | `evict_terminal()` 单一方法 |
| 多 session | 同一进程只有一个 AppState | 每个 session 一个 `TaskRegistry` 实例 |

### 1.4 TaskOutput — 输出收集

```python
# kernel/agents/mustang/tasks/output.py

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


def get_task_output_dir(session_id: str) -> Path:
    """Per-session task output 目录。"""
    base = Path(tempfile.gettempdir()) / "mustang" / session_id / "tasks"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_task_output_path(session_id: str, task_id: str) -> str:
    return str(get_task_output_dir(session_id) / f"{task_id}.output")


class TaskOutput:
    """Task 输出管理。

    **File mode**（bash）：stdout/stderr 直写文件（subprocess stdio fd
    直接指向文件），不经过 Python。进度通过 poll 文件 tail 获取。

    对齐 CC `TaskOutput.ts`。DeepCLI 目前只需要 file mode（bash）。
    Pipe mode（hooks 场景）未来按需添加。
    """

    def __init__(self, session_id: str, task_id: str) -> None:
        self.session_id = session_id
        self.task_id = task_id
        self.path = get_task_output_path(session_id, task_id)
        self._total_bytes: int = 0

    async def init_file(self) -> str:
        """创建空输出文件，返回路径。"""
        # O_CREAT | O_EXCL — 安全：不跟踪 symlink
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        return self.path

    async def read_all(self, max_bytes: int = 8 * 1024 * 1024) -> str:
        """读取全部输出（不超过 max_bytes）。"""
        try:
            return await asyncio.to_thread(self._read_sync, max_bytes)
        except FileNotFoundError:
            return ""

    def _read_sync(self, max_bytes: int) -> str:
        with open(self.path, "r", errors="replace") as f:
            return f.read(max_bytes)

    async def read_tail(self, max_bytes: int = 8 * 1024 * 1024) -> str:
        """读取输出尾部（大文件只读末尾 max_bytes）。"""
        try:
            size = os.path.getsize(self.path)
            if size <= max_bytes:
                return await self.read_all(max_bytes)
            offset = size - max_bytes
            data = await asyncio.to_thread(self._read_range_sync, offset, max_bytes)
            skipped_kb = offset // 1024
            return f"[{skipped_kb}KB of earlier output omitted]\n{data}"
        except FileNotFoundError:
            return ""

    def _read_range_sync(self, offset: int, length: int) -> str:
        with open(self.path, "r", errors="replace") as f:
            f.seek(offset)
            return f.read(length)

    async def read_delta(self, from_offset: int, max_bytes: int = 8 * 1024 * 1024) -> tuple[str, int]:
        """增量读取。返回 (content, new_offset)。"""
        try:
            data = await asyncio.to_thread(self._read_range_sync, from_offset, max_bytes)
            return data, from_offset + len(data.encode("utf-8"))
        except FileNotFoundError:
            return "", from_offset

    async def cleanup(self) -> None:
        """删除输出文件。"""
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
```

**与 CC 的差异**：
- CC 的 `TaskOutput` 有 file mode + pipe mode + 内存 buffer +
  CircularBuffer + polling poller + disk spillover。
- DeepCLI 只实现 **file mode**——bash 的 stdout/stderr 直写文件，读取时
  用 `read_all` / `read_tail` / `read_delta`。
- Pipe mode 和 progress polling 在有前端需求时按需添加。

### 1.5 Notification 管道 — 接入 Orchestrator

核心问题：后台 task 完成时，通知怎么到达 LLM？

**方案**：复用现有 `pending_reminders` 通道 + 新增 `drain_task_notifications`。

```
task 完成
  → TaskRegistry.enqueue_notification(task_id)
  → Orchestrator step 6d drain_task_notifications()
  → 格式化为 <task-notification> XML
  → 追加到下一轮 user message（作为 system-reminder 注入）
```

#### 1.5.1 OrchestratorDeps 新增字段

```python
# kernel/agents/mustang/orchestrator/types.py — OrchestratorDeps 新增

    task_registry: TaskRegistry | None = field(default=None)
    """TaskRegistry | None — 后台 task 注册表。当 BashTool
    run_in_background 或 AgentTool 后台化时使用。None 时后台 task
    功能不可用。"""
```

#### 1.5.2 Orchestrator 注入点

在 `orchestrator.py` 的 task-notification drain 阶段实现：

```python
# orchestrator.py — task-notification drain

# 6d. Drain task notifications（只取属于当前 agent 的）
if self._deps.task_registry is not None:
    completed_ids = self._deps.task_registry.drain_notifications(
        agent_id=self._agent_id  # None=根 agent, str=子 agent
    )
    for task_id in completed_ids:
        task = self._deps.task_registry.get(task_id)
        if task is not None:
            notification = _format_task_notification(task)
            # 推入 pending_reminders，下一轮 step 0 注入 LLM
            if self._deps.queue_reminders is not None:
                self._deps.queue_reminders([notification])

    # GC: 清除已通知的 terminal tasks
    self._deps.task_registry.evict_terminal()
```

注意：`self._agent_id` 是 Orchestrator 的 agent 身份标识。根
Orchestrator 的 `_agent_id` 为 `None`；子 Orchestrator 在构造时由
`AgentTool` 设置。这确保每个 Orchestrator 只 drain 自己发起的 task
通知，不会抢走其他 agent 的通知。

**边界情况**：如果子 agent 已结束但它发起的后台 task 还在跑，task
完成时通知带 `owner_agent_id=子agent_id`，但没有对应的 Orchestrator
来 drain 它了。处理方式：根 Orchestrator 在 `SubAgentEnd` 后执行一次
**orphan drain**——把已终止子 agent 的通知收归自己：

```python
# orchestrator.py — SubAgentEnd 后

def _drain_orphan_notifications(self, ended_agent_id: str) -> None:
    """子 agent 结束后，接管其未 drain 的 task 通知。"""
    if self._deps.task_registry is None:
        return
    orphans = self._deps.task_registry.drain_notifications(
        agent_id=ended_agent_id
    )
    for task_id in orphans:
        task = self._deps.task_registry.get(task_id)
        if task is not None:
            notification = _format_task_notification(task)
            if self._deps.queue_reminders is not None:
                self._deps.queue_reminders([notification])
```

#### 1.5.3 通知格式

```python
def _format_task_notification(task: TaskState) -> str:
    """生成 task 完成通知 XML，对齐 CC enqueueShellNotification 格式。"""
    status = task.status.value  # "completed" | "failed" | "killed"

    if isinstance(task, ShellTaskState):
        if status == "completed":
            summary = f'Background command "{task.description}" completed'
            if task.exit_code is not None:
                summary += f" (exit code {task.exit_code})"
        elif status == "failed":
            summary = f'Background command "{task.description}" failed'
            if task.exit_code is not None:
                summary += f" with exit code {task.exit_code}"
        else:
            summary = f'Background command "{task.description}" was stopped'
    elif isinstance(task, AgentTaskState):
        if status == "completed":
            summary = f'Agent "{task.description}" completed'
        elif status == "failed":
            summary = f'Agent "{task.description}" failed: {task.error or "Unknown error"}'
        else:
            summary = f'Agent "{task.description}" was stopped'
    else:
        summary = f'Task "{task.description}" {status}'

    tool_use_line = (
        f"\n<tool-use-id>{task.tool_use_id}</tool-use-id>"
        if task.tool_use_id
        else ""
    )
    result_section = ""
    if isinstance(task, AgentTaskState) and task.result:
        result_section = f"\n<result>{task.result}</result>"

    return (
        f"<task-notification>\n"
        f"<task-id>{task.id}</task-id>{tool_use_line}\n"
        f"<output-file>{task.output_file}</output-file>\n"
        f"<status>{status}</status>\n"
        f"<summary>{summary}</summary>{result_section}\n"
        f"</task-notification>"
    )
```

### 1.6 ToolContext.tasks 接入

将 `ToolContext.tasks: Any = None` 替换为强类型：

```python
# kernel/agents/mustang/tools/context.py — 修改

from kernel.agents.mustang.tasks.registry import TaskRegistry

@dataclass
class ToolContext:
    ...
    tasks: TaskRegistry | None = None
    """Session ``TaskRegistry`` — BashTool ``run_in_background`` 和
    AgentTool 后台化时使用。``None`` 时后台 task 功能不可用。"""
```

SessionManager 在构建 `OrchestratorDeps` 时创建 `TaskRegistry` 实例，
Orchestrator 在构建 `ToolContext` 时传入同一个引用。

### 1.7 Session 结束时的清理

Session 结束时（disconnect、`/clear`、进程退出），必须清理所有
running tasks，否则 orphan 进程会泄漏。

```python
# kernel/agents/mustang/tasks/registry.py — TaskRegistry 新增

async def shutdown(self) -> None:
    """Kill 所有 running tasks 并清理 output 文件。

    SessionManager 在 session 结束时调用。
    """
    for task in list(self._tasks.values()):
        if task.status != TaskStatus.running:
            continue
        # Kill shell process
        if isinstance(task, ShellTaskState) and task.process is not None:
            try:
                task.process.kill()
                await task.process.wait()
            except ProcessLookupError:
                pass
            task.process = None
        # Cancel agent
        if isinstance(task, AgentTaskState) and task.cancel_event is not None:
            task.cancel_event.set()
        task.status = TaskStatus.killed
        task.end_time = time.time()

    # 清理 output 文件
    for task in self._tasks.values():
        if task.output_file:
            try:
                os.unlink(task.output_file)
            except FileNotFoundError:
                pass

    self._tasks.clear()
```

SessionManager 在 session teardown 时调用：

```python
# session/__init__.py — session 结束路径
if deps.task_registry is not None:
    await deps.task_registry.shutdown()
```

---

## 2. BashTool `run_in_background`

### 2.1 Input Schema 扩展

```python
class BashTool(Tool[dict[str, Any], str]):
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Kill the command after this many ms. Default 120000.",
            },
            "description": {
                "type": "string",
                "description": "Clear, concise description of what this command does.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this command in the background.",
            },
        },
        "required": ["command"],
    }
```

### 2.2 call() 后台分支

```python
async def call(
    self,
    input: dict[str, Any],
    ctx: ToolContext,
) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
    command = input["command"]
    timeout_ms = int(input.get("timeout_ms") or 120_000)
    run_in_background = bool(input.get("run_in_background", False))
    description = input.get("description") or command[:80]

    if run_in_background and ctx.tasks is not None:
        yield await self._spawn_background(command, description, timeout_ms, ctx)
        return

    # ... 原有前台同步逻辑不变 ...


async def _spawn_background(
    self,
    command: str,
    description: str,
    timeout_ms: int,
    ctx: ToolContext,
) -> ToolCallResult:
    """Spawn 后台 shell task，立即返回 task_id。"""
    from kernel.agents.mustang.tasks.types import ShellTaskState, TaskStatus, generate_task_id, TaskType
    from kernel.agents.mustang.tasks.output import TaskOutput

    task_id = generate_task_id(TaskType.local_bash)
    output = TaskOutput(ctx.session_id, task_id)
    output_path = await output.init_file()

    # 打开文件 fd 供 subprocess 直写（不经过 Python）
    fd = os.open(output_path, os.O_WRONLY | os.O_APPEND)

    process = await asyncio.create_subprocess_shell(
        command,
        stdout=fd,
        stderr=fd,
        cwd=str(ctx.cwd),
        env={**ctx.env} if ctx.env else None,
    )
    os.close(fd)  # 子进程已继承 fd，父进程关闭

    # 注册 task
    task = ShellTaskState(
        id=task_id,
        status=TaskStatus.running,
        description=description,
        tool_use_id=None,  # 由 ToolExecutor 外部设置
        owner_agent_id=ctx.agent_id,  # None=根 agent, str=子 agent
        command=command,
        output_file=output_path,
        process=process,
    )
    ctx.tasks.register(task)

    # 后台等待完成 + 通知
    asyncio.create_task(
        self._wait_and_notify(task_id, process, timeout_ms, ctx.tasks)
    )

    body = (
        f"Command running in background with ID: {task_id}. "
        f"Output is being written to: {output_path}"
    )
    return ToolCallResult(
        data={"task_id": task_id, "status": "running"},
        llm_content=[TextBlock(type="text", text=body)],
        display=TextDisplay(text=body),
    )


@staticmethod
async def _wait_and_notify(
    task_id: str,
    process: asyncio.subprocess.Process,
    timeout_ms: int,
    registry: TaskRegistry,
) -> None:
    """后台等待进程结束，更新 registry 并推送通知。"""
    try:
        returncode = await asyncio.wait_for(
            process.wait(), timeout=timeout_ms / 1000.0
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        returncode = -1

    status = TaskStatus.completed if returncode == 0 else TaskStatus.failed
    registry.update_status(task_id, status, exit_code=returncode)
    registry.enqueue_notification(task_id)
```

### 2.3 Stall Watchdog

CC 有一个 stall watchdog：每 5 秒检查输出文件增长，45 秒无增长且末行
匹配交互式 prompt 模式（`(y/n)`, `Continue?` 等）时通知 LLM。

在 `_wait_and_notify` 里启动一个周期性 `asyncio.create_task` 检查
output 文件增长：

```python
# BashTool — stall watchdog

STALL_CHECK_INTERVAL_S = 5.0
STALL_THRESHOLD_S = 45.0
STALL_TAIL_BYTES = 1024

PROMPT_PATTERNS = [
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"\[y/n\]", re.IGNORECASE),
    re.compile(r"\(yes/no\)", re.IGNORECASE),
    re.compile(r"\b(?:Do you|Would you|Shall I|Are you sure)\b.*\?\s*$", re.IGNORECASE),
    re.compile(r"Press (any key|Enter)", re.IGNORECASE),
    re.compile(r"Continue\?", re.IGNORECASE),
    re.compile(r"Overwrite\?", re.IGNORECASE),
]


def _looks_like_prompt(tail: str) -> bool:
    last_line = tail.rstrip().rsplit("\n", 1)[-1]
    return any(p.search(last_line) for p in PROMPT_PATTERNS)


async def _stall_watchdog(
    task_id: str,
    description: str,
    output_path: str,
    registry: TaskRegistry,
    queue_reminders: Callable[[list[str]], None] | None,
) -> None:
    """周期性检查后台命令是否卡在交互式 prompt。

    注意：stall 通知**不**走 ``registry.enqueue_notification()``，
    因为那个方法会设 ``notified=True``，阻止后续真正的完成通知。
    stall 是一个 advisory hint，直接推 ``queue_reminders`` 即可。
    """
    last_size = 0
    last_growth = time.time()

    while True:
        await asyncio.sleep(STALL_CHECK_INTERVAL_S)
        task = registry.get(task_id)
        if task is None or task.status.is_terminal:
            return

        try:
            size = os.path.getsize(output_path)
        except FileNotFoundError:
            continue

        if size > last_size:
            last_size = size
            last_growth = time.time()
            continue

        if time.time() - last_growth < STALL_THRESHOLD_S:
            continue

        # 读 tail 检查是否像交互式 prompt
        try:
            with open(output_path, "rb") as f:
                f.seek(max(0, size - STALL_TAIL_BYTES))
                tail = f.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            continue

        if not _looks_like_prompt(tail):
            last_growth = time.time()  # reset，避免每 5 秒都读
            continue

        # 直接推 queue_reminders，不走 enqueue_notification
        # （不设 notified=True，task 最终完成时仍会正常通知）
        if queue_reminders is not None:
            notification = (
                f"<task-notification>\n"
                f"<task-id>{task_id}</task-id>\n"
                f"<summary>Background command \"{description}\" appears to be "
                f"waiting for interactive input</summary>\n"
                f"</task-notification>\n"
                f"Last output:\n{tail.rstrip()}\n\n"
                f"The command is likely blocked on an interactive prompt. "
                f"Kill this task and re-run with piped input (e.g., "
                f"`echo y | command`) or a non-interactive flag if one exists."
            )
            queue_reminders([notification])
        return  # 只通知一次
```

在 `_spawn_background` 中启动 watchdog：

```python
# 在 _spawn_background 末尾，asyncio.create_task(_wait_and_notify...) 之后
# queue_reminders 从 OrchestratorDeps 传入（通过 ToolContext 扩展或闭包捕获）
asyncio.create_task(
    _stall_watchdog(task_id, description, output_path, ctx.tasks, queue_reminders)
)
```

---

## 3. AgentTool

### 3.1 定位

AgentTool 是一个**普通 Tool**，`kind = ToolKind.execute`。它的 `call()`
内部构造子 `StandardOrchestrator`（depth + 1），运行一个完整的 query
loop，然后把结果打包为 `ToolCallResult` 返回。

AgentTool 支持两种模式：

1. **前台同步**（默认）：`call()` 阻塞直到子 agent 完成，透传所有事件
2. **后台异步**（`run_in_background=true`）：注册为 `AgentTaskState`，
   `call()` 立即返回 task_id

### 3.2 Input Schema

```python
class AgentTool(Tool[dict[str, Any], str]):
    name = "Agent"
    description = "Launch a new agent to handle complex, multi-step tasks."
    kind = ToolKind.execute

    input_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "A short (3-5 word) description of the task.",
            },
            "prompt": {
                "type": "string",
                "description": "The task for the agent to perform.",
            },
            "subagent_type": {
                "type": "string",
                "description": "The type of specialized agent to use.",
            },
            "model": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
                "description": "Optional model override for this agent.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this agent in the background.",
            },
        },
        "required": ["description", "prompt"],
    }
```

### 3.3 call() — 前台同步模式

对齐 `docs/kernel/subsystems/orchestrator.md` Sub-agent 章节的现有设计：

```python
async def call(
    self,
    input: dict[str, Any],
    ctx: ToolContext,
) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
    description = input["description"]
    prompt = input["prompt"]
    agent_type = input.get("subagent_type", "general-purpose")
    model = input.get("model")
    run_in_background = bool(input.get("run_in_background", False))

    if run_in_background and ctx.tasks is not None:
        yield await self._spawn_background(input, ctx)
        return

    # -- 前台同步：spawn 子 Orchestrator，收集最终文本 --

    if ctx.spawn_subagent is None:
        yield ToolCallResult(
            data={"error": "Sub-agent spawning not available at this depth"},
            llm_content=[TextBlock(type="text", text="Error: sub-agent spawning not available")],
            display=TextDisplay(text="Error: sub-agent spawning not available"),
        )
        return

    # spawn_subagent 由 Orchestrator 提供，内部构建子 StandardOrchestrator。
    # 子事件不透传到父客户端；只收集 TextDelta 作为 Agent 工具结果，
    # 并从 SubAgentEnd 汇总 tool use / token / duration 统计。
    result_text_parts: list[str] = []
    agent_stats: dict[str, int] = {}
    async for event in ctx.spawn_subagent(prompt, []):
        # 收集 sub-agent 的 text 输出作为最终结果
        if isinstance(event, TextDelta):
            result_text_parts.append(event.content)
        if isinstance(event, SubAgentEnd):
            agent_stats = {
                "toolUseCount": event.tool_use_count,
                "inputTokens": event.input_tokens,
                "outputTokens": event.output_tokens,
                "totalTokens": event.input_tokens + event.output_tokens,
                "durationMs": event.duration_ms,
            }

    final_text = "".join(result_text_parts) or "(agent produced no output)"
    yield ToolCallResult(
        data={"result": final_text, "agent": agent_stats},
        llm_content=[TextBlock(type="text", text=final_text)],
        display=TextDisplay(text=final_text),
        meta={"mustang.agent/agentStats": agent_stats},
    )
```

> **注意**：早期设计要求 `passthrough_event` 平坦透传 sub-agent 事件。
> 这会让 child assistant text 直接渲染到主窗口，已改为 Claude Code
> main 风格：前台 AgentTool 收集 child 输出并作为 Agent 工具结果返回。
> 当前实用版还会把子 Agent 的 tool use / token / duration 统计挂在
> `mustang.agent/agentStats`，供 CLI 的 Agent 工具卡片渲染完成摘要；
> 完整 CC 进度 UI 见 `docs/plans/agent-tool-claude-code-parity-plan.md`。

### 3.4 call() — 后台异步模式

```python
async def _spawn_background(
    self,
    input: dict[str, Any],
    ctx: ToolContext,
) -> ToolCallResult:
    """Spawn 后台 agent task，立即返回 task_id。"""
    from kernel.agents.mustang.tasks.types import AgentTaskState, TaskStatus, generate_task_id, TaskType
    from kernel.agents.mustang.tasks.output import TaskOutput

    description = input["description"]
    prompt = input["prompt"]
    agent_type = input.get("subagent_type", "general-purpose")
    model = input.get("model")

    task_id = generate_task_id(TaskType.local_agent)
    output = TaskOutput(ctx.session_id, task_id)
    output_path = await output.init_file()

    task = AgentTaskState(
        id=task_id,
        status=TaskStatus.running,
        description=description,
        owner_agent_id=ctx.agent_id,  # None=根 agent, str=子 agent
        output_file=output_path,
        agent_id=task_id,  # agent_id = task_id（一一对应）
        agent_type=agent_type,
        prompt=prompt,
        model=model,
    )
    ctx.tasks.register(task)

    # 提取闭包引用——不捕获整个 ctx（per-tool-call 对象）。
    # spawn_subagent 闭包的生命周期由父 Orchestrator 管理，
    # 只要父 session 存活，闭包就有效。
    spawn_fn = ctx.spawn_subagent
    registry = ctx.tasks

    # 后台启动子 agent
    asyncio.create_task(
        self._run_agent_background(
            task_id, prompt, agent_type, model,
            spawn_fn=spawn_fn, registry=registry,
        )
    )

    body = (
        f"Agent running in background with ID: {task_id}. "
        f"You will be notified when it completes."
    )
    return ToolCallResult(
        data={"task_id": task_id, "status": "running"},
        llm_content=[TextBlock(type="text", text=body)],
        display=TextDisplay(text=body),
    )


@staticmethod
async def _run_agent_background(
    task_id: str,
    prompt: str,
    agent_type: str,
    model: str | None,
    *,
    spawn_fn: Callable | None,
    registry: TaskRegistry,
) -> None:
    """后台运行子 agent 直到完成，更新 registry 并推送通知。

    注意：不捕获 ToolContext——它是 per-tool-call 的，父 tool call
    返回后不应再使用。只提取 spawn_fn（Orchestrator 级生命周期）
    和 registry（session 级生命周期）。
    """
    if spawn_fn is None:
        registry.update_status(
            task_id, TaskStatus.failed, error="spawn_subagent not available"
        )
        registry.enqueue_notification(task_id)
        return

    try:
        result_parts: list[str] = []
        # 传入 agent_id=task_id，避免 spawn_subagent 内部再生成一个
        async for event in spawn_fn(prompt, [], agent_id=task_id):
            if isinstance(event, TextDelta):
                result_parts.append(event.content)
            # 实现时写 output 文件供 TaskOutputTool 读取
            # 实现时更新 AgentProgress

        result = "".join(result_parts) or "(agent produced no output)"
        registry.update_status(task_id, TaskStatus.completed, result=result)
    except asyncio.CancelledError:
        registry.update_status(task_id, TaskStatus.killed)
    except Exception as exc:
        registry.update_status(task_id, TaskStatus.failed, error=str(exc))

    registry.enqueue_notification(task_id)
```

### 3.5 spawn_subagent 回调的实现

`ToolContext.spawn_subagent` 由 Orchestrator/SessionManager 在构建
ToolContext 时注入。它内部：

1. 创建子 `StandardOrchestrator`（depth + 1，fork 父 deps）
2. 调用子 `query(prompt)`
3. 在事件流前后注入 `SubAgentStart` / `SubAgentEnd`
4. yield 所有事件

```python
# orchestrator.py — Orchestrator 提供 spawn_subagent 闭包

def _make_spawn_subagent(self) -> Callable:
    """构造 spawn_subagent 闭包供 ToolContext 使用。"""
    parent = self

    async def spawn_subagent(
        prompt: str,
        attachments: list[ContentBlock],
        *,
        agent_id: str | None = None,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        # 允许调用方传入 agent_id（后台模式下 task_id=agent_id，
        # 避免双重生成）。前台模式不传，自动生成。
        if agent_id is None:
            agent_id = generate_task_id(TaskType.local_agent)

        child = StandardOrchestrator(
            deps=parent._deps,
            session_id=f"{parent._session_id}/agent-{agent_id}",
            initial_history=[],
            config=parent._config,
            depth=parent._depth + 1,
            agent_id=agent_id,  # 新增：子 Orchestrator 知道自己的 agent_id
        )

        yield SubAgentStart(
            agent_id=agent_id,
            description=prompt[:80],
            agent_type="general-purpose",
            spawned_by_tool_id="",  # ToolExecutor 会填入
        )

        async for event in child.query(prompt):
            yield event

        yield SubAgentEnd(
            agent_id=agent_id,
            stop_reason=child.stop_reason or StopReason.end_turn,
        )

        # Orphan drain：子 agent 结束后，接管其发起的后台 task 通知
        parent._drain_orphan_notifications(agent_id)

    return spawn_subagent
```

### 3.6 事件透传机制

现有 `ToolExecutor` 的 `_run_one()` 只 yield `ToolCallProgress` 和
`ToolCallResult`。早期 AgentTool 设计曾要求透传 `OrchestratorEvent`
（TextDelta, SubAgentStart 等）；当前前台 AgentTool 不再这么做，避免
child transcript 成为父会话顶层输出。

**保留机制**：`ToolCallProgress` 仍有可选字段 `passthrough_event`。
ToolExecutor 在 yield ToolCallProgress 时检查此字段——若存在，直接
yield `passthrough_event` 代替 ToolCallProgress 本身。它现在是通用
escape hatch，不是 AgentTool 前台路径的必需机制：

```python
# types.py — ToolCallProgress 扩展

@dataclass
class ToolCallProgress:
    content: list[ContentBlock]
    passthrough_event: OrchestratorEvent | None = None
    """当 Tool 需要透传 OrchestratorEvent 时设置。
    ToolExecutor 遇到此字段时直接 yield event，不包装为 Progress。"""
```

```python
# tool_executor.py — _run_one() 修改

async for chunk in tool.call(parsed_input, ctx):
    if isinstance(chunk, ToolCallProgress):
        if chunk.passthrough_event is not None:
            yield chunk.passthrough_event  # 直接透传
        else:
            yield ToolCallStart(...)  # 或原有 progress 逻辑
    elif isinstance(chunk, ToolCallResult):
        ...
```

前台 AgentTool 的当前不变量是：子 Agent 输出保持私有，只将最终文本
作为 Agent 工具结果返回。

---

## 4. LLM 交互工具

### 4.1 TaskOutputTool

```python
# kernel/agents/mustang/tools/builtin/task_output.py

class TaskOutputTool(Tool[dict[str, Any], str]):
    """读取后台 task 的输出。"""

    name = "TaskOutput"
    description = "Read output from a background task."
    kind = ToolKind.read
    aliases = ("BashOutput", "AgentOutput")

    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to get output from.",
            },
            "block": {
                "type": "boolean",
                "description": "Whether to wait for completion. Default true.",
                "default": True,
            },
            "timeout": {
                "type": "number",
                "description": "Max wait time in ms. Default 30000.",
                "default": 30000,
                "minimum": 0,
                "maximum": 600000,
            },
        },
        "required": ["task_id"],
    }

    @property
    def is_concurrency_safe(self) -> bool:
        return True  # 只读，可并发

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        task_id = input["task_id"]
        block = input.get("block", True)
        timeout_ms = input.get("timeout", 30000)

        if ctx.tasks is None:
            yield ToolCallResult(
                data={"error": "Task system not available"},
                llm_content=[TextBlock(type="text", text="Error: task system not available")],
                display=TextDisplay(text="Error: task system not available"),
            )
            return

        task = ctx.tasks.get(task_id)
        if task is None:
            yield ToolCallResult(
                data={"error": f"No task found with ID: {task_id}"},
                llm_content=[TextBlock(type="text", text=f"No task found with ID: {task_id}")],
                display=TextDisplay(text=f"No task found with ID: {task_id}"),
            )
            return

        # 等待完成（如果请求 block）
        if block and task.status == TaskStatus.running:
            task = await self._wait_for_completion(
                task_id, ctx.tasks, timeout_ms / 1000.0, ctx.cancel_event
            )

        # 读取输出
        output = TaskOutput(ctx.session_id, task_id)
        content = await output.read_tail()

        # 组装结果
        result: dict[str, Any] = {
            "task_id": task.id,
            "task_type": task.type.value,
            "status": task.status.value,
            "description": task.description,
            "output": content,
        }
        if isinstance(task, ShellTaskState):
            result["exit_code"] = task.exit_code
        if isinstance(task, AgentTaskState):
            result["prompt"] = task.prompt
            if task.result:
                result["result"] = task.result
            if task.error:
                result["error"] = task.error

        retrieval_status = "success" if task.status.is_terminal else "timeout"
        body = f"[{retrieval_status}] Task {task_id} ({task.status.value}):\n{content}"

        yield ToolCallResult(
            data={"retrieval_status": retrieval_status, "task": result},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )

    @staticmethod
    async def _wait_for_completion(
        task_id: str,
        registry: TaskRegistry,
        timeout_s: float,
        cancel_event: asyncio.Event,
    ) -> TaskState:
        """Poll registry 直到 task terminal 或超时。"""
        import time
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if cancel_event.is_set():
                break
            task = registry.get(task_id)
            if task is None or task.status.is_terminal:
                return task
            await asyncio.sleep(0.1)
        return registry.get(task_id)
```

### 4.2 TaskStopTool

```python
# kernel/agents/mustang/tools/builtin/task_stop.py

class TaskStopTool(Tool[dict[str, Any], str]):
    """停止一个运行中的后台 task。"""

    name = "TaskStop"
    description = "Stop a running background task by ID."
    kind = ToolKind.execute
    aliases = ("KillShell",)

    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the background task to stop.",
            },
        },
        "required": ["task_id"],
    }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        task_id = input["task_id"]

        if ctx.tasks is None:
            yield ToolCallResult(
                data={"error": "Task system not available"},
                llm_content=[TextBlock(type="text", text="Error: task system not available")],
                display=TextDisplay(text="Error"),
            )
            return

        task = ctx.tasks.get(task_id)
        if task is None:
            yield ToolCallResult(
                data={"error": f"No task found with ID: {task_id}"},
                llm_content=[TextBlock(type="text", text=f"No task found with ID: {task_id}")],
                display=TextDisplay(text=f"No task: {task_id}"),
            )
            return

        if task.status != TaskStatus.running:
            yield ToolCallResult(
                data={"error": f"Task {task_id} is not running (status: {task.status.value})"},
                llm_content=[TextBlock(type="text", text=f"Task {task_id} not running")],
                display=TextDisplay(text=f"Task {task_id} not running"),
            )
            return

        # Kill
        if isinstance(task, ShellTaskState) and task.process is not None:
            task.process.kill()
        elif isinstance(task, AgentTaskState) and task.cancel_event is not None:
            task.cancel_event.set()

        ctx.tasks.update_status(task_id, TaskStatus.killed)
        ctx.tasks.enqueue_notification(task_id)

        body = f"Successfully stopped task: {task_id} ({task.description})"
        yield ToolCallResult(
            data={"message": body, "task_id": task_id, "task_type": task.type.value},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )
```

---

## 5. TodoWriteTool（独立系统）

TodoWriteTool 是 LLM 的自管理 checklist，与后台 task framework **完全
独立**。CC 里也是两套系统（`TodoWriteTool` vs `Task`）。

### 5.1 数据模型

```python
# kernel/agents/mustang/tools/builtin/todo_write.py

@dataclass
class TodoItem:
    content: str
    status: Literal["pending", "in_progress", "completed"]
```

### 5.2 存储

存在 `TaskRegistry` 上（复用同一个 session 级对象）：

```python
# kernel/agents/mustang/tasks/registry.py — TaskRegistry 新增

class TaskRegistry:
    def __init__(self) -> None:
        ...
        # TodoWrite 数据：key = agent_id（None = 根 agent）
        self._todos: dict[str | None, list[dict[str, str]]] = {}

    def get_todos(self, agent_id: str | None) -> list[dict[str, str]]:
        return self._todos.get(agent_id, [])

    def set_todos(self, agent_id: str | None, todos: list[dict[str, str]]) -> None:
        if todos:
            self._todos[agent_id] = todos
        else:
            self._todos.pop(agent_id, None)
```

TodoWriteTool 通过 `ctx.tasks.set_todos(ctx.agent_id, new_todos)` 更新。
不需要 `context_modifier`——todos 是 registry 上的可变状态，不是
Orchestrator 的 per-turn context。

### 5.3 实现

```python
class TodoWriteTool(Tool[dict[str, Any], str]):
    name = "TodoWrite"
    description = "Manage the session task checklist."
    kind = ToolKind.other

    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["todos"],
    }

    async def call(self, input, ctx):
        todos = input["todos"]
        all_done = all(t["status"] == "completed" for t in todos)
        new_todos = [] if all_done else todos

        old_todos = ctx.tasks.get_todos(ctx.agent_id) if ctx.tasks else []
        if ctx.tasks is not None:
            ctx.tasks.set_todos(ctx.agent_id, new_todos)

        body = "Todos have been modified successfully."
        yield ToolCallResult(
            data={"old_todos": old_todos, "new_todos": new_todos},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )
```

TodoWriteTool 是**延迟加载**（`shouldDefer = True`），schema 不在初始
tool listing 里。LLM 通过 ToolSearch 按需加载。

---

## 6. 目录结构

```
kernel/agents/mustang/tasks/
├── __init__.py          # TaskManager(Subsystem)? 或纯工具模块（见 §7 讨论）
├── types.py             # TaskType, TaskStatus, TaskStateBase, ShellTaskState, AgentTaskState
├── id.py                # generate_task_id()
├── registry.py          # TaskRegistry
└── output.py            # TaskOutput, get_task_output_path()

kernel/agents/mustang/tools/builtin/
├── bash.py              # BashTool（扩展 run_in_background）
├── agent.py             # AgentTool（新增）
├── task_output.py       # TaskOutputTool（新增）
├── task_stop.py         # TaskStopTool（新增）
└── todo_write.py        # TodoWriteTool（新增）
```

---

## 7. 设计决策

### 7.1 TaskRegistry 是 Subsystem 还是普通模块？

**决策：普通模块，不做 Subsystem。**

理由：
- TaskRegistry 没有独立的 startup/shutdown 生命周期——它随 session 创建/
  销毁
- 没有需要 KernelModuleTable 的跨子系统依赖
- 它是 session 级对象（每 session 一个），不是 kernel 级单例
- SessionManager 在创建 `OrchestratorDeps` 时实例化 `TaskRegistry()`
  即可

**对比 CC**：CC 也没有 TaskManager 类。`AppState.tasks` 就是一个 dict，
工具函数散布在 `utils/task/framework.ts`。我们把散布的函数封装到
`TaskRegistry` 类里，但不升级为 Subsystem。

### 7.2 TaskRegistry vs OrchestratorDeps.task_registry

两者持有同一个引用：

```
SessionManager 创建 TaskRegistry()
  → 注入到 OrchestratorDeps.task_registry
  → Orchestrator 在 step 6d drain notifications
  → Orchestrator 构建 ToolContext 时传 ctx.tasks = deps.task_registry
  → BashTool / AgentTool 通过 ctx.tasks 注册和管理 task
```

### 7.3 后台 task 输出直写文件，不经过 Python

对齐 CC 的关键设计决策。bash 后台命令的 stdout/stderr 直接通过
subprocess 的 file descriptor 写入文件（`stdout=fd, stderr=fd`），
不经过 Python 内存。优点：

- 零内存压力——即使输出 GB 级别，Python 进程内存不增长
- 进度通过 poll 文件 tail 获取（stall watchdog 用此机制）

### 7.4 通知复用 pending_reminders 通道

不新建独立管道。后台 task 通知格式化为 `<task-notification>` XML 后，
推入现有的 `pending_reminders` buffer，下一轮 step 0 自动注入。

优点：
- 不需要修改 Orchestrator 的输入/输出接口
- 通知可以和其他 system-reminder（hook 产生的）自然合并
- 下一轮 LLM 调用一定能看到

### 7.5 AgentTool 前台模式 vs spawn_subagent 回调

前台模式下，AgentTool 不直接构造子 Orchestrator——它通过
`ctx.spawn_subagent` 回调委托给父 Orchestrator。这保持了设计原则：
**Tool 不直接持有 Orchestrator 引用**。

后台模式也通过同一个回调，只是在 `asyncio.create_task()` 里调用。

### 7.6 passthrough_event 方案的现状

`ToolCallProgress.passthrough_event` 仍作为通用 escape hatch 保留，但
前台 `AgentTool` 不再使用它。子 Agent transcript 保持私有，最终文本
通过 `ToolCallResult.llm_content` 回到父 LLM，避免 child 输出成为主会话
顶层正文。

### 7.7 StandardOrchestrator 新增 `agent_id` 构造参数

`StandardOrchestrator.__init__` 新增 `agent_id: str | None = None`。
根 Orchestrator 传 `None`；子 Orchestrator 由 `spawn_subagent` 闭包
传入（与 task_id 一致）。

此字段用于：
- `drain_notifications(agent_id=self._agent_id)` — 只取自己的通知
- `_drain_orphan_notifications(ended_agent_id)` — 接管已终止子 agent
- `ToolContext.agent_id` — 传给工具，标识当前执行上下文

**修改清单**新增一行：`kernel/agents/mustang/orchestrator/orchestrator.py` —
`StandardOrchestrator.__init__` 新增 `agent_id` 参数。

### 7.8 多 Orchestrator 共享 TaskRegistry + 通知路由

整个 session 的 Orchestrator 树（根 + 所有子 agent）共享同一个
`TaskRegistry` 实例，通过 `OrchestratorDeps` 传递（子 Orchestrator
直接复用父的 deps）。

并发安全不是问题——Python asyncio 是协作式多任务，`TaskRegistry` 的
方法都是同步 dict 操作。

**通知路由**是关键设计点：每个 task 记录 `owner_agent_id`（发起它的
agent），drain 时按 agent 过滤。这对齐 CC `query.ts:1570` 的行为——
主线程只 drain `agentId===undefined`，子 agent 只 drain addressed-to-me。

**Orphan 处理**：子 agent 结束后，它发起的后台 task 可能还在跑。根
Orchestrator 在 `SubAgentEnd` 后执行 orphan drain，接管这些通知。

```
TaskRegistry (session 级, 所有 Orchestrator 共享)
  │
  ├── 根 Orchestrator (agent_id=None)
  │     drain_notifications(agent_id=None) → 只取根 agent 的
  │     SubAgentEnd 后 → drain_notifications(agent_id=ended_child) → 接管 orphan
  │
  ├── 子 Orchestrator A (agent_id="a_xxx")
  │     drain_notifications(agent_id="a_xxx") → 只取自己的
  │
  └── 子 Orchestrator B (agent_id="a_yyy")
        drain_notifications(agent_id="a_yyy") → 只取自己的
```

### 7.9 TodoWrite 与 Task 完全独立

对齐 CC。两者的职责不同：

| | Task (background) | TodoWrite |
|---|---|---|
| 谁创建 | BashTool / AgentTool | LLM 自行管理 |
| 生命周期 | 绑定到真实进程/agent | 纯数据 checklist |
| 状态 | 5 种 (pending/running/completed/failed/killed) | 3 种 (pending/in_progress/completed) |
| 输出 | 有（文件/agent 回复） | 无 |
| 通知 | 完成时推送 LLM | 无通知 |
| 持久化 | 不持久化（session 内） | 不持久化（session 内） |

---

## 8. 实装清单

全部一次性实装，不分 phase。按依赖顺序排列：

**A. 基础层 — Task Framework**

1. 创建 `kernel/agents/mustang/tasks/types.py` — TaskType, TaskStatus, TaskStateBase, ShellTaskState, AgentTaskState, AgentProgress
2. 创建 `kernel/agents/mustang/tasks/id.py` — generate_task_id()
3. 创建 `kernel/agents/mustang/tasks/output.py` — TaskOutput, get_task_output_path()
4. 创建 `kernel/agents/mustang/tasks/registry.py` — TaskRegistry（注册/更新/查询/通知队列/GC）
5. 创建 `kernel/agents/mustang/tasks/__init__.py` — 模块入口，public exports

**B. 接入层 — Orchestrator + ToolContext 对接**

6. `kernel/agents/mustang/tools/context.py` — `tasks: Any` → `tasks: TaskRegistry | None`
7. `kernel/agents/mustang/orchestrator/types.py` — `OrchestratorDeps` 新增 `task_registry: TaskRegistry | None`
8. `kernel/agents/mustang/orchestrator/orchestrator.py` — step 6d 实现 `drain_task_notifications()` + GC
9. `kernel/agents/mustang/tools/types.py` — `ToolCallProgress` 新增 `passthrough_event: OrchestratorEvent | None`
10. `kernel/agents/mustang/orchestrator/tool_executor.py` — 处理 `passthrough_event`（透传而非包装）
11. `kernel/agents/mustang/sessions/__init__.py` — 创建 `TaskRegistry` 实例并注入 `OrchestratorDeps`

**C. 消费者 — 工具实现**

12. `kernel/agents/mustang/tools/builtin/bash.py` — 扩展 input_schema（run_in_background + description）+ 后台分支 _spawn_background / _wait_and_notify
13. `kernel/agents/mustang/tools/builtin/agent.py` — AgentTool（前台同步 + 后台异步双模式）+ spawn_subagent 回调实现（Orchestrator 侧）
14. `kernel/agents/mustang/tools/builtin/task_output.py` — TaskOutputTool
15. `kernel/agents/mustang/tools/builtin/task_stop.py` — TaskStopTool
16. `kernel/agents/mustang/tools/builtin/todo_write.py` — TodoWriteTool

**D. 注册 + 集成**

17. `kernel/agents/mustang/tools/builtin/__init__.py` — 注册 AgentTool, TaskOutputTool, TaskStopTool, TodoWriteTool
18. Stall watchdog — BashTool 后台命令的交互式 prompt 检测

---

## 9. 与现有代码的修改清单

| 文件 | 修改 |
|------|------|
| `kernel/agents/mustang/tools/context.py` | `tasks: Any` → `tasks: TaskRegistry \| None` |
| `kernel/agents/mustang/orchestrator/types.py` | `OrchestratorDeps` 新增 `task_registry` |
| `kernel/agents/mustang/orchestrator/orchestrator.py` | `__init__` 新增 `agent_id` 参数；step 6d notification drain；`_make_spawn_subagent`；`_drain_orphan_notifications` |
| `kernel/agents/mustang/orchestrator/events.py` | (已有 SubAgentStart/End，无需改) |
| `kernel/agents/mustang/tools/types.py` | `ToolCallProgress` 新增 `passthrough_event` |
| `kernel/agents/mustang/orchestrator/tool_executor.py` | 处理 `passthrough_event` |
| `kernel/agents/mustang/tools/builtin/bash.py` | 扩展 schema + 后台分支 + stall watchdog |
| `kernel/agents/mustang/tools/builtin/__init__.py` | 注册新工具 |
| `kernel/agents/mustang/sessions/__init__.py` | 创建 TaskRegistry 并注入 deps；session teardown 调 `registry.shutdown()` |
| **新文件** | |
| `kernel/agents/mustang/tasks/__init__.py` | 模块入口 |
| `kernel/agents/mustang/tasks/types.py` | 数据模型 |
| `kernel/agents/mustang/tasks/id.py` | ID 生成 |
| `kernel/agents/mustang/tasks/registry.py` | TaskRegistry（含 shutdown + todos） |
| `kernel/agents/mustang/tasks/output.py` | TaskOutput |
| `kernel/agents/mustang/tools/builtin/agent.py` | AgentTool |
| `kernel/agents/mustang/tools/builtin/task_output.py` | TaskOutputTool |
| `kernel/agents/mustang/tools/builtin/task_stop.py` | TaskStopTool |
| `kernel/agents/mustang/tools/builtin/todo_write.py` | TodoWriteTool |
