# Supervisor-Controlled Runtime Restart Plan

状态：**核心实现已落地；launcher command fallback 与完整自重启 E2E 待补**

日期：**2026-05-07**

## 2026-05-07 实施记录

已落地：

- Supervisor 现在拥有 Hub / Access / Primary 的 runtime lifecycle，并在 child
  异常退出时按 `hub -> access -> primary` 依赖后缀重启。
- 增加本机 Unix socket control API：`status`、`restart_runtime`、
  `restart_agent`、`stop_runtime`。
- Access Agent 暴露 operator-only ACP 方法：
  `_mustang.agent/runtime/status` 与 `_mustang.agent/runtime/restart`。
- CLI 新增 `/kernel status` 与 `/kernel restart`。
- 新增 model-visible `RestartSelf`，只允许当前 Primary Agent Runtime 请求
  受控自重启；restart request 在 tool result/turn response flush 后触发。
- `ToolAuthorizer` 增加 runtime process guard；Bash/shell 类工具命中
  Supervisor/Hub/Access/Agent Runtime kill/restart 语义时 hard-deny，且
  `bypass` 不能绕过。
- Supervisor runtime file 现在写入 `status`、`degradedReason`、
  `restartCounts`、`lastExit`、children pid/running 状态；连续重启超过
  rolling budget 后进入 `degraded`。

仍待后续补齐：

- launcher-level `deepcli restart` / `deepcli stop` 的 fallback 迁移到 control
  API；当前已提供 `/kernel restart` operator path。
- `RestartSelf` 的真实 Supervisor E2E：需要验证 tool result 已落库、Primary
  被替换、Hub/Access 不重启、CLI resume 正常。
- Agent 尝试 raw kill 的真实 tool-call E2E；当前已有 authorizer 单元覆盖。

## 问题

Supervisor 的职责应该是 DeepCLI runtime 的 owner：启动、监护、重启、
停止 Hub / Access Agent / Primary Runtime。现在边界没有立住：

- `SupervisorRuntime.wait()` 发现任意 child 退出后直接让 Supervisor 退出，
  不是自动拉起 child。
- launcher 的 `restart` 通过 `kill` 整个进程组实现，是外部强杀，不是受控
  restart。
- Agent 可以通过 Bash 尝试 `kill` Supervisor / Kernel pid。即使需要权限确认，
  这也不是正确能力边界：运行时生命周期不应该是普通 shell side effect。
- 当前 runtime state 只服务 launcher readiness，尚未形成受保护的控制面。

这次要解决的是 ownership，而不是再给 `kill` 多加一条提示文案。

## 目标

- Supervisor 存活时，child 被杀后自动按策略重启。
- Supervisor / Kernel / Hub / Access / Primary 的停止和重启只能通过受控
  runtime-control 路径触发。
- Agent tool call 不能直接杀 DeepCLI runtime 进程；`bypass` 也不绕过这条
  self-protection。Agent 需要重启自己时，必须走明确的 self-restart 能力。
- 用户仍然可以显式重启 runtime，但动作走 CLI / ACP / Supervisor control
  API，而不是让 Agent 执行 `kill`。
- 重启过程可观测：runtime file、readiness、CLI 状态和日志都能说明当前阶段。

## 非目标

- 不阻止用户在 DeepCLI 外部的普通终端手动 `kill` 自己的进程。用户拥有本机
  shell，这不是可完全禁止的安全边界。
- 不把任意 runtime restart 暴露成 LLM tool。只允许一个窄口径的
  self-restart：Agent 可以请求重启自己的 Agent Runtime，不能重启
  Supervisor、Hub、Access Agent 或其他 Agent。
- 不引入 systemd / launchd / Windows Service；用户级 Supervisor 仍是 v1
  owner。
- 不在 CLI 里直接管理 Kernel 子进程。CLI 只发受控请求，执行权在 Supervisor。

## 核心原则

1. **Supervisor owns process lifecycle**：只有 Supervisor 可以 spawn、stop、
   restart child processes。
2. **Launcher owns Supervisor lifecycle**：只有 launcher 可以启动不存在的
   Supervisor；运行中的 restart 优先走 Supervisor control API。
3. **Separate operator restart from self-restart**：用户可通过 CLI 触发 full
   runtime restart；LLM 只拥有 self-restart，且只能作用于当前 Agent Runtime。
4. **ToolAuthorizer self-protection is hard deny**：shell 类工具命中 DeepCLI
   runtime 进程操作时直接 deny，发生在 mode override / grant cache 之前。
5. **Crash recovery and intentional restart are different paths**：child crash 由
   Supervisor monitor 自动恢复；用户 restart 由 control API 做 drain + replace。

## 设计

### 1. Supervisor 监护策略

