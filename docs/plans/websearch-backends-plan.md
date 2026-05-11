# WebSearch Backend 细化与管理入口计划

状态：**proposed**

日期：**2026-05-11**

## 当前事实

DeepCLI 已经有 `WebSearchTool`，而且 DuckDuckGo fallback 已经能工作：

- `WebSearchTool`：`src/kernel/kernel/agents/mustang/tools/builtin/web_search.py`
- `SearchBackend` ABC：`src/kernel/kernel/agents/mustang/tools/web/search_backends/base.py`
- DuckDuckGo backend：`src/kernel/kernel/agents/mustang/tools/web/search_backends/duckduckgo.py`
- fallback chain：`src/kernel/kernel/agents/mustang/tools/web/search_backends/__init__.py`

所以本计划不是“实现 WebSearch”。本计划只做一件事：

```text
把已有 WebSearch 的 backend 层细化成可配置、可观察、可验证、可扩展的系统。
```

## 最终交付

用户最后得到的是 **WebSearch backend 管理能力**，不是一个新的搜索工具：

```text
/websearch backend      # 选择默认搜索后端
/websearch config       # 配置后端参数 / API key
WebSearch               # 已存在；继续作为 LLM 唯一搜索工具
```

第一版完成后，用户可以：

- 用 `/websearch backend` 看到现有后端、缺什么 key、哪个免费、哪个更适合国内。
- 用 `/websearch config searxng.base_url http://127.0.0.1:8080` 配置免费自托管搜索。
- 用 `/websearch config bocha.api_key` 以 secret input 保存 API key。
- 选择或保存配置时，系统自动真实搜索一次确认 backend 能用。
- 继续让 LLM 调已有 `WebSearch(query=...)`，只是 backend 选择更清楚。

## 原始需求

`WebSearch` 的根本需求是：**给模型找到可信来源 URL**。现在这个工具已经存在，
问题在 backend 层：

- 已有后端是“代码里能 fallback”，但用户不知道有哪些后端。
- API key / base URL 主要靠 env，不像 `/webfetch` 那样可配置。
- 新增 SearXNG / Bocha / Metaso 等后端前，没有统一 inventory 和验收口径。
- OpenClaw 支持的 provider 矩阵没有变成 DeepCLI 的可执行 backend 清单。

所以实现必须回答四个问题：

1. 用户怎么配置搜索后端？
2. 现有 `WebSearch` 如何读取用户选择的 backend？
3. 每个后端需要哪些参数和凭证？
4. 每个后端参考谁实现，验收时怎么证明它真的能用？

## 不做什么

- 不新增 `MUSTANG_SEARCH_REGION` 或 `web_search.region`。
- 不把搜索拆成 `ChinaSearch` / `GlobalSearch`。
- 不让用户直接编辑 env var 才能完成配置；env 只作为兼容输入。
- 不把 provider SDK 作为主路径；统一优先用 `httpx` 直调 REST / HTML endpoint。
- 不把 API key 回显到聊天流、日志或 UI。
- 不把 HTML 搜索页抓取结果伪装成稳定 API。`bing_html` / `baidu_html`
  必须纳入 backend 清单，但 `test_backend` 要能识别验证码、挑战页、
  空页面和 selector 失效，并返回明确 `blocked_or_captcha` / `empty_results`
  状态。

## 交付清单

### 现有 LLM Tool: `WebSearch`

这是模型唯一长期可见的搜索工具，已经实现。本计划只做小改：

- 读取 `web_search.backend` 配置，而不是只看 `MUSTANG_SEARCH_BACKEND`。
- 可选保留 `MUSTANG_SEARCH_BACKEND` 作为临时 override。
- 返回 `fallback_attempts` metadata，便于 UI 和调试。
- 暂不新增 provider-specific 参数。

当前 schema 保持：

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "minLength": 2,
      "description": "Search query."
    },
    "limit": {
      "type": "integer",
      "default": 10,
      "minimum": 1,
      "maximum": 25,
      "description": "Number of results to return."
    }
  },
  "required": ["query"]
}
```

输出给 LLM：

```text
Note: Dates in snippets below are from the original pages, not the current date.

1. <title>
   <url>
   <snippet>

