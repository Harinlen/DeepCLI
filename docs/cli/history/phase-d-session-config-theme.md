# CLI Phase D — Session 管理、本地配置与主题计划

**父计划**: [`../../plans/cli-plan.md`](../../plans/cli-plan.md)
**范围**: `src/cli/` TypeScript / Bun client
**状态**: implemented
**前置**: Phase A/B/C 已完成；kernel Session ACP compliance + lifecycle actions 已完成；
Phase E 断线重连不在本阶段实现

## 目标

Phase D 把 CLI 从“能进入一个新会话聊天”补齐为可长期使用的本地客户端：

- 启动时默认新建 session；用户进入 TUI 后可以查看、切换和管理最近 session。
- CLI 有自己的本地配置文件，统一管理 kernel 连接、token 来源、默认 session 行为、
  UI 偏好和主题。
- 主题系统从 active-port 状态变成用户可配置能力。
- 可选实现本地 kernel 自启动，但只覆盖启动前连接失败的场景；运行中断线重连留给
  Phase E。

## 实现结果（2026-04-28）

- `src/cli/src/config/` 已新增 typed client config loader，canonical path 为
  `~/.mustang/client.yaml`，覆盖顺序为 defaults → config → env → argv。
- `src/cli/src/sessions/` 已新增 ACP-only `SessionService`、summary mapper、
  picker/list model、terminal picker fallback，以及 active-port `SessionSelectorComponent`
  adapter；所有 lifecycle action 都走 `session/rename` / `session/archive` /
  `session/delete`。
- `src/cli/src/startup/` 已拆出 argv、connect/token、session startup、theme
  注入和可选 kernel autostart。
- `InteractiveMode` 已接入 Welcome recent sessions、`/session` 本地命令、
  `/theme` 本地命令，切换/删除当前 session 时不混用旧 chat view。
- Kernel 自启动已实现为 opt-in：仅 loopback URL、必须显式配置
  `kernel.autostart_command`，并只清理 CLI 自己 spawn 的进程。
- 验证命令：
  `/home/saki/.bun/bin/bunx tsc -p src/cli/tsconfig.json --noEmit`；
  `BUN_BIN=/home/saki/.bun/bin/bun /home/saki/.bun/bin/bun run src/cli/tests/run_phase_d.ts`；
  `BUN_BIN=/home/saki/.bun/bin/bun /home/saki/.bun/bin/bun run src/cli/scripts/check_active_port.ts`；
  临时 kernel `ws://127.0.0.1:18200` 下 Phase A smoke 和 lifecycle ACP probe。

Phase D 仍保持 CLI thin-client 边界：所有会话数据真相来自 kernel ACP
`session/list` / `session/load`，CLI 不保存对话 transcript，也不直接读取 kernel
SQLite。

**硬规则**：CLI 只能通过 WebSocket ACP 与 kernel 沟通。Phase D 任何实现都不得
import `src/kernel` Python 模块、调用 kernel 子系统对象、直接读写 kernel SQLite / state /
sidecar 文件，或用本地文件约定替代协议调用。缺少的能力必须先补 kernel ACP 表面，
再接 CLI。

## 设计取舍

### 配置文件路径

CLI 设计文档使用 `~/.mustang/client.yaml`，父计划早期草案写的是
`~/.mustang/config.yaml`。Phase D 采用前者作为 canonical path：

```text
~/.mustang/client.yaml
```

原因：

- `config.yaml` 已经是 kernel 业务配置的自然名称；CLI 写入它容易和 kernel
  layered config 混淆。
- `client.yaml` 明确表示这是前端偏好，不是 kernel 子系统配置。
- 迁移成本低：Phase D 可以只读 `client.yaml`；如发现已有旧
  `~/.mustang/config.yaml` 的 CLI 字段，再提供一次性兼容读取并提示迁移。

### Session 选择入口

默认启动策略（2026-04-28 UX 修订后）：

