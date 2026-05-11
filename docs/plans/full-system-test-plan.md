# 全系统测试计划

状态: Phase 3 CLI verification completed for current deterministic smoke + existing CLI suites
创建: 2026-05-01
范围: `src/kernel/`、`src/probe/`、`src/cli/`

## 目标

这份计划只回答一件事：怎样确认 DeepCLI 的每个功能真的能用。

测试分三层推进，顺序不能反过来：

1. **Kernel 单元测试**：先针对 Kernel 每个组件补单元测试，提高覆盖率，证明模块自己的公开接口、边界条件和错误路径正确。
2. **Probe 功能验证**：再通过 Probe 走真实 Kernel/Supervisor/ACP 路径，验证每个组件的功能在真实系统里能跑通。
3. **CLI 界面响应验证**：最后通过 CLI/PTY 测试确认每个功能都有对应的用户界面响应，不能只证明 Kernel 后台做了事。

这三层各自回答不同问题：

| 层级 | 回答的问题 | 不回答的问题 |
|---|---|---|
| Kernel 单元测试 | 模块逻辑是否正确，边界是否守住，错误是否清晰 | 真实进程链路是否工作，CLI 是否显示 |
| Probe 功能验证 | 功能是否通过真实 ACP/Supervisor 路径端到端可用 | TUI 是否把事件渲染给用户 |
| CLI 界面验证 | 用户是否能看到、选择、操作、恢复 | Kernel 内部覆盖率是否足够 |

## 背景

2026-05-01 的 Agent Control Plane router backend 曾暴露两个问题：

1. Hub 到 Primary Runtime 的 `agent.prompt` 被 5 秒内部 WebSocket 超时截断，真实 LLM/tool turn 变成 `[-32603] Internal error`。
2. 当时 Primary Runtime 发起的 `session/request_permission` 没有桥回 Access Agent/CLI，权限请求在 Runtime 内 fail-closed，CLI 完全没有机会显示询问。

这两个问题已经在当前单 Primary Agent Control Plane 主路径中补齐，但它们说明了一个原则：`ping -> pong` smoke test 不足以证明功能正常。以后每个功能都必须同时有 Kernel 单测、Probe 功能验证、CLI 界面响应验证。

## 总体执行顺序

每个功能按下面顺序推进：

```text
1. Kernel unit tests
   -> 2. Probe functional tests
      -> 3. CLI / PTY UI tests
         -> 4. progress.md 记录实际命令和 Probe 输出摘要
```

不能用后面的测试替代前面的测试：

- Probe 通过，不代表 Kernel 单元覆盖够。
- CLI 看到输出，不代表每个错误路径被测过。
- 单元测试通过，不代表真实 Supervisor/ACP 路径可用。

## Phase 1: Kernel 单元测试

状态：已完成 owned Phase 1 单元测试覆盖目标；剩余行覆盖缺口已归属到 platform / MCP transport / memory / scheduler / external integration 后续任务。

细化任务见 [`kernel-unit-test-phase1.md`](../kernel/testing/history/kernel-unit-test-phase1.md)。

目标：针对 Kernel 的每一个组件补齐单元测试，提高覆盖率，并保证每个模块的公共接口、边界条件、错误路径、返回结构都有断言。

执行范围：

- 只测 `src/kernel/kernel/` 内部模块。
- 尽量使用真实轻量依赖，例如临时 SQLite、临时目录、fixture config。
- 可以 mock 外部网络/LLM，但不能把被测模块本身 mock 掉。
- 每个新增模块必须有对应测试文件。
- 每个 public function/class method 的返回结构必须断言关键字段。

### Kernel 组件单测矩阵

