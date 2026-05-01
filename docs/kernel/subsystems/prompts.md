# PromptManager — Design

Status: **landed** — shipped as bootstrap service (Phase 8, D18).

## 0. 动机

D18 规定"所有 prompt 文本放在 `.txt` 文件里，`.py` 文件不许写 prompt
文本"。Daemon 时代 `engine/prompts/` 有 16 个 `.txt` 文件严格执行了这条
规则。Kernel rewrite 只迁移了 `orchestrator/prompts/base.txt`，其余全部
硬编码在 Python 中，严重违反 D18：

| 位置 | 变量 | 内容 |
|------|------|------|
| `orchestrator/compactor.py:40-48` | `_COMPACT_SYSTEM` | 会话摘要 system prompt |
| `orchestrator/compactor.py:51` | `_COMPACT_PROMPT_PREFIX` | 摘要用户消息前缀 |
| `orchestrator/compactor.py:253` | 字面量 | 摘要失败兜底占位符 |
| `orchestrator/history.py:230` | f-string | 摘要注入 header |
| `orchestrator/orchestrator.py:632` | f-string | system-reminder XML wrapper |
| `tool_authz/bash_classifier.py:49-63` | `_SYSTEM_PROMPT` | Bash 安全分类器 system prompt |
| `tool_authz/bash_classifier.py:66-70` | `_USER_TEMPLATE` | Bash 分类器 user message 模板 |

此外，随着 SkillManager / MemoryManager / 更多 Tool 的实装，prompt 数量
只会增长。如果不建立集中管理机制，散落的 prompt 会越来越难审计、版本控制、
A/B 测试和国际化。

**目标**：建立一个 PromptManager 子系统 + 磁盘 prompt 文件树，一次性清偿
D18 技术债，并为后续模块提供统一的 prompt 加载/模板渲染接口。

---

## 1. 设计原则

1. **文件是权威** — prompt 文本只存在于 `.txt`（或 `.md`）文件中，Python
   代码只做加载和模板渲染。
2. **按模块组织** — prompt 文件按所属子系统分目录，目录结构镜像
   `kernel/` 的包结构。
3. **零魔法** — `Path.read_text()` 加载，`str.format()` 渲染模板。
   不引入 Jinja2 等第三方模板引擎。
4. **启动期加载，运行期只读** — 所有 prompt 在 `startup()` 时加载到内存，
   运行期通过 key 查找，不做文件 I/O。
5. **向后兼容** — 现有消费方（`PromptBuilder`、`Compactor`、
   `BashClassifier`）的公开接口不变，内部改用 PromptManager 加载。
6. **不是 Subsystem** — PromptManager 是 bootstrap 服务（同 FlagManager /
   ConfigManager），启动失败即 abort kernel，不降级。

---

## 2. 文件树结构

```
src/kernel/kernel/
├── prompts/                          # PromptManager 包
│   ├── __init__.py                   # re-export PromptManager
│   ├── manager.py                    # PromptManager 类
│   └── default/                      # 默认 prompt 文件树（用户可覆盖）
│       ├── orchestrator/
│       │   ├── base.txt              # 现有 base prompt（从 orchestrator/prompts/ 迁移）
│       │   ├── compact_system.txt    # 会话摘要 system prompt
│       │   ├── compact_prefix.txt    # 摘要用户消息前缀
│       │   ├── compact_fallback.txt  # 摘要失败兜底
│       │   ├── summary_header.txt    # 摘要注入 history 的 header 模板
│       │   └── system_reminder.txt   # system-reminder XML wrapper 模板
│       ├── tool_authz/
│       │   ├── bash_classifier_system.txt   # Bash 安全分类器 system prompt
│       │   └── bash_classifier_user.txt     # Bash 分类器 user message 模板
│       └── _index.yaml               # prompt 注册清单（可选，见 §4）
│   # 用户覆盖层（运行期发现，优先级高于 default/）：
│   # ~/.mustang/prompts/<module>/<name>.txt       — 用户全局覆盖
│   # <project>/.mustang/prompts/<module>/<name>.txt — 项目级覆盖
```

### 命名规则

- 目录名 = kernel 子系统包名（`orchestrator`、`tool_authz`、`tools`、
  `memory`、`skills` …）
- 文件名 = `snake_case.txt`，描述 prompt 用途
- 模板占位符用 `{placeholder}` 语法（`str.format()` 兼容）
- 纯静态 prompt 不含占位符