(N results via <backend>)
```

计划补充 `ToolCallResult.data`：

```json
{
  "query": "...",
  "backend": "searxng",
  "result_count": 5,
  "fallback_attempts": ["brave: missing key", "searxng: ok"]
}
```

参考：

- DeepCLI current `WebSearchTool`: `src/kernel/kernel/agents/mustang/tools/builtin/web_search.py`
- Claude Code prompt contract: `src/kernel/kernel/agents/mustang/prompts/default/tools/web_search.txt`
- OpenClaw provider inventory and search result shape:
  `/home/saki/Documents/alex/openclaw/docs/tools/web.md`

### CLI Command: `/websearch backend`

目的：选择已有 `WebSearch` 的默认 backend。

命令：

```text
/websearch backend
/websearch backend auto
/websearch backend searxng
```

裸命令打开 selector。快捷命令直接选择目标 backend。

显示字段：

| 字段 | 例子 | 说明 |
|---|---|---|
| `id` | `searxng` | 后端 id |
| `label` | `SearXNG` | 用户可读名，selector 主要显示它 |
| `description` | `Self-hosted metasearch` | 一句话说明这个后端本质是什么 |
| `status` | `available` / `missing_key` / `missing_config` / `disabled` | 当前可用性 |
| `cost` | `free-keyless` / `free-self-hosted` / `paid` / `free-tier` | 成本提示 |
| `network` | `global` / `cn-friendly` / `self-hosted` | 网络适配提示，不做 region 模式 |
| `required_config` | `base_url` | 缺什么 |
| `source` | `config` / `env` / `secret` / `builtin` | 配置来源 |

Selector 不应该只显示内部 id。用户看到的是：

```text
Google Programmable Search
Gemini Search Grounding
SearXNG
DuckDuckGo
```

内部 id 只用于 config / debug：

```text
google_cse
gemini_grounding
searxng
duckduckgo
```

选择流程：

1. CLI 调 `_mustang.agent/web_search/backend_options`。
2. 用户选择 backend。
3. 若缺配置，打开 `/websearch config <backend>` 表单。
4. CLI 调 `_mustang.agent/web_search/test_backend` 做内部真实 probe。
5. probe 成功后调 `_mustang.agent/web_search/set_backend`。
6. probe 失败则不改变当前 backend，并展示错误和修复建议。

### CLI Command: `/websearch config`

目的：配置 backend 参数和 API key，供已有 `WebSearch` backend resolver 使用。

命令：

```text
/websearch config
/websearch config searxng.base_url http://127.0.0.1:8080
/websearch config brave.api_key
/websearch config bocha.api_key
```

规则：

- 普通字段可以用快捷命令直接写。
- `api_key` / `token` / `secret` 类字段必须走 secret input。
- Kernel 写 SecretManager，Config 只保存 secret ref。
- UI 只显示 `missing` / `configured` / `env` / `secret_ref`，不显示 secret value。

Kernel methods：

```text
_mustang.agent/web_search/get_config
_mustang.agent/web_search/set_config
```

## Kernel Surface

新增 ACP methods：

```text
_mustang.agent/web_search/backend_options
_mustang.agent/web_search/set_backend
_mustang.agent/web_search/get_config
_mustang.agent/web_search/set_config
_mustang.agent/web_search/test_backend
```

`test_backend` 是内部 probe method，不是用户命令。CLI 在保存配置或切换 backend
时调用它；E2E 测试和 Definition of Done 也用它验证真实闭合缝。

### `backend_options`

输入：

```json
{}
```

输出：

```json
{
  "current": "auto",
  "fallback_order": ["brave", "google_cse", "exa", "tavily", "firecrawl", "parallel", "perplexity", "kimi", "xai", "bocha", "metaso", "tencent_searchpro", "aliyun_unified_search", "aliyun_bailian_mcp", "kuaisou", "searxng", "bing_html", "baidu_html", "duckduckgo"],
  "backends": [
    {
      "id": "searxng",
      "label": "SearXNG",
      "description": "Self-hosted metasearch through the SearXNG JSON API",
      "available": true,
      "status": "available",
      "cost": "free-self-hosted",
      "network": "self-hosted",
      "required_config": ["base_url"],
      "configured": {"base_url": true},
      "secret": null
    }
  ]
}
```

### `set_backend`

输入：

```json
{"backend": "searxng"}
```

写入：

```yaml
web_search:
  backend: searxng
```

`WebSearchTool` 执行时读取这个配置。

### `get_config`

输入：

```json
{"backend": "bocha"}
```

输出字段 schema，不输出 secret value：

```json
{
  "backend": "bocha",
  "fields": [
    {"name": "api_key", "kind": "secret", "status": "missing"},
    {"name": "base_url", "kind": "string", "value": "https://api.bochaai.com"}
  ]
}
```

### `set_config`

输入：

```json
{
  "backend": "bocha",
  "field": "api_key",
  "value": "secret input payload"
}
```

行为：

- secret 字段写入 SecretManager。
- Config 保存 `${secret:web_search.bocha.api_key}` 或等价 secret ref。
- 普通字段直接写 Config。

### `test_backend`（内部）

输入：

```json
{"backend": "searxng", "query": "Python programming", "limit": 3}
```

行为：绕过 LLM，直接调用 SearchBackend 真实路径。它不作为 `/websearch test`
暴露给普通用户。

## 后端接口

继续使用当前已有抽象，不重做 tool interface：

```python
@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str

