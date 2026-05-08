# SecretManager — Design

Status: **landed** — 全部实装（bootstrap 服务，Phase 16）。

> 前置阅读：
> - 架构子系统表：[kernel/architecture.md](../../kernel/architecture.md)
> - ConfigManager 实现：`kernel/config/manager.py`
> - MCP 连接状态机：[mcp.md](mcp.md)
> - Roadmap credential store 条目：[plans/roadmap.md](../../plans/roadmap.md) §Standing gaps
> - Hermes credential store：`hermes-agent/hermes_cli/auth.py`

---

## 1. 核心概念

**SecretManager 是凭证的存储与查询服务**。它在 kernel 启动
序列中 **排在 ConfigManager 之前**（与 FlagManager 同级的
bootstrap 服务），因为 ConfigManager 加载的 YAML 里可能包含
`${secret:name}` 引用，需要 SecretManager 已就绪才能展开。

**安全模型**：不做数据库加密。与 Hermes（明文 JSON + 0600）
和 Claude Code（Linux 回退路径也是明文 JSON + 0600）一致。
防线在文件权限 + LLM 隔离，不在加密——加密需要 master key，
而 key 放在同一台机器上等于锁和钥匙放在一起，徒增复杂度。

它**做**：
1. 管理一个 SQLite 数据库（`~/.deepcli/secrets.db`，0600 权限），
   使用 Python 标准库 `sqlite3`，零额外依赖
2. 提供 `get(name) → str | None` / `set(name, value, metadata)`
   / `delete(name)` CRUD API
3. 提供 `resolve(template) → str` 展开 `${secret:name}` 引用
4. LLM 隔离：不暴露任何 tool 给 LLM，不出现在 prompt 中（见 §6）

它**不**做：
- 不做数据库加密（见 §3.1 安全模型）
- 不做 MCP OAuth 流程编排（未来 OAuthFlow 子系统负责，本设计
  只提供 token 持久化的存储层）
- 不做 config 合并 / 分层（ConfigManager 负责）
- 不做运行时 config section 管理（config 的 `bind_section` /
  `get_section` 机制不用于 secrets）
- 不做 OS keychain 集成

---

## 2. 职责边界

| 组件 | SecretManager | ConfigManager | MCPManager | 未来 OAuthFlow |
|------|:---:|:---:|:---:|:---:|
| 凭证持久化存储 | ✅ | | | |
| `${secret:name}` 展开 | ✅ resolve | ✅ 调用时机 | | |
| YAML 分层合并 | | ✅ | | |
| MCP server 配置 | | ✅ | ✅ 消费 | |
| OAuth token 持久化 | ✅ 存取 | | | ✅ 写入 |
| OAuth 流程编排 | | | | ✅ |
| provider API key 存储 | ✅ | | | |
| MCP header token 存储 | ✅ | | | |

---

## 3. 存储引擎

### 3.1 安全模型

**不加密数据库。** 理由：

1. **参考项目都不加密** — Hermes 用明文 JSON（0600），Claude
   Code 在 Linux 上回退路径也是明文 JSON（0600）。没有先例
   证明加密 SQLite 在这个场景下有实际收益。
2. **锁和钥匙问题** — 数据库加密需要 master key。key 存在
   同一台机器上（key file / env var），任何能读 `~/.deepcli/`
   的进程同时拿到两者，加密形同虚设。
3. **真正的防线在别处** — 文件权限（0600）防其他用户读，
   LLM 隔离（§6）防 agent 泄露。这两层才是实际有效的防御。
4. **零依赖** — 标准库 `sqlite3`，不需要 apsw-sqlite3mc 的
   C 扩展编译、wheel 兼容性等问题。

**威胁模型**：

| 威胁 | 防御 |
|------|------|
| 其他系统用户读文件 | 0600 权限 |
| LLM 通过 tool 读取 | LLM 隔离（§6）— 无 tool、不进 prompt |
| root / 同用户恶意进程 | **不防御** — 超出范围，与 Hermes/CC 一致 |

