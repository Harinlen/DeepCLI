# Design Decisions

每条决策连带理由 —— agents 按此执行，除非用户明确推翻。

编号保留历史顺序；被 supersede 的条目已删除（见 git history）。

---

## D1 — Kernel-first, not CLI-first

FastAPI kernel 是产品本体，CLI / Web 都是瘦客户端。单用户、多并发
session（主聊天 + sub-agents + 独立 context）。跑在 Raspberry Pi
4/5 (4 GB RAM)：idle < 50 MB RSS、extensions 懒加载、冷启动 < 3 s。

> 连接认证见 D22。D1 不再声明 auth 形态。

## D2 — Python 3.12+, fully async

I/O 路径零 sync：FastAPI / LLM SDKs / `asyncio.subprocess` /
concurrent tool calls 都要求 async。

## D3 — Provider-agnostic engine

Engine 只认 DeepCLI 自己的 `Message` / `StreamEvent` /
`ToolDefinition`。Provider SDK 类型不得泄漏进 engine。

**Provider modularity**：
- 每家 vendor 一个 Provider 子类，vendor 怪癖（endpoint 可用性、
  context-window 探测、特殊参数）封在子类里。
- OpenAI-compat 翻译（`_to_openai_messages`、tool 转换、streaming
  解析）放 base/mixin (`providers/openai_base.py`)。
- **禁止硬编码 model 表**。context window 从 provider API 拉取，首次
  探测写回 `config.yaml`；用户显式 override 优先。

## D4 — Everything is a plugin

| Type | Location |
|---|---|
| Tool | `builtin/` or `~/.mustang/tools/` |
| Skill | `~/.mustang/skills/` (single `.md`) |
| MCP server | `~/.mustang/mcp.json` |
| Hook | `~/.mustang/hooks/` or inline in config |

## D5 — Permission policy pipeline

不是单一 mode flag，而是多层检查：

```
tool request → global rules → extension rules →
tool-level declaration (none/prompt/dangerous) →
mode check (prompt/auto/accept_edits/bypass) →
client callback (CLI prompt / Web dialog) → execute or deny
```

## D6 — Session key routing

格式 `{scope}:{owner}:{id}`：
- `main` — 默认 CLI chat
- `agent:explorer:task-42` — 独立 sub-agent
- `chat:project-a:conv-1` — 第二条独立对话

## D7 — Source vs runtime config

```python
class ProviderSourceConfig(BaseModel):    # user YAML — all Optional
    api_key: str | None = None

class ProviderRuntimeConfig(BaseModel):   # post-merge — all required
    api_key: str
```

Runtime config 永远完整；代码里不撒 `None` 检查。源自 OpenClaw。

## D8 — Pydantic v2 for all schemas

Tool input、config、API payloads、stream events 全走 Pydantic v2。
`.model_json_schema()` 直接产出给 LLM 的 tool definition。

## D10 — uv for package management

Fast、现代 lockfile、内置 venv。

## D11 — MCP as first-class extension protocol

Config 里声明的 MCP server 在 kernel 启动时自动连接，其 tools 透明
接入 tool registry。

## D12 — Skill = directory with SKILL.md

**Updated**: 对齐 Claude Code，改为目录格式 `skill-name/SKILL.md`（原 loose `.md` 废弃）。目录支持附带资源文件 + `${SKILL_DIR}` 替换。

YAML frontmatter + Markdown body + `$ARGUMENTS` / `${name}` 替换。Lazy-load：启动扫 frontmatter，调用时 load body。四层发现：project `.mustang/skills/` → user `~/.mustang/skills/` → bundled → MCP。运行时 dynamic discovery + conditional activation (paths glob)。

详见 `docs/kernel/subsystems/skills.md`。

## D14 — Sub-agents share the runtime

Parent / child agent 共用同一个 orchestrator query loop。Per-state
隔离策略（shared / cloned / isolated），不是全隔离也不是全共享。
详见 `docs/plans/roadmap.md` Phase 5.2。

## D15 — 4-layer context compaction

