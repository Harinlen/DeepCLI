# ScheduleManager 设计 — Cron / Monitor / 定时调度

> **Quick header**
> - **Role**: cron/monitor scheduling, execution, and delivery routing.
> - **Current code**: `kernel.agents.mustang.schedule.*`, cron tools under `kernel.agents.mustang.tools.builtin.*`.
> - **Runtime owner**: Mustang runtime after sessions, commands, and gateways.
> - **Boundary**: scheduled execution only; `/loop` is a SkillManager bundled skill.

Status: **landed** — 三参考源码审查完成，当前实现已迁入 `kernel.agents.mustang.schedule`.

> 蓝图来源：
> - **Claude Code** `src/tools/ScheduleCronTool/` (CronCreate/Delete/List),
>   `src/utils/cronScheduler.ts` + `cronTasks.ts` + `cronTasksLock.ts`,
>   `src/utils/cronJitterConfig.ts`, `src/skills/bundled/loop.ts`,
>   `src/tools/RemoteTriggerTool/`, `src/tools/MonitorTool/` (feature-gated)
> - **OpenClaw** `src/cron/` — 完整生产级实现：`types.ts`, `store.ts`,
>   `schedule.ts`, `service/timer.ts`, `service/ops.ts`, `service/jobs.ts`,
>   `isolated-agent/run.ts`, `isolated-agent/delivery-dispatch.ts`,
>   `delivery.ts`, `heartbeat-policy.ts`, `run-log.ts`, `session-reaper.ts`
> - **Hermes** `cron/scheduler.py`, `cron/jobs.py`, `tools/cronjob_tools.py`,
>   `hermes_cli/cron.py`, `gateway/platforms/webhook.py`

---

## 0. 三个参考的调度系统对比

### 0.1 架构总览

| 维度 | Claude Code | OpenClaw | Hermes |
|------|-------------|----------|--------|
| **进程模型** | 单进程 CLI，session = process | 常驻 daemon，多 account | 常驻 gateway，单 user |
| **调度器** | 1s tick 轮询 + chokidar 文件监听 | 60s max timer + 强制 reload | 60s daemon thread tick |
| **存储** | `.claude/scheduled_tasks.json` | `~/.openclaw/cron/jobs.json` (原子写+备份) | `~/.hermes/cron/jobs.json` |
| **多实例协调** | lock 文件 (`scheduled_tasks.lock`) | `runningAtMs` 内存标记 + 磁盘持久化 | `fcntl`/`msvcrt` 文件锁 |
| **执行隔离** | enqueue 到当前 session | isolated-agent（独立 session）or main session | 独立 agent session（无历史） |
| **结果投递** | 无（prompt 注入当前 session） | announce (channel) / webhook / heartbeat | origin chat / platform / local file |
| **失败处理** | 无 | 指数退避 (30s→60m) + 3 次 schedule error 自动禁用 + failure alert | 无自动退避 |
| **执行记录** | 无 | JSONL per-job (`runs/{jobId}.jsonl`, 2MB cap) | markdown per-run (`output/{job_id}/{ts}.md`) |
| **session 清理** | 无 | session-reaper（24h 默认，5min 检查） | 无 |
| **jitter** | 确定性 (task ID hash)，10% interval cap 15min | 无 | 无 |
| **schedule 格式** | 5-field cron only | cron / every / at | cron / every / duration / timestamp |
| **pre-run script** | 无 | 无 | Python script → stdout 注入 context |
| **model 覆盖** | 无 | job > hook > global | per-job model/provider/base_url |
| **skill 加载** | 无 | skill snapshot resolve | multi-skill chaining |
| **repeat 限制** | 7 天过期 (recurring) | 无内置限制 | `repeat.times` 计数器 |
| **webhook 触发** | 无 | 无 | 完整 webhook adapter (HMAC + template) |
| **安全** | 无 prompt 检查 | external hook content 安全检查 | prompt injection 检测 + script path 限制 |
| **CLI 管理** | 无独立 CLI | 通过 API | `hermes cron list/create/edit/pause/resume/run/remove/status/tick` |
| **LLM 工具** | CronCreate/Delete/List (deferred) | 通过 API | `cronjob()` (create/list/update/pause/resume/remove/run) |

### 0.2 各参考的独特价值

**Claude Code 独有**：
- ScheduleWakeup（LLM 自选下次唤醒间隔，/loop dynamic 模式）
- RemoteTrigger（Anthropic CCR 云端 agent）
- Jitter 系统（fleet 负载分散）
- `durable` vs session-only 区分

**OpenClaw 独有**（最完整的生产实现）：
- **delivery-dispatch 系统**——resolve target → resolve channel → announce/webhook，支持 multi-account、thread context、Feishu/Lark prefix
- **heartbeat 策略**——`shouldSkipHeartbeatOnlyDelivery()` 抑制空 ack，`shouldEnqueueCronMainSummary()` 失败回退
- **指数退避**——transient vs permanent error 分类，30s→60m 五级退避
- **session-reaper**——定期清理过期 cron session（24h 默认）
- **run-log**——JSONL 格式 per-job 执行日志，2MB cap + 2000 行裁剪
- **startup catch-up**——前 5 个 missed job 立即执行，其余 stagger 5s（防 gateway 过载）
- **schedule error 自动禁用**——连续 3 次 cron 表达式计算失败 → 自动 disable
- **atomic 存储**——temp file + rename + .bak 备份
- **3 种 sessionTarget**——`main`（注入主 session）/ `isolated`（独立 session）/ `session:<id>`（指定 session）

**Hermes 独有**：
- **pre-run script**——Python 脚本在 agent 前运行，stdout 注入 prompt context（数据采集、变更检测）
- **multi-skill chaining**——per-job 加载多个 skill
- **model/provider 覆盖**——per-job 指定 model、provider、base_url
- **repeat 计数器**——`repeat.times=5` → 跑 5 次自动删除
- **`[SILENT]` 标记**——agent 回复 `[SILENT]` 则跳过投递（适合"无变更则不通知"场景）
- **webhook adapter**——HTTP server + HMAC + event filter + template prompt（GitHub/GitLab/Stripe）
- **media 提取**——`[MEDIA: /path]` tag → 平台原生附件
- **inactivity timeout**——10min 无 tool call / API call / stream token → kill
- **`hermes cron tick`**——手动触发一次 due job 检查（测试用）
- **prompt injection 检测**——invisible Unicode + keyword pattern 扫描

### 0.3 DeepCLI 的架构差异

| 维度 | CC / OpenClaw / Hermes | DeepCLI |
|------|------------------------|---------|
| 进程模型 | 各有不同（CLI / daemon / gateway） | 常驻 kernel，session 是内存对象 |
| 多实例协调 | lock 文件 / runningAtMs / fcntl | SQLite 行锁（`running_by` 列），见 § 3.3 |
| 存储 | JSON 文件 | SQLite（WAL + 事务） |
| 事件流 | stdout pipe / channel message | ACP WebSocket（已有） |
| 远程 agent | CC 专属 CCR API | kernel 本身常驻，"远程"就是 ACP prompt |
| 结果投递 | 各自实现 | DeliveryRouter → GatewayManager（已有 Discord adapter） |

**关键洞察**：DeepCLI 用 SQLite 统一解决了三个参考各自用文件锁 /
文件监听 / 内存标记分别解决的多实例问题。同时需要认真借鉴
OpenClaw 的 delivery-dispatch 和 Hermes 的 skill/model 能力。

---

## 1. 设计总览

```
+----------------------------------------------------------+
|                    LLM 可调用的工具                        |
+--------------+--------------+--------------+-------------+
| CronCreate   | CronDelete   | CronList     | Monitor     |
| (deferred)   | (deferred)   | (deferred)   | (deferred)  |
+------+-------+------+-------+------+-------+------+------+
       |              |              |              |
       v              v              v              v
+--------------------------------------+  +----------------+
|        ScheduleManager               |  |  TaskRegistry   |
|  (独立 Subsystem, kernel 生命周期)   |  |  (已有, session  |
|                                      |  |   scope)        |
|  +--------------+  +---------------+ |  +----------------+
|  | CronStore    |  | CronScheduler | |
|  | (SQLite)     |  | (asyncio      | |
|  |              |  |  event-driven) | |
|  +------+-------+  +-------+-------+ |
|         |                  |          |
|         +------------------+          |
|                  |                    |
|         +--------v---------+          |
|         | CronExecutor     |          |
|         | (isolated        |          |
|         |  session spawn)  |          |
|         +--------+---------+          |
|                  |                    |
|         +--------v---------+          |
|         | DeliveryRouter   |          |
|         | (session / acp / |          |
|         |  gateway)        |          |
|         +------------------+          |
+---------------------------------------+
```

---

## 2. 数据模型

### 2.1 CronTask

综合三个参考的数据模型设计。

