# SkillManager — Design

Status: **landed** — shipped as optional subsystem (Phase 8).

Reference:
- Claude Code `src/skills/loadSkillsDir.ts`, `src/tools/SkillTool/`,
  `src/commands.ts`, `src/bootstrap/state.ts` (invokedSkills)
- Hermes Agent `agent/skill_utils.py`, `agent/skill_commands.py`,
  `tools/skills_tool.py`, `agent/prompt_builder.py`

---

## 核心概念

Skill 是**一段带元数据的 Markdown，被注入对话让 LLM 获得领域知识和行为指令**。

Skill ≠ 可执行代码（和 Hook 的本质区别）。Skill 通过两条路径生效：

1. **Skill listing** — 系统提示词中列出可用 skill 的名称 + 描述，让 LLM
   知道可以通过 `Skill` tool 调用它们
2. **Skill activation** — 用户 `/skill-name` 或 LLM 调用 `Skill` tool 后，
   body 内容作为 user message 注入对话，LLM 据此获取具体指令

两条路径对应 Claude Code 的两个机制：
- `prompt.ts` 的 `formatCommandsWithinBudget()` → listing
- `SkillTool.ts` 的 `call()` → activation（inline 或 fork）

DeepCLI 的用户 slash path 是 Kernel-owned：

- `SkillManager.user_invocable_skills()` 是 truth
- `CommandManager` 把这些 skills 投影为 `source="skill"` 命令
- CLI 从 `_mustang.agent/commands/list` 拉取目录用于 autocomplete
- 输入 `/skill-name args` 时，CLI 调
  `_mustang.agent/session/activate_skill`
- `SessionManager.activate_skill()` 校验 `user_invocable`，调用
  `SkillManager.activate()`，记录 invoked skill 用于 compaction，然后把
  skill body 作为本轮 prompt 的 active instructions

CLI 不扫描 `~/.deepcli/skills` 或 `.mustang/skills`；避免 Access/CLI 与
Primary Runtime 在 router mode 下看到不同 Skill 状态。

---

## 文件格式

### 目录格式：`skill-name/SKILL.md`

与 Claude Code 对齐，采用目录格式而非 D12 原计划的 loose `.md`。理由：
- Claude Code 已从 loose `.md` 迁移到目录格式
- 目录支持附带资源文件（schema、脚本等）
- `${SKILL_DIR}` 替换需要 base directory

```
~/.deepcli/skills/
├── my-skill/
│   ├── SKILL.md          # 必须，skill 定义
│   ├── references/       # 可选，参考文档（LLM 可按需 Read）
│   │   └── api.md
│   ├── templates/        # 可选，输出模板
│   │   └── template.py
│   ├── scripts/          # 可选，辅助脚本
│   │   └── setup.sh
│   └── schemas/          # 可选，schema 文件
│       └── api.json
├── another-skill/
│   └── SKILL.md
```

**Supporting files（来自 Hermes）**：skill 目录下的子文件/子目录
会被自动发现并列在 activation message 中，告知 LLM 可以用 Read tool
按需加载。这实现了 progressive disclosure — SKILL.md body 给核心指令，
supporting files 按需深入。Claude Code 仅通过 `${SKILL_DIR}` 间接支持，
Hermes 的 explicit listing 更友好。

### Frontmatter schema

```yaml
---
# ── 基础 (Claude Code 对齐) ──────────────────────────────────
name: my-skill                      # optional; defaults to dir name
description: "Short description"    # required (或从 body 首行提取)
allowed-tools:                      # optional, tool 权限扩展
  - "Bash(npm run *)"
  - "Bash(git push *)"
argument-hint: "<url>"              # optional, 调用提示
arguments: [url, format]            # optional, 命名参数 ${url} ${format}
when-to-use: "When user asks..."    # optional, LLM 判断何时主动使用
user-invocable: true                # optional, default true; false = 用户不能 /skill
disable-model-invocation: false     # optional, default false; true = LLM 不能调 Skill tool
os: [linux, darwin]                 # optional, sys.platform allow-list
context: fork                       # optional, "fork" = sub-agent 执行
agent: general-purpose              # optional, fork 时的 agent type
model: opus                         # optional, 覆盖当前 model
hooks:                              # optional, skill 注册的 hooks
  pre_tool_use:
    - command: "echo skill pre-hook"
paths:                              # optional, 条件激活 glob 模式
  - "src/api/**"
  - "*.proto"

# ── Eligibility (Claude Code requires + Hermes 增强) ────────
requires:
  bins: [git, npm]                  # all must be on PATH
  env: [API_KEY]                    # all must be set and non-empty (Claude Code 级)
  tools: [Bash, Grep]              # 来自 Hermes: 仅当这些 tool 可用时显示
  toolsets: [mcp_github]            # 来自 Hermes: 仅当这些 toolset 可用时显示
fallback-for:                       # 来自 Hermes: 降级 skill
  tools: [WebSearch]                # 当 WebSearch 可用时隐藏本 skill
  toolsets: [mcp_browser]           # 当 mcp_browser toolset 可用时隐藏

# ── Environment setup (来自 Hermes，Claude Code 不具备) ──────
setup:
  env:                              # 交互式环境变量引导
    - name: OPENAI_API_KEY
      prompt: "Enter your OpenAI API key"
      help: "Get from https://platform.openai.com/api-keys"
      secret: true                  # 掩码输入
      optional: false
    - name: MODEL_NAME
      prompt: "Which model to use?"
      help: "e.g. gpt-4o, claude-3-opus"
      optional: true
      default: "gpt-4o"

# ── Skill config (来自 Hermes，Claude Code 不具备) ───────────
config:                             # skill 声明的配置变量
  max_retries: 3                    # 默认值
  output_format: "markdown"         # 从 config.yaml skills.<name>.* 覆盖
---

# My Skill

Skill body content here.

Supports `$ARGUMENTS` (positional) and `${url}` (named).
Supports `${SKILL_DIR}` for referencing bundled resources.
Config vars accessible as `${config.max_retries}`.
```

### Description fallback

Claude Code 在 `description` 缺失时从 body 首行提取
（`extractDescriptionFromMarkdown`）。我们同样支持：如果 frontmatter 没有
`description`，从 body 第一个 `#` 标题或首段文本提取。

---

