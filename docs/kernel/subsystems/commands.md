# CommandManager — Design

> **Quick header**
> - **Role**: slash-command catalog design.
> - **Current code**: `kernel.agents.mustang.commands.*`.
> - **Runtime owner**: Mustang runtime; Access/CLI only consume command lists over ACP.
> - **Boundary**: catalog only, not command dispatch or CLI autocomplete UI.

Status: **active / closure verified 2026-05-24** — CommandManager 目录、
`/skills`、以及 `user-invocable` Skills 的 canonical `/skill:<name>` 投影
已经 landed。2026-05-24 的命令闭合修正已重新对齐 Kernel command catalog、
CLI slash execution surface、CLI autocomplete surface、Mustang Agent Runtime ACP
dispatcher、real-kernel E2E 覆盖。当前规则仍然不变：catalog 不是完成证明，
凡 catalog 暴露的用户命令都必须有对应 CLI 执行路径和真实 kernel closure
probe。

---

## 核心概念

CommandManager 是**命令目录提供者**，不是执行者。

- 维护一份 `CommandDef` 注册表（名称、描述、用法、映射关系）
- WS 客户端在 initialize 握手后拉取目录，自己解析命令并调用对应 ACP 方法
- kernel-side 客户端（DiscordBackend）查目录，直接调对应的 SessionManager / LLMManager 方法
- `user-invocable` Skills 投影为 `source="skill"` 命令；canonical name 是
  `skill:<name>`，执行走
  `_mustang.agent/session/activate_skill`，CLI 不读本地 Skill 文件
- **没有 `session/command` ACP 方法**，不新建执行通道，执行永远走现有机制

这与 Claude Code 一致：命令是客户端解析的 convenience wrapper，执行走现有协议原语。

---

## 当前命令闭合状态

### 实际跑过的命令

`src/cli/tests/probe_real_kernel_slash_commands.ts` 当前会启动
`scripts/run-kernel.sh`，通过 CLI `AcpClient` 连接真实 Access Router
`/session`，并执行 CLI builtin slash registry。真实 probe 明确跑过这些输入：

```text
/clear
/compact
/cost
/memory list
/memory show probe-memory
/memory delete probe-memory --confirm
/cron list
/cron create 1h check scheduling
/cron delete missing-cron-job
/help
/theme current
/theme list
/theme set dark
/plan status
/plan enter
/plan status
/plan exit
/session info
/session current
/session list
/session new
/session switch 1
/session load 1
/session resume 1
/session rename Probe Session
/session archive
/session unarchive
/model current
/model list
/model add
/model use real_slash_fake/real-slash-model
/webfetch backend
/webfetch backend auto
/webfetch browser install
/webfetch browser status
/webfetch browser pair
/webfetch browser reset
/webfetch config
/webfetch install httpx
/kernel status
/global backup
/global backups
/global export <temp-file>
/global import <temp-file> --dry-run
/global restore <temp-file> --confirm
/flag list
/flag read kernel.memory
/flag set kernel.memory false
/flag reset kernel.memory
/secrets list
/secrets audit
/secrets rename <fixture-secret-id> probe-renamed <revision>
/secrets delete <fixture-secret-id> <revision> --confirm
/agents create worker <workspace> Worker
/agents list
/agents read worker
/agents add worker2 /tmp Worker2
/agents set-identity worker WorkerRenamed
/agents bindings
/agents health worker
/agents grants
/agents grants worker
/agents grant worker agent_control global
/agents revoke-grant <fixture-grant-id>
/agents start worker
/agents stop worker
/agents restart worker
/gateways create testgw test {}
/gateways list
/gateways read testgw
/gateways status testgw
/gateways enable testgw
/gateways reload testgw
/gateways bindings
/gateways bind testgw chan1 worker
/gateways bindings testgw
/gateways unbind testgw:chan1
/agents bind worker testgw:chan2
/agents unbind worker testgw:chan2
/gateways disable testgw
/agent send primary hello
/mcp create remote {"type":"http","url":"https://mcp.example.test","headers":{"Authorization":"secret:abc"}}
/mcp list
/mcp read remote
/mcp update remote {"type":"http","url":"https://mcp.example.test/v2","headers":{"Authorization":"secret:abc"}}
/mcp delete remote
/skills list
/skills inspect skill-installer
/skills refresh
/skills install owner/repo --ref main
/skills sources
/skills search test
/skills check skill-installer
/skills update skill-installer
/skills audit
/skills uninstall skill-installer
/agents delete worker --confirm
/agents delete worker2 --confirm
/agents delete <fixture-agent-id> --confirm
/gateways delete testgw --confirm
/session delete confirm
/kernel restart
/quit
/exit
```

另外，probe 会从真实 runtime `commands/list` 里拿出所有
`source="skill"` 的命令，并逐个通过 CLI `--print /skill:<name>` 激活。当前
至少包括 `/skill:skill-installer`。

### 没有证明的东西

这次 probe 没有证明下面这些：

- 没有证明完整 TUI/PTY 渲染行为，只证明 slash registry 输入会执行到对应路径。
- `/compact`、`/clear`、`/help`、`/quit`、`/exit` 是本地 UI 命令；probe 只证明
  它们被 CLI registry 调到了本地 handler，没有证明 Kernel ACP。
- `/model add`、`/webfetch backend`、`/webfetch config` 这类打开选择器的命令，
  probe 只证明入口可调用，没有在 TUI 里完成人工选择流程。
