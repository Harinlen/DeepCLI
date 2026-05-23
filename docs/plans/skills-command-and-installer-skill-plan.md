# `/skills` Command 与内置 Skill Installer 计划

状态: implemented
日期: 2026-05-22
实现日期: 2026-05-23

> 2026-05-24 audit note: `/skills` 与 `/skill:<name>` 本身已经实现并通过
> real-kernel probe。随后发现的 broader slash-command surface 漂移也已闭合：
> Kernel CommandManager catalog、active CLI slash execution surface、CLI
> autocomplete surface、Primary Runtime ACP dispatcher、real-kernel E2E 覆盖
> 已重新对齐。闭合状态记录在
> [`docs/kernel/subsystems/commands.md`](../kernel/subsystems/commands.md)。
> 后续实现仍不得把 catalog presence 当作 command completion proof。

## 背景

DeepCLI 现在已经有 SkillManager、SkillTool、user-invocable skill command
projection、ResourceStore-backed global skill declarations，以及可选的
Claude Code `.claude/skills/` 兼容层。但还没有一个用户可见的 `/skills`
管理入口，也没有安装/导入技能库的产品路径。

当前代码还已经有一个 **bundled skill 机制雏形**：

- `kernel.agents.mustang.skills.bundled.BundledSkillDef`
- `register_bundled_skill()` / `get_bundled_skills()`
- 一个程序化注册的 `/loop` bundled skill

但这个机制目前没有闭合到 SkillManager startup：
`SkillManager._discover_startup_skills()` 仍然传 `bundled_skills=[]`，所以
bundled registry 不是实际发现层的一部分。本计划不是从零设计 builtin skill，
而是先把现有 bundled skill 机制接进 SkillManager，再在这个机制上新增
`skill-installer`。

这不能简单做成内核里的“直接安装 skill”命令。原因是 DeepCLI 的 skill 目标
不是单一格式：

- 要兼容 Claude Code 的 `.claude/skills/<name>/SKILL.md`。
- 要兼容 Codex 的 `$CODEX_HOME/skills` skill 目录语义和系统 skill 思路。
- 要吸收 OpenClaw / Hermes 的 registry、browse、install、update 经验。
- DeepCLI 自己的 SkillManager 已经把 skill 定义成 Markdown 指令 + 元数据，
  不是可执行插件；安装是软件供应链行为，不应隐藏在普通激活路径里。

所以本计划采用 Codex 形式：**安装能力本身是一个内置 skill**。`/skills`
负责管理面入口、目录查看和引导；真正的搜索、导入、迁移、安装动作由内置
`skill-installer` skill 教会 agent 如何做，并在需要时调用现有工具。

## 参考结论

### Claude Code

Claude Code 有 `/skills`，但它是查看/管理可用 skills 的 UI，不是一等
`/skills install`。创建或引入 skill 主要靠写入 `.claude/skills/`，官方
推荐的 skill 创建能力通过 plugin，例如 `/plugin install skill-creator...`。

DeepCLI 借鉴：

- `/skills` 是稳定的管理入口。
- skill 文件仍是 `skill-name/SKILL.md` 目录格式。
- 不把用户输入 `/skills install` 直接等同于内核写目录。

不借鉴：

- 不把 “安装更多 skill” 依赖 Claude plugin 生态。

### Codex

Codex 的关键形式是系统内置 `skill-installer` skill。它通过 helper scripts
列出 curated/experimental skills，并从 GitHub repo/path 安装到
`$CODEX_HOME/skills/<skill-name>`。用户看到的是 agent 能力，不是一个硬编码
slash command。

DeepCLI 借鉴：

- 内置 `skill-installer` 是安装能力的主实现载体。
- 安装说明、registry 选择、GitHub path、私有 repo fallback、重启/刷新提示
  都写在 skill body/supporting files 中。
- Kernel 只提供必要的安全边界和 catalog projection，不把所有 registry 逻辑
  烧进 CommandManager。

### OpenClaw

OpenClaw 有一等 `openclaw skills install <slug>`，也有 gateway
`skills.install`。它适合 ClawHub 单一 registry / workspace skill 目录模型。

DeepCLI 借鉴：

- `/skills list/search/info/check/update` 这些 UX 词汇。
- 安装 provenance、版本、target dir、force/update/audit 的管理概念。
- install 后进入 workspace/project skill layer，而不是污染 kernel bundled
  skills。

不直接照搬：

- 不把 ClawHub 作为唯一 source。
- 不把 gateway `skills.install` 作为安装主路径；gateway 只能复用同一
  `/skills`/skill-installer 语义，不能绕过 agent-native 安装流。

### Hermes

Hermes 有最完整的 `hermes skills` 和 `/skills`：browse、search、install、
inspect、list、check、update、audit、uninstall、tap、snapshot/import/export，
并支持 URL、GitHub、well-known、ClawHub、optional-skills。

DeepCLI 借鉴：

- `/skills` 应覆盖 browse/search/install/inspect/list/check/update/audit 的
  管理面。
- direct URL / GitHub / registry tap 是真实需求。
- optional / official / community source 要体现在 provenance 中。

不照搬：

- 不一次性实现 Hermes 全量 hub。
- 不在 TUI 内构建完整 marketplace UI；通过 agent skill + ACP catalog 闭合。

## 目标