## 发现层次

多层发现，高优先级覆盖低优先级（name 相同时）。
Claude Code 四层 + Hermes external dirs + Claude Code 兼容层：

| 优先级 | 层 | 路径 | 说明 |
|--------|---|------|------|
| 0 | project | `.mustang/skills/` | 项目级 skill |
| 0 | project-compat | `.claude/skills/` | **Claude Code 兼容 (opt-in)**：仅 `skills.claude_compat=true` 时扫 |
| 1 | external | config.yaml `skills.external_dirs` | 来自 Hermes：团队共享目录 |
| 2 | user | `~/.deepcli/skills/` | 用户级 skill |
| 2 | user-compat | `~/.claude/skills/` | **Claude Code 兼容 (opt-in)**：仅 `skills.claude_compat=true` 时扫 |
| 3 | bundled | `kernel/skills/bundled/` | 内置 skill |
| 4 | MCP | MCPManager 提供 | MCP server 暴露的 skill |

### Claude Code skill 兼容 (opt-in)

DeepCLI **可选**同时扫描 `.mustang/skills/` 和 `.claude/skills/`，
实现对 Claude Code skill 的**零修改直接复用**。默认关闭 —— 设
`skills.claude_compat: true` 才启用。

**兼容性分析**（已通过 Claude Code 源码确认）：
- **文件格式**：完全一致 — `skill-name/SKILL.md` 目录格式
- **Frontmatter**：DeepCLI 是 Claude Code 的超集（多出的 Hermes
  字段被 silently dropped），Claude Code 的所有字段（`name`,
  `description`, `allowed-tools`, `argument-hint`, `arguments`,
  `when-to-use`, `user-invocable`, `disable-model-invocation`,
  `requires`, `os`, `context`, `agent`, `model`, `hooks`, `paths`）
  均已对齐
- **Body 语法**：`$ARGUMENTS`, `${name}` 参数替换完全一致
- **`${CLAUDE_SKILL_DIR}`**：映射到 DeepCLI 的 `${SKILL_DIR}`
  （解析时自动识别两种写法）

**优先级规则**：同一层内 `.mustang/skills/` 优先于 `.claude/skills/`。
如果同名 skill 同时存在于两个目录，`.mustang/` 版本生效。这让用户
可以逐步迁移：先在 `.claude/skills/` 里使用，需要 DeepCLI 独有特性
（setup、config、fallback-for）时复制到 `.mustang/skills/` 并增强。

**Dynamic discovery 也兼容**：`on_file_touched()` 向上遍历时同时
查找 `.mustang/skills/` 和 `.claude/skills/`（Claude Code 源码确认
其 `discoverSkillDirsForPaths` 硬编码查找 `.claude/skills/`）。

**启用兼容层（opt-in）**：从 2026-04-22 起，默认**关闭**扫描
`.claude/skills/`。理由：`.claude/skills/` 是 Claude Code CLI 的目录，
开发者可能在那儿放纯 session-scoped workflow skill（如
`/done-check`），这些对 DeepCLI 的终端用户无意义，若默认扫进来
会污染 LLM skill listing 和 `/` autocomplete。需要跨工具复用
Claude Code 用户级 skill 的话显式 opt-in：

```yaml
# ~/.deepcli/config/skills.yaml
skills:
  claude_compat: true    # default: false
```

配置走 `ConfigManager.bind_section(file="skills", section="skills",
schema=SkillsConfig)`，schema 在 [`src/kernel/kernel/skills/config.py`](../../../src/kernel/kernel/skills/config.py)。
构造器 kwarg `claude_compat=...` 优先级高于 config（测试用途）。

### External dirs（来自 Hermes）

config.yaml 可声明额外的 skill 目录，典型用途是团队共享
（NFS/Git submodule）或组织级 skill 分发。Claude Code 仅通过
CLI `--add-dir` 实现类似功能，Hermes 的 config 声明更持久、更可维护。

```yaml
# config.yaml
skills:
  external_dirs:
    - ~/team-skills
    - /opt/org-skills
```

**去重**：同一物理文件通过 symlink 出现在多层时，`realpath` 去重
（Claude Code 的 `getFileIdentity`），先发现的保留。

### Per-gateway disabled skills（来自 Hermes）

不同的 gateway（CLI、Discord、Web）可能需要屏蔽某些 skill。
Hermes 支持 `skills.disabled` 全局禁用和 `skills.platform_disabled`
按平台禁用。我们对齐：

```yaml
# config.yaml
skills:
  disabled: [deprecated-skill]      # 全局禁用
  gateway_disabled:                  # 按 gateway 禁用
    discord: [interactive-debug]
    web: [terminal-only-skill]
```

SkillRegistry 在 `model_invocable()` / `user_invocable()` 查询时
过滤 disabled 列表。Gateway 信息从 session context 获取。

### Dynamic discovery

Claude Code 在文件操作（Read/Write/Edit）时沿文件路径向上查找
`.claude/skills/` 目录，发现后动态加入。我们对齐：

- ToolExecutor 在 file 类 tool 完成后，调用
  `SkillManager.discover_for_paths(file_paths)` 向上查找
  `.mustang/skills/`
- 新发现的 skill 加入 dynamic registry
- 发出 signal 通知 listing cache 失效

### Conditional skills (paths frontmatter)

带 `paths` frontmatter 的 skill 在启动时只加入 conditional 池。
当 file tool 操作的文件路径匹配 gitignore-style pattern 时，skill
被激活并移入 active registry。

---

## 模块结构

```
kernel/skills/
├── __init__.py          # SkillManager (Subsystem)
├── types.py             # SkillManifest, SkillRequires, LoadedSkill, SkillSource,
│                        #   SkillSetup, SkillFallbackFor, ActivationResult, etc.
├── manifest.py          # SKILL.md frontmatter 解析 + supporting files discovery
├── eligibility.py       # 静态 (OS/bins/env) + 动态 (tools/toolsets/fallback) 检查
├── loader.py            # 多层发现 + 去重 + 磁盘 snapshot 缓存
├── registry.py          # SkillRegistry — 内存索引 + disabled 过滤
├── arguments.py         # $ARGUMENTS / ${name} / ${config.*} 替换
├── setup.py             # 来自 Hermes: 环境变量 setup 检查 + 引导消息生成
└── bundled/             # 内置 skill 定义 (Python 注册)
    └── __init__.py
```

