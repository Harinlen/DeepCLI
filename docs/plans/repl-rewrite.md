# REPL Tool 重写计划 — Python `exec` 化

**当前实现**: [`src/kernel/kernel/tools/builtin/repl.py`](../../src/kernel/kernel/tools/builtin/repl.py)（JSON BatchTool，待退役）
**CC 参考**: Claude Code main `/home/saki/.local/share/claude/versions/2.1.119`，binary offsets 562571 / 562600
**优先级**: P2（REPL flag 默认关闭，模型直接调工具能干活；要对齐 CC "少 turn 多操作"工作流必须实装）

---

## Part 1 — 设计

### 1.1 背景

2026-04-25 比对 CC main binary 的 REPL prose 与反编译代码后发现，DeepCLI
当前的 REPL 是一个 JSON 批量调度器（`{calls:[{tool_name,input},...]}` →
`<repl_result>` blocks），**和 CC 的 REPL 不是同一种东西**——CC 的 REPL 是
个跑 JavaScript 源码的执行环境。准确说当前 DeepCLI REPL 应叫 `BatchTool`。

### 1.2 当前实现 vs CC main

| 维度 | DeepCLI BatchTool | CC main REPL |
|------|--------|----|
| 输入 | `{calls: [...]}` JSON 数组 | JS 源码字符串 |
| 工具调用 | 数组里的 object | `await Read({...})` async 函数 |
| 控制流 | 顺序 / 并发 fixed batch | for / if / await / Promise.all |
| 变量持久化 | ✗ | ✓（跨 REPL call） |
| 表达式 / 返回值 | per-call 结果数组 | 最后表达式（或 `o`） |
| Shorthands | ✗ | sh / cat / rg / rgf / gl / put / chdir |
| 子模型采样 | ✗ | `haiku(prompt, schema?)` |
| 自定义工具 | ✗ | registerTool / unregisterTool / listTools / getTool |
| Output | indexed result blocks | stdout + stderr + return value |

### 1.3 隔离强度澄清

CC 的 REPL **不是真 sandbox**，是 Node `vm` 模块的 vm context：

```javascript
M = vm.createContext(
  { __proto__: null },
  { codeGeneration: { strings: true, wasm: false } }
);
vm.runInContext(userCode, M);
```

V8 层级：

```
Process → Isolate（堆/JIT/GC） → Context（globalThis） → 用户代码
```

`createContext` 只动 Context 那一层。Host 和 REPL 脚本 **同进程、同堆、同 GC、同 isolate**，
只是 globalThis 独立。等价于 Python `exec(code, fresh_globals_dict)`。

| | CC vm context | isolated-vm | child_process |
|---|---|---|---|
| 新 globalThis | ✓ | ✓ | ✓ |
| 独立堆 | ✗ | ✓ | ✓ |
| 独立 GC | ✗ | ✓ | ✓ |
| 跨界对象引用 | ✓ | ✗ | ✗ |
| 创建开销 | μs | ms | 10ms+ |
| OOM 互不影响 | ✗ | ✓ | ✓ |

CC 设计语境：模型脚本在用户本机，半可信；"sealed context" 防误污染 host globals，
不防恶意攻击。**不需要更强的隔离。**

### 1.4 候选方案对照

| 方案 | 描述 | 量级 | 与 CC 兼容度 | 决策 |
|------|------|------|------------|------|
| A. 嵌 JS 引擎（PyMiniRacer / pythonmonkey / quickjs） | Python 进程嵌入 JS runtime，跑 JS 源码 | 重（10-50MB native deps） | 高（行为完全一致） | ✗ |
| B. subprocess(deno/node) + IPC | 起 Node/Deno 子进程跑 JS | 中（runtime install） | 高 | ✗（IPC 开销 + 状态持久化复杂） |
| C. **Python `exec` + per-session globals** | LLM 写 Python，host 解释器跑 | **极轻（0 deps）** | 中（语义同形，语言不同） | **✓** |
| D. 自定义 mini-DSL | 自己定义表达式语言 | 重（实现解释器） | 低（LLM 要重学语法） | ✗ |
| E. 静态 graph DSL（YAML 计划） | 步骤 + 依赖描述 | 中 | 极低 | ✗ |