1. 新增 `/skills` 作为 Kernel-owned command catalog 中的 builtin command。
2. 接通现有 bundled skill 机制，并新增 DeepCLI bundled/system skill：
   `skill-installer`。
3. 保持安装逻辑 agent-native：由 `skill-installer` 指导 agent 搜索、下载、校验、
   写入或生成计划。
4. 支持 skill 安装新的 slash command，但 command name 必须加 `skill:`
   prefix。
5. 不破坏现有 Claude Code-compatible 裸 `/skill-name` 激活路径；交付时同时
   提供 canonical `/skill:<name>` 和兼容 alias。

## 非目标

- 不实现完整远程 marketplace。
- 不把 OpenClaw ClawHub 或 Hermes Skills Hub 内置为唯一服务。
- 不让 CLI 直接扫描本地 skill 目录。
- 不让 Access/CLI 绕过 ACP 写 ResourceStore 或 skill declaration。
- 不把 bundled kernel skill 与用户安装 skill 混在一个可写目录。

## 用户体验

### `/skills`

无参数时展示当前 skills 管理面：

```text
/skills
```

必须展示：

- installed: 当前可见 skills，按 source/layer 分组。
- commands: skill-contributed slash commands，显示为 `/skill:<name>`。
- installer: 提示可用 `/skills install ...` 或激活内置 `skill-installer`。
- compat: Claude Code compat 是否开启，当前是否扫描 `.claude/skills/`。

### `/skills list`

列出 SkillManager 当前 registry 视图：

- model-invocable / user-invocable。
- source: project / external / user / bundled / mcp。
- layer detail: native / claude-compat / resource-store-managed。
- path 或 resource key。
- disabled / requires missing / setup needed 状态。

### `/skills inspect <name>`

展示一个 skill 的 manifest 摘要，不直接输出完整 body：

- name、description、when-to-use。
- source/layer/path。
- supported files。
- allowed tools / requires / setup / config。
- exposed command: `/skill:<name>` 或 none。

### `/skills search <query>`

搜索可安装 skill source。它必须激活 `skill-installer`：

```text
activate skill-installer with args:
search <query>
```

本实现的 search 不做完整 marketplace UI。它必须覆盖：

- bundled well-known source index。
- GitHub repo/path source 的 manifest quick inspect。
- 已安装 skills 的 name/description/provenance 搜索。

### `/skills sources`

列出 `skill-installer` 知道的 well-known registries / curated lists。它必须激活
`skill-installer`：

```text
activate skill-installer with args:
sources
```

输出至少包含 source id、kind、URL/path、trust level、是否支持 update。

### `/skills install <source>`

`/skills install` 不由 CommandManager 直接执行安装。它必须解析成一次对
`skill-installer` 的显式激活：

```text
/skills install openai/skills/skill-creator
```

等价语义：

```text
activate skill-installer with args:
install openai/skills/skill-creator
```

然后由当前 agent 按 skill 指令执行：

- 识别 source 类型：GitHub path、URL、本地 path、registry slug。
- 检查目标 layer：project `.mustang/skills` 或 user `~/.deepcli/skills`。
- 下载/复制到临时目录。
- 校验 `SKILL.md` manifest。
- 做安全/重名/provenance 检查。
- 写入目标目录或提出确认。
- 通知 SkillManager refresh / 重启需求。

### `/skills check`

检查已安装且有 provenance 的 skills 是否仍可解析 source，并报告本地修改状态。
它必须激活 `skill-installer`：

```text
activate skill-installer with args:
check [<skill-name>|--all]
```

必须输出：

- skill name。
- target layer/path。
- provenance source/ref/hash。
- local drift 状态。
- upstream reachable 状态（如果 source 支持）。
- setup needed / incompatibility warnings。

### `/skills update <name|--all>`

更新只允许处理有 provenance 的 skills。它必须激活 `skill-installer`：

```text
activate skill-installer with args:
update <skill-name|--all> [--force]
```

规则：

- 默认不覆盖本地修改。
- `--force` 才允许覆盖有 drift 的目标。
- 更新仍走 temp dir -> validate -> scan -> copy/replace -> provenance 的完整路径。
- 更新失败时必须保持原 skill 可用。

### `/skills audit [<name>|--all]`

重新扫描已安装 skills，不下载新内容。它必须激活 `skill-installer`：

```text
activate skill-installer with args:
audit [<skill-name>|--all]
```

必须检查：

- manifest parse。
- path traversal / symlink escape。
- suspicious scripts/templates/references。
- missing bins/env/tools/toolsets。
- provenance 文件是否缺失或损坏。

### `/skills uninstall <name>`

卸载不直接 delete。它必须激活 `skill-installer`：

```text
activate skill-installer with args:
uninstall <skill-name>
```

规则：

- 只能卸载 user/project layer skills。
- 不能卸载 bundled/MCP skills。
- 默认 move 到 archive，而不是 delete。
- archive 后调用 SkillManager refresh。
- commands/list 不再显示 `/skill:<name>`。

Archive 目录：

```text
.mustang/skills/.archive/<skill-name>-<UTC compact timestamp>/
~/.deepcli/skills/.archive/<skill-name>-<UTC compact timestamp>/
```

Archive 记录：

```text
<archived-skill-dir>/.deepcli-skill-archive.json
```

字段：