### 3.2 数据库位置

```
~/.deepcli/secrets.db       # 0600 权限，owner-only
```

与 `~/.deepcli/config/`（用户编辑的意图声明）和
`~/.deepcli/state/`（运行时产物）分开。secrets.db 是独立文件，
不放在 config 目录下——ConfigManager 完全不知道它的存在。

**为什么用 SQLite 而不是像 Hermes 一样用 JSON**：
- OAuth token 有结构化关联数据（expiry、refresh、server_key），
  SQL 查询比 JSON 嵌套操作方便
- `PRAGMA user_version` 自动迁移，与 SessionManager 模式一致
- WAL 模式下并发安全，不需要像 Hermes 那样自己管 flock
- 未来 credential pool 扩展（多凭证轮转）时 SQL 天然支持

### 3.3 Schema

**Phase 1 schema**（`user_version = 1`）：

```sql
PRAGMA journal_mode = WAL;
PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;

-- 主表：所有凭证（static API keys, bearer tokens 等）
CREATE TABLE secrets (
    name        TEXT PRIMARY KEY,       -- 引用名：'anthropic-api-key'
    value       TEXT NOT NULL,          -- 明文值
    type        TEXT NOT NULL DEFAULT 'static',  -- static | bearer
    metadata    TEXT NOT NULL DEFAULT '{}',      -- JSON blob
    created_at  TEXT NOT NULL,          -- ISO 8601
    updated_at  TEXT NOT NULL           -- ISO 8601
);
```

**Phase 4 migration**（`user_version = 2`）：

```sql
-- OAuth 扩展表：仅 type='oauth' 的条目用
-- name 是 oauth token 的唯一标识，格式为 'oauth:<server_key>'
CREATE TABLE oauth_tokens (
    name            TEXT PRIMARY KEY REFERENCES secrets(name) ON DELETE CASCADE,
    refresh_token   TEXT,               -- nullable（有些 provider 不返回）
    expires_at      TEXT,               -- ISO 8601，nullable = 不过期
    client_config   TEXT DEFAULT '{}',  -- JSON: client_id, token_endpoint, etc.
    server_key      TEXT NOT NULL UNIQUE -- MCP server 标识，1:1 映射
);

PRAGMA user_version = 2;
```

**设计说明**：
- Phase 1 只建 `secrets` 表，OAuth 在 Phase 4 通过 migration 加入
- `oauth_tokens.server_key` 是 `UNIQUE` — 每个 MCP server 最多
  一组 OAuth 凭证。`name` 使用约定格式 `'oauth:<server_key>'`
  自动派生，调用方不需要指定
- `ON DELETE CASCADE` 保证删 secret 时 oauth 元数据自动清理

---

## 4. 公开 API

