# Linux 一键安装计划

状态：**Linux amd64 核心实现已落地；线上 release 发布待 tag 验证**

日期：**2026-05-07**

归属：DeepCLI Linux v1 发布 / 安装路径，承接
[`launcher-subrepo-plan.md`](launcher-subrepo-plan.md) 的 Linux 安装部分。

## 目标

提供一个类似 oh-my-zsh 的 Linux 一键安装入口：

```bash
curl -fsSL https://<release-host>/install.sh | sh
```

第一版不做 `.deb` / `.rpm` / systemd user service，也不把 Kernel 打成
wheel。目标是把当前开发态的运行方式变成“可下载、可解压、可运行”的用户级
安装包。

用户安装后只需要运行：

```bash
deepcli
```

第一版只支持 Linux x86_64 / amd64。Linux arm64 后续再补，不进入当前计划的
实现和验收范围。

## 核心决策

### 1. Kernel 不打 wheel

Kernel 以源码运行时包发布，形态接近当前 checkout 里的 `src/kernel/`：

```text
kernel/
├── pyproject.toml
├── uv.lock
└── kernel/
```

安装时在 DeepCLI 自己的 release 目录内创建私有 venv，然后用这个 venv
运行：

```bash
<release>/kernel/.venv/bin/python -m kernel.supervisor
```

这样保持当前 dev 模式的简单性，不引入 Python wheel 发布、包索引、entrypoint
安装等额外复杂度。

### 2. CLI 必须预编译

假设用户机器没有 Node / npm / Bun。CLI 不能要求用户安装 JavaScript
工具链。

release 构建阶段把 CLI 编译成 Linux 单文件可执行物：

```text
cli/deepcli-cli
```

launcher 直接执行这个 artifact。用户侧不运行 `bun install`，不运行
`npm install`，也不需要全局 Bun。

### 3. 私有 uv，不污染用户环境

安装器不能假设用户已有 `uv`，也不能把 `uv` 安装到用户 PATH 上。

DeepCLI 下载或复用一个私有 `uv` binary：

```text
~/.local/share/deepcli/tools/uv/<uv-version>/uv
```

规则：

- 不写 `~/.local/bin/uv`
- 不修改 `.bashrc` / `.zshrc`
- 不覆盖用户自己安装的 `uv`
- installer / launcher 调用 `uv` 时使用绝对路径

`uv` 只服务 DeepCLI 自己的 Kernel venv 和必要的 Python bootstrap。

### 4. venv 完全私有

每个 DeepCLI release 拥有自己的 Kernel venv：

```text
~/.local/share/deepcli/releases/<version>/kernel/.venv/
```

安装器不碰：

- 用户项目里的 `.venv`
- 系统 Python site-packages
- 用户 PATH 上的 Python 工具

主流 Linux 通常已有 `python3`。安装器可以用系统 `python3` 做轻量 bootstrap
和版本检查，但 Kernel 实际运行不能依赖系统 Python。

DeepCLI 必须用私有 `uv` 安装 managed Python，并把 managed Python 保持在
DeepCLI 自己目录下。Kernel venv 一律基于这份 managed Python 创建，避免不同
发行版的系统 Python 版本、编译选项、site-packages 或 distro patch 影响运行。

## 发布物形态

GitHub Release 至少包含：

```text
deepcli-linux-amd64.tar.gz
install.sh
checksums.txt
manifest.json
```

每个 tarball 内部建议布局：

```text
deepcli-<version>-linux-<arch>/
├── VERSION
├── kernel/
│   ├── pyproject.toml
│   ├── uv.lock
│   └── kernel/
├── cli/
│   └── deepcli-cli
├── launcher/
│   └── deepcli
└── assets/
    └── welcome-logo.txt
```

`kernel/` 是源码运行时目录；`cli/deepcli-cli` 是预编译单文件；`launcher/deepcli`
是用户最终运行的 Bash launcher。

## 用户安装布局

默认只写用户目录：

```text
~/.local/bin/deepcli
~/.local/share/deepcli/
├── tools/
│   └── uv/<uv-version>/uv
└── releases/
    └── <version>/
        ├── VERSION
        ├── kernel/
        │   ├── .venv/
        │   ├── pyproject.toml
        │   ├── uv.lock
        │   └── kernel/
        ├── cli/
        │   └── deepcli-cli
        ├── launcher/
        │   └── deepcli
        └── assets/
~/.local/state/deepcli/
└── runtime/
~/.config/deepcli/
```

稳定入口：

```text
~/.local/bin/deepcli -> ~/.local/share/deepcli/releases/<version>/launcher/deepcli
```

安装器只提示用户把 `~/.local/bin` 加入 PATH；第一版不自动修改 shell rc 文件。

## 在线 installer 流程

`install.sh` 的职责：

1. 检测 Linux / WSL2 / CPU 架构。
2. 解析目标版本，默认 `latest`。
3. 下载对应 tarball、`manifest.json`、`checksums.txt`。
4. 校验 sha256。
5. 解压到临时目录。
6. 原子移动到：

   ```text
   ~/.local/share/deepcli/releases/<version>/
   ```