```json
{
  "archivedAt": "2026-05-23T00:00:00Z",
  "originalPath": "...",
  "skillName": "...",
  "reason": "user-requested",
  "source": "deepcli"
}
```

### `/skills refresh`

触发 SkillManager 重新读取 registry 或告诉用户当前需要新 session/restart。
如果实现上已有 signal/file touched 能力，则优先走真实 refresh，不做假 UI。

## Command Prefix 设计

### 为什么需要 `skill:` prefix

当前 CommandManager 会把 `user_invocable` skills 直接投影为裸命令：

```text
/debug
/commit
/loop
```

这对 Claude Code 兼容有价值，但作为 PDS 软件库会产生三个问题：

1. skill 安装可以带来新 command，裸命令会和 builtin command 撞名。
2. 用户很难区分 `/model` 这种 kernel command 和 `/commit` 这种 skill command。
3. Codex 的 skill / plugin 生态更强调来源边界；DeepCLI 需要保留 provenance。

因此新规则：

```text
skill command name = "skill:<skill-name>"
user input        = "/skill:<skill-name> [args]"
source            = "skill"
```

例子：

```text
/skill:commit
/skill:skill-installer install openai/skills/skill-creator
/skill:openclaw-migration --from ~/.openclaw
```

### 兼容策略

兼容规则：

- 已存在的 naked `/skill-name` 继续工作，用于 Claude Code compatibility 和旧项目。
- 新安装的 DeepCLI-native skills 在 catalog 中必须至少出现 `/skill:<name>`。
- 若裸 `/name` 不撞 builtin，可以作为 deprecated alias，但 UI 优先显示
  `/skill:<name>`。
- `CommandDef.source == "skill"` 的 canonical name 使用 `skill:<name>`。
- `CommandDef` 增加 `aliases` 或 `canonical_name` 字段，裸 `/name` 作为
  compatibility alias。
- `/help` 和 autocomplete 默认隐藏 deprecated naked alias，除非用户开启
  compat view。

`CommandDef` 的 skill metadata 必须稳定：

```python
metadata={
    "kind": "skill",
    "skillName": manifest.name,
    "canonicalCommand": f"skill:{manifest.name}",
    "compatAliases": [manifest.name],
}
```

CLI 和 gateway 只能使用 `metadata["skillName"]` 调用
`_mustang.agent/session/activate_skill`，不能靠切字符串推断 skill name。

### 内置命令保留裸名

这些仍保持裸 slash command：

```text
/skills
/mcp
/agents
/agent
/gateways
/model
/global
/flags
```

即使某个 skill 名叫 `model`，它也只能成为：

```text
/skill:model
```

不能 shadow `/model`。

## 内置 `skill-installer` Skill 设计

### 位置

作为 bundled/system skill，而不是普通 user skill：

```text
src/kernel/kernel/agents/mustang/skills/bundled/skill_installer/
├── __init__.py
├── SKILL.md
├── scripts/
└── references/
```

`SKILL.md` 是 skill body 的唯一来源；helper scripts 和 source index 是真实
supporting files。`__init__.py` 只负责读取该目录并注册到现有 Python bundled
registry，不承载长 prompt 字符串。

### Manifest

```yaml
---
name: skill-installer
description: Install or import DeepCLI-compatible skills from GitHub, URLs, local paths, or known registries.
when-to-use: When the user asks to install, import, migrate, browse, update, or audit skills.
user-invocable: true
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash(git *)
  - Bash(curl *)
  - Bash(uv *)
argument-hint: "<install|search|sources|inspect|update|audit> <source>"
---
```

`allowed-tools` 必须按 ToolAuthorizer 实际规则收紧；这里是设计意图，不是
实现时可以无校验照抄的权限清单。

### Skill body 责任

`skill-installer` 需要明确告诉 agent：

- 默认不要改 `AGENTS.md` 入口文件。
- 优先安装到 project `.mustang/skills/<name>/`，除非用户要求 global。
- 若 source 是 Claude Code skill，保持 `SKILL.md` 兼容字段；必要时复制到
  `.mustang/skills` 并保留 provenance。
- 若 source 是 Codex skill，检查是否依赖 Codex-only tools；不能自动承诺可用。
- 若 source 是 OpenClaw/Hermes skill，检查 frontmatter、scripts、tool
  dependencies，并把不可兼容项写入 install report。
- 安装完成后必须让 SkillManager 重新发现，或告诉用户需要新 session/restart。

### Install Argument Contract

`/skills install` 的用户参数必须完整保留给 `skill-installer`。本实现支持
这些稳定参数：

```text
/skills install <source> [--name <name>] [--ref <ref>] [--project|--global] [--force]
```

规则：

- `<source>` 是 GitHub repo/path、raw URL、local path，或 well-known registry slug。
- 默认 target 是 project `.mustang/skills/<name>/`。
- `--global` target 是 `~/.deepcli/skills/<name>/`。
- `--project` 显式覆盖默认 project target。
- `--name` 覆盖从 manifest/source 推断的目录名，但不能覆盖 manifest `name`
  为另一个值；不一致时安装失败。
- `--ref` 只适用于 GitHub/registry source。
- `--force` 允许替换已存在目标；没有 `--force` 时目标已存在必须失败。

### Helper scripts

参考 Codex，helper scripts 放在 bundled skill 支持文件中，而不是 Kernel
command 代码中：

