# LLMManager

## Purpose

LLMManager 管理用户定义的 provider 配置，实现 `LLMProvider` Protocol，
是 Orchestrator 的 `deps.provider` 的具体实现者。

它是 Provider 层和 Orchestrator 之间的路由层：

```
Orchestrator
    ↓ stream(model=ModelRef("anthropic", "claude-opus-4-6"))
LLMManager                 ← 本文档
    ↓ _resolve(ref) → (ModelSpec, Provider)
    ↓ provider_manager.get_provider(type, api_key, base_url)
LLMProviderManager            ← llm_provider.md
    ↓ 返回 Provider 实例（按凭证缓存）
Provider.stream(model_id="claude-opus-4-6", ...)
```

职责边界：

- ✅ 读取并持有 provider 配置（`providers` dict）
- ✅ alias 解析（`"opus"` → `ModelRef("anthropic", "claude-opus-4-6")`）
- ✅ 路由：ModelRef → Provider 实例 + model_id + 参数
- ✅ 实现 `LLMProvider` Protocol（`stream` / `models` / `context_window` / `model_for`）
- ✅ 实现 `ModelHandler` Protocol（`list_providers` / `add_provider` / `remove_provider` / `refresh_models` / `set_default_model`）
- ✅ 暴露 `current_used` 角色映射（`model_for(role)`）给 Session / Orchestrator 层
- ✅ 运行时 provider CRUD + 持久化（通过 `MutableSection.update()`）
- ❌ 不持有 Provider 实例（交给 LLMProviderManager）
- ❌ 不管对话历史（Orchestrator 的事）
- ❌ 不管 tool 执行（ToolExecutor 的事）
- ❌ 不管 system prompt 组装（PromptBuilder 的事）

---

## Provider-centric 配置

凭据和模型分离：Provider 持有凭据 + 模型列表，避免同一账号下多个模型重复填写凭据。

```yaml
llm:
  providers:
    bedrock:
      type: bedrock
      api_key: AKIA...
      aws_secret_key: secret...
      aws_region: us-east-1
      models:                                  # 手动填写
        - us.anthropic.claude-sonnet-4-6
        - id: us.anthropic.claude-haiku-4-5
          max_tokens: 4096

    anthropic:
      type: anthropic
      api_key: sk-ant-xxx
      models: null                             # null → 自动发现

  current_used:
    default: [anthropic, claude-opus-4-6]
    bash_judge: [bedrock, us.anthropic.claude-haiku-4-5]

  model_aliases:
    opus: [anthropic, claude-opus-4-6]
```

---

## 两个 Protocol

`LLMManager` 同时实现两个 Protocol，服务于不同调用方：

| Protocol | 调用方 | 职责 |
|---|---|---|
| `LLMProvider` | Orchestrator | 流式 LLM 调用路由（`stream` / `models` / `context_window`） |
| `ModelHandler` | ACP 协议层 | 运行时 provider 管理（CRUD + 持久化） |

两个 Protocol 之间没有交叉：Orchestrator 永远不知道 provider CRUD 接口；协议层永远不知道 `stream` 细节。

---

## 配置 Schema（`kernel/llm/config.py`）

```python
class ModelSpec(BaseModel):
    """单个模型的配置，支持 str 简写。"""
    id: str
    max_tokens: int = 8192
    thinking: bool = False
    prompt_caching: bool = True

class ProviderConfig(BaseModel):
    """一个 provider 条目：凭据 + 模型列表。"""
    type: str                        # "anthropic" | "bedrock" | "openai_compatible" | "nvidia"
    api_key: str | None = None
    base_url: str | None = None
    aws_secret_key: str | None = None
    aws_region: str | None = None
    models: list[ModelSpec] | None = None  # None = 自动发现

class ModelRef(BaseModel):
    """(provider, model_id) 二元组，YAML 中写作 [provider, model_id]。"""
    provider: str
    model: str

class CurrentUsedConfig(BaseModel):
    default: ModelRef
    bash_judge: ModelRef | None = None
    memory: ModelRef | None = None
    embedding: ModelRef | None = None

class LLMConfig(BaseModel):
    providers: dict[str, ProviderConfig] = {}
    current_used: CurrentUsedConfig
    model_aliases: dict[str, ModelRef] = {}
```