---

## 类型

```python
# types.py

class SkillSource(str, Enum):
    """发现层，决定优先级。"""
    PROJECT = "project"
    EXTERNAL = "external"
    USER = "user"
    BUNDLED = "bundled"
    MCP = "mcp"


@dataclass(frozen=True)
class SkillRequires:
    """Eligibility predicates。

    bins/env 对齐 Claude Code + HookRequires。
    tools/toolsets 来自 Hermes — 按可用 tool 条件显示。
    """
    bins: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()       # 来自 Hermes: 需要这些 tool 可用
    toolsets: tuple[str, ...] = ()     # 来自 Hermes: 需要这些 toolset 可用


@dataclass(frozen=True)
class SkillFallbackFor:
    """来自 Hermes — 当主工具可用时隐藏此降级 skill。"""
    tools: tuple[str, ...] = ()
    toolsets: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillSetupEnvVar:
    """来自 Hermes — 交互式环境变量引导条目。"""
    name: str
    prompt: str                       # 提示用户输入的文案
    help: str | None = None           # 帮助说明（如获取地址）
    secret: bool = False              # True = 掩码输入
    optional: bool = False
    default: str | None = None


@dataclass(frozen=True)
class SkillSetup:
    """来自 Hermes — 首次使用时的交互式设置流程。"""
    env: tuple[SkillSetupEnvVar, ...] = ()


@dataclass(frozen=True)
class SkillManifest:
    """SKILL.md frontmatter 解析结果。不含 body。"""
    name: str
    description: str
    has_user_specified_description: bool  # frontmatter 显式声明 vs body 提取
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str | None = None
    argument_names: tuple[str, ...] = ()
    when_to_use: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    requires: SkillRequires = field(default_factory=SkillRequires)
    fallback_for: SkillFallbackFor | None = None  # 来自 Hermes
    os: tuple[str, ...] = ()
    context: Literal["inline", "fork"] | None = None
    agent: str | None = None
    model: str | None = None
    hooks: dict | None = None      # skill-scoped hook definitions
    paths: tuple[str, ...] | None = None  # conditional activation globs
    setup: SkillSetup | None = None   # 来自 Hermes: 交互式环境设置
    config: dict[str, Any] | None = None  # 来自 Hermes: skill 级配置 + 默认值
    base_dir: Path = field(default_factory=Path)
    supporting_files: tuple[str, ...] = ()  # 来自 Hermes: 自动发现的附带文件


@dataclass
class LoadedSkill:
    """发现 + eligibility 通过后的 skill。Body lazy-loaded。"""
    manifest: SkillManifest
    source: SkillSource
    layer_priority: int         # project=0, user=1, bundled=2, mcp=3
    file_path: Path             # absolute path to SKILL.md (for dedup)
    _body: str | None = field(default=None, repr=False)

    @property
    def body(self) -> str:
        """Lazy load body on first access."""
        if self._body is None:
            self._body = _load_body(self.file_path)
        return self._body

    @property
    def content_length(self) -> int:
        """Body 字符数，用于 token budget 估算。"""
        return len(self.body)


@dataclass
class InvokedSkillInfo:
    """已激活 skill 的追踪记录，用于 compaction preservation。"""
    skill_name: str
    skill_path: str         # SKILL.md 的路径
    content: str            # 渲染后的 body（已做参数替换）
    invoked_at: float       # time.time()
    agent_id: str | None    # None = 主 session
```

---

## manifest.py — Frontmatter 解析

复用 `hooks/manifest.py` 的 `_extract_frontmatter` + `_coerce_str_list`
模式。新增 skill-specific 字段解析。

```python
def parse_skill_manifest(skill_dir: Path) -> SkillManifest:
    """解析 skill_dir/SKILL.md 的 frontmatter。

    Raises ManifestError on:
    - Missing SKILL.md
    - Missing / unclosed frontmatter fence
    - YAML parse errors

    Unknown keys silently dropped (forward-compatible).
    description 缺失时 fallback 到 body 首行提取。
    """
```

字段映射（Claude Code + Hermes 合并）：

| Frontmatter 字段 | SkillManifest 字段 | 来源 |
|-----------------|-------------------|------|
| `name` | `name` | Claude Code |
| `description` | `description` | Claude Code |
| `allowed-tools` | `allowed_tools` | Claude Code |
| `argument-hint` | `argument_hint` | Claude Code |
| `arguments` | `argument_names` | Claude Code |
| `when-to-use` / `when_to_use` | `when_to_use` | Claude Code |
| `user-invocable` | `user_invocable` | Claude Code |
| `disable-model-invocation` | `disable_model_invocation` | Claude Code |
| `requires.bins` / `requires.env` | `requires.bins` / `requires.env` | Claude Code |
| `requires.tools` | `requires.tools` | **Hermes** |
| `requires.toolsets` | `requires.toolsets` | **Hermes** |
| `fallback-for.tools` / `.toolsets` | `fallback_for` | **Hermes** |
| `os` | `os` | Claude Code (= Hermes `platforms`) |
| `context` | `context` | Claude Code |
| `agent` | `agent` | Claude Code |
| `model` | `model` | Claude Code |
| `hooks` | `hooks` | Claude Code |
| `paths` | `paths` | Claude Code |
| `setup.env` | `setup` | **Hermes** |
| `config` | `config` | **Hermes** |
| *(auto-discovered)* | `supporting_files` | **Hermes** |

---

## eligibility.py

两阶段 eligibility 检查：

### 静态 eligibility（startup 时）

复用 `hooks/eligibility.py` 的逻辑：`os` + `requires.bins` +
`requires.env`。如果 hooks 和 skills 共用同一个函数签名，提取到
`kernel/utils/eligibility.py` 共享。

### 动态 visibility（listing / activation 时，来自 Hermes）

`requires.tools` / `requires.toolsets` 和 `fallback_for` 不在
startup 时检查（tool 可能在 session 中动态注册/移除），而是在
`model_invocable()` / `user_invocable()` 查询时实时过滤：

