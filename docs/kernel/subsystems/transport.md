# Transport

## Purpose

WebSocket `/session` 是用户进入 kernel 的唯一 IO 入口。按
[architecture.md](../architecture.md) 的分层设计，transport 是
这条路径上最靠外的一层：它拥有 socket 本身，但**不**解析 JSON、
**不**派发业务逻辑、**不**认识 session 和 orchestrator。它的职责
小而明确：

1. 接受 WebSocket 连接
2. 调 `ConnectionAuthenticator.authenticate()` 验证身份
3. 循环 `recv → 解码 → 派发 → 编码 → send`，直到客户端断开
4. 断开时做最小清理并关 socket

其中"解码 / 派发 / 编码"都不是 transport 自己写的 —— transport
只是循环的驱动者，真正的 codec 和 dispatcher 由一个
**ProtocolStack** 提供，transport 通过 Flag 查到当前激活的 stack。

## Design Decisions

### 为什么 transport 只做"socket 驱动者"

把 socket 操作和协议业务耦在一起，会让以后增加新的协议栈（非
ACP 的 transport 绑定、debug 模式、CLI 专用 fast path）都要重写
一次 recv/send loop。三层分离的好处是这个 loop 只写一次：

```python
while True:
    raw = await ws.receive_text()
    try:
        msg = stack.codec.decode(raw)
    except ProtocolError as exc:
        await ws.send_text(stack.codec.encode_error(exc))
        continue
    async for response in stack.dispatcher.dispatch(msg, auth):
        await ws.send_text(stack.codec.encode(response))
```

transport 不关心 `msg` 的真实类型是什么，不关心 dispatcher 怎么
产出响应，也不关心 codec 用 JSON 还是 MessagePack。它只负责
"从 socket 搬字节到 stack，从 stack 搬字节回 socket"。

### 为什么 auth 在 transport，不在 session handler

[connection_authenticator.md](connection_authenticator.md) 的设计决策：
未认证的连接根本不能进入协议层和会话层。transport 的第一件事
（`accept()` 完之后）就是 `ConnectionAuthenticator.authenticate()`，
失败直接 `close(4003)`。下游层连
`AuthContext` 都拿不到的话，就连被 DDoS 的机会都没有 —— 协议
层不会被调用、session 不会被创建、no state to leak。

### 为什么是一个 ProtocolStack 而不是两个独立 flag

codec 和 dispatcher 必须是**匹配的一对**：ACP codec 产出的
`AcpMessage` 只有 ACP dispatcher 能处理，DummyCodec 产出的裸
字符串只有 DummyDispatcher 能处理。把它们拆成两个 flag 允许
用户配置 `codec=acp, dispatcher=dummy`，结果是运行时 type error。
把它们绑成一个 "stack"，flag 选的是名字（`"dummy"` / `"acp"`），
factory 保证出来的一对必然匹配。

### 为什么是 Flag，不是 Config

Flag 的语义是"启动时冻结的模式开关"，Config 的语义是
"运行时可调的业务参数"。选 stack 完全匹配前者：

- 运行中换 stack 对活连接毫无意义 —— 一个连接的 codec / dispatcher
  必须从头到尾一致
- 修改方式永远是"编辑 `flags.yaml` → 重启 kernel"，正是 flag
  系统的工作模式
- `Literal["dummy"]` 这种类型约束直接由 pydantic 在 `register`
  时校验，配错名字启动就挂，不需要额外验证代码

### 为什么不让 transport 变成一个 Subsystem

Transport 不需要 `startup` / `shutdown` 钩子 —— FastAPI 的 route
函数本身就是它的入口，生命周期等同 HTTP server 生命周期。把它
包成 Subsystem 只会多一层 `app.state.transport` 指向一个空壳
对象。`routes/flags.py` 的 `TransportFlags` 注册由 lifespan 直接
做（就一行），这和 FlagManager 内部注册 `KernelFlags` 是同样的
"不属于任何 subsystem 的配置"模式。

## Layer Interfaces

### ProtocolCodec

```python
class ProtocolCodec(Protocol[M]):
    """JSON ↔ typed message codec — no knowledge of session state."""

    def decode(self, raw: str) -> M: ...
    def encode(self, msg: M) -> str: ...
    def encode_error(self, error: ProtocolError) -> str: ...
```

- `decode` 抛 `ProtocolError` → transport 通过 `encode_error` 回一
  条错误帧，**不**断开连接（客户端可能只是发错了一条消息，
  后续消息仍然合法）
- `encode` 不允许失败 —— 消息是由 dispatcher 产出的类型化结构，
  codec 必须能序列化；如果 encode 失败那是 codec 实现 bug