---

## 模型发现

| Provider | `models: null` 时的行为 |
|----------|------------------------|
| Anthropic | `client.models.list()` 自动填充 |
| OpenAI Compatible | `GET /v1/models` 自动填充 |
| Nvidia | 同 OpenAI Compatible |
| Bedrock | 报错，要求手动填写（AWS 无干净的发现 API） |

发现时机：添加 provider 时发现一次，结果持久化到 config。
提供 `refresh_models` 命令手动刷新。

---

## Model 解析

```
ModelRef("anthropic", "claude-opus-4-6")
    → providers["anthropic"] → ProviderConfig
    → 找 models 中 id="claude-opus-4-6" 的 ModelSpec
    → provider_manager.get_provider(type, api_key, ...) → Provider 实例
    → provider.stream(model_id="claude-opus-4-6", ...)

"opus" (alias string)
    → aliases["opus"] → ModelRef("anthropic", "claude-opus-4-6")
    → 同上
```

未知 ref 直接 raise `ModelNotFoundError`，没有 fallback 逻辑。

---

## ACP 协议层看到的接口（`ModelHandler` Protocol）

```python
class ModelHandler(Protocol):
    async def list_providers(...) -> ListProvidersResult: ...
    async def add_provider(...) -> AddProviderResult: ...
    async def remove_provider(...) -> RemoveProviderResult: ...
    async def refresh_models(...) -> RefreshModelsResult: ...
    async def set_default_model(...) -> SetDefaultModelResult: ...
```

| ACP 方法 | ModelHandler 方法 |
|---|---|
| `model/provider_list` | `list_providers` |
| `model/provider_add` | `add_provider` |
| `model/provider_remove` | `remove_provider` |
| `model/provider_refresh` | `refresh_models` |
| `model/set_default` | `set_default_model` |

---

## Probe REPL 命令

```
/provider list                                     列出所有 provider 及其模型
/provider add <name> <type> [--api-key ...] ...    添加 provider
/provider remove <name>                            删除 provider
/provider refresh <name>                           重新发现模型
/model default <provider> <model_id>               设置默认模型
/model list                                        列出所有可用模型
```

---

## 文件布局

```
kernel/llm/
  __init__.py      # LLMManager (Subsystem, 实现 LLMProvider + ModelHandler Protocol)
  types.py         # LLMChunk、PromptSection、Message、ToolSchema、ModelInfo
  config.py        # ProviderConfig、ModelSpec、ModelRef、LLMConfig（Pydantic schema）
  errors.py        # ModelNotFoundError
```

```
kernel/llm_provider/   # Provider 实现（内部，不对外暴露）
  __init__.py      # LLMProviderManager (Subsystem)
  base.py          # Provider ABC + discover_models()
  errors.py        # ProviderError、PromptTooLongError
  anthropic.py     # discover_models() via client.models.list()
  bedrock.py       # discover_models() returns [] (manual only)
  openai_compatible.py  # discover_models() via GET /v1/models
  nvidia.py        # inherits OpenAI Compatible discovery
  format/
    anthropic.py
    openai.py
```

---

## 启动顺序

```python
# kernel/app.py
_CORE_SUBSYSTEMS = [
    ("provider",    LLMProviderManager),   # 先起：建 Provider 实例缓存
    ("llm",         LLMManager),           # 后起：读 config，调 get_provider() 预热缓存
]
```

---

## 设计约束

- **LLMManager 不持有对话状态**：每次 `stream()` 完全无状态，对话历史由
  Orchestrator 维护。

- **`ModelNotFoundError` 不静默降级**：未知 model ref 立即 raise，
  不 fallback 到 `current_used.default`。

- **Provider 实例由 LLMProviderManager 独占管理**：LLMManager 只持有
  `LLMProviderManager` 引用，不直接存储 Provider 实例。

- **凭据不重复**：同一个 provider 下的多个模型共享一组凭据。
  改 API key 只需改一处 provider 配置。


---

## Appendix: current_used 角色表重构

# LLMManager — `default_model` → `current_used` 重构

Status: **pending**