```text
scripts/install-from-github.py
scripts/install-from-url.py
scripts/install-from-local.py
scripts/list-sources.py
scripts/resolve-source.py
scripts/validate-skill.py
scripts/write-provenance.py
scripts/check-installed.py
scripts/update-installed.py
scripts/audit-installed.py
scripts/archive-skill.py
```

交付必须包含：

1. `validate-skill.py`：检查目录里是否有 `SKILL.md`，解析 frontmatter，输出 JSON。
2. `install-from-github.py`：下载 GitHub repo/path 到目标目录，支持 `--ref`、
   `--dest`、`--name`、`--force`。
3. `install-from-url.py`：下载单个 `SKILL.md` URL，要求或推断 name。
4. `install-from-local.py`：从本地目录复制 skill 到目标目录，必须拒绝 source
   在 target 内、target 在 source 内、symlink escape、绝对覆盖和 hidden
   archive 目录写入。
5. `list-sources.py`：读取 bundled `references/skill-sources.json`，输出可用
   well-known sources。
6. `resolve-source.py`：把 registry slug / curated id 解析成 GitHub path、URL
   或 local path，并输出 provenance source kind。
7. `write-provenance.py`：写入 source URL/ref/hash、target layer、warnings。

新增：

8. `check-installed.py`：读取 provenance，检查 source reachability 和 local drift。
9. `update-installed.py`：按 provenance 更新 skill，默认拒绝覆盖 local drift。
10. `audit-installed.py`：对已安装 skill 重新运行 manifest/path/script/dependency 检查。
11. `archive-skill.py`：把 user/project skill 移入 `.archive/` 并写 archive record。

Supporting references：

```text
references/skill-sources.json
```

`skill-sources.json` 是 bundled 静态索引，不是远程 marketplace。字段：

```json
{
  "schemaVersion": 1,
  "sources": [
    {
      "id": "codex:skill-installer",
      "kind": "official|community|optional|local",
      "label": "Codex skill installer",
      "source": "github|url|local",
      "url": "...",
      "repo": "owner/name",
      "path": "skills/example",
      "ref": "main",
      "trust": "bundled-index|user-provided",
      "supportsUpdate": true
    }
  ]
}
```

Helper scripts 的输入输出必须是 JSON-friendly。正常输出一行 JSON 到 stdout；
错误输出一行 JSON 到 stderr 并返回非零 exit code。这样 agent 可以稳定解析，
测试也可以直接断言结果。

通用成功格式：

```json
{
  "ok": true,
  "action": "install|check|update|audit|archive|validate",
  "skillName": "example",
  "targetPath": "...",
  "warnings": []
}
```

通用失败格式：

```json
{
  "ok": false,
  "action": "install|check|update|audit|archive|validate",
  "error": "machine_readable_error_code",
  "message": "human readable message"
}
```

### Bundled File Extraction

当前 bundled registry 只把 supporting files 描述保存在 `BundledSkillDef.files`，
并不能保证 activation 时脚本已经落盘。本实现必须闭合这个缝：

- `LoadedSkill` 或 bundled-specific wrapper 必须保留 `files` 信息，或
  SkillManager 能按 skill name 找回 bundled files。
- `SkillManager.activate()` 在激活 bundled skill 前必须调用
  `extract_bundled_files(name, files)`。
- extraction 成功后，activation body 里的 base directory 必须指向真实目录。
- extraction 失败时，activation 必须返回 setup/error，不允许给 LLM 一个不存在的
  script path。
- tests 必须证明 `skill-installer` 激活后 helper scripts 确实存在于 bundled root。

### 安全规则

- 下载先进入临时目录，再 validate，再 copy/rename 到目标目录。
- 目标目录已存在默认失败，`--force` 必须显式。
- 禁止路径穿越、绝对目标覆盖、symlink escape。
- scripts/templates/references 可以随 skill 复制，但执行仍受 ToolAuthorizer。
- 安装报告记录 source URL/ref/hash、target layer、warnings。

### Provenance 文件

每个由 `skill-installer` 写入的 skill 目录必须包含：

```text
.deepcli-skill-source.json
```

字段：

```json
{
  "schemaVersion": 1,
  "installedAt": "2026-05-23T00:00:00Z",
  "installedBy": "skill-installer",
  "source": {
    "kind": "github|url|local|registry",
    "url": "...",
    "repo": "owner/name",
    "path": "skills/example",
    "ref": "main",
    "resolvedCommit": "...",
    "contentHash": "sha256:..."
  },
  "target": {
    "layer": "project|user",
    "path": "...",
    "skillName": "example"
  },
  "compatibility": {
    "claudeCode": "compatible|warning|unsupported",
    "codex": "compatible|warning|unsupported",
    "openclaw": "compatible|warning|unsupported",
    "hermes": "compatible|warning|unsupported"
  },
  "warnings": []
}
```

`check` / `update` / `audit` / `uninstall` must use this file as their
source of truth. Skills without provenance are manual/local skills; they can
be listed and audited, but update must refuse them unless the user supplies a
new source explicitly.

## Kernel/API 设计

### CommandManager

新增 builtin：

```python
CommandDef(
    name="skills",
    description="Manage skills and skill-installed commands",
    usage="/skills [list | inspect | search | sources | install | refresh | check | update | audit | uninstall]",
    acp_method=MustangMethod.SKILLS_LIST,
    subcommands=[
        "list",
        "inspect",
        "search",
        "sources",
        "install",
        "refresh",
        "check",
        "update",
        "audit",
        "uninstall",
    ],
)
```