```python
# kernel/agents/mustang/schedule/types.py

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class CronTaskStatus(str, enum.Enum):
    """Cron task lifecycle."""
    active = "active"
    paused = "paused"       # 手动暂停 or 连续失败自动暂停
    expired = "expired"     # 超龄自动过期
    completed = "completed" # one-shot 执行完成 or repeat 次数耗尽
    deleted = "deleted"     # 用户删除（软删除，审计用）


class ScheduleKind(str, enum.Enum):
    """Schedule 类型——OpenClaw 3 种 + Hermes duration，共 4 种。"""
    cron = "cron"           # 5-field cron expression (local time), e.g. "0 9 * * 1-5"
    every = "every"         # 固定间隔, e.g. 1800s = 30 分钟
    at = "at"               # 绝对时刻 (epoch seconds), e.g. "2026-05-01T09:00"
    delay = "delay"         # 相对延迟, e.g. "5m" → now + 5min（Hermes duration）


@dataclass
class Schedule:
    """Schedule 定义。

    四种格式共用一个 dataclass，kind 决定哪些字段有效。
    - cron: expr 是 5-field cron string
    - every: interval_seconds 是间隔秒数
    - at: run_at 是绝对时刻 (epoch seconds)
    - delay: 解析层语法糖——用户输入 "5m"，解析时计算
      run_at = now + 300，然后 **kind 转为 at 存储**。
      delay_seconds 仅在解析阶段存在，不进 SQLite。
    """
    kind: ScheduleKind
    expr: str = ""               # cron kind: "*/30 * * * *"
    interval_seconds: float = 0  # every kind: 1800 (= 30m)
    run_at: float = 0            # at kind: epoch seconds（delay 解析后也写入此字段）


@dataclass
class RepeatConfig:
    """重复执行的限制条件。

    三个维度可以组合使用，任何一个先到就停：
    - max_count: 最多执行多少次（如 5 次）
    - max_duration_seconds: 从创建开始算，最长跑多久（如 7 天 = 604800）
    - until: 绝对截止时间（epoch seconds，如 2026-05-01T00:00:00 的时间戳）

    全部为 None = 无限重复（直到手动停止或 max_age 超龄过期）。

    示例：
    - RepeatConfig()                                → 无限重复
    - RepeatConfig(max_count=5)                     → 跑 5 次停
    - RepeatConfig(max_duration_seconds=7*24*3600)  → 跑 7 天停
    - RepeatConfig(until=1746057600)                → 到 2025-05-01 停
    - RepeatConfig(max_count=10, max_duration_seconds=3600) → 10 次或 1 小时，先到先停
    """
    max_count: int | None = None
    max_duration_seconds: float | None = None
    until: float | None = None

    def is_exhausted(self, fire_count: int, created_at: float, now: float) -> bool:
        """任何一个限制到达就返回 True。"""
        if self.max_count is not None and fire_count >= self.max_count:
            return True
        if self.max_duration_seconds is not None and now - created_at >= self.max_duration_seconds:
            return True
        if self.until is not None and now >= self.until:
            return True
        return False


@dataclass
class DeliveryConfig:
    """结果投递配置——借鉴 OpenClaw delivery + Hermes deliver。

    target 格式（逗号分隔支持多目标）：
    - "session" — 注入 system-reminder 到创建者 session
    - "acp" — ACP WebSocket 广播 CronCompletionNotification
    - "gateway:<adapter>:<channel_id>" — 发到 Gateway channel（OpenClaw announce）
    - "none" — 不投递
    默认 "session,acp"（同时走两条路）。
    """
    target: str = "session,acp"
    on_failure: bool = True      # 失败时是否也投递
    silent_pattern: str = ""     # Hermes [SILENT] 模式：response 匹配此 pattern 则跳过投递


@dataclass
class FailureAlertConfig:
    """Per-task 失败通知配置（对齐 OpenClaw CronFailureAlert）。"""
    after: int = 3                   # 连续失败多少次后触发通知
    cooldown_seconds: float = 3600   # 两次通知之间至少间隔（默认 1h）
    target: str = "session"          # 通知发到哪里（复用 delivery target 格式）


@dataclass
class CronTask:
    """One scheduled cron job.

    综合三个参考：
    - CC: id, cron, prompt, recurring, durable
    - OpenClaw: schedule (3 kinds), sessionTarget, delivery, failureAlert, state
    - Hermes: skills, model, script, repeat, deliver, origin
    """
    id: str                          # 8-char UUID slice
    schedule: Schedule               # 调度定义（cron/every/at/delay）
    prompt: str                      # 要注入的 prompt 文本
    description: str = ""            # 人类可读描述

    # ── 行为 ──
    recurring: bool = True           # True=周期执行, False=one-shot
    # recurring 的默认值由 schedule_parser 根据 kind 自动推导：
    #   cron/every → recurring=True（天然周期性）
    #   at/delay   → recurring=False（天然一次性）
    # 用户可以显式覆盖，但 recurring=True + at 没有意义（只有一个时间点），
    # 创建时校验：at kind + recurring=True → 拒绝并报错。
    durable: bool = True             # True=持久化, False=仅内存（session 关闭即消失）

    # ── 执行配置（Hermes 启发） ──
    skills: list[str] = field(default_factory=list)  # 执行时加载的 skill 列表
    model: str | None = None         # model 覆盖（None=用 kernel 默认）
    timeout_seconds: float = 30 * 60 # 单次执行超时
    inactivity_timeout_seconds: float = 10 * 60  # 无活动超时，默认 10 分钟（0 = 禁用）

    # ── 投递（OpenClaw + Hermes 启发） ──
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)

    # ── 归属 ──
    session_id: str | None = None    # 创建此 task 的 session
    project_dir: str | None = None   # 关联的项目目录（cwd）

    # ── 时间戳 (epoch seconds) ──
    created_at: float = 0.0
    last_fired_at: float | None = None
    next_fire_at: float | None = None

    # ── 状态 ──
    status: CronTaskStatus = CronTaskStatus.active
    fire_count: int = 0
    consecutive_failures: int = 0

    # ── 重复限制 ──
    repeat: RepeatConfig = field(default_factory=RepeatConfig)  # 见上方定义
    max_age_seconds: float = 7 * 24 * 3600  # 安全网：recurring 无活动 7 天自动过期（0 = 禁用）
    # max_age_seconds vs RepeatConfig.max_duration_seconds 的区别：
    #   max_age_seconds     — 系统级安全网，防止用户忘记清理的 zombie job，
    #                         从 last_fired_at 算起（无活动才触发）
    #   max_duration_seconds — 用户主动设的"最多跑多久"，从 created_at 算起
    #                         （不管有没有活动，时间到就停）
    # 两者独立判断，任一到达就停。

    # ── 失败通知（OpenClaw failure alert） ──
    failure_alert: FailureAlertConfig | None = None  # None = 不发失败通知
    last_failure_alert_at: float | None = None       # 上次发通知的时间（cooldown 用）
```

### 2.2 CronExecution（执行记录）

借鉴 OpenClaw 的 run-log 和 Hermes 的 output 目录。

```python
@dataclass
class CronExecution:
    """One execution record of a cron task.

    对齐 OpenClaw RunLogEntry 的关键字段。
    """
    id: str                          # 8-char UUID slice
    task_id: str                     # 关联的 CronTask.id
    session_id: str                  # 执行时创建的 session
    started_at: float = 0.0
    ended_at: float | None = None
    duration_ms: float | None = None
    status: str = "running"          # running / completed / failed / timeout
    error: str | None = None
    stop_reason: str | None = None   # Orchestrator StopReason
    summary: str | None = None       # LLM 最后的文本输出摘要
    delivery_status: str | None = None  # delivered / not-delivered / skipped
    delivery_error: str | None = None
```

### 2.3 三参考数据模型对比

| 字段 | CC | OpenClaw | Hermes | DeepCLI |
|------|---|---|---|---|
| id | 8-char | string | 12-char hex | 8-char |
| schedule | `cron` (string only) | `CronSchedule` (cron/every/at) | 4 格式 (cron/every/duration/timestamp) | `Schedule` (cron/every/at/delay) 4 种全覆盖 |
| prompt | ✅ | `payload.text` | ✅ | ✅ |
| recurring | ✅ | 由 schedule kind 隐含 | 由 schedule kind 隐含 | ✅（显式，更清晰） |
| durable | ✅ | 全部持久化 | 全部持久化 | ✅（可选） |
| skills | — | — | ✅ | ✅ |
| model | — | job > hook > global | ✅ | ✅ |
| delivery | — | announce/webhook/none | origin/local/platform | session/acp/gateway/none |
| repeat | 7d 过期 | — | `repeat.times` | `RepeatConfig` (count / duration / until 三维度组合) |
| inactivity timeout | — | — | ✅ (10min) | ✅ |
| failure backoff | — | 指数退避 5 级 (30s/1m/5m/15m/60m) | — | 指数退避 5 级 (OpenClaw 模式) |
| execution log | — | JSONL per-job | markdown per-run | SQLite |
| session cleanup | — | reaper (24h) | — | session auto-cleanup |

---

## 3. ScheduleManager 子系统

### 3.1 定位

**独立 Subsystem**，不隶属于任何现有子系统。原因：

> 它横跨 SessionManager / GatewayManager / Orchestrator，
> 没有自然归属。

生命周期跟随 kernel——kernel startup 时 `startup()`，
kernel shutdown 时 `shutdown()`。

### 3.2 包结构

