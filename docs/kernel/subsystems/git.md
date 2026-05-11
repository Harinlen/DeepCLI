# GitManager + EnterWorktree / ExitWorktree + Git Context Injection

> **Quick header**
> - **Role**: git context injection and worktree tool coordination.
> - **Current code**: `kernel.agents.mustang.git.*`, `tools/builtin/enter_worktree.py`, `tools/builtin/exit_worktree.py`.
> - **Runtime owner**: Mustang runtime, loaded after ToolManager.
> - **Boundary**: local git/worktree state only; session cwd persistence lives in `sessions/`.

> **Status**: landed
> **Phase**: TBD
> **Prerequisites**: None (all infrastructure in place)
> **CC Reference Files**:
> - `src/tools/EnterWorktreeTool/EnterWorktreeTool.ts`
> - `src/tools/ExitWorktreeTool/ExitWorktreeTool.ts`
> - `src/utils/worktree.ts` (1520 行, core logic)
> - `src/context.ts` (git status injection)
> - `src/utils/cwd.ts` (CWD 管理)
> - `src/constants/systemPromptSections.ts` (prompt section cache)

---

## 1. 目标

补齐 DeepCLI 三个缺口：

1. **GitManager Subsystem** — 集中所有 git 操作到一个 flag-gated 的
   Subsystem，统一 binary 探测、超时、错误处理、shutdown 清理。
2. **Git Context Injection** — PromptBuilder 当前仅注入 branch name，缺
   status / diff / log / user 等完整上下文。
3. **EnterWorktree / ExitWorktree** — 两个 deferred 工具，让 LLM 能够创建
   git worktree 进行隔离开发，完成后退出（保留或删除）。

---

## 2. Claude Code 实现分析

### 2.1 Git Context Injection

**位置**: `context.ts:36-111`

CC 在 session 启动时执行一次 `getGitStatus()`，并缓存（`@memoize`）：

```
并行执行 5 条 git 命令:
  git rev-parse --abbrev-ref HEAD        → branch
  git symbolic-ref refs/remotes/origin/HEAD → main branch
  git status --short                      → status (截断 2000 chars)
  git log --oneline -n 5                  → recent commits
  git config user.name                    → git user
```

输出格式：
```
gitStatus: This is the git status at the start of the conversation.
Note that this status is a snapshot in time, and will not update
during the conversation.

Current branch: <branch>
Main branch (you will usually use this for PRs): <mainBranch>
Git user: <user>

Status:
<status, truncated to 2000 chars>

Recent commits:
<5 latest commits>
```

**关键设计**：
- **Snapshot, not live** — 明确告知 LLM 这是快照，不会实时更新
- **Memoized** — 整个 session 只算一次（除非 EnterWorktree/ExitWorktree
  触发 `clearSystemPromptSections()`）
- **Truncated** — status 截断到 2000 chars，避免大 repo 爆 prompt
- 注入位置：作为 `systemContext.gitStatus` 传给 prompt assembler，
  在 system prompt 的动态区域

### 2.2 EnterWorktree

**位置**: `EnterWorktreeTool.ts` + `worktree.ts`

**流程**：
1. 验证：不在 worktree session 中
2. `findCanonicalGitRoot()` — 解析到主仓库根（处理嵌套 worktree）
3. `createWorktreeForSession(sessionId, slug)`:
   - `validateWorktreeSlug()` — max 64 chars, `[a-zA-Z0-9._-]`, 禁止 `..`
   - `getOrCreateWorktree()`:
     - Fast resume: 如果 worktree 路径已存在且 `.git` 指针有效，直接复用
     - Create: `git worktree add -B <branch> <path> <base>`
   - `performPostCreationSetup()`:
     - 复制 `settings.local.json`
     - 配置 git hooks（Husky / `.git/hooks`）
     - Symlink `node_modules` 等大目录
     - 复制 `.worktreeinclude` 文件
4. **CWD 切换**:
   - `process.chdir(worktreePath)`
   - `setCwd(worktreePath)`
   - `setOriginalCwd(worktreePath)`
5. **Cache 清除**:
   - `clearSystemPromptSections()` — 下一 turn 重新计算 git context
   - `clearMemoryFileCaches()` — memory 重新发现
   - `getPlansDirectory.cache.clear()` — plan 目录重定向

**Worktree 路径**: `.claude/worktrees/<slug>/`（相对于 git root）

### 2.3 ExitWorktree

**位置**: `ExitWorktreeTool.ts`

**参数**:
- `action`: `"keep"` | `"remove"`
- `discard_changes`: `boolean` (仅 remove 时)

**流程**:
1. 验证：必须在 `createWorktreeForSession()` 创建的 worktree 中
2. 如果 `action=remove`，检查未提交变更：
   - `countWorktreeChanges()` — uncommitted changes + unpushed commits
   - 有变更且 `discard_changes=false` → 报错
3. `action=keep`:
   - `keepWorktree()` — 解除 session 关联但保留 worktree 目录
4. `action=remove`:
   - `cleanupWorktree()`:
     - `git worktree remove --force <path>`
     - `git branch -D <branch>`
5. **CWD 恢复**:
   - `setCwd(originalCwd)`
   - `setOriginalCwd(originalCwd)`
6. **Cache 清除**: 同 EnterWorktree

### 2.4 CWD 管理

CC 维护三层 CWD：
- `cwdOverrideStorage` (AsyncLocalStorage) — per-async-context（并发 agent）
- `getCwdState()` — 全局 Claude Code state
- `getOriginalCwd()` — session 启动时的原始目录

**DeepCLI 对应**: `Orchestrator._cwd` (per-session) + `ToolContext.cwd`

### 2.5 Stale Worktree 清理

CC 有 `cleanupStaleAgentWorktrees()` 定期清理泄漏的 worktree：
- Agent worktree 匹配 `agent-a<7hex>` 模式
- 超过 30 天 + 无未提交变更 + 无未推送提交 → 删除

---

## 3. DeepCLI 适配设计

### 3.1 核心决策：GitManager Subsystem

**为什么需要集中管理**：

当前 git 调用散落在 `prompt_builder.py`（subprocess 取 branch），未来
worktree 工具还要加更多。如果不集中：
- 每个调用点各自处理 "git 找不到" 的 fallback
- 超时 / 错误处理不一致
- Flag 禁用要到处 if-else
- Worktree 清理无处落脚

