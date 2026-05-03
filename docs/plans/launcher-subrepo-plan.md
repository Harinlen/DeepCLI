# DeepCLI Launcher 子仓库计划

**状态**：planned
**归属目录**：`src/launcher/`（新的 sub-repo 边界）
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
- Linux v1 不提供 `.deb` / `.rpm` 作为主发布路径，不要求 systemd user
  service。包管理器和 service install 都是后续可选增强。

## 建议形态

创建新的 native launcher sub-repo，放在 `src/launcher/`：

```text
src/launcher/
├── bin/deepcli                   # Linux Bash launcher
├── packaging/linux/              # install.sh、release staging
└── docs/                         # launcher 自己的打包说明
```

测试不放在 `src/launcher/` 内部，按当前 monorepo 习惯放在外层 `tests/`
对应目录下，例如 `tests/launcher/` 或后续约定的跨仓库验证目录。launcher
子仓库只保留实现代码和自身说明文档。

Linux v1 实现语言：**Bash**。

原因：Linux 安装路径本来就是 `install.sh`，用 Bash 做 launcher 可以避免再引入
Go/Rust 等额外工具链。Bash launcher 只负责本机 Linux/WSL2 的用户级 runtime
管理；Windows/macOS 后续可以在不影响 Linux v1 的前提下重新评估 native 实现。

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
<kernel-python> -m kernel.supervisor \
  --access-port <port> \
  --state-dir <state-dir> \
  --config-dir <config-dir> \
  --workspace <cwd>
```

Supervisor 会启动：

- Access Agent：使用用户可见端口
- Agent Hub：内部 loopback 端口
- Primary Agent Runtime：内部 loopback 端口

CLI 只连接 Access Agent：

```text
ws://127.0.0.1:<access-port>/session
```

## Linux v1 发布决策

Linux v1 采用 Claude Code / Hermes 风格的一键 shell installer，而不是
`.deb` / `.rpm` / distro package manager：

```bash
curl -fsSL https://install.deepcli.dev/linux.sh | sh
```

设计原则：

- 不需要 `sudo`。
- 不碰系统 Python。
- 不要求用户提前安装 Bun / Node。
- 默认只写用户目录。
- 安装后用户只需要运行 `deepcli`。
- Kernel、CLI、launcher 都由 DeepCLI 自己在用户目录下版本化管理。

Linux v1 用户级目录：

```text
~/.local/bin/deepcli
~/.local/share/deepcli/
├── bin/
│   └── deepcli-1.0.0              # launcher binary
├── kernel/
│   └── 1.0.0/
│       ├── .venv/                 # uv-managed Python runtime
│       ├── wheels/
│       └── uv.lock
├── cli/
│   └── 1.0.0/
│       └── deepcli-cli            # bundled CLI artifact
└── downloads/
~/.local/state/deepcli/
├── launcher.lock
├── launcher.log
└── runtime/
    ├── supervisor.json
    ├── supervisor.stdout.log
    └── supervisor.stderr.log