```
kernel/agents/mustang/schedule/
├── __init__.py          # ScheduleManager (Subsystem)
├── types.py             # CronTask, CronExecution, Schedule, DeliveryConfig, RepeatConfig, FailureAlertConfig
├── store.py             # CronStore (SQLite 持久化 + 内存 dict + 执行记录 CRUD)
├── scheduler.py         # CronScheduler (asyncio event-driven 定时器 + 心跳 + claim)
├── executor.py          # CronExecutor (isolated session spawn + 心跳 loop)
├── delivery.py          # DeliveryRouter (session / acp / gateway + retry + idempotency)
├── errors.py            # transient/permanent error 分类 + backoff 计算
└── schedule_parser.py   # schedule 解析 (cron/every/at/delay) + next_fire 计算 (croniter)
```

### 3.3 组件职责

#### CronStore

```python
class CronStore:
    """Cron task 持久化层。

    SQLite 表在 kernel state database（~/.deepcli/state/kernel.db），
    不在 session.db 中——cron task 跨 session 存在。

    durable=False 的 task 不写 SQLite，仅存内存 dict。
    kernel 重启后 non-durable task 自然消失。

    多实例注意：non-durable task 只在创建它的 kernel 实例内可见，
    其他 kernel 实例看不到（因为不在 SQLite 里）。这是预期行为
    ——non-durable 本身就是"session 级临时任务"的语义。
    """

    async def startup(self, db_path: Path) -> None: ...
    async def shutdown(self) -> None: ...

    # CRUD
    async def add(self, task: CronTask) -> None: ...
    async def remove(self, task_id: str) -> None: ...
    async def get(self, task_id: str) -> CronTask | None: ...
    async def list_all(self) -> list[CronTask]: ...
    async def list_active(self) -> list[CronTask]: ...
    async def update_fired(self, task_id: str, fired_at: float, next_at: float) -> None: ...
    async def update_status(self, task_id: str, status: CronTaskStatus) -> None: ...

    # 执行记录
    async def add_execution(self, execution: CronExecution) -> None: ...
    async def list_executions(self, task_id: str, limit: int = 20) -> list[CronExecution]: ...
    async def prune_executions(self, retention_days: int = 30) -> int: ...
```

**SQLite schema**：

```sql
-- ~/.deepcli/state/kernel.db

CREATE TABLE IF NOT EXISTS cron_tasks (
    id                  TEXT PRIMARY KEY,
    schedule_kind       TEXT NOT NULL,        -- cron / every / at（delay 解析后存为 at）
    schedule_expr       TEXT NOT NULL DEFAULT '',
    schedule_interval   REAL NOT NULL DEFAULT 0,
    schedule_run_at     REAL NOT NULL DEFAULT 0,
    prompt              TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    recurring           INTEGER NOT NULL DEFAULT 1,
    durable             INTEGER NOT NULL DEFAULT 1,
    skills              TEXT NOT NULL DEFAULT '[]',  -- JSON array
    model               TEXT,
    timeout_seconds     REAL NOT NULL DEFAULT 1800,
    inactivity_timeout  REAL NOT NULL DEFAULT 600,  -- 默认 10 分钟（0 = 禁用）
    delivery_target     TEXT NOT NULL DEFAULT 'session,acp',
    delivery_on_failure INTEGER NOT NULL DEFAULT 1,
    delivery_silent_pattern TEXT NOT NULL DEFAULT '',
    session_id          TEXT,
    project_dir         TEXT,
    created_at          REAL NOT NULL,
    last_fired_at       REAL,
    next_fire_at        REAL,
    status              TEXT NOT NULL DEFAULT 'active',
    fire_count          INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    max_age_seconds     REAL NOT NULL DEFAULT 604800,
    repeat_max_count    INTEGER,          -- 最多执行次数 (NULL=无限)
    repeat_max_duration REAL,             -- 最长持续秒数 (NULL=无限)
    repeat_until        REAL,             -- 绝对截止时间 (NULL=无限)
    failure_alert_after INTEGER,          -- 连续失败多少次后通知（NULL=不通知）
    failure_alert_cooldown REAL,          -- 通知 cooldown 秒数
    failure_alert_target TEXT,            -- 通知目标（复用 delivery target 格式）
    last_failure_alert_at REAL,           -- 上次通知时间
    running_by          TEXT,             -- 正在执行此 task 的 kernel instance ID (NULL=空闲)
    running_heartbeat   REAL              -- 心跳时间戳，执行中每 30s 刷新（>2min 无更新 = 崩溃）
);

CREATE TABLE IF NOT EXISTS cron_executions (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES cron_tasks(id),
    session_id      TEXT NOT NULL,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    duration_ms     REAL,
    status          TEXT NOT NULL DEFAULT 'running',
    error           TEXT,
    stop_reason     TEXT,
    summary         TEXT,
    delivery_status TEXT,
    delivery_error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_cron_tasks_next_fire
    ON cron_tasks(next_fire_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_cron_executions_task
    ON cron_executions(task_id);
CREATE INDEX IF NOT EXISTS idx_cron_executions_started
    ON cron_executions(started_at);
```

**为什么用 SQLite 而不是 JSON 文件**（CC / OpenClaw / Hermes 全用 JSON）：

- DeepCLI 已有 SQLite 基础设施（session.db），不增加新依赖
- 执行记录查询需要 index（JSON 文件做分页/过滤效率低）
- 并发安全免费（WAL），不需要 OpenClaw 的 atomic write + .bak 模式
- Prune 旧记录是一条 DELETE，不需要 Hermes 的行数裁剪逻辑

#### CronScheduler

```python
class CronScheduler:
    """Cron 定时器——kernel 生命周期内运行的后台 asyncio Task。

    三个参考用不同策略：
    - CC: 1 秒 tick 轮询 + chokidar 文件监听 + lock 文件
    - OpenClaw: 60 秒 max timer + 强制 reload + runningAtMs 标记
    - Hermes: 60 秒 daemon thread tick + fcntl 文件锁

    DeepCLI：event-driven sleep-to-next，cap 60 秒（防漂移）。
    task 列表变更时 cancel 当前 sleep 重新计算。
    """

    def __init__(self, store: CronStore, executor: CronExecutor) -> None: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def notify_change(self) -> None: ...

    async def _schedule_loop(self) -> None:
        """主循环：
        1. _cleanup_stale_claims()（每次 tick 都检查孤儿标记）
        2. 从 store 拿所有 active task（SQLite SELECT）
        3. 找最近的 next_fire_at
        4. sleep min(delta, 60s)（或被 notify_change 打断）
        5. 尝试 claim 到期 task（UPDATE ... WHERE running_by IS NULL）
        6. fire claimed task（并发度可配，默认 1）
        7. 处理过期 / repeat 耗尽 task
        8. 回到 1
        """

    async def _fire_task(self, task: CronTask) -> None:
        """触发一个 task：
        1. executor.execute(task)
        2. 更新 last_fired_at, fire_count, 清除 running_by
        3. recurring + repeat 未耗尽 → 计算下次 next_fire_at
        4. one-shot or repeat 耗尽 → status=completed
        5. 失败 → 走 failure backoff 逻辑（见 § 3.4）
        """

    async def _handle_startup_catchup(self) -> None:
        """Kernel 重启时的 missed task 处理。

        借鉴 OpenClaw 的 stagger 策略：
        - one-shot + missed → 立即补 fire
        - recurring + missed → 跳到下次周期（不补历史）
        - 前 5 个立即执行，其余 stagger 5 秒（防 overload）
        - 清理 stale running_by 标记（上次 kernel 崩溃留下的）
        """
```

**多实例协调**：

同一台机器可能跑多个 kernel 实例（不同项目、不同端口），共享
`~/.deepcli/state/kernel.db`。需要防止两个 kernel 同时 fire 同一个 task。

借鉴 OpenClaw 的 `runningAtMs` 标记，但用 SQLite 行锁代替内存标记，
并用**心跳**代替固定超时来判断执行方是否还活着。

```sql
-- cron_tasks 表新增列
ALTER TABLE cron_tasks ADD COLUMN running_by TEXT;       -- kernel instance ID (NULL = 未被认领)
ALTER TABLE cron_tasks ADD COLUMN running_heartbeat REAL; -- 心跳时间戳，执行中每 30s 刷新
```

**Claim 协议**（SQLite 的 `BEGIN IMMEDIATE` 保证原子性）：