class SearchBackend(ABC):
    name: str

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        ...

    def is_available(self) -> bool:
        ...
```

新增一个 backend metadata registry，供 `/websearch backend` 使用：

```python
@dataclass(frozen=True, slots=True)
class SearchBackendSpec:
    id: str
    label: str
    description: str
    cost: Literal["free-keyless", "free-self-hosted", "free-tier", "paid"]
    network: Literal["global", "cn-friendly", "self-hosted"]
    required_config: tuple[ConfigField, ...]
    env_vars: tuple[str, ...]
    reference: str
```

### 后端展示名

| id | selector label | 说明 |
|---|---|---|
| `duckduckgo` | DuckDuckGo | Free keyless fallback |
| `brave` | Brave Search | Brave Search API |
| `google_cse` | Google Programmable Search | Google Custom Search / Programmable Search JSON API |
| `gemini_grounding` | Gemini Search Grounding | Gemini answers grounded with Google Search citations |
| `exa` | Exa Search | Neural / keyword search with optional extracted content |
| `tavily` | Tavily Search | Structured web search API |
| `firecrawl` | Firecrawl Search | Search provider paired with crawl/extract |
| `parallel` | Parallel Search | Agentic web search provider |
| `perplexity` | Perplexity Search | Answer / citations via Perplexity or OpenRouter |
| `kimi` | Kimi / Moonshot Search | Moonshot built-in web search |
| `xai` | xAI / Grok Search | xAI web grounding |
| `bocha` | Bocha Search | China-friendly web search API |
| `metaso` | Metaso Search | China-friendly research/search API |
| `searxng` | SearXNG | Self-hosted metasearch |
| `tencent_searchpro` | Tencent SearchPro | Tencent Cloud web search API |
| `aliyun_unified_search` | Aliyun UnifiedSearch | Bailian REST web search API |
| `aliyun_bailian_mcp` | Aliyun Bailian MCP Search | Bailian search through MCP Manager |
| `kuaisou` | Kuaisou Search | China-friendly web search API |
| `bing_html` | Bing HTML | HTML scraping backend based on OpenManus parser |
| `baidu_html` | Baidu HTML | HTML/provider backend based on OpenManus Baidu implementation |

## Backend 细化范围

本节只保留两类：**纳入本计划的 SearchBackend** 和
**明确不作为 SearchBackend**。纳入清单里的每个 backend 都要有
inventory、config/secret 解析、内部 `test_backend` probe、`WebSearch` fallback 接入。

### 纳入本计划的 SearchBackend

| Backend | 当前状态 | 参数 | 凭证 | 参考 / 验证结论 |
|---|---|---|---|---|
| `duckduckgo` | 已工作，零 key fallback | `timeout_seconds` | 无 | DeepCLI 当前实现；保留并补 inventory/probe。 |
| `brave` | 已有 backend | `api_key`, `country`, `language`, `timeout_seconds` | `BRAVE_API_KEY` | DeepCLI 当前实现；补 metadata/config/probe。 |
| `google_cse` | 已有 backend / 文档设计 | `api_key`, `cse_id`, `timeout_seconds`, `site_search` | `GOOGLE_API_KEY`, `GOOGLE_CSE_ID` | 普通 Google Programmable Search；和 Gemini grounding 分开。 |
| `exa` | 已有 backend | `api_key`, `type`, `contents`, `timeout_seconds` | `EXA_API_KEY` | DeepCLI/OpenClaw parity；补 metadata/config/probe。 |
| `tavily` | 已有 backend | `api_key`, `search_depth`, `topic`, `timeout_seconds` | `TAVILY_API_KEY` | DeepCLI/OpenClaw parity；补 metadata/config/probe。 |
| `firecrawl` | 已有 backend | `api_key`, `base_url`, `timeout_seconds` | `FIRECRAWL_API_KEY` | DeepCLI/OpenClaw parity；补 metadata/config/probe。 |
| `parallel` | 已有 backend | `api_key`, `mode`, `timeout_seconds` | `PARALLEL_API_KEY` | DeepCLI current backend / Hermes；补 metadata/config/probe。 |
| `perplexity` | 已有 backend | `api_key`, `base_url`, `model`, `timeout_seconds` | `PERPLEXITY_API_KEY` / `OPENROUTER_API_KEY` | LLM-search provider，可映射 sources；补 metadata/config/probe。 |
| `kimi` | 已有 backend | `api_key`, `model`, `timeout_seconds` | `KIMI_API_KEY` / `MOONSHOT_API_KEY` | LLM-search provider，可映射 sources；补 metadata/config/probe。 |
| `xai` | 已有 backend | `api_key`, `model`, `timeout_seconds` | `XAI_API_KEY` | LLM-search provider，可映射 sources；补 metadata/config/probe。 |
| `searxng` | 新增 | `base_url`, `categories`, `language`, `safesearch`, `timeout_seconds` | 无 | SearXNG 官方 Search API 支持 `/search?q=...&format=json`；需要实例启用 JSON。 |
| `bocha` | 新增 | `api_key`, `base_url`, `freshness`, `summary`, `count`, `timeout_seconds` | `BOCHA_API_KEY` | Bocha 官方开放平台提供 `POST https://api.bochaai.com/v1/web-search`；无密钥 GET 验证 endpoint 返回 405。 |
| `metaso` | 新增 | `api_key`, `base_url`, `scope`, `size`, `page`, `timeout_seconds` | `METASO_API_KEY` | Metaso 官方 playground 明示 `/api/v1/search POST`、`q/scope/size/page`，支持 MCP。 |
| `tencent_searchpro` | 新增 | `api_key`, `base_url`, `mode`, `site`, `from_time`, `to_time`, `timeout_seconds` | `TENCENT_SEARCHPRO_API_KEY` | 腾讯云联网搜索 API `SearchPro`；官方文档给出 `POST https://api.wsa.cloud.tencent.com/SearchPro`、Bearer API KEY、Pages 输出。 |
| `aliyun_unified_search` | 新增 | `api_key`, `endpoint`, `engine_type`, `time_range`, `category`, `contents`, `timeout_seconds` | `ALIYUN_BAILIAN_API_KEY` / provider token | 阿里云搜索 API `UnifiedSearch` 是面向 Agent 的开放域搜索；不是 MCP-only。 |
| `aliyun_bailian_mcp` | 新增 | `server_name`, `tool_name`, `args_mapping`, `result_mapping`, `timeout_seconds` | 由 MCP server 配置决定 | MCP 不是排除理由；通过现有 MCP Manager / `MCPAdapter` 调用 Bailian MCP tool，再归一化成 `SearchResult`。 |
| `kuaisou` | 新增 | `api_key`, `base_url`, `count`, `offset`, `freshness`, `timeout_seconds` | `KUAISOU_API_KEY` | Kuaisou Search API 文档给出 `POST /api/web-search`、`query/count/offset/freshness`。 |
| `bing_html` | 新增 | `base_url`, `user_agent`, `market`, `language`, `count`, `timeout_seconds`, `proxy`, `enabled` | 无 | OpenManus `BingSearchEngine` 使用 `requests + BeautifulSoup` 解析 `ol#b_results li.b_algo`。本机 live probe：HTTP 200，但返回 Turnstile challenge，必须判定 `blocked_or_captcha`。 |
| `baidu_html` | 新增 | `user_agent`, `count`, `timeout_seconds`, `proxy`, `enabled` | 无 | OpenManus `BaiduSearchEngine` 依赖 `baidusearch~=1.0.3`。本机 live probe：HTTP 200，但返回“百度安全验证”，必须判定 `blocked_or_captcha`。 |