1. `--session <id>` 明确给出时，直接 `session/load`。
2. `--new` 明确给出时，直接 `session/new`。
3. 有 positional prompt 或 `--print` 时，默认新建 session，避免 TUI 选择器阻塞脚本。
4. 无显式 session 参数时，默认直接 `session/new`，让用户尽快进入 TUI。
5. 进入 TUI 后通过 `/session` 查看最近 session，用 `/session switch <number|id>` 切换。
6. 仍保留高级配置 `session.startup = "picker" | "last"`，但 canonical 默认值是 `new`。

### 主题加载

当前 `src/cli/src/active-port/coding-agent/modes/theme/` 已带入 oh-my-pi 主题系统和
大量内置 JSON 主题。Phase D 不重写主题引擎，只新增 DeepCLI 配置读取和启动注入：

- `ui.theme`: 主题名，默认 `dark`。
- `ui.auto_theme`: 跟随系统深浅色，可选。
- `ui.symbols`: `unicode` / `nerd` / `ascii`。
- `ui.status_line`: 是否显示 status line。

`InteractiveMode` 不再硬编码 `initTheme(false)`，而是接收配置后的 theme options。

### Kernel 自启动

自启动是 Phase D 的可选子项，只处理“启动时连接失败”：

- 配置 `kernel.autostart = true` 才启用。
- 只对 loopback URL 生效，远程 URL 不自动 spawn。
- spawn 命令来自配置，默认使用当前 repo 下的 kernel 启动命令。
- readiness 用 HTTP health endpoint 或 ACP connect retry 验证。
- CLI 退出时只清理自己启动的 kernel 进程；连接到既有 kernel 时绝不 kill。

运行中断线、指数退避和 session 恢复属于 Phase E。

## 参考表面

| 来源 | 用途 |
|---|---|
| `src/kernel/kernel/protocol/acp/routing.py` | `session/list` / `session/load` wire shape |
| `src/kernel/kernel/protocol/acp/schemas/session.py` | ACP session summary schema |
| `src/probe/probe/client.py` | Python ACP client 对 session 请求的参考 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/session-selector.ts` | TUI session picker 主体 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/cli/session-picker.ts` | standalone picker lifecycle |
| `docs/kernel/subsystems/session/history/session-lifecycle-actions.md` | kernel-side plan for user-visible delete / rename / archive |
| `src/cli/src/active-port/coding-agent/modes/theme/theme.ts` | 已 port 的主题加载、内置主题、custom theme 目录 |
| `src/cli/src/main.ts` | 当前 argv / connect / session bootstrap |
| `src/cli/src/modes/interactive.ts` | 当前 TUI 初始化和 `initTheme(false)` 调用点 |
| `src/cli/src/session.ts` | 当前 `session/new` / `session/load` wrapper |

## 当前实现快照

| 项 | 当前状态 | Phase D 要补的缺口 |
|---|---|---|
| Session 创建 / 恢复 | `--session` 可 load；无 flag 总是 create | 缺 `session/list` wrapper、picker、启动策略、最近 session resume |
| Welcome recent sessions | `WelcomeComponent` 支持 recent sessions 入参 | 当前传空数组，未接 kernel session summaries |
| Slash `/session` | autocomplete 中存在 `/session` | 本地未处理；Phase D 支持 picker / new / current，并接入 rename / archive / delete |
| 本地配置 | 只读 env、`--port`、token 文件 | 缺 typed config loader、schema、defaults、env/argv precedence |
| Token | `MUSTANG_TOKEN` 或 `~/.mustang/state/auth_token` | 缺配置指定 token 文件 / literal token / password auth 预留 |
| Theme | active-port 主题文件已存在 | 缺用户选择、config 注入、主题列表测试 |
| Kernel 自启动 | 未实现 | 可选 D6；失败时保留当前清晰错误路径 |

### 2026-04-28 风险复核快照

对照当前 `src/kernel/`、`src/cli/` 和 docs 后的事实：

