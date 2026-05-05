# ToolManager — Design

Status: **landed** — 全部实装。Phase 1–6 已完成（含 ToolSearchTool + deferred 层 + AgentTool + TaskManager）。

> 前置阅读：
> - Claude Code 流程：[claude-code-query-loop-walkthrough.md](../../reference/claude-code-query-loop-walkthrough.md)
> - Orchestrator 契约：[kernel/subsystems/orchestrator.md](../../kernel/subsystems/orchestrator.md)
> - 架构启动顺序：[kernel/architecture.md#启动顺序](../../kernel/architecture.md)

---

## 1. 核心概念

**ToolManager 是"工具目录 + 工具执行接口"的提供者**，不负责授权决策、不负责并发调度、不负责结果持久化。它暴露：

1. 一个 **`Tool` 抽象**（所有工具统一接口）
2. 一个 **两层 registry**（`core` / `deferred`，+ MCP 代理 + Agent 作为普通工具）
3. 每次 `query()` 前被调用的 **`snapshot(ctx)` 方法** —— 返回当前会话可见的 `list[ToolSchema]` + 一张 `name → Tool` 查找表
4. 工具共享的 **`ToolContext`**（访问 Session 级 blobs/tasks、FileStateCache、cwd、agent_depth）
5. Tools 内部共享组件（`FileStateCache`，以及 registry 本身）

它**不**做：
- 权限决策（`ToolAuthorizer` 子系统负责 → 见 `architecture.md` 子系统表）
- tool 执行的并发调度（`Orchestrator.ToolExecutor` 负责）
- tool_result 的落盘与 budget（Session `BlobStore` + Orchestrator compression layer 负责）
- Hook 触发（`HookManager` 负责）

**设计原则**：Tools 是"目录 + 接口"，所有横切关注点由上下文（`ToolContext`）注入，工具本体只写业务逻辑。

---

## 2. 职责边界（对比 Claude Code 的模糊 vs mustang 的清晰）

Claude Code 的 [`src/Tool.ts`](../../../../../projects/claude-code-main/src/Tool.ts) 把决策逻辑和领域知识揉在 Tool 接口里。mustang 按已决策的子系统切分**决策角色**（谁拍板）与**信息源角色**（谁懂这次调用的领域语义）：

| Claude Code Tool 字段 | mustang 归属 | 说明 |
|---|---|---|
| `name`, `description`, `inputSchema`, `call()` | ✅ `Tool` 接口 | 身份 + 入参 schema + 执行 |
| `isReadOnly`, `isConcurrencySafe` | ✅ `Tool` 接口 | 供 Orchestrator 做并发分批 |
| `validateInput` | ✅ `Tool` 接口 | 便宜早拒绝，在授权之前 |
| `userFacingName`, `getActivityDescription` | ✅ `Tool` 接口 | UI 展示元数据 |
| `aliases`, `searchHint` | ✅ `Tool` 接口 | 兼容 + ToolSearch 匹配 |
| `shouldDefer`, `alwaysLoad` | ✅ `Tool` 接口 | 两层 registry 分区 |
| `maxResultSizeChars` | ✅ `Tool` 接口 | 上报，Orchestrator 做聚合 budget |
| `checkPermissions` | ✅ `Tool` 接口 → `default_risk()` | **Tool 是"这次调用危险不危险"的信息源**。返回 PermissionSuggestion（low/medium/high + 建议 allow/ask/deny + reason），由 ToolAuthorizer 消费仲裁 |
| `preparePermissionMatcher` | ✅ `Tool` 接口 → `prepare_permission_matcher()` | Tool 提供"rule pattern 怎么匹 input"的闭包（FileEdit 用 glob 匹 path；Bash 用前缀匹 command），ToolAuthorizer 无法抽象这种 per-tool 领域逻辑 |
| `renderToolResultMessage` | ✅ `Tool` 接口 → `display_payload()` | **Tool 是"渲染需要哪些结构化数据"的信息源**。不返回 ReactNode，而是返回 `ToolDisplayPayload` union（TextDisplay / DiffDisplay / LocationsDisplay / …），客户端按 payload 类型路由到自己的 renderer |
| `isDestructive` | ✅ `Tool` 接口 → 独立 bool 字段 | 与 `ToolKind` 正交（edit kind 可能不破坏；execute kind 可能不破坏）。ToolAuthorizer 用此信号对不可逆操作用更严策略（allow_always 只对非 destructive 生效等） |
| `contextModifier` | ✅ `Tool` 接口（在 `ToolCallResult` 上） | Tool 改了**不能编进 tool_result 喂 LLM** 的 session state（cwd、env、worktree path）时，用 modifier 显式告诉 Orchestrator。客户端同步走 ACP 事件，但下一个 tool 调用看到新 state 必须靠这个 |
| `extractSearchText` | ❌ | transcript search 是客户端 feature，kernel 不做 |
| `mcpMeta`, `isMcp`, `isLsp` | ✅ `Tool` 接口 | 由 `MCPAdapter` 填写，built-in 不动 |
| `interruptBehavior` | ✅ `Tool` 接口 | 默认 `block`（等同 Claude Code） |

**核心区分**：Tool 提供**领域知识**（这次 input 危险程度、渲染结构、副作用范围、匹配规则怎么写），但**不仲裁**。仲裁由独立子系统负责——
- `ToolAuthorizer` 用 Tool 的 `default_risk` + 分层 rules + session grants 仲裁 allow/deny/ask
- 客户端用 Tool 的 `display_payload` + 本端渲染能力决定怎么呈现

把信息源也剥离掉会让 ToolAuthorizer 被迫抽象每个 tool 的领域语义（无法做到），或让客户端对每个 tool name 硬编码渲染规则（N×M 耦合）。Tool 必须是信息源。

---

## 3. Tool 抽象

### 3.1 接口（Python ABC）

```python
# kernel/tools/tool.py
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, Generic

from kernel.orchestrator.types import ToolKind
from kernel.llm.types import ToolSchema

InputT = TypeVar("InputT", bound=dict[str, Any])
OutputT = TypeVar("OutputT")


@dataclass(frozen=True)
class ToolCallProgress:
    """Zero or more progress updates emitted while a tool runs."""
    content: list[Any]  # list[ContentBlock]


# ── Display payload（客户端渲染用）──────────────────────────────
#
# Tool 是"这次输出应该怎么呈现"的信息源。不返回 ReactNode；返回结构化
# payload，由客户端（web/IDE 插件）按类型路由到自己的 renderer。
# 每个客户端只需实现有限几种 payload 类型，不用为 43 个 tool name 各
# 写一个渲染函数。

@dataclass(frozen=True)
class TextDisplay:
    """Plain / markdown text block.  Bash stdout、纯文本 tool 默认走这个。"""
    text: str
    language: str | None = None          # syntax highlight hint

@dataclass(frozen=True)
class DiffDisplay:
    """File edit 之前 / 之后对比。FileEdit / FileWrite。"""
    path: str
    before: str | None                   # None → 新建文件
    after: str

@dataclass(frozen=True)
class LocationsDisplay:
    """可跳转的 source locations。Grep / Glob。"""
    locations: list[dict[str, Any]]      # [{path, line?, column?}, ...]
    summary: str | None = None

@dataclass(frozen=True)
class FileDisplay:
    """FileRead（文本/图片/PDF）。文本可能被截断；图片/PDF 以文字摘要展示。"""
    path: str
    content: str
    truncated: bool = False

@dataclass(frozen=True)
class RawBlocks:
    """兜底：直接把 ContentBlock list 丢给客户端，它自己处理。"""
    blocks: list[Any]

ToolDisplayPayload = (
    TextDisplay | DiffDisplay | LocationsDisplay | FileDisplay | RawBlocks
)


# ── Permission suggestion（Tool 对这次调用的风险判断）──────────
# Tool 提供建议；ToolAuthorizer 结合分层 rules + session grants 仲裁。

@dataclass(frozen=True)
class PermissionSuggestion:
    risk: Literal["low", "medium", "high"]
    default_decision: Literal["allow", "ask", "deny"]
    reason: str                          # 给 ToolAuthorizer 日志 + 用户看


# ── Tool 执行结果 ────────────────────────────────────────────
@dataclass(frozen=True)
class ToolCallResult(Generic[OutputT]):
    """Tool.call() 最后一次 yield 的终态。三份字段各有消费者。"""

    data: OutputT
    """原始结构化结果。Orchestrator **不读**；用于：
       (a) 上层 Tool 消费（AgentTool 把 sub-agent data 展平进自己的返回
           —— 对齐 Claude Code AgentTool.tsx:309 的 TeammateSpawnedOutput）
       (b) logging / telemetry hook 按工具类别抽字段（FileEdit.diff、
           FileRead.content、Bash.stdout）—— 对齐 toolExecution.ts:1227-1293
    """

    llm_content: list[Any]
    """喂回 LLM 的 tool_result content blocks（list[ContentBlock]）。
    Orchestrator 把它裹进 user-role ToolResultContent 追加进 history。"""

    display: ToolDisplayPayload
    """给客户端渲染的 payload，走 ToolCallDisplay 事件到 ACP。LLM 看不到。"""

    context_modifier: "ContextModifier | None" = None
    """非 None 时，Orchestrator 执行完此 tool 后用它更新 ToolContext。
    用于 Tool 改了 session-level state 但该 state 不该塞进 llm_content 的
    场景——cwd、env、临时 worktree path 等。见 §5.3。"""


# contextModifier 是一个 callable：拿到当前 ctx，返回更新后的 ctx。
ContextModifier = Callable[["ToolContext"], "ToolContext"]


class Tool(ABC, Generic[InputT, OutputT]):
    """Contract every tool implements.

    All identity & metadata are class attributes; execution + validation
    + risk + rendering are methods that may depend on ``input``.
    ``ToolContext`` is the *only* way a tool touches the rest of the kernel.
    """

    # ── 元数据（类属性，启动时读一次） ─────────────────────────────
    name: str
    description: str                      # plain text（非 markdown），对齐 Claude Code
                                          # FileReadTool/prompt.ts:12。以后若需要 per-input
                                          # 动态文案（permission UI），另加 describe_call(input)
    kind: ToolKind                         # read/search/edit/delete/execute/…
    aliases: tuple[str, ...] = ()
    search_hint: str = ""                 # 3-10 word capability phrase
    should_defer: bool = False            # True → 只暴露 name 给 LLM
    always_load: bool = False             # True → 即便 defer 启用也全量加载
    cache: bool = True                    # schema 是否可 prompt-cache

    # 便宜优化字段
    max_result_size_chars: int = 100_000  # Orchestrator budget 层参考
    interrupt_behavior: Literal["cancel", "block"] = "block"
    """cancel: 支持取消、丢弃未完成工作；block: 等 tool 自己跑完（默认）。
    BashTool / AgentTool 应声明 cancel；纯计算 tool 保持 block。"""

    # ── Input schema（支持 lazy，对齐 Claude Code lazySchema）──────
    # input_schema 的静态形式是类属性，但**通过 classmethod 获取**，允许
    # 子类在 registration time 根据 FlagManager / 运行时状态构造 schema。
    # 对齐 Claude Code Tool.ts 的 `lazySchema(() => z.object(...))` 模式：
    # 某些 tool 的字段集依赖 feature flag（例 AgentTool 的 subagent_type
    # 枚举需要读 agents 配置），不能在 import time 硬编码。
    input_schema: ClassVar[dict[str, Any]] = {}   # 简单情形直接填

    @classmethod
    def build_input_schema(cls, module_table: "KernelModuleTable") -> dict[str, Any]:
        """Return the tool's JSON Schema.  Called once by ToolRegistry at
        ``register()`` time, result is cached.  Default returns class attr;
        override to inspect ``module_table.flags`` / ``module_table.config``
        for feature-gated fields.

        Example (AgentTool):
            @classmethod
            def build_input_schema(cls, module_table):
                agents = module_table.get(AgentManager).list()
                return {
                    "type": "object",
                    "properties": {
                        "subagent_type": {"enum": [a.name for a in agents]},
                        "prompt": {"type": "string"},
                    },
                }
        """
        return cls.input_schema

    # ── 静态判定（调度 & UI） ─────────────────────────────────────
    def user_facing_name(self, _input: InputT) -> str:
        return self.name

    def activity_description(self, _input: InputT) -> str | None:
        """Present-tense gerund for spinners, e.g. "Reading src/foo.ts"."""
        return None

    @property
    def is_read_only(self) -> bool:
        return self.kind.is_read_only

    @property
    def is_concurrency_safe(self) -> bool:
        """Default: read-only tools are safe, others aren't.  Override when
        a mutating tool can actually parallelize (rare)."""
        return self.is_read_only

    def is_destructive(self, _input: InputT) -> bool:
        """不可逆操作（delete / overwrite / HTTP POST with side effects /
        git push / rm）。与 ``is_read_only`` 正交——同为 False 的 edit 工具
        可能可逆（undo-able FileEdit）也可能不可（FileWrite 覆盖）。

        ToolAuthorizer 对 destructive 用更严策略：``allow_always`` grant
        默认不覆盖 destructive 调用；每次都走 ``ask`` 更安全。

        Default: False.  覆盖的例子：
            class BashTool:
                def is_destructive(self, input):
                    return is_destructive_command(input.command)
        """
        return False

    # ── 权限信息源（非决策者）──────────────────────────────────────
    def default_risk(
        self, input: InputT, ctx: "ToolContext"
    ) -> PermissionSuggestion:
        """Tool 对"这次具体 input 有多危险"的领域判断。

        ToolAuthorizer 消费此结果 + 分层 rules + session grants 仲裁
        最终 allow / deny / ask。Tool 自己不做决策。

        例子：
            BashTool: `rm -rf /` → high + deny
                     `git status`  → low  + allow
            FileEdit: path in /etc → high + ask
                     path in cwd  → low  + allow
        """
        return PermissionSuggestion(
            risk="low", default_decision="ask", reason="no tool-specific rule"
        )

    def prepare_permission_matcher(
        self, input: InputT
    ) -> Callable[[str], bool]:
        """返回一个 matcher 闭包，给 ToolAuthorizer 的 rule engine 用。

        用户 rules 长这样：
            allowedTools: ["Bash(git *)", "FileEdit(/src/**/*.ts)"]

        ToolAuthorizer 把括号里的 pattern 传给此 matcher：
            matcher = tool.prepare_permission_matcher(input)
            if matcher("git *"): ...  # BashTool 用前缀匹 input.command
            if matcher("/src/**/*.ts"): ...  # FileEdit 用 glob 匹 input.file_path

        Tool 实现决定"pattern 怎么应用到 input"——这是 per-tool 的领域
        语义，ToolAuthorizer 无法抽象。

        Default: 空 matcher，任何 pattern 都不匹（等于 rule 不生效）。
        """
        return lambda _pattern: False

    # ── 执行生命周期 ─────────────────────────────────────────────
    async def validate_input(self, input: InputT, ctx: "ToolContext") -> None:
        """Raise ``ToolInputError`` if input is malformed.  Called BEFORE
        ToolAuthorizer.  Keep cheap — permission & hooks are the expensive
        steps and run after this."""

    @abstractmethod
    def call(
        self,
        input: InputT,
        ctx: "ToolContext",
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult[OutputT], None]:
        """Run the tool.  Yields 0+ ``ToolCallProgress`` events, then
        exactly one ``ToolCallResult`` as the final yield.

        Must honor ``ctx.cancel_event``; long-running tools should poll it
        between work units.  On cancel, raise ``asyncio.CancelledError``.
        """

    # ── LLM 负载转换 ────────────────────────────────────────────
    def to_schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            input_schema=self._cached_input_schema,   # 由 ToolRegistry 注入
            cache=self.cache,
        )

    # ── Transcript 搜索索引（客户端用）─────────────────────────────
    def extract_search_text(self, result: "ToolCallResult[OutputT]") -> str:
        """Return flat text for transcript-search indexing.

        Clients (web archive、日后的 session search) 索引历史对话时
        需要把 tool_result 的"语义文本"提取出来——不能直接拿 llm_content
        的 JSON 反序列化，也不能拿 display payload（比如 LocationsDisplay
        里全是路径没内容）。Tool 是唯一知道"这次执行有哪些文本值得被搜到"
        的权威源——对齐 Claude Code Tool.ts:599 extractSearchText。

        Default: 把 llm_content 里所有 TextContent 拼起来。覆盖例子：
          - FileRead: 返回文件内容（不含 header/truncation 提示）
          - Grep: 返回匹配行集合（不含 "found N matches" 封装）
          - AgentTool: 返回 sub-agent 的 final TextDelta 汇总
        """
        return _default_search_text(result.llm_content)
```

### 3.1.1 六种职责的字段分组

| 职责 | 谁决策 | Tool 提供的字段 |
|---|---|---|
| **执行** | Tool 自己 | `call()`、`validate_input()` |
| **调度** | Orchestrator | `is_read_only`、`is_concurrency_safe`、`interrupt_behavior` |
| **授权** | ToolAuthorizer | `is_destructive`、`default_risk()`、`prepare_permission_matcher()` |
| **渲染** | 客户端 | `user_facing_name()`、`activity_description()`、`ToolCallResult.display` |
| **transcript 搜索** | 客户端 | `extract_search_text(result)` |
| **registry 分层** | ToolManager | `should_defer`、`always_load`、`search_hint`、`aliases`、`build_input_schema()` |

Tool 是每个职责的**信息源**，但不是决策者（除执行外）。

### 3.2 为什么不用 Protocol

DeepCLI 的 `LLMProvider` 用 Protocol（结构化鸭子类型，适合单一实现），但 Tool 是**一个接口、几十个实现**。ABC 给我们：

- `@abstractmethod` 强制实现 `call()`
- 默认实现可集中（`is_read_only` / `user_facing_name` 等），不需要每个 tool 重复
- Decorator-based 注册（见 §4.1）能直接查 `Tool` 的 MRO 做运行时验证

### 3.3 输入 schema 用 JSON Schema（不是 Pydantic 直接）

Claude Code 用 Zod（`z.ZodType`）+ MCP 的 `inputJSONSchema` fallback。mustang 统一走 **JSON Schema dict**：

- LLM API 要的就是 JSON Schema，避免转换层
- MCP 工具天然是 JSON Schema
- Python 侧用 `pydantic.BaseModel.model_json_schema()` 自动生成即可，不强制 pydantic 绑定

每个 built-in tool 推荐用 pydantic 定义 input shape，然后在 `input_schema` 属性里调 `model_json_schema()`：

```python
class BashInput(BaseModel):
    command: str
    timeout_ms: int | None = None
    run_in_background: bool = False

class BashTool(Tool[BashInput, str]):
    name = "Bash"
    input_schema = BashInput.model_json_schema()
    kind = ToolKind.execute

    async def validate_input(self, input: BashInput, ctx: ToolContext) -> None:
        if len(input.command) > 32_000:
            raise ToolInputError("command too long")
```

---

## 4. Registry 设计

### 4.1 目录结构

```
kernel/tools/
├── __init__.py            # ToolManager(Subsystem) + public exports
├── tool.py                # Tool ABC、ToolCallProgress/Result、ToolInputError
├── context.py             # ToolContext 定义
├── registry.py            # ToolRegistry：核心注册表（core + deferred）
├── file_state.py          # FileStateCache（Phase 5.5.3A 的 Python 版）
├── builtin/
│   ├── __init__.py        # BUILTIN_TOOLS 列表（core 层）
│   ├── bash.py            # BashTool
│   ├── file_read.py
│   ├── file_edit.py
│   ├── file_write.py
│   ├── glob.py
│   ├── grep.py
│   ├── tool_search.py     # ToolSearchTool（deferred 解锁器）
│   └── agent.py           # AgentTool（sub-agent as tool）
└── mcp_adapter.py         # 把 MCPManager 暴露的远端 tool wrap 成 Tool 实例
```

### 4.2 ToolRegistry 接口

```python
class ToolRegistry:
    """Core + deferred 两层注册表。"""

    def register(self, tool: Tool, *, layer: Literal["core", "deferred"]) -> None: ...

    def snapshot(self, ctx: ToolContext) -> ToolSnapshot:
        """Return the tool pool visible for the upcoming LLM call.

        Filters by:
          - FlagManager (tool disabled via config → excluded)
          - Plan mode (write/edit tools excluded when active)
          - ctx.agent_whitelist (sub-agent tool scoping)

        Ordering: core tools alphabetical, then deferred (name-only stubs),
        then MCP tools alphabetical — keeps prompt-cache prefix stable
        even when MCP pool churns (mirrors Claude Code tools.ts:345-390).
        """


@dataclass(frozen=True)
class ToolSnapshot:
    schemas: list[ToolSchema]            # 送给 LLM 的 tool 定义
    lookup: dict[str, Tool]              # name → Tool，ToolExecutor 查表用
    deferred_names: set[str]             # 声明 defer_loading=True 的子集
```

### 4.3 ToolManager（Subsystem）

```python
class ToolManager(Subsystem):
    """kernel subsystem for tool registry + shared state.

    Loaded at step 5 (after ToolAuthorizer, before Session).
    """

    async def startup(self) -> None:
        # 1. 注册 FlagManager section（哪些 tool 启用）
        self._flags = self._module_table.flags.bind_section("tools", ToolFlags)

        # 2. 初始化共享组件
        self._file_state = FileStateCache()
        self._registry = ToolRegistry()

        # 3. 注册内置 tools（feature-gated）
        for tool_cls in BUILTIN_TOOLS:
            if self._flags.is_enabled(tool_cls.name):
                self._registry.register(tool_cls(), layer=tool_cls.layer)

        # 4. 挂 MCPManager 的 ready signal，远端 tool 动态进 registry
        if self._module_table.has(MCPManager):
            self._module_table.get(MCPManager).on_tools_changed.connect(self._sync_mcp)

    def snapshot_for_session(
        self, session_id: str, plan_mode: bool, agent_whitelist: set[str] | None
    ) -> ToolSnapshot: ...

    def file_state(self) -> FileStateCache:
        return self._file_state
```

### 4.4 Built-in 工具清单（Phase 1 最小集）

| Tool | kind | layer | 备注 |
|---|---|---|---|
| `Bash` | execute | core | 依赖 `ctx.tasks` 跑 `run_in_background` |
| `FileRead` | read | core | 文本 + 图片(PNG/JPEG/WebP/GIF → `ImageContent`) + PDF(PyMuPDF → PNG pages)；文本模式依赖 `ctx.file_state.record()` |
| `FileEdit` | edit | core | 依赖 `ctx.file_state.verify(path)` 防竞态 |
| `FileWrite` | edit | core | 同上 |
| `Glob` | search | core | 纯函数，无 ctx 依赖 |
| `Grep` | search | core | 纯函数 |
| `ToolSearch` | think | core | 解锁 deferred 工具的前置 |
| `Agent` (Task) | other | core | 递归调 Orchestrator（见 §6） |

Deferred 层已激活：ToolManager 按 `Tool.should_defer` 自动路由到 `deferred` 层，ToolSearchTool 解锁 deferred 工具的 schema。PlanMode 工具（EnterPlanMode / ExitPlanMode）将作为首批 deferred 消费者。

---

## 5. ToolContext —— 工具—Kernel 单一门户

### 5.1 设计动机

> 每个工具通过 `ToolContext` 访问它需要的外部资源。

ToolContext 是 Tool 唯一合法的依赖出口，禁止 Tool 直接 import Session / Hooks / ToolAuthorizer 等。

### 5.2 字段

```python
@dataclass
class ToolContext:
    # ── 运行时元数据 ─────────────────────────────────────────────
    session_id: str
    agent_depth: int                     # 0 = root agent, ≥1 = sub-agent
    agent_id: str | None                 # sub-agent 的独立 id（过滤通知）
    cwd: Path                            # 当前工作目录（可被工具临时改写）

    # ── 取消 ─────────────────────────────────────────────────────
    cancel_event: asyncio.Event          # 长跑工具轮询

    # ── Session 级共享资源（通过 module_table 注入）──────────────
    file_state: FileStateCache           # Tools 内部
    blobs: BlobStore                     # Session 子系统
    tasks: BackgroundTaskRegistry        # Session 子系统

    # ── 用于 sub-agent 递归 ──────────────────────────────────────
    spawn_subagent: Callable[
        [str, list[ContentBlock]], AsyncGenerator[OrchestratorEvent, None]
    ] | None = None

    # ── 权限透传（Tool 不做 pre-check，字段留空，未来预留）──────
    #   对齐 Claude Code：Tool 接口不持有权限 hint；真正的授权由
    #   ToolExecutor 在 call() 之前走 ToolAuthorizer.authorize() 完成。
    #   Tool 可以在 call() 内部根据业务逻辑再次问 authorizer（例：某个
    #   子操作需要二次确认），但必须通过 ToolExecutor 传入的显式 ref，
    #   不放进 ToolContext。
```

**设计要点**：

- **Context 由 Orchestrator 构造并注入**（不是 ToolManager），因为 `agent_depth` / `spawn_subagent` / `cancel_event` 这些是 per-turn 状态
- `file_state` 由 ToolManager 注入（长期共享），`blobs` / `tasks` 由 Session 注入
- Tool 对 Session 无感知，只面对 ToolContext API
- **无 `authorizer_hint`**（对齐 Claude Code `Tool.ts:500` 的 `checkPermissions` 不走 ctx）：Tool 不做 pre-check；权限决策 100% 在 ToolExecutor 层 via ToolAuthorizer

### 5.3 ContextModifier —— Tool 如何改 session-level state

Tool 改了**不能编进 tool_result 喂 LLM** 的 state（cwd、env、临时 worktree
path 等）时，返回 `ToolCallResult.context_modifier`，Orchestrator 执行完
此 tool 后 apply 它：

```python
# Orchestrator.ToolExecutor 里的伪代码
result = await run_tool(tool, input, ctx)   # 最后一次 yield 的 ToolCallResult

if result.context_modifier is not None:
    ctx = result.context_modifier(ctx)       # 替换成新 ctx
    # 通过 ACP session/update 广播给客户端（cwd 变了 UI 要跟着变）
    await broadcast_context_change(ctx)

# 下一个 tool 的 call() 看到新 ctx
```

具体例子：

| Tool | input | 返回的 context_modifier |
|---|---|---|
| `cd` / `pushd` 式工具 | `{path: "/foo"}` | `lambda c: replace(c, cwd=Path("/foo"))` |
| `EnterWorktree` | `{branch: "main"}` | `lambda c: replace(c, cwd=tmpdir, env={...})` |
| `ActivateVenv` | `{name: "project"}` | `lambda c: replace(c, env={**c.env, "PATH": ...})` |
| 绝大多数 tool（FileRead / Grep / Bash 无副作用调用）| —— | **None**（不改 ctx） |

**为什么不直接让 Tool 改 ctx**：

- **(A) 直接改 ctx 引用** (`ctx.cwd = new`)：隐式、难测、并发 tool 互相踩
- **(B) contextModifier 纯函数**：显式、幂等、可以 dry-run、单测直接断言

**并发语义**（抄 Claude Code [`toolOrchestration.ts:26-81`](../../../../../projects/claude-code-main/src/services/tools/toolOrchestration.ts)）：

- **非 concurrency-safe 批**（只 1 个 tool）：modifier 立刻 apply，下一 tool 见新 ctx
- **concurrency-safe 批**（N 个并行 tool）：modifier buffer 起来（`queuedContextModifiers[tool_use_id]`），等整批完成后**按 yield / 完成顺序**依次 apply
- **允许并发 tool 返 modifier，无运行时 check**（Claude Code 也没检查）。但 Tool 作者必须理解：同批的其它并发 tool **看不到**你的 modifier，只有下一批或串行 tool 才看得到——如果你的 modifier 想立刻影响兄弟 tool，你这个 tool 就不该声明 `is_concurrency_safe=True`

### 5.4 与 Claude Code 的 ToolUseContext 对比

Claude Code 的 `ToolUseContext` 里有：
- `tools`, `mcpClients`, `handleElicitation`, `getAppState`, `setAppState`, `appendSystemPrompt`, `contentReplacementState`, `queryTracking`, `abortController`, `renderedSystemPrompt`…

mustang 的 ToolContext **刻意窄**：

- `tools` / `mcpClients` / `getAppState` 等 registry 访问 → Tool 不该知道 registry
- `renderedSystemPrompt` → 只有 Orchestrator 需要
- `contentReplacementState` → Orchestrator compression 层负责
- `abortController` → 简化为 `cancel_event`（Python async 原生）

**结果**：ToolContext 字段从 Claude Code 的 ~20 个缩到 ~10 个，每个都有明确合法使用场景。Context 改动走 `ToolCallResult.context_modifier`（显式、纯函数），不让 Tool 直接 mutate。

---

## 6. 执行流水（Orchestrator.ToolExecutor 的改写）

当前 [`src/kernel/kernel/orchestrator/tool_executor.py`](../../../src/kernel/kernel/orchestrator/tool_executor.py) 是 Phase 1 stub。真实实现分四段：

```python
async def _run_impl(self, tool_calls, on_permission, plan_mode):
    snapshot = self._tool_manager.snapshot_for_session(
        session_id=self._session_id,
        plan_mode=plan_mode,
        agent_whitelist=self._agent_whitelist,
    )
    ctx = self._build_tool_context()       # cwd、env、file_state、blobs、tasks…

    # 1. 分批：concurrency-safe 可并行；其它串行
    batches = partition_tool_calls(tool_calls, snapshot.lookup)

    for batch in batches:
        # 2. 每批内部并行跑（受 max_concurrency 限制，默认 10）
        results = await asyncio.gather(*[
            self._run_one(tc, snapshot.lookup[tc.name], on_permission, ctx)
            for tc in batch
        ])

        # 3. Apply context modifiers（concurrency-safe 批 buffer 后统一 apply）
        for event, result in results:
            yield event, result
            if result and result.context_modifier:
                ctx = result.context_modifier(ctx)
                await self._broadcast_context_change(ctx)
```

`_run_one(tool_call, tool, on_permission, ctx)` 里的**七步**（对齐 Claude Code [`permissions.ts:1158-1224`](../../../../../projects/claude-code-main/src/utils/permissions/permissions.ts)）：

```
1. validate_input(input, ctx)
       → ToolInputError → yield ToolCallError，return

2. ToolAuthorizer.authorize(tool, input, ctx):
   内部顺序（对齐 Claude Code permissions.ts:1158-1224 的 1a/1b/1c）：
      a. session grant cache 命中 allow_always？→ allow（短路，不调 Tool）
             Claude Code 等价：hasPermissionsToUseTool 的 forceDecision 参数
      b. deny rule 命中？→ deny（短路）
      c. ask rule 命中？→ 记下"本次必须 ask"
      d. 调用 tool.default_risk(input, ctx)         # 信息源
         调用 tool.prepare_permission_matcher(input) # 信息源
         调用 tool.is_destructive(input)             # 信息源
            —— 三个 Tool 方法无条件调，纯函数成本可忽略
      e. 综合优先级：deny rule > default_risk.deny > (ask rule ∨ default_risk.ask)
         > allow rule > default_risk.allow > fallback ask
      f. 最终 ask → on_permission(PermissionRequest) await 用户：
            → "reject"       → yield ToolCallError，return
            → "allow_once"   → 仅本次
            → "allow_always" → authorizer.grant(tool, input, ctx) 持久化
                               （对齐 Claude Code PermissionUpdate.ts:349：grant 层
                                无脑持久化；is_destructive 工具的 allow_always 按钮
                                在 RuleEngine 构造 PermissionAsk.suggestions 时已被
                                过滤，UI 上根本看不到这个选项 —— 见 ToolAuthorizer §3.3）

3. Hooks.fire("pre_tool_use", tool=..., input=..., ctx=...)
       → 返回改写后的 input（hook 可以 rewrite、insert）
       → 可 block → yield ToolCallError，return
       ⚠️ **只在 authorize 返 allow（或用户 approve 的 ask）时 fire**。
       authorize 直接 deny 时 skip。对齐 Claude Code toolExecution.ts
       ——权限 deny 路径不进 pre-tool-use / post-tool-use hooks。

4. yield ToolCallStart(id=tc.id, title=tool.user_facing_name(input),
                       kind=tool.kind)

5. async for event in tool.call(input, ctx):
     if isinstance(event, ToolCallProgress):
         yield ToolCallProgress(id=tc.id, content=event.content)
     elif isinstance(event, ToolCallResult):
         final = event

6. Hooks.fire("post_tool_use", result=final, ctx=ctx)
       → hook 可以改写 final.llm_content（MCP 输出注解等）

7. yield ToolCallResult(id=tc.id, content=final.llm_content)
   yield ToolCallDisplay(id=tc.id, payload=final.display)  # 见 §6.2
   return (events, final)                 # final.context_modifier 给上层 apply
```

### 6.2 Display payload 的传递

`ToolCallResult.display`（`ToolDisplayPayload` union）**不**走 LLM 回路，它
沿事件链走到客户端。Orchestrator 在 yield `ToolCallResult` 之后额外 yield
一个 `ToolCallDisplay` 事件：

```python
@dataclass(frozen=True)
class ToolCallDisplay:
    id: str                               # 对应 ToolCallStart.id
    payload: ToolDisplayPayload           # TextDisplay / DiffDisplay / …
```

Session 层把 `ToolCallDisplay` 映射到 ACP `session/update` 通知发给客户端。
现有的 `ToolCallDiff` / `ToolCallLocations` 事件可视作 `DiffDisplay` /
`LocationsDisplay` 的特化——落地时统一到 `ToolCallDisplay` 框架下即可，
避免类型碎片化。

### 6.3 `StreamingToolExecutor` —— 一次设计到位

Claude Code 的 [`StreamingToolExecutor.ts`](../../../../../projects/claude-code-main/src/services/tools/StreamingToolExecutor.ts)
能在 LLM 还在 stream `tool_use` block 时就启动工具执行（安全并发工具
提前跑，节省 TTFB）。这**不是一个可以后期加的优化**——它直接决定
`ToolExecutor` 的对外接口形状：

| 问题 | 非 streaming 接口 | streaming 接口 |
|---|---|---|
| 何时能给 tool_use | 整次 LLM stream 结束后批量传 | stream 中逐个 `add_tool()` |
| 何时调 `Tool.call` | `run(tool_calls_list)` 里 | 收到 tool_use 就可能立刻启动 |
| Orchestrator 调用点 | 一次 `await ... .run(...)` | `add_tool()` + `finalize_stream()` + `async for result in ...` |

**phase 1 不跑 streaming**，但 **ToolExecutor 的接口必须从头就是 streaming
形状**，否则 phase 2 就要重写 Orchestrator 的事件循环和 AgentTool 的
事件转发逻辑。

#### 6.3.1 接口

```python
class ToolExecutor:
    """Streaming-shaped executor: tool_use 到达就 add，最终统一 finalize。"""

    def __init__(
        self,
        tool_manager: ToolManager,
        authorizer: ToolAuthorizer,
        hooks: HookManager | None,
        ctx: ToolContext,
        *,
        streaming: bool,      # phase 1 = False, phase 2 = True
        max_concurrency: int = 10,
    ) -> None: ...

    def add_tool(self, tool_use: ToolUseContent) -> None:
        """收到一个 tool_use block。
          - streaming=False: 入队列，等 finalize_stream 后统一分批
          - streaming=True:  立刻判定 is_concurrency_safe；
                             若本轮尚无"非 safe 在跑"，启动；否则 queue
        """

    def finalize_stream(self) -> None:
        """LLM stream 结束，不会再有 add_tool()。
          - streaming=False: 触发 partition + dispatch
          - streaming=True:  放行所有 queue 中的非 safe tool（之前被阻塞的）
        """

    async def results(
        self,
    ) -> AsyncGenerator[tuple[OrchestratorEvent, ToolCallResult | None], None]:
        """按完成顺序 yield 所有 tool 的事件流，同时给上层 batch loop
        提供 final ToolCallResult 以便 apply context_modifier。"""

    def discard(self) -> None:
        """Fallback / abort 时调用。取消所有进行中的 tool，丢弃所有
        buffered 事件。对齐 Claude Code StreamingToolExecutor.discard()。"""
```

#### 6.3.2 Orchestrator 侧的使用

```python
# StandardOrchestrator._run_query 的 tool 执行段
executor = ToolExecutor(..., streaming=self._config.streaming_tools)

# (a) streaming=False 路径 —— phase 1 默认
async for chunk in provider.stream(...):
    match chunk:
        case ToolUseChunk() as tc:
            tool_uses.append(ToolUseContent(...))    # 暂存
        # 其它 chunk 照常处理

# stream 结束后
for tu in tool_uses:
    executor.add_tool(tu)
executor.finalize_stream()

async for event, result in executor.results():
    yield event
    if result and result.context_modifier:
        ctx = result.context_modifier(ctx)
        # ...

# (b) streaming=True 路径 —— phase 2
async for chunk in provider.stream(...):
    match chunk:
        case ToolUseChunk() as tc:
            executor.add_tool(ToolUseContent(...))   # 立即进 executor
        # 其它 chunk 照常处理
executor.finalize_stream()
# 消费 executor.results() 路径同上
```

**关键不变量**：两条路径用同一套 `ToolExecutor` 接口，Orchestrator 的
外层事件循环结构**完全一样**。phase 1 到 phase 2 只翻一个 flag：
`streaming_tools = True`，Orchestrator 主循环调 `add_tool` 的时机从"stream
结束后"改为"stream 中"。没有接口变更、没有 AgentTool 重写。

#### 6.3.3 关键实现细节

来自 Claude Code [`StreamingToolExecutor.ts`](../../../../../projects/claude-code-main/src/services/tools/StreamingToolExecutor.ts)：

1. **队列与执行中的区分**：`_queue` / `_executing` 两个集合
2. **can_execute_tool(is_safe)** 规则：
   - 若当前 `_executing` 全是 safe 且 `add_tool` 来的也是 safe → 立刻启动
   - 若当前 `_executing` 有非 safe → 等它完成（非 safe 只允许孤身执行）
   - 若 `add_tool` 来的是非 safe → 等当前批 safe 全部完成
3. **按完成顺序 yield 结果**，不是按 add 顺序（Claude Code 同）
4. **max_concurrency 限流**：信号量对 safe 并发数盖顶，超出入队

### 6.4 AgentTool（sub-agent 递归）

依照 Claude Code 的 [`runAgent.ts:748`](../../../../../projects/claude-code-main/src/tools/AgentTool/runAgent.ts#L748) 模式：

```python
class AgentTool(Tool[AgentInput, str]):
    name = "Task"
    kind = ToolKind.other

    async def call(self, input, ctx):
        if ctx.spawn_subagent is None:
            yield ToolCallResult(data="", content=[TextContent(
                text="Sub-agent dispatch not available")])
            return

        parts: list[str] = []
        async for event in ctx.spawn_subagent(input.subagent_type, input.prompt):
            if isinstance(event, TextDelta):
                parts.append(event.content)
            # ... 其它事件忽略或汇报 progress

        yield ToolCallResult(
            data="".join(parts),
            content=[TextContent(text="".join(parts))],
        )
```

`spawn_subagent` 由 Orchestrator 构造：`StandardOrchestrator` 实例在被 AgentTool 调用时递归地起一个新 `StandardOrchestrator`（共享 deps，`depth += 1`），跑完 `query()` 把最终 TextDelta 汇总作为 tool_result 返回。

---

## 7. FileStateCache

pre-kernel era Phase 5.5.3A 已实装（TS 版）；Python 版需重实装。最小接口：

```python
class FileStateCache:
    def record(self, path: Path, mtime: float, content_hash: str) -> None:
        """Record what FileRead saw — enables FileEdit to detect external mutation."""

    def verify(self, path: Path) -> FileState | None:
        """Return the recorded state for path (or None if not seen).
        FileEdit / FileWrite compare current mtime+hash against this before writing."""

    def invalidate(self, path: Path) -> None:
        """Called after a successful write so the next read doesn't false-positive."""
```

**职责边界**：FileStateCache **只存状态**，不做 policy。是否"外部改了就拒绝写"由具体工具决定（FileEdit 通常拒绝；FileWrite 可能警告后继续）。

---

## 8. 和 Claude Code 的关键取舍对比

| 关注点 | Claude Code | mustang | 理由 |
|---|---|---|---|
| 权限决策 | Tool.checkPermissions + canUseTool 内部决定 | Tool.default_risk + prepare_permission_matcher → **ToolAuthorizer 仲裁** | 决策角色分离；Tool 只提供信息 |
| 权限信息源 | Tool.checkPermissions / preparePermissionMatcher | **同在 Tool 上保留**（名字改为 `default_risk` / `prepare_permission_matcher`）| 领域知识（path glob vs bash prefix）只有 Tool 懂 |
| 渲染 | tool.renderToolResultMessage → ReactNode | tool 产出 `ToolDisplayPayload` union（TextDisplay/DiffDisplay/…），客户端按类型路由 | kernel 不能返 ReactNode；但 Tool 仍是渲染结构的信息源 |
| isDestructive | 独立 bool 字段 | **保留独立 bool 字段** | 与 ToolKind 正交（edit 可能可逆；execute 可能无害） |
| contextModifier | `ToolResult.contextModifier` 闭包 | **保留**，在 `ToolCallResult.context_modifier` | cwd/env 改变必须显式传给 Orchestrator，否则下一 tool 看不到新 state |
| 并发 | StreamingToolExecutor 边 stream 边跑 | ✅ **已实装** `ToolExecutor(streaming=bool)` + `partition_tool_calls` 并行分批 + `asyncio.Queue` 事件合并 + `asyncio.Lock` 序列化 permission prompt。`streaming_tools=False`（默认）等 stream 结束再分批并行；`True` 边 stream 边 `add_tool`。`OrchestratorConfig.streaming_tools` 控制。 | |
| Result budget | applyToolResultBudget 每轮跑 | Phase 2+ 加，Tool 上报 `max_result_size_chars` | 先打通再优化 |
| Deferred | `defer_loading=True` + ToolSearch | ✅ **已实装** `ToolSearchTool` + `ToolRegistry.promote()` + `should_defer` 自动分层 + deferred listing 注入 | EnterPlanMode / ExitPlanMode 为首批 deferred 消费者 |
| MCP | 同 Tool 接口，namespaced name | 同 | 直接抄 |
| Sub-agent | AgentTool 递归 query() | AgentTool 递归 `StandardOrchestrator` | 同构，Python 等价 |
| Tool 注册 | 静态数组 + feature() 宏 | `BUILTIN_TOOLS` 列表 + FlagManager gate | Python 没有 bundle 裁剪，靠运行时 flag |
| Context | 大杂烩 ToolUseContext | 窄 ToolContext + 显式 context_modifier | Session/Hooks/Authorizer 已独立 |
| lazy schema | `lazySchema()` 延迟构造 | 直接 `Input.model_json_schema()` at import | Python startup 成本不是问题 |
| extractSearchText | 有（transcript 搜索） | **不做** | 客户端 feature，kernel 不管 |

---

## 9. 与其他子系统的接线

### 9.1 依赖（启动序）

```
Flag (0) → Config (1) → ToolAuthorizer (3) → Tools (5) → Session (10)
```

- **ToolAuthorizer 必须早于 Tools**：不是因为 Tool 自己需要 authorizer（已确认 Tool 不做 pre-check），而是为了让 Session（step 10）构造 `OrchestratorDeps` 时能从 module_table 同时取 `ToolManager` 和 `ToolAuthorizer` —— 两者都必须在 Session 启动前就位
- **Session 晚于 Tools**：Session 构造 `ToolContext` 时把 `BlobStore` / `BackgroundTaskRegistry` 填进去；构造 `OrchestratorDeps` 时注入 `tool_source` + `authorizer` 两个 ref

### 9.2 OrchestratorDeps 注入

```python
# SessionManager.create_session() 里：
deps = OrchestratorDeps(
    provider=self._module_table.get(LLMManager),
    tool_source=self._module_table.get(ToolManager),      # <-- 此处
    authorizer=self._module_table.get(ToolAuthorizer),    # <-- 新增字段
    hooks=self._module_table.get(HookManager) if has_hooks else None,
    memory=...,
    skills=...,
)
```

`tool_source` + `authorizer` 现在是 `Any` stub。落地后改为：

```python
tool_source: ToolManager | None = field(default=None)
authorizer: ToolAuthorizer | None = field(default=None)
```

`authorizer=None` 时 ToolExecutor fallback 到 "allow-all"（对齐
tool-authorizer.md § 15.2 的降级策略）。

Orchestrator 调用：

```python
snapshot = deps.tool_source.snapshot_for_session(
    session_id=self._session_id,
    plan_mode=self._plan_mode,
    agent_whitelist=None,  # sub-agent 会传具体 whitelist
)
# 用 snapshot.schemas 给 provider.stream，snapshot.lookup 给 ToolExecutor
```

### 9.3 Hooks 挂接

Tools 不直接调 hooks。由 Orchestrator 的 ToolExecutor 在 `pre_tool_use` / `post_tool_use` 两个点 fire，避免每个 tool 重复样板。

---

## 10. 实施顺序

| 阶段 | 工作 | 依赖 |
|---|---|---|
| **1a** | `tool.py`：Tool ABC + `ToolCallProgress` / `ToolCallResult` / `ToolDisplayPayload` union / `PermissionSuggestion` / `ContextModifier` / `build_input_schema` / `extract_search_text` 默认实现 | 无 |
| **1b** | `context.py`（ToolContext）+ `registry.py`（ToolRegistry + ToolSnapshot + `matches_name()` helper） | 1a |
| **1c** | 替换空壳 `ToolManager(Subsystem)`：flag section、startup 装载 `BUILTIN_TOOLS`、调 `build_input_schema(module_table)` 缓存每 tool 的 schema、`snapshot_for_session` 实装 | 1b |
| **1d** | `FileStateCache`（Python 版）+ 单测 | 无 |
| **1e** | 6 个核心 built-in：`Bash` / `FileRead` / `FileEdit` / `FileWrite` / `Glob` / `Grep`。先不接 `ctx.tasks` / `ctx.blobs`；`default_risk` 覆盖为具体判断（Bash 做 argv 分类；FileEdit/FileWrite 检查路径是否在 cwd 外）；`interrupt_behavior` 按 tool 声明（Bash = cancel，纯计算 = block） | 1a-d |
| **2a** | ✅ **已实装** **ToolExecutor 定型**（streaming 形状）：`add_tool` / `finalize_stream` / `results()` / `discard()` 接口齐备。`partition_tool_calls` 分批（连续 safe 合批并行，unsafe 单独串行）；`asyncio.Queue` per-tool 事件流 + `_merge_queues` 按完成顺序 yield；`asyncio.Semaphore(max_concurrency)` 限流；`asyncio.Lock` 序列化 `on_permission`；`discard()` 设 cancel_event + cancel task。`run()` 保留为向后兼容 wrapper。 | 1a-e |
| **2b** | ✅ **已实装** Orchestrator 改为每轮实例化 `ToolExecutor(streaming=config.streaming_tools)`；`streaming_tools=False` 时 stream 结束后 `add_tool` + `finalize_stream`；`True` 时 stream 中 `ToolUseChunk` 到达即 `add_tool`。`OrchestratorConfig` + `OrchestratorConfigPatch` 新增 `streaming_tools: bool` 字段。 | 2a |
| **2c** | `ToolCallDisplay` 事件 + Session 层映射到 ACP `session/update`；把旧的 `ToolCallDiff` / `ToolCallLocations` 迁移到 `ToolCallDisplay` 统一框架 | 2a + session 层改动 |
| **3** | ✅ **已实装** `AgentTool` + `spawn_subagent` closure 注入 ToolContext + SubAgentStart/SubAgentEnd 事件 + TaskRegistry per-session + TodoWrite/TaskOutput/TaskStop 工具 | 2 + Orchestrator sub-agent 重构 |
| **4** | ✅ **已实装** `ToolSearchTool` + deferred 层 registry 分区：`ToolRegistry.promote()` + `ToolSnapshot.deferred_listing` + 3 种查询模式（select/+prefix/freetext）+ Orchestrator `<system-reminder>` 注入 + ToolManager 自动按 `should_defer` 分层。+28 unit + 3 e2e tests。 | 1c |
| **5** | ✅ **已实装** **Streaming execution 路径**：Orchestrator 已支持 `streaming_tools=True`（stream 中 `ToolUseChunk` 到达即 `add_tool`）。默认 `False`，可通过 `OrchestratorConfig.streaming_tools` 开启。 | 2a |
| **6** | ✅ **已实装** `MCPAdapter` + MCPManager 接入（4 种传输 + health monitor + reconnect）| MCP 子系统单独落地，设计见 [mcp.md](mcp.md) |

前三阶段 ≈ 5 天工作量（streaming 接口上到 phase 1 多半天；lazy schema + extract_search_text 基本不增量）；MCP 和 streaming activation 可独立、并行推进。

---

## 11. Claude Code 各特性的落地策略

把原"显式延后"拆成三类：**设计时必须定的契约**（即便 phase 1 不激活也
要写进接口）、**在对应子系统里定的**（属 ToolAuthorizer / Session / MCP
的问题）、**真不做的**。每项都要明确归属，避免后期加东西时接口翻车。

### 11.1 设计时必须定的契约（即便 phase 1 不激活）

| 特性 | Claude Code | mustang 当前设计 | phase 1 激活？ |
|---|---|---|---|
| **Streaming tool execution** | `StreamingToolExecutor` | ✅ **已实装** `ToolExecutor(streaming=bool)`，两路径共用接口 (§ 6.3)。`add_tool` / `finalize_stream` / `results()` / `discard()` 齐备；`partition_tool_calls` 分批 + `asyncio.Queue` 并行事件合并 + `asyncio.Lock` 序列化 permission。Orchestrator 已集成两条路径（`streaming_tools` config flag）。 | ✅ `streaming=False` 已激活并行分批；`True` 路径已实装待开启 |
| **Lazy input schema** | `lazySchema(() => z.object(...))` | `Tool.build_input_schema(module_table)` classmethod (§ 3.1) | ✅ 直接上；默认走 `input_schema` 类属性 |
| **`extract_search_text`** | `Tool.ts:599` | `Tool.extract_search_text(result) -> str` (§ 3.1) | ✅ 默认实现拼 TextContent；覆盖在具体 tool 里按需 |
| **`interruptBehavior` = `cancel`** | 区分 cancel / block | `Tool.interrupt_behavior: Literal[...]` 字段已在 | ✅ 字段必须在；实装时 Bash/Agent 声明 `cancel`，ToolExecutor 收到 CancelledError 时按字段决定是否 await tool 收尾 |
| **`aliases`** | 用于 rename 兼容 + rule matching | `aliases: tuple[str, ...]` + `ToolRegistry.matches_name()` + `ToolAuthorizer.toolMatchesRule()` 共用 helper (§ 12.4) | ✅ 字段保留，helper 实装 |

### 11.2 在对应子系统里定的

这些 Claude Code 特性不属于 ToolManager 的设计面，在相关子系统文档里决定——
ToolManager 要做的是**在接口上预留好数据通道**。

| 特性 | 归属子系统 | ToolManager 侧需要做的 |
|---|---|---|
| **Speculative bash classifier**（输入 Bash 命令时后台先跑分类器，2s race timeout，high-confidence 安全命令自动 approve 不弹 UI） | ToolAuthorizer | **职责拆分（对齐 Claude Code bashPermissions.ts + dangerousPatterns.ts）**：1) BashTool 的 `default_risk(input, ctx)` 独占 argv 解析 + safe/dangerous 清单（`dangerousPatterns.ts` 等价物），返回 low/medium/high + allow/ask/deny；2) ToolAuthorizer 的 BashClassifier 组件**只**含 LLMJudge（对齐 `bashClassifier.ts`），当 `default_risk` 返 ask 且 `tool.name == "Bash"` 时由 authorizer 用 LLM 做二次仲裁；3) 触发方式是 tool name 字符串相等（抄 CC，不用 isinstance / class flag），name constant 定义在 `kernel/tool_authz/constants.py` |
| **prompt cache 稳定前缀**（built-in 字母序 + MCP 字母序）| ToolManager 内部 | `ToolRegistry.snapshot()` 保证排序稳定 (§ 4.2 已写) |
| **`deniedTools: ["mcp__slack"]` server-level deny** | ToolAuthorizer | ToolManager 保证 MCP 工具名是 `mcp__<server>__<tool>` 格式；ToolAuthorizer 的 rule parser 识别 server-only rule (§ 12.5) |
| **Non-interactive 降级（background agent 不能弹 UI）** | ToolAuthorizer | ToolContext 里把 `ask` 转 `deny` 的判断走 ToolAuthorizer 的 `shouldAvoidPermissionPrompts` 字段，ToolManager 不参与 |
| **Large tool_result 溢写（>200KB）** | Orchestrator（compression 层）+ Session（BlobStore）| Tool 上报 `max_result_size_chars`；Orchestrator 做聚合 budget；Session 提供 BlobStore 落盘 |
| **`PostToolUse` hook 改写 MCP 输出** | HookManager | ToolExecutor 在 step 6 fire `post_tool_use` 给 hook 一次 rewrite `llm_content` 的机会 (§ 6 已写) |