| 组件 | 测试目录/文件 | 必测内容 |
|---|---|---|
| `flags` | `tests/kernel/flags/` | section 注册、重复注册、默认值、配置覆盖、坏 schema、启动失败 |
| `config` | `tests/kernel/config/` | 全局/项目配置合并、secret resolver、坏 YAML、owner/schema mismatch、signal |
| `secrets` | `tests/kernel/secrets/` | SQLite 存取、缺失 secret、`${secret:name}` 展开、MCP OAuth token |
| `prompts` | `tests/kernel/prompts/` | 默认 prompt、用户 override、项目 override、缺失文件、加载顺序 |
| `connection_auth` | `tests/kernel/connection_auth/` | token、password、local trust、无效 credential、AuthContext 字段 |
| `tool_authz` | `tests/kernel/tool_authz/` | rule parser、Bash classifier、allow/reject/ask、session grant、优先级 |
| `llm_provider` | `tests/kernel/llm_provider/` | provider 创建/删除、重复 provider、流式错误、lifecycle |
| `llm` | `tests/kernel/llm/` | model profile CRUD、role routing、`current_used`、未知 provider/model |
| `mcp` | `tests/kernel/mcp/` | stdio/http transport、handshake、tool/resource 同步、断线重连、失败降级 |
| `tools` | `tests/kernel/tools/` | 每个 builtin tool 的输入校验、权限需求、事件输出、失败消息 |
| `skills` | `tests/kernel/skills/` | manifest 校验、发现、lazy load、条件激活、SkillTool 参数和错误 |
| `hooks` | `tests/kernel/hooks/` | 注册、fire、失败隔离、异步任务清理、system reminder drain |
| `memory` | `tests/kernel/memory/` | global/project scope、读写删、索引、坏文件、后台刷新 |
| `session` | `tests/kernel/session/` | new/load/resume/list/prompt/cancel/close、队列、持久化、`clientTurnId` |
| `orchestrator` | `tests/kernel/orchestrator/` | LLM/tool loop、tool error、permission result、compaction、plan mode、cancel |
| `protocol` | `tests/kernel/protocol/` | ACP codec、schema alias、event mapper、outbound request/response、cancel |
| `agent_hub` | `tests/kernel/agent_hub/` | registration、routing、runtime record、错误传播、unknown target、long prompt |
| `agent_runtime` | `tests/kernel/agent_runtime/` | websocket contract、session service、notify/request bridge、shutdown |
| `access_agent` | `tests/kernel/access_agent/` | readiness、auth、router backend 转发、错误映射、session lifecycle |
| `supervisor` | `tests/kernel/supervisor/` | 启动顺序、runtime files、token 下发、shutdown cleanup、child exit |
| `gateways` | `tests/kernel/gateways/` | adapter lifecycle、平台入站、reply sink、router mode、失败隔离 |
| `schedule` | `tests/kernel/schedule/` | cron parser、store、scheduler、executor、delivery route |
| `git` | `tests/kernel/git/` | worktree 检测、工具注册、context injection、无 git 降级 |

### Kernel 单测验收命令

先按组件补测，最后跑完整 Kernel 单测：

```bash
cd src/kernel
uv run pytest ../../tests/kernel -q
uv run pytest --cov=kernel ../../tests/kernel
uv run ruff check kernel ../../tests/kernel
uv run mypy kernel
```

覆盖率目标分阶段推进：

| 阶段 | 目标 |
|---|---|
| 第一轮 | 每个组件有对应测试文件，关键 public API 有 happy/error path |
| 第二轮 | 每个组件覆盖边界条件、坏输入、失败依赖、并发/取消路径 |
| 第三轮 | 覆盖率缺口逐项收敛，新增功能不得降低目标覆盖率 |

## Phase 2: Probe 功能验证

状态：已完成确定性 Probe 覆盖强化；外部网络和平台适配器扩展见本节末尾“仍需独立套件覆盖的范围”。

目标：Kernel 单元测试通过后，用 Probe 验证每个组件在真实系统路径里是否能正常工作。Probe 必须走真实 WebSocket ACP/Supervisor 路径，不能直接 import Kernel 内部模块。

真实路径：

```text
Probe
  -> Access Agent WebSocket /session
  -> Agent Hub Router
  -> Primary Agent Runtime
  -> SessionManager
  -> Orchestrator
  -> Tools / MCP / Memory / Skills / Hooks / LLM
```

Probe 验证的是“功能真的能用”，不是 UI。

### Probe 能力要求

Probe 必须支持：

- `initialize`
- `session/new`
- `session/resume`
- `session/prompt`
- `session/cancel`
- `session/close`
- 固定 `clientTurnId`
- 自动处理 `session/request_permission`
- 权限策略：`allow_once`、`allow_always`、`reject`
- 收集 `session/update`
- 收集 tool calls / tool results
- 收集 runtime/client request 错误
- JSON 输出，供 completion report 粘贴