### 1.5 选定方案：Python `exec` + per-session globals

LLM 写 Python 源码，`exec()` 在 per-session globals dict 里执行；
工具作为 async function 注入 globals。架构上 **与 CC 在 vm context 里
跑 JS 等价**，只是把语言换成 Python。

理由：

1. **零外部依赖**：用标准库 `ast` / `asyncio` / `contextlib` / `io`
2. **隔离强度同 CC**：都是 host 进程内独立 globals
3. **LLM Python 写得也很好**，能力上没有牺牲
4. **DeepCLI 已经在跑 LLM 写的 Bash**，威胁模型已是"半信任 LLM"，
   Python `exec` 不会更糟
5. **原生 async**：`asyncio` 和 Python 协程天然集成
6. 实装量 ~500 行 Python

### 1.6 隔离强度对比（最终）

| 维度 | CC（Node vm） | DeepCLI（Python exec） |
|---|---|---|
| 独立 globals | ✓ | ✓（per-session dict） |
| 与 host 同进程同堆 | ✓ | ✓ |
| 禁 import / require | ✓（不注入） | ✓（AST 预扫 reject） |
| 禁 eval / new Function / `__import__` | ✗（CC 允许） | ✓（AST 预扫 reject）—**更严** |
| timeout abort | ✓ | ✓（asyncio.wait_for） |
| 内存 hard limit | ✗ | ✗ |
| 跨界对象 | 共享 | 共享 |
| Tool call 走权限层 | ✓ | ✓（每次走 ToolAuthorizer） |

---

## Part 2 — 架构

### 2.1 工具改造

| | 旧 BatchTool | 新 REPLTool |
|---|------|------|
| name | REPL | REPL |
| description_key | tools/repl | tools/repl |
| input_schema | `{calls: [{tool_name, input}, ...]}` | `{code: string}` |
| 引擎 | dispatch loop | `exec(compile(code, '<repl>', 'exec'), session_globals)` |
| 状态 | 无 | per-session globals dict |

旧 BatchTool 的语义短期保留——加 alias `BatchTool` 指向 dispatch 实现，
迁移期 LLM 仍能用旧式 `{calls: [...]}` 调用。**切换完成后退役 alias**。

### 2.2 Per-session globals 生命周期

`SessionState` 加字段：

```python
class SessionState:
    repl_globals: dict[str, Any] = field(default_factory=dict)
    repl_user_tools: dict[str, UserToolSpec] = field(default_factory=dict)
```

- 创建：session_new 时初始化空 dict + 注入 builtin shorthands + `o = {}`
- 复用：每次 REPL call 复用同一 dict —— 变量自然跨 call 持久
- 销毁：session 销毁时随 SessionState 一起回收
- 不进 SQLite：纯 in-memory，session 重启不保留（与 CC 行为一致）

### 2.3 Tool 注入

每个 builtin tool 包成 Python async function 写进 globals：

```python
def _make_tool_wrapper(tool_name: str, ctx: ToolContext) -> Callable:
    async def _wrapper(**input):
        return await ctx.run_tool(tool_name, input)  # 走 ToolAuthorizer
    _wrapper.__name__ = tool_name
    return _wrapper

builtin_wrappers = {
    "Read":      _make_tool_wrapper("FileRead", ctx),
    "Write":     _make_tool_wrapper("FileWrite", ctx),
    "Edit":      _make_tool_wrapper("FileEdit", ctx),
    "Glob":      _make_tool_wrapper("Glob", ctx),
    "Grep":      _make_tool_wrapper("Grep", ctx),
    "Bash":      _make_tool_wrapper("Bash", ctx),
    "Agent":     _make_tool_wrapper("Agent", ctx),
    # NotebookEdit when implemented
}
```

调用走的是和直接 tool call 完全一样的 ToolAuthorizer 链路，权限模型零变化。

### 2.4 Shorthands API（对齐 CC）