---

## 动机

当前 `LLMConfig.default_model: str` 是扁平字段，对未来扩展（compact / vision
/ small 等角色）不友好。用户要求改成：

```yaml
llm:
  current_used:
    default: claude-sonnet
    # 未来：
    # compact: claude-haiku
```

把"某个角色当前用什么 model"建模成 role → model 映射。"default" 只是其
中一个角色，不是特殊 meta 字段。Orchestrator / Compactor / 未来的
Vision 工具都通过 `llm_manager.model_for(role)` 取自己需要的模型。

---

## 目标

1. 配置文件结构：`llm.current_used.default` 取代 `llm.default_model`
2. 内部 API：`llm_manager.model_for("default")` 取代
   `llm_manager.default_model`
3. ACP wire format：保持不变（一期不动客户端合约）
4. 测试 + 文档全部更新
5. 无需兼容旧 config——直接破坏式迁移（rewrite 期无存量用户）

## 非目标

- 不新增 `compact` / `vision` 等角色（留给后续 PR，等 Compactor
  真需要更便宜 model 时再加）
- 不改 ACP wire schema（`ListProfilesResponse.default_model` / `SetDefaultModelResponse.default_model` 保留）
- 不引入 `set_role_model(role, name)` 通用方法（YAGNI；等第二个角色出现时再加）

---

## 设计

### 新配置 schema

```python
# kernel/llm/config.py

class CurrentUsedConfig(BaseModel):
    """角色 → model ref 映射。`default` 是唯一必填角色。"""
    default: str = "claude-opus"
    # 未来在这里新增字段即可，不影响调用方。

class LLMConfig(BaseModel):
    current_used: CurrentUsedConfig = CurrentUsedConfig()
    models: dict[str, ModelEntryConfig] = {}
    model_aliases: dict[str, str] = {}
```

### LLMManager 内部状态

```python
# kernel/llm/__init__.py

async def startup(self) -> None:
    ...
    self._current_used: CurrentUsedConfig = config.current_used
    # startup 结束前验证：每个角色都能解析到一个已注册 model。
    for role_name, model_ref in self._current_used.model_dump().items():
        if model_ref is None:
            continue
        self._resolve(model_ref)   # raise ModelNotFoundError if bad
```

### 新公开 API

```python
# LLMProvider Protocol 新增一个方法，替代 `default_model` property：

def model_for(self, role: str) -> str:
    """Return the model ref assigned to ``role``.

    Raises ``KeyError`` for unknown roles. 当前唯一 role 是 "default"。
    """
    value = getattr(self._current_used, role, None)
    if value is None:
        raise KeyError(f"Unknown role: {role}")
    return value
```

保留 `set_default_model` ACP method —— 它就是 "set
`current_used.default`" 的字面实现，方法名不改。

### 持久化

```python
async def _persist(self) -> None:
    current = self._cfg_section.get()
    new_config = LLMConfig(
        current_used=self._current_used,
        models=dict(self._model_configs),
        model_aliases=current.model_aliases,
    )
    await self._cfg_section.update(new_config)
```

`remove_profile` 内的"如果删掉了 default 就换下一个" 逻辑：

```python
if self._current_used.default == params.name:
    self._current_used.default = next(iter(self._model_configs))
```

`set_default_model`:

```python
self._current_used.default = resolved
```

---

## 变更点清单

### 代码（src/kernel/kernel）

| 文件 | 变更 |
|------|------|
| `llm/config.py` | 新增 `CurrentUsedConfig`；`LLMConfig.default_model` → `LLMConfig.current_used` |
| `llm/__init__.py` | `self._default_model` → `self._current_used`；`@property default_model` → `def model_for(role)`；startup 加 current_used 解析验证；`_persist` / `remove_profile` / `set_default_model` / `list_profiles` 跟改 |
| `orchestrator/orchestrator.py` | L101-104 `deps.provider.default_model` → `deps.provider.model_for("default")`；`hasattr` 检查改成 `callable(getattr(deps.provider, "model_for", None))`（测试用的 FakeProvider 需要知情） |
| `session/__init__.py` | L1499 `llm_manager.default_model` → `llm_manager.model_for("default")` |
| `gateways/base.py` | L324 `key == llm.default_model` → `key == llm.model_for("default")` |