```python
class SecretManager:
    """Bootstrap service — credential store backed by SQLite.

    Loaded before ConfigManager.  Not a Subsystem subclass (same as
    FlagManager / ConfigManager): has a dedicated typed slot on
    KernelModuleTable.

    Security model: file permissions (0600) + LLM isolation.
    No database encryption — see design doc §3.1.
    """

    def __init__(
        self,
        *,
        db_path: Path | None = None,
    ) -> None:
        """
        Parameters
        ----------
        db_path:
            Override database location.  Defaults to
            ``~/.deepcli/secrets.db``.  Tests pass a tmp_path
            to stay hermetic.
        """

    async def startup(self) -> None:
        """Open (or create) the database, run migrations, set 0600."""

    def get(self, name: str) -> str | None:
        """Return the plaintext value, or None if not found."""

    def set(
        self,
        name: str,
        value: str,
        *,
        kind: str = "static",
        metadata: dict | None = None,
    ) -> None:
        """Insert or update a secret.

        Parameters
        ----------
        kind:
            Secret type — 'static' (API key), 'bearer' (manual token),
            'oauth' (managed by OAuthFlow).  Maps to ``type`` column.
        """

    def delete(self, name: str) -> bool:
        """Delete a secret.  Returns True if it existed."""

    def list_names(self, *, kind: str | None = None) -> list[str]:
        """Return secret names, optionally filtered by kind."""

    def resolve(self, template: str) -> str:
        """Expand ${secret:name} references in a string.

        Unknown references → raise SecretNotFoundError (fail loud,
        not silent empty like env var expansion).
        """

    # --- OAuth convenience (Phase 4, thin wrappers over get/set) ---
    # secret name 自动派生为 'oauth:<server_key>'，调用方只管 server_key。

    def get_oauth_token(self, server_key: str) -> OAuthToken | None:
        """Return the full OAuth token bundle for an MCP server.

        Looks up secrets row 'oauth:<server_key>' + oauth_tokens row.
        Returns None if no token stored for this server.
        """

    def set_oauth_token(self, server_key: str, token: OAuthToken) -> None:
        """Persist an OAuth token bundle (access + refresh + expiry).

        Upserts secrets row with name='oauth:<server_key>',
        type='oauth', value=access_token.  Then upserts the
        oauth_tokens row with refresh/expiry/client_config.
        """

    def delete_oauth_token(self, server_key: str) -> bool:
        """Delete OAuth token for a server.  CASCADE cleans oauth_tokens."""

    def close(self) -> None:
        """Close the database connection."""
```

```python
@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    client_config: dict[str, Any] = field(default_factory=dict)
```

### 4.1 同步 vs 异步

标准库 `sqlite3` 是同步的。secrets.db 非常小（通常 <100 行），
单次读写 <1ms。因此 `get` / `set` / `delete` / `resolve` 全部
**同步**，不走 `run_in_executor`。`startup` 是 async 只因为
bootstrap 服务统一用 async startup 签名。

### 4.2 文件权限强制

`startup()` 中：
```python
# 创建时设置 0600
if not self._db_path.exists():
    self._db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    self._db_path.touch(mode=0o600)

# 每次启动检查权限，如果被改了就修回来
current_mode = self._db_path.stat().st_mode & 0o777
if current_mode != 0o600:
    logger.warning(
        "secrets.db permissions were %o, fixing to 0600", current_mode
    )
    self._db_path.chmod(0o600)
```

---

## 5. 启动序列集成

SecretManager 插入到 FlagManager 和 ConfigManager 之间：

```
lifespan:
  0. FlagManager.initialize()          # 功能开关
  1. SecretManager.startup()           # ← NEW: 打开 DB
  2. ConfigManager.startup()           # 扫描 YAML，现在可以展开 ${secret:...}
  3. PromptManager.load()              # prompt 模板
  4. state_dir / module_table 构建
  5. core subsystems...
  ...
```

### 5.1 app.py 变更

```python
# --- 1. SecretManager (fatal on failure) ---
secrets = SecretManager()
try:
    await secrets.startup()
except Exception:
    logger.critical("SecretManager failed to start — aborting kernel")
    raise

# --- 2. ConfigManager (fatal on failure) ---
config = ConfigManager(secret_resolver=secrets.resolve)
#                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# ConfigManager 在 loader 阶段调用 resolver 展开 ${secret:...}
```

### 5.2 KernelModuleTable 变更

```python
class KernelModuleTable:
    def __init__(
        self,
        flags: FlagManager,
        secrets: SecretManager,     # ← NEW
        config: ConfigManager,
        state_dir: Path,
        prompts: PromptManager | None = None,
    ) -> None:
        self.flags = flags
        self.secrets = secrets      # ← NEW
        self.config = config
        self.state_dir = state_dir
        self.prompts = prompts
        self._subsystems: dict[type[Subsystem], Subsystem] = {}
```