- codec 是纯函数，没有内部状态，stack 构造后永远共享

### SessionDispatcher

```python
class SessionDispatcher(Protocol[M]):
    """Inbound typed message → async stream of outbound typed messages."""

    def dispatch(
        self, msg: M, auth: AuthContext
    ) -> AsyncIterator[M]: ...

    async def on_disconnect(self, auth: AuthContext) -> None: ...
```

- `dispatch` 返回 `AsyncIterator[M]` —— 一条入站消息可以产生任意
  条出站消息（ACP 的 `session/prompt` 产生一连串 `session/update`
  就是这种模式）
- `auth` 按值传入每次 dispatch；dispatcher 自己不缓存 auth，
  因为 transport 层保证同一个 socket 的 auth 从头到尾一致
- dispatcher 可以有内部状态（per-connection 或 per-process），
  但**不**持有 socket —— 产出通过 iterator，不直接写 socket
- `on_disconnect` 在 WebSocket 关闭后被 transport 调用（不管是
  正常断开、对端主动断、还是 transport crash），dispatcher 用它
  来取消正在进行的任务、从 session 解绑当前连接。实现为 no-op
  的 dispatcher（如 DummyDispatcher）直接 `pass` 即可

### ProtocolStack

```python
@dataclass(frozen=True)
class ProtocolStack(Generic[M]):
    codec: ProtocolCodec[M]
    dispatcher: SessionDispatcher[M]
```

一个 stack 是 "(codec, dispatcher)" 的冻结组合。transport 拿到
它只用两个字段，`M` 在 transport 侧是 `Any`，因为 transport
不需要 —— 类型约束在 codec 和 dispatcher 的实现内部。

### ProtocolError

```python
class ProtocolError(Exception):
    """Raised by ``ProtocolCodec.decode`` on malformed input."""
```

只是一个标记基类，具体 codec 可以继承它加更多字段。transport
捕获它、调 `encode_error`、继续循环。

## Dummy Stack

在真正的协议层 / 会话层完成之前，需要一个能让整条链路跑起来
的占位实现。原则：**Echo 不是"特殊路径"，它是 stack 名字叫
"dummy" 的一种合法 stack 实现**。transport 代码对 echo 一无所
知 —— 它只是按常规循环跑 DummyCodec + DummyDispatcher，结果
碰巧是回显。

```python
class DummyCodec:
    """Identity codec — passes raw strings through as-is."""

    def decode(self, raw: str) -> str:
        return raw

    def encode(self, msg: str) -> str:
        return msg

    def encode_error(self, error: ProtocolError) -> str:
        return f'{{"error": {json.dumps(str(error))}}}'


class DummyDispatcher:
    """Identity dispatcher — yields the input back once."""

    async def dispatch(
        self, msg: str, auth: AuthContext
    ) -> AsyncIterator[str]:
        yield msg
```

两个加起来就是 echo。transport loop 对它们的处理和对真正 ACP
stack 的处理是**同一段代码**。

Dummy stack 的存在意义：

1. 让 transport 层现在就可以跑完整流程，不用等协议 / 会话层
2. 让测试可以验证 "transport 层的认证、错误处理、断开清理"
   等 socket-level 行为，不依赖 ACP
3. 给开发提供一个 smoke test 入口（Postman / websocat 连上就
   能回显）

真正的 `acp` stack 出现以后，dummy stack 不会立刻删 —— 它作为
纯 socket 测试工具还有价值。什么时候删由 flags 默认值决定：
默认从 `dummy` 切到 `acp` 之后，dummy 就是 opt-in 的调试工具。

## Connection Lifecycle

```
client                      transport                       stack
──────                      ─────────                       ─────
CONNECT  ───────────────→   ws.accept()
                            connection_id = uuid4()

                            module_table.get(ConnectionAuthenticator)
                               ✗ → close(4003)              (end)

                            credential ← query_params
                               missing → close(4003)        (end)

                            auth.authenticate(credential)
                               ✗ → close(4003)              (end)
                               ✓ → auth_ctx

                            stack = create_stack(flags.stack)

         ──── frame ────→   raw = ws.receive_text()
                            msg = codec.decode(raw)  ───→   decode
                              ProtocolError
                            ←── encode_error(exc)           (continue)
                                                     ←───   msg
                            async for out in dispatcher.dispatch(msg, auth_ctx):
                                                     ───→   dispatch
                                                     ←───   out
                            ws.send_text(codec.encode(out))

         ←──── frame ────

         ──── CLOSE ────→    WebSocketDisconnect
                            finally: dispatcher.on_disconnect(auth_ctx)
```

## Credential Extraction