`CommandDef.acp_method` 只代表无参数 `/skills` 和 `/skills list` 的默认方法。
CLI/gateway 必须按子命令做显式分发：

| Input | ACP / action |
|---|---|
| `/skills` | `_mustang.agent/skills/list` |
| `/skills list` | `_mustang.agent/skills/list` |
| `/skills inspect <name>` | `_mustang.agent/skills/inspect` |
| `/skills refresh` | `_mustang.agent/skills/refresh` |
| `/skills search ...` | `_mustang.agent/session/activate_skill(skill="skill-installer", args="search ...")` |
| `/skills sources` | `_mustang.agent/session/activate_skill(skill="skill-installer", args="sources")` |
| `/skills install ...` | `_mustang.agent/session/activate_skill(skill="skill-installer", args="install ...")` |
| `/skills check ...` | `_mustang.agent/session/activate_skill(skill="skill-installer", args="check ...")` |
| `/skills update ...` | `_mustang.agent/session/activate_skill(skill="skill-installer", args="update ...")` |
| `/skills audit ...` | `_mustang.agent/session/activate_skill(skill="skill-installer", args="audit ...")` |
| `/skills uninstall ...` | `_mustang.agent/session/activate_skill(skill="skill-installer", args="uninstall ...")` |

`/skills install` 的执行规则固定为：

- CLI 将 `/skills install ...` 转成
  `_mustang.agent/session/activate_skill(skill="skill-installer", args=...)`。
- CommandManager 不新增 install dispatcher。
- Kernel 不提供安装 apply helper；所有 write apply 必须由 skill-installer
  agent flow 通过现有工具执行。

### Skill command projection

调整 `_register_skill_commands()`：

- canonical command: `skill:<manifest.name>`。
- usage: `/skill:<name> <argument-hint>`.
- source: `"skill"`.
- acp_method: `MustangMethod.SESSION_ACTIVATE_SKILL`。
- metadata 必须包含 `skillName`，CLI/gateway 用它调用 activate_skill。
- aliases: naked `<name>` when compat enabled or legacy mode enabled.

需要补充 `CommandDef` 字段：

```python
aliases: list[str] = field(default_factory=list)
canonical_name: str | None = None
metadata: dict[str, Any] = field(default_factory=dict)
```

必须扩展 `CommandDef`，不要用注册两个 `CommandDef` 的方式模拟 alias。
否则 autocomplete/help 很难优雅隐藏 deprecated naked alias。

`CommandRegistry.lookup(name)` 必须同时支持 canonical name 和 alias lookup，但
`list_commands()` 只返回 canonical commands。需要 alias 展示的 UI 读取
`CommandDef.aliases`，不能把 alias 当独立 command。

### SkillManager

新增只读管理 API：

```python
list_skill_records()
inspect_skill(name)
refresh()
```

返回类型：

```python
@dataclass(frozen=True)
class SkillRecord:
    name: str
    source: str
    layer_priority: int
    path: str | None
    user_invocable: bool
    model_invocable: bool
    command: str | None
    aliases: tuple[str, ...]
    setup_needed: bool
    missing_bins: tuple[str, ...]
    missing_env: tuple[str, ...]
    missing_tools: tuple[str, ...]
    provenance: dict[str, Any] | None
    warnings: tuple[str, ...]

@dataclass(frozen=True)
class SkillInspectResult:
    record: SkillRecord
    description: str
    when_to_use: str | None
    allowed_tools: tuple[str, ...]
    argument_hint: str | None
    supporting_files: tuple[str, ...]
    requires: dict[str, Any]
    setup: dict[str, Any] | None
    config: dict[str, Any] | None
```

`inspect_skill()` 不返回 body。Body 只能通过 skill activation 注入。

`refresh()` 必须：

- prune missing file-backed skills。
- rescan project/user/compat/external layers。
- reload ResourceStore-backed global declarations。
- reload bundled registry。
- preserve invoked skill records only when the skill still exists.
- emit skills_changed so CommandManager rebuilds `skill:<name>` commands.

不要把 install apply 塞进 SkillManager。SkillManager 的边界仍是：

- discovery
- manifest/body lifecycle
- activation
- ResourceStore declaration projection

安装目录写入由 skill-installer 的工具流负责；SkillManager 只负责 refresh/discover。

### ACP Methods

新增：

```text
_mustang.agent/skills/list
_mustang.agent/skills/inspect
_mustang.agent/skills/refresh
```

新增 `MustangMethod` enum members：

```python
SKILLS_LIST = "_mustang.agent/skills/list"
SKILLS_INSPECT = "_mustang.agent/skills/inspect"
SKILLS_REFRESH = "_mustang.agent/skills/refresh"
```

新增 ACP schema：

```python
class SkillsListRequest(AcpModel):
    include_commands: bool = True
    meta: dict[str, Any] | None = None

class SkillsListResponse(AcpModel):
    skills: list[SkillRecordEntry]
    commands: list[SkillCommandEntry] = Field(default_factory=list)
    meta: dict[str, Any] | None = None

class SkillsInspectRequest(AcpModel):
    name: str
    meta: dict[str, Any] | None = None

class SkillsInspectResponse(AcpModel):
    skill: SkillInspectEntry
    meta: dict[str, Any] | None = None

class SkillsRefreshRequest(AcpModel):
    reason: str | None = None
    meta: dict[str, Any] | None = None

class SkillsRefreshResponse(AcpModel):
    changed: bool
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    meta: dict[str, Any] | None = None
```