### 5.3 ConfigManager 集成点

ConfigManager **不**依赖 SecretManager 的 Pydantic schema，
不 import 它。唯一的接触面是一个 `Callable[[str], str] | None`
类型的 `secret_resolver`，通过构造参数注入。

**展开时机：bind 时（late resolve），不在 collect 时。**

参考 OpenClaw 的模式：config 解析时只保留 `${secret:name}`
原文，真正展开推迟到消费时。Claude Code 做 early expansion
（`expandEnvVars` 在 `parseMcpConfig` 阶段），但那是因为它
只展开环境变量且不会失败。`${secret:name}` 未找到是 fatal，
late resolve 可以给出更精确的错误（知道是哪个 section 触发了
展开），并且避免改动 `loader.collect()` 签名。

```python
# config/manager.py 变更 — _get_or_create_section 中

raw_section = self._raw.get(file, {}).get(section) or {}
if not isinstance(raw_section, dict):
    raise ValueError(...)

# ← NEW: expand ${secret:name} before Pydantic validation
if self._secret_resolver is not None:
    raw_section = _expand_secrets_in_dict(raw_section, self._secret_resolver)

instance = schema.model_validate(raw_section)
```

```python
# config/manager.py — 新增辅助函数

_SECRET_RE = re.compile(r"\$\{secret:([^}]+)\}")

def _expand_secrets_in_dict(
    data: dict[str, Any],
    resolver: Callable[[str], str],
) -> dict[str, Any]:
    """Recursively expand ${secret:name} in leaf string values."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = _expand_secrets_in_dict(v, resolver)
        elif isinstance(v, list):
            out[k] = [_expand_in_value(item, resolver) for item in v]
        elif isinstance(v, str):
            out[k] = _SECRET_RE.sub(lambda m: resolver(m.group(1)), v)
        else:
            out[k] = v
    return out

def _expand_in_value(value: Any, resolver: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return _SECRET_RE.sub(lambda m: resolver(m.group(1)), value)
    if isinstance(value, dict):
        return _expand_secrets_in_dict(value, resolver)
    if isinstance(value, list):
        return [_expand_in_value(item, resolver) for item in value]
    return value
```

**要点**：
- `loader.collect()` 签名不变，raw dict 保留 `${secret:...}` 原文
- 展开发生在 `_get_or_create_section()` 中，YAML 解析之后、
  Pydantic `model_validate` 之前
- 递归处理 dict / list / leaf string，不展开 dict key
- `resolver` 为 `None` 时跳过（无 SecretManager 场景，如测试）

---

## 6. LLM 隔离

**核心原则：SecretManager 对 LLM 完全不可见。**

### 6.1 不暴露 Tool

SecretManager **不注册任何 Tool**。LLM 不能调用
`secret_get` / `secret_set` 等方法。凭证管理只能通过：
- CLI 命令（`/auth`，由 CommandManager 注册，直接调
  SecretManager API，不经过 orchestrator）
- 手动编辑 config 中的 `${secret:name}` 引用

### 6.2 不出现在 Prompt 中

- system prompt 不提及 SecretManager 的存在
- PromptBuilder 不注入任何 secret 相关内容
- SkillManager 不发现 secret 相关 skill

### 6.3 值不进入对话历史

ConfigManager 展开 `${secret:name}` 后，展开后的值只存在于
Pydantic config 对象的内存中（provider 拿到 API key 去调
LLM，MCP transport 拿到 header token 去建连）。这些值
**不会**出现在：

- conversation history / session events
- tool call input/output
- system prompt sections
- compaction summaries
- session event log（SQLite）

### 6.4 防御 prompt injection 读取

即使 LLM 被 prompt injection 诱导尝试读取凭证：

1. **无 tool 可调** — 没有 `secret_get` tool，这是主防线
2. **FileRead 有限防护** — SQLite 二进制格式让 FileRead 返回
   大量不可读内容，但 **secret 明文值可能出现在 raw page 中**
   （SQLite 不加密文本）。这不是可靠防线，只是增加了难度
