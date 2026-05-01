# LLMProviderManager

## Purpose

LLMProviderManager 管理 LLM Provider 实例的生命周期。

它的职责只有一件事：**按凭证缓存和复用 Provider 实例**（`AnthropicProvider`、
`OpenAICompatibleProvider`、`BedrockProvider`）。它不知道 model 叫什么名字，
不做路由，不实现 `LLMProvider` Protocol。

路由和 model 名字解析由上层的 **LLMManager** 负责（见 [`llm.md`](llm.md)）。

---

## 核心设计决策

### Provider 按凭证去重

同一套 `(type, api_key, base_url)` 只建一个 Provider 实例，多个 model 条目共享。

```
Provider 配置（用户定义）                   Provider 实例（运行时）
────────────────────────────────────      ─────────────────────────────────
anthropic { api_key: xxx }           ──→  AnthropicProvider(api_key=xxx)
  models: [opus, sonnet]                   (两个 model 共享同一实例)

bedrock   { aws_key: yyy, region }   ──→  BedrockProvider(aws_key=yyy)
  models: [sonnet, haiku]                  (两个 model 共享同一实例)

local     { base_url: localhost }    ──→  OpenAICompatibleProvider(base_url=...)
  models: [qwen3:14b]
```

原因：
- `AnthropicProvider` 内部持有 `AsyncAnthropic` client（HTTP 连接池）
- `OpenAICompatibleProvider` 内部持有 `httpx.AsyncClient`
- 相同凭证建多个实例 = 重复建连接池，浪费资源

### LLMProviderManager 不读 model 配置

`LLMProviderManager` 自身没有 config section。它只提供 `get_provider()` 方法，
由 `LLMManager` 在 startup 时主动传入凭证来驱动 Provider 实例的创建。

---

## 对外接口

```python
class LLMProviderManager(Subsystem):

    def get_provider(
        self,
        *,
        provider_type: str,           # "anthropic" | "bedrock" | "openai_compatible" | "nvidia"
        api_key: str | None,
        base_url: str | None,
        prompt_caching: bool = True,  # Anthropic 专有，影响 client 行为
        thinking: bool = False,       # Anthropic 专有
    ) -> Provider:
        """
        返回与凭证匹配的 Provider 实例，按 (type, api_key, base_url) 去重。
        首次调用时创建并缓存；后续调用直接返回缓存实例。

        由 LLMManager.startup() 调用，不对 Orchestrator 或 Session 暴露。
        """
```

> `get_provider()` 是同步方法——Provider 构造函数不做 I/O（SDK client 懒连接）。

---

## `Provider` ABC（内部抽象）

每个 Provider 实现继承此 ABC。不对外暴露，只在 `kernel/llm_provider/` 内部使用。

```python
# kernel/llm_provider/base.py

class Provider(ABC):
    """
    单个 LLM 端点的通信实现。
    持有 SDK client，负责"怎么和这家 API 对话"。
    不知道 model 叫什么逻辑名，只接收 model_id。
    """

    @abstractmethod
    def stream(
        self,
        *,
        system: list[PromptSection],
        messages: list[Message],
        tool_schemas: list[ToolSchema],
        model_id: str,         # 直接发给 API 的 model 标识，不是用户定义的逻辑名
        temperature: float | None,
        thinking: bool,
        max_tokens: int,
    ) -> AsyncGenerator[LLMChunk, None]: ...

    @abstractmethod
    async def models(self) -> list[ModelInfo]: ...

    async def context_window(self, model_id: str) -> int | None:
        return None

    async def aclose(self) -> None:
        """关闭底层连接（如 httpx client）。默认空实现。"""
```

### Provider 实现列表

| 实现类 | 文件 | 支持后端 |
|---|---|---|
| `AnthropicProvider` | `anthropic.py` | Anthropic Messages API |
| `BedrockProvider` | `bedrock.py` | AWS Bedrock（继承 AnthropicProvider，强制 prompt_caching=False）|
| `OpenAICompatibleProvider` | `openai_compatible.py` | OpenAI / Ollama / 其他兼容接口 |
| `NvidiaProvider` | `nvidia.py` | NVIDIA NIM API（继承 OpenAICompatible，默认 base_url 指向 NIM）|

`OpenAICompatibleProvider` 使用 `httpx.AsyncClient` + 手动 SSE 解析，
**不使用 openai SDK**（该 SDK 历史上不稳定）。

---

## `LLMProviderManager` 实现要点

```python
class LLMProviderManager(Subsystem):

    async def startup(self) -> None:
        # 无 config section，_providers 从空开始，
        # 由 LLMManager 调用 get_provider() 填充
        self._providers: dict[tuple, Provider] = {}

    async def shutdown(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
        self._providers.clear()

    def get_provider(self, *, provider_type, api_key, base_url, **kwargs) -> Provider:
        key = (provider_type, api_key, base_url)
        if key not in self._providers:
            self._providers[key] = _create_provider(
                provider_type, api_key, base_url, **kwargs
            )
        return self._providers[key]
```

---

## 文件布局

```
kernel/llm_provider/
  __init__.py             # LLMProviderManager (Subsystem)
  base.py                 # Provider ABC
  errors.py               # ProviderError, PromptTooLongError
  anthropic.py            # AnthropicProvider
  bedrock.py              # BedrockProvider
  openai_compatible.py    # OpenAICompatibleProvider
  nvidia.py               # NvidiaProvider (继承 OpenAICompatible)
  format/
    __init__.py
    anthropic.py          # universal → Anthropic API 格式转换（纯函数）
    openai.py             # universal → OpenAI API 格式转换（纯函数）
```

Universal 类型（`LLMChunk`、`PromptSection`、`Message`、`ToolSchema`）
定义在 `kernel/llm/types.py`，Provider 实现从那里 import。

---

## 与其他子系统的关系

| 子系统 | 关系 |
|---|---|
| **LLMManager** | 唯一调用方。在 `startup()` 时调 `get_provider()` 创建并缓存 Provider 实例 |
| **Config** | 无直接关系。LLMProviderManager 不读 config；凭证由 LLMManager 从 config 读取后传入 |

---

## 设计约束

- **Provider 是无状态的通信层**：每次 `stream()` 调用完全无状态，Provider
  不持有对话历史，不做路由，只管"怎么调这个 API"。

- **格式转换在 Provider 实现里**：`kernel/llm/types.py` 的 universal 类型
  → SDK native format 的转换完全封装在各 Provider 实现内的 `format/` helpers 里。

- **`ToolUseChunk` 是完整的**：Provider 实现在 `content_block_stop` 时
  一次性 `json.loads()` 后 emit，Orchestrator 不处理流式 JSON 片段。

- **`StreamError` 不抛异常**：可恢复错误（限流、临时 API 故障）以
  `StreamError` chunk 形式 yield。不可恢复错误（认证失败、配置错误）
  在 Provider 构造时或 `stream()` 开始前 raise `ProviderError`。

- **`BedrockProvider` 强制关闭 prompt caching**：Bedrock 不支持 Anthropic
  的 prompt caching 机制，`stream()` override 始终传 `prompt_caching=False`。
  `AsyncAnthropicBedrock` 在 `anthropic` 主包中，无需额外依赖。
