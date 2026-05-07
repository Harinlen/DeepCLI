# WebFetch 后端管理计划

状态：**待审阅草案**

日期：**2026-05-07**

## 问题

WebFetch 现在已经有轻量本地路径和多个可选 provider 路径，但浏览器渲染路径太薄：

- `httpx` 很快、免费，但对 JavaScript 重页面、前端渲染页面、部分反爬页面经常抓不到有效内容。
- `readability-lxml` 只在“HTML 已经抓下来”之后有用；它不会渲染 JavaScript。
- 当前 Playwright backend 只是裸 `goto + document.body.innerText`，承担了浏览器安装成本，却没有足够强的正文抽取价值。
- Firecrawl 很强，但它是云服务，免费额度有限，不能当成默认长期答案。

真实需求是建立一套长期可管理的 WebFetch 命令面：用户进入 CLI 后，应该能检查、安装、验证、启用、禁用、偏好后端。

## 决策

1. 用 Crawl4AI backend 替代裸 Playwright backend。

   Crawl4AI 已经封装 Playwright/Chromium，并提供面向 LLM 的 crawling 和 markdown extraction。它的抽象层级更适合 WebFetch。裸 Playwright 应该留给未来的交互式浏览器控制，而不是 URL-to-content 抓取。

2. 保留 Readability。

   Readability 的含义是“从已经抓到的 HTML 中提取主文章正文”。它便宜、本地、快，应该继续留在 fallback chain 里，并且排在任何浏览器渲染后端之前。

3. 不用环境变量做功能开关。

   运行期冻结的功能启用/禁用属于 `FlagManager`。环境变量仍然可以用于 provider credentials 或兼容性覆盖，但不能作为产品 feature gate。

4. 后端安装、验证、诊断是一级用户操作。

   产品应该明确展示 installed/missing 状态、成本模型、依赖、健康检查、精确安装动作，而不是静默失败，或让用户从 traceback 里猜该装什么。

## 后端模型

Kernel 应该暴露一个小型 backend inventory model。例如：

```json
{
  "id": "crawl4ai",
  "label": "Crawl4AI",
  "kind": "optional-local-browser",
  "installed": false,
  "enabled": true,
  "available": false,
  "cost": "free-local",
  "requiresRestart": true,
  "installActions": [
    "uv pip install crawl4ai",
    "crawl4ai-setup"
  ],
  "notes": "Installs local browser-rendered extraction for JavaScript-heavy pages."
}
```

建议后端分类：

| Backend | Category | Cost | Role |
|---|---|---|---|
| `httpx` | builtin-local | free | 快速直接 HTTP fetch，处理 JSON/text/HTML cleanup |
| `readability` | optional-local | free | 从静态 HTML 中抽取主正文 |
| `crawl4ai` | optional-local-browser | free software, local compute | 本地浏览器渲染，处理 JavaScript 页面 |
| `firecrawl` | external-service | free tier / paid | 困难页面的云端 fallback |
| `parallel` | external-service | paid/API-key | provider extraction |
| `exa` | external-service | paid/API-key | provider extraction |
| `tavily` | external-service | paid/API-key | provider extraction |

## Fallback 策略

初始目标策略：

1. 便宜的本地路径优先。
2. Crawl4AI 只有在已安装且确实需要时才进入。
3. 外部 provider 只有在已配置或用户明确偏好时才进入。

具体默认顺序建议：

```text
readability -> httpx -> crawl4ai -> firecrawl -> parallel -> exa -> tavily
```

理由：

- `readability` 和 `httpx` 快且便宜。
- `crawl4ai` 会启动浏览器，不应该成为普通页面的第一次尝试。
- Provider 后端可能收费或需要账号，所以应该是 configured fallback，而不是隐藏默认依赖。

待审阅问题：`httpx` 是否应该排在 `readability` 之前。当前实现把 Readability 当成独立 fetch backend；更干净的未来形态可能是在同一个本地 pipeline 内做 `httpx fetch -> readability extraction -> basic cleanup`。

## FlagManager

如果需要 kill switch，应加到 `ToolFlags`：

```yaml
tools:
  web_fetch_crawl4ai: true
  web_fetch_external_providers: true
```

建议语义：

- `web_fetch_crawl4ai=false`：即使安装了 Crawl4AI，也不把 Crawl4AI backend 放入 fallback chain。
- `web_fetch_external_providers=false`：自动 fallback 不使用 Firecrawl/Parallel/Exa/Tavily。用户明确指定 provider 时如何处理，需要单独决策。

不引入 `MUSTANG_WEB_FETCH_CRAWL4AI` 或类似 env switch。

## Kernel Surface

新增 Kernel-owned methods，供 `/webfetch` 管理命令调用：

```text
_mustang.agent/web_fetch/backends
_mustang.agent/web_fetch/install_backend
_mustang.agent/web_fetch/verify_backend
_mustang.agent/web_fetch/doctor
_mustang.agent/web_fetch/set_policy
```

职责：