3. **Bash 是主要攻击面** — LLM 可以尝试
   `sqlite3 ~/.deepcli/secrets.db 'SELECT * FROM secrets'`。
   防御依赖 ToolAuthorizer 的 content-scoped deny 规则。
   DeepCLI 的 `Bash(content)` 规则匹配委托给 Bash tool 的
   `prepare_permission_matcher()`，但**目前只支持 prefix
   match（`cmd:*`）和 exact match**，不支持 wildcard。
   Claude Code 的 `shellRuleMatching.ts` 已实现完整 wildcard
   （`*` 匹配任意字符序列）。**Phase 2 须**：
   1. 给 `BashTool.prepare_permission_matcher()` 加 wildcard
      支持（`fnmatch` 风格，`*` 匹配任意）
   2. 在默认 user-layer 规则中加入：
      ```yaml
      deny:
        - "Bash(sqlite3:*)"
        - "Bash(*secrets.db*)"
      ```
4. **env var 不泄露** — secret 不存在于环境变量中

### 6.5 资源开销

零额外资源：
- 无后台线程 / 无定时任务 / 无 watcher
- 数据库 startup 时建连，保持到 shutdown
- 单连接，无连接池（secrets.db 只有 kernel 进程访问）

---

## 7. CLI 命令（/auth）

SecretManager 不做 CLI 交互。

### 7.1 路由设计

**学 Hermes：auth 不走 session 层。** Hermes 的 auth 是 CLI
直调函数，不经过 gateway/agent 命令分发。Claude Code 的
`/login` 走 session 层（`local-jsx` 命令），但那是因为它需要
渲染 OAuth 浏览器 UI——我们不需要。

`/auth` 操作全局 SecretManager，与 session 状态无关。走
`target="session"` 概念不干净。方案：

**新增 `HandlerTarget = "secrets"`**：

```python
# routing.py
HandlerTarget = Literal["session", "model", "secrets"]
```

```python
# session_handler.py — _get_handler_for()
if target == "secrets":
    return self._module_table.secrets
```

这样 `/auth` 路由到 SecretManager 本身，不经过 SessionHandler。

### 7.2 CommandManager 注册

在 `_BUILTIN_COMMANDS` 中新增 `CommandDef`：

```python
CommandDef(
    name="auth",
    description="Manage stored credentials",
    usage="/auth set|get|list|delete|import-env ...",
    acp_method="secrets/auth",
    subcommands=["set", "get", "list", "delete", "import-env"],
)
```

### 7.3 ACP 路由 + Schema

```python
# routing.py
"secrets/auth": RequestSpec(
    handler=_handle_auth,
    params_type=AuthRequest,
    result_type=AuthResult,
    target="secrets",
)
```

ACP Schema（新建 `protocol/acp/schemas/auth.py`）：

```python
class AuthRequest(BaseModel):
    action: Literal["set", "get", "list", "delete", "import_env"]
    name: str | None = None
    value: str | None = None
    kind: str | None = None       # static | bearer | oauth
    env_var: str | None = None    # import_env 用

class AuthResult(BaseModel):
    value: str | None = None      # get 返回 masked 值
    names: list[str] | None = None  # list 返回
    ok: bool = True
```

Handler wrapper：

```python
async def _handle_auth(
    sm: SecretManager, ctx: HandlerContext, p: AuthRequest
) -> AuthResult:
    match p.action:
        case "set":
            sm.set(p.name, p.value, kind=p.kind or "static")
            return AuthResult()
        case "get":
            val = sm.get(p.name)
            return AuthResult(value=_mask(val))
        case "list":
            return AuthResult(names=sm.list_names(kind=p.kind))
        case "delete":
            sm.delete(p.name)
            return AuthResult()
        case "import_env":
            val = os.environ.get(p.env_var)
            if val is None:
                raise SecretNotFoundError(f"env var {p.env_var!r} not set")
            sm.set(p.name, val)
            return AuthResult()
```

