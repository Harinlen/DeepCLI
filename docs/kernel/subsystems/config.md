# ConfigManager

## Purpose

ConfigManager 是 kernel 的 **bootstrap 服务**，第二个加载
（仅在 [FlagManager](flags.md) 之后）。它管理**运行期可变的
结构化配置** —— provider 列表、tool 参数、hooks 定义、auth port
等所有"启动后还可能改"的东西。

Flag 管"通电/不通电"的启动期开关，Config 管"运行期可调的结构化
配置"，两者职责严格分离。类比：Flag 是 FuseBox，Config 是仪表盘。

## Design Decisions

1. **分散 schema** —— 每个子系统定义自己的 Pydantic schema，
   负责默认值和验证。没有一个巨大的全局 `RuntimeConfig`。
   删掉一个子系统只需要删它的目录，不动全局 schema。

2. **First bind wins（owner 硬约束）** —— 每个 `(file, section)`
   只能被 `bind_section` 成功一次，调用者成为唯一 owner，
   持有写权限。后续对同一 key 的 `bind_section` 抛 `ValueError`。

3. **读者任意多，读者拿不到 update** —— 任何子系统通过
   `get_section` 拿到 `ReadOnlySection` wrapper，字面上**没有
   `update` 方法**，IDE / 类型检查直接阻止误用。

4. **单一真值源** —— 每个 section 背后是一个内部 `_Section` 对象，
   持有 current value、lock、signal。Owner 和 readers 拿到的是
   两种 wrapper 类型，但代理的是同一个底层状态，不存在"各人各自
   快照互相漂移"的可能。

5. **Signal/Slot 通知** —— owner 调 `update` 后 Section 自动 emit
   一个 `changed` signal；想监听的子系统通过 reader 的
   `changed.connect(slot)` 自行注册，owner 不维护订阅者列表。

## File Layout

```
~/.mustang/config/          # 全局用户层
  config.yaml               # 默认共享文件
  mcp.yaml                  # 某子系统选择独占一个文件
  ...                       # 任意多个 yaml 文件
```

子系统 `bind_section(file=..., section=...)` 时自己决定该 section
存哪个文件里：想独占就换个文件名，想共用就用 `config.yaml`。

同一 file 名的三层路径：

- **全局用户层**：`~/.mustang/config/<file>.yaml`
- **项目层**：`<cwd>/.mustang/config/<file>.yaml`
- **项目本地层**：`<cwd>/.mustang/config/<file>.local.yaml`（gitignored）

### 配置来源分层

低 → 高优先级：

```
1. 子系统 schema defaults        (Pydantic model 默认值)
2. 全局用户层    ~/.mustang/config/<file>.yaml
3. 项目层        <cwd>/.mustang/config/<file>.yaml
4. 项目本地层    <cwd>/.mustang/config/<file>.local.yaml
5. 环境变量      MUSTANG_<FILE>__<SECTION>__<KEY>=...
6. 命令行参数    --config <file>.<section>.<key>=...
```

启动时按文件名分组，每组内按优先级从低到高 `deep_merge`，得到
每个文件最终的 raw dict。子系统 `bind_section` 时 ConfigManager
从对应文件的 raw dict 里取出 section 部分，`schema.model_validate`
后产生初始值。

### 深合并规则

```python
def deep_merge(low: Any, high: Any) -> Any:
    # 只在两边都是 dict 时递归；其他类型一律高优先级赢
    if not isinstance(low, dict) or not isinstance(high, dict):
        return high
    result = dict(low)
    for k, v in high.items():
        result[k] = deep_merge(result[k], v) if k in result else v
    return result
```

- **dict 递归合并** —— 嵌套 dict 按 key 逐个处理，底层未被高层
  覆盖的 key 自然保留
- **list / str / int / bool / null 整体替换** —— 叶子类型，
  高优先级一出现就整块覆盖
- **不合并 list** —— list 合并语义有 concat / 按 key 去重 /
  按 index 覆盖三种，没有通用正确答案。用户要"追加"就在高层
  重写完整 list；要避免写完整就把数据结构换成 dict

## Signal Primitive