建议 Probe JSON 输出：

```json
{
  "ok": true,
  "sessionId": "uuid",
  "promptCompleted": true,
  "stopReason": "end_turn",
  "permissionRequests": [
    {"tool": "WebFetch", "decision": "allow_once"}
  ],
  "toolCalls": ["WebSearch", "WebFetch"],
  "text": "final answer",
  "errors": []
}
```

### Probe 功能验证矩阵

| 功能区域 | Probe 场景 | 验收 |
|---|---|---|
| Supervisor | 启动 Hub -> Access -> Primary Runtime | readiness 从 starting 到 `default_route_ready=true` |
| Auth/transport | token/password/local 连接 | 有效 credential 成功，无效 credential 拒绝 |
| Session lifecycle | new/resume/load/close/list | session 可创建、恢复、关闭，resume 不 replay |
| Prompt | 中文/英文自然语言 prompt | 返回 `stopReason=end_turn`，文本非空，超过 5 秒不超时 |
| `clientTurnId` | 同 ID 重试 completed turn | 不创建第二条 user message，返回 replayed result |
| Queue/status | 并发 prompt / queued prompt | queue 顺序正确，status projection 正确 |
| Cancel | 长 turn cancel 后再 prompt | cancel 生效，下一轮可继续 |
| Permission | 触发权限工具，Probe allow | Probe 收到 permission request，allow 后工具执行 |
| Permission reject | 触发权限工具，Probe reject | 模型收到拒绝结果，turn 正常结束 |
| WebSearch | prompt 明确要求搜索 | tool call 出现，结果进入回答 |
| WebFetch | prompt 明确要求 fetch URL | permission + fetch + result 都可观察 |
| FileRead | 读取 fixture 文件 | tool call 出现，回答包含文件内容 |
| FileWrite/Edit | 写临时目录文件 | permission 出现，文件内容符合预期 |
| Bash/Shell | 运行安全命令 | permission/deny 策略正确，stdout 可观察 |
| Todo | 创建/更新 todo | todo updates 可观察 |
| SendMessage | `agent:<id>` route / 兼容 session route | Router 解析正确，错误 target 有明确错误 |
| MCP | 本地 fixture MCP tool/resource | tool/resource 可用，远端失败可降级 |
| Skills | 技能发现/调用 | skill listing 或 skill tool 行为可观察 |
| Hooks | hook 成功/失败 | 成功结果可注入，失败不破坏 turn |
| Memory | 写入/读取 memory | memory tool 或注入路径可观察 |
| Gateway/Platform | 模拟平台 inbound message | 经 Hub prompt Primary，reply sink 收到回复 |
| Error propagation | route missing/runtime error/bad params | 错误类型和 message 不被吞成裸 `Internal error` |

### Probe E2E 文件建议

| 文件 | 覆盖 |
|---|---|
| `tests/e2e/test_supervisor_boot_e2e.py` | Supervisor 启动和 readiness |
| `tests/e2e/test_probe_session_lifecycle_e2e.py` | session lifecycle |
| `tests/e2e/test_probe_router_prompt_e2e.py` | prompt、长 turn、中文输入 |
| `tests/e2e/test_probe_permission_e2e.py` | permission allow/reject |
| `tests/e2e/test_probe_tools_e2e.py` | WebSearch/WebFetch/File/Bash/Todo/SendMessage |
| `tests/e2e/test_probe_client_turn_id_e2e.py` | duplicate `clientTurnId` replay |
| `tests/e2e/test_probe_mcp_e2e.py` | MCP fixture 和失败降级 |
| `tests/e2e/test_probe_gateway_e2e.py` | Platform Adapter router path |
| `tests/e2e/test_probe_error_e2e.py` | 错误传播 |

### Probe 验收命令

```bash
scripts/run-kernel.sh --access-port 8361 --dev

cd src/probe
uv run python -m probe --port 8361 --test --prompt "Reply with exactly: pong"
uv run python -m probe --port 8361 --test --client-turn-id 00000000-0000-4000-8000-000000000001 --prompt "Reply with exactly: pong"
uv run pytest ../../tests/e2e/test_probe_router_prompt_e2e.py ../../tests/e2e/test_probe_permission_e2e.py -q -m e2e
```