### 11.3 真不做的（不进 kernel 契约）

| 特性 | 不做原因 |
|---|---|
| `renderToolResultMessage` 返 `React.ReactNode` | kernel 不能返 React；已用 `ToolDisplayPayload` union 替代 |
| `extractSearchText` 跑 transcript search 的索引器 | 索引是客户端 feature（web 档案库自己 ES）；Tool 只提供文本，不提供索引 |
| Claude Code 的 `feature('FLAG')` bundle-time dead code elimination | Python 无等价；靠运行时 FlagManager gate `BUILTIN_TOOLS` 列表即可，成本可忽略 |
| Anthropic SDK 的 `ToolResultBlockParam` 专用类型 | mustang 已有 `ToolResultContent`，provider 适配层做转换，Tool 只管产 `list[ContentBlock]` |

---

## 12. 设计决策（对齐 Claude Code main 后的结论）

以下问题在 Claude Code 源码里有明确答案，直接照搬 / 对齐。

### 12.1 `ToolCallResult.data` —— **保留，不是死字段** ✅

`data` 在 Claude Code 里有两种真实消费路径，不能砍：

1. **Telemetry / analytics**：[`toolExecution.ts:1227-1293`](../../../../../projects/claude-code-main/src/services/tools/toolExecution.ts) 按工具类别抽字段（FileEdit 的 `diff`、FileRead 的 `content`、Bash 的 `stdout`）作为 OTel span 事件
2. **Tool 作为调用方消费 data**：[`AgentTool.tsx:309`](../../../../../projects/claude-code-main/src/tools/AgentTool/AgentTool.tsx#L309) 把 sub-agent 的 `result.data` 展开进自己的返回值 `TeammateSpawnedOutput` ——**sub-agent 的结构化输出 ≠ 喂给 parent LLM 的 tool_result**

**mustang 保留 `data` 字段**。使用约定：
- `llm_content`：最终喂 LLM 的 `list[ContentBlock]`
- `display`：喂客户端的 `ToolDisplayPayload`
- `data`：Tool 作者自己的结构化输出。默认**不被 Orchestrator 读**，仅供上层 Tool 消费（如 AgentTool）或 logging hook 读

### 12.2 `ToolDisplayPayload` union —— **mustang 原创，Claude Code 无对应** ⚠️ 仍是开放

Claude Code 直接返 `React.ReactNode`，没有抽象的 payload 类型——因为前端和引擎同进程。mustang 是 kernel + 多前端，**必须**抽象，所以这是我们独有的问题。

**取舍**：
- 保留 payload union 作为 ACP 契约（客户端依此实现渲染）
- 采用"最小集 + RawBlocks 兜底"策略：phase 1 只定 `TextDisplay` / `DiffDisplay` / `LocationsDisplay` / `FileDisplay` / `RawBlocks`
- Bash 实时进度用 `ToolCallProgress` 事件流（已有），**不**新增 `ProgressLogDisplay`
- 图片 / 表格等需求到实际 built-in 有产出时再加类型

**仍开放**：union 何时加新类型，由实装时第一个触发需求的 tool 驱动。

### 12.3 `description` —— **Tool 里保留 plain string，不是 markdown** ✅

[`FileReadTool/prompt.ts:12`](../../../../../projects/claude-code-main/src/tools/FileReadTool/prompt.ts#L12) `DESCRIPTION = 'Read a file from the local filesystem.'` ——纯文本常量。

Claude Code 的 [`Tool.ts:386-388`](../../../../../projects/claude-code-main/src/Tool.ts#L386) 签名 `description(input, options): Promise<string | ReactNode>` 是方法，支持**动态**（基于 input 生成 permission UI 文案），但大部分 built-in 工具用静态常量。

**mustang**：`description: str` 类属性（静态），用于 `to_schema()`。以后若需要 per-input 动态文案（permission UI 展示），再加独立方法 `describe_call(input) -> str`，不污染 `description` 本身。

### 12.4 Tool alias —— **必备，同时服务重命名 + rule matching** ✅

[`Tool.ts:348-360`](../../../../../projects/claude-code-main/src/Tool.ts#L348) `toolMatchesName` 同时查 primary name 和 aliases；[`permissions.ts:238-268`](../../../../../projects/claude-code-main/src/utils/permissions/permissions.ts) 的 `toolMatchesRule` 用同一套逻辑——**aliased tool 自动继承旧名字下的 permission rules**，否则 rename 会默默破坏用户配置。

**mustang**：保留 `aliases: tuple[str, ...]`。`ToolRegistry.lookup` 和 `ToolAuthorizer.toolMatchesRule` 都走同一个 `matches_name()` helper。phase 1 built-in 暂无 alias 需求，但接口先留。

### 12.5 Server-level MCP deny 是真实 feature ✅

[`permissions.ts:258-268`](../../../../../projects/claude-code-main/src/utils/permissions/permissions.ts) 里 rule `mcp__slack`（`toolName is undefined`）匹配所有 `mcp__slack__*` 工具；[`tools.ts:262-269`](../../../../../projects/claude-code-main/src/tools.ts#L262) 的 `filterToolsByDenyRules` 在 tool pool 组装阶段就剥离——**LLM 看都看不到**。

**mustang 归属**：
- Tools 子系统：MCP 工具命名必须是 `mcp__<server>__<tool>` 格式
- ToolAuthorizer：rule 解析要识别"只有 server 段、没有 tool 段"的 rule，匹配该 server 下所有 tool
- **filter 时机**：和 Claude Code 一致，在 `snapshot_for_session` 返回给 Orchestrator 之前就 filter 掉，不让 LLM 看到

### 12.6 Concurrency-safe + context_modifier —— **允许，但延迟到批末统一 apply，无硬约束** ✅

[`toolOrchestration.ts:31-62`](../../../../../projects/claude-code-main/src/services/tools/toolOrchestration.ts) 的并发路径把 modifier 收集到 `queuedContextModifiers[toolUseID]`，整批完成后统一按 yield 顺序 apply（line 54-62）；串行路径立即 apply（line 141）。Claude Code **没有**禁止并发 tool 返 modifier 的运行时检查。

**mustang 采用同样策略**：
- 允许并发 tool 返 `context_modifier`
- Orchestrator 的批内 buffer → 批末按完成顺序 apply
- **不加运行时 check**（跟 Claude Code 一致）；文档里说明并发 modifier "延迟生效、同批其它 tool 看不到"，让 Tool 作者自己判断是否合理
- 若实装中出现 bug 再加 lint-style warning

§ 5.3 的"约定：concurrency-safe 的 tool 不应该返回 context_modifier"应改为"并发 tool 可以返，但 modifier 延迟到批末 apply，同批兄弟 tool 看不到"——见 § 5.3 修订。

### 12.7 `default_risk` 调用时机 —— **每次调用无条件调，放在 deny-rule / ask-rule 之后** ✅

Claude Code [`permissions.ts:1158-1224`](../../../../../projects/claude-code-main/src/utils/permissions/permissions.ts) 的 `hasPermissionsToUseToolInner`：
- **1a** deny rule → match 就立即 deny
- **1b** ask rule → match 就走 ask
- **1c** 无条件调 `tool.checkPermissions(input)` （line 1216）

关键：`checkPermissions` **每次**都调，**不缓存**。allow_always grants 通过 `hasPermissionsToUseTool` 的 `forceDecision` 参数短路整个流程（[`useCanUseTool.tsx:37`](../../../../../projects/claude-code-main/src/hooks/useCanUseTool.tsx#L37)），但这不算"绕过 checkPermissions"——是**在更外层**决定根本不进入这个函数。

**mustang 对齐**：

```
ToolExecutor._run_one:
  1. validate_input
  2. ToolAuthorizer.authorize(tool_name, ctx):
      a. deny rule match?  → deny return
      b. ask rule match?   → ask（走 on_permission）
      c. 调 tool.default_risk(input, ctx) —— 无条件
      d. 调 tool.is_destructive(input)
      e. 综合（deny rule > ask rule > default_risk.deny > default_risk.ask > allow）
      f. 最终 ask → on_permission → 如果 allow_always 且非 destructive → 写 session grant
  3. pre_tool_use hook
  ...
```

`default_risk` 总是被调用，即便结果被更高优先级的 rule 覆盖——成本可忽略（纯函数，Tool 作者会把它写成 O(1) 判断）。**session grant cache 是在更外层的 ToolAuthorizer 内部**，命中时直接返回 allow，不进入 `authorize()` 主体——跟 Claude Code 的 `forceDecision` 等价。

---

## 13. 真正仍开放的问题

Claude Code 源码里无直接答案、需要 mustang 自己决定的：

1. **sub-agent 的 `ToolUseContext` 克隆策略**：Claude Code 的 fork agent 会 clone 父 `contentReplacementState` 保 cache。mustang 的 ToolContext 是否也要 clone file_state？cwd/env 继承？这些属 AgentTool 设计细节，实装到 §10 第 3 阶段时再定。

---

## 延伸阅读

- Tool interface 具体字段参考 Claude Code [`src/Tool.ts`](../../../../../projects/claude-code-main/src/Tool.ts) L362–695
- Registry 组装模式参考 [`src/tools.ts`](../../../../../projects/claude-code-main/src/tools.ts) L158–390
- 执行流水参考 [`src/services/tools/toolOrchestration.ts`](../../../../../projects/claude-code-main/src/services/tools/toolOrchestration.ts)
- AgentTool 递归模式参考 [`src/tools/AgentTool/runAgent.ts`](../../../../../projects/claude-code-main/src/tools/AgentTool/runAgent.ts)
- 与 orchestrator 的结合点参考 [query loop walkthrough § 6](../../reference/claude-code-query-loop-walkthrough.md)


---

## Appendix: WebFetchTool + WebSearchTool Design

# Web Tools — WebFetchTool + WebSearchTool 设计

Status: **pending**

> 前置阅读：
> - Tool ABC：[tool-manager.md](landed/tool-manager.md) § 3
> - ToolContext：[tool-manager.md](landed/tool-manager.md) § 5
> - 参考实现（archived daemon）：`archive/daemon/daemon/extensions/tools/builtin/`
> - Lessons learned：[../lessons-learned.md](../lessons-learned.md) § "web_fetch anti-bot fallback"

---

## 1. 设计目标

填补 [claude-code-coverage.md](../../reference/claude-code-coverage.md) 标记的 ❌ web 工具缺口。
提供两个工具：

| 工具 | 职责 |
|------|------|
| **WebFetchTool** | 抓取 URL 内容（HTML→Markdown、JSON API、二进制文件） |
| **WebSearchTool** | 搜索引擎查询，返回 title + URL + snippet |

两者都是 **deferred tools**（`should_defer = True`），通过 ToolSearchTool 按需加载。

**核心设计原则**：

1. 支持 OpenClaw 和 Hermes 的所有 backend，通过统一 ABC 接入
2. **全部 backend 只用 httpx 直调 REST API，不引入任何第三方 SDK**
3. 最终用一个零外部依赖方案兜底（httpx+html2text / DuckDuckGo）
4. 唯一新增必须依赖：`html2text`；唯一可选依赖：`playwright`、`readability-lxml`（本地处理用，非 API 客户端）

**2026-05-05 implementation note**：WebFetchTool 已整合 OpenClaw / Hermes
里对抓取成功率最有用的部分：本地 `httpx` backend 使用浏览器级
`User-Agent` / `Accept-Language` / `text/markdown` 优先请求；HTTP 4xx/5xx
作为错误结果而不是成功页面；JSON 会 pretty-print；二进制响应会明确拒绝；
fallback 层缓存成功结果；Firecrawl REST backend 支持 endpoint URL、
`maxAge`、`proxy`、`storeInCache` 和 failed-payload 归一化；Tool 输出带
`title` / `truncated` / `raw_length` / `cached` 元数据。默认 UA 不绑定具体
Chrome 版本；需要模拟特定当前浏览器时，用 `DEEPCLI_WEB_FETCH_USER_AGENT`
覆盖。

---

## 2. 三家实现复盘

### 2.1 Claude Code（TypeScript）— 参考安全模型，不参考 backend

Claude Code 的 WebFetch 用自己的 httpx + Turndown 做内容抓取，
WebSearch 委托 Anthropic 的 `web_search_20250305` server tool。
两者都不走第三方 backend。

**采纳**：域名级权限、预批准域名列表、逐跳重定向 SSRF 验证、HTTP→HTTPS 升级、
外部内容截断策略、权限 matcher 设计。

**不采纳**：Anthropic domain_info 预检 API（我们没有）、Haiku 摘要（不额外调 LLM）。

### 2.2 OpenClaw（TypeScript）— Fetch 2 backend + Search 9 backend

**Fetch backends**:

| Backend | 库/API | 说明 |
|---------|--------|------|
| Readability | `@mozilla/readability` + `linkedom` | Mozilla 可读性提取 |
| Firecrawl | `POST api.firecrawl.dev/v2/scrape` | 云端抓取服务（直调 REST，无 SDK） |
| (Basic HTML) | 正则 + 去标签 | 最终 fallback |

**Search backends**:

| Backend | 环境变量 | 说明 |
|---------|---------|------|
| Brave | `BRAVE_API_KEY` | 结构化结果 + LLM context 模式 |
| Google/Gemini | `GEMINI_API_KEY` | Gemini grounded search |
| DuckDuckGo | 无 | 免费 fallback |
| Exa | `EXA_API_KEY` | 语义搜索 + 内容提取 |
| Tavily | `TAVILY_API_KEY` | 结构化 + AI 摘要 |
| Firecrawl | `FIRECRAWL_API_KEY` | 搜索 + 抓取一体 |
| Perplexity | `PERPLEXITY_API_KEY` | 答案合成 + 引用 |
| Kimi/Moonshot | `KIMI_API_KEY` | 内置搜索的 LLM |
| xAI/Grok | xAI API Key | Web + X/Twitter 搜索 |

### 2.3 Hermes（Python）— Fetch 4 backend + Search 4 backend

**Fetch backends**:

| Backend | SDK/API | 环境变量 |
|---------|---------|---------|
| Firecrawl | `firecrawl` SDK `.scrape()` | `FIRECRAWL_API_KEY` / `FIRECRAWL_API_URL` |
| Parallel | `parallel` SDK `.beta.extract()` | `PARALLEL_API_KEY` |
| Exa | `exa_py` SDK `.get_contents()` | `EXA_API_KEY` |
| Tavily | httpx POST `/extract` | `TAVILY_API_KEY` |

**Search backends**:

| Backend | SDK/API | 环境变量 |
|---------|---------|---------|
| Firecrawl | `firecrawl` SDK `.search()` | `FIRECRAWL_API_KEY` |
| Parallel | `parallel` SDK `.beta.search()` | `PARALLEL_API_KEY` |
| Exa | `exa_py` SDK `.search()` | `EXA_API_KEY` |
| Tavily | httpx POST `/search` | `TAVILY_API_KEY` |

### 2.4 SDK 淘汰决策

Hermes 用了三个 SDK（`firecrawl-py`、`exa-py`、`parallel`），但它们都是
REST API 的薄包装。OpenClaw 在 TypeScript 侧已经证明 Firecrawl 和 Exa 可以
直接 httpx 调 REST endpoint。

| SDK | 实际 endpoint | 替代方式 |
|-----|--------------|---------|
| `firecrawl-py` | `POST /v2/scrape`, `POST /v2/search` | httpx + `Authorization: Bearer <key>` |
| `exa-py` | `POST https://api.exa.ai/search` | httpx + `x-api-key: <key>` |
| `parallel` | `POST https://api.parallel.ai/search`, `/extract` | httpx + Bearer token |

**结论**：全部 17 个 backend 统一用 httpx 直调，零 SDK 依赖。

---

## 3. WebFetchTool

### 3.1 工具 Schema

```python
name = "WebFetch"
kind = ToolKind.read
should_defer = True
interrupt_behavior = "cancel"
max_result_size_chars = 100_000

input_schema = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "HTTP or HTTPS URL to fetch.",
        },
        "prompt": {
            "type": "string",
            "description": "What to extract from the page (guides content selection).",
        },
        "max_chars": {
            "type": "integer",
            "default": 50_000,
            "description": "Maximum characters of content to return.",
        },
    },
    "required": ["url"],
}
```

### 3.2 FetchBackend ABC

```python
# kernel/tools/web/fetch_backends/base.py

@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str               # 最终 URL（重定向后）
    content: str           # 提取的文本/Markdown
    content_type: str      # 原始 Content-Type
    title: str = ""
    status_code: int = 200
    error: str | None = None

class FetchBackend(ABC):
    """每个 fetch backend 实现此接口。"""
    name: str

    @abstractmethod
    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        ...

    def is_available(self) -> bool:
        """运行时检查此 backend 是否可用（API key 存在、依赖已安装等）。"""
        return True
```

### 3.3 全量 Fetch Backend 支持

7 个 backend，全部 httpx 直调（第三方服务）或纯本地处理：

| # | Backend | 来源 | API endpoint | 环境变量 | 说明 |
|---|---------|------|-------------|---------|------|
| 1 | **Firecrawl** | Hermes + OpenClaw | `POST api.firecrawl.dev/v2/scrape` | `FIRECRAWL_API_KEY` | JS 渲染 + anti-bot |
| 2 | **Parallel** | Hermes | `POST api.parallel.ai/extract` | `PARALLEL_API_KEY` | 全内容提取 |
| 3 | **Exa** | Hermes | `POST api.exa.ai/search` (w/ contents.text) | `EXA_API_KEY` | 语义内容提取 |
| 4 | **Tavily** | Hermes | `POST api.tavily.com/extract` | `TAVILY_API_KEY` | 提取 API |
| 5 | **Readability** | OpenClaw | 本地 `readability-lxml` | 无 | 可选本地依赖 |
| 6 | **Playwright** | daemon | 本地 headless Chrome | 无 | 可选本地依赖 |
| 7 | **httpx + html2text** | daemon | 本地 httpx GET | 无 | **永远可用** |

#### 3.3.1 每个 backend 的实现

**Firecrawl** (`fetch_backends/firecrawl.py`)：
```python
class FirecrawlFetchBackend(FetchBackend):
    name = "firecrawl"
    # OpenClaw 已验证的 REST API — 无需 firecrawl-py SDK

    def is_available(self) -> bool:
        return _has_env("FIRECRAWL_API_KEY") or _has_env("FIRECRAWL_API_URL")

    async def fetch(self, url, *, max_chars=50_000) -> FetchResult:
        base = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev")
        api_key = os.getenv("FIRECRAWL_API_KEY", "")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/v2/scrape",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "timeout": 60000,
                },
            )
            resp.raise_for_status()
        data = resp.json().get("data", {})
        return FetchResult(
            url=data.get("metadata", {}).get("sourceURL", url),
            content=data.get("markdown", "")[:max_chars],
            content_type="text/html",
            title=data.get("metadata", {}).get("title", ""),
            status_code=data.get("metadata", {}).get("statusCode", 200),
        )
```

**Parallel** (`fetch_backends/parallel.py`)：
```python
class ParallelFetchBackend(FetchBackend):
    name = "parallel"

    def is_available(self) -> bool:
        return _has_env("PARALLEL_API_KEY")

    async def fetch(self, url, *, max_chars=50_000) -> FetchResult:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.parallel.ai/extract",
                headers={"Authorization": f"Bearer {os.getenv('PARALLEL_API_KEY')}",
                         "Content-Type": "application/json"},
                json={"urls": [url], "full_content": True},
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return FetchResult(url=url, content="", content_type="",
                               error="no results")
        r = results[0]
        return FetchResult(
            url=r.get("url", url),
            content=(r.get("full_content") or "")[:max_chars],
            content_type="text/html",
            title=r.get("title", ""),
        )
```

**Exa** (`fetch_backends/exa.py`)：
```python
class ExaFetchBackend(FetchBackend):
    name = "exa"
    # OpenClaw 已验证的 REST endpoint: POST api.exa.ai/search

    def is_available(self) -> bool:
        return _has_env("EXA_API_KEY")

    async def fetch(self, url, *, max_chars=50_000) -> FetchResult:
        # Exa 没有独立的 get_contents endpoint —
        # 用 search endpoint 加 contents.text 参数提取内容
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": os.getenv("EXA_API_KEY"),
                         "Content-Type": "application/json"},
                json={
                    "query": url,  # URL 作为 query
                    "numResults": 1,
                    "contents": {"text": True},
                },
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return FetchResult(url=url, content="", content_type="",
                               error="no results from Exa")
        r = results[0]
        return FetchResult(
            url=r.get("url", url),
            content=(r.get("text") or "")[:max_chars],
            content_type="text/html",
            title=r.get("title", ""),
        )
```

**Tavily** (`fetch_backends/tavily.py`)：
```python
class TavilyFetchBackend(FetchBackend):
    name = "tavily"

    def is_available(self) -> bool:
        return _has_env("TAVILY_API_KEY")

    async def fetch(self, url, *, max_chars=50_000) -> FetchResult:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.tavily.com/extract",
                json={
                    "urls": [url],
                    "api_key": os.getenv("TAVILY_API_KEY"),
                    "include_images": False,
                },
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return FetchResult(url=url, content="", content_type="",
                               error="no results from Tavily")
        r = results[0]
        content = r.get("raw_content") or r.get("content") or ""
        return FetchResult(
            url=r.get("url", url),
            content=content[:max_chars],
            content_type="text/html",
            title=r.get("title", ""),
        )
```

**Readability** (`fetch_backends/readability.py`)：
```python
class ReadabilityFetchBackend(FetchBackend):
    """Mozilla Readability via readability-lxml — 可选本地依赖，无 API。"""
    name = "readability"

    def is_available(self) -> bool:
        try:
            import readability  # noqa: F401
            return True
        except ImportError:
            return False

    async def fetch(self, url, *, max_chars=50_000) -> FetchResult:
        from readability import Document
        # 1. httpx GET raw HTML（复用共享的 redirect-safe client）
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response, final_url = await _send_with_redirect_check(client, url)
        # 2. Readability 提取正文
        doc = Document(response.text)
        html_content = doc.summary()
        title = doc.title()
        # 3. html2text 转 Markdown
        markdown = _html_to_markdown(html_content, max_chars)
        return FetchResult(
            url=final_url, content=markdown,
            content_type="text/html", title=title,
            status_code=response.status_code,
        )
```

**Playwright** (`fetch_backends/playwright.py`)：
```python
class PlaywrightFetchBackend(FetchBackend):
    """Headless Chrome — 可选本地依赖，无 API。"""
    name = "playwright"

    def is_available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    async def fetch(self, url, *, max_chars=50_000) -> FetchResult:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)
                title = await page.title()
                content = await page.evaluate("document.body.innerText")
                return FetchResult(
                    url=page.url, content=(content or "")[:max_chars],
                    content_type="text/html", title=title,
                )
            finally:
                await browser.close()
```

**httpx + html2text** (`fetch_backends/httpx_html.py`) — **永远可用**：
```python
class HttpxFetchBackend(FetchBackend):
    """零外部依赖兜底。httpx GET + html2text。"""
    name = "httpx"

    def is_available(self) -> bool:
        return True

    async def fetch(self, url, *, max_chars=50_000) -> FetchResult:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response, final_url = await _send_with_redirect_check(client, url)
        content_type = response.headers.get("content-type", "")

        if "json" in content_type or "xml" in content_type:
            body = response.text[:max_chars]
        elif "html" in content_type:
            body = _html_to_markdown(response.text, max_chars)
        else:
            body = response.text[:max_chars]

        return FetchResult(
            url=final_url, content=body,
            content_type=content_type,
            status_code=response.status_code,
        )
```

### 3.4 Fetch Fallback Chain

```
用户配置的首选 backend（MUSTANG_FETCH_BACKEND）
  ↓ 失败 / 不可用
按优先级尝试其他已配置的第三方 backend
  ↓ 全部失败或都没配置
Readability（如已安装 readability-lxml）
  ↓ 未安装或失败
httpx + html2text ← 永远可用
  ↓ 遇到 anti-bot / 空内容
Playwright（如已安装）
  ↓ 未安装
返回 httpx 结果 + 提示
```

```python
# kernel/tools/web/fetch_backends/__init__.py

_BACKEND_PRIORITY: list[type[FetchBackend]] = [
    FirecrawlFetchBackend,    # 最强：JS 渲染 + anti-bot
    ParallelFetchBackend,
    ExaFetchBackend,
    TavilyFetchBackend,
    ReadabilityFetchBackend,  # 本地，无需 API key
    PlaywrightFetchBackend,   # 本地，需安装
    HttpxFetchBackend,        # 永远可用
]

def get_available_backends() -> list[FetchBackend]:
    """返回当前环境可用的 backend 实例列表（按优先级排序）。"""
    return [cls() for cls in _BACKEND_PRIORITY if cls().is_available()]

async def fetch_with_fallback(
    url: str,
    *,
    max_chars: int = 50_000,
    preferred: str | None = None,
) -> tuple[FetchResult, str]:
    """依次尝试可用 backend，返回 (result, backend_name)。"""
    backends = get_available_backends()
    if preferred:
        backends.sort(key=lambda b: (0 if b.name == preferred else 1))

    errors: list[str] = []
    httpx_result: FetchResult | None = None

    for backend in backends:
        try:
            result = await backend.fetch(url, max_chars=max_chars)
            if result.error:
                errors.append(f"{backend.name}: {result.error}")
                if backend.name == "httpx":
                    httpx_result = result
                continue

            if _looks_like_anti_bot(result):
                errors.append(f"{backend.name}: anti-bot page detected")
                if backend.name == "httpx":
                    httpx_result = result
                continue

            return result, backend.name

        except Exception as exc:
            errors.append(f"{backend.name}: {exc}")
            continue

    if httpx_result:
        return httpx_result, f"httpx (fallback, errors: {'; '.join(errors)})"
    return FetchResult(url=url, content="", content_type="",
                       error=f"All backends failed: {'; '.join(errors)}"), "none"


def _looks_like_anti_bot(result: FetchResult) -> bool:
    if not result.content or len(result.content.strip()) < 200:
        return True
    lower = result.content.lower()
    if result.status_code in (403, 429, 503):
        markers = ("captcha", "cloudflare", "challenge", "just a moment",
                   "verify you are human", "access denied")
        return any(m in lower for m in markers)
    return False
```

### 3.5 安全层

#### 3.5.1 DomainFilter（`kernel/tools/web/domain_filter.py`）

从 archived daemon 的 `domain_filter.py` 迁移，增强：

```python
def check_domain(url: str) -> str | None:
    """返回错误信息字符串，或 None 表示通过。"""
    # 1. scheme 必须是 http/https
    # 2. 禁止 URL 内嵌凭证（user:pass@host）— 取自 Claude Code
    # 3. IP 字面量检查：loopback / link-local / private / reserved
    # 4. hostname == "localhost" 检查
    # 5. 运算符黑名单（可配置）
    # 6. 嵌入式 API key 检测（取自 Hermes _PREFIX_RE）
```

**注意**：SSRF 检查在 WebFetchTool 入口统一执行一次，第三方 backend
（Firecrawl、Tavily 等）不经过此检查——它们由第三方服务端处理 SSRF。
httpx / Readability / Playwright 三个本地 backend 必须经过检查。

#### 3.5.2 重定向安全

逐跳验证（仅用于本地 backend：httpx、Readability）：

```python
async def _send_with_redirect_check(
    client: httpx.AsyncClient, url: str, *, max_redirects: int = 10
) -> tuple[httpx.Response, str]:
    current_url = url
    for _ in range(max_redirects + 1):
        response = await client.request("GET", current_url)
        if not response.is_redirect:
            return response, current_url
        next_url = str(response.url.join(response.headers.get("location", "")))
        if err := check_domain(next_url):
            raise RedirectBlockedError(current_url, next_url, err)
        current_url = next_url
    raise TooManyRedirectsError(url, max_redirects)
```

#### 3.5.3 预批准域名

```python
PREAPPROVED_HOSTS: frozenset[str] = frozenset({
    "docs.python.org", "pypi.org", "docs.rs",
    "developer.mozilla.org", "stackoverflow.com",
    "github.com", "raw.githubusercontent.com",
    "registry.npmjs.org", "pkg.go.dev",
    # ... 从 Claude Code preapproved.ts 移植完整列表
})
```

#### 3.5.4 内容安全

- 外部 HTML 内容标记为 `untrusted`（取自 OpenClaw）
- base64 `data:image/...` 全部剥离（取自 Hermes）
- 嵌入式 `<script>` 标签剥离
- 响应 body 硬上限 10 MB bytes / 100K chars markdown

### 3.6 权限模型

```python
def default_risk(self, input, ctx) -> PermissionSuggestion:
    host = urlparse(input["url"]).hostname or ""
    if host in PREAPPROVED_HOSTS:
        return PermissionSuggestion(
            risk="low", default_decision="allow",
            reason=f"preapproved host: {host}",
        )
    return PermissionSuggestion(
        risk="medium", default_decision="ask",
        reason=f"outbound fetch to {host}",
    )

def prepare_permission_matcher(self, input):
    host = urlparse(input["url"]).hostname or ""
    return lambda pattern: fnmatch(host, pattern)
```

### 3.7 输出格式

```
[fetched: https://example.com → https://www.example.com (via firecrawl)]
Content-Type: text/html

# Page Title

Main content in Markdown...

... (truncated at 50000 chars)
```

- JSON API → 原始 body，不做转换
- 二进制文件 → 存磁盘，返回：`Saved 2.3 MB to /tmp/mustang-fetch/<hash>.pdf`
- 尾行标注实际使用的 backend

### 3.8 Display Payload

Phase 1 用 `TextDisplay`。Phase 2 加：

```python
@dataclass(frozen=True)
class WebFetchDisplay:
    url: str
    final_url: str | None
    status_code: int
    content_type: str
    content_preview: str       # 前 500 chars
    truncated: bool
    backend: str               # 实际使用的 backend name
```

---

## 4. WebSearchTool

### 4.1 工具 Schema

```python
name = "WebSearch"
kind = ToolKind.read
should_defer = True
interrupt_behavior = "cancel"
max_result_size_chars = 100_000

input_schema = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 2,
            "description": "Search query.",
        },
        "limit": {
            "type": "integer",
            "default": 10,
            "minimum": 1,
            "maximum": 25,
            "description": "Number of results to return.",
        },
    },
    "required": ["query"],
}
```

### 4.2 SearchBackend ABC

```python
# kernel/tools/web/search_backends/base.py

@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str

class SearchBackend(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        ...

    def is_available(self) -> bool:
        return True
```

### 4.3 全量 Search Backend 支持

10 个 backend，全部 httpx 直调：

| # | Backend | 来源 | REST endpoint | Auth | 环境变量 |
|---|---------|------|--------------|------|---------|
| 1 | **Brave** | OC+daemon | `GET api.search.brave.com/res/v1/web/search` | `X-Subscription-Token` header | `BRAVE_API_KEY` |
| 2 | **Google CSE** | daemon | `GET googleapis.com/customsearch/v1` | query param `key` | `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` |
| 3 | **Exa** | Hermes+OC | `POST api.exa.ai/search` | `x-api-key` header | `EXA_API_KEY` |
| 4 | **Tavily** | Hermes+OC | `POST api.tavily.com/search` | body 内 `api_key` | `TAVILY_API_KEY` |
| 5 | **Firecrawl** | Hermes+OC | `POST api.firecrawl.dev/v2/search` | `Authorization: Bearer` | `FIRECRAWL_API_KEY` |
| 6 | **Parallel** | Hermes | `POST api.parallel.ai/search` | `Authorization: Bearer` | `PARALLEL_API_KEY` |
| 7 | **Perplexity** | OC | `POST api.perplexity.ai/chat/completions` | `Authorization: Bearer` | `PERPLEXITY_API_KEY` |
| 8 | **Kimi** | OC | `POST api.moonshot.ai/v1/chat/completions` | `Authorization: Bearer` | `KIMI_API_KEY` |
| 9 | **xAI/Grok** | OC | `POST api.x.ai/v1/chat/completions` | `Authorization: Bearer` | `XAI_API_KEY` |
| 10 | **DuckDuckGo** | daemon | `GET lite.duckduckgo.com/lite/` | 无 | 无 |

### 4.4 每个 backend 的实现

**Brave** (`search_backends/brave.py`)：
```python
class BraveSearchBackend(SearchBackend):
    name = "brave"

    def is_available(self) -> bool:
        return _has_env("BRAVE_API_KEY")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(limit, 20)},
                headers={"X-Subscription-Token": os.getenv("BRAVE_API_KEY"),
                         "Accept": "application/json"},
            )
            resp.raise_for_status()
        data = resp.json()
        return [SearchResult(title=r["title"], url=r["url"],
                             snippet=r.get("description", ""))
                for r in data.get("web", {}).get("results", [])[:limit]]
```

**Google CSE** (`search_backends/google.py`)：
```python
class GoogleSearchBackend(SearchBackend):
    name = "google"

    def is_available(self) -> bool:
        return _has_env("GOOGLE_API_KEY") and _has_env("GOOGLE_CSE_ID")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"q": query, "key": os.getenv("GOOGLE_API_KEY"),
                        "cx": os.getenv("GOOGLE_CSE_ID"),
                        "num": min(limit, 10)},
            )
            resp.raise_for_status()
        items = resp.json().get("items", [])
        return [SearchResult(title=r["title"], url=r["link"],
                             snippet=r.get("snippet", ""))
                for r in items[:limit]]
```

**Exa** (`search_backends/exa.py`)：
```python
class ExaSearchBackend(SearchBackend):
    name = "exa"

    def is_available(self) -> bool:
        return _has_env("EXA_API_KEY")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": os.getenv("EXA_API_KEY"),
                         "Content-Type": "application/json"},
                json={
                    "query": query,
                    "numResults": limit,
                    "contents": {"highlights": True},
                },
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        return [SearchResult(
            title=r.get("title", ""), url=r["url"],
            snippet=" ".join(r.get("highlights", [])),
        ) for r in results[:limit]]
```

**Tavily** (`search_backends/tavily.py`)：
```python
class TavilySearchBackend(SearchBackend):
    name = "tavily"

    def is_available(self) -> bool:
        return _has_env("TAVILY_API_KEY")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"query": query, "max_results": min(limit, 20),
                      "api_key": os.getenv("TAVILY_API_KEY"),
                      "include_raw_content": False,
                      "include_images": False},
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        return [SearchResult(title=r.get("title", ""), url=r["url"],
                             snippet=r.get("content", ""))
                for r in results[:limit]]
```

**Firecrawl** (`search_backends/firecrawl.py`)：
```python
class FirecrawlSearchBackend(SearchBackend):
    name = "firecrawl"

    def is_available(self) -> bool:
        return _has_env("FIRECRAWL_API_KEY") or _has_env("FIRECRAWL_API_URL")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        base = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev")
        api_key = os.getenv("FIRECRAWL_API_KEY", "")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/v2/search",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"query": query, "limit": limit},
            )
            resp.raise_for_status()
        data = resp.json().get("data", [])
        return [SearchResult(
            title=r.get("title", ""), url=r.get("url", ""),
            snippet=r.get("description", ""),
        ) for r in data[:limit]]
```

**Parallel** (`search_backends/parallel.py`)：
```python
class ParallelSearchBackend(SearchBackend):
    name = "parallel"

    def is_available(self) -> bool:
        return _has_env("PARALLEL_API_KEY")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        mode = os.getenv("PARALLEL_SEARCH_MODE", "agentic")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.parallel.ai/search",
                headers={"Authorization": f"Bearer {os.getenv('PARALLEL_API_KEY')}",
                         "Content-Type": "application/json"},
                json={
                    "search_queries": [query],
                    "objective": query,
                    "mode": mode,
                    "max_results": min(limit, 20),
                },
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        return [SearchResult(
            title=r.get("title", ""), url=r.get("url", ""),
            snippet=" ".join(r.get("excerpts", [])),
        ) for r in results[:limit]]
```

**Perplexity** (`search_backends/perplexity.py`)：
```python
class PerplexitySearchBackend(SearchBackend):
    name = "perplexity"

    def is_available(self) -> bool:
        return _has_env("PERPLEXITY_API_KEY")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        api_key = os.getenv("PERPLEXITY_API_KEY", "")
        if api_key.startswith("pplx-"):
            base_url = "https://api.perplexity.ai"
        else:
            base_url = "https://openrouter.ai/api/v1"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": "perplexity/sonar-pro",
                    "messages": [{"role": "user", "content": query}],
                },
            )
            resp.raise_for_status()
        data = resp.json()
        citations = data.get("citations", [])
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        # 从 citations 构建 SearchResult
        return [SearchResult(title="", url=c, snippet="")
                for c in citations[:limit]] if citations else [
            SearchResult(title="Perplexity answer", url="", snippet=content[:500])
        ]
```

**Kimi/Moonshot** (`search_backends/kimi.py`)：
```python
class KimiSearchBackend(SearchBackend):
    name = "kimi"

    def is_available(self) -> bool:
        return _has_env("KIMI_API_KEY") or _has_env("MOONSHOT_API_KEY")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        api_key = os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY") or ""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.moonshot.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": "moonshot-v1-128k",
                    "messages": [{"role": "user", "content": query}],
                    "tools": [{"type": "builtin_function",
                               "function": {"name": "$web_search"}}],
                },
            )
            resp.raise_for_status()
        data = resp.json()
        search_results = data.get("search_results", [])
        return [SearchResult(
            title=r.get("title", ""), url=r.get("url", ""),
            snippet=r.get("snippet", ""),
        ) for r in search_results[:limit]]
```

**xAI/Grok** (`search_backends/xai.py`)：
```python
class XaiSearchBackend(SearchBackend):
    name = "xai"

    def is_available(self) -> bool:
        return _has_env("XAI_API_KEY")

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('XAI_API_KEY', '')}",
                         "Content-Type": "application/json"},
                json={
                    "model": "grok-3",
                    "messages": [{"role": "user", "content": query}],
                    "tools": [{"type": "function",
                               "function": {"name": "web_search",
                                            "parameters": {}}}],
                },
            )
            resp.raise_for_status()
        # 从 tool_calls / citations 提取搜索结果
        data = resp.json()
        choices = data.get("choices", [])
        # xAI 返回格式需要适配具体 response shape
        ...
```

**DuckDuckGo** (`search_backends/duckduckgo.py`) — **永远可用**：
```python
class DuckDuckGoSearchBackend(SearchBackend):
    """HTML scrape of DuckDuckGo lite — 零 API key，永远可用。"""
    name = "duckduckgo"

    def is_available(self) -> bool:
        return True

    async def search(self, query, *, limit=10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
        return _parse_ddg_html(resp.text, limit)
```

### 4.5 Search Fallback Chain

```python
# kernel/tools/web/search_backends/__init__.py

_BACKEND_PRIORITY: list[type[SearchBackend]] = [
    BraveSearchBackend,
    GoogleSearchBackend,
    ExaSearchBackend,
    TavilySearchBackend,
    FirecrawlSearchBackend,
    ParallelSearchBackend,
    PerplexitySearchBackend,
    KimiSearchBackend,
    XaiSearchBackend,
    DuckDuckGoSearchBackend,   # 永远可用
]

def get_available_backends() -> list[SearchBackend]:
    return [cls() for cls in _BACKEND_PRIORITY if cls().is_available()]

async def search_with_fallback(
    query: str,
    limit: int,
    *,
    preferred: str | None = None,
) -> tuple[list[SearchResult], str]:
    """依次尝试可用 backend，第一个成功就返回。"""
    backends = get_available_backends()
    if preferred:
        backends.sort(key=lambda b: (0 if b.name == preferred else 1))

    errors: list[str] = []
    for backend in backends:
        try:
            results = await backend.search(query, limit=limit)
            if results:
                return results, backend.name
            errors.append(f"{backend.name}: 0 results")
        except Exception as exc:
            errors.append(f"{backend.name}: {exc}")
            continue

    return [], f"all backends failed: {'; '.join(errors)}"
```

### 4.6 输出格式

```
Note: Dates in snippets below are from the original pages, not the current date.

1. Python Documentation
   https://docs.python.org/3/
   Welcome to Python 3.12 documentation...

2. Real Python Tutorials
   https://realpython.com/
   Learn Python programming with step-by-step tutorials...

(10 results via brave)
```

### 4.7 权限模型

```python
def default_risk(self, input, ctx) -> PermissionSuggestion:
    return PermissionSuggestion(
        risk="low", default_decision="allow",
        reason="web search is read-only and low-risk",
    )
```

---

## 5. 共享基础设施

### 5.1 模块结构

```
kernel/tools/
├── web/
│   ├── __init__.py
│   ├── domain_filter.py          # SSRF 防护
│   ├── preapproved.py            # 预批准域名集合
│   ├── html_convert.py           # HTML→Markdown（html2text wrapper）
│   ├── fetch_backends/
│   │   ├── __init__.py           # get_available_backends() + fetch_with_fallback()
│   │   ├── base.py               # FetchBackend ABC + FetchResult
│   │   ├── httpx_html.py         # httpx + html2text（零依赖兜底）
│   │   ├── readability_be.py     # Mozilla Readability（可选本地依赖）
│   │   ├── playwright_be.py      # Headless Chrome（可选本地依赖）
│   │   ├── firecrawl.py          # POST api.firecrawl.dev/v2/scrape
│   │   ├── parallel.py           # POST api.parallel.ai/extract
│   │   ├── exa.py                # POST api.exa.ai/search (contents.text)
│   │   └── tavily.py             # POST api.tavily.com/extract
│   └── search_backends/
│       ├── __init__.py           # get_available_backends() + search_with_fallback()
│       ├── base.py               # SearchBackend ABC + SearchResult
│       ├── duckduckgo.py         # GET lite.duckduckgo.com（零依赖兜底）
│       ├── brave.py              # GET api.search.brave.com
│       ├── google.py             # GET googleapis.com/customsearch
│       ├── exa.py                # POST api.exa.ai/search
│       ├── tavily.py             # POST api.tavily.com/search
│       ├── firecrawl.py          # POST api.firecrawl.dev/v2/search
│       ├── parallel.py           # POST api.parallel.ai/search
│       ├── perplexity.py         # POST api.perplexity.ai/chat/completions
│       ├── kimi.py               # POST api.moonshot.ai/v1/chat/completions
│       └── xai.py                # POST api.x.ai/v1/chat/completions
├── builtin/
│   ├── web_fetch.py              # WebFetchTool
│   └── web_search.py             # WebSearchTool
```

### 5.2 依赖策略

**全部 API backend 只用 httpx，零 SDK 依赖。**

| 类型 | 包 | 用途 |
|------|---|------|
| **已有** | `httpx` | 所有 17 个 backend 的 HTTP 客户端 |
| **新增必须** | `html2text` | HTML→Markdown 转换（httpx backend 兜底用） |
| **可选本地** | `readability-lxml` + `lxml` | Readability fetch backend（本地 HTML 正文提取） |
| **可选本地** | `playwright` | Playwright fetch backend（headless Chrome） |

```toml
# pyproject.toml
[project]
dependencies = [
    "httpx",       # 已有
    "html2text",   # 新增
]

[project.optional-dependencies]
web-readability = ["readability-lxml", "lxml"]
web-browser = ["playwright"]
web-all = ["readability-lxml", "lxml", "playwright"]
```

**为什么不用 SDK**：Firecrawl、Exa、Parallel 三家的 Python SDK 都是
REST API 的薄 wrapper。OpenClaw 在 TypeScript 侧已证明 Firecrawl 和 Exa
可以直接 HTTP 调用。统一用 httpx 的好处：

1. 零额外安装——`pip install mustang` 即可使用所有 API backend
2. 统一的超时、重试、错误处理
3. 不受上游 SDK 版本变更影响
4. 依赖树更干净

### 5.3 配置

```bash
# 环境变量 — 存在即启用对应 backend
BRAVE_API_KEY=...
GOOGLE_API_KEY=...
GOOGLE_CSE_ID=...
EXA_API_KEY=...
TAVILY_API_KEY=...
FIRECRAWL_API_KEY=...
FIRECRAWL_API_URL=...          # 自托管 Firecrawl（覆盖默认 api.firecrawl.dev）
PARALLEL_API_KEY=...
PARALLEL_SEARCH_MODE=agentic   # fast | one-shot | agentic
PERPLEXITY_API_KEY=...
KIMI_API_KEY=...               # 或 MOONSHOT_API_KEY
XAI_API_KEY=...

# 可选偏好（覆盖优先级排序）
MUSTANG_FETCH_BACKEND=firecrawl
MUSTANG_SEARCH_BACKEND=brave
```

---

## 6. Fallback 矩阵总览

### WebFetchTool

```
首选 backend（MUSTANG_FETCH_BACKEND 或第一个可用的第三方）
  │ 失败
  ↓
依优先级尝试其余已配置的第三方 backend
  │ 全部失败或都没配置
  ↓
Readability（如已安装 readability-lxml）
  │ 未安装或失败
  ↓
httpx + html2text ← 永远可用
  │ 遇到 anti-bot / 空内容
  ↓
Playwright（如已安装）
  │ 未安装
  ↓
返回 httpx 结果 + 提示
```

### WebSearchTool

```
首选 backend（MUSTANG_SEARCH_BACKEND 或第一个可用的 API-key backend）
  │ 失败
  ↓
依优先级尝试其余已配置的 backend
  │ 全部失败或都没配置
  ↓
DuckDuckGo ← 永远可用
  │ 也失败（DDG 改版/网络不通）
  ↓
返回聚合错误信息
```

---

## 7. 实现清单

### 基础设施

- [ ] `web/domain_filter.py` — 从 archive 迁移 + 凭证检查 + URL 嵌入 key 检测
- [ ] `web/preapproved.py` — 预批准域名集（从 Claude Code preapproved.ts 移植）
- [ ] `web/html_convert.py` — html2text wrapper + base64 剥离 + script 剥离

### Fetch Backend（全部）

- [ ] `web/fetch_backends/base.py` — FetchBackend ABC + FetchResult
- [ ] `web/fetch_backends/__init__.py` — get_available_backends() + fetch_with_fallback() + _looks_like_anti_bot()
- [ ] `web/fetch_backends/httpx_html.py` — httpx + html2text（零依赖兜底）
- [ ] `web/fetch_backends/firecrawl.py` — POST api.firecrawl.dev/v2/scrape
- [ ] `web/fetch_backends/parallel.py` — POST api.parallel.ai/extract
- [ ] `web/fetch_backends/exa.py` — POST api.exa.ai/search (contents.text)
- [ ] `web/fetch_backends/tavily.py` — POST api.tavily.com/extract
- [ ] `web/fetch_backends/readability_be.py` — readability-lxml（可选本地依赖）
- [ ] `web/fetch_backends/playwright_be.py` — headless Chrome（可选本地依赖）

### Search Backend（全部）

- [ ] `web/search_backends/base.py` — SearchBackend ABC + SearchResult
- [ ] `web/search_backends/__init__.py` — get_available_backends() + search_with_fallback()
- [ ] `web/search_backends/duckduckgo.py` — GET lite.duckduckgo.com（零依赖兜底）
- [ ] `web/search_backends/brave.py` — GET api.search.brave.com
- [ ] `web/search_backends/google.py` — GET googleapis.com/customsearch
- [ ] `web/search_backends/exa.py` — POST api.exa.ai/search
- [ ] `web/search_backends/tavily.py` — POST api.tavily.com/search
- [ ] `web/search_backends/firecrawl.py` — POST api.firecrawl.dev/v2/search
- [ ] `web/search_backends/parallel.py` — POST api.parallel.ai/search
- [ ] `web/search_backends/perplexity.py` — POST api.perplexity.ai
- [ ] `web/search_backends/kimi.py` — POST api.moonshot.ai
- [ ] `web/search_backends/xai.py` — POST api.x.ai

### Tool 注册

- [ ] `builtin/web_fetch.py` — WebFetchTool（should_defer=True）
- [ ] `builtin/web_search.py` — WebSearchTool（should_defer=True）
- [ ] `builtin/__init__.py` — 注册到 BUILTIN_TOOLS
- [ ] `pyproject.toml` — html2text 必须依赖 + optional-dependencies

### 单元测试

- [ ] `tests/kernel/tools/web/test_domain_filter.py`
- [ ] `tests/kernel/tools/web/test_html_convert.py`
- [ ] `tests/kernel/tools/web/test_fetch_fallback.py`
- [ ] `tests/kernel/tools/web/test_search_fallback.py`
- [ ] `tests/kernel/tools/web/test_web_fetch_tool.py`
- [ ] `tests/kernel/tools/web/test_web_search_tool.py`

### E2E 测试

- [ ] `tests/kernel/tools/web/e2e/test_fetch_e2e.py`
- [ ] `tests/kernel/tools/web/e2e/test_search_e2e.py`

---

## 8. 测试策略

### 8.1 单元测试（pytest，全部 mock HTTP）

**domain_filter**：
```python
# tests/kernel/tools/web/test_domain_filter.py

def test_blocks_loopback():
    assert check_domain("http://127.0.0.1/admin") is not None

def test_blocks_link_local():
    assert check_domain("http://169.254.169.254/metadata") is not None

def test_blocks_private():
    assert check_domain("http://10.0.0.1/internal") is not None
    assert check_domain("http://192.168.1.1/") is not None

def test_blocks_localhost():
    assert check_domain("http://localhost:8080/") is not None

def test_blocks_embedded_credentials():
    assert check_domain("http://user:pass@example.com/") is not None

def test_blocks_embedded_api_key():
    assert check_domain("http://example.com/?key=sk-abc123") is not None

def test_allows_public():
    assert check_domain("https://docs.python.org/3/") is None

def test_allows_ip_public():
    assert check_domain("http://8.8.8.8/") is None

def test_operator_blocklist():
    add_blocked_domain("evil.com")
    assert check_domain("http://evil.com/") is not None
    remove_blocked_domain("evil.com")
    assert check_domain("http://evil.com/") is None
```

**html_convert**：
```python
# tests/kernel/tools/web/test_html_convert.py

def test_basic_conversion():
    md = html_to_markdown("<h1>Title</h1><p>Hello</p>")
    assert "# Title" in md
    assert "Hello" in md

def test_strips_base64_images():
    html = '<img src="data:image/png;base64,AAAA...">'
    md = html_to_markdown(html)
    assert "data:image" not in md

def test_strips_script_tags():
    html = "<p>Hi</p><script>alert(1)</script><p>Bye</p>"
    md = html_to_markdown(html)
    assert "alert" not in md
    assert "Hi" in md

def test_truncation():
    html = "<p>" + "x" * 10_000 + "</p>"
    md = html_to_markdown(html, max_chars=100)
    assert len(md) <= 100
```

**fetch_with_fallback**（mock backends）：
```python
# tests/kernel/tools/web/test_fetch_fallback.py

@pytest.fixture
def mock_backends():
    """三个 backend：第一个失败，第二个返回 anti-bot，第三个成功。"""
    fail = MockFetchBackend("fail", error="connection refused")
    antibot = MockFetchBackend("antibot", content="<html>Please verify</html>",
                                status_code=403)
    ok = MockFetchBackend("ok", content="# Real Content\n\nHello world.")
    return [fail, antibot, ok]

async def test_fallback_skips_failures(mock_backends):
    result, name = await fetch_with_fallback(
        "https://example.com", backends=mock_backends)
    assert name == "ok"
    assert "Real Content" in result.content

async def test_fallback_all_fail():
    fail1 = MockFetchBackend("a", error="timeout")
    fail2 = MockFetchBackend("b", error="403")
    result, name = await fetch_with_fallback(
        "https://example.com", backends=[fail1, fail2])
    assert result.error
    assert "all" in name.lower() or "fallback" in name.lower()

async def test_preferred_backend_tried_first(mock_backends):
    result, name = await fetch_with_fallback(
        "https://example.com", backends=mock_backends, preferred="ok")
    assert name == "ok"

async def test_anti_bot_detection():
    assert _looks_like_anti_bot(FetchResult(
        url="", content="Just a moment...", content_type="text/html",
        status_code=403))
    assert _looks_like_anti_bot(FetchResult(
        url="", content="", content_type="text/html", status_code=200))
    assert not _looks_like_anti_bot(FetchResult(
        url="", content="x" * 500, content_type="text/html", status_code=200))
```

**search_with_fallback**（mock backends）：
```python
# tests/kernel/tools/web/test_search_fallback.py

async def test_first_available_wins():
    brave = MockSearchBackend("brave", results=[
        SearchResult("Python", "https://python.org", "The language")])
    ddg = MockSearchBackend("duckduckgo", results=[
        SearchResult("Python", "https://python.org", "A language")])
    results, name = await search_with_fallback("python", 10,
                                                backends=[brave, ddg])
    assert name == "brave"

async def test_fallback_on_exception():
    fail = MockSearchBackend("brave", raise_exc=RuntimeError("rate limited"))
    ddg = MockSearchBackend("duckduckgo", results=[
        SearchResult("Python", "https://python.org", "A language")])
    results, name = await search_with_fallback("python", 10,
                                                backends=[fail, ddg])
    assert name == "duckduckgo"
    assert len(results) == 1

async def test_all_backends_fail():
    fail1 = MockSearchBackend("a", raise_exc=RuntimeError("x"))
    fail2 = MockSearchBackend("b", raise_exc=RuntimeError("y"))
    results, name = await search_with_fallback("python", 10,
                                                backends=[fail1, fail2])
    assert results == []
    assert "all backends failed" in name
```

**WebFetchTool / WebSearchTool**（mock ToolContext + mock fallback）：
```python
# tests/kernel/tools/web/test_web_fetch_tool.py

@pytest.fixture
def tool():
    return WebFetchTool()

@pytest.fixture
def ctx():
    ctx = MagicMock()
    ctx.cwd = Path.cwd()
    ctx.session_id = "s-1"
    ctx.cancel_event = asyncio.Event()
    return ctx

def test_default_risk_preapproved(tool):
    s = tool.default_risk({"url": "https://docs.python.org/3/"}, ctx)
    assert s.default_decision == "allow"

def test_default_risk_unknown(tool):
    s = tool.default_risk({"url": "https://sketchy.example.com/"}, ctx)
    assert s.default_decision == "ask"

def test_permission_matcher(tool):
    matcher = tool.prepare_permission_matcher(
        {"url": "https://api.github.com/repos"})
    assert matcher("*.github.com")
    assert not matcher("*.evil.com")

async def test_validate_rejects_ftp(tool, ctx):
    with pytest.raises(ToolInputError):
        await tool.validate_input({"url": "ftp://bad.com/file"}, ctx)

async def test_validate_rejects_ssrf(tool, ctx):
    with pytest.raises(ToolInputError):
        await tool.validate_input({"url": "http://169.254.169.254/"}, ctx)
```

### 8.2 E2E 测试（真实网络请求）

E2E 测试发真实 HTTP 请求，验证每个 backend 在真实环境下的行为。
用 `pytest.mark.e2e` 标记，默认 `pytest` 不运行，需要
`pytest -m e2e` 或 `pytest --run-e2e` 显式触发。

**前提**：CI 中配置对应的 API key 环境变量；无 key 的 backend 自动 skip。

```python
# tests/kernel/tools/web/e2e/conftest.py

import os
import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: real network requests")

def skip_without_key(env_var: str):
    """如果环境变量不存在，skip 该测试。"""
    return pytest.mark.skipif(
        not os.getenv(env_var),
        reason=f"{env_var} not set",
    )
```

**Fetch E2E**：
```python
# tests/kernel/tools/web/e2e/test_fetch_e2e.py

import pytest
from kernel.tools.web.fetch_backends import fetch_with_fallback
from kernel.tools.web.fetch_backends.httpx_html import HttpxFetchBackend
from kernel.tools.web.fetch_backends.firecrawl import FirecrawlFetchBackend
from kernel.tools.web.fetch_backends.exa import ExaFetchBackend
from kernel.tools.web.fetch_backends.tavily import TavilyFetchBackend
from kernel.tools.web.fetch_backends.parallel import ParallelFetchBackend

pytestmark = pytest.mark.e2e

# ── 兜底 backend（永远可运行） ──

class TestHttpxFetchBackend:
    """httpx + html2text — 零依赖兜底，CI 必跑。"""

    async def test_fetch_html_page(self):
        be = HttpxFetchBackend()
        result = await be.fetch("https://example.com")
        assert result.status_code == 200
        assert len(result.content) > 100
        assert "Example Domain" in result.content

    async def test_fetch_json_api(self):
        be = HttpxFetchBackend()
        result = await be.fetch("https://httpbin.org/json")
        assert "200" == str(result.status_code) or result.status_code == 200
        assert "slideshow" in result.content  # httpbin /json 返回 slideshow

    async def test_fetch_redirect(self):
        be = HttpxFetchBackend()
        result = await be.fetch("http://httpbin.org/redirect/1")
        assert result.status_code == 200  # 跟随重定向后

    async def test_fetch_respects_max_chars(self):
        be = HttpxFetchBackend()
        result = await be.fetch("https://example.com", max_chars=50)
        assert len(result.content) <= 50

    async def test_fetch_ssrf_blocked(self):
        """本地 backend 必须经过 SSRF 检查。"""
        be = HttpxFetchBackend()
        result = await be.fetch("http://169.254.169.254/latest/meta-data/")
        assert result.error  # 应该被 domain_filter 拦截


# ── 第三方 backend（需要 API key） ──

class TestFirecrawlFetchBackend:
    pytestmark = skip_without_key("FIRECRAWL_API_KEY")

    async def test_fetch_js_heavy_page(self):
        be = FirecrawlFetchBackend()
        result = await be.fetch("https://docs.python.org/3/")
        assert not result.error
        assert len(result.content) > 500
        assert "Python" in result.content

    async def test_fetch_returns_markdown(self):
        be = FirecrawlFetchBackend()
        result = await be.fetch("https://example.com")
        assert "#" in result.content or "Example" in result.content

class TestExaFetchBackend:
    pytestmark = skip_without_key("EXA_API_KEY")

    async def test_fetch_content(self):
        be = ExaFetchBackend()
        result = await be.fetch("https://docs.python.org/3/")
        assert not result.error
        assert len(result.content) > 100

class TestTavilyFetchBackend:
    pytestmark = skip_without_key("TAVILY_API_KEY")

    async def test_fetch_extract(self):
        be = TavilyFetchBackend()
        result = await be.fetch("https://example.com")
        assert not result.error
        assert "Example" in result.content

class TestParallelFetchBackend:
    pytestmark = skip_without_key("PARALLEL_API_KEY")

    async def test_fetch_extract(self):
        be = ParallelFetchBackend()
        result = await be.fetch("https://example.com")
        assert not result.error
        assert len(result.content) > 50


# ── Fallback chain 集成 ──

class TestFetchFallbackE2E:
    """验证 fallback chain 在真实环境下的工作。"""

    async def test_zero_config_uses_httpx(self, monkeypatch):
        """无任何 API key 时，fallback 到 httpx。"""
        for var in ("FIRECRAWL_API_KEY", "PARALLEL_API_KEY",
                     "EXA_API_KEY", "TAVILY_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        result, backend = await fetch_with_fallback("https://example.com")
        assert "Example Domain" in result.content
        assert "httpx" in backend

    async def test_preferred_backend(self):
        """preferred 参数生效（用 httpx 测试，因为它永远可用）。"""
        result, backend = await fetch_with_fallback(
            "https://example.com", preferred="httpx")
        assert backend == "httpx"
        assert "Example Domain" in result.content
```

**Search E2E**：
```python
# tests/kernel/tools/web/e2e/test_search_e2e.py

import pytest
from kernel.tools.web.search_backends import search_with_fallback
from kernel.tools.web.search_backends.duckduckgo import DuckDuckGoSearchBackend
from kernel.tools.web.search_backends.brave import BraveSearchBackend
from kernel.tools.web.search_backends.google import GoogleSearchBackend
from kernel.tools.web.search_backends.exa import ExaSearchBackend
from kernel.tools.web.search_backends.tavily import TavilySearchBackend
from kernel.tools.web.search_backends.firecrawl import FirecrawlSearchBackend
from kernel.tools.web.search_backends.parallel import ParallelSearchBackend
from kernel.tools.web.search_backends.perplexity import PerplexitySearchBackend
from kernel.tools.web.search_backends.kimi import KimiSearchBackend
from kernel.tools.web.search_backends.xai import XaiSearchBackend

pytestmark = pytest.mark.e2e

# ── 兜底 backend（永远可运行） ──

class TestDuckDuckGoSearchBackend:
    """DDG — 零 API key，CI 必跑。"""

    async def test_search_returns_results(self):
        be = DuckDuckGoSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1
        assert all(r.url.startswith("http") for r in results)
        assert all(r.title for r in results)

    async def test_search_respects_limit(self):
        be = DuckDuckGoSearchBackend()
        results = await be.search("python", limit=3)
        assert len(results) <= 3

    async def test_search_empty_query_returns_something(self):
        """DDG lite 即使空关键词也会返回内容（或优雅失败）。"""
        be = DuckDuckGoSearchBackend()
        results = await be.search("xyznonexistent12345qwerty", limit=5)
        # 极冷门词可能 0 结果——这是合法的
        assert isinstance(results, list)


# ── 第三方 backend（需要 API key） ──

class TestBraveSearchBackend:
    pytestmark = skip_without_key("BRAVE_API_KEY")

    async def test_search(self):
        be = BraveSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1
        assert results[0].url.startswith("http")

class TestGoogleSearchBackend:
    pytestmark = [skip_without_key("GOOGLE_API_KEY"),
                  skip_without_key("GOOGLE_CSE_ID")]

    async def test_search(self):
        be = GoogleSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1

class TestExaSearchBackend:
    pytestmark = skip_without_key("EXA_API_KEY")

    async def test_search_with_highlights(self):
        be = ExaSearchBackend()
        results = await be.search("python programming", limit=3)
        assert len(results) >= 1
        assert results[0].snippet  # highlights 应该非空

class TestTavilySearchBackend:
    pytestmark = skip_without_key("TAVILY_API_KEY")

    async def test_search(self):
        be = TavilySearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1

class TestFirecrawlSearchBackend:
    pytestmark = skip_without_key("FIRECRAWL_API_KEY")

    async def test_search(self):
        be = FirecrawlSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1

class TestParallelSearchBackend:
    pytestmark = skip_without_key("PARALLEL_API_KEY")

    async def test_search(self):
        be = ParallelSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1

class TestPerplexitySearchBackend:
    pytestmark = skip_without_key("PERPLEXITY_API_KEY")

    async def test_search(self):
        be = PerplexitySearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1

class TestKimiSearchBackend:
    pytestmark = skip_without_key("KIMI_API_KEY")

    async def test_search(self):
        be = KimiSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1

class TestXaiSearchBackend:
    pytestmark = skip_without_key("XAI_API_KEY")

    async def test_search(self):
        be = XaiSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1


# ── Fallback chain 集成 ──

class TestSearchFallbackE2E:

    async def test_zero_config_uses_duckduckgo(self, monkeypatch):
        """无任何 API key 时，fallback 到 DDG。"""
        for var in ("BRAVE_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CSE_ID",
                     "EXA_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY",
                     "PARALLEL_API_KEY", "PERPLEXITY_API_KEY",
                     "KIMI_API_KEY", "MOONSHOT_API_KEY", "XAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        results, backend = await search_with_fallback("python", 5)
        assert backend == "duckduckgo"
        assert len(results) >= 1

    async def test_preferred_backend(self):
        """preferred=duckduckgo 跳过所有 API backend。"""
        results, backend = await search_with_fallback(
            "python", 5, preferred="duckduckgo")
        assert backend == "duckduckgo"
        assert len(results) >= 1
```

### 8.3 测试运行方式

```bash
# 单元测试（无网络，快速，CI 默认）
pytest tests/kernel/tools/web/ -m "not e2e" -v

# E2E 测试 — 兜底 backend（无需 API key，CI 可跑）
pytest tests/kernel/tools/web/e2e/ -m e2e -k "Httpx or DuckDuckGo or Fallback" -v

# E2E 测试 — 单个第三方 backend
BRAVE_API_KEY=xxx pytest tests/kernel/tools/web/e2e/ -m e2e -k "Brave" -v

# E2E 测试 — 全量（需要所有 API key）
pytest tests/kernel/tools/web/e2e/ -m e2e -v
```

### 8.4 测试覆盖矩阵

| 组件 | 单元测试 | E2E 测试 |
|------|---------|---------|
| domain_filter | SSRF 各类 IP、凭证、blocklist | — |
| html_convert | 转换、剥离、截断 | — |
| fetch_with_fallback | mock backend 失败/anti-bot/成功 | 真实 example.com + httpbin |
| search_with_fallback | mock backend 异常/空结果/成功 | 真实 "python" 查询 |
| httpx backend | — | example.com, httpbin.org/json, redirect |
| 每个第三方 fetch backend | — | docs.python.org（skip if no key） |
| 每个第三方 search backend | — | "python programming"（skip if no key） |
| WebFetchTool | default_risk, permission_matcher, validate_input | — |
| WebSearchTool | default_risk | — |

---

## 9. 与 archived daemon 实现的对照

| daemon 工具 | kernel 对应 | 差异 |
|-------------|------------|------|
| `http_fetch` | WebFetchTool（httpx backend） | 合并进统一 Tool |
| `page_fetch` | WebFetchTool（Playwright backend） | 合并为 fallback 层 |
| `web_search` | WebSearchTool | 从 3 backend 扩展到 10 |
| `domain_filter` | `web/domain_filter.py` | 直接迁移 + 增强 |
| `web_backends/*` | `web/search_backends/*` | 大幅扩展 |


---

## Appendix: SendMessageTool + Agent Resume Design

# SendMessage + Agent Resume + ACP 跨 Session — Design

Status: **landed** — 全部实装。

> 2026-04-30 architecture note: the landed in-session / sub-agent resume behavior
> remains current, but the older ACP cross-session route described in this appendix
> is superseded for future work by
> [`agent-control-plane.md`](../../plans/agent-control-plane.md). New durable
> Agent-to-Agent communication must go through Agent Hub.Router; do not extend
> `SessionManager.deliver_message()` as the primary multi-agent transport.

> 蓝图来源：
> - Claude Code `src/tools/SendMessageTool/SendMessageTool.ts`
> - Claude Code `src/tools/AgentTool/resumeAgent.ts`
> - Claude Code `src/tasks/LocalAgentTask/LocalAgentTask.tsx` (`queuePendingMessage`, `drainPendingMessages`)
> - Claude Code `src/utils/attachments.ts` (`getAgentPendingMessageAttachments`)
> - DeepCLI `src/kernel/kernel/tools/builtin/agent.py` (现有 AgentTool)
> - DeepCLI `src/kernel/kernel/tasks/types.py` (`AgentTaskState.pending_messages` 已定义)
> - DeepCLI `src/kernel/kernel/tasks/registry.py` (现有 TaskRegistry)
> - DeepCLI `src/kernel/kernel/orchestrator/orchestrator.py` (query loop, step 0/6d)
> - DeepCLI `src/kernel/kernel/orchestrator/history.py` (`ConversationHistory`)
> - DeepCLI `src/kernel/kernel/session/__init__.py` (SessionManager, sub-agent event handling)
> - DeepCLI `docs/plans/task-manager.md` (Task framework 设计)

---

## 0. 动机

coverage doc 标记：

```
| ✅ AgentTool | ✅ Agent + SendMessage | 缺 SendMessage（续聊已有 sub-agent）|
```

当前 AgentTool 的 background 模式是"fire-and-forget"——父 agent spawn
一个 background agent，agent 跑完后父 agent 能看到结果通知，但无法：

1. 给**正在跑的** agent 追加指令（"顺便也查一下 X"）
2. 给**跑完的** agent 续聊追问（"你刚才说的 Y 具体是什么？"）
3. 按**名字**寻址 agent（只有 task_id，LLM 记不住）
4. **跨 session** 发消息（Claude Code 用 UDS，DeepCLI 应走 ACP）

Claude Code 的 SendMessageTool + resumeAgent 解决 1-3；UDS 解决 4。
本文档统一设计全部功能，适配 DeepCLI 架构。

---

## 1. 行为概述

### 1.1 SendMessageTool

LLM 调用 `SendMessage(to="explorer", message="also check the logs")`。

根据 `to` 的格式分三条路径：

**路径 A — in-session agent（name 或 task_id）**

1. resolve `to` → task_id（先查 name registry，再当 raw task_id）
2. 查 TaskRegistry 拿到 AgentTaskState
3. **Running** → `queue_message(task_id, msg)` 进 pending queue
4. **Stopped** → resume：用 transcript + message 重启 agent（§ 1.3）
5. **Not found** → 返回错误

**路径 B — cross-session（`to="session:<session-id>"`）**

1. 检测 `session:` prefix → 解析 target session_id
2. 通过 kernel 内部 API 将 message 投递到目标 session 的 pending_reminders
3. 目标 session 的 Orchestrator 在下一轮 STEP 0 收到

**路径 C — broadcast（`to="*"`）**

预留入口。返回 "broadcast not yet supported"。Team/Swarm 阶段实装。

### 1.2 Agent 命名

AgentTool 新增 `name` 参数（可选）。Background agent spawn 时，如果提供了
name，注册到 TaskRegistry 的 name→id 映射。

- Foreground agent 不可命名（同步阻塞，无需寻址）
- 重名 → 返回错误（不覆盖，与 Claude Code 一致）
- Name 生命周期 = TaskRegistry 生命周期 = session 级内存

### 1.3 Agent Resume（transcript 恢复续聊）

Stopped agent 被 SendMessage 续聊时：

1. 从 `AgentTaskState.transcript` 取出 agent 完成时保存的对话历史
2. 创建新的子 Orchestrator，`initial_history` = 已保存的 transcript
3. 以 message 作为新的 user prompt，继续 query loop
4. 走 background agent 流程（spawn task、通知、GC 同现有逻辑）
5. 复用原来的 name（如果有），更新 name→id 映射指向新 task_id

**Transcript 保存时机**：background agent 的 `_run_agent_background()` 在
agent 完成后，从子 Orchestrator 取出 `history.messages` 存入
`AgentTaskState.transcript`。纯内存，不持久化到 SQLite（name registry
也是内存的，一致性保持简单）。

### 1.4 Pending message 注入

Sub-agent 的 Orchestrator 在每轮 query loop 开头（STEP 0），检查自己的
`agent_id` 在 TaskRegistry 中是否有 pending messages。有则 drain 清空，
格式化为 `<system-reminder>` 注入到 user message。走现有的
`_drain_pending_reminders` → `_format_reminders` 路径。

### 1.5 Cross-session 消息投递（ACP 路径）

> Superseded: do not extend this path for future durable Agent-to-Agent
> messaging. The landed compatibility behavior may remain as a session reminder
> bridge, but new routing must use Agent Hub.Router from
> [`agent-control-plane.md`](../../plans/agent-control-plane.md).

Claude Code 用 UDS 做同机跨 session 通信。DeepCLI 的 kernel 已经管理
所有 session（SessionManager._sessions），天然支持内部路由：

1. SendMessageTool 检测 `session:` prefix → 拿到 target session_id
2. 调用 `SessionManager.deliver_message(target_session_id, message)`
3. SessionManager 查找目标 session → 放入 `pending_reminders`
4. 目标 session 的 Orchestrator 下一轮 STEP 0 收到

**与 UDS 的对比优势**：

| | Claude Code (UDS) | DeepCLI (ACP) |
|---|---|---|
| 发现 | 文件系统扫描 socket 文件 | SessionManager 直接查 |
| 传输 | Unix socket 文件 | 内部方法调用（同进程） |
| 寻址 | `uds:<socket-path>` | `session:<session-id>` |
| 跨机器 | 不支持 | ACP/WS 天然支持（future） |
| 认证 | 无 | 复用 ConnectionAuthenticator |

---

## 2. 模块清单

| 模块 | 文件 | 类型 | 说明 |
|------|------|------|------|
| SendMessageTool | `tools/builtin/send_message.py` | 新建 | 工具实现，三条路径 |
| AgentTool 扩展 | `tools/builtin/agent.py` | 修改 | `name` 参数 + name 注册 |
| AgentTaskState 扩展 | `tasks/types.py` | 修改 | 加 `transcript` 字段 |
| TaskRegistry 扩展 | `tasks/registry.py` | 修改 | name registry + message queue/drain |
| Orchestrator 注入 | `orchestrator/orchestrator.py` | 修改 | STEP 0 drain pending messages；spawn_subagent 捕获 transcript |
| SessionManager 扩展 | `session/__init__.py` | 修改 | `deliver_message()` 跨 session 投递 |
| 注册 | `tools/builtin/__init__.py` | 修改 | BUILTIN_TOOLS + __all__ |

---

## 3. 数据流

### 3.1 Running agent — queue path

```
Parent LLM
  → SendMessage(to="explorer", message="also check logs")
    → SendMessageTool.call()
      → registry.resolve_name("explorer") → task_id
      → registry.get(task_id) → AgentTaskState(status=running)
      → registry.queue_message(task_id, msg)
      → return "Message queued for delivery at next tool round"

Sub-agent Orchestrator (next iteration of query loop):
  → STEP 0: _drain_agent_messages(agent_id)
  → found ["also check logs"]
  → inject as <system-reminder>Message from parent:\nalso check logs</system-reminder>
  → LLM sees it, adjusts behavior
```

### 3.2 Stopped agent — resume path

```
Parent LLM
  → SendMessage(to="explorer", message="what did you mean by X?")
    → SendMessageTool.call()
      → registry.resolve_name("explorer") → task_id
      → registry.get(task_id) → AgentTaskState(status=completed)
      → task.transcript is not None → has conversation history
      → spawn new background agent:
          child = StandardOrchestrator(initial_history=task.transcript)
          child.query([TextContent(message)])
      → register new task, update name→id mapping
      → return "Agent 'explorer' resumed with your message"
```

### 3.3 Cross-session — ACP path

> Superseded for future work: keep only as compatibility documentation. New
> durable Agent messaging goes through Agent Hub.Router, not direct
> `SessionManager.deliver_message()`.

```
Session A's LLM
  → SendMessage(to="session:abc-123", message="status update?")
    → SendMessageTool.call()
      → detect "session:" prefix → target_id = "abc-123"
      → ctx.deliver_cross_session(target_id, message)
        → SessionManager.deliver_message("abc-123", message)
          → session = self._sessions["abc-123"]
          → session.pending_reminders.append(formatted_message)
      → return "Message delivered to session abc-123"

Session B's Orchestrator (next turn):
  → STEP 0: _drain_pending_reminders()
  → found ["Message from session ...: status update?"]
  → LLM sees it
```

---

## 4. 与现有系统的交互

### 4.1 TaskRegistry

现有 `AgentTaskState.pending_messages: list[str]` 已定义。需加：
- name → id 映射（`_name_to_id: dict[str, str]`）
- `register_name()` / `resolve_name()` / `unregister_name()`
- `queue_message()` / `drain_messages()`
- `evict_terminal()` 联动清理 name 映射

### 4.2 AgentTaskState

新增 `transcript: list[Message] | None` 字段，默认 `None`。
Agent 完成时由 `_run_agent_background()` 填入。

### 4.3 Orchestrator query loop

STEP 0 扩展（在 `_drain_pending_reminders` 之后）：

```
# 现有
reminders = _drain_pending_reminders(self._deps)

# 新增
if self._agent_id and self._deps.task_registry:
    agent_msgs = self._deps.task_registry.drain_messages(self._agent_id)
    for msg in agent_msgs:
        reminders.append(f"Message from parent agent:\n{msg}")
```

### 4.4 spawn_subagent closure

`_make_spawn_subagent()` 中，子 Orchestrator 完成后，提取
`child._history.messages` 并通过回调存入 AgentTaskState.transcript。

对于 background agent：`_run_agent_background()` 需要拿到子
Orchestrator 的 history。当前 `spawn_fn` 只 yield events，不暴露
history。两种方案：

- **方案 A**：`spawn_fn` 返回 `(events_gen, get_history_fn)` 元组
- **方案 B**：在 SubAgentEnd event 中附带 final history
- **方案 C**：`_run_agent_background` 通过 `spawn_fn` 的 kwarg 传入
  一个 callback，子 Orchestrator 结束时调用

方案 A 最直接：修改 `spawn_subagent` 的签名，增加一个 `capture_history`
kwarg，当为 True 时返回额外的 history accessor。只影响 background
agent path。

### 4.5 SessionManager — cross-session delivery

> Superseded for future work: this method is not the primary multi-agent
> transport. It may be retained only as a session reminder primitive or
> compatibility bridge during Agent Control Plane migration.

新增 `deliver_message(target_session_id, message, sender_session_id)` 方法：
- 查找 `_sessions[target_session_id]`
- 不存在 → raise / return error
- 存在 → `session.pending_reminders.append(formatted)`
- formatted 格式：`"Cross-session message from {sender}:\n{message}"`

这是一个内部方法，不暴露为 ACP 端点（安全考虑：cross-session 消息
由 kernel 内部路由，不允许客户端直接投递）。

### 4.6 ToolContext 扩展

新增 `deliver_cross_session` closure（类似 `queue_reminders`），由
ToolExecutor 从 OrchestratorDeps 注入。OrchestratorDeps 新增
`session_manager` 引用或一个 `deliver_cross_session` 闭包。

### 4.7 Notification 通知

Resumed agent 完成后，走现有 notification pipeline（step 6d drain →
system-reminder 通知 → evict）。不需要新机制。

### 4.8 GC / evict

`evict_terminal()` 扩展：联动清理 `_name_to_id` 中指向已驱逐 task 的条目。
注意：evict 时 transcript 也会被 GC，这是期望行为（session 级内存，
不跨 session 存活）。

---

## 5. SendMessageTool 行为细节

### 5.1 输入

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `to` | string | 是 | Agent name, task_id, `session:<id>`, 或 `*` (future) |
| `message` | string | 是 | 消息内容 |
| `summary` | string | 否 | UI 预览摘要（5-10 词） |

### 5.2 `to` 解析顺序

1. `*` → broadcast path（返回 not-yet-supported）
2. `session:<id>` → cross-session path
3. name registry lookup → 找到则走 in-session agent path
4. 当作 raw task_id → 找到则走 in-session agent path
5. 都找不到 → 返回错误

### 5.3 输出

成功：`{ "success": true, "message": "..." }`
失败：`{ "success": false, "message": "..." }`

### 5.4 权限

`default_risk = low`。`kind = execute`。与 Claude Code 一致。

### 5.5 并发安全

`is_concurrency_safe = True`。多个 SendMessage 可以并发执行。

### 5.6 延迟加载

不做 deferred。跟 AgentTool 同级，常用工具。

---

## 6. AgentTool 扩展

### 6.1 `name` 参数

新增可选输入字段 `name: string`。仅 background 模式生效。

- `_spawn_background()` 如果 name 非空，调用 `registry.register_name(name, task_id)`
- 重名 → 返回错误
- Foreground 忽略 name

### 6.2 Transcript 捕获

Background agent 完成后，`_run_agent_background()` 需要拿到子
Orchestrator 的 conversation history，存入 `AgentTaskState.transcript`。

修改 `spawn_subagent` closure：增加 `on_complete` callback kwarg，
子 Orchestrator 结束后回调传出 `history.messages`。
`_run_agent_background()` 在 callback 中写入 task state。

---

## 7. 不做的事

- **Team/Swarm 完整协议** — broadcast, shutdown, plan approval, permission
  继承。已列入 roadmap 作为独立 phase。
- **Structured message** — `shutdown_request` 等类型。Team 功能的一部分。
- **Transcript 持久化到 SQLite** — 当前 transcript 存在 AgentTaskState
  内存中（session 级）。跨 session 恢复需要 SQLite 存储，但 name registry
  也是内存的，两者一致。如果未来需要跨 session resume，同时做 name +
  transcript 的持久化。
- **跨机器 ACP** — `session:` 路径目前只支持同 kernel 内的 session。
  跨机器需要 ACP/WS relay，是 ACP 协议层的扩展。
