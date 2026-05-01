# ConnectionAuthenticator

## Purpose

Kernel 始终监听 `127.0.0.1:<port>`（默认 7777）。任何能连上
这个端口的客户端都能执行工具、读文件，所以必须验证客户端身份。
ConnectionAuthenticator 是验证**连接接入**身份的**唯一入口**，
传输层在 accept 后、进入协议层前调用它，验证通过产出不可变的
`AuthContext`。

这个子系统的 scope 被刻意缩到只有一件事：**判断"这个 WS 连接是谁"**。
它不碰 provider API key、不碰 MCP OAuth、不碰工具调用权限——这些是
`CredentialStore` / 未来的 MCP OAuth / `ToolAuthorizer` 各自的职责。
命名上的 `Connection` 前缀就是为了把 scope 在名字里钉死，不被后续
扩展稀释成一个模糊的 "auth"。

### 和 ToolAuthorizer 的关系

Kernel 的认证/授权分成两个不同的决策点，由两个不同的子系统承担：

| 子系统 | 决策点 | 输入 | 输出 |
|---|---|---|---|
| **ConnectionAuthenticator** | transport 层 WS accept 之后、协议层之前 | 凭证（token / password）+ `connection_id` | `AuthContext`（身份证明） |
| **ToolAuthorizer** | orchestrator 层每个 tool call 之前 | `(tool_name, tool_input, ctx)` | `PermissionDecision` |

前者回答"**你是谁**"（AuthN），后者回答"**你能做什么**"（AuthZ）。
一次连接进 authenticator 一次，之后每个 tool call 各自进 authorizer
一次。两者都有独立的子系统文档。

## Design Decisions

### 为什么是传输层认证，不走 ACP 的 `authenticate` 方法

Kernel 的认证发生在**传输层**（WebSocket `accept()` 后立即进行），
**不**走 ACP 的 `authenticate` 方法。我们在 `InitializeResponse.authMethods`
返回 `[]`，告知 ACP client "协议层不需要再认证"。这是 ACP 规范
明确允许的配置。

四个决定性理由：

1. **ACP 的 `AuthenticateRequest` schema 无法承载凭证** —— 该 schema
   只有 `methodId: string` 一个字段，没有标准位置放 token / password。
   实际凭证只能塞 `_meta`，既非标准也无互操作性。ACP 的 `authenticate`
   设计面向 OAuth 或 vendor API key 流程，不是本机 token 文件这种
   简单凭证模型。

2. **最小化攻击面** —— 传输层认证让未认证连接**根本进不了协议层**。
   验证失败直接 `close(code=4003)`，kernel 不为这个连接分配
   `ConnectionContext`、不跟踪任何状态、不解析任何 JSON 帧。
   DoS 防御从 "协议层给未认证状态加超时 + rate limit" 退化成
   "accept 时一次判断"，简单得多。

3. **POSIX 文件权限本身就是充分的认证介质** ——
   `~/.mustang/state/auth_token` 的 0600 权限建立了这条等价链：

   > 能读 auth_token → 是本机文件系统的合法用户 → 认证通过

   这条链**完全由 OS 保证**，不需要 kernel 做任何密码学。把认证
   搬到协议层相当于对同一条 OS 级安全保证做重复表达，没有任何
   增益。

4. **本地 kernel 不是远程云服务** —— ACP 的 `authenticate` 模型
   假设 agent 是有账号 / 有配额 / 有 vendor dashboard 的云服务，
   需要 client 明确"登录"一次。我们是本机 kernel，没有账号系统、
   没有配额、没有计费。在这个架构下走协议层认证只是增加往返次数，
   没解决任何问题。

### 网络拓扑：kernel 只绑 loopback

kernel **不提供 `bind` 配置项**，永远只监听 `127.0.0.1`。想要
远程访问只有一条路径：

```
远程客户端 → 反向代理 (Caddy / nginx / traefik) → kernel 127.0.0.1:port
```

这个硬约束的好处：

