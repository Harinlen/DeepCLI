# FlagManager

## Purpose

FlagManager 是 kernel 的 **bootstrap 服务**，最优先加载。它管理
**运行期不可变的功能开关** —— 一旦 kernel 启动就按这份配置运行，
变更只能靠改 `~/.mustang/flags.yaml` 然后重启 kernel。

类比：**车上的 FuseBox**。保险丝是上车前装好的，行驶过程中
不会动；要改装某条电路必须停车操作。

职责边界：

- ✅ 加载 `~/.mustang/flags.yaml`
- ✅ 让各子系统注册自己的 flag section（Pydantic schema）
- ✅ 提供强类型的 flag 实例给子系统缓存使用
- ❌ **不管运行期变更** —— 没有 `set_many`，没有 hot reload，
  变了只能重启
- ❌ **不管业务配置** —— 那是 [ConfigManager](config.md) 的事

## Design Decisions

### Flag vs Config 的分界

| 维度 | Flag（本文档） | [Config](config.md) |
|---|---|---|
| 变更时机 | 启动期；运行期只读 | 运行期可变 |
| 典型内容 | "哪些子系统启用"、启动时才生效的开关 | provider 列表、API key、hooks 定义等业务配置 |
| 分层 | 单一文件（flags.yaml） | 六层合并（defaults → user → project → local → env → cli） |
| 热重载 | 不支持，重启 kernel | 通过 owner 的 `update()` 写回磁盘 + signal 通知 |
| 类比 | FuseBox | 仪表盘 / 空调 |

"启动期决定、运行期不变"是 Flag 的核心不变量。这个约束让接口
可以大幅简化：register 返回的就是**冻结的 Pydantic 实例**，
子系统可以自由缓存，不需要"每次访问都实时查"的 callable 封装。

### `kernel` section —— 子系统启停开关

`kernel` section 是 FlagManager 自己内置的 schema，不是由某个
子系统注册的。它专门管"哪些**可选子系统**在这次启动时启用"。

```python
# kernel/flags/kernel_flags.py
class KernelFlags(BaseModel):
    """Which optional subsystems are enabled.

    Managed by FlagManager itself, not by any subsystem.  Core
    subsystems (auth / provider / session) are deliberately absent —
    they cannot be disabled.
    """
    memory: bool = True
    mcp: bool = True
    skills: bool = True
    hooks: bool = True
    tools: bool = True
```

不可禁用的核心子系统（Auth / Provider / Session）**根本不出现
在 `KernelFlags` 里**，用户没法禁用它们。这个约束在类型层面就
表达清楚了。

## File Location

```
~/.mustang/
  flags.yaml           # FlagManager 管理（单文件）
  config/              # ConfigManager 管理的目录
  state/               # kernel 运行时产物
```

如果 `flags.yaml` 不存在，所有 flag 用 schema 定义的默认值，
不自动生成文件。

### Flag 结构

每个子系统注册一个 section，section 名字就是子系统名。
Section 内部是 Pydantic model，字段名用 snake_case：

```yaml
# ~/.mustang/flags.yaml
kernel:                    # kernel 内置的 section
  memory: true
  mcp: true
  skills: true
  hooks: true
  tools: true

tools:                     # tools 子系统注册的 section
  bash: true
  browser: false

memory:                    # memory 子系统注册的 section
  auto_extract: true
```

> **注意**：类似 `bash_timeout: 120` 这种"运行期可调的参数"
> **不应该放在 flags.yaml 里**，它属于 Config 的范畴。Flag 里
> 只放"通电/不通电"式的启动期开关。

## Interface

FlagManager 是 bootstrap 服务，**不继承 `Subsystem`**（详见
[architecture.md#生命周期](../architecture.md#生命周期)）。
它有自己的 `initialize` 入口，由 lifespan 直接构造和调用，
失败即 abort kernel。

```python
class FlagManager:
    async def initialize(self) -> None:
        """读取 flags.yaml，注册内置的 KernelFlags。"""

    def register(
        self, section: str, schema: type[T]
    ) -> T:
        """子系统启动时调用。返回 Pydantic 实例（运行期冻结）。

        子系统可以自由缓存返回值 —— FlagManager 不会在运行期改动它。

        Raises:
            ValueError: section 名字已被占用
            ValidationError: 用户配置不符合 schema（硬失败）
        """

    def get_section(self, section: str) -> BaseModel:
        """拿到某个 section 当前的 Pydantic 实例（运行期只读）。"""

    def list_all(self) -> dict[str, tuple[type[BaseModel], BaseModel]]:
        """列出所有已注册的 section 及其 schema + 当前值。

        供客户端渲染设置界面用 —— 设置界面只能提示用户"去编辑
        flags.yaml 然后重启"，不能直接改。
        """
```

### 子系统的用法

```python
class ToolsFlags(BaseModel):
    bash: bool = Field(True, description="Enable bash tool")
    browser: bool = Field(False, description="Enable browser tool")


class ToolRegistry(Subsystem):
    async def startup(self) -> None:
        self.flags = self._module_table.flags.register("tools", ToolsFlags)
        # self.flags 是一个 ToolsFlags 实例，可以直接用

    async def execute_bash(self) -> None:
        if not self.flags.bash:
            raise ToolDisabledError
        ...
```

**关键约定**：子系统拿到的是实例而不是 callable。因为 flags
运行期不变，缓存就是正确的。

## Registration Timing

1. `FlagManager.initialize()` 读取 `flags.yaml` 到 raw dict，
   注册内置 `KernelFlags`
2. 子系统依次初始化，每个子系统在 `startup()` 里调用
   `flags.register(name, Schema)`
   - FlagManager 查找 raw dict 里对应 section
   - 用 Schema 做 `model_validate` + apply defaults
   - 未知字段（Pydantic extra）直接忽略
   - 验证失败 → 抛异常，kernel 启动失败
   - Section 名字冲突 → 抛异常，kernel 启动失败
3. 子系统拿到实例后就可以使用，运行期不会变

## Why no runtime mutation

早期设计考虑过 `set_many` + callable accessor 的热更新模式，
但后来统一到"flag = 启动期决定、运行期冻结"之后砍掉了。理由：

- **简化接口** —— 去掉 callable 一层包装，`register` 直接返回
  实例，使用侧不用写 `self.flags().bash` 这种额外括号
- **明确职责边界** —— Flag 管"上电开关"，Config 管"运行期调节"，
  不让一个机制同时背两种用途
- **避免"哪些是热的、哪些是冷的"的心智负担** —— 所有 flag 一律冷，
  改了就重启，不用记例外

运行期可变的东西（provider 设置、hooks 列表、memory 参数等）
全部走 [Config](config.md)。

## Related

- [architecture.md](../architecture.md) —— bootstrap 服务如何接入 lifespan
- [config.md](config.md) —— 运行期可变配置的接入机制