**为什么是 Subsystem（不是 bootstrap service）**：
- **shutdown 清理** — agent 创建的 transient worktree 需要在 kernel
  shutdown 时清理（无变更的自动删除，有变更的记录日志）
- **Flag-gated** — `KernelFlags.git = False` 时整个 git 功能跳过
- **ConfigManager signal** — 用户可中途配置 `git.binary`，触发
  工具动态注册/注销

### 3.1.1 Startup 策略：永不失败 + `_available` 标志

GitManager 的 `startup()` **永远成功**，不抛异常。内部维护
`_available: bool` 标志：

```
startup():
    self._resolve_binary()  # 设置 _available
    self._subscribe_config()  # 监听 git.binary 变更
    self._sync_tools()  # 按 _available 注册/注销工具
```

**为什么不用 Subsystem 降级（startup 抛异常 → load 返回 None）**：

Subsystem 降级意味着 GitManager 实例不进 module_table。一旦
不在 module_table 中，就无法订阅 ConfigManager signal —— 用户
中途安装 git 或配置 `git.binary` 时没有任何接收者，只能重启
kernel 才能生效。这不符合需求。

选择 startup 永不失败，GitManager 始终存活在 module_table 中，
持续监听 config signal。用户中途装了 git 或改了配置 →
`_on_config_change()` → `_resolve_binary()` → `_available`
翻 True → `_sync_tools()` 注册工具 → 下一次 prompt build /
tool call 自动生效，无需重启。

### 3.1.2 Git Binary 解析优先级

```python
def _resolve_binary(self) -> None:
    """按优先级查找 git binary，更新 _available。"""
    # 1. 用户配置优先 (config.yaml: git.binary)
    user_bin = self._config_section.get("binary")  # e.g. "/usr/local/bin/git"
    if user_bin:
        resolved = shutil.which(user_bin)
        if resolved:
            self._git_bin = resolved
            self._available = True
            return
        logger.warning("Configured git binary %r not found", user_bin)

    # 2. 系统 PATH fallback
    system_bin = shutil.which("git")
    if system_bin:
        self._git_bin = system_bin
        self._available = True
        return

    # 3. 都没有 → 标记不可用
    self._git_bin = None
    self._available = False
    logger.info("Git binary not found — git features disabled")
```

### 3.1.3 动态工具注册/注销

用户中途配了 `git.binary` → ConfigManager signal → `_on_config_change()`
→ 重新解析 binary → `_sync_tools()`:

```python
def _sync_tools(self) -> None:
    """按 _available 状态动态注册/注销 worktree 工具。"""
    tool_mgr = self._module_table.get(ToolManager)
    if tool_mgr is None:
        return

    if self._available and not self._tools_registered:
        tool_mgr.register(EnterWorktreeTool(), layer="deferred")
        tool_mgr.register(ExitWorktreeTool(), layer="deferred")
        self._tools_registered = True
        logger.info("Git available — registered EnterWorktree/ExitWorktree")

    elif not self._available and self._tools_registered:
        tool_mgr.unregister("EnterWorktree")
        tool_mgr.unregister("ExitWorktree")
        self._tools_registered = False
        logger.info("Git unavailable — unregistered EnterWorktree/ExitWorktree")


def _on_config_change(self, new_section) -> None:
    """ConfigManager signal handler for git config changes."""
    self._resolve_binary()
    self._sync_tools()
    # 清除所有 context cache（binary 可能变了）
    self._context_cache.clear()
```

**已有先例**: MCPManager 在 MCP server 上线/下线时动态注册/注销
MCPAdapter 工具，机制相同。

**效果**：LLM 在 deferred listing 中看到的工具列表始终反映真实可用状态。
Git 不可用时 → 工具不存在 → LLM 不会尝试调用 → 不浪费 tool call 轮次。

### 3.2 GitManager 职责

```
kernel/agents/mustang/git/
├── __init__.py          # GitManager Subsystem
├── types.py             # GitContext, WorktreeSession dataclasses
├── executor.py          # 底层 git 命令执行 (async subprocess + timeout)
├── context.py           # git context snapshot (status/branch/log/user)
├── store.py             # WorktreeStore — SQLite 持久化 (kernel.db)
└── worktree.py          # worktree CRUD (create/cleanup/list/stale-gc)
```

| 职责 | 方法 | 消费者 |
|------|------|--------|
| Binary 解析 | `_resolve_binary()` (startup + config change) | 自身 |
| 动态工具注册 | `_sync_tools()` (按 `_available` 注册/注销) | 自身（startup + config signal） |
| 命令执行 | `run(args, cwd, timeout)` | 内部所有模块 |
| Context snapshot | `get_context(cwd) → GitContext` | PromptBuilder |
| Cache 失效 | `invalidate_context()` | worktree 工具 |
| Worktree 创建 | `create_worktree(root, slug)` | EnterWorktreeTool |
| Worktree 删除 | `remove_worktree(session)` | ExitWorktreeTool |
| Worktree 变更检查 | `count_changes(path)` | ExitWorktreeTool |
| Git root 解析 | `find_git_root(cwd)` | EnterWorktreeTool |
| Worktree 持久化 | `WorktreeStore` (SQLite `kernel.db`) | register/unregister + startup GC |
| Startup GC | `_gc_stale_worktrees()` | startup 时清理崩溃残留 |
| Shutdown 清理 | `shutdown()` 中执行 | lifespan |
| Session tracking | `register/unregister_worktree(session_id, ws)` | 工具 via Orchestrator |

### 3.3 与 CC 的关键差异

| 维度 | Claude Code | DeepCLI |
|------|-------------|---------|
| **进程模型** | 单进程 CLI | kernel 服务端 + 多 session |
| **Git 操作归属** | `utils/git.ts` + `utils/worktree.ts` 散落 | `kernel/agents/mustang/git/` 集中 Subsystem |
| **CWD 语义** | `process.chdir()` 全局 | per-session `_cwd`，不修改 OS CWD |
| **Prompt 缓存** | `systemPromptSections` 注册表 | `PromptBuilder` per-turn 重建 |
| **Worktree 路径** | `.claude/worktrees/<slug>/` | `.mustang/worktrees/<slug>/` |
| **Worktree 跟踪** | 模块级 `currentWorktreeSession` 单例 | GitManager 内 `dict[session_id, WorktreeSession]`（多 session） |
| **Post-setup** | Husky hooks, node_modules symlink | 不需要（Python 项目无等价物） |