```python
def is_visible(skill: LoadedSkill, available_tools: set[str]) -> bool:
    """判断 skill 在当前 session 是否应该对 LLM 可见。"""
    req = skill.manifest.requires

    # requires.tools: 任一缺失 → 隐藏
    if req.tools and not all(t in available_tools for t in req.tools):
        return False

    # fallback_for: 主工具全部可用 → 隐藏降级 skill
    fb = skill.manifest.fallback_for
    if fb is not None:
        if fb.tools and all(t in available_tools for t in fb.tools):
            return False

    return True
```

这让 skill listing 自适应当前 session 的 tool 集合 — 比如
MCP server 连接后新 tool 出现，某些 fallback skill 自动隐藏。

---

## loader.py — 多层发现

```python
def discover(
    *,
    project_dir: Path | None,           # .mustang/skills/
    project_compat_dir: Path | None,    # .claude/skills/ (Claude Code 兼容)
    external_dirs: list[Path],           # config.yaml skills.external_dirs（来自 Hermes）
    user_dir: Path,                      # ~/.deepcli/skills/
    user_compat_dir: Path | None,        # ~/.claude/skills/ (Claude Code 兼容)
    bundled_skills: list[LoadedSkill],   # 内置注册
) -> tuple[list[LoadedSkill], list[LoadedSkill]]:
    """多层发现。返回 (unconditional, conditional) 两组。

    conditional = 有 paths frontmatter 的 skill，等待文件操作激活。
    扫描顺序：project → project-compat → external → user → user-compat → bundled。
    同层内 .mustang/ 优先于 .claude/（同名 skill 去重时先到先得）。
    """
```

### 单层发现流程（对齐 `loadSkillsFromSkillsDir`）

```
for each subdir in base_dir:
    if not subdir.is_dir() and not subdir.is_symlink():
        continue  # 不支持 loose .md，只支持 skill-name/ 目录
    skill_path = subdir / "SKILL.md"
    if not skill_path.exists():
        log + skip
    manifest = parse_skill_manifest(subdir)
    eligible, reason = is_eligible(manifest)
    if not eligible:
        log + skip
    yield LoadedSkill(manifest, source, priority, skill_path)
```

### Supporting files discovery（来自 Hermes）

`parse_skill_manifest()` 解析 frontmatter 后，扫描 skill 目录下
SKILL.md 以外的文件，记入 `manifest.supporting_files`：

```python
def _discover_supporting_files(skill_dir: Path) -> tuple[str, ...]:
    """递归列出 skill 目录下的辅助文件（相对路径）。"""
    files = []
    for path in skill_dir.rglob("*"):
        if path.is_file() and path.name != "SKILL.md":
            files.append(str(path.relative_to(skill_dir)))
    return tuple(sorted(files))
```

Activation 时，supporting files 列表附在 body 后面：

```
[This skill has supporting files you can load with Read:]
- references/api.md
- templates/template.py
```

### 去重（对齐 `getFileIdentity` + `seenFileIds`）

```python
async def _dedup(skills: list[LoadedSkill]) -> list[LoadedSkill]:
    """realpath 去重，先出现的保留。"""
    seen: dict[str, SkillSource] = {}
    result: list[LoadedSkill] = []
    for skill in skills:
        real = skill.file_path.resolve()
        real_str = str(real)
        if real_str in seen:
            logger.debug("skills: dedup %s (already from %s)", skill.manifest.name, seen[real_str])
            continue
        seen[real_str] = skill.source
        result.append(skill)
    return result
```

### Dynamic discovery（对齐 `discoverSkillDirsForPaths`）

```python
def discover_for_paths(
    file_paths: list[str],
    cwd: str,
    known_dirs: set[str],
    claude_compat: bool = True,
) -> list[Path]:
    """从 file_paths 向上遍历到 cwd，查找 skill 目录。

    每层同时检查 .mustang/skills/ 和 .claude/skills/（当 claude_compat=True）。
    cwd 级别的 skill 已在 startup 加载，这里只发现嵌套子目录。
    known_dirs 记录已检查过的路径（hit or miss），避免重复 stat。
    返回新发现的 skill 目录列表，按深度降序（深的优先）。
    """
```

### Conditional activation（对齐 `activateConditionalSkillsForPaths`）

```python
def activate_conditional(
    file_paths: list[str],
    cwd: str,
    conditional_pool: dict[str, LoadedSkill],
) -> list[LoadedSkill]:
    """检查 conditional skill 的 paths glob 是否匹配。

    匹配时从 conditional_pool 移出，返回新激活列表。
    使用 pathspec 或 fnmatch 做 gitignore-style 匹配。
    """
```

---

## registry.py — SkillRegistry

```python
class SkillRegistry:
    """线程安全 skill 索引。

    三个池：
    - _skills: 无条件加载的 skill (name → LoadedSkill)
    - _conditional: 有 paths 的 skill，等待激活 (name → LoadedSkill)
    - _dynamic: 运行时文件操作发现的 skill (name → LoadedSkill)
    """

    def register(self, skill: LoadedSkill) -> None:
        """注册 skill，低优先级不覆盖高优先级。"""

    def register_dynamic(self, skill: LoadedSkill) -> None:
        """注册运行时发现的 skill。"""

    def activate_conditional(self, name: str) -> None:
        """将 conditional skill 移入 dynamic pool。"""

    def lookup(self, name: str) -> LoadedSkill | None:
        """按 name 查找。动态 > 静态。"""

    def all_skills(self) -> list[LoadedSkill]:
        """所有已加载 skill（含 dynamic，不含 conditional）。"""

    def model_invocable(self) -> list[LoadedSkill]:
        """LLM 可通过 Skill tool 调用的 skill。
        排除 disable_model_invocation=True。"""

    def user_invocable(self) -> list[LoadedSkill]:
        """用户可通过 /skill-name 调用的 skill。"""

    def conditional_count(self) -> int:
        """待激活的 conditional skill 数量。"""
```

---

## arguments.py — 参数 + 配置替换

对齐 Claude Code 的 `argumentSubstitution.ts`，扩展 Hermes 的
config 替换：