优先级：`autoCompact`（LLM summary）→ `microCompact`（tool-result
cache）→ `snipCompact`（truncation）→ `contextCollapse`（read-time
projection）。四层解决不同问题；compact 之后要重新注入关键文件
+ skill 指令，避免丢核心 context。

## D17 — Memory storage model

> **更新**：经过 13 项目竞品研究后重新设计。
> 详见 `docs/kernel/subsystems/memory/design.md`。

跨 project 长期记忆放 `~/.mustang/memory/`，按认知科学分类的
目录树结构（`profile/` / `semantic/` / `episodic/` /
`procedural/`）+ 顶层 `index.md`。

- **分类体系**（替代原 `user/feedback/project/reference`）：
  - `profile/` — 用户画像（身份、偏好、习惯）
  - `semantic/` — 语义知识（事实、概念、外部资源）
  - `episodic/` — 情景记忆（事件、决策、带时间戳）
  - `procedural/` — 程序性知识（流程、经验、编码模式）
- **Scope**：全局 `~/.mustang/memory/` + 项目级 `.mustang/memory/`
  （Phase 2）。
- **Shape**：每条 memory 是带 YAML frontmatter 的 markdown
  （`name` / `description` / `category` / `confidence` /
  `access_count` / `locked`）。`description` 是检索 scoring 的
  主要目标（对标 ReMe when_to_use、OpenViking L0）。
- **Index 是信号层**：按 category 分组的 pointer 列表。常驻
  system prompt（cacheable），上限 200 行。
- **专用 LLM**：memory 操作（scoring、提取、合并）使用
  `LLMManager.get_model("memory")`，允许配置为便宜模型。
- **log.md audit**：不变（同原版）。
- **MemoryStore 独占写**：不变（同原版）。
- **Tools**：5 个工具——`memory_write` / `memory_append` /
  `memory_delete` / `memory_list` / `memory_search`。
  `memory_delete` 需 `confirmation=True`。
- **Hygiene via prompt**：不变 + 新增 MetaMem 启发的记忆使用
  策略规则（200-500 token，嵌入 base prompt）。

## D18 — Prompt text lives in `.txt` files, not Python

所有 prompt 字符串与模板放在 `src/kernel/kernel/**/prompts/*.txt`
（例如 `orchestrator/prompts/base.txt`），Python 模块 import 时
`Path.read_text()` 加载。**`.py` 文件里不许写 prompt 文本**。

理由：干净的 diff、非 Python 编辑器可改 prompt、未来 hot-reload
trivial、prompt 内容与加载逻辑解耦。

详见 [`prompts.md`](prompts.md) § "Prompt Files" —— 文件索引
+ "Adding a new prompt" 配方。

## D20 — SQLite for session storage (kernel era)

`~/.mustang/sessions/sessions.db` 取代原 JSONL + `index.json` 双写
（早期决策，已删除）。

**动机**：两条独立 I/O 路径（append `*.jsonl` + rewrite
`index.json`）在 crash 时可能发散；token 计数器无法与 event 写入
原子更新。SQLite WAL 一个 `BEGIN … COMMIT` 同时覆盖 event insert
+ counter update。

**Schema**：两张表 —— `sessions`（每 session 一行，累计 token +
title）、`session_events`（append-only，按 `session_id ORDER BY
timestamp` 查询）。Sub-agent event 同表，`agent_depth > 0` 区分。

**Migrations**：`session/migrations.py` 用 `PRAGMA user_version`
追踪版本，`SessionStore.open()` 每次启动调用 `migrations.apply()`，
全自动无需用户介入。版本耦合见 D21。

**Tool-result spillover**：大 tool output 仍落到
`<session_id>/tool-results/<hash>.txt`，大 blob 不进 SQLite。

## D21 — Versioning convention

Product / Kernel semver is release-facing:

```
major   发布级兼容边界或根本性产品架构重构
minor   新 subsystem / 用户可见能力上线（Tools, Skills, MCP, Commands, …）
patch   bug fix / perf / 内部 refactor
```

SQLite / 持久化 schema version 是存储契约，必须单调递增，但**不再等于**
Kernel semver major。原因：产品还未发布时可以重置 release-facing 版本号
（例如回到 `1.0.0`），但已经写到本地 dev 数据库里的 `PRAGMA user_version`
不能倒退；否则旧 dev 数据会被当前 build 当成"未来版本"拒绝打开。