### 3.4 不需要实现的部分

- **`performPostCreationSetup()`** — CC 的 Husky hooks/node_modules
  symlink/attribution hook 都是 JS 生态特有的。
- **tmux session** — CC 的 `--worktree` CLI 模式。DeepCLI 是服务端。

---

## 4. 实施计划

### Milestone 1: GitManager Subsystem + Git Context

**新包**: `kernel/agents/mustang/git/`

#### 4.1.1 types.py — 数据类型

```python
@dataclass(frozen=True)
class GitContext:
    """Session 启动时的 git snapshot，注入到 system prompt。"""
    branch: str
    main_branch: str
    user: str
    status: str          # 截断到 MAX_STATUS_CHARS
    recent_commits: str  # 最近 5 条 oneline

    def format(self) -> str:
        """CC 格式的 git context 字符串。"""
        return "\n".join([
            "gitStatus: This is the git status at the start of the"
            " conversation. Note that this status is a snapshot in time,"
            " and will not update during the conversation.",
            "",
            f"Current branch: {self.branch}",
            f"Main branch (you will usually use this for PRs): {self.main_branch}",
            f"Git user: {self.user}",
            "",
            "Status:",
            self.status or "(clean)",
            "",
            "Recent commits:",
            self.recent_commits or "(no commits)",
        ])


@dataclass
class WorktreeSession:
    """Tracks an active worktree entered via EnterWorktreeTool."""
    session_id: str
    original_cwd: Path
    worktree_path: Path
    worktree_branch: str
    slug: str
    created_at: datetime  # 用于 stale GC 判断
```

#### 4.1.2 executor.py — 命令执行

executor 是 GitManager 的实例方法（不是模块级函数），使用
`self._git_bin` 而非硬编码 `"git"`：

```python
DEFAULT_TIMEOUT = 5.0  # seconds

# GitManager 方法:

async def run(
    self,
    args: list[str],
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, str, str]:
    """执行 git 命令，返回 (returncode, stdout, stderr)。

    使用 self._git_bin（用户配置 > 系统 PATH），调用前须检查
    self._available。
    """
    assert self._git_bin is not None, "run() called when git unavailable"
    proc = await asyncio.create_subprocess_exec(
        self._git_bin, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise GitTimeoutError(f"git {args[0]} timed out after {timeout}s")
    return proc.returncode, stdout.decode(), stderr.decode()


async def run_ok(self, args: list[str], cwd: Path, **kw) -> str | None:
    """执行 git 命令，成功返回 stdout.strip()，失败返回 None。"""
    try:
        rc, out, _ = await self.run(args, cwd, **kw)
        return out.strip() if rc == 0 else None
    except GitTimeoutError:
        return None
```

所有 git 调用点统一经过这两个方法 — binary 路径、超时、错误处理一处搞定。
用户通过 ConfigManager 换了 `git.binary` → `_resolve_binary()` 更新
`self._git_bin` → 后续调用自动用新路径。

#### 4.1.3 context.py — Git Context Snapshot

```python
MAX_STATUS_CHARS = 2000

async def build_git_context(git_mgr: GitManager, cwd: Path) -> GitContext | None:
    """并行执行 5 条 git 命令，构建 GitContext。非 git 目录返回 None。"""
    branch, main_branch, status, log, user = await asyncio.gather(
        git_mgr.run_ok(["rev-parse", "--abbrev-ref", "HEAD"], cwd),
        git_mgr.run_ok(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd),
        git_mgr.run_ok(["--no-optional-locks", "status", "--short"], cwd),
        git_mgr.run_ok(["--no-optional-locks", "log", "--oneline", "-n", "5"], cwd),
        git_mgr.run_ok(["config", "user.name"], cwd),
    )
    if branch is None:
        return None

    # main branch: "origin/main" → "main"
    if main_branch:
        main_branch = main_branch.rsplit("/", 1)[-1]
    else:
        main_branch = "main"

    # truncate status
    if status and len(status) > MAX_STATUS_CHARS:
        status = status[:MAX_STATUS_CHARS] + "\n... (truncated)"

    return GitContext(
        branch=branch,
        main_branch=main_branch,
        user=user or "unknown",
        status=status or "",
        recent_commits=log or "",
    )
```

#### 4.1.4 store.py — WorktreeStore（SQLite 持久化）

WorktreeSession 写入 `kernel.db`（与 ScheduleManager 的 CronStore
共用同一个数据库），确保 kernel 崩溃后能恢复 worktree 跟踪信息。

```sql
-- kernel.db 新增表
CREATE TABLE IF NOT EXISTS worktrees (
    session_id   TEXT PRIMARY KEY,
    slug         TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    original_cwd TEXT NOT NULL,
    worktree_branch TEXT NOT NULL,
    created_at   TEXT NOT NULL  -- ISO 8601
);
```

```python
class WorktreeStore:
    """WorktreeSession 的 SQLite 持久层。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def open(self) -> None:
        """建表（如不存在）。复用 kernel.db 的连接模式。"""

    async def insert(self, ws: WorktreeSession) -> None:
        """创建 worktree 时写入。"""

    async def delete(self, session_id: str) -> None:
        """退出/清理 worktree 时删除。"""

    async def list_all(self) -> list[WorktreeSession]:
        """startup 时查询所有残留记录，用于 GC。"""
```

**GC 流程**（GitManager.startup 中调用）：

```python
async def _gc_stale_worktrees(self) -> None:
    """startup 时清理上次崩溃残留的 worktree。"""
    if not self._available:
        return
    stale = await self._store.list_all()
    for ws in stale:
        if not ws.worktree_path.exists():
            # 目录已被手动清理，只删记录
            await self._store.delete(ws.session_id)
            continue
        changes = await self._count_changes(ws.worktree_path)
        if changes == 0:
            await self._remove_worktree(ws)
            await self._store.delete(ws.session_id)
            logger.info("GC: cleaned up stale worktree %s", ws.slug)
        else:
            logger.warning(
                "GC: stale worktree %s has %d uncommitted change(s), keeping",
                ws.slug, changes,
            )
```

#### 4.1.5 `__init__.py` — GitManager Subsystem

