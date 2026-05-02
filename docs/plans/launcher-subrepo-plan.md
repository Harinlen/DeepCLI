# DeepCLI Launcher 子仓库计划

**状态**：planned
**归属仓库**：新的子仓库，命名 `launcher`
**产品命令**：`deepcli`
**当前相关代码**：`src/cli/`、`src/kernel/kernel/supervisor/`

这份计划定义 DeepCLI 的跨平台启动器。目标是把 DeepCLI 做成一个真正的
一键本地产品：

```bash
deepcli
```

用户执行 `deepcli` 后，launcher 必须先确保本机 Kernel runtime 已经作为
当前用户的单例在后台运行；如果没有运行，就后台启动；然后直接进入 CLI。
这个 launcher 是独立于 TypeScript TUI CLI 和 Python Kernel 的新产品边界。

## 目标

- Windows、macOS、Linux 上都可以通过一个 `deepcli` 命令后台启动 Kernel
  runtime，然后打开 CLI。
- Kernel runtime 在同一个 OS 用户下必须是单例。多个终端同时运行
  `deepcli` 时，应该连接到同一个 runtime，而不是启动多个互相竞争的
  Kernel/Supervisor。
- Access Agent 有稳定默认端口；如果默认端口被无关进程占用，launcher
  自动选择新的本地端口并记录下来。
- CLI 保持 thin ACP/WebSocket client。CLI 不 import Kernel 代码，也不负责
  进程监督。
- launcher 同时支持开发 checkout 模式和正式安装包模式。

## 非目标

- 这个子仓库不做 Home Screen UI。launcher 只负责启动/连接 runtime，然后
  调起 CLI。
- launcher 不实现 agent loop，不实现 ACP 协议。
- CLI 不直接读取 Kernel SQLite、sidecar 文件或 subsystem 状态。runtime
  发现由 launcher 和 Kernel/Supervisor readiness endpoint 负责。
- v1 不做系统级 daemon。单例范围是当前 OS 用户，不是整台机器。

## 建议形态

创建新的 native launcher 子仓库：

```text
launcher/
├── cmd/deepcli/                  # native deepcli 命令
├── internal/launcher/            # ensure / attach / start 主逻辑
├── internal/platform/            # Windows/macOS/Linux 进程和锁适配
├── internal/runtime/             # runtime 文件 schema + readiness probe
├── internal/install/             # packaged/dev 安装布局发现
└── docs/                         # launcher 自己的打包说明
```

测试不放在 launcher 子仓库内部，按当前 monorepo 习惯放在外层 `tests/`
对应目录下，例如 `tests/launcher/` 或后续约定的跨仓库验证目录。launcher
子仓库只保留实现代码和自身说明文档。

建议实现语言：**Go**。

原因：launcher 的核心工作是进程控制、文件锁、HTTP probe、路径发现和跨平台
打包。Go 可以产出小型 native binary，Windows/macOS/Linux 进程 API 够直接，
GitHub Actions 构建简单，并且避免用户在 launcher 启动前就必须先装好
Node/Bun/Python。

Rust 也可行，但除非后续打包或嵌入需求明显更适合 Rust，否则 Go 是更简单的
选择。

## 命令契约

安装后的 `deepcli` 命令就是 launcher。默认行为：

1. 确保本地 DeepCLI runtime 存在。
2. 把连接信息和 auth 信息放进 CLI 环境变量。
3. 前台执行或启动 TUI CLI。

初始命令：

| 命令 | 行为 |
|---|---|
| `deepcli [args...]` | 确保 runtime，然后把剩余参数传给 CLI。 |
| `deepcli status` | 输出单例 runtime 状态、端口、pid、readiness。 |
| `deepcli stop` | 停止当前用户的 runtime。 |
| `deepcli restart` | 停止后重新启动 runtime，然后退出。 |
| `deepcli kernel start` | 只启动/确保 runtime，不打开 CLI。 |
| `deepcli kernel logs` | tail launcher/Supervisor 日志。 |

默认命令应该像普通 CLI app 一样：用户输入 `deepcli`，进入聊天界面；后台
runtime 的启动细节安静处理。

## 启动哪个 Runtime

launcher 应启动当前产品路径，也就是 Supervisor：

```bash
python -m kernel.supervisor --access-port <port> --state-dir <state-dir> --workspace <cwd>
```

Supervisor 会启动：

- Access Agent：使用用户可见端口
- Agent Hub：内部 loopback 端口
- Primary Agent Runtime：内部 loopback 端口

CLI 只连接 Access Agent：

```text
ws://127.0.0.1:<access-port>/session
```

## 开发模式 vs 安装包模式

launcher 需要支持两种运行布局。

| 模式 | 发现方式 | Kernel 命令 | CLI 命令 |
|---|---|---|---|
| 开发 checkout | `DEEPCLI_DEV_ROOT` 或仓库标记文件 | 在 `src/kernel` 下执行 `uv run python -m kernel.supervisor ...` | 在 `src/cli` 下执行 `bun run src/main.ts ...` |
| 正式安装包 | launcher binary 旁边的文件布局 | bundled Python env / kernel wheel | bundled CLI artifact |

