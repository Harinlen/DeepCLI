# CommandManager — Design

Status: **landed** — 全部实装。8 个内置命令（`/help`、`/model`、`/plan`、`/compact`、`/session`、`/cost`、`/memory`、`/auth`）。

---

## 核心概念

CommandManager 是**命令目录提供者**，不是执行者。

- 维护一份 `CommandDef` 注册表（名称、描述、用法、映射关系）
- WS 客户端在 initialize 握手后拉取目录，自己解析命令并调用对应 ACP 方法
- kernel-side 客户端（DiscordBackend）查目录，直接调对应的 SessionManager / LLMManager 方法
- **没有 `session/command` ACP 方法**，不新建执行通道，执行永远走现有机制

这与 Claude Code 一致：命令是客户端解析的 convenience wrapper，执行走现有协议原语。

---

## 命令映射表

每个命令映射到一个已有的 ACP 方法或 kernel 内部方法：

| 命令 | ACP 方法（WS 客户端） | Kernel 内部（Discord 等） | 缺口 |
|------|----------------------|--------------------------|----|
| `/model list` | `model/provider_list` | `LLMManager.list_providers()` | 无 |
| `/model add` | `model/add` | `LLMManager.add_model()` | 无 |
| `/model current` | `model/provider_list` | `LLMManager.list_providers()` | 无 |
| `/model use [role] <provider>/<model>` | `model/set_current` | `LLMManager.set_current_model()` | 无 |
| `/plan [enter\|exit\|status]` | `session/set_mode` | `session_manager.set_mode()` | 无 |
| `/compact` | `session/compact` ← 待新增 | `orchestrator.manual_compact()` | ACP 方法缺失 ⚠️ |
| `/session list` | `session/list` | `session_manager.list()` | 无 |
| `/session delete <id>` | `session/delete` ← 待新增 | `session_manager.delete()` | ACP 方法缺失 ⚠️ |
| `/session resume <id>` | `session/load` | `session_manager.load_session()` | 无 |
| `/cost` | `_mustang.agent/session/get_usage` | `SessionManager.get_usage()` | 美元价格估算待可信 pricing table |
| `/help` | 本地渲染（从 catalog 生成） | 本地渲染 | 无 |
| `/memory` | 本地渲染 + file I/O | 同左 | 无 |
| `/auth` | `secrets/auth` | `SecretManager` API | 无 |

---

## 目录结构

```
src/kernel/kernel/commands/
├── types.py      ← CommandDef
├── registry.py   ← CommandRegistry（register + lookup + list）
└── manager.py    ← CommandManager（Subsystem，注册内置命令）
```

没有 `builtin/` 执行逻辑，没有 `CommandResult`，没有 `dispatch()`。

---

## 类型

```python
@dataclass
class CommandDef:
    name: str
    description: str          # /help 显示
    usage: str                # "/model [list | add | current | use]"
    acp_method: str | None    # WS 客户端用 ("session/set_config_option")
                              # None = 本地命令（/help）
    subcommands: list[str] = field(default_factory=list)
```

---

## CommandManager

Subsystem #10，Session 之后启动。

```python
class CommandManager(Subsystem):
    async def startup(self) -> None:
        self._registry = CommandRegistry()
        for cmd in _BUILTIN_COMMANDS:
            self._registry.register(cmd)

    def list_commands(self) -> list[CommandDef]: ...
    def lookup(self, name: str) -> CommandDef | None: ...
```

无 `dispatch()`，无执行逻辑，无 shutdown 清理。

---

## ACP 集成

客户端获取命令目录的时机：在 `initialize` 握手的 response 里，或通过一个轻量的 `commands/list` 请求。

```
Client → { method: "commands/list" }
Kernel → { result: [ { name, description, usage, acp_method }, ... ] }
```

这是 DeepCLI 扩展方法，加入 `protocol.md` 采纳表。

---

## WS 客户端的职责