```python
class GitManager(Subsystem):
    """集中管理所有 git 操作。

    startup() 永不失败 — 内部维护 _available 标志。
    用户可通过 ConfigManager 中途配置 git.binary，触发动态
    工具注册/注销。
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        super().__init__(module_table)
        self._git_bin: str | None = None
        self._available: bool = False
        self._tools_registered: bool = False
        self._store: WorktreeStore | None = None
        # session_id → WorktreeSession (内存热缓存，持久化在 SQLite)
        self._worktrees: dict[str, WorktreeSession] = {}
        # session_id → GitContext cache
        self._context_cache: dict[str, GitContext | None] = {}

    async def startup(self) -> None:
        # 1. 打开 WorktreeStore（kernel.db）
        db_path = self._module_table.state_dir / "kernel.db"
        self._store = WorktreeStore(db_path)
        await self._store.open()
        # 2. 解析 git binary（用户配置 > 系统 PATH > 不可用）
        self._resolve_binary()
        # 3. 监听 config 变更
        self._subscribe_config()
        # 4. 清理上次崩溃残留的 worktree
        await self._gc_stale_worktrees()
        # 5. 按 _available 注册/注销工具
        self._sync_tools()

    async def shutdown(self) -> None:
        # 清理所有 session 中未退出的 worktree
        for sid, ws in list(self._worktrees.items()):
            if not self._available:
                break  # 无法执行 git 命令，跳过清理
            try:
                changes = await self._count_changes(ws.worktree_path)
                if changes == 0:
                    await self._remove_worktree(ws)
                    await self._store.delete(sid)
                    logger.info("Cleaned up worktree %s (session %s)",
                                ws.slug, sid)
                else:
                    # 有变更 → 保留 worktree 和 DB 记录，下次 startup GC 再处理
                    logger.warning(
                        "Worktree %s has %d uncommitted change(s), keeping",
                        ws.slug, changes,
                    )
            except Exception:
                logger.exception("Failed to cleanup worktree %s", ws.slug)
        self._worktrees.clear()
        self._context_cache.clear()

    # --- Binary resolution + config signal ---

    def _resolve_binary(self) -> None:
        """按优先级查找 git binary，更新 _available。"""
        # 详见 § 3.1.2

    def _subscribe_config(self) -> None:
        """监听 ConfigManager 的 git section 变更。"""
        config = self._module_table.config
        config.subscribe("git", self._on_config_change)

    def _on_config_change(self, new_section) -> None:
        old_available = self._available
        self._resolve_binary()
        if self._available != old_available:
            self._sync_tools()
        self._context_cache.clear()  # binary 可能变了

    def _sync_tools(self) -> None:
        """按 _available 状态动态注册/注销 worktree 工具。"""
        # 详见 § 3.1.3

    # --- Public API: availability ---

    @property
    def available(self) -> bool:
        return self._available

    # --- Public API: git context ---

    async def get_context(self, cwd: Path, session_id: str) -> GitContext | None:
        """返回 session 级缓存的 git context，首次调用时计算。"""
        if not self._available:
            return None
        if session_id not in self._context_cache:
            self._context_cache[session_id] = await build_git_context(self, cwd)
        return self._context_cache.get(session_id)

    def invalidate_context(self, session_id: str) -> None:
        """Worktree 切换后刷新 git context。"""
        self._context_cache.pop(session_id, None)

    # --- Public API: worktree tracking ---

    async def register_worktree(self, ws: WorktreeSession) -> None:
        self._worktrees[ws.session_id] = ws
        await self._store.insert(ws)  # 持久化，crash-safe

    async def unregister_worktree(self, session_id: str) -> WorktreeSession | None:
        ws = self._worktrees.pop(session_id, None)
        await self._store.delete(session_id)  # 清除持久记录
        return ws

    def get_worktree(self, session_id: str) -> WorktreeSession | None:
        return self._worktrees.get(session_id)
```

#### 4.1.6 Flag + Lifespan 注册

```python
# flags/kernel_flags.py
git: bool = Field(True, description="Enable git subsystem")

# app.py — _OPTIONAL_SUBSYSTEMS 中加入（在 tools 之前）
("git", GitManager),
```

注意 GitManager 必须在 ToolManager 之前加载 —— `_sync_tools()`
在 startup 中调用 `tool_mgr.register()`，此时 ToolManager 必须
已经在 module_table 中。

但 `_OPTIONAL_SUBSYSTEMS` 的顺序是 mcp → tools → ...，git 应该
放在 tools **之后**（因为它要 register 到 ToolManager）。这意味着
`_sync_tools()` 在 startup 时如果 ToolManager 还没加载，就跳过；
等 ToolManager 加载后，GitManager 需要一个 "late sync" 机会。

**解法**：GitManager 放在 tools **之后**。startup 时 ToolManager
已可用（因为在 `_OPTIONAL_SUBSYSTEMS` 中 tools 排在前面），
`_sync_tools()` 正常工作。

```python
_OPTIONAL_SUBSYSTEMS = [
    ("mcp", MCPManager),
    ("tools", ToolManager),    # 先加载
    ("skills", SkillManager),
    ("hooks", HookManager),
    ("memory", MemoryManager),
    ("git", GitManager),       # 后加载，startup 时 ToolManager 已可用
]
```

#### 4.1.7 PromptBuilder 改动

```python
# orchestrator/prompt_builder.py

async def build(self, prompt_text: str = "") -> list[PromptSection]:
    sections = [...]

    # 2. Dynamic environment context
    sections.append(PromptSection(text=self._build_env_context(), cache=False))

    # 2.5 Git context — from GitManager (session-level cache)
    git_mgr = self._deps.git  # GitManager | None
    if git_mgr is not None:
        git_ctx = await git_mgr.get_context(
            cwd=self._deps.cwd,           # Orchestrator 当前 cwd
            session_id=self._session_id,
        )
        if git_ctx is not None:
            sections.append(PromptSection(text=git_ctx.format(), cache=False))

    # ... rest unchanged
```

`PromptBuilder._build_env_context()` 中现有的 `subprocess.run(git ...)` 删除
（git branch 信息已由 GitContext 覆盖）。

#### 4.1.8 OrchestratorDeps 改动

```python
# orchestrator/deps.py (or wherever OrchestratorDeps lives)
git: Any = None  # GitManager | None
```

SessionManager 构造 OrchestratorDeps 时从 module_table 取 GitManager。

### Milestone 2: context_modifier 管线补全

**问题**: `ToolCallResult.context_modifier` 已定义但 `ToolExecutor._run_one()`
从未消费它。EnterWorktree 需要通过 `context_modifier` 修改 `cwd`。