### 明确不作为 SearchBackend

| 候选 | 决策 | 原因 |
|---|---|---|
| `gemini_grounding` | 不作为 `SearchBackend` | Gemini/Vertex Grounding with Google Search 是模型生成时的 grounding tool，返回的是 grounded response metadata，不是稳定的 raw search result API。若接入，应作为 LLM provider capability。 |
| `azure_bing_grounding` | 不作为 `SearchBackend` | Azure Grounding with Bing Search 是 Azure AI Agent tool；官方说明 tool output 不直接返回给开发者/最终用户，不适合映射成 title/url/snippet backend。 |

## 每个后端的参数细节

### `searxng`

参考：SearXNG Search API。

配置字段：

| 字段 | 类型 | 默认 | 必需 | 说明 |
|---|---|---|---:|---|
| `base_url` | string | env `SEARXNG_BASE_URL` | 是 | 自托管实例地址 |
| `categories` | string | `general` | 否 | SearXNG categories |
| `language` | string | unset | 否 | SearXNG language |
| `safesearch` | integer | unset | 否 | SearXNG safesearch |
| `timeout_seconds` | integer | `15` | 否 | 请求超时 |

请求：

```text
GET {base_url}/search?q=<query>&format=json&categories=general&pageno=1
```

映射：

```text
title   <- result.title
url     <- result.url
snippet <- result.content | result.snippet | ""
```

