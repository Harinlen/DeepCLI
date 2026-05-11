# Browser WebFetch Bridge 计划

状态：**proposed**

日期：**2026-05-11**

## 原始需求

现在的 `WebFetch` 已经有本地 HTTP、Crawl4AI、本地/云端 provider fallback，
但它们都有一个共同弱点：请求不是发生在用户正在使用的真实浏览器会话里。

典型失败场景：

- 站点拒绝 CLI / headless / server-side crawler。
- 页面必须在真实浏览器里执行 JavaScript 后才有正文。
- 内容依赖用户浏览器里的登录态、cookie、localStorage 或站点风控状态。
- 用户能在浏览器里正常打开页面，但 `httpx` / Crawl4AI / provider extract 拿不到正文。

本计划的目标是新增一个 **Browser WebFetch backend**：

```text
WebFetch -> Kernel local bridge -> browser extension -> silent managed tab -> DOM/text -> Kernel
```

用户打开装了 DeepCLI 插件的 Chrome / Firefox 后，Kernel 可以请求插件在后台打开一个
非激活 tab，加载指定 URL，提取主要内容，然后关闭 tab。这个过程默认不打断用户当前浏览。

## 核心判断

这个方案不是“万能反 anti-bot”。更准确的定位是：

```text
使用用户授权的真实浏览器上下文完成 WebFetch。
```

它能改善：

- 真实浏览器 JS 执行。
- 更自然的浏览器环境。
- 用户已有登录态。
- 部分只拦 CLI / headless crawler 的站点。

它不能保证绕过：

- CAPTCHA / Turnstile / hCaptcha / reCAPTCHA。
- 账号风控、设备信誉、行为检测。
- 明确禁止自动化读取的站点策略。
- 扩展权限和浏览器安全限制。

所以实现要有 fallback / assisted 模式，而不是假装所有页面都能静默抓取。

## 不做什么

- 不扫描用户所有已打开 tab。
- 不读取浏览历史。
- 不导出 cookie / localStorage 给 Kernel。
- 不做全浏览器隐式监听。
- 不把插件做成“绕过验证码”的工具。
- 不让远程服务直接连接浏览器插件。
- 不把插件权限一次性开成 `<all_urls>` 的永久全读权限，除非用户显式选择高级模式。

## 第一版交付

第一版交付一个新的 WebFetch backend：

```text
browser
```

用户侧体验：

```text
/webfetch backend
  browser  setup needed/configured/available - Use installed browser extension via managed tab
```

当 WebFetch 使用 `browser` backend：

1. Kernel 向本机 bridge 发起 fetch 请求。
2. 插件收到请求。
3. 插件创建 inactive tab。
4. tab 加载目标 URL。
5. 插件等待页面稳定。
6. content script 提取内容。
7. 插件返回结果给 Kernel。
8. 插件关闭 managed tab。

返回内容至少包括：

- `url`
- `final_url`
- `title`
- `text`
- `html`（可选，受 max bytes 限制）
- `metadata`
- `status`
- `error`
- `extraction_method`

## 模式划分

### 1. `browser-current`

读取用户当前授权 tab。

用途：

- “把我现在看的页面交给 agent”
- 用户选中一段文字后让 agent 总结
- 页面必须人工登录/导航后才能读取

第一版可以不作为 WebFetch 默认 backend，但协议要预留。

### 2. `browser-tab`

静默 managed tab。Kernel 指定 URL，插件后台打开、抓取、关闭。

这是本计划第一版主路径。

### 3. `browser-assisted`

当 `browser-tab` 遇到 CAPTCHA、登录墙、权限页、跳转异常、空正文时：

1. 插件不关闭 tab。
2. CLI 提示用户接管浏览器 tab。
3. 用户完成登录 / CAPTCHA / 地区选择 / cookie consent。
4. 用户回到 CLI 继续，或插件检测页面 ready 后自动继续。
5. 插件再次提取内容。

这个模式第一版可以只做设计，不强制实现。

## 架构

### Kernel 侧

新增模块建议：

```text
src/kernel/kernel/agents/mustang/tools/web/browser_bridge/
  __init__.py
  server.py              # local bridge server
  protocol.py            # request/response schema
  pairing.py             # pairing token / session auth
  tab_fetch.py           # browser backend client wrapper
```

新增 WebFetch backend：

```text
src/kernel/kernel/agents/mustang/tools/web/fetch_backends/browser_bridge.py
```

职责：

- 检查 browser bridge 是否有已连接插件。
- 发送 fetch request。
- 等待 response / timeout。
- 转换成 `FetchResult`。
- 对返回 HTML/text 做统一 cap。
- 统一标记 external content / untrusted content。

### CLI 侧

需要新增命令入口或复用 `/webfetch config`：

```text
/webfetch browser
/webfetch browser pair
/webfetch browser status
```

但第一版可以先只做：