| 检查项 | 结论 | 证据 |
|---|---|---|
| `session/list` / `session/load` 可作为 Phase D 主 seam | 已实现且 shape 已更新 | `AcpSessionInfo` 现在以 `updatedAt` 为主，并带 `archivedAt` / `titleSource` / `_meta`；`session/new/load` 返回 `configOptions` 和 `modes` |
| session delete | ACP route 已实现 | `session/delete` 返回 `{deleted}`；active session 默认要求 `force=true`；kernel 会删除 sidecar |
| session rename | ACP route 已实现 | `session/rename` 返回更新后的 `AcpSessionInfo`；kernel 写 `titleSource=user`，自动标题不会覆盖用户标题 |
| session archive | ACP route 已实现 | `session/archive` 通过 `archived` bool 归档/取消归档；`session/list` 默认隐藏 archived，支持 `includeArchived` / `archivedOnly` |
| upstream session selector 直接可用性 | 仍需要 adapter，但 lifecycle action 可接 kernel | upstream selector 选择 `SessionInfo.path` 并有 file-storage 删除路径；DeepCLI 必须把 `sessionId` 投影到组件字段，并把 Delete/Rename/Archive 回调改成 ACP 调用 |
| CLI active-port session manager | 当前是 stub | `src/cli/src/active-port/coding-agent/session/session-manager.ts` 的 `getRecentSessions()` 返回 `[]` |
| 主题系统 | 已复制但未配置化 | `theme.ts` 有内置/custom theme 加载；当前 `InteractiveMode.run()` 仍硬编码 `initTheme(false)` |
| 配置文件命名风险 | 真实存在 | CLI 设计文档写 `~/.mustang/client.yaml`，旧父计划写 `~/.mustang/config.yaml`；Phase D 采用 `client.yaml` 作为 canonical path |
| per-session MCP | 明确 unsupported | kernel 对非空 `mcpServers` fail-fast `InvalidParams`；CLI Phase D 仍只发送 `mcpServers: []` |

## D0 — 配置和启动状态模型

新增 CLI 本地配置模块：

```text
src/cli/src/config/
├── schema.ts
├── loader.ts
└── paths.ts
```

建议 schema：

```yaml
kernel:
  url: ws://localhost:8200
  token: null
  token_file: ~/.mustang/state/auth_token
  autostart: false
  autostart_command: null
  health_url: http://localhost:8200/

session:
  startup: new           # new | picker | last
  list_scope: cwd        # cwd | all
  include_archived: false
  picker_limit: 50
  restore_cwd: true

ui:
  theme: dark
  auto_theme: false
  symbols: unicode       # unicode | nerd | ascii
  status_line: true
  welcome_recent: 3
```

Precedence：

1. hardcoded defaults
2. `~/.mustang/client.yaml`
3. environment variables：`KERNEL_URL`、`KERNEL_PORT`、`MUSTANG_TOKEN`
4. argv：`--kernel`、`--port`、`--session`、`--new`、`--theme`

验收：

- loader 对缺失文件返回 defaults。
- malformed YAML fail closed，显示文件路径和字段错误。
- env/argv 覆盖顺序有单测。
- token 读取支持 env token、literal config token、token file，且报错不打印 token 内容。

## D1 — ACP Session 列表 wrapper

扩展 `DeepCLISession` 或新增 `src/cli/src/sessions/` 边界模块，提供：

```text
src/cli/src/sessions/
├── types.ts
├── mapper.ts
└── service.ts
```

职责：

- 调 `session/list`，支持 `cwd` filter 和 cursor pagination。
- 支持 `includeArchived` / `archivedOnly`，默认遵循 config `session.include_archived`。
- 将 kernel `AcpSessionInfo` 映射成 UI-facing `CliSessionInfo`。
- 保留原始 `sessionId`，不要使用 oh-my-pi 的 file path 作为会话标识。
- 显式映射 `updatedAt`、`archivedAt`、`titleSource`、`_meta.createdAt`、
  `_meta.totalInputTokens`、`_meta.totalOutputTokens`。
- 兼容 session summary 字段缺失：title / cwd / timestamps / token counters 缺失时仍可渲染。
- 提供 thin lifecycle methods：`rename(sessionId,title)`、`archive(sessionId,archived)`、
  `delete(sessionId,{force})`，全部通过 ACP 调 kernel。

关键约束：

- CLI 与 kernel 的唯一 runtime seam 是 WebSocket ACP；`SessionService` 只能依赖
  `AcpClient.request/notify/on`。