### 7.4 Gateway 路径

Gateway 场景（Discord 等）不支持 `/auth`——凭证管理只允许
通过本机 ACP 连接：

```python
# gateways/base.py _execute_for_channel() 中
if name == "auth":
    return "/auth is only available via local ACP connection."
```

### 7.5 子命令

```
/auth set <name> <value>               # 写入 static secret
/auth set <name> <value> --kind bearer # 写入 bearer token
/auth get <name>                       # 显示值（masked: ****xxxx）
/auth list                             # 列出所有 secret 名称
/auth list --kind oauth                # 按类型过滤
/auth delete <name>                    # 删除
/auth import-env <VAR> <name>          # 从环境变量导入为 secret
```

不经过 orchestrator，不进入对话历史。

---

## 8. 错误处理

```python
class SecretError(Exception):
    """Base class for SecretManager errors."""

class SecretNotFoundError(SecretError):
    """Referenced secret does not exist in the store."""

class SecretDatabaseError(SecretError):
    """Database corruption or I/O error."""
```

### 8.1 错误策略

| 场景 | 行为 |
|------|------|
| DB 文件不存在 | 自动创建 + 初始化 schema |
| `${secret:name}` 引用不存在的 name | `SecretNotFoundError`，**fatal** — ConfigManager 无法启动 |
| DB schema 版本不匹配 | 自动迁移（`PRAGMA user_version`，同 SessionManager 模式） |
| 写入时磁盘满 | SQLite 原生错误，传播为 `SecretDatabaseError` |
| DB 文件权限异常 | 修复为 0600 + warning log |

### 8.2 为什么 `${secret:name}` 未找到是 fatal

与 `$VAR` 展开（未定义 → 空字符串）不同，secret 引用未找到
意味着配置有误（用户写了引用但忘了存入 secret），静默变空会
导致下游 provider 拿到空 API key 然后报错，调试困难。Fail
loud, fail early。

### 8.3 Secret 更新后需要重启 kernel

`${secret:name}` 展开发生在 config section 首次 bind/get 时
（late resolve），之后 Pydantic 对象缓存在 `_Section._current`
中，不会重新展开。**通过 `/auth set` 更新 secret 值后，
已缓存的 config section 不会自动刷新——需要重启 kernel。**

这与 Hermes 行为一致：Hermes 的 gateway 在启动时读取
`auth.json`，运行中修改 auth.json 不影响已运行的 session，
需要重启。Claude Code 通过 React state 的 `authVersion`
计数器实现了热刷新，但那是 UI 框架的能力，kernel 层没有。

未来可通过 SecretManager 的 `on_changed` signal + ConfigManager
section invalidation 实现热刷新，但首期不做。

### 8.4 Windows 文件权限

`Path.chmod(0o600)` 在 Windows 上是 no-op（POSIX 权限不适用）。
Claude Code 和 Hermes **都不做 Windows 特殊处理**——两者都
无条件调用 chmod，靠 OS 静默忽略。DeepCLI 同样：

- POSIX（Linux/macOS）：chmod 0600 有效
- Windows：chmod 静默忽略，依赖用户 home 目录的默认 ACL +
  LLM 隔离（§6）作为防线

不做 Windows ACL 设置。这与两个参考项目一致。

---

## 9. 测试策略

### 9.1 单元测试