```text
/webfetch backend browser
```

如果插件未连接，返回 setup guidance：

```text
Browser WebFetch extension is not connected.
Open Chrome/Firefox with the DeepCLI extension installed, then run pairing.
```

### 浏览器插件侧

建议独立目录：

```text
src/browser-extension/
  manifest.chrome.json
  manifest.firefox.json
  src/
    background.ts
    content.ts
    readability.ts
    bridge.ts
    permissions.ts
```

插件职责：

- 与 Kernel local bridge 配对。
- 接收 `fetch_tab` request。
- 后台打开 managed tab。
- 注入 content script。
- 提取正文。
- 返回结果。
- 关闭 tab。

## 本地 Bridge

### 连接方向

优先选择：

```text
extension -> ws://127.0.0.1:<port>/browser-bridge?token=...
```

理由：

- 浏览器插件主动连接本机服务比 Kernel 连接插件更稳定。
- Kernel 只监听 loopback。
- pairing token 可防止任意网页伪装插件。

### Pairing

配对流程：

1. Kernel 生成一次性 pairing token。
2. CLI 显示 token 或打开 `chrome-extension://.../pair.html?token=...`。
3. 插件把 token 发送给 bridge。
4. Kernel 记录 extension identity。
5. 后续连接使用长期随机 secret，存入 SecretManager。

不允许：

- 无 token 连接。
- 远程地址连接。
- 任意网页通过 `fetch(127.0.0.1)` 调用 bridge。

Bridge 必须校验：

- `Origin`
- token / secret
- protocol version
- extension id（浏览器可提供时）

## Extension Fetch 协议

Request：

```json
{
  "id": "fetch-...",
  "type": "fetch_tab",
  "url": "https://example.com/article",
  "timeout_ms": 45000,
  "wait_until": "network_idle_or_dom_ready",
  "max_html_bytes": 2000000,
  "max_text_chars": 50000,
  "extract": {
    "html": true,
    "text": true,
    "readability": true,
    "metadata": true,
    "screenshot": false
  }
}
```

Response：

```json
{
  "id": "fetch-...",
  "ok": true,
  "url": "https://example.com/article",
  "final_url": "https://example.com/article",
  "title": "Article title",
  "text": "...",
  "readability_text": "...",
  "html": "<html>...</html>",
  "metadata": {
    "description": "...",
    "site_name": "..."
  },
  "signals": {
    "login_required": false,
    "captcha_detected": false,
    "cookie_banner_seen": true
  }
}
```

Error response：

```json
{
  "id": "fetch-...",
  "ok": false,
  "error": "captcha_detected",
  "message": "The page requires human verification.",
  "tab_id": 123,
  "assist_available": true
}
```

## 内容提取策略

插件提取优先级：

1. 用户 selection（current-tab 模式）
2. Mozilla Readability
3. `document.body.innerText`
4. cleaned HTML
5. metadata only

返回给 Kernel 后，Kernel 仍然统一执行：

- max chars cap
- byte cap
- external-content wrapper
- untrusted 标记
- URL / final URL 记录
- backend metadata：`backend=browser`

## 权限策略

Chrome / Firefox 权限建议：

基础权限：

```json
{
  "permissions": ["tabs", "scripting", "activeTab", "storage"],
  "host_permissions": []
}
```

默认不申请 `<all_urls>`。

当用户第一次 fetch 某域名：

- 方案 A：请求该域名 host permission。
- 方案 B：要求用户在插件设置里开启 “allow all sites”。

第一版建议：

- 支持 per-origin permission。
- 支持高级模式 `<all_urls>`，但必须明确告知用户。

## Managed Tab 行为

Tab 创建：

```ts
chrome.tabs.create({ url, active: false })
```

加载等待：

- `tabs.onUpdated` status complete
- 再等待短暂 idle window
- 可选检测 DOM 长度变化停止

关闭策略：

- 成功后关闭。
- 超时后关闭。
- 检测到 CAPTCHA / login required 时：
  - 第一版：关闭并返回 error。
  - assisted 模式：保留 tab，提示用户接管。

必须避免：

- 打开多个 tab 失控。
- 失败路径不关闭 tab。
- 同一时间无限并发。

第一版并发限制：

```text
max 1 managed tab per browser profile
```

## 后端状态

`/webfetch backend` 中新增：

| 状态 | 含义 |
|---|---|
| `setup needed` | 插件未安装或 bridge 未配对 |
| `configured` | 已配对，但当前浏览器未连接 |
| `available` | 插件在线，能接收 fetch |
| `current` | 当前默认 backend |
| `unavailable` | 配置损坏、版本不兼容、权限被拒绝 |

注意：`configured` 不是 `available`。只有插件当前在线才是 `available`。

## 安全边界

必须遵守：