Probe 验收标准：

- 不出现 `[-32603] Internal error`，除非测试正在验证受控错误路径。
- Kernel 日志不能出现 `runtime client request not available`。
- 权限 allow 后工具必须执行。
- 权限 reject 后 turn 必须正常结束。
- `clientTurnId` duplicate retry 不重复执行。
- completion report 必须粘贴 Probe JSON 输出摘要。

### Phase 2 覆盖强化记录 — 2026-05-01

本轮已把核心 Probe 验证固化为 `tests/e2e/test_probe_phase2_e2e.py`，并使用本地
OpenAI-compatible 假模型服务保证不依赖外部 API key。测试路径为真实进程链：

```text
ProbeClient / probe --test
  -> Supervisor
  -> Access Agent /session
  -> Agent Hub
  -> Primary Agent Runtime
  -> SessionManager / Orchestrator / ToolExecutor
```

已覆盖场景：

| 场景 | 覆盖结果 |
|---|---|
| Supervisor readiness | `process_ready=true`、`hub_ready=true`、`default_route_ready=true` |
| Auth/transport | bad token WebSocket 被拒绝/关闭 |
| Session lifecycle | `session/new`、`session/list`、`session/resume`、`session/close` 通过 router 到 Primary Runtime |
| Prompt | deterministic `pong` prompt 通过真实 router path |
| `clientTurnId` | 同 ID 重试返回同一完成结果，`session/load` replay 只看到 1 条 user chunk |
| Tool execution | fake LLM 调用 `FileRead`，Probe 观察到 tool call/update/final answer |
| Permission allow | fake LLM 调用 `FileWrite` 覆盖已有文件，Probe 收到 permission request，allow 后文件被写入 |
| Permission reject | Probe reject 后 turn 正常 `end_turn`，目标文件未写入 |
| Error propagation | bad raw method 返回 `-32601 Method not found`，不被吞成裸 `Internal error` |
| Probe JSON | `python -m probe --test` 输出 machine-readable JSON，包括 `sessionId`、`promptCompleted`、`toolCalls`、`permissionRequests`、`errors` |
| Mode/cancel execution 扩展 | `session/set_mode`、`_mustang.agent/session/cancel_execution` 通过 router 到 Primary Runtime |
| User REPL 执行扩展 | `_mustang.agent/session/execute_shell`、`execute_python` 通过 router 到 Primary Runtime，并回放 execution update |
| Builtin tools | `Bash`、`Python`、`TodoWrite`、`Glob`、`Grep`、`SendMessage` missing-target、`ToolSearch` 均通过真实 tool executor |
| AskUserQuestion | Runtime 发起 permission/client request，Probe 使用 `updated_input` 回答并完成 turn |
| MCP | 本地 fixture MCP server 的 tool call、resource list、resource read 均通过 router path |
| Skills | user skill fixture 被 Primary Runtime 发现，`Skill` 激活可通过 Probe turn 观察 |
| Hooks | user `user_prompt_submit` hook fixture 在 Primary Runtime turn 内触发，sentinel 文件确认执行 |
| Memory | `memory_write`、`memory_append`、`memory_list` 通过真实 MemoryManager/ToolExecutor 路径 |
| Schedule | `CronCreate`、`CronList`、`CronDelete` 通过真实 ScheduleManager 路径 |
| Git | 临时 git workspace 的 `gitStatus` 上下文进入 LLM 请求 |
| Model/provider | `_mustang.agent/model/profile_list`、`_mustang.agent/model/provider_list` 可通过 Probe 调用 |
| Secrets | `_mustang.agent/secrets/auth` set/list/get/delete 通过 Probe 调用，get 返回 masked value |
| Gateway route | `/gateways/{adapter}/webhook` 在 GatewayManager 已加载但 adapter 缺失时返回 404 |

Phase 2 过程中修复的真实闭合缝问题：