把 `SupervisorRuntime.wait()` 从“任一 child 退出就退出 Supervisor”改为
monitor loop：

```text
loop:
  poll hub/access/primary
  if child exited unexpectedly:
    mark child restarting in runtime file
    stop dependents in reverse dependency order
    restart required suffix in startup order
    wait readiness
    update runtime file
  sleep
```

依赖顺序：

```text
hub -> access -> primary
```

重启策略：

| 退出对象 | 恢复动作 |
|---|---|
| primary | restart primary；等待 hub registration + access default route ready |
| access | stop primary；restart access；restart primary |
| hub | stop primary + access；restart hub；restart access；restart primary |

增加 restart budget：

- rolling window，例如 60 秒内最多 5 次。
- 超过 budget 后 runtime 进入 `degraded`，Supervisor 保持存活并写 runtime file。
- readiness 返回 `process_ready=true` 但 `default_route_ready=false`，CLI 显示
  degraded 状态和日志路径。

### 2. Supervisor control API

Supervisor 增加本机私有 control endpoint，建议优先 Unix domain socket：

```text
~/.local/state/deepcli/runtime/supervisor/control.sock
```

Windows 后续用 named pipe；开发模式可先用 loopback HTTP + token，但最终形态
应抽象为 `SupervisorControlServer` / `SupervisorControlClient`。

control token：

- Supervisor 启动时生成。
- 写入 runtime file 或单独 `control-token` 文件，权限 `0600`。
- Access Agent 启动时由 Supervisor 注入 token/env，不让 LLM 接触。

control methods：

| Method | 行为 |
|---|---|
| `status` | 返回 Supervisor pid、child pid、endpoint、restart counts、degraded reason |
| `restart_child(name)` | 只重启指定 child，主要用于调试和后续 Home Screen |
| `restart_agent(agent_id, reason, after_ack)` | 重启一个 Agent Runtime；`after_ack=true` 时等待调用方写完 tool result 后再终止 |
| `restart_runtime(reason)` | 受控重启 hub/access/primary，不杀 Supervisor |
| `stop_runtime(reason)` | 受控停止 child，Supervisor 可选择随后退出 |

`restart_runtime` 流程：

```text
receive request
mark status = restarting
stop primary -> access -> hub
start hub -> access -> primary
wait readiness/default route
mark ready
return final status
```

### 3. ACP operator method

在 Access Agent 暴露 DeepCLI-owned ACP extension：

```text
_mustang.runtime/status
_mustang.runtime/restart
```

这些是 client/operator 方法，不进入 ToolManager，不进入 prompt，不进入 LLM
tool schema。

调用路径：

```text
CLI slash command (/kernel status, /kernel restart)
  -> ACP _mustang.runtime/*
  -> Access Agent RuntimeControlClient
  -> Supervisor control socket
  -> SupervisorRuntime
```

权限：

- 只允许本地 token-authenticated client。
- 不允许 gateway peer / remote password auth 默认调用 restart。
- 方法参数必须带 human-facing reason，写入 audit log。

### 3.5 Agent self-restart tool

新增一个 model-visible 但窄权限的工具，建议名：

```text
RestartSelf
```

语义：

- 只能重启当前 Agent Runtime。
- 不能选择 pid，不能传任意 agent id，不能重启 Supervisor / Hub / Access。
- tool call 必须先返回 tool result，例如 `Self-restart scheduled`。
- tool result、assistant history 和 turn-completed event flush 后，Session/Runtime
  调用 Supervisor control `restart_agent(current_agent_id, after_ack=true)`。
- Supervisor 终止并重启该 Agent Runtime；Access/Hub 保持运行。
- CLI 看到连接断开时按已有 reconnect/session resume 逻辑恢复。

为什么不允许 Agent 直接 `kill $$` 或 `kill <primary_pid>`：

- 直接 kill 会在 tool result 写入前切断当前 turn，容易制造 orphan tool call。
- 直接 kill 没有审计、没有 restart reason、没有 drain。
- 直接 kill 的参数空间太大，容易误伤 Supervisor / Hub / Access / 其他 Agent。

系统提示要明确告诉 Agent：

```text
If you need to restart your own runtime, use RestartSelf. Do not kill DeepCLI
Supervisor, Kernel, Hub, Access Agent, or Agent Runtime processes from shell.
```

### 4. Launcher restart 改造

`deepcli restart` 不再直接 `kill` 进程组作为首选路径。

新流程：

```text
if Supervisor ready and control socket reachable:
  supervisor.restart_runtime(reason="launcher restart")
  wait_ready
else:
  fallback stop_runtime + ensure_runtime
```

`deepcli stop` 可以保留进程组 TERM/KILL 作为最后手段，但应先调用
`stop_runtime` control method。只有 Supervisor 不响应时才强杀。