```python
# tests/kernel/test_secret_manager.py

async def test_set_get_roundtrip(tmp_path):
    """Basic CRUD: set → get → verify."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    sm.set("api-key", "sk-12345")
    assert sm.get("api-key") == "sk-12345"

async def test_resolve_template(tmp_path):
    """${secret:name} expansion."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    sm.set("token", "abc")
    assert sm.resolve("Bearer ${secret:token}") == "Bearer abc"

async def test_resolve_missing_raises(tmp_path):
    """Unknown ${secret:name} → SecretNotFoundError."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    with pytest.raises(SecretNotFoundError):
        sm.resolve("${secret:nonexistent}")

async def test_no_expansion_without_pattern(tmp_path):
    """Strings without ${secret:...} pass through unchanged."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    assert sm.resolve("plain string") == "plain string"
    assert sm.resolve("$OTHER_VAR") == "$OTHER_VAR"

async def test_file_permissions(tmp_path):
    """DB file created with 0600 permissions."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    mode = (tmp_path / "secrets.db").stat().st_mode & 0o777
    assert mode == 0o600

async def test_permissions_auto_repair(tmp_path):
    """If permissions are wrong, startup fixes them."""
    db = tmp_path / "secrets.db"
    sm = SecretManager(db_path=db)
    await sm.startup()
    sm.close()
    db.chmod(0o644)  # simulate tampering
    sm2 = SecretManager(db_path=db)
    await sm2.startup()
    assert db.stat().st_mode & 0o777 == 0o600

async def test_oauth_token_roundtrip(tmp_path):
    """OAuth token bundle set → get with all fields."""

async def test_list_names_filtered(tmp_path):
    """list_names(kind='oauth') only returns oauth secrets."""

async def test_delete_cascades_oauth(tmp_path):
    """Deleting a secret with oauth_tokens cascades."""

async def test_file_is_sqlite_binary(tmp_path):
    """DB file is SQLite binary, not human-readable text like JSON."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    sm.set("password", "super-secret-value-12345")
    sm.close()
    raw = (tmp_path / "secrets.db").read_bytes()
    # SQLite header magic
    assert raw[:6] == b"SQLite"
    # Note: secret values MAY appear in raw pages as plain text.
    # This is NOT a security guarantee — defense is via file
    # permissions (0600) + LLM tool isolation, not file format.
```

### 9.2 集成测试

```python
# tests/kernel/test_secret_config_integration.py

async def test_config_resolves_secrets(tmp_path):
    """ConfigManager expands ${secret:name} from SecretManager."""
    # 1. Create SecretManager with a test secret
    # 2. Create ConfigManager with secret_resolver=sm.resolve
    # 3. Write config YAML with ${secret:my-key}
    # 4. Verify bind_section returns resolved value
```

### 9.3 E2E 测试

```python
# tests/e2e/test_secret_e2e.py

async def test_full_boot_with_secrets(tmp_path):
    """Kernel boots with secrets → config → MCP using resolved secrets."""
    # 1. Pre-populate secrets.db with MCP server header token
    # 2. Write mcp.yaml with ${secret:mcp-token} in headers
    # 3. Boot kernel (abbreviated lifespan)
    # 4. Verify MCPManager receives resolved headers
```

---

## 10. 文件清单

### Phase 1: 核心存储

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `kernel/secrets/__init__.py` | SecretManager 类 |
| 新建 | `kernel/secrets/types.py` | SecretError, SecretNotFoundError, SecretDatabaseError, OAuthToken |
| 新建 | `tests/kernel/test_secret_manager.py` | 单元测试 |

### Phase 2: ConfigManager 集成 + LLM 隔离

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `kernel/app.py` | lifespan 加 SecretManager 启动（FlagManager 之后，ConfigManager 之前） |
| 修改 | `kernel/module_table.py` | 新增 `secrets: SecretManager` 字段 |
| 修改 | `kernel/config/manager.py` | 新增 `secret_resolver` 构造参数 + `_expand_secrets_in_dict` |
| 修改 | `kernel/tools/builtin/bash.py` | `prepare_permission_matcher()` 加 wildcard（`fnmatch`）支持 |
| 新建 | `tests/kernel/test_secret_config_integration.py` | 集成测试 |