```python
HEARTBEAT_INTERVAL = 30      # 每 30 秒刷新一次心跳
HEARTBEAT_STALE_THRESHOLD = 120  # 心跳超过 2 分钟没更新 → 判定为崩溃

async def _claim_due_tasks(self) -> list[CronTask]:
    """原子认领到期 task，防止多实例重复 fire。

    用 SQLite UPDATE ... WHERE 做 compare-and-swap：
    只有 running_by IS NULL 的 task 能被认领。
    """
    now = time.time()
    async with self.store.db.execute("""
        UPDATE cron_tasks
        SET running_by = ?, running_heartbeat = ?
        WHERE status = 'active'
          AND next_fire_at <= ?
          AND running_by IS NULL
        RETURNING *
    """, (self.kernel_id, now, now)):
        ...

async def _heartbeat_task(self, task_id: str) -> None:
    """刷新心跳——告诉其他 kernel 实例"我还在跑"。

    CronExecutor 执行期间每 30 秒调用一次。
    只要心跳在刷，不管跑多久都不会被误判为 stale。
    """
    await self.store.db.execute("""
        UPDATE cron_tasks SET running_heartbeat = ?
        WHERE id = ? AND running_by = ?
    """, (time.time(), task_id, self.kernel_id))

async def _release_task(self, task_id: str) -> None:
    """执行完成后释放认领标记。"""
    await self.store.db.execute("""
        UPDATE cron_tasks SET running_by = NULL, running_heartbeat = NULL
        WHERE id = ?
    """, (task_id,))

async def _cleanup_stale_claims(self) -> None:
    """清理崩溃留下的孤儿标记。

    **每次调度循环 tick 时都调用**（不只是 startup），
    这样如果另一个 kernel 在运行期间崩溃，当前 kernel
    能在 ≤60s + 2min 内发现并清理。

    不用固定超时（2h 会误杀长时间任务），
    而是看心跳：running_heartbeat 超过 2 分钟没更新
    → 说明执行方已崩溃，清除标记让 task 恢复可调度。
    """
    await self.store.db.execute("""
        UPDATE cron_tasks SET running_by = NULL, running_heartbeat = NULL
        WHERE running_by IS NOT NULL
          AND running_heartbeat < ?
    """, (time.time() - HEARTBEAT_STALE_THRESHOLD,))
```

**为什么用心跳而不是固定超时**：
- 固定 2h：如果 job 真的跑了 3 小时（大型数据分析），会被误判为
  stale，另一个 kernel 重复 fire，白跑了
- 心跳 30s：只要执行方还活着就一直刷，跑再久都不会被误杀。
  只有真正崩溃（心跳停止 > 2 分钟）才清理

**kernel_id**：每个 kernel 实例 startup 时生成一个 UUID，仅用于
cron claim 协调，不持久化。

**三参考调度器对比**：

| 维度 | CC | OpenClaw | Hermes | DeepCLI |
|------|---|---|---|---|
| 检查间隔 | 每 1 秒查一次有没有到期 job | 最多 60 秒 | 每 60 秒 | event-driven（sleep 到下个 job 的到期时间），cap 60s |
| 并发执行 | 无控制 | 可配 (默认 1) | 无控制 | 可配 (默认 1) |
| 错过补跑 | 找到 missed job → 弹窗问用户确认 | 前 5 个立即跑，其余 stagger 5s 防过载 | 按周期算 grace window | stagger 5s (OpenClaw 模式) |
| 多实例防重复 fire | chokidar 文件监听 + lock 文件 | 内存 runningAtMs 标记 + 磁盘持久化 | fcntl 文件锁 | SQLite `running_by` 行锁 |
| 崩溃后清理孤儿标记 | 无 | 2h 固定超时清除 | 无 | 心跳 30s + 2min 无心跳判定崩溃（不会误杀长任务） |

#### CronExecutor

```python
class CronExecutor:
    """Cron task 的执行器——isolated session spawn。

    三个参考的执行模型：
    - CC: enqueue prompt 到当前 session
    - OpenClaw: isolated-agent（独立 session）or main session
    - Hermes: 独立 agent session（无历史，disabled tools: cronjob/messaging/clarify）

    DeepCLI 采用 OpenClaw 的 isolated-agent 模式（默认）：
    - 每次 fire 创建独立临时 session
    - 不打断用户正在进行的对话
    - session 完成后由 reaper 清理

    额外借鉴 Hermes：
    - 支持 per-job skill 加载
    - 支持 per-job model 覆盖
    - inactivity timeout
    - 执行时禁用 CronCreate 等工具（防递归）
    """

    def __init__(self, session_manager: SessionManager) -> None: ...

    async def execute(self, task: CronTask) -> CronExecution:
        """执行一个 cron task。

        1. 创建临时 session:
           - project_dir = task.project_dir
           - flags = SessionFlags(source="cron", cron_task_id=task.id)
           - model 覆盖 (if task.model)
           - skill 预加载 (if task.skills)
        2. 启动心跳 asyncio.Task（每 30s 刷新 running_heartbeat）
        3. 注入 prompt (通过 SessionManager.prompt())
           - Orchestrator.query(prompt) → 正常 LLM ↔ tool 循环
           - 禁用 CronCreate/Delete/List 工具（防递归，Hermes 启发）
        4. 等待完成 or timeout:
           - task.timeout_seconds 总超时
           - task.inactivity_timeout_seconds 无活动超时（0 = 禁用）
        5. cancel 心跳 task
        6. 收集结果 (stop_reason, 最后文本输出)
        7. 调 DeliveryRouter.deliver(task, execution)
        8. 返回 CronExecution

        心跳管理：execute() 内部用 asyncio.create_task 启动一个
        _heartbeat_loop(task_id)，它每 HEARTBEAT_INTERVAL 秒调一次
        scheduler._heartbeat_task()。execute() 结束后（无论成功/
        失败/超时）cancel 这个 task。这样心跳和执行并发运行，
        不会被 await session 阻塞。
        """
```

**执行流程**：

```
CronScheduler._fire_task(task)
  │
  ├─ CronExecutor.execute(task)
  │    │
  │    ├─ session = SessionManager.create_session(...)
  │    │
  │    ├─ heartbeat_task = asyncio.create_task(_heartbeat_loop(task.id))
  │    │    └─ 每 30s 刷新 running_heartbeat
  │    │
  │    ├─ SessionManager.prompt(session.id, task.prompt)
  │    │    └─ Orchestrator.query(prompt) → LLM ↔ tool 循环
  │    │
  │    ├─ await 完成 or timeout (总超时 + 无活动超时)
  │    │
  │    ├─ heartbeat_task.cancel()
  │    │
  │    ├─ 收集结果 (stop_reason, summary)
  │    │
  │    └─ DeliveryRouter.deliver(task, execution)
  │
  └─ store.update_fired(task.id, now, next_fire_at)
```

#### DeliveryRouter（结果投递 / announce）

**Delivery 是什么**：cron job 跑完产出了结果（LLM 的文本输出），
delivery 就是把这个结果发给用户看的过程。如果不做 delivery，
结果就沉在临时 session 里，用户看不到。

OpenClaw 叫 `announce`（广播到 channel），Hermes 叫 `deliver`
（发到 origin chat），本质一样：**cron 执行结果的出口**。

```python
class DeliveryRouter:
    """结果投递路由（对齐 OpenClaw delivery-dispatch）。

    四种投递目标：
    - "session" — 把结果作为 system-reminder 注入创建者 session，
      用户下次和该 session 对话时会看到
    - "acp" — 通过 ACP WebSocket 广播 CronCompletionNotification
      给所有连接该 session 的客户端（直连 WS 的用户走这条路）
    - "gateway:<adapter>:<channel>" — 通过 GatewayManager 发到
      外部平台（如 Discord channel），对齐 OpenClaw announce
    - "none" — 不投递，结果仅存在执行记录里

    默认 "session,acp"（同时走两条路）。
    """

    def __init__(
        self,
        session_manager: SessionManager,
        gateway_manager: GatewayManager | None,
    ) -> None:
        # 幂等性缓存——防止重复投递（对齐 OpenClaw idempotency key）
        # key = f"{execution_id}:{target}", value = (timestamp, success)
        # TTL 24h, max 2000 entries
        self._delivered: dict[str, tuple[float, bool]] = {}

    async def deliver(
        self, task: CronTask, execution: CronExecution
    ) -> tuple[str, str | None]:
        """投递结果，返回 (delivery_status, delivery_error)。

        1. 检查 silent_pattern 匹配 → skip
        2. 检查 idempotency cache → 已投递则 skip
        3. 解析 delivery.target（逗号分隔，支持多目标）：
           - "session" → 注入 system-reminder 到创建者 session
           - "acp" → 广播 CronCompletionNotification 到 WS 客户端
           - "gateway:<adapter>:<channel>" → GatewayManager.send()
           - "none" → skip
        4. 投递失败 → transient retry（5s / 10s / 20s，3 次）
        5. 全部成功 → 记入 idempotency cache
           partial failure → 不缓存（下次重试可能重复成功的部分，
           但不会丢失失败的部分——对齐 OpenClaw 的设计决策）
        6. 失败时如果 delivery.on_failure=true → 投递错误摘要
        7. 返回 (status, error)
        """

    async def _retry_transient(
        self, fn: Callable, *, max_retries: int = 3
    ) -> Any:
        """Transient delivery 重试（对齐 OpenClaw）。

        重试间隔：[5s, 10s, 20s]
        transient 判定：网络错误、gateway 未连接、超时
        permanent 判定：channel 不存在、bot 被踢、权限不足
        """

    def _prune_cache(self) -> None:
        """清理 >24h 的 idempotency 缓存条目，保留 max 2000。"""
```

### 3.4 失败处理（OpenClaw 模式）

完整采用 OpenClaw 的 failure handling 设计——三参考中唯一经过生产
验证的实现。

#### 3.4.1 错误分类：transient vs permanent

