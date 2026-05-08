# CLI Client — Design Doc

CLI 客户端是 DeepCLI 的**第一个面向用户的前端**。
它是一个 thin ACP client，所有 agent 逻辑都在 kernel 里运行；
CLI 只负责 TUI 渲染和用户输入，通过 ACP/WebSocket 与 kernel 通信。

**硬边界**：CLI 与 kernel 的唯一运行时耦合是 WebSocket 上的 ACP/JSON-RPC。
CLI 不允许 import Python kernel 模块、不允许调用 kernel 子系统对象、不允许直接读写
kernel SQLite / state / sidecar 文件，也不允许通过本地文件约定绕过协议共享状态。
如果 CLI 需要新的 kernel 能力，先在 kernel 增加 ACP request / notification，再由
CLI 通过 WebSocket 消费。

---

## 定位

```
用户
 │
 │ stdin / stdout
 ▼
┌─────────────────────────────────┐
│  CLI 客户端（TypeScript/Bun）    │
│  - TUI 渲染（oh-my-pi 移植）    │
│  - ACP client 连接              │
│  - 用户输入 → ACP event         │
│  - ACP event → TUI 组件更新     │
└────────────┬────────────────────┘
             │ ACP WebSocket
             ▼
┌─────────────────────────────────┐
│  DeepCLI Kernel（Python）        │
│  - SessionManager               │
│  - Orchestrator / agent loop    │
│  - ToolManager / MCPManager…    │
└─────────────────────────────────┘
```

与 oh-my-pi 最大的区别：oh-my-pi 把 agent loop 内嵌在同一进程；
DeepCLI CLI 是纯 UI 层，不运行任何 LLM 调用。

---

## 技术栈

| 层 | 选择 | 原因 |
|----|------|------|
| 运行时 | Bun | 与 oh-my-pi 相同，TypeScript 原生，无需构建步骤 |
| 语言 | TypeScript | 与 oh-my-pi TUI 库类型兼容 |
| TUI 库 | `@oh-my-pi/pi-tui`（直接引用本地路径） | 移植源，无需重写 |
| ACP 客户端 | `@agentclientprotocol/sdk` | oh-my-pi 已验证用法（`acp-mode.ts`） |
| 包管理 | bun workspaces | 与 oh-my-pi 同方案 |

产品发布时，用户不需要安装 Bun。Linux v1 的 CLI 发布为 launcher 调用的
bundled artifact：

```text
~/.local/share/deepcli/cli/<version>/deepcli-cli
```

优先尝试 Bun single executable；如果 TUI、资源文件或动态 import 在 single
executable 中不稳定，则 fallback 为 bundled Bun runtime + JS bundle：

```text
~/.local/share/deepcli/cli/<version>/
├── bun
├── dist/main.js
└── assets/
```

TypeScript CLI 的源码仍使用 Bun 开发和测试；产品入口 `deepcli` 是 native
launcher，不是直接暴露给用户的 Bun script。

---

## 仓库位置

```
deepcli/
├── src/kernel/          ← Python kernel（现有）
├── src/probe/           ← ACP 测试客户端（现有）
└── src/cli/             ← CLI 客户端（新建）
    ├── src/
    │   ├── main.ts             入口：解析 argv，启动 InteractiveMode 或 print
    │   ├── acp/
    │   │   └── client.ts       WebSocket JSON-RPC client（request / notify / subscribe）
    │   ├── session.ts          session 操作封装（new/load/prompt/cancel）
    │   ├── tui/                vendored from @oh-my-pi/pi-tui（直接复制源码）
    │   ├── modes/
    │   │   ├── interactive.ts  主 TUI 循环
    │   │   └── print.ts        非交互输出模式
    │   └── components/         TUI 组件（移植自 oh-my-pi）
    │       ├── assistant-message.ts
    │       ├── tool-execution.ts
    │       ├── status-line.ts
    │       ├── welcome.ts
    │       └── …
    ├── package.json
    └── tsconfig.json
```

---

## 移植范围（来自 oh-my-pi）

### 直接复用（依赖引入，不修改）