```python
async def sh(cmd: str, ms: int | None = None) -> str:
    """Shell command. stdout+stderr merged, never write 2>&1."""

async def cat(path: str, off: int | None = None, lim: int | None = None) -> str:
    """Read file content."""

async def rg(pat: str, path: str | None = None, **opts) -> str:
    """Match text (A/B/C/glob/head/type/i)."""

async def rgf(pat: str, path: str | None = None, glob: str | None = None) -> list[str]:
    """Matching file paths."""

async def gl(pat: str, path: str | None = None) -> list[str]:
    """Glob file paths."""

async def put(path: str, content: str) -> None:
    """Write file."""

def chdir(path: str) -> None:
    """Set cwd for this REPL call (mutates ToolContext.cwd)."""

async def haiku(prompt: str, schema: dict | None = None) -> Any:
    """One-turn sub-model sampling. Without schema returns text;
    with JSON schema returns parsed object. Goes through llm_provider's
    'compact' role (same as autocompact)."""

def registerTool(name: str, desc: str, schema: dict, handler: Callable) -> None:
    """Register a session-scoped tool. Persists for session lifetime."""

def unregisterTool(name: str) -> None: ...
def listTools() -> list[str]: ...
def getTool(name: str) -> dict: ...
```

行为约定（照搬 CC）：

- `sh` / `cat` / `rg` 失败时返回错误文本，不抛异常
- `rgf` / `gl` 失败时返回 `[]`，永不返回 `None`
- 权限被拒是 hard fail——抛异常给 LLM，让它 pivot

### 2.5 返回值约定

照搬 CC 的 `o` 模式：

1. globals 预埋 `o = {}`
2. LLM 脚本最后写 `o` 或赋值给 `o`
3. REPL 返回 `repr(globals_dict["o"])`
4. **Fallback**：用 `ast.parse` 检查最后一条 statement，若是 `Expression`，
   单独 `eval` 拿值；否则用 `o`

```python
tree = ast.parse(code)
if tree.body and isinstance(tree.body[-1], ast.Expr):
    body, last_expr = tree.body[:-1], tree.body[-1]
    exec(compile(ast.Module(body=body, type_ignores=[]), '<repl>', 'exec'), globals_)
    result = eval(compile(ast.Expression(body=last_expr.value), '<repl>', 'eval'), globals_)
else:
    exec(compile(tree, '<repl>', 'exec'), globals_)
    result = globals_.get("o")
```

### 2.6 AST 静态预扫

执行前 walk 一遍 AST，reject 危险节点。**CC 不做这层**，但我们成本极低。

**Reject rules**:

| AST 节点 | 规则 |
|---|---|
| `ast.Import` / `ast.ImportFrom` | 全 reject（CC 也禁） |
| `ast.Name` 引用 | reject 名字 in `{eval, exec, compile, __import__, open, globals, locals, vars, breakpoint}` |
| `ast.Attribute` | reject `__class__` / `__bases__` / `__subclasses__` / `__globals__` / `__builtins__` 等 dunder 逃逸 |
| `ast.Call` | 已被上面 Name/Attribute 拦截 |

**实现**: `class _ReplLinter(ast.NodeVisitor)`，遇到违规抛
`ToolInputError("disallowed: <reason>. Use sh()/cat()/Read() etc.")`，
带提示让 LLM pivot。

### 2.7 运行期保护

```python
async def _run_repl(code: str, globals_: dict, timeout: float = 60) -> ReplResult:
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    try:
        async with asyncio.timeout(timeout):
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                # eval-last-expr trick from §2.5
                result = await _exec_with_last_expr(code, globals_)
    except TimeoutError:
        return ReplResult(timeout=True, stdout=stdout_buf.getvalue(), stderr=stderr_buf.getvalue())
    except Exception as exc:
        return ReplResult(error=traceback.format_exc(), stdout=..., stderr=...)
    return ReplResult(value=result, stdout=stdout_buf.getvalue(), stderr=stderr_buf.getvalue())
```

- 超时：`asyncio.timeout`（默认 60s，可由 ConfigManager `tools.repl.timeout` 覆盖）
- stdout / stderr：`redirect_stdout` / `redirect_stderr` 截到 `StringIO`
- 不做 hard memory limit（同 CC）
- 异常：捕获 + format_exc 给 LLM，**不杀 session**

### 2.8 输出格式

```
<repl_result>
stdout:
...
stderr:
...
return:
{...}
</repl_result>
```

超时：

```
<repl_result>
TIMEOUT after 60s
stdout: ...
stderr: ...
</repl_result>
```