错误：

- JSON format 未启用：提示用户改 SearXNG `settings.yml`。
- base URL 不可达：提示检查服务地址。
- 空结果：返回 0 results，不吞掉错误。

### `bocha`

参考：Bocha Open Platform。

配置字段：

| 字段 | 类型 | 默认 | 必需 | 说明 |
|---|---|---|---:|---|
| `api_key` | secret | env `BOCHA_API_KEY` | 是 | Bocha API key |
| `base_url` | string | Bocha 官方 endpoint | 否 | 自定义 endpoint |
| `timeout_seconds` | integer | `30` | 否 | 请求超时 |

第一版只要求返回 title/url/snippet。若 Bocha 返回更多 answer / crawl metadata，先放入
`ToolCallResult.data`，不要扩大 LLM-visible schema。

### `metaso`

参考：Metaso Search API playground / SDK docs。

配置字段：

| 字段 | 类型 | 默认 | 必需 | 说明 |
|---|---|---|---:|---|
| `api_key` | secret | env `METASO_API_KEY` | 是 | Metaso API key |
| `base_url` | string | Metaso 官方 endpoint | 否 | 自定义 endpoint |
| `timeout_seconds` | integer | `30` | 否 | 请求超时 |

第一版只要求 title/url/snippet。

### `tencent_searchpro`

参考：腾讯云联网搜索 API `SearchPro`。

配置字段：

| 字段 | 类型 | 默认 | 必需 | 说明 |
|---|---|---|---:|---|
| `api_key` | secret | env `TENCENT_SEARCHPRO_API_KEY` | 是 | 服务 API KEY |
| `base_url` | string | `https://api.wsa.cloud.tencent.com/SearchPro` | 否 | API endpoint |
| `mode` | integer | `0` | 否 | 0 自然检索，1 多模态，2 混合 |
| `site` | string | unset | 否 | 站内搜索域名 |
| `from_time` / `to_time` | integer | unset | 否 | 秒级时间戳过滤 |
| `timeout_seconds` | integer | `30` | 否 | 请求超时 |

映射：

```text
title   <- parsed Page.title
url     <- parsed Page.url
snippet <- parsed Page.passage | Page.content | ""
```

注意：腾讯返回的 `Pages` 是 JSON 字符串数组，backend 必须逐条 parse。

### `aliyun_unified_search`

参考：阿里云搜索 API `UnifiedSearch`。

配置字段：

| 字段 | 类型 | 默认 | 必需 | 说明 |
|---|---|---|---:|---|
| `api_key` | secret | env `ALIYUN_BAILIAN_API_KEY` | 是 | 访问令牌 / API key |
| `endpoint` | string | provider default | 否 | API endpoint |
| `engine_type` | string | `Generic` | 否 | `Generic` / `GenericAdvanced` / `LiteAdvanced` |
| `time_range` | string | `NoLimit` | 否 | `OneDay` / `OneWeek` / `OneMonth` / `OneYear` / `NoLimit` |
| `category` | string | unset | 否 | 金融、法律、医疗、新闻等分类 |
| `contents` | object | unset | 否 | 是否返回正文 / markdown / summary 等 |
| `timeout_seconds` | integer | `30` | 否 | 请求超时 |

第一版只映射网页结果的 title/url/snippet；正文、markdown、rerank score 放入 data。

### `kuaisou`

参考：Kuaisou AI Search API。

配置字段：

| 字段 | 类型 | 默认 | 必需 | 说明 |
|---|---|---|---:|---|
| `api_key` | secret | env `KUAISOU_API_KEY` | 是 | Kuaisou API key |
| `base_url` | string | provider default | 否 | API base URL |
| `count` | integer | `10` | 否 | 单页结果数，最大 50 |
| `offset` | integer | `0` | 否 | 分页偏移 |
| `freshness` | string | `noLimit` | 否 | `oneDay` / `oneWeek` / `oneMonth` / `oneYear` / 日期范围 |
| `timeout_seconds` | integer | `30` | 否 | 请求超时 |

实现时必须以真实 `test_backend(kuaisou)` 确认认证 header 和响应字段，再合并。

### `aliyun_bailian_mcp`

参考：Mustang 当前 MCP Manager / ToolManager 集成。

这个 backend 不直接写 HTTP adapter，而是走现有 MCP Manager：

```text
SearchBackend.search()
  -> MCPManager.get_connections() / list_tools(server_name)
  -> MCPManager.call_tool(server_name, tool_name, mapped_args)
  -> normalize MCP text/json/resource content into SearchResult[]
```

配置字段：