**改动范围**: `orchestrator/tool_executor.py` + `orchestrator/orchestrator.py`

**实现**:

在 `_run_one()` 的 step (7) 之前，消费 `context_modifier`:

```python
# tool_executor.py — _run_one() 中 step (6.5) 之后

# (6.7) apply context_modifier — cwd / env / worktree 等 session 状态变更
if final_result.context_modifier is not None:
    new_ctx = final_result.context_modifier(self._build_tool_context(...))
    if self._on_context_changed is not None:
        self._on_context_changed(new_ctx)
```

**Orchestrator 端**:
- `ToolExecutor.__init__` 接收 `on_context_changed: Callable` 回调
- Orchestrator 传入闭包：更新 `self._cwd`，调用
  `deps.git.invalidate_context(session_id)`

**设计选择 — 为什么用回调而非返回值**:
- `_run_one()` 是 async generator，已经在 yield event/content pair
- 返回值不方便传递状态变更（generator 没有 return value 语义）
- 回调（闭包）是最简单的 side-channel，与现有 `on_permission` 模式一致

### Milestone 3: EnterWorktreeTool（含 sparse checkout）

**新文件**: `kernel/agents/mustang/tools/builtin/enter_worktree.py`

**参数**:
```python
{
    "slug": str,              # worktree 名称, max 64 chars, [a-zA-Z0-9._-]
    "sparse_paths": list[str] | None,  # 可选，只 checkout 指定目录
}
```

**Tool 属性**:
- `name = "EnterWorktree"`
- `kind = "tool"` (有副作用)
- `should_defer = True` (通过 ToolSearch 加载)
- `default_risk = PermissionSuggestion("medium", "ask", "creates git worktree")`

**实现逻辑**:

```python
async def call(self, input, ctx) -> AsyncGenerator:
    slug = input["slug"]
    sparse_paths = input.get("sparse_paths")
    git_mgr: GitManager = ctx.git_manager  # 通过 ToolContext 注入

    # 1. 验证 slug
    validate_slug(slug)

    # 2. 验证不在 worktree session 中
    if git_mgr.get_worktree(ctx.session_id) is not None:
        raise ToolInputError("already in a worktree session")

    # 3. 找到 git root
    git_root = await find_git_root(git_mgr, ctx.cwd)

    # 4. 创建 worktree
    worktree_path, branch = await create_worktree(git_mgr, git_root, slug)

    # 5. sparse checkout（可选）
    if sparse_paths:
        await setup_sparse_checkout(git_mgr, worktree_path, sparse_paths)

    # 6. 注册到 GitManager（同时写入 SQLite）
    ws = WorktreeSession(
        session_id=ctx.session_id,
        original_cwd=ctx.cwd,
        worktree_path=worktree_path,
        worktree_branch=branch,
        slug=slug,
        created_at=datetime.utcnow(),
    )
    await git_mgr.register_worktree(ws)

    # 7. 返回结果 + context_modifier
    def modifier(old_ctx: ToolContext) -> ToolContext:
        return dataclasses.replace(old_ctx, cwd=worktree_path)

    msg = f"Entered worktree at {worktree_path} on branch {branch}"
    if sparse_paths:
        msg += f" (sparse: {', '.join(sparse_paths)})"

    yield ToolCallResult(
        data={"worktree_path": str(worktree_path), "branch": branch,
              "sparse_paths": sparse_paths},
        llm_content=[TextContent(text=msg)],
        display=TextDisplay(text=msg),
        context_modifier=modifier,
    )
```

**worktree.py 中的 Git 操作**:

```python
def validate_slug(slug: str) -> None:
    """CC 对齐的 slug 校验。"""
    if not slug or len(slug) > 64:
        raise ToolInputError("slug must be 1-64 characters")
    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise ToolInputError("slug must not contain '.' or '..' segments")
        if not re.match(r"^[a-zA-Z0-9._-]+$", segment):
            raise ToolInputError(
                f"slug segment '{segment}' contains invalid characters"
            )


async def find_git_root(git_mgr: GitManager, cwd: Path) -> Path:
    """找到 git root（处理嵌套 worktree → 回溯到 main repo）。"""
    toplevel = await git_mgr.run_ok(["rev-parse", "--show-toplevel"], cwd)
    if toplevel is None:
        raise ToolInputError("not in a git repository")

    root = Path(toplevel)
    git_dir = root / ".git"
    if git_dir.is_file():
        # 在 worktree 中 → 找 main repo
        common = await git_mgr.run_ok(
            ["rev-parse", "--git-common-dir"], cwd
        )
        if common:
            return Path(common).resolve().parent
    return root


async def create_worktree(
    git_mgr: GitManager, git_root: Path, slug: str,
) -> tuple[Path, str]:
    """创建 git worktree，返回 (worktree_path, branch_name)。"""
    worktree_dir = git_root / ".mustang" / "worktrees" / slug
    branch_name = f"worktree-{slug}"

    # Fast resume — 已存在且有效
    if worktree_dir.exists() and (worktree_dir / ".git").is_file():
        branch = await git_mgr.run_ok(
            ["rev-parse", "--abbrev-ref", "HEAD"], worktree_dir
        )
        return worktree_dir, branch or branch_name

    # 获取 HEAD 作为 base
    base = await git_mgr.run_ok(["rev-parse", "HEAD"], git_root)
    if base is None:
        raise ToolInputError("cannot determine HEAD — is this an empty repo?")

    rc, _, stderr = await git_mgr.run(
        ["worktree", "add", "-B", branch_name, str(worktree_dir), base],
        cwd=git_root,
    )
    if rc != 0:
        raise ToolInputError(f"git worktree add failed: {stderr.strip()}")

    return worktree_dir, branch_name


async def setup_sparse_checkout(
    git_mgr: GitManager,
    worktree_path: Path,
    paths: list[str],
) -> None:
    """在 worktree 中启用 sparse-checkout，只保留指定目录。

    CC 参考: worktree.ts:336-366
    """
    # 启用 sparse-checkout (cone mode)
    rc, _, stderr = await git_mgr.run(
        ["sparse-checkout", "init", "--cone"], worktree_path
    )
    if rc != 0:
        raise ToolInputError(f"sparse-checkout init failed: {stderr.strip()}")

    # 设置要 checkout 的目录
    rc, _, stderr = await git_mgr.run(
        ["sparse-checkout", "set", *paths], worktree_path
    )
    if rc != 0:
        raise ToolInputError(f"sparse-checkout set failed: {stderr.strip()}")


async def count_changes(git_mgr: GitManager, worktree_path: Path) -> int:
    """未提交变更数（uncommitted + untracked）。"""
    output = await git_mgr.run_ok(
        ["--no-optional-locks", "status", "--porcelain"], worktree_path
    )
    if output is None:
        return 0
    return len([l for l in output.splitlines() if l.strip()])


async def remove_worktree(git_mgr: GitManager, ws: WorktreeSession) -> None:
    """git worktree remove + branch delete。"""
    await git_mgr.run(
        ["worktree", "remove", "--force", str(ws.worktree_path)],
        cwd=ws.original_cwd,
    )
    # 删除分支（best-effort）
    await git_mgr.run_ok(["branch", "-D", ws.worktree_branch], ws.original_cwd)
```