安装包模式可以先采用务实布局：

```text
DeepCLI/
├── bin/deepcli                 # native launcher
├── kernel/                     # Python venv 或嵌入式 runtime
├── cli/                        # CLI bundle
└── resources/
```

不同平台的具体打包形式可以不同，但 launcher 内部应通过一个 `InstallLayout`
抽象隐藏这些差异。

## 用户级状态目录

沿用 Mustang 兼容状态目录：

| 平台 | State Root |
|---|---|
| Windows | `%USERPROFILE%\.mustang\state` |
| macOS | `$HOME/.mustang/state` |
| Linux | `$HOME/.mustang/state` |

launcher 拥有的文件：

```text
~/.mustang/state/
├── launcher.lock
├── launcher.log
└── supervisor/
    ├── supervisor.json
    ├── supervisor.stdout.log
    └── supervisor.stderr.log
```

`supervisor.json` 当前已经存在。launcher 可以扩展它，但要兼容现有字段：

```json
{
  "ready": true,
  "access": {
    "pid": 1234,
    "endpoint": "http://127.0.0.1:8200"
  },
  "children": {}
}
```

未来 launcher 字段必须 additive，例如：

```json
{
  "launcherVersion": "0.1.0",
  "access": {
    "host": "127.0.0.1",
    "port": 8200,
    "wsUrl": "ws://127.0.0.1:8200/session",
    "healthUrl": "http://127.0.0.1:8200/"
  }
}
```

## 单例算法

单例必须由 launcher 保证，而不是由 CLI 保证。

1. 读取现有 runtime 文件，如果存在。
2. probe Access Agent：
   `GET http://127.0.0.1:<port>/access/readiness`。
3. 如果 readiness 返回 `process_ready`、`hub_ready`、
   `default_route_ready`，复用该 runtime。
4. 获取当前用户的 launcher lock。
5. 获取锁后再次 probe，处理多个 `deepcli` 并发启动的竞态。
6. 如果仍然没有可用 runtime，选择端口并后台 detached 启动 Supervisor。
7. 等待 readiness。
8. 原子写入/更新 runtime 状态。
9. 释放锁，然后启动 CLI。

如果 runtime 文件过期，probe 失败后直接忽略。launcher 不应该因为某个端口被
记录过，就杀掉占用该端口的未知进程。

## 跨平台锁

锁必须能跨独立终端进程生效。

| 平台 | 推荐锁实现 |
|---|---|
| Windows | Named mutex，或通过 Win32 API 做 exclusive lock file |
| macOS | `flock` / `fcntl` 语义的 advisory file lock |
| Linux | `flock` / `fcntl` 语义的 advisory file lock |

对外抽象：

```text
AcquireUserLock(stateRoot, timeout) -> LockHandle
LockHandle.Release()
```

超时策略：

- 默认 `deepcli`：最多等待 30 秒，让另一个 launcher 完成 runtime 启动。
- `deepcli status`：不等待；直接报告 startup in progress。
- `deepcli stop/restart`：最多等待 30 秒。

## 端口选择

默认 Access Agent 端口：`8200`。

规则：

1. 如果 runtime 文件记录的端口上已有 ready 的 DeepCLI Access Agent，复用。
2. 如果没有 runtime，且 `8200` 空闲，使用 `8200`。
3. 如果 `8200` 上已经是 ready 的 DeepCLI Access Agent，即使 runtime 文件缺失，
   也复用它。
4. 如果 `8200` 被其它进程占用，从 OS 申请一个空闲 loopback 端口并记录。
5. 如果用户显式传了 `--port <port>`，不要静默换端口。若该端口被非 DeepCLI
   进程占用，直接给出清晰错误。

readiness 检测优先使用 Supervisor Access Agent 的 `/access/readiness`。
为了兼容旧的 bare Kernel 进程，launcher 可以额外 probe `GET /`，但产品主路径
应优先 Supervisor。

## 后台进程语义

launcher 启动的 Supervisor 是独立于 CLI 的后台进程。关闭 CLI 不应停止
Kernel runtime。

平台注意事项：

| 平台 | 启动行为 |
|---|---|
| Windows | 使用 `CREATE_NEW_PROCESS_GROUP`；正式安装包中尽量避免弹出额外 console window。 |
| macOS | 从 CLI process group detached；日志写入 state dir。未来可以加 LaunchAgent，但 v1 不要求。 |
| Linux | 创建新的 session/process group；日志写入 state dir。systemd user unit 是未来可选项，不属于 v1。 |

v1 不要求 runtime 在 logout 或重启后仍然存活。它只需要在 CLI 退出后继续运行，
并能被同一用户后续终端复用。

## Auth 交接

连接认证仍由 Kernel 拥有。当前 token 存储在：

```text
~/.mustang/state/auth_token
```

launcher 在 Supervisor readiness 后读取 token，并通过环境变量传给 CLI：

```text
MUSTANG_TOKEN=<token>
KERNEL_URL=ws://127.0.0.1:<port>
KERNEL_HEALTH_URL=http://127.0.0.1:<port>/
```