```python
# kernel/agents/mustang/schedule/errors.py

import re

# 临时性错误（可重试）——网络抖动、provider 过载、限流
TRANSIENT_PATTERNS: dict[str, re.Pattern] = {
    "rate_limit": re.compile(
        r"(rate[_ ]limit|too many requests|429|resource has been exhausted)", re.I
    ),
    "overloaded": re.compile(
        r"\b529\b|\boverloaded\b|high demand|capacity exceeded", re.I
    ),
    "network": re.compile(
        r"(network|econnreset|econnrefused|fetch failed|socket)", re.I
    ),
    "timeout": re.compile(r"(timeout|etimedout)", re.I),
    "server_error": re.compile(r"\b5\d{2}\b"),  # 任何 5xx
}

# 永久性错误（不可重试）——配置错误、权限拒绝
PERMANENT_PATTERNS: list[re.Pattern] = [
    re.compile(r"unsupported channel", re.I),
    re.compile(r"chat not found", re.I),
    re.compile(r"bot.*not.*member", re.I),
    re.compile(r"forbidden", re.I),
]

def is_transient_error(error: str) -> bool:
    """判断是否为临时性错误。如果匹配 permanent → False，否则匹配 transient → True。"""
    if any(p.search(error) for p in PERMANENT_PATTERNS):
        return False
    return any(p.search(error) for p in TRANSIENT_PATTERNS.values())
```

#### 3.4.2 指数退避（Exponential backoff）

**指数退避**：执行失败后，不立刻按原 schedule 重试，而是把下次
执行时间往后推——失败次数越多推得越远，防止"一直失败一直重试"
占满资源。

```python
# 退避时间表（秒）——从 OpenClaw 搬过来
BACKOFF_SCHEDULE = [30, 60, 300, 900, 3600]  # 30s, 1m, 5m, 15m, 60m

def backoff_delay(consecutive_failures: int) -> float:
    """第 N 次连续失败后应等待多久。"""
    idx = min(consecutive_failures - 1, len(BACKOFF_SCHEDULE) - 1)
    return BACKOFF_SCHEDULE[max(0, idx)]
```

#### 3.4.3 Recurring job 失败处理

```python
async def _apply_recurring_failure(self, task: CronTask, error: str) -> None:
    """Recurring task 失败后的处理。

    对齐 OpenClaw timer.ts 的逻辑：
    - 计算正常的下次调度时间 (natural_next)
    - 计算退避时间 (backoff_next = now + backoff_delay)
    - 取两者的较晚者（保证退避生效，但不永久偏移 schedule）
    """
    task.consecutive_failures += 1

    # 正常下次调度
    natural_next = compute_next_fire(task.schedule, from_time=time.time())
    # 退避
    backoff_next = time.time() + backoff_delay(task.consecutive_failures)
    # 取较晚者
    task.next_fire_at = max(natural_next, backoff_next)

    # schedule 表达式本身计算失败（比如无效 cron）→ 3 次后自动禁用
    # 正常执行失败不算 schedule error
```

#### 3.4.4 One-shot job 失败处理

```python
async def _apply_oneshot_failure(self, task: CronTask, error: str) -> None:
    """One-shot task 失败后的处理。

    对齐 OpenClaw：
    - transient error + 重试次数 ≤ 3 → 退避后重试
    - permanent error or 重试耗尽 → 禁用（status=paused）
    """
    task.consecutive_failures += 1

    if is_transient_error(error) and task.consecutive_failures <= 3:
        # 退避重试（one-shot 只用前 3 级：30s, 1m, 5m）
        task.next_fire_at = time.time() + backoff_delay(task.consecutive_failures)
    else:
        # permanent error 或重试耗尽 → 暂停
        task.status = CronTaskStatus.paused
        task.next_fire_at = None
```

#### 3.4.5 成功后重置

```python
# 执行成功后
task.consecutive_failures = 0
```

#### 3.4.6 完整场景表

| 场景 | Recurring job | One-shot job |
|------|--------------|-------------|
| 成功 | `consecutive_failures=0`，按 schedule 算下次 | `status=completed` |
| transient error (第 1-3 次) | `next = max(natural, now+30s/1m/5m)` | `next = now + 30s/1m/5m`（退避重试） |
| transient error (第 4+ 次) | `next = max(natural, now+15m/60m)`，继续跑 | `status=paused`（重试耗尽） |
| permanent error | `status=paused` | `status=paused` |
| schedule 计算失败 × 3 | `status=paused`（cron 表达式本身有问题） | N/A |
| 超龄 (`max_age_seconds`) | `status=expired` | N/A |
| repeat 耗尽（count/duration/until 任一到达） | `status=completed` | N/A |
| kernel 重启 missed | 跳到下次周期 | 补 fire（stagger 5s） |

#### 3.4.7 Failure Alert（失败通知）

对齐 OpenClaw 的 `CronFailureAlert`——连续失败超过阈值时主动
通知用户，带 cooldown 防刷。

`FailureAlertConfig` 定义在 § 2.1 数据模型中，CronTask 通过
`failure_alert` 和 `last_failure_alert_at` 字段引用。

**触发逻辑**（在 `_apply_recurring_failure` / `_apply_oneshot_failure` 末尾）：

```python
if (task.failure_alert
    and task.consecutive_failures >= task.failure_alert.after
    and (task.last_failure_alert_at is None
         or time.time() - task.last_failure_alert_at >= task.failure_alert.cooldown_seconds)):
    await self.delivery_router.deliver_alert(task, error)
    task.last_failure_alert_at = time.time()
```

### 3.5 Session 清理（Session Reaper）

**问题**：每次 cron fire，CronExecutor 都会创建一个临时 session
来跑 prompt。这个 session 跑完后就没用了，但 SessionManager 不会
自动删它（session 设计上是长期保留的）。如果不清理，每天跑 24 次
cron = 24 个废弃 session 堆在 SQLite 里，一周就是 168 个。

**解法**（借鉴 OpenClaw 的 session-reaper）：

- 定期清理 `source="cron"` 标记的过期临时 session
- 默认保留 24 小时（可配，让用户有时间查看执行过程）
- **设为 0 = 永不删除**
- 清理周期：5 分钟一次（CronScheduler tick 附带检查，不独立线程）
- 只删 session 数据，CronExecution 执行记录保留（独立表）
- 配置项 `schedule.session_retention_hours`（0 = 禁用清理）

### 3.6 ScheduleManager 生命周期

```python
class ScheduleManager(Subsystem):
    """Cron 调度子系统。

    依赖：ConfigManager, SessionManager, GatewayManager(optional)
    被依赖：无（叶子节点）
    """

    SECTION = "schedule"

    async def startup(self) -> None:
        """
        1. 打开 kernel.db (aiosqlite)
        2. CronStore.startup() — auto-migrate schema
        3. CronExecutor 绑定 SessionManager
        4. DeliveryRouter 绑定 SessionManager + GatewayManager
        5. CronScheduler.start() — 启动后台调度循环
        6. _handle_startup_catchup() — 处理 missed tasks
        """

    async def shutdown(self) -> None:
        """
        1. CronScheduler.stop()
        2. 等待 in-flight executions 完成 (timeout 30s)
        3. CronStore.shutdown()
        """

    # ── 对外 API（供 Tool 调用） ──

    async def create_task(self, task: CronTask) -> CronTask: ...
    async def delete_task(self, task_id: str) -> bool: ...
    async def list_tasks(self) -> list[CronTask]: ...
    async def get_task(self, task_id: str) -> CronTask | None: ...
    async def pause_task(self, task_id: str) -> bool: ...
    async def resume_task(self, task_id: str) -> bool: ...
    async def trigger_now(self, task_id: str) -> CronExecution: ...  # Hermes: cron run
    async def list_executions(self, task_id: str, limit: int = 20) -> list[CronExecution]: ...
```

---

## 4. 工具设计

所有调度工具都是 **deferred tools**——通过 ToolSearch 加载 schema。

### 4.1 CronCreateTool

```python
class CronCreateTool(Tool):
    """Create a scheduled cron job."""

    name = "CronCreate"
    should_defer = True

    class Input(BaseModel):
        schedule: str = Field(
            description="Schedule expression. Formats:\n"
            "- Cron: '*/30 * * * *' (every 30 min), '0 9 * * 1-5' (weekdays 9am)\n"
            "- Interval: 'every 30m', 'every 2h', 'every 1d'\n"
            "- One-shot delay: '5m', '2h' (from now)\n"
            "- Timestamp: '2026-04-21T09:00' (ISO 8601, local time)"
        )
        prompt: str = Field(
            description="The prompt to execute at each fire time. "
            "Should be self-contained — it runs in an isolated session with no prior context."
        )
        description: str = Field(
            default="",
            description="Human-readable description of what this job does"
        )
        recurring: bool | None = Field(
            default=None,
            description="Whether to repeat (true) or fire once (false). "
            "If null, auto-inferred from schedule: cron/interval → true, delay/timestamp → false."
        )
        durable: bool = Field(
            default=True,
            description="Persist across kernel restarts (true) or session-only (false)"
        )
        skills: list[str] = Field(
            default_factory=list,
            description="Skills to load before execution (e.g. ['/check-build', '/deploy'])"
        )
        model: str | None = Field(
            default=None,
            description="Model override for this job (e.g. 'claude-sonnet-4-6')"
        )
        delivery: str = Field(
            default="session,acp",
            description="Where to deliver results (comma-separated for multiple): "
            "'session' (notify creator), 'acp' (WebSocket broadcast), "
            "'gateway:<adapter>:<channel>' (e.g. 'gateway:discord:123456'), or 'none'"
        )
        repeat_count: int | None = Field(
            default=None,
            description="Run at most N times then stop (null = unlimited)"
        )
        repeat_duration: str | None = Field(
            default=None,
            description="Keep repeating for this duration then stop "
            "(e.g. '7d', '12h', '30m'). Null = unlimited"
        )
        repeat_until: str | None = Field(
            default=None,
            description="Stop repeating after this time "
            "(ISO 8601 timestamp, e.g. '2026-05-01T00:00'). Null = unlimited"
        )

    class Output(BaseModel):
        id: str
        human_schedule: str    # e.g. "every 30 minutes"
        next_fire_at: str      # ISO 8601 local time
        recurring: bool
        durable: bool
```