### 未来扩展

新子系统的 prompt 直接在 `default/` 下建同名目录：

```
default/
├── memory/
│   ├── instructions.txt          # 记忆工具教学
│   ├── extract.txt               # 自动提取子代理模板
│   └── lint.txt                  # /memory lint 工作流
├── skills/
│   └── injection_preamble.txt    # 技能注入前言
```

---

## 3. PromptManager API

```python
class PromptManager:
    """Bootstrap 服务：加载并管理所有内置 prompt 文本。

    启动期一次性读取 default/ 目录树，运行期通过 key 查找。
    """

    def __init__(
        self,
        defaults_dir: Path | None = None,
        user_dirs: list[Path] | None = None,
    ) -> None:
        """
        Args:
            defaults_dir: 内置 prompt 文件根目录。默认为
                ``<package>/default/``。
            user_dirs: 用户覆盖目录列表，按优先级从低到高排列。
                文件同 key 时，后面的目录覆盖前面的。
                目录不存在时静默跳过。
        """

    def load(self) -> None:
        """加载内置 defaults，再依次覆盖 user_dirs。

        Key 规则：相对路径去掉 .txt 后缀，用 "/" 分隔。
        例如 ``default/orchestrator/base.txt`` → ``"orchestrator/base"``

        Raises:
            PromptLoadError: defaults_dir 不存在或文件读取失败（启动 abort）。
        """

    def get(self, key: str) -> str:
        """按 key 查找 prompt 原文（不做模板渲染）。

        Args:
            key: 如 ``"orchestrator/base"``、
                 ``"tool_authz/bash_classifier_system"``

        Raises:
            KeyError: key 不存在。
        """

    def render(self, key: str, **kwargs: str) -> str:
        """查找 prompt 并用 str.format() 渲染模板变量。

        Args:
            key: prompt key。
            **kwargs: 模板占位符值。

        Raises:
            KeyError: key 不存在。
            KeyError: 模板中有未提供的占位符。

        Example::

            text = pm.render(
                "tool_authz/bash_classifier_user",
                command="ls -la",
                cwd="/home/user",
            )
        """

    def keys(self) -> list[str]:
        """返回所有已加载的 prompt key 列表。"""

    def has(self, key: str) -> bool:
        """检查 key 是否存在。"""
```

### 错误类型

```python
class PromptLoadError(Exception):
    """default/ 目录不存在或文件读取失败。"""

class PromptKeyError(KeyError):
    """请求了不存在的 prompt key。"""
```

---

## 4. `_index.yaml` — 可选注册清单

`default/_index.yaml` 是可选的元数据文件，用于文档化和校验：

```yaml
# 每个条目对应一个 .txt 文件
prompts:
  - key: orchestrator/base
    description: 核心 system prompt，cacheable
    source: mustang        # mustang | adapted | verbatim
    cacheable: true
    has_placeholders: false

  - key: tool_authz/bash_classifier_user
    description: Bash 安全分类器 user message 模板
    source: mustang
    cacheable: false
    has_placeholders: true
    placeholders: [command, cwd]
```

**PromptManager 不依赖此文件运行** — 它是纯文档 + CI 校验用途。
可以在 CI 中检查：所有 `.txt` 文件都有对应条目、所有条目都有对应文件、
声明的 `placeholders` 与文件中的 `{xxx}` 一致。

---

## 5. 集成方式

### 5.1 生命周期

PromptManager 是 bootstrap 服务，在 `app.py` lifespan 中最早加载
（在 FlagManager / ConfigManager 之后，所有 Subsystem 之前）：

```python
# app.py lifespan
async with lifespan(...):
    flags = FlagManager(...)
    config = ConfigManager(...)
    prompts = PromptManager()       # ← 新增
    prompts.load()                  # 启动失败 → abort

    # 传入各 Subsystem
    orchestrator = Orchestrator(prompts=prompts, ...)
    tool_authz = ToolAuthorizer(prompts=prompts, ...)
```

### 5.2 消费方改造

#### PromptBuilder（`orchestrator/prompt_builder.py`）

```python
# Before:
_BASE_PROMPT = (_PROMPTS_DIR / "base.txt").read_text(...)

# After:
class PromptBuilder:
    def __init__(self, prompts: PromptManager, ...):
        self._prompts = prompts

    async def build(self, ...):
        sections.append(PromptSection(
            text=self._prompts.get("orchestrator/base"),
            cache=True,
        ))
```