### Milestone 4: ExitWorktreeTool

**新文件**: `kernel/agents/mustang/tools/builtin/exit_worktree.py`

**参数** (CC 对齐):
```python
{
    "action": "keep" | "remove",       # keep=保留 worktree, remove=删除
    "discard_changes": bool | None,     # 仅 remove 时，强制丢弃未提交变更
}
```

**Tool 属性**:
- `name = "ExitWorktree"`
- `should_defer = True`
- `default_risk`:
  - `action=keep` → `PermissionSuggestion("low", "allow", "keeps worktree")`
  - `action=remove` → `PermissionSuggestion("high", "ask", "removes worktree")`

**实现逻辑**:

```python
async def call(self, input, ctx) -> AsyncGenerator:
    action = input["action"]
    discard = input.get("discard_changes", False)
    git_mgr: GitManager = ctx.git_manager

    # 1. 验证在 worktree session 中
    ws = git_mgr.get_worktree(ctx.session_id)
    if ws is None:
        raise ToolInputError("not in a worktree session")

    # 2. remove 时检查未提交变更
    if action == "remove" and not discard:
        changes = await count_changes(git_mgr, ws.worktree_path)
        if changes > 0:
            raise ToolInputError(
                f"worktree has {changes} uncommitted change(s). "
                "Set discard_changes=true to force remove."
            )

    # 3. 执行
    if action == "remove":
        await remove_worktree(git_mgr, ws)

    # 4. 从 GitManager 注销（同时删除 SQLite 记录）
    await git_mgr.unregister_worktree(ctx.session_id)

    # 5. 恢复 cwd
    def modifier(old_ctx: ToolContext) -> ToolContext:
        return dataclasses.replace(old_ctx, cwd=ws.original_cwd)

    msg = (f"Removed worktree at {ws.worktree_path}"
           if action == "remove"
           else f"Exited worktree (kept at {ws.worktree_path})")

    yield ToolCallResult(
        data={"action": action, "original_cwd": str(ws.original_cwd)},
        llm_content=[TextContent(text=msg)],
        display=TextDisplay(text=msg),
        context_modifier=modifier,
    )
```

### Milestone 6: Session Resume（worktree cwd 恢复）

Session 重连时，从 DB 恢复 worktree 状态，Orchestrator cwd
自动切回 worktree 目录。

**改动范围**: `kernel/agents/mustang/git/__init__.py` + `kernel/agents/mustang/sessions/__init__.py`

**GitManager 新增方法**:

```python
async def restore_worktree_for_session(
    self, session_id: str,
) -> WorktreeSession | None:
    """Session 重连时查 DB，如果有 worktree 记录且目录有效则恢复。

    返回 WorktreeSession（恢复成功）或 None（无记录/目录失效）。
    """
    if not self._available or self._store is None:
        return None

    # 已在内存中 → 直接返回
    if session_id in self._worktrees:
        return self._worktrees[session_id]

    # 查 DB（按主键查询，不全表扫描）
    ws = await self._store.get_by_session(session_id)
    if ws is None:
        return None

    # 验证目录有效性
    if not ws.worktree_path.exists() or not (ws.worktree_path / ".git").is_file():
        # 目录已失效 → 清理 DB 记录
        await self._store.delete(session_id)
        logger.warning("Worktree %s no longer valid, cleaned DB record", ws.slug)
        return None

    # 恢复到内存
    self._worktrees[session_id] = ws
    self.invalidate_context(session_id)  # 重新计算 git context
    return ws
```

**SessionManager 改动**:

SessionManager 在 `_get_or_load` / session resume 路径中，构建
OrchestratorDeps 之前调用 `git_mgr.restore_worktree_for_session()`：

```python
# session/__init__.py — session resume 路径

git_mgr = self._module_table.get(GitManager)
if git_mgr is not None:
    ws = await git_mgr.restore_worktree_for_session(session_id)
    if ws is not None:
        initial_cwd = ws.worktree_path  # 恢复 cwd
    else:
        initial_cwd = original_cwd  # 正常 cwd
```

**WorktreeStore 改动**:

新增 `get_by_session()` 方法避免 list_all 全表扫描：

```python
async def get_by_session(self, session_id: str) -> WorktreeSession | None:
    """按 session_id 查询单条记录。"""
```

### Milestone 7: Worktree Startup Mode

ACP session 创建时可通过 `_meta` 扩展字段指定 worktree 参数，
session 从一开始就运行在 worktree 中（等价于 CC 的 `--worktree`
CLI flag）。

**改动范围**: `kernel/core/protocol/` + `kernel/agents/mustang/sessions/__init__.py`

**ACP 扩展**:

```python
# session/create 请求的 _meta 扩展
{
    "method": "session/create",
    "params": {
        "_meta": {
            "worktree": {
                "slug": "feature-x",
                "sparse_paths": ["src/", "tests/"]  # 可选
            }
        }
    }
}
```

**SessionManager 改动**:

session 创建时检查 `_meta.worktree`：

```python
# session/__init__.py — session create 路径

worktree_meta = create_params.meta.get("worktree") if create_params.meta else None
if worktree_meta and git_mgr is not None and git_mgr.available:
    slug = worktree_meta["slug"]
    sparse_paths = worktree_meta.get("sparse_paths")

    git_root = await find_git_root(git_mgr, original_cwd)
    worktree_path, branch = await create_worktree(git_mgr, git_root, slug)

    if sparse_paths:
        await setup_sparse_checkout(git_mgr, worktree_path, sparse_paths)

    ws = WorktreeSession(
        session_id=session_id,
        original_cwd=original_cwd,
        worktree_path=worktree_path,
        worktree_branch=branch,
        slug=slug,
        created_at=datetime.utcnow(),
    )
    await git_mgr.register_worktree(ws)
    initial_cwd = worktree_path
```