### 5. Agent shell self-protection

新增 runtime process guard，放在 ToolAuthorizer 早期，优先级高于：

- session grant cache
- `bypass`
- bash LLMJudge
- allowlist

保护对象来源：

- launcher runtime state：Supervisor pid / pgid。
- Supervisor runtime file：hub/access/primary pid。
- fallback：当前进程祖先和已知 `kernel.supervisor` child runtime files。

拦截范围：

- Bash / PowerShell / Cmd / ShellExec / Monitor 这类 shell execution tools。
- 命中以下语义时 deny：
  - `kill`, `pkill`, `killall`, `taskkill`, `Stop-Process`
  - target pid / process group 是 Supervisor 或 child pid
  - command line pattern 指向 `kernel.supervisor`, `kernel.agent_hub`,
    `kernel.access_agent`, `kernel.agent_runtime`
  - `deepcli stop`, `deepcli restart`, `scripts/run-kernel.sh` restart/kill path
    由 Agent 触发
  - 当前 Agent Runtime 的 raw pid kill 也 deny；自重启必须走 `RestartSelf`

返回文案：

```text
DeepCLI runtime lifecycle is protected. Use RestartSelf to restart this agent,
or /kernel restart for a full user-controlled runtime restart. Agents cannot
kill Supervisor or Kernel processes directly.
```

这条 deny 不能通过 `allow_always` 缓存。用户若真要强杀，可以离开 DeepCLI 在
外部终端执行。

### 6. Prompt 和 UX

系统提示里加入短规则：

- 不要用 shell 杀 DeepCLI Kernel / Supervisor。
- 如果需要重启自己，使用 `RestartSelf`。
- 需要重启 runtime 时，让用户使用 `/kernel restart` 或 launcher command。

CLI：

- 新增 `/kernel status`
- 新增 `/kernel restart`
- restart 前显示确认，避免用户误触。
- restart 后显示 reconnect/retry 状态。

## 实施阶段

### Phase 1 — Supervisor restart policy

- 重构 `SupervisorRuntime` 为 child state machine。
- 实现 dependency-aware restart。
- runtime file 写入 `status`, `restartCounts`, `lastExit`, `lastRestartAt`。
- 单元测试 child crash 后重启 suffix，而不是 Supervisor 退出。

### Phase 2 — Control socket

- 增加 `supervisor/control.py`。
- 实现 `status` / `restart_runtime`。
- launcher 优先走 control API。
- 单元测试 token、bad method、restart order、fallback stop。

### Phase 3 — ACP operator API

- Access Agent 接入 `RuntimeControlClient`。
- 增加 `_mustang.runtime/status` / `_mustang.runtime/restart` contracts。
- CLI `/kernel status`、`/kernel restart` 调用 ACP。
- 确认这些方法不出现在 CommandManager 的 model-invocable tool list。

### Phase 4 — Agent self-restart

- 增加 `RestartSelf` tool。
- Tool result flush 后触发 Supervisor `restart_agent(current_agent_id)`。
- Runtime/Session 层确保 restart request 被记录到 audit / turn metadata。
- 单元测试：tool result 先写入，随后 Supervisor restart request 才发出。

### Phase 5 — Runtime process guard

- 增加 `kernel.agents.mustang.tool_authz.runtime_guard`。
- 在 `ToolAuthorizer._authorize_impl` 最前面调用。
- 覆盖 Bash / Cmd / PowerShell / ShellExec / Monitor。
- 单元测试 `bypass` 下仍 deny runtime kill，包括 deny 当前 Agent Runtime 的
  raw pid kill。

### Phase 6 — E2E probes

- 真实 Supervisor 启动后 kill Primary pid，确认 Supervisor 自动拉起并恢复
  default route。
- 真实 CLI 执行 `/kernel restart`，确认 restart 成功且 TUI reconnect。
- Agent 调用 `RestartSelf`，确认当前 Agent Runtime 被重启、Supervisor/Hub/Access
  不重启、session 可恢复。
- Agent 尝试 `kill <primary_pid>`，确认 tool denied 且 Supervisor 仍存活。
- Agent 尝试 `deepcli restart`，确认 tool denied，并提示正确路径。

## 验收标准

- child crash 不导致 Supervisor 退出。
- 正常 restart 不依赖 shell `kill`。
- Agent 无法通过 tools 杀 DeepCLI runtime，即使当前 permission mode 是
  `bypass`。
- Agent 可以通过 `RestartSelf` 请求重启自己的 Agent Runtime，并被 Supervisor
  立即拉起。
- 用户有清晰、受控的 `/kernel restart` 和 `deepcli restart` 路径。
- runtime state 能解释当前是 ready / restarting / degraded / stopped。
