# Kernel — Overview

> **Slogan**: _The agent that reinvents software._ 🐎

## DeepCLI 的定位：Personalize Dynamic Software

DeepCLI **就是**一套 **Personalize Dynamic Software（PDS）**——下
一代软件本身。主 UX 是一个跟 **主 Agent** 的聊天对话（体感接近
Claude Code CLI），**真正目的是帮用户逐步 build 出属于自己的一整
套专属软件**，这些软件跑在同一个 kernel 上，未来通过一个**独立的
Home Screen 启动台前端 repo**（类 iOS 主屏 / macOS Shortcuts 风
格）统一浏览 / 启动 / 挂 widget / 分享。

DeepCLI 走**长期协同构建**的路线——用户通过 chat 带着 agent 一起
建，建出来的东西是 durable 的、可复用的、可分享的，随着使用不断
累积和演化，不是每轮丢弃重生的一次性 UI。

### 分层（三个 repo / 三层结构）

| 层 | 是什么 | 在哪儿 |
|---|---|---|
| **Home Screen** | 统一入口。浏览 / 启动 / widget / 分享用户 build 出来的软件，同时能观察 kernel 里正在跑的 agent / session 状态。 | *未来独立前端 repo，尚未启动* |
| **Multi-agent Kernel** | 运行时引擎。**一个主 agent** 常驻跟用户对话；**session agent** 各自在自己 session 里跑（OpenClaw 风格，不是 CC 那种 sub-agent-as-tool）。通过 memory / skills / hooks 自我进化。 | *本 repo* |
| **用户软件库** | 产品本体。用户累积出来的那堆专属软件，下面说。 | *用户的 DeepCLI 数据目录* |

### 用户 build 出来的软件的三种形态

1. **Plugin** —— 一个原子贡献：一个 skill、一个 UI template、一个
   tool、一个 MCP server。注册进 kernel registry，agent 或其他软件
   可以调用。
2. **Template-App** —— UI template + config + 少量胶水代码。一个
   成品小应用，**没有自己的 agent loop**，运行起来像 widget（例：
   定制化的 TradingView + 特定代币配置）。
3. **Session Agent** —— 一套 agent 设定（自带 skill / tool / prompt /
   memory scope），在自己的 session 里长期跑；用户可以随时打开对话
   跟它聊（例："Research Assistant"、"Email Triage"、某个 repo
   scope 的 pair-programmer）。

三种形态都会以图标 / widget 的形式挂到 Home Screen 上，支持一键分享，
用户也可以继续通过跟主 agent 聊天来演化它们。

### Kernel 的出处

Kernel 本身完全重写了原有 codebase（旧代码已归档至
`archive/daemon/`），是一个**模块化、可自我进化的 AI agent 引擎**，
融合了三个参考项目：

- **Claude Code** 的 harness —— agent loop、tool use、memory、
  compaction、skills、plan mode
- **OpenClaw** 的开放式架构 + 多 agent 模型 —— kernel/client 分离、
  plugin 系统、policy pipeline、**session-per-agent**（DeepCLI 的
  多 agent 模型沿用此模型，而非 Claude Code 的 sub-agent-as-tool）
- **Hermes Agent** 的 Python 实现 —— ACP adapter、多平台 gateway、
  SQLite session store、prompt caching

三个参考项目合起来覆盖的是 **Kernel 层**的工程；Home Screen 和
用户软件库（三种形态、长期累积、一键分享）这些 PDS 层的东西是
DeepCLI 自己的设计，没有直接可抄的对标物。

Kernel 通过 WebSocket（ACP 协议）为所有前端（未来的 Home Screen、
IDE 扩展、terminal probe、messaging gateway 等）提供服务 —— 所有
前端都是薄的 ACP 客户端，kernel 是唯一真理源。

## 目标

### 1. 支撑 PDS：帮用户 build 自己的软件库

Kernel 层的首要职责不是"做好某一类 workload"，而是**让用户能通过
跟 agent 的对话，逐步 build 出属于自己的那一整套软件**。Skills
（单文件 markdown，lazy-load）、Hooks（事件驱动拦截）、Memory
（全局 + 项目，由 agent 自己维护）是 agent **协助构建**的底座 ——
每一次会话都可以帮用户留下一个新的 plugin / template-app /
session-agent，下一次以后所有前端都能直接访问到它。