- CLI 不读 kernel SQLite。
- CLI 不读写 kernel session sidecar，不调用 kernel Python API。
- `session/load` 仍只传 `sessionId`、`cwd`、`mcpServers: []`；Phase D 不支持非空 session-scoped MCP。
- list 失败时不阻塞新建 session；显示 warning 后 fallback create。
- lifecycle action 失败时必须显示 kernel error；不能 fallback 到本地文件操作。

验收：

- fake ACP tests 覆盖空列表、分页、多 cwd、archived 过滤、字段缺失。
- fake ACP tests 覆盖 rename/archive/delete 的 request/response mapper。
- real kernel probe 覆盖 `session/list -> select id -> session/load`。
- real kernel probe 覆盖 `session/rename -> session/archive -> session/list(archivedOnly) -> session/delete`。

## D2 — Session 选择器 active-port

把 oh-my-pi `session-selector.ts` 作为 UI 主体迁入 active-port：

```text
src/cli/src/active-port/coding-agent/modes/components/session-selector.ts
```

如果 upstream 组件强依赖 `SessionInfo.path` / `FileSessionStorage`：

- 保留 render / input 主体。
- 在 DeepCLI mapper 中把 `sessionId` 投影到组件需要的 `path` 字段，选择后再映射回
  `sessionId`。
- Delete / Rename / Archive 入口可以启用，但必须接 DeepCLI session service 的 ACP
  lifecycle methods，不能调用 upstream `FileSessionStorage`。
- Delete 默认先显示确认；如果当前 session 或 active session 被删除，必须传 `force=true`
  并清晰提示这是永久删除。普通历史 session 可以先尝试 `force=false`，kernel 若拒绝再提示。
- Archive 是默认隐藏，不是本地移除；picker 需要有 include archived 视图或 filter。

启动 picker lifecycle 可参考 upstream `cli/session-picker.ts`，但 DeepCLI 版本必须使用
现有 `@/tui/index.js` facade 和 `ProcessTerminal`，不能引入新的 oh-my-pi package
依赖。

交互要求：

- 搜索框支持 fuzzy filter。
- 上下键、PageUp/PageDown、Enter、Esc、Ctrl+C 行为与 upstream 一致。
- Delete / Rename / Archive keybinding 只在服务层能力存在时显示；Phase D 的 kernel
  前置已满足这些能力。
- Esc 取消后按配置 fallback：默认新建 session。
- Ctrl+C 在 picker 内退出 CLI，exit code 0。
- 空列表直接返回 “new session”。

验收：

- render test：空列表、短列表、长列表滚动、title fallback、cwd 显示。
- render test 覆盖 archived 标记和 titleSource=user 的显示策略。
- keyboard test：搜索、选择、rename、archive/unarchive、delete confirm、Esc fallback、Ctrl+C exit path。
- active-port manifest 包含新增 copied 文件。

## D3 — 启动编排重构

把 `main.ts` 中的启动流程拆成可测试单元：

```text
src/cli/src/startup/
├── args.ts
├── connect.ts
├── session-startup.ts
└── autostart.ts
```

目标流程：

```text
parse argv
  -> load config
  -> resolve kernel endpoint + token
  -> connect or optional autostart
  -> resolve session startup mode
  -> init theme
  -> run InteractiveMode
```

注意：

- `main.ts` 只保留 thin orchestration 和 `process.exit`。
- `--session` / `--new` 互斥，冲突时打印 usage 并 exit 2。
- `--print` / positional prompt 如已存在，不能被 picker 阻塞。
- session startup 失败时，只有明确 `--session` 的 load failure 是 fatal；picker/last
  的 load failure 可以 fallback new 并提示。

验收：

- 单测覆盖 argv/config/env precedence 和 startup branch。
- 当前 Phase A `run_all.ts` 仍通过。

## D4 — Welcome 和 `/session` 本地命令

把 session 信息带进当前 TUI：

- `WelcomeComponent` 的 recent sessions 入参使用 `session/list` 最近 N 条。
- status line session title 在 load 后立即使用 summary title；后续仍由
  `session_info_update` 更新。