- bridge 只监听 `127.0.0.1`。
- 每个 extension fetch request 必须来自 Kernel。
- 插件不主动抓取任何页面。
- 插件不扫描用户已有 tabs。
- 插件不读取历史记录。
- 插件不把 cookie / localStorage 值传给 Kernel。
- 插件只返回页面内容和必要 metadata。
- 所有返回内容都按 untrusted external content 处理。
- managed tab 有 timeout 和 close guarantee。

## 与 Crawl4AI 的关系

两者不是替代关系：

| Backend | 优势 | 弱点 |
|---|---|---|
| `crawl4ai` | 不依赖用户打开浏览器；本地自动化；可控环境 | 登录态弱；容易被 headless / automation 风控 |
| `browser` | 真实用户浏览器；真实 profile；登录态强 | 需要插件在线；权限复杂；浏览器兼容成本 |

Fallback 建议：

```text
httpx -> crawl4ai -> browser -> external provider
```

但用户也可以手动指定：

```text
WebFetch(url="...", backend="browser")
```

## 实施阶段

### Phase 1 — Protocol 与 Bridge Skeleton

- 定义 browser bridge schema。
- Kernel 增加 loopback websocket server。
- 支持 extension connect / heartbeat / status。
- `/webfetch backend` 能显示 browser backend 的连接状态。

验收：

- fake extension 连接 bridge。
- Kernel 能看到 `browser configured/available` 状态。
- 未配对连接被拒绝。

### Phase 2 — Chrome Extension MVP

- Manifest V3 Chrome extension。
- background service worker 连接 bridge。
- 支持 pairing token。
- 支持 `fetch_tab`。
- 支持 managed tab 打开/提取/关闭。

验收：

- 插件能抓取 `https://example.com`。
- 成功后 tab 自动关闭。
- 超时后 tab 自动关闭。

### Phase 3 — WebFetch Backend 集成

- 新增 `BrowserBridgeFetchBackend`。
- 加入 WebFetch backend inventory。
- `WebFetch(... backend="browser")` 走插件。
- 结果显示 `backend=browser`。

验收：

- Kernel unit test：fake bridge response -> `FetchResult`。
- ACP probe：`WebFetch` 调 browser backend。
- CLI 显示 backend。

### Phase 4 — Firefox 支持

- Firefox manifest。
- 兼容 `browser.*` API 或 wrapper。
- 验证 background / permissions / tabs 行为。

验收：

- Firefox 插件能完成 pairing。
- Firefox managed tab fetch 成功。

### Phase 5 — Assisted Mode

- CAPTCHA / login / empty-content signals。
- 插件保留 tab。
- CLI 提示用户接管。
- 用户完成后继续提取。

验收：

- fake CAPTCHA page 返回 `assist_available=true`。
- 用户确认后能继续提取。

## 测试计划

单元测试：

- bridge token 校验。
- schema parse。
- backend status 映射。
- `FetchResult` 转换。
- managed tab close guarantee 的插件逻辑。

集成测试：

- fake extension websocket client。
- fake browser response。
- WebFetch backend selection。
- Agent Hub `agent.tools_request` 路径。

E2E / Probe：

- 启动 Kernel。
- 启动 fake extension。
- `/webfetch backend browser`。
- `WebFetch(url="https://example.com", backend="browser")`。
- 验证结果里有 `backend=browser` 和正文。

真实手验：

- Chrome 插件安装。
- Firefox 插件安装。
- 后台 tab 抓取普通 JS 页面。
- 后台 tab 抓取需要登录态的页面。
- 失败时 tab 不泄漏。

## 风险

- Manifest V3 background service worker 生命周期会睡眠，WebSocket 长连接可能不稳定。
- Firefox 和 Chrome extension API 细节不同。
- host permission UX 可能比较烦。
- managed inactive tab 仍可能被某些站点识别。
- 用户 profile / cookie 行为涉及高隐私边界，必须非常克制。
- 如果 Kernel crash，插件需要清理 pending managed tabs。

## 开放问题

1. 第一版是否只支持 Chrome，Firefox 放 Phase 4？
2. 是否允许高级 `<all_urls>` 权限，还是必须 per-origin？
3. browser backend 是否进入默认 fallback chain，还是只允许用户手动指定？
4. assisted mode 的用户确认 UI 放 CLI 里，还是浏览器插件 popup 里？
5. 插件分发是 unpacked 本地安装，还是未来走 Chrome Web Store / Firefox Add-ons？

## Definition of Done

- `browser` backend 出现在 `/webfetch backend`。
- 插件在线时显示 `available`，离线但配对过显示 `configured`。
- `WebFetch(... backend="browser")` 能通过 fake extension probe 完成。
- Chrome 插件能真实抓取 `https://example.com`。
- managed tab 成功/失败/超时都能关闭。
- 所有返回内容标记为 untrusted external content。
- 文档说明隐私边界和 anti-bot 能力边界。