支持任意 workload（coding、analysis、planning、messaging gateway 等）
是这个目标的自然结果 —— 通过用户自己 build 出来的 tools / skills /
MCP / hooks / session agent 扩展，而不是内建特定场景。

### 2. 高度模块化

每一个子系统都是独立模块，可以被随时：

- **禁用** — 配置一个开关即可关闭，不影响其他模块
- **替换** — 实现相同接口的任何实现都能即插即用
- **扔掉** — 删除模块目录，不产生级联破坏

模块之间通过明确定义的接口（Protocol / ABC）通信，
不允许跨模块直接 import 内部实现。

### 3. 自我进化（Self-evolution）

Agent 不是每次冷启动，而是跨 session 积累用户 / 项目知识：

- **Memory** — 跨 session 的长期记忆（`~/.mustang/memory/`，D17）
- **Skills** — 用户可沉淀的可复用技能（单文件 markdown，D12）
- **Hooks** — 事件驱动的自定义行为（`session:start`、
  `tool:before_call` 等）
- **Project context** — AGENTS.md + 每个项目的 memory 层

这几个子系统协同，让 Agent 每次运行都"更懂"用户与项目，
而不是停留在同一起点。

### 4. 多模型 Benchmarking

Provider-agnostic engine 不只是图方便 —— 这是 Kernel 的**核心用途**
之一。同一个 Agent、同一份 workload、切换底层 LLM（Anthropic /
OpenAI / local Qwen / …），可以做端到端性能对比：

- 相同 tool schema、相同 prompt、相同 compaction 策略
- 通过 `LLMManager` 切换 model alias，零代码改动
- Session event log（SQLite，D20）记录完整 turn + token 消耗，
  是直接可用的评测数据源

这使 Kernel 天然就是一个"同条件对照"的 LLM 评测平台。

### 5. 功能覆盖（参考项目对齐）

Kernel 需要覆盖的能力集合来自三个参考项目：

#### Claude Code — Harness 特性

- 工具系统（bash、file ops、glob、grep、agent 等）
- Prompt 工程（system prompt 构建、环境注入、identity）
- 权限系统（rule-based、per-tool、always allow / deny / prompt）
- Plan mode（只读工具 + plan 文件）
- Memory（全局 + 项目级、自动提取、相关性过滤）
- Skills（YAML frontmatter、lazy load、prompt 注入）
- Hooks（9 种事件点、可拦截 / 改写）
- Session 管理（resume、cleanup）
- Context compaction（auto + manual、reactive）
- 多 provider（Anthropic、OpenAI、Bedrock、…）
- MCP（stdio / SSE、proxy tools、health monitor）
- Agent / sub-agent（嵌套、depth 控制）
- TodoWrite、background tasks
- 流式响应（text / thinking / tool events）

#### OpenClaw — 架构特性

- Daemon/client 分离
- Plugin 发现、加载、隔离、热重载
- Policy pipeline（可组合的权限策略链）
- 多 session 并发与隔离

#### Hermes Agent — Python 实现特性

- ACP adapter（IDE 接入，Zed / VS Code / JetBrains）
- 多平台 gateway（Discord、Telegram、Slack、WhatsApp、Feishu 等）
- SQLite + FTS5 session store
- Slash command 中央注册表（CLI / gateway / ACP 共用）
- 平台级 skill / tool enable-disable 配置

### 6. 技术栈

- **语言**: Python 3.12+（D2）
- **框架**: FastAPI + uvicorn
- **数据验证**: Pydantic v2（D8）
- **传输**: WebSocket + ACP（见 `interfaces/protocol.md`）
- **持久化**: SQLite WAL（D20），大 blob 溢写到 sidecar 文件
- **配置**: YAML + JSON，layered（default → user → project，D7）
- **包管理**: uv（D10）

---

## 设计原则

1. **接口先行** — 每个模块先定义 Protocol，再写实现
2. **依赖反转** — 高层模块不依赖低层实现，都依赖抽象
3. **配置驱动** — 功能的启用/禁用通过配置，不通过改代码
4. **零循环依赖** — 模块间依赖关系是 DAG，不允许环
5. **可测试** — 每个模块可以独立测试，不需要启动整个 kernel

---

## 下一步读什么

- [architecture.md](architecture.md) —— 子系统拓扑、生命周期、
  Subsystem 基类、WebSocket 接入的传输 / 协议 / 会话三层分工
- [subsystems/](subsystems/) —— 每个子系统的设计细节