7. 准备私有 `uv`：
   - 如果已有匹配版本的 DeepCLI 私有 `uv`，复用。
   - 否则下载 standalone `uv` binary 到 DeepCLI tools 目录。
8. 准备 Kernel venv：
   - 用私有 `uv` 安装 managed Python。
   - 基于 managed Python 创建 release 内私有 venv。
   - release 构建阶段为 staged Kernel runtime 生成发布包内的 `uv.lock`。
   - 在 `<release>/kernel/` 中执行 `uv sync --locked --no-dev`。
9. 更新 `~/.local/bin/deepcli` symlink。
10. 检查 `~/.local/bin` 是否在 PATH。
11. 运行 smoke：

    ```bash
    ~/.local/bin/deepcli --help
    ```

12. 如果安装前已有 packaged runtime，按 launcher / Supervisor 控制路径停止或提示重启。

## Launcher 行为

安装后的 launcher 需要支持两种布局。

开发 checkout：

```bash
scripts/run-kernel.sh
cd src/cli && bun run src/main.ts
```

正式安装包：

```bash
<release>/kernel/.venv/bin/python -m kernel.supervisor
<release>/cli/deepcli-cli
```

正式安装包模式不调用用户 PATH 上的 `uv`、`bun`、`node`、`npm`。

## GitHub CI

新增 release workflow：

触发：

```yaml
on:
  push:
    tags:
      - "v*"
```

构建矩阵：

```text
linux-amd64
```

每个 arch 做：

1. checkout 源码。
2. 安装构建机需要的 Bun / uv。
3. 运行基础测试。
4. 编译 CLI 单文件。
5. stage Kernel 源码运行时目录。
6. stage launcher 和 assets。
7. 生成 `deepcli-linux-<arch>.tar.gz`。
8. 生成 `manifest.json` 和 `checksums.txt`。
9. 上传到 GitHub Release。

注意：构建机可以安装 Bun / uv；用户机器不能被要求安装 Bun / Node / npm，也不能被
污染用户自己的 `uv`。

Linux arm64 的构建矩阵和 artifact 命名后续追加；不要为了 arm64 在第一版引入
交叉编译、QEMU、runner 选择或额外发布复杂度。

## 分支与版本发布策略

DeepCLI 采用三层发布流，不再直接 push 到 `main`。

```text
dev
  ↓ bump prerelease version
feature-freeze
  ↓ promote final version
main
  ↓ tag
GitHub Release assets
```

### 分支职责

| 分支 | 职责 | 版本形态 | 是否发正式 release |
|---|---|---|---|
| `dev` | 日常集成分支。所有功能、修复、文档先进入这里。 | 跟随当前 Kernel 版本，不代表发布承诺。 | 否 |
| `feature-freeze` | 固定发布冻结分支。只允许 bug fix、验证修复、release 文档和版本 bump。 | prerelease：`1.1.0a1`、`1.1.0b1`、`1.1.0rc1`。 | 可发 prerelease assets |
| `main` | 稳定发布分支。只接收通过 freeze 验收的最终版本。 | final：`1.1.0`。 | 是 |

`main` 应开启 branch protection：禁止直接 push，只允许从对应
`feature-freeze` 合并。

`dev` 是默认开发目标。日常 PR / push 进入 `dev`，不是 `main`。

### 版本权威

产品版本的唯一 source of truth 是 Kernel：

```text
src/kernel/kernel/__init__.py
```

```python
__version__ = "1.1.0a1"
```

CLI、launcher、release manifest 都必须跟这个 Kernel version 同步。仓库根目录
不再新增 `VERSION` 文件。

需要同步/校验的投影：

```text
src/cli/package.json
src/cli/src/compat/utils.ts
src/cli/src/acp/client.ts
release tarball VERSION
manifest.json version
GitHub tag
```

### 版本号语义

遵循 Python / PEP 440 风格 prerelease 后缀：

```text
1.1.0a1
1.1.0a2
1.1.0b1
1.1.0b2
1.1.0rc1
1.1.0rc2
1.1.0
```

语义：

- `aN`：alpha。功能已进入 freeze 分支，但仍可能有明显缺口或较大 bug。
- `bN`：beta。功能集合基本稳定，主要修 bug 和体验问题。
- `rcN`：release candidate。只修阻断发布的问题。
- final：去掉后缀，进入 `main` 后打正式 tag。

示例流程：

```text
dev                         version: 1.0.0 或下一开发态
feature-freeze              version: 1.1.0a1
feature-freeze              version: 1.1.0a2
feature-freeze              version: 1.1.0b1
feature-freeze              version: 1.1.0rc1
main                        version: 1.1.0
tag                         v1.1.0
```

### Release 脚本

新增单入口脚本：

```text
scripts/release.sh
```

使用说明见 [`../workflow/release-tool.md`](../workflow/release-tool.md)。

职责：