| 字段 | 类型 | 默认 | 必需 | 说明 |
|---|---|---|---:|---|
| `server_name` | string | `aliyun-bailian` | 是 | MCP server 名称，来自 ConfigManager / `.mcp.json` |
| `tool_name` | string | unset | 是 | Bailian MCP 暴露的搜索 tool 名称 |
| `args_mapping` | object | built-in mapping | 否 | `query` / `limit` 到 MCP tool 入参的映射 |
| `result_mapping` | object | built-in mapping | 否 | MCP 返回内容到 title/url/snippet 的映射 |
| `timeout_seconds` | integer | `30` | 否 | 调用超时 |

状态判断：

- `missing_mcp_server`：MCP config 里没有 `server_name`。
- `needs_auth`：MCP server 处于 OAuth / credential pending 状态。
- `missing_tool`：server 已连接，但 `tools/list` 里没有 `tool_name`。
- `available`：server connected 且 tool 存在。

这条路必须和现有 MCP Manager 打通，不能另起一套 MCP 客户端。当前可复用点：

- `src/kernel/kernel/agents/mustang/mcp/config.py`：支持 `.mcp.json` 与
  stdio / sse / http / ws server config。
- `src/kernel/kernel/agents/mustang/mcp/__init__.py`：已有
  `MCPManager.list_tools()` / `MCPManager.call_tool()`。
- `src/kernel/kernel/agents/mustang/tools/mcp_adapter.py`：已有 MCP tool content
  提取逻辑，可复用 `extract_text_content()`。

### `bing_html`

参考：OpenManus `app/tool/search/bing_search.py`。

OpenManus 实现结论：

- 用 `requests.Session` 加浏览器 UA 请求 `https://www.bing.com/search?q=...`。
- 用 `BeautifulSoup(lxml)` 解析 `ol#b_results li.b_algo`。
- `title <- h2.text`，`url <- h2.a["href"]`，`snippet <- p.text`。
- 下一页靠 `a[title="Next page"]`。

Mustang 实现要求：

- 用 `httpx.AsyncClient` 实现，不直接复制同步 `requests`。
- 保留 OpenManus selector 作为第一 selector，但加入挑战页检测。
- `enabled` 默认为 `false`；用户显式选择 `bing_html` 或设置
  `web_search.backends.bing_html.enabled=true` 后才进入 fallback。
- `test_backend` 必须把 Turnstile / Cloudflare / captcha 页面判成
  `blocked_or_captcha`，不能返回空成功。

本机验证：

```text
curl https://www.bing.com/search?q=OpenAI -> HTTP 200, size 67569
页面包含 Turnstile challenge / captcha；未出现可解析的 b_algo 结果。
结论：代码可实现，但当前网络下 probe 应返回 blocked_or_captcha。
```

### `baidu_html`

参考：OpenManus `app/tool/search/baidu_search.py`。

OpenManus 实现结论：

- 不手写 Baidu selector，而是依赖 `baidusearch~=1.0.3`。
- 返回值可能是 URL string、dict 或对象；OpenManus 统一转成
  `SearchItem(title, url, description)`。

Mustang 实现要求：

- 不引入 provider SDK 作为唯一实现；先用 `httpx` HTML backend + selector /
  challenge detection，必要时把 `baidusearch` 作为可选参考实现。
- `enabled` 默认为 `false`；用户显式选择 `baidu_html` 或设置
  `web_search.backends.baidu_html.enabled=true` 后才进入 fallback。
- `test_backend` 必须识别“百度安全验证”、`wappass.baidu.com`、
  captcha / verify 页面，返回 `blocked_or_captcha`。

本机验证：

```text
curl https://www.baidu.com/s?wd=OpenAI -> HTTP 200, size 1488
页面 title 是“百度安全验证”；没有可解析结果。
结论：代码可实现，但当前网络下 probe 应返回 blocked_or_captcha。
```

### 已有 provider 的参数基线

这些 provider 全部纳入同一个 inventory，不再拆多套分类。