**与 EnterWorktree 的区别**:

| | EnterWorktree (M3) | Worktree Startup (M7) |
|---|---|---|
| 触发方式 | LLM tool call (mid-session) | ACP session create `_meta` |
| 时机 | 对话过程中 | session 创建时，Orchestrator 启动前 |
| context_modifier | 需要（运行时切换 cwd） | 不需要（cwd 一开始就是 worktree） |
| CC 对应 | `EnterWorktreeTool.ts` | `setup.ts:174-285` (`--worktree` flag) |

**Fallback**: worktree 创建失败时 session 仍正常启动（用原始 cwd），
错误记录到日志 + 作为 system-reminder 注入首轮对话。

### 改动文件汇总（全 Milestone 累计）

| 文件 | 改动 |
|------|------|
| `kernel/agents/mustang/git/__init__.py` | **新增** — GitManager Subsystem |
| `kernel/agents/mustang/git/types.py` | **新增** — GitContext, WorktreeSession |
| `kernel/agents/mustang/git/executor.py` | **新增** — run / run_ok (GitManager 方法) |
| `kernel/agents/mustang/git/context.py` | **新增** — build_git_context |
| `kernel/agents/mustang/git/store.py` | **新增** — WorktreeStore (SQLite `kernel.db`) |
| `kernel/agents/mustang/git/worktree.py` | **新增** — validate_slug / find_git_root / create / remove / count |
| `tools/builtin/enter_worktree.py` | **新增** — EnterWorktreeTool |
| `tools/builtin/exit_worktree.py` | **新增** — ExitWorktreeTool |
| `tools/context.py` | **改动** — 加 `git_manager` 字段 |
| `flags/kernel_flags.py` | **改动** — 加 `git: bool = True` |
| `app.py` | **改动** — `_OPTIONAL_SUBSYSTEMS` 加 `("git", GitManager)` |
| `orchestrator/prompt_builder.py` | **改动** — 删除内联 subprocess，改从 `deps.git` 取 context |
| `orchestrator/orchestrator.py` | **改动** — `on_context_changed` 闭包，调用 `git.invalidate_context()` |
| `orchestrator/tool_executor.py` | **改动** — 消费 `context_modifier` + `on_context_changed` 回调 |
| `session/__init__.py` | **改动** — OrchestratorDeps 注入 git + session resume worktree restore + session create worktree startup |

**工具注册由 GitManager 自管理**（不在 ToolManager.startup 中）：

GitManager.startup() 中调用 `_sync_tools()`，按 `_available` 状态
向 ToolManager 动态 register/unregister。后续 ConfigManager signal
触发时同样调用 `_sync_tools()`。

- GitManager 禁用（flag=False）→ 不加载 → 工具不注册
- GitManager 加载但 git 不可用 → `_available=False` → 工具不注册
- 用户中途配了 `git.binary` → config signal → `_available=True` → 工具注册
- 用户中途删了 `git.binary` → config signal → `_available=False` → 工具注销

LLM 的 deferred listing 始终反映真实可用状态。

---

## 5. 数据流

### 5.1 Git Context Injection

```
PromptBuilder.build()
  → deps.git.get_context(cwd, session_id)
    → [not available] return None
    → [cache hit] return cached GitContext
    → [cache miss] build_git_context(self, cwd)
      → asyncio.gather(5 × self.run_ok(...))
      → GitContext(branch, main_branch, user, status, log)
    → cache[session_id] = result
  → git_ctx.format() → PromptSection(text=..., cache=False)
```

### 5.2 EnterWorktree → CWD 切换 → Context 刷新

```
LLM calls EnterWorktree(slug="feature-x")
  → Tool validates + git_mgr.create_worktree()
  → git_mgr.register_worktree(ws)
  → yield ToolCallResult(context_modifier=lambda: cwd=worktree_path)
  → ToolExecutor consumes context_modifier
    → on_context_changed callback → Orchestrator
      → self._cwd = worktree_path
      → deps.git.invalidate_context(session_id)
  → Next turn: PromptBuilder.build()
    → git_mgr.get_context(worktree_path, session_id) → cache miss
    → 重新计算 → 新 branch / 新 status
```

### 5.3 用户中途配置 git.binary

```
用户编辑 config.yaml: git.binary = "/opt/homebrew/bin/git"
  → ConfigManager 检测变更 → fire signal("git")
  → GitManager._on_config_change()
    → _resolve_binary()
      → shutil.which("/opt/homebrew/bin/git") → 找到
      → _available = True, _git_bin = "/opt/homebrew/bin/git"
    → _sync_tools()
      → _tools_registered == False → register EnterWorktree + ExitWorktree
      → _tools_registered = True
    → _context_cache.clear()
  → 下一 turn: LLM 在 deferred listing 中看到 EnterWorktree/ExitWorktree
  → PromptBuilder 注入 git context
```

### 5.4 Startup GC（崩溃恢复）

```
GitManager.startup()
  → WorktreeStore.open() — 连接 kernel.db
  → _gc_stale_worktrees()
    → store.list_all() → [残留记录]
    → for each:
      → worktree_path 不存在 → 仅删 DB 记录
      → count_changes() == 0 → git worktree remove + 删 DB 记录
      → count_changes() > 0  → log warning，保留
```

### 5.5 Session Resume（worktree cwd 恢复）

```
Session reconnect (session_id = "abc123")
  → SessionManager._get_or_load("abc123")
  → git_mgr.restore_worktree_for_session("abc123")
    → 内存无记录 → store.get_by_session("abc123")
    → [DB 有记录] → 验证目录存在 + .git 有效
      → 恢复到内存 _worktrees["abc123"] = ws
      → invalidate_context("abc123")
      → return ws
    → [DB 无记录] → return None
  → ws != None → Orchestrator cwd = ws.worktree_path
  → 下一 turn: git context 基于 worktree 目录计算
```

### 5.6 Worktree Startup Mode

```
ACP session/create { _meta: { worktree: { slug: "feat-x" } } }
  → SessionManager 解析 _meta.worktree
  → git_mgr.available == True
  → find_git_root(git_mgr, original_cwd) → git_root
  → create_worktree(git_mgr, git_root, "feat-x") → worktree_path, branch
  → git_mgr.register_worktree(ws) → 内存 + DB
  → Orchestrator(cwd=worktree_path)
  → 首轮 prompt build: git context = worktree 的 branch/status
```