~/.config/deepcli/
└── config.yaml
```

`~/.local/bin/deepcli` 是用户 PATH 上的稳定入口，通常指向当前版本的
launcher：

```text
~/.local/bin/deepcli -> ~/.local/share/deepcli/bin/deepcli-1.0.0
```

installer 负责：

1. 检测 Linux / WSL2 / CPU 架构。
2. 创建 `~/.local/share/deepcli`、`~/.local/state/deepcli`、
   `~/.config/deepcli`。
3. 下载 launcher binary、CLI artifact、Kernel wheel、lock metadata。
4. 安装或定位 `uv`。
5. 用 `uv python install 3.13` 准备 Kernel Python。
6. 创建 `~/.local/share/deepcli/kernel/<version>/.venv`。
7. 安装 Kernel wheel + locked dependencies。
8. 创建 / 更新 `~/.local/bin/deepcli` symlink。
9. 检查 `~/.local/bin` 是否在 PATH；缺失时提示或写入 shell rc。
10. 运行 `deepcli doctor` 或等价 smoke，确认安装物可执行。

release artifact 至少包含：

```text
deepcli-launcher-linux-x64
deepcli-launcher-linux-arm64
deepcli-cli-linux-x64.tar.gz
deepcli-cli-linux-arm64.tar.gz
deepcli-kernel-1.0.0-py3-none-any.whl
deepcli-kernel-lock-1.0.0.txt / uv.lock
install-linux.sh
checksums.txt
manifest.json
```

Kernel 不是 frozen binary。Kernel 发布为 wheel，并安装到 launcher 管理的
完整 Python venv 中。这样 `import`、插件、MCP、Python 工具和未来动态依赖
都仍然在真实 Python 环境里运行。

`!` 命令和 shell tool 不使用 Kernel venv 的 PATH。Kernel venv 只用于运行
Kernel 自己；用户命令必须在用户项目 cwd 和用户 shell 环境中执行。Linux shell
选择规则：

1. 优先 `$SHELL`。
2. fallback `/bin/bash`。
3. fallback `/bin/sh`。

`!python` 因此走用户 PATH 上的 Python，而不是
`~/.local/share/deepcli/kernel/<version>/.venv/bin/python`。

## Windows Native vs WSL2

Windows native 和 WSL2 必须视为两个独立 runtime world：

| 环境 | Kernel runtime | Shell | 单例范围 |
|---|---|---|---|
| Windows native `deepcli.exe` | Windows Python runtime | PowerShell / cmd | Windows 用户 |
| WSL2 内的 `deepcli` | Linux Python runtime | bash / sh | 当前 WSL distro 用户 |

v1 默认不跨 world 复用 Kernel。Windows native launcher 不连接 WSL2 Kernel，
WSL2 launcher 也不连接 Windows Kernel。后续可以设计显式目标，例如
`deepcli --target wsl:Ubuntu`，但它不是默认行为。

Linux launcher 应识别 WSL2：

- `runtime.GOOS == "linux"`；
- 且 `WSL_DISTRO_NAME` / `WSL_INTEROP` 存在，或
  `/proc/sys/kernel/osrelease` 包含 `microsoft` / `WSL`。

识别 WSL2 只是为了安装提示、目录说明和诊断输出；运行模型仍按 Linux artifact
和 Linux Supervisor 处理。

## 开发模式 vs 安装包模式

launcher 需要支持两种运行布局。

| 模式 | 发现方式 | Kernel 命令 | CLI 命令 |
|---|---|---|---|
| 开发 checkout | `DEEPCLI_DEV_ROOT` 或仓库标记文件 | 在 `src/kernel` 下执行 `uv run python -m kernel.supervisor ...` | 在 `src/cli` 下执行 `bun run src/main.ts ...` |
| 正式安装包 | `~/.local/share/deepcli` 等平台安装布局 | managed Python venv + Kernel wheel | bundled CLI artifact |

跨平台安装包模式由 `InstallLayout` 抽象隐藏差异。Linux v1 的实体布局见
“Linux v1 发布决策”；Windows/macOS 后续可采用各自更合适的 installer
布局，但 launcher 内部仍暴露相同字段：

```text
InstallLayout
├── launcherPath
├── kernelPython
├── kernelVersion
├── cliCommand
├── stateDir
├── configDir
└── logDir
```

## 用户级状态目录

DeepCLI 产品态使用 DeepCLI 自己的目录，不沿用旧兼容目录：

| 平台 | State Root |
|---|---|
| Windows native | `%LOCALAPPDATA%\DeepCLI\state` |
| macOS | `$HOME/Library/Application Support/DeepCLI/state` 或 `$HOME/.local/state/deepcli`（待 macOS 打包决策确认） |
| Linux / WSL2 | `$HOME/.local/state/deepcli` |

launcher 拥有的文件：

```text
~/.local/state/deepcli/
├── launcher.lock
├── launcher.log
└── runtime/
    ├── supervisor.json
    ├── supervisor.stdout.log
    └── supervisor.stderr.log