#### Compactor（`orchestrator/compactor.py`）

```python
# Before:
_COMPACT_SYSTEM = PromptSection(text="You are a conversation summariser...")

# After:
class Compactor:
    def __init__(self, prompts: PromptManager, ...):
        self._compact_system = PromptSection(
            text=prompts.get("orchestrator/compact_system"),
            cache=False,
        )
        self._compact_prefix = prompts.get("orchestrator/compact_prefix")
```

#### BashClassifier（`tool_authz/bash_classifier.py`）

```python
# Before:
_SYSTEM_PROMPT = "You are a security classifier..."
_USER_TEMPLATE = "<command>\n{command}\n</command>..."

# After:
class BashClassifier:
    def __init__(self, prompts: PromptManager, ...):
        self._system = prompts.get("tool_authz/bash_classifier_system")
        self._user_tpl = prompts.get("tool_authz/bash_classifier_user")

    async def classify(self, command: str, cwd: str, ...):
        user_msg = self._prompts.render(
            "tool_authz/bash_classifier_user",
            command=command, cwd=cwd,
        )
```

### 5.3 旧目录清理

迁移完成后删除 `orchestrator/prompts/` 目录（`base.txt` 已移至
`prompts/default/orchestrator/base.txt`）。

---

## 6. 不做什么

| 选项 | 决定 | 理由 |
|------|------|------|
| Jinja2 模板 | 不用 | `str.format()` 足够，零依赖 |
| 运行期 hot-reload | 不做 | 启动加载即可，hot-reload 增加复杂度且无当前需求 |
| prompt 版本控制 | 不做 | git 已经提供了完整的版本历史 |
| prompt A/B 测试框架 | 不做 | 超出当前范围，未来可通过 key 别名实现 |
| 继承 Subsystem | 不做 | bootstrap 服务，不需要降级语义 |
| 用户自定义 prompt 覆盖 | **已实现（M2）** | `~/.mustang/prompts/`（global）和 `<project>/.mustang/prompts/`（project-local）；project 优先；目录不存在静默跳过 |
| `.md` 格式 | 不用 | `.txt` 更简单，prompt 不需要 frontmatter |
| 国际化 | 不做 | 当前无需求，未来可通过 locale 子目录扩展 |

---

## 7. 实装步骤

### M1 — PromptManager 核心 + 迁移现有 prompt

1. 创建 `kernel/prompts/` 包：`__init__.py`、`manager.py`
2. 创建 `kernel/prompts/default/` 目录树，把 7 段硬编码 prompt
   提取为 `.txt` 文件
3. 把 `orchestrator/prompts/base.txt` 移入
   `prompts/default/orchestrator/base.txt`
4. 实现 `PromptManager.load()` / `get()` / `render()`
5. 在 `app.py` lifespan 中注册 PromptManager
6. 改造 `PromptBuilder`、`Compactor`、`BashClassifier` 使用
   PromptManager
7. 删除旧的 `orchestrator/prompts/` 目录
8. 删除 `.py` 文件中的硬编码 prompt 文本

### M2 — 未来子系统 prompt（按需）

SkillManager / MemoryManager 实装时，直接在 `default/` 下建
对应目录放置 prompt 文件。

---

## 8. 测试策略

1. **单元测试** — `PromptManager` 的 `load()`、`get()`、`render()`、
   key 不存在 raise、占位符缺失 raise
2. **完整性检查** — `default/` 下每个 `.txt` 文件都能被
   `PromptManager.load()` 正确加载
3. **集成测试** — `PromptBuilder.build()` 返回的 sections 包含
   正确的 prompt 文本（不再依赖文件直接读取）
4. **可选 CI** — 如果有 `_index.yaml`，校验文件 ↔ 条目一致性

---

## 9. 与 D18 的关系

本设计是 D18 在 kernel 时代的完整落地。D18 原文：

> 所有 prompt 字符串与模板放在 `src/kernel/kernel/**/prompts/*.txt`，
> Python 模块 import 时 `Path.read_text()` 加载。`.py` 文件里不许写
> prompt 文本。

本设计把分散的 `**/prompts/` 统一收归到 `kernel/prompts/default/`，
同时保留了 D18 的核心约束：文本在 `.txt`，Python 只做加载。统一入口
（PromptManager）是 D18 没有规定但自然演化出来的管理层。

实装后应更新 D18 的描述，指向 `kernel/prompts/` 而非
`**/prompts/*.txt`。