### 4.2 CronDeleteTool

```python
class CronDeleteTool(Tool):
    name = "CronDelete"
    should_defer = True

    class Input(BaseModel):
        id: str = Field(description="Job ID returned by CronCreate")

    class Output(BaseModel):
        id: str
        deleted: bool
```

### 4.3 CronListTool

```python
class CronListTool(Tool):
    name = "CronList"
    should_defer = True

    class Input(BaseModel):
        include_completed: bool = Field(
            default=False,
            description="Also show completed/expired/deleted jobs"
        )

    class Output(BaseModel):
        jobs: list[CronJobSummary]

class CronJobSummary(BaseModel):
    id: str
    schedule: str             # 原始 schedule 表达式
    human_schedule: str       # "every 30 minutes"
    prompt: str               # 截断到前 200 字符
    description: str
    recurring: bool
    durable: bool
    status: str
    fire_count: int
    last_fired_at: str | None
    next_fire_at: str | None
    last_status: str | None   # 最近一次执行的 status
    last_error: str | None    # 最近一次执行的 error (截断)
```

### 4.4 MonitorTool

Monitor 和 Cron 是正交的——它是 session 内的后台流式任务，
注册在 TaskRegistry 中。放在 ScheduleManager 设计文档里是因为
它在 CC 的 coverage gap 里属于同一批缺失工具。

```python
class MonitorTool(Tool):
    """Start a background monitor that streams events from a command.

    Unlike Bash run_in_background (which runs once and notifies on
    completion), Monitor streams continuous output — each stdout line
    becomes a notification to the LLM in the next turn.
    """

    name = "Monitor"
    should_defer = True

    class Input(BaseModel):
        command: str = Field(
            description="Shell command to monitor (e.g. "
            "'tail -f /var/log/app.log | grep --line-buffered ERROR')"
        )
        description: str = Field(
            description="What this monitor watches for"
        )
        timeout_ms: int = Field(
            default=300_000,
            description="Auto-stop after this many ms (default 5 min)"
        )

    class Output(BaseModel):
        task_id: str
        description: str
```

**实现**：TaskRegistry 新增 `TaskType.monitor` + `MonitorTaskState`：

```python
class TaskType(str, enum.Enum):
    local_bash = "local_bash"
    local_agent = "local_agent"
    monitor = "monitor"          # 新增

@dataclass
class MonitorTaskState(TaskStateBase):
    type: TaskType = field(default=TaskType.monitor, init=False)
    command: str = ""
    recent_lines: list[str] = field(default_factory=list)
    max_buffered_lines: int = 50
```

**drain 机制**：复用 TaskRegistry 已有的 notification drain 路径。
Monitor 的 `recent_lines` 在每轮 `drain_notifications` 时被清空
并注入 system-reminder。

---

## 5. /loop Skill 对齐

### 5.1 解析规则

CC `loop.ts` 的解析 + Hermes 的 schedule 格式统一：

| 用户输入 | 解析结果 |
|----------|---------|
| `/loop 5m /check-build` | `every 5m`, recurring, prompt = `/check-build` |
| `/loop 2h check status` | `every 2h`, recurring, prompt = `check status` |
| `/loop check build every 30m` | `every 30m`, recurring, prompt = `check build` |
| `/loop /check-deploy` | 无间隔 → **dynamic 模式** |

### 5.2 Dynamic 模式

CC 用 `ScheduleWakeup` 工具。DeepCLI 等价实现：

**CronCreate one-shot + LLM 自调度**——`/loop` skill 注入 prompt 指导
LLM 在每次执行结束时 CronCreate 一个 `recurring=false` 的 one-shot
来调度下次执行。不需要独立的 ScheduleWakeup 工具。

---

## 6. 与现有子系统的交互

### 6.1 DAG 依赖

```
FlagManager → ConfigManager → ... → SessionManager → ScheduleManager
                                   → GatewayManager ↗
```

**必须在 SessionManager + GatewayManager 之后启动**。
Shutdown 顺序相反。

### 6.2 与 TaskRegistry 的关系

**正交**：

- TaskRegistry = **session scope**，管理 session 内后台 bash/agent/monitor task
- CronTask = **kernel scope**，管理跨 session 定时任务
- CronExecutor 创建的临时 session 内部仍有自己的 TaskRegistry

### 6.3 配置

```yaml
# ~/.deepcli/config/config.yaml

schedule:
  enabled: true
  max_jobs: 50                       # 最多同时存在多少个 cron job 定义
  max_concurrent_executions: 1       # 最多同时跑多少个 job（OpenClaw: 默认串行）
  default_max_age_days: 7
  default_timeout_minutes: 30
  default_inactivity_timeout_minutes: 10  # 无活动超时（0 = 禁用）
  backoff_schedule: [30, 60, 300, 900, 3600]  # 指数退避 5 级 (秒)
  execution_retention_days: 30
  session_retention_hours: 24        # cron session 保留时长（0 = 永不删除）
  startup_stagger_seconds: 5         # OpenClaw missed job stagger
```

---

## 7. ACP 协议扩展

不需要新 ACP 方法——Cron 工具通过已有的 `session/prompt` + tool
调用机制工作。

新增 ACP 事件（`session/update` 通知扩展）：

```python
class CronFireNotification(BaseModel):
    type: Literal["cron_fire"] = "cron_fire"
    task_id: str
    execution_id: str
    session_id: str
    prompt_preview: str      # 前 100 字符

class CronCompletionNotification(BaseModel):
    type: Literal["cron_completion"] = "cron_completion"
    task_id: str
    execution_id: str
    status: str              # completed / failed / timeout
    summary: str | None
```

---

## 8. Hook 事件扩展

给 HookManager 新增 2 个事件（14 → 16），用于 cron 执行前后的
自定义逻辑。这是 Hermes pre-run script 的通用替代方案。

| 事件 | 触发时机 | HookEventCtx 新增字段 | 用途 |
|------|---------|---------------------|------|
| `pre_cron_fire` | CronExecutor 创建 session **之前** | `cron_task_id`, `cron_expr`, `prompt` | 数据采集：hook 脚本的 stdout 会被拼接到 prompt 前面（和 Hermes pre-run script 等价） |
| `post_cron_fire` | CronExecutor 执行完成**之后**（delivery 之前） | `cron_task_id`, `execution_id`, `status`, `summary` | 自定义后处理：额外通知、日志、指标上报 |

**`pre_cron_fire` 的 stdout 注入**：

```python
# CronExecutor.execute() 内部
hook_result = await hooks.fire(HookEvent.PRE_CRON_FIRE, ctx)
if hook_result and hook_result.stdout:
    # 把 hook 脚本输出拼接到 prompt 前面作为额外 context
    enriched_prompt = f"[Pre-run data]\n{hook_result.stdout}\n\n{task.prompt}"
```

这样用户可以在 `~/.deepcli/hooks/` 下写一个 `pre_cron_fire` hook：

```yaml
# ~/.deepcli/hooks/pre_cron_fire.yaml
event: pre_cron_fire
type: command
command: "python3 scripts/collect_metrics.py"
```

脚本的 stdout（比如最新的指标数据）会自动注入到 cron prompt 里，
agent 拿到的就是最新数据。

---

## 9. 不做的事

| 功能 | 为什么不做 | 来源 |
|------|-----------|------|
| **RemoteTrigger 工具** | CC 专属——调用 Anthropic 的云服务（CCR），在 Anthropic 服务器上启动远程 agent 跑定时任务（"租云端 agent"）。DeepCLI kernel 本身常驻，不需要租云端实例；如果需要"关机后 cron 还跑"，解法是把 kernel 跑成 systemd 服务，不是对接 CCR API | CC 专属 |
| **ScheduleWakeup 独立工具** | CronCreate one-shot 语义等价 | CC 专属 |
| **Jitter 系统** | 单机无 fleet 分散需求 | CC 专属 |
| **CC 的 lock 文件 / chokidar** | CC 用文件锁 + 文件监听做多实例协调。DeepCLI 用 SQLite 行锁（`running_by` 列）替代，更可靠。见 § 3.3 | CC / Hermes |
| **自动重启** | kernel 哲学："崩了报错让 LLM 决定" | OpenClaw supervisor |
| **Webhook adapter** | 可通过 Gateway 扩展，不放 ScheduleManager 内 | Hermes webhook.py |
| **Pre-run script（字段级）** | 不在 CronTask 上加 `pre_script` 字段。改为给 HookManager 新增 `pre_cron_fire` / `post_cron_fire` 事件（见下方说明），用户在 hook 里做数据采集，stdout 注入 prompt context。更通用且不耦合 | Hermes script 注入 |
| **Prompt injection 检测** | 应在 ToolAuthorizer 层统一做，不在 Cron 内重复 | Hermes cronjob_tools |
| **GrowthBook 集成** | CC 内部用的远程 feature flag 服务（类似 LaunchDarkly），控制功能对用户可见性。DeepCLI 用 FlagManager + `flags.yaml` 本地文件控制 `schedule.enabled`，不需要对接外部服务 | CC 专属 |