`SkillRecordEntry` 和 `SkillInspectEntry` 是 ACP 传输模型，字段必须覆盖
`SkillRecord` / `SkillInspectResult`，但不能包含 skill body。

不新增：

```text
_mustang.agent/skills/install
_mustang.agent/skills/uninstall
_mustang.agent/skills/update
```

原因：install/update/uninstall 是写文件 + 供应链动作。它们必须让
`skill-installer` 通过现有 tools 走可审计路径，避免绕过权限模型。

### CLI

CLI 仍然是 thin ACP client：

- `/skills list` -> `_mustang.agent/skills/list`
- `/skills inspect <name>` -> `_mustang.agent/skills/inspect`
- `/skills refresh` -> `_mustang.agent/skills/refresh`
- `/skills search ...` -> `_mustang.agent/session/activate_skill` with
  `skill="skill-installer"` and `args="search ..."`
- `/skills sources` -> `_mustang.agent/session/activate_skill` with
  `skill="skill-installer"` and `args="sources"`
- `/skills install ...` -> `_mustang.agent/session/activate_skill` with
  `skill="skill-installer"` and `args="install ..."`
- `/skills check ...` -> `_mustang.agent/session/activate_skill` with
  `skill="skill-installer"` and `args="check ..."`
- `/skills update ...` -> `_mustang.agent/session/activate_skill` with
  `skill="skill-installer"` and `args="update ..."`
- `/skills audit ...` -> `_mustang.agent/session/activate_skill` with
  `skill="skill-installer"` and `args="audit ..."`
- `/skills uninstall ...` -> `_mustang.agent/session/activate_skill` with
  `skill="skill-installer"` and `args="uninstall ..."`
- `/skill:<name> ...` -> `_mustang.agent/session/activate_skill` with
  `skill="<name>"`

CLI 不扫描 `.mustang/skills`、`.claude/skills`、`~/.deepcli/skills`。

所有 `_mustang.agent/session/activate_skill` 调用都必须带当前 session id。
CLI 负责把当前 attached session id 填入 ACP request；gateway 使用该 channel
绑定的 session id。

## 实现范围

这不是分阶段计划。一次实现必须把 `/skills` 管理面、bundled
`skill-installer`、`skill:` command prefix、安装闭环、安全校验和真实探针一起
交付。不能只做只读 catalog，也不能只做文档式 skill。

必须完成：

1. 接通现有 bundled skill registry：SkillManager startup 从
   `get_bundled_skills()` 读取 bundled skills，而不是传空列表。
2. 新增 bundled `skill-installer` skill，并提供 validate、install-from-github、
   install-from-url、install-from-local、list-sources、resolve-source、
   write-provenance、check-installed、update-installed、audit-installed、
   archive-skill helper scripts，以及 `references/skill-sources.json`。
3. 新增 `/skills` builtin command，覆盖 `list`、`inspect`、`install`、
   `search`、`sources`、`refresh`、`check`、`update`、`audit`、`uninstall`。
4. 新增 `_mustang.agent/skills/list`、`_mustang.agent/skills/inspect`、
   `_mustang.agent/skills/refresh`。这些是管理/发现方法，不执行安装 apply。
5. CLI 支持 `/skills list`、`/skills inspect`、`/skills refresh`、
   `/skills search`、`/skills sources`、
   `/skills check`、`/skills update`、`/skills audit`、`/skills uninstall`。
   写动作必须激活 `skill-installer`，不能新增 kernel install dispatcher。
6. `/skills install ...` 激活 `skill-installer`，args 保留用户原始 intent。
7. CommandManager 将 user-invocable skills canonical 投影为
   `skill:<name>`，裸 `/name` 仅作为 compatibility alias。
8. 扩展 `CommandDef` 支持 canonical name / aliases / metadata，并让 `/help` 与
   autocomplete 优先显示 `/skill:<name>`。
   `CommandRegistry.lookup()` 必须支持 alias lookup，但 `list_commands()` 只返回
   canonical commands。
9. CLI parser 支持 `/skill:<name> args`，并映射到
   `_mustang.agent/session/activate_skill(skill="<name>", args=...)`。
10. 安装报告写入目标 skill 目录下的 provenance 文件，例如
    `.deepcli-skill-source.json`。
11. validator 输出 Claude Code / Codex / OpenClaw / Hermes 字段兼容报告。
12. 对 downloaded/copied skill 做 path traversal、symlink escape、manifest、
    suspicious script/reference/template 扫描。
13. 缺失 bin/env/tool 依赖时，不阻止用户明确安装，但必须在 inspect/list/report
    中显示 setup needed 或 incompatibility warning。
14. `/skills update` 根据 provenance 执行可审计更新；不覆盖本地修改，除非
    用户明确 force。
15. `/skills uninstall` 默认 move 到 archive，不直接 delete。
16. `SkillManager.refresh()` 必须 rescan all layers、reload bundled skills、
    preserve still-valid invoked records，并 emit skills_changed。
17. 激活 bundled `skill-installer` 前必须把 helper scripts 提取到真实 bundled
    root；提取失败不能返回不存在的 script path。