异常：

```
<repl_result>
ERROR: <traceback>
stdout: ...
stderr: ...
</repl_result>
```

### 2.9 Prompt 拆分

| 文件 | 内容 |
|---|---|
| `prompts/default/tools/repl.txt` | schema-level 简短："Execute Python code with access to DeepCLI tools as async functions. Variables persist across REPL calls within a session." |
| `prompts/default/orchestrator/session_guidance/repl_usage.txt` | dense scripts 引导 + API 速查 + 规则；feature-flag 控制注入；仿 CC prose 结构 |

`repl_usage.txt` 内容大纲：

```
REPL is your **only way** to investigate when REPL mode is on — shell, file
reads, and code search all happen here via the shorthands below.

**Aim for 1-3 REPL calls per turn** — over-fetch and batch.

## Dense scripts — every char is an output token
o["git"] = await sh("git status")
for f in (await rgf("X", "src"))[:5]:
    o[f] = await cat(f, 1, 300)

## API
- sh(cmd, ms=None)         → stdout+stderr merged
- cat(path, off=None, lim=None) → file content
- rg / rgf / gl / put / chdir / haiku / registerTool / ...

## Rules
- One investigation = one call.
- No `import` / `__import__` / `eval` / `exec` — sealed context.
- ≥3 ops per call. Over-fetch (3-5 files, 3-4 patterns).
- Variables persist across calls. Last expression (or `o`) = return value.
- Shorthands never throw — sh/cat/rg return error text on failure;
  rgf/gl return [], never None.
- Permission denied is hard fail — don't retry, pivot or stop.
```

---

## Part 3 — 实施计划

### 3.1 文件清单

**新增**:

| 文件 | 内容 | 估行 |
|---|---|---|
| `src/kernel/kernel/tools/builtin/repl_python.py` | RunPython 引擎、AST linter、tool/shorthand 注入 | 350 |
| `src/kernel/kernel/tools/repl/__init__.py` | repl 子模块入口 | 5 |
| `src/kernel/kernel/tools/repl/shorthands.py` | sh/cat/rg/rgf/gl/put/chdir/haiku 实现 | 200 |
| `src/kernel/kernel/tools/repl/linter.py` | `_ReplLinter(ast.NodeVisitor)` | 80 |
| `src/kernel/kernel/tools/repl/runner.py` | `_run_repl` + last-expr eval | 120 |
| `src/kernel/kernel/tools/repl/user_tools.py` | `registerTool` / `listTools` 等 | 60 |
| `src/kernel/kernel/prompts/default/tools/repl.txt` | 重写（schema-level 简短） | 5 |
| `src/kernel/kernel/prompts/default/orchestrator/session_guidance/repl_usage.txt` | dense scripts 引导 | 60 |
| `tests/kernel/tools/repl/test_linter.py` | AST linter 单测 | 150 |
| `tests/kernel/tools/repl/test_runner.py` | exec 引擎单测 | 200 |
| `tests/kernel/tools/repl/test_shorthands.py` | shorthands 单测 | 200 |
| `tests/e2e/test_repl_python_e2e.py` | E2E（真实 LLM 写 Python） | 200 |
| `tests/probe/probe_repl_python.py` | 独立 probe | 80 |

**修改**:

| 文件 | 改动 |
|---|---|
| `src/kernel/kernel/tools/builtin/repl.py` | 短期保留，加 deprecation log；切换完成后删除 |
| `src/kernel/kernel/tools/builtin/__init__.py` | `BUILTIN_TOOLS` 里换 RunPythonTool；`__all__` 更新 |
| `src/kernel/kernel/tools/__init__.py` | `ToolManager.startup` 不变 |
| `src/kernel/kernel/session/state.py` | `SessionState` 加 `repl_globals` / `repl_user_tools` 字段 |
| `src/kernel/kernel/orchestrator/orchestrator.py` | 注入 session-guidance 时检查 `repl_usage.txt` 的启用条件 |
| `src/kernel/kernel/tools/flags.py` | 加 `tools.repl.timeout` config field |
| `docs/kernel/subsystems/tools.md` | 加一节描述 REPL 新设计 |
| `docs/plans/backlog.md` | §12 改为 1-行指针 |
| `docs/plans/progress.md` | 加新条目跟踪 |