```python
def substitute_arguments(
    content: str,
    args: str,
    argument_names: tuple[str, ...],
) -> str:
    """替换 skill body 中的参数占位符。

    1. $ARGUMENTS → 整个 args 字符串
    2. ${name} → 按命名参数拆分后的值
    3. ${SKILL_DIR} → skill 的 base_dir 路径
    4. ${CLAUDE_SKILL_DIR} → 同 ${SKILL_DIR}（Claude Code 兼容）
    """


def substitute_config(
    content: str,
    config: dict[str, Any],
) -> str:
    """替换 skill body 中的配置占位符（来自 Hermes）。

    ${config.key} → config[key] 的字符串值。
    未找到的 key → 保留原占位符（不报错）。
    """
```

---

## SkillManager — Subsystem

```python
class SkillManager(Subsystem):
    """发现、索引 skills；提供 listing 和 activation。

    消费者:
    - PromptBuilder: get_skill_listing() → 系统提示词
    - SkillTool: activate() → body 注入对话
    - CommandManager: user_invocable_skills() → / 命令列表
    - ToolExecutor: on_file_touched() → dynamic + conditional discovery
    - Compactor: get_invoked_skills() → compaction preservation
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        super().__init__(module_table)
        self._registry = SkillRegistry()
        self._invoked: dict[str, InvokedSkillInfo] = {}  # composite_key → info
        self._known_dynamic_dirs: set[str] = set()

    async def startup(self) -> None:
        config = self._module_table.config
        claude_compat = config.get("skills.claude_compat", True)

        project_dir = _resolve_project_skills_dir(config)         # .mustang/skills/
        project_compat_dir = _resolve_claude_skills_dir() if claude_compat else None  # .claude/skills/
        external_dirs = _resolve_external_dirs(config)             # 来自 Hermes
        user_dir = _resolve_user_skills_dir(config)                # ~/.deepcli/skills/
        user_compat_dir = _resolve_claude_user_skills_dir() if claude_compat else None  # ~/.claude/skills/
        bundled = _load_bundled_skills()

        unconditional, conditional = discover(
            project_dir=project_dir,
            project_compat_dir=project_compat_dir,
            external_dirs=external_dirs,
            user_dir=user_dir,
            user_compat_dir=user_compat_dir,
            bundled_skills=bundled,
        )

        for skill in unconditional:
            self._registry.register(skill)
        for skill in conditional:
            self._registry.register_conditional(skill)

        logger.info(
            "skills: loaded %d (%d conditional)",
            len(self._registry.all_skills()),
            self._registry.conditional_count(),
        )

    async def shutdown(self) -> None:
        self._invoked.clear()

    # ── Listing (consumed by PromptBuilder) ──────────────────────

    def get_skill_listing(self, context_window_tokens: int | None = None) -> str:
        """skill 目录文本，注入系统提示词。

        包含所有 model-invocable skills 的 name + description + when_to_use。
        遵守 token budget：context window 的 1%（与 Claude Code 对齐）。
        Bundled skills 描述不截断；其余按预算均分截断。
        """

    # ── Activation (consumed by SkillTool) ───────────────────────

    def activate(
        self,
        name: str,
        args: str = "",
        agent_id: str | None = None,
    ) -> ActivationResult | None:
        """激活 skill，返回渲染后的 body 和元数据。

        流程：
        1. lookup(name) — 找不到返回 None
        2. check setup.env — 检查必需环境变量（来自 Hermes）
           缺失时返回 ActivationResult.setup_needed = True + 引导信息
        3. lazy-load body
        4. substitute_arguments(body, args, manifest.argument_names)
        5. substitute ${SKILL_DIR} → manifest.base_dir
        6. resolve config vars — 从 config.yaml 覆盖默认值，替换 ${config.*}
        7. append supporting_files listing（来自 Hermes）
        8. 记录到 _invoked（compaction preservation）
        9. 返回 ActivationResult

        返回 None = skill 不存在。
        """

    def deactivate(self, name: str, agent_id: str | None = None) -> None:
        """移除 invoked skill 记录。"""

    # ── Invoked skill tracking (consumed by Compactor) ───────────

    def add_invoked(
        self,
        skill_name: str,
        skill_path: str,
        content: str,
        agent_id: str | None = None,
    ) -> None:
        """记录已激活的 skill，compaction 后重注入用。"""
        key = f"{agent_id or ''}:{skill_name}"
        self._invoked[key] = InvokedSkillInfo(
            skill_name=skill_name,
            skill_path=skill_path,
            content=content,
            invoked_at=time.time(),
            agent_id=agent_id,
        )

    def get_invoked_for_agent(
        self, agent_id: str | None = None,
    ) -> list[InvokedSkillInfo]:
        """返回指定 agent 的已激活 skills，按 invoked_at 降序。"""

    def clear_invoked(self, preserve_agent_ids: set[str] | None = None) -> None:
        """清理 invoked 记录。保留指定 agent 的。"""

    # ── Dynamic discovery (consumed by ToolExecutor) ─────────────

    async def on_file_touched(self, file_paths: list[str], cwd: str) -> None:
        """文件操作后调用，触发 dynamic discovery + conditional activation。

        1. discover_for_paths → 新 .mustang/skills/ 目录
        2. 加载新发现的 skill → register_dynamic
        3. activate_conditional → 检查 paths glob
        4. 有新 skill 时 emit signal
        """

    # ── Lookup (consumed by SkillTool, CommandManager) ───────────

    def lookup(self, name: str) -> LoadedSkill | None:
        return self._registry.lookup(name)

    def user_invocable_skills(self) -> list[LoadedSkill]:
        return self._registry.user_invocable()

    # ── MCP skill integration ────────────────────────────────────

    def register_mcp_skill(self, skill: LoadedSkill) -> None:
        """MCPManager 连接后注册 MCP-exposed skill。"""
        self._registry.register(skill)

    def unregister_mcp_skills(self, server_name: str) -> None:
        """MCP server 断开后移除其 skill。"""
```

### ActivationResult

```python
@dataclass
class ActivationResult:
    """activate() 的返回值。"""
    body: str                           # 渲染后的 body text（含 supporting files listing）
    allowed_tools: tuple[str, ...]      # tool 权限扩展
    model: str | None                   # model 覆盖
    context: Literal["inline", "fork"] | None  # 执行模式
    agent: str | None                   # fork 时的 agent type
    hooks: dict | None                  # skill-scoped hooks
    skill_root: str | None              # base_dir (for SKILL_DIR)
    setup_needed: bool = False          # 来自 Hermes: True = 缺少必需环境变量
    setup_message: str | None = None    # 来自 Hermes: 引导用户设置的提示信息
    config: dict[str, Any] | None = None  # 来自 Hermes: 合并后的 skill 配置
```