| Backend | 最小配置字段 | 高级字段 |
|---|---|---|
| `brave` | `api_key`, `timeout_seconds` | `country`, `language`, `mode` |
| `google_cse` | `api_key`, `cse_id`, `timeout_seconds` | `site_search` |
| `exa` | `api_key`, `timeout_seconds` | `type`, `contents`, `date_after`, `date_before` |
| `tavily` | `api_key`, `timeout_seconds` | `search_depth`, `topic` |
| `firecrawl` | `api_key`, `base_url`, `timeout_seconds` | provider-specific search options |
| `parallel` | `api_key`, `timeout_seconds` | `mode` |
| `perplexity` | `api_key`, `base_url`, `model`, `timeout_seconds` | domain filters, content budget |
| `kimi` | `api_key`, `model`, `timeout_seconds` | search options if API exposes them |
| `xai` | `api_key`, `model`, `timeout_seconds` | grounding options |
| `bocha` | `api_key`, `base_url`, `timeout_seconds` | freshness, summary, count |
| `metaso` | `api_key`, `base_url`, `timeout_seconds` | scope, size, page |
| `tencent_searchpro` | `api_key`, `base_url`, `timeout_seconds` | mode, site, time filters |
| `aliyun_unified_search` | `api_key`, `endpoint`, `timeout_seconds` | engine_type, category, contents |
| `aliyun_bailian_mcp` | `server_name`, `tool_name`, `timeout_seconds` | args_mapping, result_mapping |
| `kuaisou` | `api_key`, `base_url`, `timeout_seconds` | count, offset, freshness |
| `bing_html` | `enabled`, `timeout_seconds` | user_agent, market, language, proxy |
| `baidu_html` | `enabled`, `timeout_seconds` | user_agent, proxy |

## Fallback 规则

没有 region 模式。resolver 只按三件事排序：

1. 用户显式选择的 `web_search.backend`。
2. 内置 priority 中“配置完整”的 backend。
3. 无 key fallback。

默认 priority：

```text
brave -> google_cse -> exa -> tavily -> firecrawl -> parallel ->
perplexity -> kimi -> xai -> bocha -> metaso -> tencent_searchpro ->
aliyun_unified_search -> aliyun_bailian_mcp -> kuaisou -> searxng ->
bing_html -> baidu_html -> duckduckgo
```

规则：

- 未配置 key 的 paid backend 不发请求，直接标记 `missing key`。
- MCP backend 只有 server connected、tool 存在、auth ready 才发请求。
- `searxng` 只有配置了 `base_url` 才进入请求链。
- `bing_html` / `baidu_html` 默认不进入 auto fallback；只有显式选择或
  `enabled=true` 时进入，并且验证码/挑战页必须记录为 `blocked_or_captcha`。
- `duckduckgo` 永远可用，但可能因网络/反爬失败。
- 显式选择 backend 失败后允许 fallback，但结果里要记录 fallback attempts。

## 配置存储

Config：

```yaml
web_search:
  backend: auto
  backends:
    searxng:
      base_url: http://127.0.0.1:8080
      timeout_seconds: 15
    bocha:
      api_key_ref: ${secret:web_search.bocha.api_key}
    aliyun_bailian_mcp:
      server_name: aliyun-bailian
      tool_name: web_search
    bing_html:
      enabled: false
    baidu_html:
      enabled: false
```

Env fallback：

```text
BRAVE_API_KEY
GOOGLE_API_KEY
GOOGLE_CSE_ID
EXA_API_KEY
TAVILY_API_KEY
FIRECRAWL_API_KEY
PARALLEL_API_KEY
PERPLEXITY_API_KEY
OPENROUTER_API_KEY
KIMI_API_KEY
MOONSHOT_API_KEY
XAI_API_KEY
BOCHA_API_KEY
METASO_API_KEY
TENCENT_SEARCHPRO_API_KEY
ALIYUN_BAILIAN_API_KEY
KUAISOU_API_KEY
SEARXNG_BASE_URL
```

Env 可以让 backend 可用，但 `/websearch config` 不把 env value 写回 Config。

## 实现切片

不要分成一堆半成品切片。实现必须是一个可用闭环：

```text
现有 WebSearch
  + backend inventory
  + /websearch backend
  + /websearch config
  + internal test_backend
  + 所有纳入清单 backend 的 metadata/config/probe
  + SearXNG / Bocha / Metaso / Tencent SearchPro / Aliyun UnifiedSearch /
    Aliyun Bailian MCP / Kuaisou / Bing HTML / Baidu HTML backend
```

### 必须一起完成

产物：

- `SearchBackendSpec` / backend inventory registry。
- 所有已有 backend 接入 inventory：DuckDuckGo、Brave、Google CSE、Exa、
  Tavily、Firecrawl、Parallel、Perplexity、Kimi、xAI。
- 所有新增 API backend 接入 inventory 和实现：SearXNG、Bocha、Metaso、
  Tencent SearchPro、Aliyun UnifiedSearch、Kuaisou。
- MCP-backed backend 接入现有 MCP Manager：`AliyunBailianMcpSearchBackend`
  通过 `MCPManager.call_tool()` 调用已配置 MCP server。
- HTML backend 接入 inventory 和实现：`BingHtmlSearchBackend`、
  `BaiduHtmlSearchBackend`，并内置 challenge / captcha 检测。