**删除**：完成后删 `src/kernel/kernel/tools/builtin/repl.py`（旧 BatchTool）。

### 3.2 Phase 拆分

| Phase | 范围 | done-criteria |
|---|---|---|
| **P1: 引擎骨架** | runner.py + linter.py + 最小 RunPythonTool | 单测：能 exec `o = 1+1`；能 reject `import os`；能截 stdout |
| **P2: Tool 注入** | builtin tools 包成 async function | 单测：`await Read(file_path=...)` 等价于直接调 FileRead；权限链路保持 |
| **P3: Shorthands** | sh / cat / rg / rgf / gl / put / chdir | 单测：每个 shorthand 一组用例；失败语义对 |
| **P4: Session 状态** | per-session globals 持久化 | 单测：第一次 call 设 `x=1`，第二次 call 能读到 |
| **P5: haiku / registerTool** | 子模型 + 用户工具 | 单测：haiku 路由到 compact role；registerTool 能在下次 call 用到 |
| **P6: Prompt** | repl.txt + repl_usage.txt | 跑 probe，确认 LLM 看到引导且能写出能跑的 dense script |
| **P7: E2E** | 真实 kernel + 真实 LLM | E2E：让 LLM 用 REPL 完成"统计 src/ 下 Python 文件数 + 列出最大 3 个"；assert 一次 REPL call 完成 |
| **P8: 退役** | 删 BatchTool；清理 alias | 旧 schema 不再被注册；e2e 全过 |

### 3.3 API 签名

**RunPythonTool**:

```python
class RunPythonTool(Tool[dict[str, Any], dict[str, Any]]):
    name = "REPL"
    description_key = "tools/repl"
    description = "Execute Python code with access to DeepCLI tools."
    kind = ToolKind.execute
    should_defer = False
    always_load = True
    cache = True
    max_result_size_chars = 200_000
    interrupt_behavior = "cancel"

    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute.",
            },
        },
        "required": ["code"],
    }

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def call(
        self, input: dict[str, Any], ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        ...
```

**Linter**:

```python
class ReplLinter(ast.NodeVisitor):
    def lint(self, tree: ast.AST) -> None:
        """Raise ToolInputError on disallowed nodes."""
```

**Runner**:

```python
async def run_repl(
    code: str,
    globals_: dict[str, Any],
    *,
    timeout: float,
) -> ReplResult:
    """Lint → compile → exec (last-expr eval) → capture."""

@dataclass(frozen=True)
class ReplResult:
    value: Any | None
    stdout: str
    stderr: str
    error: str | None = None
    timeout: bool = False
```

### 3.4 测试清单

**Linter 单测** (~20 case)：

- ✓ `o = 1+1` 通过
- ✗ `import os` 拒
- ✗ `from os import path` 拒
- ✗ `eval("1+1")` 拒
- ✗ `().__class__.__bases__` 拒
- ✗ `__builtins__["open"]` 拒
- ✓ `await Read(file_path="...")` 通过
- ✓ `for f in await rgf(...): ...` 通过

**Runner 单测**：

- exec 简单语句
- last-expr 自动 eval
- `o` 默认为 dict
- stdout / stderr 截获
- timeout 触发
- 异常捕获 + traceback
- `await` 跑通（asyncio.run / loop）
- globals 跨 call 持久化

**Shorthands 单测**：

- 每个 shorthand 一组：成功 / 失败 / 边界
- `sh` 失败返回错误文本不抛
- `rgf` 无匹配返回 `[]`
- `chdir` 改 `ToolContext.cwd`
- `haiku` 路由到 compact role（mock provider）
- `registerTool` 注册 + 后续 call 可见

**E2E**：

- `test_repl_basic_exec`：让 LLM 写 `await sh("echo hello")`，断 stdout 含 hello
- `test_repl_variable_persistence`：两次 call，第二次读第一次的变量
- `test_repl_dense_script`：让 LLM 一次 call 完成"找 src/ 下 *.py，统计行数 top 3"
- `test_repl_register_tool`：LLM 注册一个工具并在同 turn 内调用
- `test_repl_timeout`：让 LLM 写死循环，断超时

**Probe**：