- 没有"bind 配错导致端口被意外暴露"的可能
- TLS 完全由反代负责，kernel 自己不掺和证书和加密
- 反代层可以独立做限速、IP 白名单、访问日志等

反代裸开放（kernel 没配 password）是用户的主动选择，不是 bug。
只有 token 的 kernel 被反代暴露出去时，远程客户端无法读到本机的
`auth_token` 文件，**自然无法认证通过** —— 这是"没 password 即
不可远程访问"的天然防护，kernel 不需要再做什么。

### 两种凭证

Kernel 同时接受两种凭证：

| 凭证 | 来源 | 用途 |
|------|------|------|
| **Token** | `~/.mustang/state/auth_token`（kernel 管理，0600） | 本机客户端 —— 能读这个文件就证明是本机合法用户 |
| **Password** | `~/.mustang/state/auth_password.hash`（scrypt 哈希） | 远程客户端（经反代）—— 用户手动输入明文，kernel 用哈希比对 |

客户端侧的选择逻辑：

- 能读到本机 `auth_token` → 用 token 连接
- 读不到 → 提示用户输入 password
  - 如果 kernel 没设 password → 连接失败，提示 "this kernel is
    not accepting remote connections"

**Locality 信号靠 `credential_type`，不靠 `remote_addr`** —— 因为
kernel 只 bind loopback，任何连接的远端地址都是 `127.0.0.1`（要么
本机客户端直连，要么本机反代转发），区分不了"本机 vs 远程"。
区分身份只能看用了哪种凭证：token 凭证必定本机（拿得到说明能读
本机文件），password 凭证可能本机也可能远程。

## File Layout

```
~/.mustang/
  flags.yaml                    # FlagManager
  config/
    config.yaml                 # ConfigManager 管的业务配置
    ...
  state/                        # kernel 运行时产物，人不该手编辑
    auth_token                  # ConnectionAuthenticator 生成，0600
    auth_password.hash          # ConnectionAuthenticator 生成，0600
    ...
```

`state/` 目录是"子系统运行时状态"的统一归属点。以后 memory
index、session metadata 这些也放这里，和"用户意图层"的 config /
flags 清晰分开。

## Configuration

ConnectionAuthenticator **不** bind 任何 ConfigManager section —— 目前没有
任何用户可调的 auth 选项，所以 `~/.mustang/config/config.yaml`
里也没有 `auth` 段。

为什么不放 `port`？监听端口属于**进程启动**参数（`python -m kernel
--port N`），不属于"谁能连进来"这一层。auth 子系统和端口的唯一
关系就是"连上来之后验证身份"，端口本身决定不了身份，塞进 `auth`
section 只是把两个不相关的 concern 混在一起。以后如果 `--port`
要支持"CLI 没给就读 config"，那是 `__main__.py` 加一个独立的
`server` / `transport` section 的事，和 ConnectionAuthenticator 无关。

未来若出现真正属于 auth 的配置项（比如 session timeout、per-IP
rate limit），再补一个 `auth:` section 并让 ConnectionAuthenticator `bind`
它。

Password / token 也不进 config —— 敏感数据（无论明文还是哈希）
都不应该放到有可能被 diff / commit 的位置。它们的管理完全通过
CLI 命令：

- `mustang auth set-password` —— 提示用户输入明文，scrypt 哈希后
  写入 `state/auth_password.hash`（文件不存在则创建，0600）
- `mustang auth clear-password` —— 删除 `state/auth_password.hash`
- `mustang auth rotate-token` —— 生成新 token 写入 `state/auth_token`，
  内存缓存同步更新；已存在的 WebSocket 连接**不会**被主动断开，
  但它们手里的旧 token 已失效，重连时必须用新 token

**Token 轮转策略**：ConnectionAuthenticator 启动时若 `auth_token` 文件存在就
直接读进内存，不存在才生成新 token。这样 kernel 正常重启不会让
客户端反复重新读文件；token 文件被删（比如用户清理 `~/.mustang`）
时才会自动生成新的。轮转由上面的 CLI 命令显式触发。