- `backends`：返回 installed/enabled/available 状态和安装元数据。
- `install_backend`：在用户授权后运行已知安装 recipe。
- `verify_backend`：import 并 smoke-test 指定 backend。
- `doctor`：报告缺失 package、缺失 browser binary、provider key、flags、生效 fallback order。
- `set_policy`：可选后续步骤，用于 preferred backend order 或 local-first/provider-first policy。

安装动作必须按 backend id allowlist。Kernel 不接受 CLI 传来的任意 command string。

## CLI Surface

提供长期可用的管理命令：

```text
/webfetch
/webfetch backends
/webfetch install crawl4ai
/webfetch verify crawl4ai
/webfetch doctor
/webfetch prefer crawl4ai
/webfetch disable crawl4ai
```

Launcher / non-interactive 形态后续可以镜像：

```text
deepcli webfetch backends
deepcli webfetch install crawl4ai
deepcli webfetch doctor
```

TUI 应该把它做成运维/设置界面，不是 marketing 页面：backend 表格、状态、install/verify 按钮、warning、下一步动作。

## Crawl4AI Backend

新增文件：

```text
src/kernel/kernel/tools/web/fetch_backends/crawl4ai_be.py
```

行为：

- lazy import `crawl4ai`。
- 浏览器工作前先跑已有 `check_domain(url)`。
- 使用 `AsyncWebCrawler`、`BrowserConfig`、`CrawlerRunConfig`。
- 优先返回 `result.markdown`。
- fallback 到 `result.cleaned_html` 或 `result.html`，再走现有 HTML-to-markdown 路径。
- 填充 `FetchResult.url`、`content`、`content_type`、`title`、`status_code`、`truncated`、`raw_length`。
- 对预期依赖/runtime 失败返回结构化 `FetchResult(error=...)`，不要直接抛异常。

OpenManus 是 Crawl4AI 使用方式的最近参考：

```text
/home/saki/Documents/alex/OpenManus/app/tool/crawl4ai.py
```

我们只借 Crawl4AI setup/execution pattern，不照搬它的 standalone tool schema 或用户展示格式。

## 依赖和安装策略

不要把 Crawl4AI 变成 Kernel 的硬依赖。

新增 optional extra：

```text
web-crawl4ai = ["crawl4ai>=..."]
```

`/webfetch install crawl4ai` 安装动作应该在当前 active Kernel Python environment 里执行：

```bash
uv pip install crawl4ai
crawl4ai-setup
```

dev checkout 和 packaged launcher 的具体安装命令可能不同。管理动作应该解析当前 Kernel environment，而不是让用户猜 package 该装到哪里。

验证命令：

```bash
python -c "from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig; print('ok')"
```

可选 smoke：

```text
Fetch https://example.com with Crawl4AI and verify non-empty markdown.
```

## 删除 Playwright Backend

删除：

```text
src/kernel/kernel/tools/web/fetch_backends/playwright_be.py
```

更新：

```text
src/kernel/kernel/tools/web/fetch_backends/__init__.py
src/kernel/pyproject.toml
tests/kernel/tools/web/
docs/kernel/subsystems/tools.md
```

裸 Playwright 未来可以作为独立交互式浏览器控制功能回归，但 Crawl4AI 存在后，不应该继续作为 WebFetch backend。

## 测试

单元测试：

- backend inventory 能清楚报告 Crawl4AI missing。
- fake Crawl4AI module 返回 markdown 成功。
- markdown 为空时 fallback 到 cleaned HTML 或 raw HTML。
- SSRF/private URL 在 Crawl4AI 运行前被拦截。
- truncation 和 `raw_length` 正确。
- fallback order 包含 Crawl4AI，且不再包含 Playwright。
- 如果同一实现切片加入 flag，验证 FlagManager 能禁用 Crawl4AI。

集成 / probe 测试：

- 静态 HTML 在没有 Crawl4AI 时仍然成功。
- 本地 JavaScript-rendered page：`httpx` 抓不到或缺失动态文本；安装 Crawl4AI 后能抓到。
- `/webfetch backends` 报告准确 installed/missing 状态。
- `/webfetch install crawl4ai` 使用 allowlisted install recipe，并要求权限确认。

## 实施切片

1. Inventory first。

   给现有后端增加 backend inventory 和 doctor metadata。不改变运行行为。

2. Crawl4AI backend。

   增加 optional backend、测试、fallback-chain 接入。

3. 删除 Playwright backend。

   删除 backend，清理 docs/extras/tests。

4. CLI 管理面。

   增加由 Kernel methods 支撑的 `/webfetch` 命令。

5. Policy controls。

   只有当产品确实需要时，再加入 FlagManager kill switches 和 backend preference policy。

## 待审阅问题

1. 第一版是否就加入 `ToolFlags.web_fetch_crawl4ai`，还是先让 Crawl4AI 按 dependency auto-enable，等有真实禁用需求再加 kill switch？
2. 自动 fallback 是否应该在本地失败后调用外部 provider，还是只在用户配置/明确偏好 provider 时才调用？
3. `/webfetch install crawl4ai` 是否允许直接安装 Python packages，还是在更完整的 package management policy 设计前只输出引导命令？
4. Readability 应继续作为独立 backend，还是并入 `httpx` backend，成为本地 extraction stage？