- Supervisor token 可能以 `-` 开头，子进程 argparse 会把 token 当作选项；已改为 `--primary-token=<token>` / `--registration-token=<token>`。
- router backend 原本只转发 `session/new`、`session/resume`、`session/prompt`、`session/close`、`session/cancel`，导致 `session/list`/`session/load` 看不到 Primary Runtime 的会话；已补 `agent.session_list` / `agent.session_load` 合同和 replay update 转发。
- Probe `--test` 原本遇到 permission request 会挂住；已自动选择 `allow_once` 并记录 permission 摘要。
- Router backend 下 `session/set_mode`、`_mustang.agent/session/execute_shell`、`execute_python`、`cancel_execution` 原本仍落在 Access-local `SessionManager`，对 Primary Runtime session 报 `Session not found`；已统一通过 Hub 转发到 Primary Runtime，并把 execution update 回放给 Probe。
- Primary Runtime 原本没有在 `SessionManager` 后加载 trailing subsystems（`GatewayManager`、`ScheduleManager`），导致 Cron 模块在真实 Runtime turn 中不可用；已按内核顺序补齐 trailing subsystem startup。

实际验收命令：

```bash
uv run pytest tests/e2e/test_probe_phase2_e2e.py -q -m e2e
# 32 passed

uv run pytest tests/probe/test_client.py tests/e2e/test_probe_phase2_e2e.py -q -m 'e2e or not e2e'
# 48 passed

cd src/kernel
uv run pytest ../../tests/kernel/agent_runtime/test_session_service.py ../../tests/kernel/protocol/test_session_handler.py ../../tests/kernel/agent_hub/test_agent_hub_transport_c.py ../../tests/kernel/supervisor/test_supervisor_c1.py -q
# passed

cd src/kernel
uv run pytest ../../tests/kernel -q
# 1974 passed, 9 skipped, 24 deselected

uv run ruff check src/probe/probe/client.py src/probe/probe/__main__.py tests/probe/test_client.py tests/e2e/test_probe_phase2_e2e.py src/kernel/kernel/agent_runtime/session_service.py src/kernel/kernel/agent_runtime/__main__.py src/kernel/kernel/agent_hub/server.py src/kernel/kernel/protocol/acp/session_handler.py src/kernel/kernel/supervisor/runtime.py tests/kernel/supervisor/test_supervisor_c1.py
# All checks passed!

uv run ruff check src/kernel/kernel/protocol/acp/session_handler.py src/kernel/kernel/agent_runtime/session_service.py src/kernel/kernel/agent_runtime/__main__.py src/kernel/kernel/agent_hub/server.py tests/e2e/test_probe_phase2_e2e.py
# All checks passed!

cd src/kernel && uv run mypy kernel
# Success: no issues found in 392 source files

uv run mypy src/kernel/kernel/protocol/acp/session_handler.py src/kernel/kernel/agent_runtime/session_service.py src/kernel/kernel/agent_runtime/__main__.py src/kernel/kernel/agent_hub/server.py
# Success: no issues found in 4 source files

uv run pytest tests/kernel/agent_runtime/test_session_service.py tests/kernel/protocol/test_session_handler.py tests/kernel/protocol/test_routing.py -q
# 59 passed

uv run ruff check src/kernel/kernel/agent_runtime/session_service.py tests/e2e/test_probe_phase2_e2e.py
# All checks passed!

uv run mypy src/kernel/kernel/agent_runtime/session_service.py
# Success: no issues found in 1 source file
```

Probe JSON 输出摘要：

```json
{
  "ok": true,
  "sessionId": "<uuid>",
  "promptCompleted": true,
  "stopReason": "end_turn",
  "text": "pong",
  "toolCalls": [],
  "permissionRequests": [],
  "errors": []
}
```

仍需独立套件覆盖的范围：

- WebSearch/WebFetch 真实外部网络路径依赖搜索/抓取 backend 与外网稳定性；保留独立网络型 E2E，不放进确定性 Phase 2 fake LLM 套件。
- Gateway/Platform Adapter 的真实平台入站仍需要 fixture platform test；当前只覆盖 GatewayManager route 加载和 missing-adapter 错误面，不等同于真实 Discord/平台消息入站。
- Hooks 当前用 sentinel 证明 `user_prompt_submit` hook 在 Runtime 内触发；prompt mutation 对 LLM 消息体的具体影响仍以 HookManager/Orchestrator 单测覆盖。