## Credential Transport

不同传输层用不同方式传凭证，但都进入同一个 ConnectionAuthenticator 校验：

- **WebSocket**：`?token=xxx` 或 `?password=xxx` query param
- **HTTP**（未来）：`Authorization: Bearer xxx` header

传输层只负责从原始请求里提取凭证字符串和 `credential_type`，
不需要知道格式或含义，交给 ConnectionAuthenticator 的 `authenticate()` 判断。

## AuthContext

ConnectionAuthenticator 验证通过后产出一个 `AuthContext`，从传输层一路传到
协议层、会话层、事件流，作为"这个连接是谁"的只读描述。

```python
@dataclass(frozen=True)
class AuthContext:
    """Authenticated identity bound to a single transport connection.

    Produced by ConnectionAuthenticator.authenticate() on success.  Flows from
    transport → protocol → session as a read-only descriptor.  The
    raw credential is NEVER stored here — it's consumed during
    verify and discarded before this context is returned.
    """

    connection_id: str
    """uuid4.hex, generated by the transport layer when the
    connection is accepted.  Used to correlate log entries, audit
    records, and broadcast event streams to the originating
    connection."""

    credential_type: Literal["token", "password"]
    """Which credential kind was used.  The only reliable
    locality signal (see is_local)."""

    remote_addr: str
    """Host:port of the socket's remote end.  Under our loopback-only
    architecture this is almost always 127.0.0.1:<ephemeral> (either
    a local client or a local reverse proxy forwarding a remote
    connection).  Retained purely for diagnostic logging — do NOT
    use it for authorization decisions."""

    authenticated_at: datetime
    """UTC timestamp of successful verification."""

    @property
    def is_local(self) -> bool:
        """Token credentials can only be obtained by reading the
        token file on the same machine, so token == definitely local.
        Password credentials may be local or remote — conservatively
        report non-local in that case."""
        return self.credential_type == "token"
```

**设计不变量**：

1. **Immutable** —— 认证通过就冻结。客户端想换凭证必须断开重连。
   这保证下游看到的身份和握手时验证的身份永远一致。
2. **不存原始凭证** —— `credential_type` 是类型标签，不是值。
   Token / password 明文在 `authenticate()` 返回前必须丢弃。
3. **最小字段集** —— 故意不放的字段：
   - `session_id` —— 连接可以跨多个 session（create / load / list
     都在认证之后），不属于 auth 层
   - `client_info`（name / version / capabilities）—— 那是 ACP
     `initialize` 握手阶段的产物，由协议层填到 ConnectionContext
   - `permissions` / `scopes` —— kernel 没有 RBAC
   - `expires_at` —— 凭证长期有效直到显式轮转 / 清除，没有 session
     级过期概念

## ConnectionContext

AuthContext 是认证握手阶段的产物；**ACP initialize 握手的产物**
属于协议层。协议层自己维护一个 `ConnectionContext`，包着 AuthContext
加上协议协商后的信息：

```python
@dataclass
class ConnectionContext:
    """Per-connection runtime state, owned by the protocol layer.

    AuthContext is immutable; ConnectionContext evolves as the
    ACP handshake progresses (initialize → session operations).
    """

    auth: AuthContext

    client_info: ClientInfo | None = None
    """Filled in after ACP ``initialize`` — client name, version,
    declared capabilities."""

    negotiated_capabilities: dict[str, Any] = field(default_factory=dict)

    bound_session_id: str | None = None
    """Set when session/new or session/load succeeds.  One connection
    can re-bind across multiple sessions over its lifetime."""

    @property
    def connection_id(self) -> str:
        return self.auth.connection_id
```

分层的好处：AuthContext 只关心"身份证明"，不会被后加的协议字段
污染；新增的连接级状态（rate limit 计数、hook 订阅等）都加到
ConnectionContext，不动 AuthContext 本身。

下游怎么用：