`kernel/signal.py` 提供一个轻量 Signal 实现，不引 PyQt / blinker，
自己写一份（约 20 行）：

```python
class Signal(Generic[*Args]):
    def __init__(self) -> None:
        self._slots: list[Callable[[*Args], Awaitable[None]]] = []

    def connect(
        self, slot: Callable[[*Args], Awaitable[None]]
    ) -> Callable[[], None]:
        """注册一个 async slot。返回显式 disconnect callable。"""
        self._slots.append(slot)
        def disconnect() -> None:
            try:
                self._slots.remove(slot)
            except ValueError:
                pass   # 允许重复调用
        return disconnect

    async def emit(self, *args: *Args) -> None:
        """顺序 await 每个 slot，异常隔离。"""
        for slot in list(self._slots):   # copy 防止 slot 里改列表
            try:
                await slot(*args)
            except Exception:
                logger.exception("signal slot failed")
```

约定：

- **async-only slots**，不支持 sync 回调，避免混用心智负担
- **串行 await**，保证 slot 执行顺序可预测；并发需求由 slot 内部
  `asyncio.create_task` 自理
- **单 slot 异常 log + 继续**，不中断其他 slot，也不影响发起
  emit 的源头
- **不持有弱引用、不做自动清理** —— slot 的生命周期由订阅方
  负责，详见 [订阅生命周期](#订阅生命周期) 小节
- **抛异常的 slot 仍然保留在列表里**，下次 emit 继续调用；只有
  显式 `disconnect()` 能从列表里移除 slot。自动移除会隐藏 bug
  （静默失去订阅）且把瞬时错误误判为永久失效

Signal 目前只被 ConfigManager 用，未来如果 Memory index、Session
列表等场景也需要广播变更，复用同一个原语。

## Interface

ConfigManager 是 bootstrap 服务，**不继承 `Subsystem`**（详见
[architecture.md#生命周期](../architecture.md#生命周期)）。
它只有一个 `startup` 钩子由 lifespan 直接调用 —— **没有
`shutdown`**：`update()` 写盘是同步的，运行期没有需要 drain 的
状态。`file` 参数是不带后缀的文件名 stem，写盘时 ConfigManager
拼成 `<file>.yaml`。

```python
class ConfigManager:
    async def startup(self) -> None:
        """扫描三层目录下所有 *.yaml，按文件名分组合并得 raw dict。"""

    def bind_section(
        self, *, file: str, section: str, schema: type[T]
    ) -> MutableSection[T]:
        """Owner 注册。一个 (file, section) 只能成功一次。

        Raises:
            ValueError: 该 (file, section) 已被 bind
            ValidationError: raw dict 不符合 schema
        """

    def get_section(
        self, *, file: str, section: str, schema: type[T]
    ) -> ReadOnlySection[T]:
        """Reader 注册。可任意多次调用；schema 必须和 owner 一致。
        允许在 owner `bind_section` 之前调用，section 在首次触达
        时就地物化，后续 owner bind 会复用同一份 `_Section` 状态。
        """
```

### Section wrapper

内部 `_Section` 持有真实状态，owner 和 readers 拿到两种薄代理：

```python
class _Section(Generic[T]):
    """ConfigManager 私有，不对外暴露。"""
    file: str
    section: str
    schema: type[T]
    _current: T
    _lock: asyncio.Lock
    changed: Signal[T, T]       # emits (old, new)


class MutableSection(Generic[T]):
    def get(self) -> T: ...
    async def update(self, new_value: T) -> None: ...
    @property
    def changed(self) -> Signal[T, T]: ...


class ReadOnlySection(Generic[T]):
    def get(self) -> T: ...
    @property
    def changed(self) -> Signal[T, T]: ...
    # 字面上没有 update 方法 —— IDE / 类型检查直接阻止误用
```

两种 wrapper 的 `get` / `changed` 都是向 `_Section` 的属性代理；
`update` 只存在于 `MutableSection` 上。owner 的写入会立即被所有
reader 看到（`get()` 返回新值、`changed` 收到 signal）。

## update() 时序

```python
async def update(self, new_value: T) -> None:
    # (1) schema 二次验证，早失败
    validated = self.schema.model_validate(new_value.model_dump())

    async with self._lock:
        old = self._current

        # (2) 写盘在前：失败则内存不动，订阅者不会收到通知
        await self._write_atomic(validated)

        # (3) 写盘成功才改内存，保证内存和磁盘一致
        self._current = validated

    # (4) emit 在锁外：slot 里反向触发其他 section 的 update
    #     不会死锁
    await self.changed.emit(old, validated)
```

失败语义：

- **验证 / 写盘失败** → update 抛异常，内存和磁盘都没动，
  订阅者收不到通知
- **单个 slot 异常** → Signal 内部 log + 继续；update 本身
  返回成功（写盘和内存更新都已经是既成事实，没法回滚）
- **slot 之间无原子性** —— 一个 slot 响应成功、另一个抛异常时
  系统处于"部分响应"状态，这是 signal/slot 模型的固有代价，
  由 slot 实现者自己保证幂等 / 幂补

## 订阅生命周期

Signal 不做任何自动清理。**订阅方自己负责在销毁前 disconnect**，
就像持有 socket 的人自己负责 close —— 这是标准资源管理模式。

目前最主要的订阅方是 Orchestrator，它与 Session 生命周期绑定。
SessionManager 销毁 session 前**必须**调 `orchestrator.close()`，
由 `close()` 统一做 task 取消和 signal disconnect：

```python
class Orchestrator:
    def __init__(self, config: ConfigManager):
        tools = config.get_section(
            file="config", section="tools", schema=ToolsConfig
        )
        self._tools = tools.get()

        # acquire 时就记住 release 怎么做
        self._disconnects: list[Callable[[], None]] = [
            tools.changed.connect(self._on_tools_changed),
            # 以后加 provider / hooks / ... 订阅都 append 进来
        ]
        self._tasks: set[asyncio.Task] = set()

    async def _on_tools_changed(
        self, old: ToolsConfig, new: ToolsConfig
    ) -> None:
        ...

    async def close(self) -> None:
        # (1) 取消所有后台 task
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # (2) 断开所有 signal 订阅
        for disconnect in self._disconnects:
            disconnect()
        self._disconnects.clear()


class SessionManager:
    async def destroy_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        await session.orchestrator.close()
```

"`close()` 被调了 → slot 一定死了"这条不变量是整个清理机制的
基石。它不依赖 Python 的 ref-count / GC 语义，也不会出现"僵尸
Orchestrator 还在响应配置变更"的诡异 bug。Orchestrator 本体
什么时候真正被内存回收是次要的资源问题，和 signal 正确性完全
解耦。

> 曾经考虑过用 `weakref.WeakMethod` 做自动清理，但那要求
> Orchestrator 恰好能在 session 结束那一刻被 GC 回收 —— 这对
> 未取消的 task、循环引用、全局注册表、traceback 帧等情况都
> 很脆弱。显式 disconnect + 标准资源管理更可靠，Signal 实现
> 也简单得多。

## 持久化规则

- `MutableSection.update` 只写**全局用户层**
  （`~/.mustang/config/<file>.yaml`）
- **不写**项目层 / 项目本地层 —— 那是用户的 git 管理区
- 写回时保留未修改字段；等于 schema 默认值的字段从文件中删除
  （省去的字段自然用默认值，文件保持干净）
- 使用 `tmp 文件 + os.replace` 做原子写，不依赖 fcntl / flock

## 手改文件需要重启

FlagManager 规定手改 `flags.yaml` 必须重启 kernel，**Config 同样
如此**。ConfigManager 启动时读一次，运行期不监听文件变化。

理由：

- 运行期合法变更只有 `update()` 一条路径，内存 / 磁盘自动一致
- 监听文件变化会带回一堆问题（外部变更怎么 emit signal？和程序
  自己的 update 如何去重？schema 验证失败时怎么回滚外部写入？）
- 手改属于"我知道自己在干什么"的场景，重启 kernel 代价不高

## Related

- [architecture.md](../architecture.md) —— bootstrap 服务如何接入 lifespan
- [flags.md](flags.md) —— 启动期开关（不可变）的对照设计
- [connection_authenticator.md](connection_authenticator.md) —— （当前无 `auth` section；未来若需要由它 bind）