- 本地 `/session` 命令最小支持：
  - `/session` 输出最近 session 列表和切换提示。
  - `/session switch <number|session-id>` 切换到列表中的 session 或指定 id。
  - `/session new` 新建 session 并切换当前 TUI。
  - `/session current` 显示当前 session id / title / cwd。
  - `/session rename <title>` 调 `session/rename` 并更新当前标题。
  - `/session archive` / `/session unarchive` 调 `session/archive`。
  - `/session delete` 需要确认；删除当前 session 后默认新建并切换过去。
  - `/session list --archived` 打开 archived-only picker 或输出 archived summaries。

切换 session 时：

- 如果当前 prompt 正在运行，拒绝切换并提示先 cancel。
- 清空当前 chat view 或插入明确分隔提示；不要把两个 session 的流混在同一个消息树里。
- 重新安装 update handler 使用新的 `DeepCLISession`。

验收：

- fake session service tests 覆盖 picker selection 后切换当前 session。
- fake session service tests 覆盖 rename/archive/delete 命令成功和失败路径。
- TUI test 覆盖 busy 时 `/session` 被拒绝。

## D5 — 主题配置接入

把 config 的 UI 字段传入 theme 初始化和 interactive mode：

- `main.ts` / startup 读取 `ui.theme`、`ui.auto_theme`、`ui.symbols`。
- `InteractiveMode.run()` 调用配置化的 `initTheme(...)`，不再硬编码 `false`。
- 如果主题名不存在，fallback `dark` 并显示 warning。
- `--theme <name>` 覆盖配置。
- 可选本地命令 `/theme`：
  - `/theme` 显示当前主题。
  - `/theme list` 列出内置 + custom themes。
  - `/theme set <name>` 本次进程切换，并可选写回 `client.yaml`（写回可延期）。

验收：

- theme loader 单测覆盖内置主题、缺失主题 fallback、custom theme dir。
- snapshot/render test 至少覆盖 dark 和 light 两个主题下的 welcome/status/editor。
- `bunx tsc` 不因为 theme JSON imports / typebox schema 新增错误。

## D6 — 可选 Kernel 自启动

实现为独立、可跳过批次；如果时间不够，Phase D 可以先标记 deferred，不影响 D0-D5
交付。

文件：

```text
src/cli/src/startup/autostart.ts
```

行为：

- 仅当 `kernel.autostart = true` 且初次 connect 失败时尝试。
- 只允许 loopback host：`localhost` / `127.0.0.1` / `::1`。
- `autostart_command` 缺失时使用安全默认；如果当前 repo 路径无法判断，直接提示配置命令。
- readiness 最多等待 15 秒。
- 记录 spawned process handle，CLI 正常退出时 terminate；异常退出时尽量 cleanup。

验收：

- 单测用 fake spawn/readiness，不启动真实 kernel。
- 手动 probe：无 kernel 运行时，CLI autostart 后能进入 TUI 并创建 session。

## D7 — Phase D 测试矩阵

新增或扩展：

```text
src/cli/tests/
├── run_phase_d.ts
├── test_config_loader.ts
├── test_session_list_mapper.ts
├── test_session_picker.ts
├── test_session_startup.ts
├── test_theme_config.ts
└── test_autostart.ts          # 若 D6 实现
```

真实 kernel closure-seam probes：

- `session/list` real ACP request 返回可渲染 summaries。
- picker 选择一个真实 summary 后，`session/load` 成功恢复。
- lifecycle closure seam：真实 kernel 执行 rename / archive / delete 后，下一次
  `session/list` 反映对应变化。
- `client.yaml` 指定 kernel URL/token file 后，CLI 能连接真实 kernel。
- theme config 注入后，`InteractiveMode` 首屏渲染使用指定 theme。

命令：

```bash
/home/saki/.bun/bin/bunx tsc -p src/cli/tsconfig.json --noEmit
BUN_BIN=/home/saki/.bun/bin/bun /home/saki/.bun/bin/bun run src/cli/scripts/check_active_port.ts
BUN_BIN=/home/saki/.bun/bin/bun /home/saki/.bun/bin/bun run src/cli/tests/run_phase_d.ts
BUN_BIN=/home/saki/.bun/bin/bun KERNEL_URL=ws://127.0.0.1:8200 /home/saki/.bun/bin/bun run src/cli/tests/run_all.ts
```