| 消费者 | 读什么 | 用途 |
|---|---|---|
| **Session 事件流** | `connection_id` | 每个事件带上，UI 可以知道 text delta 是哪个连接的请求触发的 |
| **审计日志** | `credential_type` / `remote_addr` / `authenticated_at` / `connection_id` | `[conn=abc123] password@127.0.0.1:45678 executed Bash tool` |
| **多连接同一 session** | `connection_id` | SessionManager 维护 `set[connection_id]`，broadcast 事件给所有 |

## ConnectionAuthenticator Interface

```python
class AuthError(Exception):
    """Authentication failed — invalid credential, unsupported type,
    or password auth disabled."""


class ConnectionAuthenticator(Subsystem):
    async def startup(self) -> None:
        """
        - 读/创建 ``<module_table.state_dir>/auth_token``（0600 权限）
        - 读 ``<module_table.state_dir>/auth_password.hash`` 到内存（若存在）
        - 从 ConfigManager ``bind_section(file="config", section="auth")``
          拿配置
        """

    async def authenticate(
        self,
        *,
        connection_id: str,
        credential: str,
        credential_type: Literal["token", "password"],
        remote_addr: str,
    ) -> AuthContext:
        """Verify credential and return an AuthContext.

        - ``connection_id`` 由传输层在 accept socket 时生成（见
          AuthContext.connection_id 字段说明），ConnectionAuthenticator 自己
          **不**分配，确保传输 / 协议 / 会话各层日志用同一个 id
          对齐同一个连接。
        - token 用 secrets.compare_digest 比较
        - password 用 hashlib.scrypt 重新哈希后和存储的 key 比较
        - 失败抛 AuthError，异常 message 不包含 credential 内容
        - 返回前清除 credential 的引用，不进任何缓存 / 日志

        Raises:
            AuthError: credential 无效；或 credential_type 是 password
                       但 kernel 未启用 password auth。
        """

    def has_password(self) -> bool:
        """Whether password auth is currently enabled (hash file present)."""

    def rotate_token(self) -> None:
        """Generate a new token, write to state/auth_token (0600)."""

    def set_password(self, plaintext: str) -> None:
        """Hash with scrypt + fresh salt, write to state/auth_password.hash."""

    def clear_password(self) -> None:
        """Delete state/auth_password.hash and clear in-memory hash."""
```

## Security Requirements

- **token 文件 0600** —— POSIX `os.chmod(path, 0o600)`；Windows
  不做特殊处理
- **常量时间比较** —— token 用 `secrets.compare_digest`；password
  用 `hashlib.scrypt` 本身（scrypt 输出定长，重新哈希后的 key 和
  存储的 key 等式比较也是常量时间）
- **scrypt 参数** —— `n=2**15, r=8, p=1, dklen=64`，salt 用
  `os.urandom(16)`。哈希文件格式：`scrypt$n$r$p$salt_b64$key_b64`
  （冒号分隔便于未来参数升级时向前兼容）
- **凭证不进日志 / transcript / error message** —— `authenticate()`
  的异常文案永远是通用 "authentication failed"；具体原因只记到
  debug 级 log 且不含原文
- **TLS 由反代负责** —— kernel 永远走明文
- **仅支持 POSIX** —— Windows 下权限 bit 不做特殊处理，用户自行
  保证 `~/.mustang/` 目录不被其他用户访问

## Service Discovery (Not Kernel's Job)

- **本地场景**：客户端直接连 `127.0.0.1:<port>`，不需要发现
- **局域网场景**：由反代通过 mDNS 广播（`_mustang._tcp.local.`），
  kernel 自身不参与发现
- Kernel 通过根端点 `GET /` 暴露身份信息（instance_id、name、
  支持的 auth 方式）供客户端探测

## Related

- [architecture.md](../architecture.md) —— WebSocket 三层分工、传输层
  如何调用 `authenticate()`
- [config.md](config.md) —— `auth` section 的注册机制
- [flags.md](flags.md) —— Flag / Config 职责边界对照