### Phase 3: CLI 命令

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `kernel/commands/__init__.py` | `_BUILTIN_COMMANDS` 加 `/auth` CommandDef |
| 修改 | `kernel/protocol/acp/routing.py` | `HandlerTarget` 加 `"secrets"` + `REQUEST_DISPATCH` 加 `secrets/auth` |
| 新建 | `kernel/protocol/acp/schemas/auth.py` | `AuthRequest` + `AuthResult` ACP schema |
| 修改 | `kernel/protocol/acp/session_handler.py` | `_get_handler_for` 加 `"secrets"` 分支 |
| 修改 | `kernel/gateways/base.py` | `/auth` gateway 拒绝 |

### Phase 4: OAuth 存储层

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `kernel/secrets/__init__.py` | migration v2 + `get_oauth_token` / `set_oauth_token` / `delete_oauth_token` |
| 新建 | `tests/kernel/test_secret_oauth.py` | OAuth token 单元测试 |

---

## 11. 分阶段实施

| Phase | 内容 | 依赖 |
|-------|------|------|
| **Phase 1: 核心存储** | SecretManager 类 + sqlite3 标准库 + CRUD API + `resolve()` + 0600 权限 + 单元测试 | 无 |
| **Phase 2: ConfigManager 集成 + LLM 隔离** | `${secret:name}` late resolve 接入 `_get_or_create_section()` + KernelModuleTable 新增 `secrets` 字段 + lifespan 启动顺序调整 + BashTool wildcard matcher 扩展 + 默认 deny `Bash(*secrets.db*)` 规则 + 集成测试 | Phase 1 |
| **Phase 3: CLI 命令** | `/auth` CommandDef + ACP `secrets/auth` RequestSpec + `HandlerTarget` 扩展 + Gateway 拒绝 | Phase 2 |
| **Phase 4: OAuth 存储层** | `oauth_tokens` 表 migration v2 + `get_oauth_token` / `set_oauth_token` / `delete_oauth_token` | Phase 2 |

---

## 12. Claude Code / Hermes 源码映射

| 参考项目 | 文件 | mustang 归属 | 说明 |
|---|---|---|---|
| Hermes | `hermes_cli/auth.py` | SecretManager | 直接对标：JSON→SQLite，flock→WAL |
| Hermes | `hermes_cli/auth_commands.py` | Phase 3 `/auth` 命令 | CLI 直调，不走 session——mustang 同 |
| Claude Code | `services/mcp/auth.ts` | SecretManager (存储) + 未来 OAuthFlow (流程) | CC 把存储和流程合在一起，mustang 分开 |
| Claude Code | `utils/secureStorage/*` | 不移植 | OS keychain，体验差，不做 |
| Claude Code | `constants/oauth.ts` | 未来 OAuthFlow | scope 定义、endpoint 配置 |
| Hermes | `agent/credential_pool.py` | 未来扩展 | 多凭证轮转，当前不做 |

---

## 13. 已知限制

1. **不防同用户进程** — 任何以同一用户运行的进程都能读
   secrets.db。这与 Hermes / Claude Code (Linux) 一致，
   是 accepted risk。
2. **明文存储** — SQLite 文件未加密，secret 值可能以明文出现
   在 raw page 中。`sqlite3` CLI 可直接 SELECT 读取。
   唯一防线是 0600 权限 + LLM 隔离（§6.4）。
3. **更新后需重启** — `/auth set` 更新 secret 后，已缓存的
   config section 不会自动刷新（§8.3）。与 Hermes 行为一致。
4. **Windows 权限无效** — chmod 0600 在 Windows 上无效果，
   靠用户 home 目录默认 ACL（§8.4）。与 CC / Hermes 一致。
5. **无 credential rotation** — 换凭证需要 `/auth set` 手动
   更新。未来可加 TTL + 自动 refresh（依赖 OAuthFlow）。