### 5.7 Kernel Shutdown 清理

```
lifespan shutdown → GitManager.unload() → shutdown()
  → for each 内存中的 WorktreeSession:
    → count_changes()
    → changes == 0 → remove_worktree() + store.delete()
    → changes > 0 → log warning，保留（DB 记录留给下次 startup GC）
  → clear caches
```

---

## 6. 安全考量

1. **Path traversal** — slug 校验禁止 `..`、只允许 `[a-zA-Z0-9._-]`
2. **Worktree session gating** — ExitWorktree 只能退出
   EnterWorktree 创建的 worktree（通过 GitManager registry），
   不影响用户手动创建的
3. **Uncommitted changes guard** — remove 操作前检查，需显式
   `discard_changes=true` 才能强制删除
4. **Git command timeout** — 所有 git subprocess 统一 5s timeout
5. **Shutdown 安全** — 只自动清理无变更的 worktree，有变更的保留并 log

---

## 7. 测试计划

### M1 GitManager + Git Context
- `tests/kernel/git/test_executor.py`:
  - run: 成功 / 失败 / 超时 / 使用自定义 git_bin
  - run_ok: 成功 → stdout / 失败 → None / 超时 → None
- `tests/kernel/git/test_context.py`:
  - git repo 中：验证 GitContext 各字段
  - 非 git 目录：返回 None
  - status 超长：截断到 2000 chars
  - 单个命令超时：其余正常返回
- `tests/kernel/git/test_manager.py`:
  - startup: git 存在 → available=True / 不存在 → available=False（不抛异常）
  - _resolve_binary: 用户配置优先 > 系统 PATH > 不可用
  - _sync_tools: available=True → 注册 / False → 不注册
  - config change: binary 变更 → 重新解析 → 工具注册/注销
  - config change: 从无到有 → available 翻 True → 注册
  - config change: 从有到无 → available 翻 False → 注销
  - get_context: available=False → None / 首次计算 + 缓存命中
  - invalidate_context: 清除缓存
  - register_worktree: 内存 + DB 双写
  - unregister_worktree: 内存 + DB 双删
  - shutdown: 无变更 → 清理 + 删 DB / 有变更 → 保留 DB 记录 / available=False → 跳过
- `tests/kernel/git/test_store.py`:
  - insert / delete / list_all 基本 CRUD
  - 重复 insert 同 session_id → 幂等或报错
  - list_all 空表 → 空列表
- `tests/kernel/git/test_gc.py`:
  - startup GC: DB 有残留 + 目录存在 + 无变更 → 清理
  - startup GC: DB 有残留 + 目录存在 + 有变更 → 保留
  - startup GC: DB 有残留 + 目录已不存在 → 仅删 DB 记录
  - startup GC: available=False → 跳过

### M2 context_modifier pipeline
- `tests/kernel/orchestrator/test_context_modifier.py`:
  - modifier 被调用 → on_context_changed 触发
  - modifier=None → 无副作用
  - modifier 异常 → 捕获，不影响 tool result

### M3 EnterWorktreeTool（含 sparse checkout）
- `tests/kernel/tools/test_enter_worktree.py`:
  - 正常创建 worktree → cwd 变更 + registered + DB 持久化
  - slug 校验（空、超长、`..`、特殊字符）
  - 已在 worktree 中 → 报错
  - 非 git repo → 报错
  - Fast resume（worktree 已存在）
  - sparse_paths → sparse-checkout init + set 被调用
  - sparse_paths=None → 不调用 sparse-checkout
- E2E: 真实 git repo，创建 worktree，验证 cwd 切换 + git context 刷新

### M4 ExitWorktreeTool
- `tests/kernel/tools/test_exit_worktree.py`:
  - keep → 保留目录，cwd 恢复，unregistered
  - remove → 目录删除，branch 删除，cwd 恢复
  - remove + 有未提交变更 → 报错
  - remove + discard_changes=true → 强制删除
  - 不在 worktree session → 报错
- E2E: enter → commit → exit remove，验证完整流程

### M5 Integration（贯穿 M1–M4，不单独实施）
- git context 在 enter/exit 后刷新
- GitManager flag 禁用 → 不加载 → 无工具 + 无 git context
- GitManager 加载但无 git → available=False → 无工具 + 无 git context
- 中途配置 git.binary → available 翻 True → 工具出现在 deferred listing
- 中途删除 git.binary → available 翻 False → 工具从 deferred listing 消失
- ToolSearch 能加载两个工具（when available）
- lifespan 测试：subsystem 数量更新

### M6 Session Resume（worktree cwd 恢复）
- `tests/kernel/git/test_session_resume.py`:
  - DB 有记录 + 目录有效 → restore_worktree_for_session 返回 ws
  - DB 有记录 + 目录不存在 → 返回 None + 删 DB
  - DB 无记录 → 返回 None
  - 恢复后 Orchestrator._cwd == worktree_path
  - 恢复后 git context 基于 worktree 目录计算
- E2E: enter worktree → 断连 → 重连同 session → cwd 恢复到 worktree

### M7 Worktree Startup Mode（session 创建时指定 worktree）
- `tests/kernel/git/test_worktree_startup.py`:
  - ACP session 创建时传 worktree 参数 → Orchestrator cwd 为 worktree
  - 无 git → 报错
  - slug 校验失败 → 报错
  - worktree 创建失败 → session 仍然正常启动（fallback 原 cwd）

---

## 8. 实施顺序

```
M1 GitManager + Git Context      ← 独立，可先落地（含 store, GC, flag, config signal）
  ↓
M2 context_modifier 管线          ← M3/M4 的前置
  ↓
M3 EnterWorktreeTool (含 sparse)  ←→  M4 ExitWorktreeTool (可并行)
  ↓                                    ↓
  └─── M5 集成测试（贯穿 M1–M4） ───┘
  ↓
M6 Session Resume                 ← DB 已有，增量改动
  ↓
M7 Worktree Startup Mode          ← 依赖 M6 的 restore 逻辑
```

预估工作量：M1 (1d) + M2 (0.5d) + M3 (1d) + M4 (0.5d)
+ M6 (0.5d) + M7 (0.5d) = **~4d**