从 `ws.query_params` 里提取凭证：

- `?token=xxx` → `credential_type = "token"`
- `?password=xxx` → `credential_type = "password"`
- 两个都给 → **优先 token**。拿到本机 token 文件意味着 locality
  更强的身份证明，password 是 fallback 路径；都提供时按强身份
  走
- 两个都没给 → `close(4003, "authentication failed")`，和凭证
  错误不区分（不暴露"是缺了 token 还是 token 错了"）

Header / Authorization 不作为 WebSocket 凭证入口 —— 不同 WS
客户端（浏览器、Python websockets、curl）对自定义 header 的
支持参差，query param 是最通用的传输方式。未来 HTTP 入口上线
时再用 `Authorization: Bearer` 走 HTTP 语义。

## Close Codes

| Code | 含义 | 何时使用 |
|------|------|----------|
| `1000` | Normal closure | handler 正常返回 / 客户端主动断开 |
| `1001` | Going away | kernel shutdown 时服务端断开 |
| `1011` | Internal error | transport 层未捕获异常 |
| `4003` | Authentication failed | 凭证缺失 / 错误 / 类型不支持 |

`4003` 在 RFC 6455 的 4000-4999 private-use 区间。客户端侧收到
这个 code 时应明确提示"authentication failed"，并根据当前凭证
类型决定下一步（token 失效 → 重读文件 / password 错 → 让用户
重新输入）。其他私有 code（4xxx）kernel **不**使用，以免和
未来扩展语义撞车。

## Heartbeat

由 uvicorn 原生的 WebSocket ping/pong 负责，transport 代码里
不写心跳逻辑：

```python
uvicorn.run(
    ...,
    ws_ping_interval=20,
    ws_ping_timeout=20,
)
```

- 20 秒没收到 pong → uvicorn 主动断开 →
  `ws.receive_text()` 抛 `WebSocketDisconnect` → 走正常断开清理
- 客户端只需保证响应 ping 即可（所有主流 WS 库默认行为）

应用层自定义心跳（"ping" JSON 消息）属于 overreach —— RFC 层面
的 ping/pong 已经足够，不需要重新发明。

## Cleanup on Disconnect

transport 的 cleanup 发生在 `finally` 块里，无论连接如何结束
（正常返回、`WebSocketDisconnect`、还是 transport 内部 crash）
都会执行：

```python
finally:
    await stack.dispatcher.on_disconnect(auth_ctx)
```

`on_disconnect` 是 `SessionDispatcher` Protocol 的必要方法。transport
不知道 dispatcher 内部有什么状态，统一交给 dispatcher 自己清理：

- **DummyDispatcher** — no-op，echo stack 没有需要清理的状态
- **ACP dispatcher（未来）** — 取消当前连接的 in-flight prompt task、
  从 session 解绑此连接、触发相应的 hook

`on_disconnect` 保证在所有断开路径（包括 transport crash）都
被调用，这意味着 dispatcher 不需要在 `dispatch` 的每个分支里自己
做清理——只需在 `on_disconnect` 里统一处理。

## Multiple Connections to the Same Session

Architecture.md 说"多连接同一 session"是允许的。transport 层本身
不管这个 —— 它只负责"一个 socket 对应一个 AuthContext"。
"这个连接绑到哪个 session" 是 session layer 的事（通过
`session/new` / `session/load` 消息），transport 完全无感知。

多连接 broadcast 通过 session layer 自己维护的
`set[connection_id]` 实现：一个 session 状态变化时，session layer
往所有绑定到它的连接的 outbound queue 里塞消息。transport 层
的 recv/send loop 对此完全透明。

## Configuration

Transport 通过 FlagManager 绑定自己的 flag section：

```yaml
# ~/.mustang/flags.yaml
transport:
  stack: dummy   # 未来会改成默认 "acp"
```

```python
# kernel/routes/flags.py
class TransportFlags(BaseModel):
    stack: Literal["dummy"] = Field(
        "dummy",
        description="Registered ProtocolStack name.",
    )
```

注册由 lifespan 直接做（transport 不是 Subsystem）：

```python
# kernel/app.py
flags.register("transport", TransportFlags)
```

用户在 `flags.yaml` 写了未知的 stack 名字 → pydantic 在 `register`
时 `ValidationError` → lifespan 把它当作 bootstrap 失败，kernel
直接挂掉。不需要 transport 代码里写运行时兜底分支。

## Related

- [connection_authenticator.md](connection_authenticator.md) —— ConnectionAuthenticator 接口、凭证类型
- [architecture.md](../architecture.md) —— 整体分层、子系统清单
- [flags.md](flags.md) —— FlagManager 的 register / frozen 语义