18. 更新 docs 和 progress；遇到兼容或安全坑时更新 lessons learned。

## 单次验收

一次实现完成时必须同时满足：

- 真实 kernel probe 通过 Access -> Hub -> Mustang runtime 调用
  `_mustang.agent/skills/list`。
- `/skills list` 输出包含 project/external/user/bundled/mcp source，并用
  layer detail 标记 claude-compat/resource-store-managed。
- CLI 不直接读本地 skill 文件。
- 名为 `model` 的 skill canonical 显示为 `/skill:model`。
- `/model` 仍调用 builtin model command。
- `/skill:model` 激活 skill。
- 裸 `/model` 不会被 source=`skill` 覆盖。
- `/skills install <github path>` 进入 `skill-installer` activation flow。
- `/skills search <query>` 和 `/skills sources` 进入 `skill-installer` activation
  flow，并读取 bundled source index。
- helper script 安装一个测试 skill 到 temp project `.mustang/skills`。
- SkillManager refresh 后能发现新 skill。
- 新 skill command 以 `/skill:<name>` 出现在 commands list。
- malformed `SKILL.md` 不会写入目标目录。
- archive/path traversal 和 symlink escape 被拒绝。
- 缺失 bin/env 的 skill 安装后 list/inspect 显示 setup needed。
- update 不覆盖本地修改，除非 force。
- uninstall 默认 archive，可恢复。
- 手动/local skills 无 provenance 时可以 list/audit，但 update 必须拒绝，除非用户
  明确提供新 source。
- bundled helper scripts 在 activation 后真实存在。
- `CommandRegistry.lookup()` 可通过 alias 找到 command，但 commands/list 不重复列出 alias。

## 测试矩阵

测试必须和实现一起提交，不能只靠手工 probe。核心原则：每个闭合缝都有一个
unit/integration 测试和一个真实 subsystem probe。

### Unit Tests

#### `tests/kernel/skills/`

新增或扩展：

- `test_bundled.py`
  - SkillManager startup 会读取 `get_bundled_skills()`。
  - `skill-installer` 作为 bundled skill 出现在 registry。
  - bundled supporting files 可提取，路径在 bundled root 内。
  - 激活 `skill-installer` 后 helper scripts 真实存在。
- `test_skill_manager.py`
  - `list_skill_records()` 返回 project/external/user/bundled/mcp source，并用
    layer detail 标记 claude-compat/resource-store-managed。
  - `inspect_skill(name)` 不返回完整 body，只返回 manifest/status/provenance。
  - `refresh()` 重新发现新写入的 `.mustang/skills/<name>/SKILL.md`。
  - `refresh()` reload bundled registry 并 emit skills_changed。
  - `refresh()` preserve still-valid invoked skill records，删除 missing skill records。
  - 缺失 bin/env/tool 时 list/inspect 标记 setup needed。
- `test_skill_installer_helpers.py`
  - `validate-skill.py` 接受合法 `SKILL.md`，拒绝 malformed frontmatter。
  - `install-from-github.py` 支持 repo/path/ref/name/dest，并拒绝目标已存在。
  - `install-from-url.py` 支持单文件 `SKILL.md` URL，name 缺失时按规则失败或推断。
  - `install-from-local.py` 复制本地 skill 目录，并拒绝 source/target 嵌套、
    symlink escape 和 archive 目录写入。
  - `list-sources.py` 输出 bundled source index。
  - `resolve-source.py` 把 well-known slug 解析成 GitHub/URL/local source。
  - `write-provenance.py` 写入 source/ref/hash/layer/warnings。
  - `check-installed.py` 检测 local drift 和 source reachability。
  - `update-installed.py` 默认拒绝覆盖 local drift，`--force` 才覆盖。
  - `audit-installed.py` 报告 manifest/path/script/dependency warnings。
  - `archive-skill.py` 只 archive user/project skills，拒绝 bundled/MCP skills。
  - path traversal、absolute path overwrite、symlink escape 都失败。

#### `tests/kernel/commands/`

新增或扩展：

- `test_command_manager.py`
  - `/skills` builtin 存在，subcommands 包含 list/inspect/search/sources/install/refresh/check/update/audit/uninstall。
  - user-invocable skill canonical command 是 `skill:<name>`。
  - naked `/name` 只作为 alias，不作为 canonical command。
  - `CommandDef.metadata["skillName"]` 是真实 skill name，CLI 不需要 parse command string。
  - `CommandRegistry.lookup("name")` 可通过 alias 找到 canonical command。
  - `list_commands()` 不重复返回 alias。
  - skill 名为 `model` 时不能 shadow builtin `/model`。
  - `/help`/catalog 输出优先显示 `/skill:<name>`。

#### `tests/kernel/protocol/` 或现有 ACP routing tests

新增：

- `_mustang.agent/skills/list` schema/routing。
- `_mustang.agent/skills/inspect` schema/routing。
- `_mustang.agent/skills/refresh` schema/routing。
- 确认没有 `_mustang.agent/skills/install/update/uninstall` apply path，避免绕过
  `skill-installer`。

### CLI Tests

位置按现有 CLI 测试结构放置；若没有专门目录，新增在 `tests/cli/` 或
现有 CLI package tests 中。

必须覆盖：

