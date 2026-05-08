# Release 工具使用说明

`scripts/release.sh` 是 DeepCLI 的发布分支和版本号辅助工具。

它不决定产品版本来源。产品版本唯一权威仍然是 Kernel：

```text
src/kernel/kernel/__init__.py
```

脚本负责把 Kernel version 投影到 CLI，并按固定分支流执行发布动作。

## 完整流程

这是一轮功能从开发到正式发布的完整路径。每一步后面都标出应该看本文档的哪个
section。

### 1. 日常功能开发进入 `dev`

日常功能、修复、文档都先进入 `dev`。

典型流程：

```bash
git checkout dev
git pull --ff-only origin dev
git checkout -b <feature-branch>
```

功能完成后，开 PR 合并回 `dev`。`main` 不接收日常功能 PR。

合并到 `dev` 前至少确认版本投影没有被误改：

```bash
scripts/release.sh check-version
```

对应 section：

- [检查版本投影](#检查版本投影)
- [分支模型](#分支模型)

### 2. 从 `dev` 进入 `feature-freeze`

当决定准备一个新版本时，从最新 `dev` 开始冻结。

```bash
git checkout dev
git pull --ff-only origin dev
scripts/release.sh feature-freeze
```

脚本会显示当前版本号，询问目标 final version，例如 `1.1.0`，然后自动设置为
`1.1.0a1` 并推送固定分支 `feature-freeze`。

对应 section：

- [开始 feature freeze](#开始-feature-freeze)
- [版本号规则](#版本号规则)
- [安全开关](#安全开关)

### 3. 冻结期只修发布问题

进入 `feature-freeze` 后，不再接收新功能。只修：

- bug fix
- release blocker
- installer / packaging 问题
- release 文档
- 版本 bump

每次修复都进入 `feature-freeze`。如果需要从 `a1` 变成 `a2`、`b1`、`rc1`，
按冻结期 bump 规则处理，并重新跑安装包 smoke。

对应 section：

- [冻结期 bump](#冻结期-bump)
- [失败条件](#失败条件)
- [注意事项](#注意事项)

### 4. 从 `feature-freeze` 发布到 `main`

当当前 freeze 版本已经达到可发布状态，例如 `1.1.0rc2`，执行正式发布：

```bash
git checkout feature-freeze
git pull --ff-only origin feature-freeze
scripts/release.sh release
```

脚本会把 `1.1.0rc2` 自动改成 `1.1.0`，fast-forward 到 `main`，先 push
`main`，再创建并 push `v1.1.0` tag。

对应 section：

- [正式发布](#正式发布)
- [版本号规则](#版本号规则)
- [注意事项](#注意事项)

### 5. GitHub tag CI 生成安装资产

`v1.1.0` tag push 后，GitHub Release CI 负责生成安装资产：

```text
deepcli-linux-amd64.tar.gz
install.sh
manifest.json
checksums.txt
```

对应 section：

- [正式发布](#正式发布)
- [注意事项](#注意事项)

## 分支模型

DeepCLI 使用三个长期分支：

```text
dev
  ↓
feature-freeze
  ↓
main
```

职责：

- `dev`：日常开发集成分支。
- `feature-freeze`：固定发布冻结分支。进入后只做 bug fix、release blocker、
  installer / packaging 修复和版本 bump。
- `main`：稳定发布分支。只接收最终 release commit。

`main` 不直接开发，不直接 push。正式 tag 只在 release commit 已经进入
远端 `main` 后创建。

## 命令

### 查看当前版本

```bash
scripts/release.sh read-version
```

读取 Kernel version，例如：

```text
1.0.0
```

### 检查版本投影

```bash
scripts/release.sh check-version
```

校验以下位置是否和 Kernel version 一致：

```text
src/cli/package.json
src/cli/src/compat/utils.ts
src/cli/src/acp/client.ts
```

成功输出：

```text
version-ok 1.0.0
```

## 开始 feature freeze

从 `dev` 进入发布冻结：

```bash
git checkout dev
git pull --ff-only origin dev
scripts/release.sh feature-freeze
```

脚本会：

1. 确认当前分支是 `dev`。
2. 确认 worktree 干净。
3. 显示当前 Kernel version。
4. 询问目标 final version，例如 `1.1.0`。
5. 自动 bump 到 `1.1.0a1`。
6. 从 `dev` 创建 / 重置固定分支 `feature-freeze`。
7. 更新 Kernel / CLI 版本投影。
8. commit：

   ```text
   release: start feature freeze 1.1.0a1
   ```

9. push：

   ```bash
   git push --force-with-lease origin feature-freeze
   ```

也可以直接传 final version：

```bash
scripts/release.sh feature-freeze 1.1.0
```

这会直接生成 `1.1.0a1`。

## 冻结期 bump

冻结期如果需要从 `a1` 到 `a2`、`b1`、`rc1`，当前第一版脚本还没有单独命令。
可以先用脚本内部同样的版本投影规则后续补 `bump` 子命令。

当前建议：

```bash
scripts/release.sh check-version
```

确保每次手动 bump 后投影一致。

## 正式发布

从 `feature-freeze` 发布到 `main`：

```bash
git checkout feature-freeze
git pull --ff-only origin feature-freeze
scripts/release.sh release
```

脚本会：

1. 确认当前分支是 `feature-freeze`。
2. 确认 worktree 干净。
3. 读取当前 prerelease version，例如：

   ```text
   1.1.0rc2
   ```

4. 自动去掉 prerelease 后缀，得到：

   ```text
   1.1.0
   ```

5. 更新 Kernel / CLI 版本投影。
6. commit：

   ```text
   release: v1.1.0
   ```

7. push `feature-freeze`。
8. checkout `main`。
9. `git pull --ff-only origin main`。
10. `git merge --ff-only feature-freeze`。
11. push `main`。
12. 创建 tag：

    ```bash
    git tag v1.1.0
    ```

13. push tag：

    ```bash
    git push origin v1.1.0
    ```

tag push 会触发 GitHub Release CI 生成：

```text
deepcli-linux-amd64.tar.gz
install.sh
manifest.json
checksums.txt
```

## 版本号规则

正式版本：

```text
1.1.0
```

冻结期 prerelease：

```text
1.1.0a1
1.1.0a2
1.1.0b1
1.1.0b2
1.1.0rc1
1.1.0rc2
```

`feature-freeze` 命令只自动创建第一版 `a1`。

`release` 命令只接受当前 Kernel version 是 prerelease：

```text
<major>.<minor>.<patch>aN
<major>.<minor>.<patch>bN
<major>.<minor>.<patch>rcN
```

然后自动发布为 final：

```text
<major>.<minor>.<patch>
```

## 安全开关

默认会在 push / tag 前询问确认。

自动确认：

```bash
DEEPCLI_RELEASE_YES=1 scripts/release.sh feature-freeze 1.1.0
```

指定 remote：

```bash
DEEPCLI_RELEASE_REMOTE=origin scripts/release.sh release
```

## 失败条件

脚本会在这些情况下停止：

- 当前分支不符合命令要求。
- worktree 不干净。
- 版本号不是合法 final semver 或 prerelease semver。
- CLI 版本投影和 Kernel 不一致。
- `main` 不能 fast-forward 到 `feature-freeze`。
- push 或 tag 失败。

## 注意事项

- `feature-freeze` 是固定分支，所以开始新一轮 freeze 时会
  `push --force-with-lease origin feature-freeze`。
- `release` 会切换到 `main` 分支。
- 正式 tag 在远端 `main` push 成功之后才创建并 push。
- 脚本不替代 release smoke。进入 `feature-freeze` 后仍需按计划运行安装包
  smoke：build tarball -> isolated HOME install -> start -> readiness -> stop。