```

`supervisor.json` 是 attach hint，不是真相。真相永远是 readiness probe。
建议字段：

```json
{
  "version": "1.0.0",
  "pid": 1234,
  "processGroupId": 1234,
  "startedAt": "2026-05-03T00:00:00Z",
  "state": "ready",
  "access": {
    "host": "127.0.0.1",
    "port": 8200,
    "wsUrl": "ws://127.0.0.1:8200/session",
    "readinessUrl": "http://127.0.0.1:8200/access/readiness"
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

readiness 检测使用 Supervisor Access Agent 的 `/access/readiness`。产品路径
不 attach bare Kernel。

## 后台进程语义

launcher 启动的 Supervisor 是独立于 CLI 的后台进程。关闭 CLI 不应停止
Kernel runtime。

Linux v1 不依赖 systemd。launcher 使用普通 detached process 语义后台启动
Supervisor：

```go
cmd := exec.Command(
    kernelPython,
    "-m", "kernel.supervisor",
    "--access-port", port,
    "--state-dir", stateDir,
    "--config-dir", configDir,
    "--workspace", cwd,
)

cmd.Dir = cwd
cmd.Env = buildKernelEnv()
cmd.Stdout = supervisorStdoutLog
cmd.Stderr = supervisorStderrLog
cmd.Stdin = nil
cmd.SysProcAttr = &syscall.SysProcAttr{
    Setsid: true,
}

cmd.Start()
cmd.Process.Release()
```

启动成功不能以 `cmd.Start()` 为准，必须等 readiness：

```text
GET http://127.0.0.1:<port>/access/readiness
```

至少需要：

```json
{
  "process_ready": true,
  "hub_ready": true,
  "primary_registered": true,
  "default_route_ready": true
}
```

如果 readiness 超时，launcher 非零退出，不启动 CLI，并打印 stdout/stderr
日志路径。

平台注意事项：

| 平台 | 启动行为 |
|---|---|
| Windows | 使用 `CREATE_NEW_PROCESS_GROUP`；正式安装包中尽量避免弹出额外 console window。 |
| macOS | 从 CLI process group detached；日志写入 state dir。未来可以加 LaunchAgent，但 v1 不要求。 |
| Linux / WSL2 | `setsid` 创建新的 session/process group；日志写入 state dir。不安装 systemd user unit。 |

v1 不要求 runtime 在 logout 或重启后仍然存活。它只需要在 CLI 退出后继续运行，
并能被同一用户后续终端复用。

`deepcli stop` 在 Linux 上按 process group 停止后台 runtime：

1. 对 `-pgid` 发送 `SIGTERM`。
2. 等待 readiness 失败 / process 消失。
3. 超时后对 `-pgid` 发送 `SIGKILL`。

Supervisor 自己负责优雅 shutdown children；launcher 负责兜底杀进程组。

## Auth 交接

连接认证仍由 Kernel 拥有。产品态 token 存储在 state dir：

```text
~/.local/state/deepcli/runtime/auth_token
```

launcher 在 Supervisor readiness 后读取 token，并通过环境变量传给 CLI：

```text
DEEPCLI_TOKEN=<token>
KERNEL_URL=ws://127.0.0.1:<port>/session
KERNEL_HEALTH_URL=http://127.0.0.1:<port>/
```

CLI 当前已经支持旧环境变量。实现本计划时需要补 `DEEPCLI_TOKEN` 和
`KERNEL_HEALTH_URL`，并保留开发期旧变量 fallback，直到本仓库脚本迁移完成。

## CLI 边界

TypeScript CLI 不应该变成 launcher。它的启动路径应保持为：

1. 从 native launcher 接收 `KERNEL_URL`、`KERNEL_HEALTH_URL`、
   `DEEPCLI_TOKEN`。
2. 连接 ACP WebSocket。
3. 渲染 TUI 或执行 `--print`。

开发时仍然可以直接运行 CLI：

```bash
KERNEL_URL=ws://127.0.0.1:8200/session DEEPCLI_TOKEN=... bun run src/main.ts
```

但用户面对的 `deepcli` 命令应该是 native launcher。

## CLI 发布和交接

CLI 是 bundled artifact，不要求用户安装 Bun。Linux v1 优先尝试 Bun single
executable：

```text
~/.local/share/deepcli/cli/1.0.0/deepcli-cli
```

launcher ensure runtime 后以前台进程执行 CLI：

```bash
DEEPCLI_TOKEN=<token> \
KERNEL_URL=ws://127.0.0.1:<port>/session \
KERNEL_HEALTH_URL=http://127.0.0.1:<port>/ \
~/.local/share/deepcli/cli/1.0.0/deepcli-cli "$@"
```

CLI 退出码就是 `deepcli` 默认命令的退出码。CLI 退出不停止 Supervisor。

如果 Bun single executable 对 TUI、资源文件或动态 import 不稳定，fallback
artifact 是 bundled Bun runtime + JS bundle：

```text
~/.local/share/deepcli/cli/1.0.0/
├── bun
├── dist/main.js
└── assets/
```

launcher 对两种 artifact 暴露同一个 `cliCommand`：

```text
single executable: [deepcli-cli, ...args]
bundled bun:       [bun, dist/main.js, ...args]
```

CLI 不做：

- runtime 单例；
- 端口选择；
- Kernel 进程启动/停止；
- token 文件读取；
- Kernel state / SQLite 读取。

CLI 要做：

- 从环境变量读取连接信息；
- 建立 ACP WebSocket；
- 支持 `--print` 和 TUI 两种前台模式；
- 展示 health / reconnect 状态时使用 `KERNEL_HEALTH_URL`；
- 在连接断开时重连当前 launcher 提供的 URL，不自行寻找其它 Kernel。

当前验证结果：

- `cd src/cli && bun build src/main.ts --target=bun --outdir /tmp/deepcli-cli-build-check`
  可以生成普通 JS bundle。
- `cd src/cli && bun build src/main.ts --target=bun --compile --outfile /tmp/deepcli-cli-check`
  可以生成 Linux single executable；当前大小约 99MB。
- `/tmp/deepcli-cli-check --help` 可以正常输出 CLI usage。

因此 Linux launcher 可以先以 Bun single executable 为目标实现；但完整 TUI
artifact 仍需要在安装包验收中跑真实 PTY smoke，确认资源文件、动态 import、
终端 raw mode 等路径在 single executable 中稳定。如果失败，切换到 bundled
Bun runtime + JS bundle。

## Linux v1 可实现性检查

已确认可以开始写 Linux launcher 和 install.sh，但需要先完成 Phase 0 的几个
本仓库契约补齐。

已经可用：

- Kernel wheel 可以构建：
  `cd src/kernel && uv build --wheel --out-dir /tmp/deepcli-kernel-wheel-check`
  生成 `mustang_kernel-1.0.0-py3-none-any.whl`。
- CLI 普通 bundle 和 Bun single executable 都可以构建。
- Supervisor 已支持 `--access-port`、`--state-dir`、`--workspace`，可以作为
  launcher 后台启动的主进程。
- Access readiness endpoint 已存在，launcher 可用
  `/access/readiness` 做真实 ready gate。

必须补齐：

- Kernel package 名称仍是 `mustang-kernel`，产品发布前需要决定是否改成
  `deepcli-kernel`，或在 v1 wheel artifact 中保留兼容名但由 manifest 映射。
- Supervisor CLI 还没有 `--config-dir`；Kernel lifespan 当前仍默认读取旧 home
  config/state 位置。产品态需要支持 `~/.config/deepcli` 和
  `~/.local/state/deepcli`。
- Supervisor primary runtime 当前仍把 agent state 写到
  `Path.home() / ".mustang" / "agents" / "primary"`；需要改成从
  SupervisorConfig 派生的产品 state dir。
- CLI 当前代码仍主要读取 `MUSTANG_TOKEN` / `KERNEL_PORT`，需要补
  `DEEPCLI_TOKEN` / `KERNEL_HEALTH_URL`，并确认 `KERNEL_URL` 总是包含
  `/session`。
- `deepcli doctor` 还不存在；install.sh 第一版可以先用 `deepcli --help` 和
  `deepcli kernel start/status` smoke 代替，或把 doctor 放进 launcher Phase 1。

结论：Linux launcher 和安装脚本现在可以开始实现骨架、下载布局、lock、端口
选择、detached Supervisor、readiness probe 和 CLI exec；但真正产品态安装包
验收前，必须先完成上面的 state/config/env 契约修正。

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
- CLI config loading 增加 `DEEPCLI_TOKEN`、`KERNEL_HEALTH_URL` 支持。
- 确保 Supervisor runtime 文件包含足够的 Access Agent 字段，launcher attach
  时不需要猜。
- Kernel/Supervisor 支持 DeepCLI 产品态 `state-dir` / `config-dir`，避免写入旧
  兼容目录。

### Phase 1 — Launcher 骨架

- 创建 `launcher` 子仓库。
- 实现 Windows/macOS/Linux 的 state root 发现。
- 实现 `deepcli status`：解析 runtime 文件并 probe readiness。
- 实现 user lock 抽象和平台测试。

### Phase 2 — Ensure Runtime

- 实现单例 ensure 算法。
- 实现默认端口和 fallback 端口选择。
- 在开发模式下 detached 启动 Supervisor。
- 把 `KERNEL_URL`、`KERNEL_HEALTH_URL`、`DEEPCLI_TOKEN` 传给 CLI。

### Phase 3 — Stop/Restart 和日志

- 实现 `deepcli stop`。
- 按平台实现 process-tree termination。
- 增加日志捕获和 `deepcli kernel logs`。

### Phase 4 — 安装包模式

- 实现 Linux `install.sh` 用户级安装路径。
- 发布 launcher binary、Kernel wheel/lock、CLI artifact、manifest/checksums。
- 用 `uv` 创建 Kernel Python 3.13 venv 并安装 wheel。
- bundle 或定位 CLI artifact。
- 增加 release builds：
  - Linux x64
  - Linux arm64
  - Windows/macOS 后续补齐

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

- CLI artifact 最终是否能稳定使用 Bun single executable；如果不能，采用
  bundled Bun runtime + JS bundle。
- macOS 未来是否需要 LaunchAgent 做 login persistence，还是 v1 保持
  on-demand 后台进程？
- Windows 正式安装包未来是否需要 tray/Home Screen？这应该独立于命令行
  launcher。
- 自动更新 `deepcli update` 的 manifest 签名、rollback 和保留版本数量。

## 验收标准

- Linux fresh install 后，用户无需手动启动 Kernel，直接运行
  `deepcli` 即可进入 CLI。
- 两个终端同时运行 `deepcli` 时，连接到同一个用户 runtime。
- 非 DeepCLI 进程占用 `8200` 时，默认启动仍然成功。
- CLI 保持 ACP-only，不接管 Kernel 进程监督。
- launcher 子仓库拥有所有平台相关的进程、锁、打包和服务管理代码。
