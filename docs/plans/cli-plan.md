# CLI 客户端实现计划

**设计文档**: [`../cli/design.md`](../cli/design.md)
**代码位置**: `src/cli/`
**技术栈**: TypeScript / Bun

> 本文件留在 `docs/plans/`，因为它仍包含未完成的 CLI 后续工作
> （例如 Phase E reconnect）。已实现的 CLI 设计事实放在
> [`../cli/design.md`](../cli/design.md)，已实现阶段的历史记录放在
> [`../cli/history/`](../cli/history/)。

---

## 前置知识：Kernel ACP 协议

在读代码之前，先把 kernel 现有的 ACP 接口搞清楚。
核心文件：`src/kernel/kernel/protocol/acp/`

### Client → Kernel（请求）

| 方法 | 作用 |
|------|------|
| `session/new` | 新建 session，返回 `sessionId` |
| `session/load` | 加载已有 session，返回历史消息 |
| `session/list` | 列出所有 session |
| `session/prompt` | 发送用户消息，长连接，期间收 `session/update` 事件流 |
| `session/cancel` | 取消进行中的 prompt（notification，无响应） |
| `session/set_mode` | 切换模式（`default` / `plan`） |
| `session/set_config_option` | 修改运行时配置 |
| `model/profile_list` / `model/set_current` | 模型管理 |
| `secrets/auth` | 认证（密码换 token） |

### Kernel → Client（推送）

所有推送都是 `session/update` notification，`params.update.sessionUpdate`
字段区分类型（camelCase，匹配当前 kernel ACP schema）：

| `update.sessionUpdate` | 触发场景 | CLI 动作 |
|---|---|---|
| `agent_message_chunk` | streaming text token | 追加到 AssistantMessage 组件 |
| `agent_thought_chunk` | thinking token | 追加到 thinking 折叠块 |
| `tool_call` | 工具调用开始 | 创建 ToolExecution 组件 |
| `tool_call_update` (in_progress) | 工具进行中 | 更新 ToolExecution 进度 |
| `tool_call_update` (completed) | 工具完成 | 更新 ToolExecution 结果 |
| `tool_call_update` (failed) | 工具报错 | 更新 ToolExecution 错误状态 |
| `tool_call_update` + `_meta.mustang/agent_start` | 子 agent 启动 | 嵌套 ToolExecution |
| `plan` | plan mode 条目变更 | 更新 Plan 面板 |
| `current_mode_update` | 模式切换 | 更新 StatusLine |
| `session_info_update` | session 标题变更 | 更新 StatusLine 标题 |
| `available_commands_update` | 斜杠命令列表变更 | 更新 autocomplete 候选 |
| `config_option_update` | 配置变更 | 本地状态同步 |

### Kernel → Client（kernel 发起的请求）

| 方法 | 场景 | CLI 动作 |
|------|------|----------|
| `session/request_permission` | 工具授权审批 | 弹出 approve/deny 对话框，返回结果 |

---

## 阶段划分

### Phase A — Skeleton + 裸 ACP 循环（P0）

**目标**：`bun run src/cli/src/main.ts` 能跑通，能和 kernel 收发消息，
看到流式文本输出。这一阶段不需要漂亮的 UI，跑通协议层是唯一目标。

#### A1 — 项目骨架

创建 `src/cli/` 目录结构：

```
src/cli/
├── package.json        name: "deepcli-cli", runtime: bun
├── tsconfig.json
└── src/
    ├── main.ts
    ├── acp/
    │   └── client.ts
    └── session.ts
```

**`package.json` 依赖**（最小集）：
```json
{
  "dependencies": {
    "ws": "^8",
    "chalk": "^5"
  }
}
```

#### A2 — ACP WebSocket Client（`src/acp/client.ts`）

实现一个轻量 JSON-RPC over WebSocket 客户端：

```typescript
class AcpClient {
  // 连接，带 token 认证（?token=xxx query param）
  static async connect(url: string, token: string): Promise<AcpClient>

  // 发请求，等响应
  async request<R>(method: string, params: unknown): Promise<R>

  // 发 notification（无响应）
  notify(method: string, params: unknown): void

  // 订阅 kernel 推送
  on(method: 'session/update', handler: (params: SessionUpdate) => void): void
  on(method: 'session/request_permission', handler: (id: string, params: PermissionRequest) => Promise<PermissionResult>): void

  close(): void
}
```

**关键点**：
- `session/prompt` 是长连接请求：发出后 kernel 会推多条 `session/update` notification，
  最后才回 `PromptResult`；client 要能在等 response 的同时处理 notification