- `probe_repl_python.py`：连真实 kernel，跑一个 dense script，打印 ReplResult
- 与现有 `probe_what_tools.py` 同款风格

### 3.5 Done criteria

- [ ] 所有 unit test 通过
- [ ] 所有 e2e test 通过
- [ ] probe 在真实 kernel 上跑通 dense script
- [ ] 旧 BatchTool 删除，无遗留 import
- [ ] `tools/repl.txt` + `repl_usage.txt` 写完
- [ ] `docs/kernel/subsystems/tools.md` 更新
- [ ] LLM 真实会话里："你现在有什么工具" 能 verbatim 描述新 REPL 用法
- [ ] PR 通过 review

### 3.6 退役 BatchTool 路径

1. P1-P7 期间，新 `RunPythonTool` 注册名 `REPL`，旧 `ReplTool` 改注册名 `BatchTool`
   并加 deprecation 日志
2. 让现有 e2e 跑一遍，确认 LLM 不再生成 `BatchTool` 调用（因为 prompt 改了）
3. P8：从 `BUILTIN_TOOLS` 删 `BatchTool`，删源文件
4. 旧 `REPL_HIDDEN_TOOLS` 常量 + REPL 模式逻辑保留（仍在新 REPL 下使用）

### 3.7 前置条件 / 阻塞项

- ✓ ToolAuthorizer 已 stable（每次 tool call 走它）
- ✓ ToolContext 已能 dispatch 工具
- ⚠️ `ctx.run_tool(name, input)` —— 需要新增。当前 ToolContext 没有
  这个 API，要加一个走 ToolAuthorizer + Registry.lookup + Tool.call 的统一入口
- ⚠️ `haiku()` 需要 LLMManager 暴露"单轮、不进 history、走 compact role"的入口，
  当前 `_make_summarise_closure` 接近但需要泛化
- ⚠️ SessionState 加字段需要协调 session resume 路径（`repl_globals` 不持久化，
  resume 后空 dict 起步——这与 CC 行为一致）

### 3.8 工作量估算

| 模块 | 新代码 | 单测 | 总行 |
|---|---|---|---|
| Engine（runner + linter） | 200 | 350 | 550 |
| Tool wrappers + shorthands | 280 | 200 | 480 |
| Session 状态 | 30 | 50 | 80 |
| haiku + user tools | 100 | 100 | 200 |
| Prompt | 65 | — | 65 |
| E2E + probe | — | 280 | 280 |
| 文档 | 80 | — | 80 |
| **合计** | **755** | **980** | **1735** |

**0 额外依赖**（标准库 ast / asyncio / contextlib / io / traceback）。

按 3-5 sessions（每 session 一个 phase）可走完。

### 3.9 风险

| 风险 | 缓解 |
|---|---|
| `exec` 的异常栈含内部细节，泄漏给 LLM | `traceback.format_exc()` 后做一次清洗，去掉 kernel 内部路径 |
| 死循环 + asyncio.timeout 不能强 kill 同步 Python 代码 | 文档警告；考虑用 signal.SIGALRM（仅 Unix）做兜底 |
| LLM 把巨大对象塞进 `o`，序列化爆掉 | `max_result_size_chars` 截断 + 提示 |
| User tool registration 内存泄漏 | session 销毁时 `repl_user_tools.clear()` |
| Python `exec` 拿不到 `await` 顶层语义 | 用 `compile(..., flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)`（3.8+） |

---

## 参考

- CC main binary：`/home/saki/.local/share/claude/versions/2.1.119`
  - REPL prose：strings offsets 562571（紧凑变体）/ 562600（详尽变体）
  - vm.createContext 调用：搜 `vm.createContext` / `runInContext`
- CC source（部分公开）：`~/Documents/projects/claude-code-main/src/tools/REPLTool/`
  - `constants.ts` —— `REPL_TOOL_NAME` / `REPL_ONLY_TOOLS` / `isReplModeEnabled`
  - `primitiveTools.ts` —— `getReplPrimitiveTools()`
  - `REPLTool.ts` —— **stripped from open-source checkout**（ant-only）
- Node `vm` module 文档：https://nodejs.org/api/vm.html
- Python `exec` + `ast`：https://docs.python.org/3/library/ast.html