---

## 10. 与三参考的对齐度

| 功能 | CC | OpenClaw | Hermes | DeepCLI |
|------|---|---|---|---|
| Cron 调度 | ✅ | ✅ | ✅ | ✅ |
| Interval 调度 | ❌ (cron only) | ✅ (every) | ✅ (every) | ✅ |
| 绝对时刻 (at/timestamp) | ❌ | ✅ (at) | ✅ (timestamp) | ✅ (at) |
| 相对延迟 (delay/duration) | ✅ (recurring=false) | ❌ | ✅ (duration) | ✅ (delay) |
| 持久化 | JSON 文件 | JSON 文件 | JSON 文件 | **SQLite (升级)** |
| 执行记录 | ❌ | JSONL | markdown | **SQLite (升级)** |
| Isolated session | ❌ (enqueue) | ✅ | ✅ | ✅ |
| 结果投递 | ❌ | ✅ (announce/webhook) | ✅ (origin/platform) | ✅ (session/acp/gateway) |
| 投递重试+幂等 | ❌ | ✅ (3 次 transient retry + idempotency cache) | ❌ | ✅ (OpenClaw 模式) |
| 失败退避 | ❌ | ✅ (指数 5 级) | ❌ | ✅ (OpenClaw 模式) |
| 失败通知 | ❌ | ✅ (failure alert + cooldown) | ❌ | ✅ (OpenClaw 模式) |
| 自动暂停 | ❌ | ✅ (schedule error) | ❌ | ✅ |
| Session 清理 | ❌ | ✅ (reaper 24h) | ❌ | ✅ (OpenClaw 模式) |
| Per-job model | ❌ | ✅ | ✅ | ✅ |
| Per-job skills | ❌ | ❌ | ✅ | ✅ |
| Repeat 限制 | 7d 过期 | ❌ | ✅ (times) | ✅ (count / duration / until，可组合) |
| Inactivity timeout | ❌ | ❌ | ✅ (10min) | ✅ |
| Silent 抑制 | ❌ | ✅ (heartbeat ack) | ✅ ([SILENT]) | ✅ |
| Startup catch-up | ✅ (confirm) | ✅ (stagger) | ✅ (grace) | ✅ (stagger) |
| /loop skill | ✅ | ❌ | ❌ | ✅ |
| LLM 工具 | ✅ (deferred) | ❌ (API only) | ✅ (cronjob tool) | ✅ (deferred) |
| CLI 管理 | ❌ | ❌ | ✅ (hermes cron) | CommandManager (`/cron`) |
| Pre-run 数据注入 | ❌ | ❌ | ✅ (script) | ✅ (pre_cron_fire hook) |
| 多实例协调 | lock 文件 | runningAtMs | fcntl | SQLite running_by |
| Monitor (流式) | feature-gated | ❌ | ❌ | ✅ |

**DeepCLI 取各家之长**：
- CC 的 deferred tool 模式 + /loop skill
- OpenClaw 的 delivery-dispatch（含 transient retry + idempotency）+ 指数退避 + failure alert + session-reaper + startup stagger + 多实例 claim 协调
- Hermes 的 per-job model/skills + repeat 限制 + silent 模式
- 自己的：SQLite 存储 + event-driven 调度 + `pre_cron_fire` / `post_cron_fire` hook 事件（替代 Hermes pre-run script）+ ACP 广播投递


---

## Appendix: 实现笔记

# ScheduleManager 实装计划

设计文档：[schedule.md](schedule.md)

遵循 [workflow.md](../../workflow/workflow.md) 的 6-phase 流程。
Phase 1（设计）已完成。本文档是 Phase 2–6 的执行清单。

**核心原则**：E2E 先行。每个步骤实装完后必须在 probe 中跑通，
确认功能端到端可用，然后才写单元测试覆盖边界条件。

---

## 前置状态

- Kernel v1.0.0，Phase 13 完成，1338+ tests passing
- 所有依赖子系统已实装：SessionManager, ToolManager, ToolSearch,
  GatewayManager, HookManager, TaskRegistry, SkillManager
- probe 客户端已有 ToolSearch deferred tool 加载、permission round-trip、
  task notification 等 E2E 测试能力

---

## 步骤清单

### Step 1：数据模型 + Store + Schema

**目标**：`kernel/agents/mustang/schedule/types.py` + `kernel/agents/mustang/schedule/store.py` 能跑通 CRUD。

**实装**：
- `types.py` — CronTaskStatus, ScheduleKind, Schedule, RepeatConfig,
  DeliveryConfig, FailureAlertConfig, CronTask, CronExecution
- `store.py` — CronStore: SQLite schema auto-migrate (`kernel.db`),
  durable/non-durable 双层存储, CRUD + execution records + prune
- `errors.py` — TRANSIENT_PATTERNS, PERMANENT_PATTERNS, is_transient_error,
  BACKOFF_SCHEDULE, backoff_delay
- `schedule_parser.py` — parse_schedule (string → Schedule),
  compute_next_fire (Schedule → epoch), 支持 cron/every/at/delay 四种

**E2E 验证**：
- `tests/e2e/test_schedule_e2e.py::test_store_crud` — 直接
  import CronStore，对 kernel.db 做 add/get/list/remove/update_fired，
  验证 SQLite 持久化（这一步不需要 kernel subprocess，是 integration test，
  但确认 schema 能跑）

**单元测试**：
- `tests/kernel/schedule/test_types.py` — RepeatConfig.is_exhausted 边界
- `tests/kernel/schedule/test_store.py` — CRUD, prune, non-durable memory-only
- `tests/kernel/schedule/test_errors.py` — transient/permanent 分类, backoff 计算
- `tests/kernel/schedule/test_schedule_parser.py` — 四种格式解析 + next_fire 计算

---

### Step 2：CronScheduler + CronExecutor + ScheduleManager 壳

**目标**：kernel startup 时 ScheduleManager 能启动调度循环，创建
一个 cron task 后能自动 fire 并 spawn isolated session。

**实装**：
- `scheduler.py` — CronScheduler: event-driven 主循环, claim 协议
  (running_by + heartbeat), _cleanup_stale_claims (每 tick),
  _handle_startup_catchup (stagger), notify_change
- `executor.py` — CronExecutor: create_session + prompt + await +
  heartbeat loop (asyncio.create_task) + 结果收集。
  暂时不接 DeliveryRouter（Step 3 做），结果先只写 CronExecution 记录
- `__init__.py` — ScheduleManager (Subsystem): startup/shutdown,
  对外 API (create_task/delete_task/list_tasks/pause/resume/trigger_now)
- `app.py` — 注册 ScheduleManager 到 lifespan + module_table
- `flags.yaml` — `schedule.enabled: true`

**E2E 验证**（**Gate：必须通过才能继续**）：
- `test_schedule_e2e.py::test_cron_fire_creates_session` —
  通过 ScheduleManager API 创建一个 `schedule="every 5s"` 的 task，
  等待 ~10s，验证：
  1. CronExecution 记录产生
  2. 临时 session 被创建（SessionManager.list 可见）
  3. task.fire_count > 0
  4. task.last_fired_at 有值
- `test_schedule_e2e.py::test_oneshot_delay_fires_once` —
  创建 `schedule="3s"` (delay → at) one-shot，等待 ~5s，验证
  fire_count=1 且 status=completed
- `test_schedule_e2e.py::test_pause_resume` — 创建 task → pause →
  等待一个周期 → 验证没有 fire → resume → 等待 → 验证 fire

**单元测试**：
- `tests/kernel/schedule/test_scheduler.py` — claim 协议 mock,
  stale cleanup, notify_change 唤醒, startup catchup
- `tests/kernel/schedule/test_executor.py` — session 创建 mock,
  heartbeat loop, timeout 处理, inactivity timeout

---

### Step 3：DeliveryRouter

**目标**：cron 执行完成后，结果能投递到 session / ACP / gateway。

**实装**：
- `delivery.py` — DeliveryRouter: target 解析 (session,acp,gateway,none),
  transient retry (5s/10s/20s), idempotency cache (24h TTL),
  partial failure 不缓存, deliver_alert (failure alert)
- 接入 CronExecutor.execute() 的 step 7
- ACP 事件：CronFireNotification, CronCompletionNotification
  注册到 SessionUpdateNotification 类型

**E2E 验证**（**Gate**）：
- `test_schedule_e2e.py::test_delivery_session_reminder` —
  创建 cron task (delivery="session"), 等待 fire 完成，
  在创建者 session 发一条 prompt，验证 system-reminder 里
  包含 cron 执行结果