1. 获取目录 → 维护本地命令注册表（用于 autocomplete、`/help`）
2. 用户输入 `/model use default provider/model`：
   - 查本地目录：`acp_method = "model/set_current"`
   - 直接发 `{ method: "model/set_current", params: { role: "default", provider: "provider", model: "model" } }`
   - 从 ACP response / broadcast 中渲染结果
3. 用户输入 `/help`：本地渲染目录，不发任何网络请求

---

## DiscordBackend 的职责

```python
if text.startswith("/"):
    name, _, args = text[1:].partition(" ")
    cmd = command_manager.lookup(name)
    if cmd is None:
        await self.send(peer_id, thread_id, f"Unknown command: /{name}")
        return
    # 根据 cmd.acp_method 直接调对应的 kernel 内部方法
    reply = await _execute_for_channel(cmd, args, session_id, self._module_table)
    await self.send(peer_id, thread_id, reply)
```

`_execute_for_channel` 是 DiscordBackend 内的一个小映射函数，把 `cmd.acp_method` 转成对 SessionManager / LLMManager 的直接调用，返回纯文本。这个逻辑属于 DiscordBackend，不属于 CommandManager。

---

## 与现有实现的差距

### 已就绪（无需改动）
- `session/set_mode` → plan mode ✅
- `session/list` → 列出 sessions ✅
- `session/load` → resume session ✅
- `model/provider_list` → 列出和管理模型 ✅
- `model/add` → 新增模型 ✅
- `model/set_current` → 切换 current-used role ✅

### 需要新增 ACP 方法（中等工作量）

| 方法 | 对应命令 | 工作量 |
|------|---------|--------|
| `session/compact` | `/compact` | 小 — Compactor 已存在，加 ACP 入口 + routing；in-flight turn 时返回 `InvalidRequest` |
| `session/delete` | `/session delete` | 小 — SessionManager 加 delete 方法 + routing；`/session clear` 由客户端循环调用此方法 |
| `commands/list` | 目录查询 | 小 — CommandManager startup 后可直接响应 |

### Token 统计持久化（`_mustang.agent/session/get_usage`）

Token 字段的实现属于 SQLite 迁移计划（`session-storage-sqlite.md`），
CommandManager 直接依赖其结果，无需重复实现。当前 `/cost` 已通过
`_mustang.agent/session/get_usage` 暴露会话累计 input/output token、
上下文占用、历史轮次、记忆和环境摘要；按模型金额估算仍等待可信的
provider/model pricing table。

迁移完成后：
- `TurnCompletedEvent` 包含 4 个 per-turn token 字段
- `sessions` 表包含 4 个累计 token 列
- `IndexEntry` 包含对应的累计字段

`_mustang.agent/session/get_usage` 从 session store 和内存 session 读取累计值。

### 需要新建（本设计的主体）
- `kernel/commands/` 目录 + `CommandDef` + `CommandRegistry` + `CommandManager` — 小

---

## 设计决定汇总

| 问题 | 决定 |
|---|---|
| UsageStats 持久化方式 | 扩展 `TurnCompletedEvent` + `IndexEntry`，不新增事件类型 |
| `/session clear` 内核支持 | 否，客户端循环调用 `session/delete` |
| compact 遇到 in-flight turn | 返回 `InvalidRequest`，客户端 turn 结束后重试 |
| index.json 未来方向 | 迁移至 SQLite（session-storage-sqlite.md），CommandManager 依赖迁移后的 IndexEntry |

---

## 实现顺序建议

```
1. 修复 set_config_option → orchestrator.set_config() 连接（Bug fix，优先）
2. 新建 CommandManager + CommandDef catalog（是后续的前提）
3. session/compact + session/delete ACP 方法
   （`_mustang.agent/session/get_usage` 已为 `/cost` 落地，金额聚合后续补齐）
4. commands/list ACP 方法
```

总工作量：约 2-3 天。