**`SCHEMA_VERSION`** 只在对应 SQLite schema shape 变化时递增；迁移列表
保持追加、升序、不可重排。

Pre-release exception：在没有客户数据兼容承诺前，可以把已经完成的迁移折叠回
当前 baseline。当前 session DB baseline 是 `SCHEMA_VERSION = 1`，已经包含
session archive / title-source metadata。已有本地 dev DB 如果曾经被 stamp 成
`user_version = 2`，需要删掉并重建。

**`KERNEL_VERSION`**（每条持久化 event 都带）在 import 时从
`kernel.__version__` 推导，单一 source of truth。

History：`0.x.x` = JSONL 前身（无版本契约，已归档）；session DB
`SCHEMA_VERSION = 1` = 当前未发布 baseline。

## D22 — AuthN / AuthZ split into two subsystems

Kernel 有两个独立"是否放行"决策点，各由独立 subsystem 承担：

| 子系统 | 触发时机 | 职责 |
|---|---|---|
| **`ConnectionAuthenticator`** | WS accept 后、协议层前 | 连接接入认证（AuthN）—— token / password → `AuthContext` |
| **`ToolAuthorizer`** | orchestrator 调用 tool 前 | 工具授权（AuthZ）—— layered rules + session grants + bash classifier → `PermissionDecision` |

**为什么是两个独立 subsystem 而不是 Manager / Orchestrator 内部**：
规则来自 Config 不来自 Tool；所有工具共享同一套 allow/deny/ask 语法；
未来可能接 LDAP / audit / 企业 IAM；`permission_denied` 是 hook 事件
需要明确 fire 点；独立 unit test 比塞进 orchestrator 容易。

**命名原因**："Manager" 在 kernel 里暗示"管集合"（FlagManager 管
flag sections），但认证是动作不是集合 —— 用动词化
`*Authenticator` / `*Authorizer`。`Connection` 前缀把 scope 钉死，
避免被未来的 `CredentialStore`（provider / MCP API key）或 MCP OAuth
稀释。两者词根对齐、grep 友好、对应业界 AuthN / AuthZ 标准术语。

**ToolAuthorizer 内部组件**（不单独成 subsystem）：`RuleStore`
（分层规则，订阅 ConfigManager）+ `RuleEngine`（纯函数遍历 +
优先级仲裁）+ `SessionGrantCache`（allow_always 缓存）+
`BashClassifier`（argv 解析 + 安全/危险清单）。

**文档**：
- [kernel/subsystems/connection_authenticator.md](../kernel/subsystems/connection_authenticator.md) —— 已写
- `kernel/subsystems/tool_authorizer.md` —— 待设计（Rule 数据模型、
  RuleStore 订阅机制、grant 生命周期未定）

**代码状态**：`src/kernel/kernel/auth/` 已于 2026-04-16 从
`AuthManager` 重命名为 `ConnectionAuthenticator`，模块文件
`manager.py` → `connection_authenticator.py`；目录名紧接着
于 2026-04-16 rename 为 `connection_auth/`，与架构文档中
`tool_authz/` 的并列布局一致。

---

## D23 — orjson for all JSON serialization

**Supersedes** D-deferred-1（原先因 32-bit armv7 无 wheel 而推迟）。
32-bit 目标已不再是约束，全面启用 `orjson >= 3.10`。

**规则**：kernel 代码 **禁止** `import json`（stdlib），一律用
`import orjson`。

**API 差异备忘**：
- `orjson.dumps()` 返回 `bytes`，需要 `str` 时加 `.decode()`
- 写文件优先用 `path.write_bytes(orjson.dumps(...))`
- `indent` → `option=orjson.OPT_INDENT_2`
- `sort_keys` → `option=orjson.OPT_SORT_KEYS`
- `orjson.JSONDecodeError` 继承自 `json.JSONDecodeError`
- Pydantic v2 装了 orjson 后 `.model_dump_json()` 自动用它

---

## Deferred decisions

（无待定决策）