- `test_schedule_e2e.py::test_delivery_acp_notification` —
  创建 cron task (delivery="acp"), 开一个 WS 连接监听，
  等待 fire，验证收到 CronCompletionNotification 事件

**单元测试**：
- `tests/kernel/schedule/test_delivery.py` — target 解析,
  retry 逻辑, idempotency cache prune, silent_pattern 匹配

---

### Step 4：Cron 工具 (CronCreate/Delete/List)

**目标**：LLM 能通过 ToolSearch 加载 cron 工具并创建/管理 cron job。

**实装**：
- `tools/builtin/cron_create.py` — CronCreateTool (deferred)
- `tools/builtin/cron_delete.py` — CronDeleteTool (deferred)
- `tools/builtin/cron_list.py` — CronListTool (deferred)
- ToolManager 注册 3 个 deferred 工具
- PromptBuilder 注入 cron 使用指导（system prompt 段落）

**E2E 验证**（**Gate**）：
- `test_schedule_e2e.py::test_llm_cron_create_via_tool_search` —
  发 prompt "创建一个每分钟检查一次的定时任务，prompt 是 'echo hello'"，
  验证 LLM 调用 ToolSearch 加载 CronCreate → 调用 CronCreate →
  返回 job id + human_schedule
- `test_schedule_e2e.py::test_llm_cron_list` —
  先通过 API 创建 2 个 task，再发 prompt "列出所有定时任务"，
  验证 LLM 调 CronList 且输出包含两个 job
- `test_schedule_e2e.py::test_llm_cron_delete` —
  创建一个 task → 发 prompt "删除定时任务 {id}" →
  验证 CronDelete 被调用且 task 被删

**单元测试**：
- `tests/kernel/schedule/test_cron_tools.py` — 各工具的 Input 校验,
  recurring 自动推导, schedule 解析错误处理

---

### Step 5：失败处理 + Failure Alert

**目标**：transient/permanent error 分类 + 指数退避 + failure alert 全链路。

**实装**：
- CronScheduler._fire_task 接入 _apply_recurring_failure /
  _apply_oneshot_failure
- FailureAlert 触发逻辑接入 DeliveryRouter.deliver_alert
- Schedule error 自动禁用（3 次 compute_next_fire 异常 → paused）

**E2E 验证**（**Gate**）：
- `test_schedule_e2e.py::test_backoff_on_failure` —
  创建一个 prompt 必定失败的 cron task（比如 prompt 触发不存在的工具），
  等待 2-3 次 fire，验证 consecutive_failures 递增 +
  next_fire_at 被推迟（而不是按原 schedule）
- `test_schedule_e2e.py::test_oneshot_transient_retry` —
  mock 一个 transient error（需要考虑怎么触发），验证 one-shot
  重试 ≤ 3 次后 paused

**单元测试**：
- `tests/kernel/schedule/test_failure_handling.py` — recurring backoff
  逻辑 (max(natural, backoff)), one-shot retry exhaustion,
  permanent error 立即 pause, failure alert cooldown

---

### Step 6：Session Reaper + Repeat 限制 + max_age

**目标**：临时 session 按时清理，repeat 三维度限制生效，zombie job 过期。

**实装**：
- CronScheduler tick 附带 session reaper 检查（5min 节流）
- SessionManager 扩展：按 source="cron" 查询 + 删除过期 session
- _fire_task 里检查 RepeatConfig.is_exhausted → status=completed
- _schedule_loop 里检查 max_age_seconds（从 last_fired_at 算）→ status=expired

**E2E 验证**（**Gate**）：
- `test_schedule_e2e.py::test_repeat_count_limit` —
  创建 `repeat_count=2, schedule="every 3s"` 的 task，等待 ~10s，
  验证 fire_count=2 且 status=completed
- `test_schedule_e2e.py::test_session_reaper` —
  配置 `session_retention_hours=0.001` (≈3.6s)，创建并等待 fire，
  等待 ~10s，验证临时 session 被清理（SessionManager.list 不可见）

**单元测试**：
- `tests/kernel/schedule/test_repeat.py` — RepeatConfig 三维度组合
- `tests/kernel/schedule/test_reaper.py` — 按 source 过滤, retention=0 禁用

---

### Step 7：Hook 事件 (pre_cron_fire / post_cron_fire)

**目标**：cron 执行前后触发 hook，pre_cron_fire 的 stdout 注入 prompt。

**实装**：
- HookEvent 枚举新增 PRE_CRON_FIRE, POST_CRON_FIRE（14 → 16）
- EVENT_SPECS 补充对应事件的 can_block / ctx 字段定义
- CronExecutor.execute() 在 create_session 前 fire PRE_CRON_FIRE，
  stdout → enriched_prompt
- CronExecutor.execute() 在 delivery 前 fire POST_CRON_FIRE

**E2E 验证**（**Gate**）：
- `test_schedule_e2e.py::test_pre_cron_fire_hook_injects_data` —
  在 test 临时目录创建一个 `pre_cron_fire` hook（echo 固定字符串），
  创建 cron task，等待 fire，验证 CronExecution 的 summary 中
  包含 hook 注入的数据

**单元测试**：
- `tests/kernel/schedule/test_hooks.py` — fire 调用 mock,
  stdout 注入拼接, hook 异常不影响执行

---

### Step 8：MonitorTool

**目标**：LLM 能启动后台 monitor，持续流式输出通过 system-reminder 通知。

**注意**：MonitorTool 在 TaskRegistry 中（session scope），不在
ScheduleManager 中。但它是同一批 coverage gap，放在一起实装。

**实装**：
- TaskType.monitor + MonitorTaskState 数据模型（扩展 tasks/types.py）
- `tools/builtin/monitor.py` — MonitorTool (deferred)
- TaskRegistry 扩展：monitor task 的 spawn (subprocess + line reader) +
  recent_lines ring buffer + drain 逻辑
- ToolManager 注册 MonitorTool 为 deferred

**E2E 验证**（**Gate**）：
- 已有 `test_monitor_e2e.py`（3 个测试），确认它们通过：
  - test_monitor_start_and_stop
  - test_monitor_invalid_command
  - test_monitor_with_task_output

**单元测试**：
- `tests/kernel/tasks/test_monitor.py` — MonitorTaskState lifecycle,
  recent_lines buffer overflow, drain 清空

---

### Step 9：/loop Skill + /cron 命令

**目标**：用户能用 `/loop 5m /check-build` 语法创建 cron job，
`/cron` 命令列出所有 job。

**实装**：
- `kernel/agents/mustang/skills/bundled/loop_skill.py` — /loop bundled skill,
  interval 解析 + CronCreate 调用指导 + dynamic 模式 prompt
- CommandManager 注册 `/cron` 命令 — 调 ScheduleManager.list_tasks

**E2E 验证**（**Gate**）：
- `test_schedule_e2e.py::test_loop_skill_creates_cron` —
  发 prompt "/loop 10s echo test"，验证 CronCreate 被调用
  且 task 出现在 list_tasks 中

**单元测试**：
- `tests/kernel/schedule/test_loop_skill.py` — interval 解析正确性

---

### Step 10：Quality Check + Report

**目标**：走完 Phase 5 + Phase 6。

**质量检查**（全部必须通过）：
```bash
uv run ruff format src/
uv run ruff check src/
uv run mypy src/
uv run pytest --cov=src tests/
cloc src/ --by-percent c         # comment density 20–25%
uv run bandit -r src/ -q
```

**文档更新**：
- `docs/plans/progress.md` — 新增 ScheduleManager 行
- `docs/plans/roadmap.md` — ScheduleManager 标记完成
- `docs/reference/claude-code-coverage.md` — 6 个 ❌ → ✅
- `docs/kernel/architecture.md` — 子系统表新增 ScheduleManager
- `docs/plans/backlog.md` — ScheduleManager 相关延迟功能

---

## E2E 测试文件汇总

所有 E2E 测试集中在一个文件：`tests/e2e/test_schedule_e2e.py`

| 测试函数 | 验证目标 | 对应步骤 |
|---------|---------|---------|
| test_cron_fire_creates_session | 基础 fire + isolated session | Step 2 |
| test_oneshot_delay_fires_once | delay → at 转换 + one-shot 完成 | Step 2 |
| test_pause_resume | pause/resume 生命周期 | Step 2 |
| test_delivery_session_reminder | delivery 到创建者 session | Step 3 |
| test_delivery_acp_notification | delivery 到 WS 客户端 | Step 3 |
| test_llm_cron_create_via_tool_search | LLM 调 ToolSearch → CronCreate | Step 4 |
| test_llm_cron_list | LLM 调 CronList | Step 4 |
| test_llm_cron_delete | LLM 调 CronDelete | Step 4 |
| test_backoff_on_failure | 指数退避生效 | Step 5 |
| test_oneshot_transient_retry | one-shot 重试 + 耗尽 | Step 5 |
| test_repeat_count_limit | repeat 次数限制 | Step 6 |
| test_session_reaper | 临时 session 清理 | Step 6 |
| test_pre_cron_fire_hook_injects_data | hook stdout 注入 | Step 7 |
| test_loop_skill_creates_cron | /loop skill → CronCreate | Step 9 |

加上已有的 `test_monitor_e2e.py`（3 个测试）= **共 17 个 E2E 测试**。