- `/skills list` -> `_mustang.agent/skills/list`。
- `/skills inspect foo` -> `_mustang.agent/skills/inspect`。
- `/skills refresh` -> `_mustang.agent/skills/refresh`。
- `/skills search <query>` -> 激活 `skill-installer`，args 保留 `search <query>`。
- `/skills sources` -> 激活 `skill-installer`，args 为 `sources`。
- `/skills install <source>` -> `_mustang.agent/session/activate_skill`，skill 为
  `skill-installer`，args 保留 `install <source>`。
- `/skills update/check/audit/uninstall ...` -> 激活 `skill-installer`，不调用
  未定义的 kernel install/update/uninstall method。
- `/skill:foo args` -> `_mustang.agent/session/activate_skill(skill="foo")`，skill name
  来自 command metadata 而不是字符串切割。
- `/model` 在存在 skill `model` 时仍调用 builtin model path。
- CLI 不读 `.mustang/skills`、`.claude/skills`、`~/.deepcli/skills`；测试用 monkeypatch
  或 fake filesystem 证明没有本地扫描。

### Integration Tests

新增或扩展 `tests/e2e/`：

- 启动 kernel，确认 `/skills list` 能看到 bundled `skill-installer` 和已有
  project/user skills。
- 在 temp workspace 安装 fixture skill 后，调用 `/skills refresh`，再通过
  commands/list 看到 `/skill:<fixture>`。
- 激活 `/skill:<fixture>`，确认进入 `session/activate_skill` 并返回 skill body。
- 安装一个名为 `model` 的 fixture skill，确认 `/model` 与 `/skill:model` 分流正确。
- 安装缺失 env/bin 的 fixture skill，确认 inspect/list 显示 setup needed。

### Closure Probes

新增真实 probe：

```text
tests/probe/probe_skills_command_installer.py
```

必须走真实 Access -> Hub -> Mustang runtime，不允许只用 mocks。Probe 输出至少包含：

```text
probe=skills_command_installer
bundled_skill_installer_loaded=True
skills_list_through_access=True
skill_prefix_command_visible=True
builtin_shadow_prevented=True
sources_routes_to_skill_installer=True
search_routes_to_skill_installer=True
install_routes_to_skill_installer=True
helper_install_fixture=True
refresh_discovers_installed_skill=True
installed_skill_activates=True
malformed_skill_rejected=True
path_traversal_rejected=True
manual_skill_update_refused=True
uninstall_archives=True
alias_not_duplicated=True
result=PASS
```

### Regression Tests

必须保留并更新现有 skill tests：

- Claude Code `.claude/skills` compat 仍按 `skills.claude_compat` 开关工作。
- ResourceStore-backed global skill declarations 仍然优先于 legacy filesystem drift。
- Dynamic discovery / conditional activation 不因 bundled skill 接入而重复注册。
- Compaction invoked-skill preservation 不因 `/skill:<name>` prefix 改名而丢失。

## 文档更新

实现时需要同步更新：

- `docs/kernel/subsystems/commands.md`
- `docs/kernel/subsystems/skills.md`
- `docs/reference/codex-cli-kernel-comparison.md`
- `docs/reference/builtin-skills-survey.md`
- `docs/plans/progress.md`
- `docs/lessons-learned.md`，如果遇到兼容或安全坑

## Definition of Done

本实现必须满足项目 DoD：

1. Unit tests 覆盖 CommandManager、SkillManager API、ACP schema/routing。
2. Integration tests 覆盖 CLI slash parsing 到 ACP method；这只证明 parser /
   dispatch shape，不等于命令闭合。
3. Closure probe 走真实 Access -> Hub -> Mustang runtime，并且从真实
   `commands/list` 验证所有 `source="skill"` command 都可通过
   `/skill:<name>` 激活。
4. `ruff` / `mypy` / targeted pytest 通过。
5. 报告中粘贴真实 probe 输出。

特别注意：`/skills install` 的完成标准不是“文件复制成功”，而是闭合到：

```text
install source -> target skill dir -> SkillManager refresh -> commands/list sees /skill:<name> -> /skill:<name> activates
```

缺任何一环都不算完成。

## Post-Implementation Command Audit

2026-05-24 复查后，`/skills` 本身的闭合状态如下：

- `/skills list` / `inspect` / `refresh` 通过 `_mustang.agent/skills/*`
  real-kernel path。
- `/skills install/search/sources/check/update/audit/uninstall` 通过
  `/skill:skill-installer ...` activation path。
- Runtime `commands/list` 中的每个 `source="skill"` command 都由
  `probe_real_kernel_slash_commands.ts` 逐个激活。

随后完成的非 `/skills` 命令闭合：

```text
/session resume
/cron list/create/delete
/memory list/show/delete
/global restore
/agents add/set-identity/bindings/unbind/start/stop/restart/health/grants/grant/revoke-grant
/gateways enable/disable/reload/bindings/unbind
/webfetch browser install/status/pair/reset
```

这些命令本来应该可用；现在均已通过 active CLI slash registry 到
Kernel ACP/Runtime dispatcher 的 real-kernel probe。闭合证据：

```text
bun run tests/probe_real_kernel_slash_commands.ts
kernel_status_via_real_acp=true
skills_management_via_real_acp=true
skill_commands_via_real_cli_print=2
top_level_slash_commands_smoked=true
warnings=0
result=PASS
```