CLI 当前已经支持 `MUSTANG_TOKEN`、`KERNEL_URL`、`KERNEL_PORT`。实现本计划时
需要补 `KERNEL_HEALTH_URL`，这样端口漂移后 Welcome / health 展示不会依赖旧
配置文件。

## CLI 边界

TypeScript CLI 不应该变成 launcher。它的启动路径应保持为：

1. 从 native launcher 接收 `KERNEL_URL` 和 `MUSTANG_TOKEN`。
2. 连接 ACP WebSocket。
3. 渲染 TUI 或执行 `--print`。

开发时仍然可以直接运行 CLI：

```bash
KERNEL_URL=ws://127.0.0.1:8200 MUSTANG_TOKEN=... bun run src/main.ts
```

但用户面对的 `deepcli` 命令应该是 native launcher。

## 失败模式

| 失败 | 行为 |
|---|---|
| 另一个 `deepcli` 正在启动 runtime | 等待锁，然后复用已启动 runtime。 |
| runtime 文件存在但 probe 失败 | 视为 stale，启动新的 runtime。 |
| 默认端口被无关进程占用 | 除非用户显式指定端口，否则选择新端口。 |
| Supervisor 启动但 readiness 超时 | 输出日志路径，非零退出，不启动 CLI。 |
| readiness 后 token 文件仍缺失 | 视为启动失败，输出 state 路径和日志路径。 |
| CLI 退出 | runtime 保持运行。 |
| 调用 `deepcli stop` | 停止 Supervisor child tree，并把 runtime 标记为 stopped。 |

## 实施阶段

### Phase 0 — 本仓库契约准备

- 更新 `docs/cli/design.md`，明确区分用户面对的 native launcher 和
  TypeScript thin client。
- CLI config loading 增加 `KERNEL_HEALTH_URL` 支持。
- 确保 Supervisor runtime 文件包含足够的 Access Agent 字段，launcher attach
  时不需要猜。

### Phase 1 — Launcher 骨架

- 创建 `launcher` 子仓库。
- 实现 Windows/macOS/Linux 的 state root 发现。
- 实现 `deepcli status`：解析 runtime 文件并 probe readiness。
- 实现 user lock 抽象和平台测试。

### Phase 2 — Ensure Runtime

- 实现单例 ensure 算法。
- 实现默认端口和 fallback 端口选择。
- 在开发模式下 detached 启动 Supervisor。
- 把 `KERNEL_URL`、`KERNEL_HEALTH_URL`、`MUSTANG_TOKEN` 传给 CLI。

### Phase 3 — Stop/Restart 和日志

- 实现 `deepcli stop`。
- 按平台实现 process-tree termination。
- 增加日志捕获和 `deepcli kernel logs`。

### Phase 4 — 安装包模式

- 定义 Windows/macOS/Linux 安装布局。
- bundle 或定位 Kernel runtime 和 CLI artifact。
- 增加 release builds：
  - Windows x64
  - macOS arm64/x64
  - Linux x64

### Phase 5 — 全系统验证

创建跨平台验证脚本，必须运行安装后的 `deepcli` binary，而不是只跑
`go test`。

必测场景：

| 场景 | 期望结果 |
|---|---|
| 没有 runtime | `deepcli --print "Reply with exactly: pong"` 自动启动 runtime 并输出 `pong`。 |
| runtime 已存在 | 第二次 `deepcli` 复用同一个 Access Agent pid/port。 |
| 两个 CLI 并发启动 | 只启动一个 Supervisor；两个 CLI 都能连接。 |
| 端口 8200 被非 DeepCLI 进程占用 | launcher 选择其它端口，CLI 仍能连接。 |
| 显式 `--port 8200` 且端口被占用 | launcher 清晰失败，不 fallback。 |
| CLI 退出 | runtime 仍然 ready。 |
| `deepcli stop` | runtime 停止；之后再次 `deepcli` 会启动新的 runtime。 |

这些场景要在 Windows、macOS、Linux CI 上都跑。

## 开放问题

- CLI 安装包 artifact 采用哪种方式：依赖 Bun runtime、Bun single executable，
  还是 Node-compatible bundle？
- Kernel 安装包 artifact 采用哪种方式：Python venv、PyApp/PyOxidizer 风格
  bundle，还是平台 installer 管理 Python？
- macOS 未来是否需要 LaunchAgent 做 login persistence，还是 v1 保持
  on-demand 后台进程？
- Windows 正式安装包未来是否需要 tray/Home Screen？这应该独立于命令行
  launcher。

## 验收标准

- Windows/macOS/Linux fresh install 后，用户无需手动启动 Kernel，直接运行
  `deepcli` 即可进入 CLI。
- 两个终端同时运行 `deepcli` 时，连接到同一个用户 runtime。
- 非 DeepCLI 进程占用 `8200` 时，默认启动仍然成功。
- CLI 保持 ACP-only，不接管 Kernel 进程监督。
- launcher 子仓库拥有所有平台相关的进程、锁、打包和服务管理代码。