如果 D6 自启动实现，还要跑一条无 kernel 预启动的 manual probe，并在完成报告贴输出。

## 完成标准

Phase D 完成时必须满足：

- 无 `--session` 的交互启动默认新建 session，不在 TUI 之前阻塞用户。
- `/session` 能从真实 kernel `session/list` 获取 session，并通过 `/session switch`
  触发 `session/load` 恢复。
- `~/.mustang/client.yaml` 被 typed loader 读取，env/argv 覆盖顺序明确且有测试。
- token 来源支持 env、config literal、config token file、默认 token file。
- 主题可通过 config 和 `--theme` 选择，缺失主题有 fallback。
- Welcome recent sessions 不再永远为空。
- `/session` 至少支持列表、编号/ID 切换、新建和当前 session 信息。
- `/session` lifecycle 子命令通过 kernel ACP 支持 rename、archive/unarchive、delete；
  picker 中对应入口不得调用 file-storage 删除路径。
- active-port 新增文件都登记在 manifest，检查脚本通过。
- TypeScript typecheck、Phase D 本地测试、Phase A smoke 测试通过。
- 若实现 D6，自启动 probe 输出必须贴在完成报告；若不实现，父计划和本文档状态明确标为
  deferred，不影响 D0-D5 完成。

## 非目标

- 不实现运行中断线检测和自动重连；那是 Phase E。
- 不把 session transcript 存到 CLI 本地。
- 不让 CLI 直接读取或写入任何 kernel SQLite / state / sidecar 文件。
- 不让 CLI import 或调用任何 kernel Python 内部 API。
- 不实现 session-scoped MCP server 配置；kernel 当前对非空 `mcpServers` 明确
  unsupported，CLI Phase D 始终发送 `mcpServers: []`。
- 不迁移 oh-my-pi agent loop、provider、tool runtime。
- 不新增 Web / IDE 前端配置。

## 风险与关闭方式

| 风险 | 当前验证 | 关闭方式 |
|---|---|---|
| `client.yaml` 与旧 `config.yaml` 命名冲突 | 已验证：CLI design doc 与旧父计划确有分歧 | 文档明确 canonical path；如实现兼容读取，只读不写旧路径并给 migration 提示 |
| upstream session selector 用 file path，不适配 ACP session id | 已验证：upstream selector 回传 `session.path` | mapper 层投影 `sessionId -> path`，UI 选择后映射回 id；不让 path 进入 ACP |
| upstream selector 暗含文件删除能力 | 已验证：upstream `session-picker.ts` 调 `FileSessionStorage.deleteSessionWithArtifacts()`；DeepCLI kernel 已有 `session/delete` ACP | adapter 层禁止使用 file storage；Delete/Rename/Archive 全部走 `SessionService` ACP 方法 |
| lifecycle action 删除当前 session | kernel 对 active session 默认拒绝，`force=true` 才允许 | CLI 先确认；删除当前 session 后清空 UI 并进入 picker/new fallback，避免继续向已删 session 发 prompt |
| archived sessions 被误认为消失 | kernel 默认 list 隐藏 archived | picker 增加 archived 视图或 includeArchived toggle；`/session list --archived` 明确展示 |
| picker 阻塞脚本 / print 模式 | 已验证启动前 picker 也会损害普通交互体验 | canonical 默认启动策略改为新建 session；session 切换放到 TUI 内 `/session` |
| 主题初始化过早，组件拿到旧 singleton | 已验证：当前 `InteractiveMode.run()` 硬编码 `initTheme(false)` | config 解析在 `InteractiveMode.run()` 前完成，所有组件创建前调用 configured `initTheme` |
| 自启动误杀用户已有 kernel | 当前未实现 | 只保存并 cleanup 本进程 spawn 的 child process；既有连接不归 CLI 管 |
| active-port 膨胀 | 已验证：当前 active-port 已含大量 theme 文件，selector 尚未复制 | 只复制 session selector 闭包；manifest 检查新增文件；发现外设依赖用 facade 截断 |