- `scripts/release.sh read-version`：只读取 `src/kernel/kernel/__init__.py`。
- `scripts/release.sh check-version`：校验 Kernel / CLI / ACP client 投影一致。
- `scripts/release.sh feature-freeze [final-version]`：
  - 只能在 `dev` 上运行。
  - 自动显示当前 Kernel version。
  - 如果没有传 `final-version`，交互式询问目标 final version，例如 `1.1.0`。
  - 自动追加 `a1` 后缀，bump 到 `1.1.0a1`。
  - 从 `dev` 重置固定分支 `feature-freeze`。
  - commit 并 push `feature-freeze`。
- `scripts/release.sh release`：
  - 只能在 `feature-freeze` 上运行。
  - 自动读取当前 prerelease version，例如 `1.1.0rc2`。
  - 自动删除后缀，bump 到 final version，例如 `1.1.0`。
  - commit，fast-forward `main`，先 push `main`，再打 tag `v1.1.0` 并 push tag。

`feature-freeze` 使用固定分支名，所以脚本 push freeze 分支时使用
`--force-with-lease`，避免旧 freeze 历史阻止下一轮发布冻结。正式 release 到
`main` 使用 fast-forward merge；如果 `main` 不能 fast-forward，脚本失败，
需要先人工处理分支关系。正式 tag 必须在远端 `main` push 成功之后再创建和
push，避免 tag CI 先于稳定分支更新触发。

### CI 规则

普通 CI：

- `dev`、`feature-freeze`、`main` 都跑测试。
- 所有分支都跑 `scripts/check-version.sh`。
- `main` 上的版本必须是 final semver，不能包含 `a` / `b` / `rc`。

Prerelease assets：

- tag 形如 `v1.1.0a1`、`v1.1.0b1`、`v1.1.0rc1` 时允许生成 GitHub prerelease
  assets。
- CI 必须校验 tag version == Kernel version。
- GitHub Release 标记为 prerelease。

正式 release assets：

- 只能从 `main` 的 tag 触发，例如 `v1.1.0`。
- CI 必须校验：
  - 当前 ref 是 tag。
  - tag version == Kernel version。
  - Kernel version 是 final semver，不带 prerelease 后缀。
  - 所有版本投影一致。
- 通过后生成：

```text
deepcli-linux-amd64.tar.gz
install.sh
manifest.json
checksums.txt
```

### 安装器 latest 语义

默认一键安装命令应安装最新 **正式** release：

```bash
curl -fsSL https://github.com/<owner>/<repo>/releases/latest/download/install.sh | sh
```

Prerelease 需要用户明确指定版本：

```bash
DEEPCLI_VERSION=1.1.0rc1 sh install.sh
```

或直接使用 prerelease tag asset URL。

### 冻结分支约束

进入 `feature-freeze` 后：

- 不再接受新功能。
- 只接受：
  - bug fix
  - release blocker 修复
  - installer / packaging 修复
  - 文档和验收说明
  - 版本 bump
- 每次 bump prerelease 后必须跑安装包 smoke：

```text
build tarball -> isolated HOME install -> deepcli kernel start -> readiness -> stop
```

## 升级与回滚

安装新版本时：

- 新版本解压到新的 `releases/<version>/` 目录。
- venv 在新 release 内单独创建。
- `~/.local/bin/deepcli` symlink 最后切换。

旧 release 可以暂时保留，便于回滚。第一版可以只提供手动回滚：

```bash
ln -sfn ~/.local/share/deepcli/releases/<old>/launcher/deepcli ~/.local/bin/deepcli
```

自动清理旧版本可以后续再做。

## 卸载

第一版 launcher 保留用户态卸载命令：

```bash
deepcli --uninstall
```

卸载默认移除：

- `~/.local/bin/deepcli`
- 当前 release 目录
- DeepCLI 私有 `uv` tools 目录（如果没有其他 installed release 使用）

卸载默认保留：

- `~/.config/deepcli/`
- `~/.local/state/deepcli/`
- sessions / logs / runtime 历史

## 非目标

- 不做 `.deb` / `.rpm`。
- 不做 systemd user service。
- 不把 Kernel 打成 wheel。
- 不要求用户安装 Node / npm / Bun。
- 不安装全局 `uv`。
- 不修改用户 shell rc 文件。
- 不支持 Windows / macOS native installer；这两个平台后续单独设计。

## 待审阅问题

1. Kernel 依赖 sync 当前采用 release tarball 内生成的 lock 和
   `uv sync --locked --no-dev`。后续是否需要把 release lock 作为可审查 artifact
   单独上传？
2. 私有 `uv` 版本是否固定在 release manifest 中，还是 installer 固定一个
   当前推荐版本？
3. release tarball 是否保留 tests/docs，还是只 stage runtime 必需文件？
4. 安装前已有 runtime 正在运行时，第一版是自动停启，还是提示用户执行
   `deepcli restart`？
5. `latest` 解析直接用 GitHub Releases API，还是发布一个稳定的
   `latest/manifest.json` 静态入口？