- `WebSearchTool` 读取 `web_search.backend` Config；保留 `MUSTANG_SEARCH_BACKEND`
  作为临时 override。
- Kernel methods：
  - `_mustang.agent/web_search/backend_options`
  - `_mustang.agent/web_search/set_backend`
  - `_mustang.agent/web_search/get_config`
  - `_mustang.agent/web_search/set_config`
  - `_mustang.agent/web_search/test_backend`
- CLI commands：
  - `/websearch backend`
  - `/websearch config`
- `SearXngSearchBackend`、`BochaSearchBackend`、`MetasoSearchBackend`、
  `TencentSearchProBackend`、`AliyunUnifiedSearchBackend`、
  `AliyunBailianMcpSearchBackend`、`KuaisouSearchBackend`、
  `BingHtmlSearchBackend`、`BaiduHtmlSearchBackend`。
- 内部 probe：切换 backend 或保存配置时真实调用 `test_backend`。
- SecretManager 写入：API key 只存 secret ref，不回显。
- `docs/kernel/subsystems/tools.md` 更新为实现事实。

验收：

- `/websearch backend` 能显示当前 backend、fallback order、每个 backend 状态。
- DuckDuckGo 显示 `available`，且现有 DuckDuckGo 搜索仍通过。
- 未配置 key 的 provider 显示 `missing_key`，不发请求。
- `/websearch backend auto`、`/websearch backend duckduckgo`、`/websearch backend searxng`
  能写 Config。
- `/websearch config searxng.base_url http://127.0.0.1:8080` 写 Config。
- `/websearch config bocha.api_key` 走 secret input，Config 只保存 secret ref。
- 有 SearXNG base URL 时，内部 `test_backend(searxng)` 返回真实 title/url/snippet。
- SearXNG 未启用 JSON 时给出明确 hint。
- 有 key 时，`test_backend(bocha|metaso|tencent_searchpro|aliyun_unified_search|kuaisou)`
  返回真实 title/url/snippet；无 key 时 inventory 显示 `missing_key` 且不发请求。
- MCP server 可用时，`test_backend(aliyun_bailian_mcp)` 通过 MCP Manager 返回
  title/url/snippet；server/tool/auth 缺失时给出对应状态。
- `test_backend(bing_html|baidu_html)` 在当前网络下允许返回
  `blocked_or_captcha`，但必须证明请求路径、selector/challenge 检测和 fallback
  记录完整；若未被拦截则返回真实 title/url/snippet。
- `WebSearch(query=...)` 使用 Config 选择的 backend，并记录 fallback attempts。
- `WebSearch(query=..., backend="searxng")` 不作为本计划要求；除非实现时成本很低。

## Definition of Done

每个 backend 完成必须过五件事：

1. Inventory 能显示 status、cost、network、required config。
2. Config / env / SecretManager 三种来源解析正确。
3. Unit tests 覆盖参数、payload mapping、missing key、error payload。
4. 内部 `test_backend(<backend>)` 真实 probe 通过或给出明确错误。
5. `WebSearch` tool 真实调用该 backend，并在回答中能提供 Sources。

## 参考实现

| 主题 | 参考 |
|---|---|
| WebSearch prompt / sources 要求 | Claude Code prompt via `src/kernel/kernel/agents/mustang/prompts/default/tools/web_search.txt` |
| Tool shape / deferred loading | DeepCLI current `WebSearchTool` |
| Backend ABC | DeepCLI `SearchBackend` |
| Provider inventory / auto-detect | OpenClaw `/home/saki/Documents/alex/openclaw/src/web-search/runtime.ts` |
| Provider metadata | OpenClaw `/home/saki/Documents/alex/openclaw/src/plugins/bundled-web-search.ts` |
| Exa parameters | OpenClaw `/home/saki/Documents/alex/openclaw/extensions/exa/src/exa-web-search-provider.ts` |
| Gemini grounding | OpenClaw `/home/saki/Documents/alex/openclaw/extensions/google/src/gemini-web-search-provider.ts` |
| DuckDuckGo fallback | DeepCLI current backend + OpenClaw DuckDuckGo provider |
| SearXNG API | https://docs.searxng.org/dev/search_api.html |
| Bocha | https://open.bochaai.com/overview |
| Metaso | https://metaso.cn/search-api/playground |
| Kimi | https://platform.kimi.com/docs/guide/use-web-search |
| Bing / Baidu HTML implementation | OpenManus `/home/saki/Documents/alex/OpenManus/app/tool/search/` |
| MCP-backed search path | Mustang `MCPManager.list_tools()` / `MCPManager.call_tool()` + `MCPAdapter.extract_text_content()` |