---

## Environment setup flow（来自 Hermes）

Claude Code 的 `requires.env` 只做 boolean 检查（有/没有），缺失时
skill 直接跳过。Hermes 提供了更友好的交互式设置流程。

当 skill 声明了 `setup.env` 且某些变量未设置时：

1. `activate()` 检测缺失变量
2. 返回 `ActivationResult(setup_needed=True, setup_message=...)`
3. SkillTool 将 `setup_message` 返回给 LLM
4. LLM 提示用户提供缺失的值
5. 用户通过对话或 `/config` 设置后重试

```
setup_message 格式：

Skill "my-skill" requires environment setup:

  OPENAI_API_KEY (required)
    Enter your OpenAI API key
    Help: Get from https://platform.openai.com/api-keys

  MODEL_NAME (optional, default: gpt-4o)
    Which model to use?

Set these in your environment or ~/.deepcli/config.yaml, then retry.
```

`secret: true` 的变量不在对话中回显值。

---

## SkillTool — Tool 集成

SkillTool 是注册到 ToolManager 的标准 Tool，LLM 通过它激活 skill。

```python
class SkillTool(Tool):
    name = "Skill"
    description = "Execute a skill within the main conversation"
    kind = ToolKind.other
    is_concurrency_safe = False  # 同一时间只激活一个 skill

    input_schema = {
        "skill": {"type": "string", "description": "Skill name"},
        "args": {"type": "string", "description": "Optional arguments"},
    }
```

### 执行流程（对齐 Claude Code SkillTool.call）

**Inline mode**（默认）：

```
1. validate: lookup(name) → exists? model-invocable?
2. authorize: ToolAuthorizer 检查 Skill(name) 权限规则
3. call:
   a. skills.activate(name, args, agent_id)
   b. 返回 ActivationResult
   c. body 作为新 user message 注入对话 (newMessages)
   d. allowed_tools 通过 contextModifier 扩展当前权限
   e. model 覆盖通过 contextModifier 切换
4. 记录 add_invoked (compaction preservation)
```

**Fork mode**（`context: fork`）：

```
1-2. 同上
3. call:
   a. skills.activate(name, args, agent_id)
   b. 创建 sub-agent (D14), 注入 body 作为 prompt
   c. 收集 agent 输出
   d. 返回 result text
```

### 权限判断（对齐 Claude Code checkPermissions）

```
1. 检查 deny rules: Skill(name) → 拒绝
2. 检查 allow rules: Skill(name) 或 Skill(name:*) → 允许
3. safe-properties check: 没有 allowed_tools / hooks 的 skill → 自动允许
4. 默认 → ask user
```

### Prompt（对齐 Claude Code prompt.ts）

SkillTool 的 tool prompt 引导 LLM 如何使用 Skill tool：

```
Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available
skills match. Skills provide specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>", they are
referring to a skill. Use this tool to invoke it.

Important:
- Available skills are listed in system-reminder messages
- When a skill matches, invoke it BEFORE generating any other response
- Do not invoke a skill that is already running
```

### Skill listing in system prompt

对齐 Claude Code 的 `formatCommandsWithinBudget`，listing 有 token
budget 上限：

- **Budget**: context window tokens × 4 chars/token × 1% = 默认 ~8000 chars
- **Bundled skills**: 描述不截断
- **其余 skills**: 描述按 budget 均分截断，极端情况退化到 name-only
- **每条上限**: 250 chars

---

## Compaction preservation

对齐 Claude Code 的 `createSkillAttachmentIfNeeded`：

Compaction 时，已激活的 skill body 需要重新注入到 compact 后的对话中，
否则 LLM 丢失 skill 指令。

```python
def create_skill_attachment(
    skills: SkillManager,
    agent_id: str | None,
) -> str | None:
    """生成 compaction 后重注入的 skill 文本。

    - 按 invoked_at 降序排列（最近使用的优先）
    - 每个 skill body 截断到 ~5000 tokens（保留头部，指令通常在开头）
    - 总预算 ~25000 tokens
    - 截断标记: "[... skill content truncated for compaction]"
    """
```

Compactor 在 compact 完成后，把 skill attachment 作为 attachment message
追加到新对话中。

注意：skill listing（目录）不在 compact 后重注入（与 Claude Code 一致
— 重注入 ~4K tokens 的 listing 是纯 cache_creation 浪费，LLM 仍然
有 Skill tool schema + invoked skill content）。

---

## Bundled skills

对齐 Claude Code 的 `bundledSkills.ts`：

内置 skill 通过 Python 代码注册，不走文件系统发现。

```python
# skills/bundled/__init__.py

def register_bundled_skill(definition: BundledSkillDef) -> LoadedSkill:
    """注册内置 skill。"""

@dataclass
class BundledSkillDef:
    name: str
    description: str
    when_to_use: str | None = None
    allowed_tools: tuple[str, ...] = ()
    user_invocable: bool = True
    context: Literal["inline", "fork"] | None = None
    agent: str | None = None
    model: str | None = None
    files: dict[str, str] | None = None  # 附带资源文件
    get_prompt: Callable[[str, ToolUseContext], Awaitable[str]]
```

Bundled skills 有 `files` 字段时，首次调用时解压到
`~/.deepcli/bundled-skills/<name>/`，并注入 `Base directory for this
skill: <dir>` 前缀。

---

## MCP skill 集成

MCPManager 连接 MCP server 后，如果 server 暴露了 skill（prompt
类型），通过 `SkillManager.register_mcp_skill()` 注入 registry。

MCP skill 的 body 来自远端，**不执行 inline shell**（安全策略，
与 Claude Code `loadedFrom !== 'mcp'` 检查对齐）。

MCP server 断开时通过 `unregister_mcp_skills()` 清理。

---

## 与 PromptBuilder 的集成

PromptBuilder 在每次 `build()` 时注入 skill listing：

```python
# prompt_builder.py

async def build(self, prompt_text: str = "") -> list[PromptSection]:
    ...
    # Skill listing (可 cache — 同 session 内不变，除非 dynamic discovery)
    skills = self._deps.skills
    if skills is not None:
        listing = skills.get_skill_listing(context_window_tokens)
        if listing:
            sections.append(PromptSection(text=listing, cache=True))
    ...
```