## Phase 3: CLI 界面响应验证

状态：当前确定性 CLI smoke 与既有 CLI/PTY 套件已通过；更细的权限弹窗、tool card、reconnect golden 仍作为 UI 回归扩展项保留。

目标：Probe 证明功能可用后，CLI 必须证明用户能看到并操作该功能。CLI 测试关注 UI 响应，不替代 Probe。

CLI 验证分两类：

1. **CLI 逻辑测试**：验证 ACP client、session service、permission mapper、reconnect 等 TypeScript 逻辑。
2. **PTY / golden frame 测试**：启动真实 CLI，输入命令或 prompt，断言屏幕上出现对应 UI。

### CLI 界面响应矩阵

| 功能 | CLI 必须出现的界面响应 |
|---|---|
| connect/init | 连接成功状态、模型/会话状态可见 |
| session/new | 新 session 可用，状态栏显示 session/model 信息 |
| session/resume | 重连后无感恢复或显示恢复提示 |
| prompt | agent 文本流式显示 |
| long prompt | 不冻结 UI，状态保持 streaming |
| tool call | tool card 出现，显示工具名、参数摘要、状态 |
| tool success | tool card 显示 success，结果可折叠/展开 |
| tool error | tool card 显示 error，错误文本可读 |
| permission request | 弹出权限选择 UI，不能静默 reject |
| permission allow | 用户选择 allow 后 tool 继续执行 |
| permission reject | 用户选择 reject 后 turn 正常继续并显示拒绝结果 |
| WebSearch | 显示 WebSearch tool card 和 query 摘要 |
| WebFetch | 显示 permission + WebFetch tool card |
| FileRead | 显示读取文件 tool card |
| FileWrite/Edit | 显示权限 UI 和编辑结果 |
| Bash/Shell | 显示命令、权限、stdout/stderr/exit code |
| Todo | 显示 todo update 或相关 tool card |
| SendMessage | 显示 send/routing 状态或错误 |
| MCP | 显示 MCP tool/resource tool card 或降级错误 |
| cancel | Ctrl-C/取消后 UI 清理 streaming 状态 |
| reconnect | 显示 disconnected/restored，下一次 prompt 自动 resume |
| duplicate retry | 不重复显示两次用户消息或重复 tool 执行 |
| 中文输入 | 中文 prompt、tool 参数、回答不乱码 |
| Platform reply | 平台入口对应 CLI 不一定显示，但 CLI 状态不能被 gateway 事件破坏 |

### CLI 测试文件建议

| 文件 | 覆盖 |
|---|---|
| `src/cli/tests/test_acp_client.ts` | ACP request/response、error、disconnect |
| `src/cli/tests/test_session_resume_before_prompt.ts` | prompt 前 resume |
| `src/cli/tests/test_permission_mapper.ts` | permission request -> UI model |
| `src/cli/tests/test_permission_controller.ts` | allow/reject 选择结果 |
| `src/cli/tests/probe_router_permission_pty.ts` | 真实权限弹窗 golden |
| `src/cli/tests/probe_tool_cards_pty.ts` | tool card 渲染 golden |
| `src/cli/tests/probe_reconnect_pty.ts` | disconnect/restored UI |
| `src/cli/tests/probe_chinese_prompt_pty.ts` | 中文输入输出 |

### CLI 验收命令

```bash
cd src/cli
bun run tests/run_phase_b.ts
bun run tests/run_phase_c.ts
bun run tests/probe_phase_b_pty.ts
bun build src/main.ts --target=bun --outdir /tmp/deepcli-cli-build
```

真实 CLI smoke：

```bash
KERNEL_PORT=8361 bun run src/main.ts --print "Reply with exactly: pong"
KERNEL_PORT=8361 bun run src/main.ts --print "你去帮我查一下今天堪培拉天气怎么样"
```

CLI 验收标准：

- Kernel 发出 `session/request_permission` 时，CLI 必须显示权限 UI。
- 用户选择 allow/reject 后，CLI 必须把结果发回 Kernel。
- tool card、状态栏、错误提示、重连提示都必须可见。
- 中文输入输出不能乱码。
- UI 测试必须覆盖用户真实入口，不能只测 mapper 函数。