- `/skills install/search/sources/check/update/audit/uninstall` 只证明会激活
  `skill-installer`，没有证明真实下载/写入/更新外部 skill。
- `/cron delete missing-cron-job` 证明 delete 方法对不存在 id 可返回，不证明删除
  一个刚创建的真实 job。
- `/kernel restart` 证明 runtime-control 方法可调用；probe 不在 restart 后继续验证
  新 runtime 的完整交互。

Verification marker:

```text
bun run tests/probe_real_kernel_slash_commands.ts
warnings=0
result=PASS
```

## 命令映射表

每个命令映射到一个已有的 ACP 方法或 kernel 内部方法：

| 命令 | ACP 方法（WS 客户端） | Kernel 内部（Discord 等） | 缺口 |
|------|----------------------|--------------------------|----|
| `/model list` | `model/provider_list` | `LLMManager.list_providers()` | 无 |
| `/model add` | `model/add` | `LLMManager.add_model()` | 无 |
| `/model current` | `model/provider_list` | `LLMManager.list_providers()` | 无 |
| `/model use [role] <provider>/<model>` | `model/set_current` | `LLMManager.set_current_model()` | 无 |
| `/plan [enter\|exit\|status]` | `session/set_mode` | `session_manager.set_mode()` | 无 |
| `/compact` | local command-controller path | CLI session compaction hook | local UI action; no Kernel ACP closure claimed |
| `/session list` | `session/list` | `session_manager.list()` | 无 |
| `/session delete confirm` | `_mustang.agent/session/delete` via CLI session service | `session_manager.delete()` | 无 |
| `/session load/switch/resume <id>` | `session/load` | `session_manager.load_session()` | 无 |
| `/cost` | `_mustang.agent/session/get_usage` | `SessionManager.get_usage()` | 美元价格估算待可信 pricing table |
| `/help` | 本地渲染（从 catalog 生成） | 本地渲染 | 无 |
| `/memory list/show/delete` | `_mustang.agent/memory/*` | `MemoryManager` management methods | 无 |
| `/skills list` | `_mustang.agent/skills/list` | `SkillManager.list_skill_records()` | 无 |
| `/skills inspect <name>` | `_mustang.agent/skills/inspect` | `SkillManager.inspect_skill()` | 无 |
| `/skills refresh` | `_mustang.agent/skills/refresh` | `SkillManager.refresh()` | 无 |
| `/skills install/search/sources/check/update/audit/uninstall` | `_mustang.agent/session/activate_skill(skill="skill-installer")` | 激活 bundled `skill-installer` | 无 kernel install apply path |
| `/cron list/create/delete` | `_mustang.agent/cron/*` | `ScheduleManager` | 无 |
| `/agents start/stop/restart/health/grants/...` | `_mustang.agent/agents/*` | AgentManager ACP lifecycle/grant APIs | 无 |
| `/gateways enable/disable/reload/bindings/unbind` | `_mustang.agent/gateways/*` | Access Router/Gateway ACP APIs | 无 |

---

## 目录结构

```
src/kernel/kernel/agents/mustang/commands/
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
    source: str = "builtin"   # "skill" = SkillManager projection
    aliases: list[str] = field(default_factory=list)
    canonical_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
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
        self._register_skill_commands()

    def list_commands(self) -> list[CommandDef]: ...
    def list_command_dicts(self) -> list[dict[str, Any]]: ...
    def lookup(self, name: str) -> CommandDef | None: ...
```

无 `dispatch()`，无执行逻辑。CommandManager 订阅 SkillManager 的
skills-changed signal，动态 skill 出现/消失时重建目录。

---

## ACP 集成

客户端通过一个轻量的 commands list 请求获取命令目录。

```
Client → { method: "_mustang.agent/commands/list" }
Kernel → { result: { commands: [ { name, description, usage, acpMethod, source }, ... ] } }
```

这是 DeepCLI 扩展方法。router backend 下该请求通过 Access -> Hub ->
Mustang Agent runtime，避免 CLI 看到 Access-local 的 stale catalog。

---

## WS 客户端的职责

1. 获取目录 → 维护本地命令注册表（用于 autocomplete、`/help`）
2. 用户输入 `/model use default provider/model`：
   - 查本地目录：`acp_method = "model/set_current"`
   - 直接发 `{ method: "model/set_current", params: { role: "default", provider: "provider", model: "model" } }`
   - 从 ACP response / broadcast 中渲染结果
3. 用户输入 `/help`：本地渲染目录，不发任何网络请求
4. 用户输入 `/skill:<name> args`：
   - 只在本地确认 catalog 中 `source="skill"`
   - 从 `CommandDef.metadata["skillName"]` 取真实 skill name
   - 发 `_mustang.agent/session/activate_skill`
   - Kernel 校验 `user_invocable`，激活 SkillManager，记录 invoked skill，
     再进入普通 prompt queue

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
- `kernel/agents/mustang/commands/` 目录 + `CommandDef` + `CommandRegistry` + `CommandManager` — 小

---

## 设计决定汇总

| 问题 | 决定 |
|---|---|
| UsageStats 持久化方式 | 扩展 `TurnCompletedEvent` 并累加到 SQLite `sessions` 行，不新增事件类型 |
| `/session clear` 内核支持 | 否，客户端循环调用 `session/delete` |
| compact 遇到 in-flight turn | 返回 `InvalidRequest`，客户端 turn 结束后重试 |
| session metadata 来源 | 读取 SessionManager / SessionStore 的当前 SQLite 视图，不维护 `index.json` |

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