**注意**：listing 注入在系统提示词。Active skill body 不在系统提示词，
而是通过 SkillTool → newMessages 注入为 user message（与 Claude Code
对齐）。

---

## 与 CommandManager 的集成

user-invocable skills 需要出现在命令目录中，让客户端支持
`/skill-name` autocomplete。

```python
# CommandManager.startup() 之后:
for skill in skill_manager.user_invocable_skills():
    command_manager.register(CommandDef(
        name=skill.manifest.name,
        description=skill.manifest.description,
        usage=f"/{skill.manifest.name} {skill.manifest.argument_hint or ''}".strip(),
        acp_method=None,  # 通过 Skill tool 执行，不是直接 ACP 方法
    ))
```

---

## 与 ToolAuthorizer 的集成

SkillTool 激活 skill 后，如果 skill 声明了 `allowed-tools`，需要
临时扩展当前 session 的 tool 权限：

```python
# ToolExecutor 在 SkillTool 返回后:
if result.allowed_tools:
    authorizer.add_session_grants(result.allowed_tools)
```

这与 Claude Code 的 `contextModifier` 对齐 — skill 的 `allowed-tools`
在 skill 激活期间生效。

---

## 与 HookManager 的集成

Skill 可以声明 `hooks` frontmatter，在激活时注册到 HookManager。

```python
# SkillTool.call() 激活后:
if result.hooks:
    hook_manager.register_skill_hooks(skill_name, result.hooks, result.skill_root)
```

Skill deactivate 或 session 结束时，注销 skill-scoped hooks。

---

## 信号机制

SkillManager 需要一个 signal 通知 listing cache 失效（dynamic
discovery / conditional activation 改变了可用 skill 列表）：

```python
class SkillManager:
    skills_changed: Signal  # on_skills_changed callback registration

    async def on_file_touched(self, ...):
        ...
        if newly_activated:
            self.skills_changed.emit()
```

PromptBuilder / SkillTool prompt cache 订阅此 signal 并 invalidate。

---

## 启动顺序

SkillManager 是 Subsystem #8（在 ToolManager 之后、HookManager 之前）：

```
... → MCPManager → ToolManager → SkillManager → HookManager → ...
```

- 依赖 ConfigManager（读取 skills 路径配置）
- 依赖 ToolManager 已启动（SkillTool 注册到 ToolManager）
- 在 HookManager 之前（skill-scoped hooks 注册到 HookManager）

---

## 配置

```yaml
# config.yaml
skills:
  user_dir: "~/.deepcli/skills"        # default
  project_enabled: true                 # 是否加载 project-layer skills
  listing_budget_percent: 0.01          # context window 的百分比
  max_listing_desc_chars: 250           # 单条描述上限

  # 来自 Hermes: 外部 skill 目录
  external_dirs:
    - ~/team-skills
    - /opt/org-skills

  # 来自 Hermes: skill 禁用
  disabled: [deprecated-skill]
  gateway_disabled:
    discord: [interactive-debug]

  # 来自 Hermes: per-skill 配置覆盖
  my-skill:
    max_retries: 5                     # 覆盖 skill 默认值 3
    output_format: "json"              # 覆盖 skill 默认值 "markdown"
```

### Skill config 解析（来自 Hermes）

Skill 在 frontmatter 中声明 `config` 字段定义配置变量和默认值。
Runtime 时从 `config.yaml` 的 `skills.<skill-name>.*` 路径读取
覆盖值，合并后注入 body 中的 `${config.key}` 占位符。

```python
def _resolve_skill_config(
    manifest: SkillManifest,
    global_config: ConfigManager,
) -> dict[str, Any]:
    """合并 skill 声明默认值 + config.yaml 覆盖。"""
    defaults = manifest.config or {}
    overrides = global_config.get(f"skills.{manifest.name}", {})
    return {**defaults, **overrides}
```

---

## Listing 缓存（来自 Hermes 三层缓存，简化为两层）

Hermes 使用三层缓存（内存 LRU → 磁盘 snapshot → 文件系统扫描）
解决大量 skill 时的启动性能问题。我们采用两层（省去 LRU 的复杂度，
Subsystem 生命周期天然提供内存缓存）：

### Layer 1: 内存 registry（primary）

`SkillRegistry` 在 startup 后持有全部 skill metadata。
`get_skill_listing()` 从 registry 生成文本，无 I/O。
`skills_changed` signal 触发 listing text 重建。

### Layer 2: 磁盘 snapshot（加速冷启动）

首次 startup 完成后，将 skill manifest 序列化到
`~/.deepcli/.skills_snapshot.json`（来自 Hermes）：

```json
{
  "version": 1,
  "manifest": {
    "my-skill/SKILL.md": [1713456789000000, 5000]
  },
  "skills": [
    {
      "name": "my-skill",
      "description": "...",
      "source": "user",
      ...
    }
  ]
}
```

下次 startup 时先读 snapshot，校验每个 SKILL.md 的 `mtime` + `size`。
全部命中 → 直接用 snapshot 数据，跳过 frontmatter 解析。
任一失效 → fallback 到完整文件系统扫描 + 写新 snapshot。

**收益**：大量 skill（50+）时，冷启动从逐个解析 YAML 降为一次 JSON
反序列化 + N 次 stat。

---

## 错误处理

与 Claude Code 和 HookManager 对齐：

- **单个 skill 加载失败**：log + skip，不影响其他 skill
- **SKILL.md 格式错误**：ManifestError → warning + skip
- **Eligibility 不满足**：info + skip
- **Body lazy-load 失败**：activate() 时 log + 返回 None
- **整个 SkillManager startup 失败**：Subsystem.load() 返回 None → 降级模式（无 skill）

---

## 测试策略

| 测试类型 | 覆盖范围 |
|---------|---------|
| manifest.py | frontmatter 解析：完整 / 缺失字段 / 类型错误 / unknown keys / description fallback |
| eligibility.py | OS / bins / env 过滤 |
| loader.py | 多层发现顺序 / 去重 / dynamic discovery / conditional activation |
| registry.py | register / lookup / 优先级覆盖 / dynamic vs static |
| arguments.py | $ARGUMENTS / ${name} / ${SKILL_DIR} 替换 |
| SkillManager | startup → listing / activate → body / on_file_touched → dynamic |
| SkillTool | inline activation / fork / permission / unknown skill error |
| integration | PromptBuilder → listing / Compactor → preservation |