### 协议层（src/kernel/kernel/protocol）

| 文件 | 变更 |
|------|------|
| `interfaces/model_handler.py` | `set_default_model` 签名不变；docstring 小幅调整（角色语义） |
| `interfaces/contracts/list_profiles_result.py` | `default_model: str` 字段名保留（wire 稳定） |
| `interfaces/contracts/set_default_model_result.py` | `default_model: str` 字段名保留 |
| `acp/schemas/model.py` | `ListProfilesResponse.default_model` / `SetDefaultModelResponse.default_model` 保留 |
| `acp/routing.py` | 无改动（仍然读 `result.default_model`，`list_profiles` 内部从 `current_used.default` 填这个字段） |

### 测试

| 文件 | 变更 |
|------|------|
| `tests/kernel/llm/test_llm_manager.py` | `_make_manager(..., default_model=...)` helper 改成接受 `current_used` 或 `default=...`；所有用例 `manager.default_model` → `manager.model_for("default")` |
| `tests/kernel/session/test_session_manager.py` | 如有 mock `default_model` property 的地方，改成 mock `model_for` |
| `tests/kernel/test_lifespan.py` | config fixture 里 `default_model:` → `current_used: { default: ... }` |
| `tests/kernel/orchestrator/conftest.py` | FakeProvider 的 `default_model` property 改成 `model_for(role)` 方法 |
| `tests/e2e/test_kernel_e2e.py` | 若其中组装 config 用到 `default_model`，改成 `current_used` |

### 文档

| 文件 | 变更 |
|------|------|
| `docs/kernel/subsystems/llm.md` | ✅ 已在本次变更里更新 |
| `docs/kernel/subsystems/orchestrator.md` | ✅ 已更新 `LLMProvider` Protocol 示意 |
| `docs/kernel/interfaces/protocol.md` | 无需动（ACP wire 保持；`set_default_model` 方法名保留） |
| `docs/plans/progress.md` | 合并后加一行 summary |

### 配置迁移

- 本仓库内 `docs/` 里搜索 `default_model` 的例子：`docs/kernel/subsystems/llm.md` 已改；无其他示例。
- 用户本地 `~/.mustang/config/kernel.yaml` 可能含旧字段：
  - 实际行为：`LLMConfig` 沿用默认 `extra="ignore"`，启动时会**静默丢弃**
    旧的 `default_model: X`，回落到新 schema 默认
    `current_used.default = "claude-opus"`。不会 raise。
  - 不做自动迁移（rewrite 期无"正式"用户）；用户手动把
    `default_model: X` 改成 `current_used: { default: X }` 即可。
  - 符合 "no backcompat shims" 约定；如需更响亮的失败可后续给 `LLMConfig` 加
    `model_config = ConfigDict(extra="forbid")`，但与其他 kernel config
    一致性更重要，暂不加。

---

## 实施顺序（单 PR）

1. 改 `llm/config.py`：新增 `CurrentUsedConfig`，重构 `LLMConfig`
2. 改 `llm/__init__.py`：`_current_used` 字段、`model_for` 方法、`_persist` / startup 校验
3. 改 orchestrator / session / gateway 三个调用点
4. 改测试 fixture + 用例
5. 运行 `pytest tests/kernel` 确认绿
6. 运行 `src/probe` e2e 手验 `/model set-default sonnet` 路径
7. `docs/plans/progress.md` 加一行

---

## 预计影响范围

- 代码行数：~60 行净变更
- 测试：~30 行 fixture 调整 + 若干断言替换
- 破坏性：本地 yaml 需手改一次（`default_model: X` → `current_used: { default: X }`）

## 风险

1. **ACP wire field name `default_model` 与内部语义轻微错位**——
   可接受，字段名本身就是"默认角色 model"的缩写；未来要加 compact 时再看
   是否需要新的 ACP method（例如 `model/set_current_used`）。
2. **遗漏的测试 fixture**——跑全套 pytest 即可兜底。
3. **FakeProvider 在测试代码里重复定义**——grep `default_model` in
   tests 统一处理。