### Phase 3 完成记录 — 2026-05-01

已执行的 CLI 逻辑、PTY、构建和真实 Supervisor smoke：

```bash
cd src/cli
bun run tests/run_phase_b.ts
# 6 passed, 0 failed

bun run tests/run_phase_c.ts
# 12 passed, 0 failed

bun run tests/run_phase_d.ts
# config/session/startup/theme/autostart readiness checks passed

bun build src/main.ts --target=bun --outdir /tmp/deepcli-cli-build
# bundled successfully

cd ../..
bun run src/cli/tests/probe_phase_b_pty.ts
# PASS: Phase B real CLI PTY/TUI probe
```

真实端到端 smoke 使用临时 `HOME`、本地 OpenAI-compatible fake model、真实
Supervisor -> Access -> Hub -> Primary Runtime 路径运行。测试沙箱可以显式写入：

```yaml
transport:
  stack: acp
```

`acp` 现在也是 `TransportFlags.stack` 的默认值；这里显式写入只是为了
让测试 fixture 不受用户本机 flags 干扰。

通过的真实 CLI smoke：

```text
readiness: {"default_route_ready": true, "hub_ready": true, "platform_bindings_active": false, "primary_registered": true, "process_ready": true}

cli_run_all_live_kernel:
PASS: connect + auth
PASS: session/new -> sessionId = ab5bd997-6933-472f-ad79-f670da7eed74
PASS: prompt -> 1 chunks, stopReason=end_turn
PASS: multi-turn context preserved
Results: 4 passed, 0 failed

cli_print_pong:
pong

cli_print_chinese_weather:
堪培拉天气查询路径正常。
```

本轮发现并确认的环境/前置条件：

- `tests/probe_phase_b_pty.ts` 是 repo-root relative；从 `src/cli` 目录直接运行会找错 `src/cli/src/main.ts`，应从 repo root 运行 `bun run src/cli/tests/probe_phase_b_pty.ts`。
- `tests/run_all.ts` 需要 live Kernel；没有启动 Supervisor 时会因 `ws://localhost:8200` 不存在而失败，这不是 CLI 逻辑失败。
- 历史上 `transport.stack` 默认不是 ACP 时，WebSocket 认证成功但 CLI `initialize` 会 30 秒超时；现在默认和唯一生产 stack 都是 `acp`，live smoke 仍需覆盖真实 ACP handshake。

## 全局完成标准

一个功能只有同时满足下面三项，才算测试完成：

1. **Kernel 单元测试通过**：模块逻辑、边界、错误路径有覆盖。
2. **Probe 功能验证通过**：真实 Supervisor/ACP 路径可用，Probe 输出可粘贴。
3. **CLI 界面响应验证通过**：用户能看到对应 UI，必要时能操作并得到反馈。

完成报告必须包含：

- 运行过的 Kernel 单测命令。
- 运行过的 Probe 命令和关键 JSON 输出。
- 运行过的 CLI/PTY 命令和界面验收摘要。
- 未覆盖项和原因。

## 当前优先级

1. 将 Phase 3 真实 Supervisor + fake LLM smoke 固化成可重复脚本/CI 测试，避免只停留在手动命令记录。
2. 增加 CLI PTY 权限弹窗、tool card、reconnect 的专门 golden/probe。
3. 后续扩展 Probe 外部集成矩阵：真实 WebSearch/WebFetch backend、Gateway/Platform inbound fixture。
4. 将这三层测试要求写入对应功能的 Definition of Done 报告模板。

## 当前已知缺口

- Phase 3 基础链路已验证；仍缺少专门覆盖 permission dialog、tool card、reconnect 的稳定 golden/probe。
- `ping -> pong` 只能作为 smoke，不能作为功能完成证明。
- MCP reference server 当前返回 `406 Not Acceptable`，后续 E2E 应改用本地可控 MCP fixture 或把远端失败作为明确降级路径测试。
- Phase 2 本轮没有依赖真实外部网络服务；真实 WebSearch/WebFetch backend 应作为后续外部集成矩阵单独验证。