- `session/request_permission` 是 kernel 主动发的 request，client 要能回 response（不是 notification）
- 用 `id` 字段区分 request/response/notification（标准 JSON-RPC 2.0）

参考：`src/probe/probe/client.py`（Python 实现，逻辑完全对应）

#### A3 — Session 操作封装（`src/session.ts`）

```typescript
class DeepCLISession {
  constructor(private client: AcpClient, public sessionId: string) {}

  static async create(client: AcpClient): Promise<DeepCLISession>
  static async load(client: AcpClient, id: string): Promise<DeepCLISession>

  async prompt(text: string, onUpdate: (update: SessionUpdate) => void): Promise<PromptResult>
  cancel(): void
  async setMode(mode: 'default' | 'plan'): Promise<void>
}
```

#### A4 — 最小 main.ts

```
main.ts 逻辑：
1. 读 ~/.mustang/config.yaml（或默认 ws://localhost:8765 + dev token）
2. AcpClient.connect()
3. DeepCLISession.create()（或 --session <id> 时 load）
4. 进入简单 readline loop：
   - 读一行 → session.prompt()
   - session/update → console.log 流式文本
   - PromptResult → 换行，继续等输入
```

#### A5 — Phase A 测试脚本

测试脚本放在 `src/cli/tests/`，用 Bun 直接跑，对着真实 kernel 验证。
每个脚本独立，pass/fail 有明确退出码（0 = 通过，非 0 = 失败）。

```
src/cli/tests/
├── run_all.ts          # 串行跑全部脚本，汇总结果
├── test_connect.ts     # seam 1：连接 + 认证
├── test_session.ts     # seam 2：session/new 往返
├── test_prompt.ts      # seam 3：session/prompt 流式接收
└── test_multiturn.ts   # seam 4：多轮对话连续性
```

**`test_connect.ts`** — 验证 seam 1

```typescript
// 连接 kernel，握手成功即通过，4003/超时即失败
const client = await AcpClient.connect(KERNEL_URL, TOKEN)
await client.close()
console.log("PASS: connect + auth")
```

**`test_session.ts`** — 验证 seam 2

```typescript
// session/new 返回 sessionId，类型正确
const client = await AcpClient.connect(KERNEL_URL, TOKEN)
const result = await client.request('session/new', {})
assert(typeof result.sessionId === 'string', 'sessionId missing')
await client.close()
console.log("PASS: session/new → sessionId =", result.sessionId)
```

**`test_prompt.ts`** — 验证 seam 3

```typescript
// session/prompt 能收到 agent_message_chunk，最终收到 PromptResult
const chunks: string[] = []
client.on('session/update', update => {
  if (update.sessionUpdate === 'agent_message_chunk') chunks.push(update.content.text)
})
const result = await session.prompt('respond with exactly: hello world')
assert(chunks.length > 0, 'no agent_message_chunk received')
assert(result.stopReason !== undefined, 'PromptResult missing')
console.log(`PASS: prompt → ${chunks.length} chunks, stopReason=${result.stopReason}`)
```

**`test_multiturn.ts`** — 验证 seam 4

```typescript
// 第一轮说一个词，第二轮问"你上一句说了什么"，验证 kernel 能回忆
await session.prompt('remember the word: ZEPHYR')
const result2 = await session.prompt('what word did I just ask you to remember?')
const fullText = result2.content.join('')
assert(fullText.includes('ZEPHYR'), `expected ZEPHYR in response, got: ${fullText}`)
console.log("PASS: multi-turn context preserved")
```

**运行方式**：

```bash
# 需要 kernel 已在运行
KERNEL_URL=ws://localhost:8765 MUSTANG_TOKEN=dev bun run src/cli/tests/run_all.ts
```

**Phase A DoD**：`run_all.ts` 全部 PASS，无人工干预。

---

### Phase B — 完整 TUI（P1）

详细执行方案已拆分到
[`../cli/history/phase-b-tui-migration.md`](../cli/history/phase-b-tui-migration.md)。

**目标**：按需迁移 oh-my-pi 的 TUI runtime、components、controllers 和
`interactive-mode.ts` 主路径，让 DeepCLI CLI 的视觉效果与输入行为和 oh-my-pi
`omp` 完全一致。

**核心原则**：默认复制当前阶段闭环需要的 oh-my-pi 代码，而不是整包导入；只有在
原逻辑触碰 oh-my-pi agent loop / provider / tool side effect、Phase B 不做的外设能力，
或 package import 需要改到 DeepCLI compat/active-port source 时才改写。