| 包 | 用途 |
|----|------|
| `@oh-my-pi/pi-tui` | TUI 框架：`TUI`, `Component`, `Editor`, `Text`, `Markdown`, `Loader`, `ProcessTerminal`... |
| `@oh-my-pi/pi-natives` | 终端能力检测（Kitty 图片协议等） |
| `@oh-my-pi/pi-utils` | 工具函数（`visibleWidth`、logger 等） |

### 移植并适配（需修改 agent 相关部分）

| 源文件（oh-my-pi） | 目标文件（DeepCLI CLI） | 主要改动 |
|--------------------|------------------------|----------|
| `modes/interactive-mode.ts` | `modes/interactive-mode.ts` | 移除 `AgentSession` 依赖；改为从 ACP client 接收 events |
| `modes/components/assistant-message.ts` | `components/assistant-message.ts` | 渲染逻辑不变；数据来源改为 ACP event |
| `modes/components/tool-execution.ts` | `components/tool-execution.ts` | 移除工具调用实现；改为展示 kernel 发来的 tool call 事件 |
| `modes/components/status-line.ts` | `components/status-line.ts` | 基本不变；model/context 信息来自 ACP；token/cost 明细留给 `/cost` |
| `modes/components/welcome.ts` | `components/welcome.ts` | 去掉 LSP 信息；加 kernel 版本 + session ID |
| `modes/controllers/input-controller.ts` | — | 保留键盘处理逻辑；submit 改为发 ACP user_message event |
| `modes/controllers/command-controller.ts` | — | 斜杠命令：本地命令（`/help`、`/quit`）直接处理；其余透传 kernel |

### 不移植（kernel 已有对应能力）

- `sdk/`、`session/`、`agent-loop.ts`：kernel 的 SessionManager + Orchestrator 已覆盖
- `mcp/`、`tools/`：kernel ToolManager + MCPManager 已覆盖
- `config/model-resolver.ts`：kernel LLMManager 已覆盖
- `plan-mode/`：kernel 已有 plan mode，CLI 只需展示 ACP 事件
- `memories/`：kernel MemoryManager 已覆盖
- `commit`、`grep`、`jupyter`、`ssh` 等子命令：DeepCLI CLI 初期不需要

---

## ACP 连接模型

```
CLI 启动
  │
  ├─ 从 native launcher 注入的环境变量读取 kernel URL + auth token
  │
  ├─ WebSocket 连接: KERNEL_URL
  │
  ├─ 发送 initialize（client info）
  │
  └─ 进入事件循环
       ├─ 用户输入 → send user_message ACP event
       ├─ 收到 assistant_message event → AssistantMessageComponent.append()
       ├─ 收到 tool_call event → ToolExecutionComponent.show()
       ├─ 收到 tool_result event → ToolExecutionComponent.update()
       ├─ 收到 status event → StatusLineComponent.update()
       └─ 收到 session_end event → 退出
```

Launcher 交接环境变量：

```text
DEEPCLI_TOKEN=<token>
KERNEL_URL=ws://127.0.0.1:<port>/session
KERNEL_HEALTH_URL=http://127.0.0.1:<port>/
```

开发期可保留旧环境变量 fallback（例如 `MUSTANG_TOKEN`、`KERNEL_PORT`），但产品
路径以 `DEEPCLI_TOKEN`、`KERNEL_URL`、`KERNEL_HEALTH_URL` 为准。

### Kernel 启动边界

CLI 不负责本地 Kernel 自启动。Kernel/Supervisor 的单例、端口选择、后台进程、
token 文件读取都归 native launcher。CLI 只消费 launcher 提供的连接信息。

---

## 启动参数（初版）

```
deepcli [messages...]           # 交互模式（默认）
deepcli -p "..."                # print 模式，非交互，输出后退出
deepcli --kernel ws://host:port # 连接远程 kernel
deepcli --session <id>          # 恢复指定 session
```

用户可见的 `deepcli` 命令由 launcher 提供；上面这些参数由 launcher 原样转交给
CLI artifact，或者由 launcher 先消费属于 runtime 管理的子命令（如
`deepcli status`、`deepcli stop`）。

---