---

## 实现顺序

```
1. types.py + manifest.py + eligibility.py       — 类型 + 解析 + supporting files
2. loader.py + registry.py                        — 发现 + 索引 + external_dirs + disabled
3. arguments.py                                   — 参数 + config 替换
4. setup.py                                       — env setup 检查 + 引导 (来自 Hermes)
5. __init__.py (SkillManager)                     — Subsystem 生命周期
6. PromptBuilder 集成 (get_skill_listing)         — listing 注入 + 动态 visibility
7. SkillTool                                      — LLM activation + setup_needed 处理
8. Dynamic discovery + conditional activation     — on_file_touched
9. Bundled skills framework                       — 内置 skill 注册
10. 磁盘 snapshot 缓存                             — 冷启动加速 (来自 Hermes)
11. Compaction preservation                        — invoked skill 重注入
12. MCP skill integration                          — MCPManager 联动
13. CommandManager integration                     — /skill autocomplete
14. HookManager integration                        — skill-scoped hooks
```

---

## 完整对齐清单（Claude Code + Hermes）

### Claude Code 对齐

| Claude Code 能力 | DeepCLI 对应 | 状态 |
|-----------------|-------------|------|
| `loadSkillsFromSkillsDir` — 目录格式 SKILL.md | `loader.py` 多层发现 | 本设计 |
| `parseSkillFrontmatterFields` — 全字段 | `manifest.py` 全字段对齐 | 本设计 |
| `createSkillCommand` — Command 对象构建 | `LoadedSkill` + `ActivationResult` | 本设计 |
| `getSkillDirCommands` — memoized 加载 | `SkillManager.startup()` + registry | 本设计 |
| `getFileIdentity` — realpath 去重 | `loader._dedup()` | 本设计 |
| `discoverSkillDirsForPaths` — 动态发现 | `on_file_touched()` + `discover_for_paths()` | 本设计 |
| `activateConditionalSkillsForPaths` — paths 条件激活 | `activate_conditional()` | 本设计 |
| `getDynamicSkills` — 运行时 skill | `registry._dynamic` | 本设计 |
| `substituteArguments` — `$ARGUMENTS` / `${name}` | `arguments.py` | 本设计 |
| `executeShellCommandsInPrompt` — `!command` inline bash | **不实现** — 安全风险高，低优先 | 排除 |
| `SkillTool.validateInput` — name 验证 | `SkillTool.validate()` | 本设计 |
| `SkillTool.checkPermissions` — allow/deny rules | `SkillTool.authorize()` via ToolAuthorizer | 本设计 |
| `SkillTool.call` — inline + fork | `SkillTool.call()` inline + fork (D14) | 本设计 |
| `formatCommandsWithinBudget` — budget-aware listing | `get_skill_listing()` | 本设计 |
| `addInvokedSkill` / `getInvokedSkillsForAgent` | `_invoked` tracking | 本设计 |
| `createSkillAttachmentIfNeeded` — compaction preservation | `create_skill_attachment()` | 本设计 |
| `registerBundledSkill` — 内置 skill | `bundled/__init__.py` | 本设计 |
| `onDynamicSkillsLoaded` — signal | `skills_changed` signal | 本设计 |
| `clearSkillCaches` — cache invalidation | signal-driven invalidation | 本设计 |
| MCP skills (`loadedFrom: 'mcp'`) | `register_mcp_skill()` | 本设计 |
| Skill-scoped hooks | `hooks` frontmatter → HookManager | 本设计 |
| `contextModifier` — allowed_tools / model override | ToolAuthorizer session grants + model switch | 本设计 |
| Plugin skills | **不实现** — DeepCLI 无 plugin 系统 | 排除 |
| `EXPERIMENTAL_SKILL_SEARCH` — remote/canonical skills | **不实现** — experimental | 排除 |
| Legacy `/commands/` directory | **不实现** — 无历史包袱 | 排除 |

### Claude Code skill 直接复用

| 兼容能力 | DeepCLI 实现 |
|---------|-------------|
| `.claude/skills/` project-layer 发现 | `project_compat_dir` 同优先级扫描 |
| `~/.claude/skills/` user-layer 发现 | `user_compat_dir` 同优先级扫描 |
| `${CLAUDE_SKILL_DIR}` 替换 | `arguments.py` 映射到 `${SKILL_DIR}` |
| Dynamic discovery `.claude/skills/` | `discover_for_paths(claude_compat=True)` |
| `skills.claude_compat: false` 禁用 | 不扫描 `.claude/` 路径 |

### Hermes 增强（超越 Claude Code）

| Hermes 能力 | DeepCLI 对应 | 说明 |
|------------|-------------|------|
| `required_environment_variables` + 交互式 setup | `setup.env` frontmatter + `ActivationResult.setup_needed` | Claude Code 只做 boolean 检查；Hermes 提供引导 prompt + help + secret mask |
| `metadata.hermes.config` — skill 级配置变量 | `config` frontmatter + `config.yaml skills.<name>.*` | skill 声明默认值，运行时从全局 config 覆盖 |
| `requires_tools` / `requires_toolsets` — 条件可见性 | `requires.tools` / `requires.toolsets` | Claude Code 无此能力；skill 自动随 tool 集合变化显隐 |
| `fallback_for_tools` — 降级 skill | `fallback_for` | 主工具可用时隐藏降级替代方案 |
| `external_dirs` — 团队共享 skill 目录 | `config.yaml skills.external_dirs` | Claude Code 仅 CLI `--add-dir`；config 声明更持久 |
| Per-platform `disabled` + `platform_disabled` | `config.yaml skills.disabled` / `skills.gateway_disabled` | 按 gateway 级别禁用 |
| Supporting files progressive disclosure | `manifest.supporting_files` + activation listing | SKILL.md 给核心指令，references/templates/scripts 按需 Read |
| 三层缓存（LRU → 磁盘 snapshot → 扫描） | 两层缓存（registry + 磁盘 snapshot） | 简化但保留磁盘 snapshot 加速冷启动 |