Phase B 按独立计划执行：

- `B0` port 管理脚手架
- `B1` TUI 主路径闭包复制与模块化激活
- `B2` 真实 kernel probe + Phase B 汇总测试

`B1` 会一次性复制 interactive TUI 主路径需要的 oh-my-pi runtime + UI 源码闭包，
再按 Runtime、Assistant、Tool、Editor、Status/Welcome/Commands、ACP Adapter、
InteractiveMode 模块激活和验收，避免最小 runtime 或碎片化迁移导致大量自写 UI glue。

**Phase B DoD**：见
[`../cli/history/phase-b-tui-migration.md#完成标准`](../cli/history/phase-b-tui-migration.md#完成标准)。

**Repair note — 2026-04-28**：当前可运行 CLI 只完成了 Phase B 的 first usable
path，并未满足 visual/input parity。入口仍走 DeepCLI 自写 `modes/interactive.ts`，
`StatusLineComponent` 也被裁成 stub。修复计划见
[`../cli/history/phase-b-ui-alignment-repair.md`](../cli/history/phase-b-ui-alignment-repair.md)；在该计划完成前，
不要把 Phase B 视为已达成 "和 oh-my-pi `omp` 完全一致"。

---

### Phase C — 工具授权交互（P1） — implemented 2026-04-27

详细执行方案已拆分到
[`../cli/history/phase-c-permissions.md`](../cli/history/phase-c-permissions.md)。

**目标**：处理 `session/request_permission` kernel request，用 oh-my-pi
现有 selector/input overlay 体系展示工具授权、结构化问题和取消流程，并把选择作为
JSON-RPC response 回给 kernel。

**核心原则**：继续 Phase B 的 active-port 策略，尽量原封不动复制 oh-my-pi UI /
controller 源码；DeepCLI 自写代码只放在 ACP 边界和状态转换层。

Phase C 已按独立计划实现：

- `C0` upstream surface inventory
- `C1` permission data model and mapper
- `C2` activate oh-my-pi overlay controller
- `C3` tool permission UI
- `C4` structured question path
- `C5` ACP client plumbing
- `C6` tests

**Phase C DoD**：见
[`../cli/history/phase-c-permissions.md#完成标准`](../cli/history/phase-c-permissions.md#完成标准)。

**Interactive keybindings gap**：Phase B/C 之后仍有一批 oh-my-pi
`InputController` 交互未迁移，调查结果见
[`cli-interactive-keybindings-gap.md`](cli-interactive-keybindings-gap.md)。

---

### Phase D — Session 管理 + 配置 + 主题（P2）

**状态**：已实现（2026-04-28）。

详细执行方案已拆分到
[`../cli/history/phase-d-session-config-theme.md`](../cli/history/phase-d-session-config-theme.md)。

**目标**：补齐长期使用 CLI 所需的 session 选择 / 恢复、本地 client 配置和主题选择。
CLI 继续保持 thin-client：session 列表和恢复都走 kernel ACP
`session/list` / `session/load`，不读取 kernel SQLite，不保存 transcript。
CLI 与 kernel 的唯一运行时通信方式是 WebSocket ACP；Phase D 不允许 import kernel
代码、读取 kernel state/SQLite/sidecar 文件，或通过本地文件约定绕过 ACP。

Phase D 按独立计划执行：

- `D0` 配置和启动状态模型
- `D1` ACP session 列表 wrapper
- `D2` session 选择器 active-port
- `D3` 启动编排重构
- `D4` Welcome recent sessions 和 `/session` 本地命令
- `D5` 主题配置接入
- `D6` 可选 kernel 自启动
- `D7` Phase D 测试矩阵

**Phase D DoD**：见
[`../cli/history/phase-d-session-config-theme.md#完成标准`](../cli/history/phase-d-session-config-theme.md#完成标准)。

---

### Phase E — 断线检测与重连（P1）

**目标**：kernel 重启或网络中断后，CLI 不 hang 死，能自动重连并恢复交互。

**背景**：Phase A 当前行为——kernel 断开后 WebSocket close 事件未监听，
所有 pending request 的 Promise 永远不 resolve，REPL hang 死，只能 Ctrl+C 强杀。

#### E1 — 断线检测（`AcpClient`）

监听 `ws.on("close", ...)` 和 `ws.on("error", ...)`，触发时：
- reject 所有 `pending` Map 里的 request（抛 `KernelDisconnected` 错误）
- 触发 `onDisconnect` 回调，通知上层

```typescript
class KernelDisconnected extends Error {}

// AcpClient 新增：
onDisconnect(handler: (code: number, reason: string) => void): void
```

#### E2 — main.ts 捕获断线

```typescript
client.onDisconnect(() => {
  process.stderr.write(chalk.red("\n[disconnected] Kernel closed the connection.\n"))
  // 进入重连流程（E3）或干净退出
})
```

当前在进行中的 `session.prompt()` 抛出 `KernelDisconnected` → REPL 捕获 → 显示断线提示。

#### E3 — 自动重连

断线后进入指数退避重连循环（最多 N 次，默认 10 次）：

```
断线 → 等 1s → 重连 → 失败 → 等 2s → 重连 → 失败 → 等 4s → … → 放弃退出
```

重连成功后：
1. `AcpClient.connect()` 建立新 WS + 重新 initialize
2. `DeepCLISession.load()` 恢复原 session（复用 `sessionId`）
3. 打印 `[reconnected]` 提示，REPL 继续接受输入

```typescript
async function reconnectLoop(
  url: string,
  token: string,
  sessionId: string,
  maxAttempts = 10,
): Promise<{ client: AcpClient; session: DeepCLISession } | null>
```

#### E4 — TUI 状态提示（依赖 Phase B）

Phase B 完成后，在 StatusLine 显示连接状态：
- 正常：不显示
- 重连中：`⟳ reconnecting… (attempt 2/10)`
- 断线放弃：`✗ kernel disconnected`

**Phase E DoD**：
- kernel 重启后 CLI 在 10 秒内自动重连并恢复输入，无需手动重启 CLI
- 重连失败超限后干净退出（exit 1），不 hang

---

## 文件清单（Phase A + B 完成后）

```
src/cli/
├── package.json
├── tsconfig.json
├── bin/
│   └── deepcli              # 入口脚本（#!/usr/bin/env bun）
├── active-port-manifest.json
├── scripts/
│   ├── check_active_port.ts
│   └── copy_oh_my_pi_file.ts
└── src/
    ├── main.ts
    ├── acp/
    │   └── client.ts        # WebSocket JSON-RPC client
    ├── session.ts           # session 操作封装
    ├── compat/              # oh-my-pi UI-facing shims
    ├── tui/
    │   └── index.ts         # thin facade / re-export to active-port/tui
    ├── active-port/
    │   ├── tui/             # mirrors packages/tui/src/**
    │   │   ├── tui.ts
    │   │   ├── terminal.ts
    │   │   └── components/
    │   └── coding-agent/    # mirrors packages/coding-agent/src/**
    │       ├── config/
    │       ├── modes/
    │       │   ├── interactive-mode.ts
    │       │   ├── components/
    │       │   ├── controllers/
    │       │   ├── theme/
    │       │   └── utils/
    │       └── tools/
    └── session/
        ├── agent-session-adapter.ts
        ├── events.ts
        └── history-storage.ts
```

---

## 已知风险

CLI Phase B 的风险不能只靠“缓解”。所有 Phase B 风险必须按
[`../cli/history/phase-b-tui-migration.md#风险根治方案`](../cli/history/phase-b-tui-migration.md#风险根治方案)
里的根治方案关闭：

- 禁止 active port 直接 import `@oh-my-pi/pi-natives`，由 DeepCLI compat 完整承接。
- `AcpClient` 必须是常驻 JSON-RPC protocol pump，permission request 不能被 prompt
  await 阻塞。
- `active-port/coding-agent/modes/interactive-mode.ts` 修改前必须完成 required-surface inventory，并用 facade/stub 截断
  Phase B 不做的外设能力。
- Component state 必须由 adapter/state builder 统一构造，不能散落 object literal。
- ACP update 到 oh-my-pi-like event 的转换只能发生在 `DeepCLIAgentSessionAdapter`。
- 不在 repo 内保存完整 oh-my-pi mirror；active port 只放登记过的当前阶段闭包。
- TUI 测试默认使用 `TestTerminal`；真实终端验证只能走带超时和 teardown 的
  pseudo-TTY probe。

---

## 参考文件索引

| 需要看 | 位置 |
|--------|------|
| ACP 事件完整定义 | `src/kernel/kernel/protocol/acp/event_mapper.py` |
| ACP routing 方法表 | `src/kernel/kernel/protocol/acp/routing.py` |
| Python ACP client 参考 | `src/probe/probe/client.py` |
| oh-my-pi TUI 源码 | `/home/saki/Documents/alex/oh-my-pi/packages/tui/src/` |
| oh-my-pi 组件参考 | `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/` |
| oh-my-pi interactive-mode | `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/interactive-mode.ts` |