## 关键设计决策

### D1 — TUI 库选 oh-my-pi，不重写

oh-my-pi `@oh-my-pi/pi-tui` 已有差分渲染、同步输出、完整组件库，
经过大量生产验证。重新实现收益极低、成本极高。

### D2 — CLI 是 thin client，不嵌 agent loop

DeepCLI 的卖点是 kernel 可插拔前端（CLI、Web、IDE）。
如果 CLI 嵌 agent loop，多前端共享状态就无从实现。
所有业务逻辑只在 kernel 里存在一份。

### D3 — ACP 是唯一传输协议

CLI 不走 HTTP REST，只走 ACP WebSocket（与 kernel 现有 `/session` 端点对接）。
好处：与 IDE 插件、Web 前端共用同一个协议，不需要额外 API。

这条同时意味着：即使 CLI 和 kernel 在同一台机器、同一个 monorepo、同一个用户目录下，
CLI 也不能直接访问 kernel 的 Python API、SQLite 数据库或 state 文件。所有 session、
memory、secret、tool、MCP、schedule、git/worktree 等 kernel 状态只能通过 ACP 表面读取
或修改。

### D4 — 组件保持 oh-my-pi 接口兼容

`Component` interface（`render(width): string[]` + `handleInput?` + `invalidate`）
原样保留，便于日后直接升级 oh-my-pi 版本。

### D5 — CLI Artifact 是前台 UI，不是进程管理器

发布产物中的 CLI artifact 只负责 ACP 连接和 UI。它不做：

- Kernel/Supervisor 启动、停止、重启；
- runtime 单例锁；
- 端口选择；
- token 文件读取；
- Kernel state / SQLite / sidecar 读取；
- 自行寻找其它 Kernel endpoint。

CLI 断线时只能重连当前 `KERNEL_URL`。如果 runtime 不存在或不 ready，应由 launcher
在启动 CLI 前处理。

---

## 实现阶段

### Phase A — 骨架 + ACP 连接（P0）

- `packages/cli/` 目录结构 + `package.json` + `tsconfig.json`
- `connection/acp-client.ts`：WebSocket 连接、重连、事件流
- `main.ts`：argv 解析、InteractiveMode / print mode 入口
- env loading：支持 `DEEPCLI_TOKEN`、`KERNEL_URL`、`KERNEL_HEALTH_URL`
- 最小 InteractiveMode：Editor 输入 → ACP user_message，收 assistant_message → Text 渲染
- 目标：`bun run deepcli` 能跑起来，能和 kernel 收发消息

### Phase B — 完整 TUI 组件（P1）

移植 oh-my-pi 全套组件：
- `ToolExecutionComponent`（工具调用展示，含 approve/deny 按钮）
- `StatusLineComponent`（model / token / session 状态）
- `WelcomeComponent`（首屏）
- `AssistantMessageComponent`（markdown 渲染、thinking 折叠）
- 斜杠命令 autocomplete
- 键盘快捷键（`Ctrl+C` 打断、`Ctrl+L` 清屏等）

### Phase C — 工具授权交互（P1）

kernel ToolAuthorizer 需要 client 审批（`auto: false` 工具），
CLI 展示 approve/deny 对话框，结果回传 ACP。
这是 Phase B 之后才需要的，因为 Phase A/B 可以先用 `auto: true` 跑通。

### Phase D — session 管理 + 本地配置（P2）

- session 选择器（列出历史 session，可恢复）
- `~/.deepcli/config/client.yaml` 配置（主题、session startup、UI 偏好；kernel
  URL/token 在产品路径由 launcher 环境变量提供）
- 主题支持（移植 oh-my-pi theme 系统）

---

## 前置依赖（kernel 侧）

| 依赖 | 状态 |
|------|------|
| ACP WebSocket transport（`/session` 端点） | ✅ 已有（Phase 6） |
| SessionManager ACP event 广播 | ✅ 已有（Phase 8） |
| ToolAuthorizer client-approval ACP event | ⬜ 待确认（Phase C 前需要） |
| `user_message` ACP event 类型 | 需确认 kernel 协议层是否已有 |
