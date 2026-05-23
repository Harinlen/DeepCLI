# WebBridge 实现计划

状态：**ready-for-implementation**

日期：**2026-05-24**

## 目标

新增一个通用 `WebBridge`：DeepCLI 和用户真实浏览器之间的本机授权桥。
本轮一次性交付的首个能力是 `browser` WebFetch backend，让 `WebFetch` 可以在
用户授权的真实 Chrome 浏览器 profile 中打开一个非激活 managed tab，等待页面
加载，提取正文，关闭 tab，并把结果按现有 WebFetch 工具格式返回。

目标链路：

```text
WebFetch(backend="browser")
  -> Primary Runtime ToolManager
  -> WebBridgeManager loopback WebSocket
  -> Chrome extension background service worker
  -> inactive managed tab + content script
  -> extracted text/html metadata
  -> FetchResult
```

WebBridge 的长期定位是“浏览器侧能力入口”，之后可以承载 current-tab
handoff、截图、console/network diagnostics、表单辅助、页面选择文本读取等能力。
本轮只实现 browser-backed WebFetch，避免一次把浏览器自动化平台铺太大。

首个 backend 解决的是“用户浏览器能打开，但 CLI/headless/server crawler 拿不到正文”
的场景。它不是验证码绕过器，不承诺绕过 CAPTCHA、账号风控、设备信誉、区域限制或
站点明确禁止自动化读取的策略。

## 一次性交付范围

本计划必须一次性实现以下能力，不拆阶段：

- `browser` 出现在 `/webfetch backend` 和 WebFetch backend inventory。
- `WebFetch(..., backend="browser")` 通过真实 Kernel -> fake extension E2E
  跑通。
- Kernel 提供浏览器可访问的安装页 URL。
- Chrome Manifest V3 extension 可从安装页下载/安装为 unpacked build。
- Chrome extension 支持 pairing、heartbeat、`fetch_tab`、managed tab
  打开/提取/关闭。
- Kernel 能区分 `setup_needed`、`configured`、`available`、`current`、
  `unavailable`。
- 所有 extension 返回内容仍按 WebFetch 外部不可信内容处理。
- 成功、错误、超时路径都保证 managed tab 关闭。

不在本轮实现：

- Firefox extension。
- WebBridge current-tab 读取。
- WebBridge assisted fetch：人工接管后继续提取。
- 默认 auto fallback 链里自动调用 `browser`。
- 远程服务连接浏览器插件。
- 扫描用户已打开 tabs、读取历史、导出 cookies/localStorage。

## 关键决定

1. **只支持 Chrome Manifest V3**
   Firefox API 和分发规则不同，放入同一轮会扩大测试矩阵并稀释核心闭合缝。
   计划里的扩展目录仍可为 Firefox 预留 wrapper，但 DoD 不要求 Firefox。

2. **`browser` 只允许显式选择，不进入 `auto` fallback**
   原因：真实浏览器 profile 可能携带登录态。自动 fallback 到浏览器会把
   “普通网页抓取失败”升级成“使用用户登录上下文抓取”。本轮只能在用户显式
   `WebFetch(..., backend="browser")` 或 `/webfetch backend browser` 后使用。

3. **Kernel bridge 属于 Primary Runtime，不属于 Access Agent**
   Browser fetch 是 ToolManager/WebFetch 的运行时能力，需要访问
   ConfigManager、SecretManager、ToolManager 状态，并随 Primary Runtime
   生命周期启停。Access Agent 只做用户 ACP 边界，不承载 WebBridge runtime。

4. **安装 URL 由 Access Agent 转发到 Primary Runtime 状态页信息，但静态文件
   由 Access Agent 服务**
   用户需要稳定浏览器 URL；浏览器不能直连 Hub runtime 私有 socket。Access
   Agent 提供 loopback HTTP 安装页：

   ```text
   http://127.0.0.1:<access-port>/web-bridge/install
   ```

   安装页不是裸 zip 下载页，而是引导式安装向导。它展示当前 pairing token、
   bridge ws URL、Chrome extension unpacked 目录、一步步安装状态，并提供
   extension build zip 作为 fallback 下载：

   ```text
   http://127.0.0.1:<access-port>/web-bridge/deepcli-web-bridge.zip
   ```

   Chrome 不允许普通网页直接一键安装非 Web Store extension，也不允许网页替用户
   打开或切换 `chrome://extensions` 的 Developer mode。所以“通过浏览器直接访问
   安装”的可实现形态必须是一个低摩擦安装向导：

   1. 显示 `chrome://extensions`，提供 copy button，并解释需要在地址栏打开。
   2. 高亮第一步：打开右上角 **Developer mode**。
   3. 高亮第二步：点击 **Load unpacked**。
   4. 显示并可复制本机 unpacked extension 目录，例如
      `src/web-bridge-extension/dist/chrome` 或 release 安装目录下的等价路径。
   5. 页面持续轮询 `_mustang.agent/web_bridge/status`，当 extension 连接成功时
      自动切到 paired/available 状态。
   6. 提供 fallback：zip 下载、重新生成 pairing token、重置 pairing。

   将来如果上 Chrome Web Store，同一路径可以优先显示商店安装按钮，并把 unpacked
   安装保留为 developer fallback。

5. **Pairing secret 存 SecretManager，WebBridge runtime state 不进 SQLite config**
   - 持久化：extension identity、长期 secret ref、protocol version、last paired
     timestamp 存 `config.web_fetch.backends.browser`，secret 值存 SecretManager。
   - 运行态：connected websocket、pending fetch requests、heartbeat、current
     browser info 只存在 WebBridgeManager 内存。

6. **不扩展 `FetchResult` 为主路径，只用 metadata 补充信息**
   现有 `FetchResult` 契约是：
   `url/content/content_type/title/status_code/error/truncated/raw_length/cached`。
   本轮 browser backend 必须能落入这个结构：
   - `url` = extension response `final_url`
   - `content` = `readability_text || text || metadata summary`
   - `content_type` = `text/html; backend=browser`
   - `title` = page title
   - `status_code` = `200` 成功，错误按合适的 4xx/5xx-ish 本地状态映射
   - `error` = extension error message

   `html`、`metadata`、`signals`、`extraction_method` 暂不进入 LLM content；
   只放入 backend-local diagnostics，并在 Tool result `data` 里暴露小型
   `browser_signals` 字段。不要让 raw HTML 默认进入 LLM。

## 参考对齐

Claude Code 没有“WebFetch 直接经浏览器 extension 抓取”的同构实现。可借鉴的是：

- `claude-in-chrome` 能力边界：Chrome extension 提供浏览器自动化，但需要用户
  明确启用并受站点权限控制。
- WebFetch 本体仍遵循现有 DeepCLI/Claude Code 风格：fetch 结果进入 tool
  result，外部内容必须被当作 untrusted content。

因此本计划不是照搬 Claude Code 模块，而是把 Chrome extension 作为新的
WebFetch backend，接入 DeepCLI 已有 `ToolManager`、`FetchBackend`、
`/webfetch backend`、Agent Hub `agent.tools_request` 路径。

## 代码变更清单

### Kernel runtime

新增：

```text
src/kernel/kernel/agents/mustang/tools/web/web_bridge/
  __init__.py
  manager.py
  protocol.py
  pairing.py
  install_assets.py
```

职责：

- `protocol.py`：Pydantic v2 schemas，使用 orjson 序列化。
- `pairing.py`：一次性 pairing token、长期 secret ref、constant-time token 比较。
- `manager.py`：Primary Runtime 内的 WebBridge state：
  - 启动 loopback WebSocket server。
  - 只绑定 `127.0.0.1`，端口 `0` 自动分配。
  - 记录 `ws_url`、`install_url`、`status`。
  - 接受 extension connect/pair/heartbeat/fetch response。
  - 管理 pending request futures 和 timeout cleanup。
  - shutdown 时取消 pending futures 并关闭 websocket server。
- `install_assets.py`：定位/打包 `src/web-bridge-extension/dist`，提供 zip 路径和
  install page metadata。

新增 backend：

```text
src/kernel/kernel/agents/mustang/tools/web/fetch_backends/browser.py
```

职责：

- 从 `ToolContext.module_table` 或 module-level runtime hook 找到
  `WebBridgeManager`。
- `is_available()` 只在已配对且 extension 当前在线时返回 true。
- `fetch()` 发送 `fetch_tab` request，等待 response，转换为 `FetchResult`。
- 对 text 做 `max_chars` 截断。
- 不返回 cookie/localStorage/history。
- 错误时返回 `FetchResult(error=...)`，不抛出普通可恢复错误。

现有文件必须同步修改：

- `src/kernel/kernel/agents/mustang/tools/web/config.py`
  - `WebFetchBackendName` 加 `"browser"`。
- `src/kernel/kernel/agents/mustang/tools/builtin/web_fetch.py`
  - input schema enum 加 `"browser"`。
  - result `data` 增加 `browser_signals`，只在 browser backend 有值。
- `src/kernel/kernel/agents/mustang/tools/web/management.py`
  - `BackendDefinition` 支持 `requires_pairing` / `install_url` / `connected`。
  - `BACKEND_DEFINITIONS` 加 browser。
  - `backend_is_available(browser)` 读 WebBridgeManager online 状态。
  - `build_backend_options()` 状态映射：
    - `setup_needed`：未配对或 extension package 不存在。
    - `configured`：已配对但 extension 离线。
    - `available`：已配对且 extension 在线。
    - `current`：当前默认 backend。
    - `unavailable`：secret/config/protocol version 损坏。
- `src/kernel/kernel/agents/mustang/tools/web/fetch_backends/__init__.py`
  - `get_backend_by_name("browser")`。
  - `get_available_backends()` 不把 browser 加进 auto priority。
- `src/kernel/kernel/agents/mustang/tools/__init__.py`
  - ToolManager startup 创建并启动 WebBridgeManager。
  - ToolManager shutdown 关闭 WebBridgeManager。

### ACP / Hub / Access

新增 ACP methods：

```text
_mustang.agent/web_bridge/status
_mustang.agent/web_bridge/pair_start
_mustang.agent/web_bridge/pair_reset
```

返回字段：

```json
{
  "status": "setup_needed|configured|available|current|unavailable",
  "installUrl": "http://127.0.0.1:<access-port>/web-bridge/install",
  "bridgeWsUrl": "ws://127.0.0.1:<bridge-port>/web-bridge",
  "paired": false,
  "connected": false,
  "protocolVersion": "web-bridge.v1",
  "browser": {"name": "Chrome", "version": "..."},
  "message": "..."
}
```

改动点：

- `src/kernel/kernel/core/protocol/acp/namespaces.py`
- `src/kernel/kernel/core/protocol/acp/schemas/web_fetch.py`
- `src/kernel/kernel/core/protocol/acp/routing.py`
- `src/kernel/kernel/agents/mustang/runtime/session_service.py`
- `src/kernel/kernel/agents/mustang/runtime/__main__.py`
- Agent Hub `agent.tools_request` path must keep forwarding these methods.
- `tests/kernel/agent_hub/test_agent_hub_transport_c.py` must prove Access and
  Primary Runtime method literals cannot drift.

Access Agent HTTP endpoints:

```text
GET /web-bridge/install
GET /web-bridge/deepcli-web-bridge.zip
GET /web-bridge/status.json
```

These endpoints must require loopback access and the normal Access Agent auth
token when token auth is enabled. The install page may accept
`?token=<access-token>` because a browser page is not the CLI ACP client.
Do not expose bridge secret in HTML. The page may show a short-lived pairing
token only after `_mustang.agent/web_bridge/pair_start` generated it.

Install page UX requirements:

- It is a single-page local wizard, not a documentation dump.
- It must show a stepper with four states:
  `build_ready -> developer_mode -> load_unpacked -> paired`.
- It must display a copyable unpacked extension path and a copyable
  `chrome://extensions` value. Do not rely on browser links to `chrome://`.
- It must show live connection status from `GET /web-bridge/status.json`.
- It must never show the long-term extension secret.
- It must include a “Pair again” action that calls the pair-start ACP path through
  the Access Agent handler and refreshes the short-lived token.
- It must include a “Reset pairing” action that calls pair-reset and clearly states
  that the extension will need to reconnect.
- Zip download remains available as fallback, but the primary path is “Load
  unpacked from this local folder”.

### CLI

Existing commands remain:

```text
/webfetch backend
/webfetch backend browser
/webfetch config
```

新增 command aliases:

```text
/webfetch browser
/webfetch browser status
/webfetch browser pair
/webfetch browser reset
/webfetch browser install
```

Behavior:

- `/webfetch browser install` opens or prints the install URL:
  `http://127.0.0.1:<access-port>/web-bridge/install`.
- `/webfetch browser pair` calls `_mustang.agent/web_bridge/pair_start`, shows pairing token,
  install URL, and bridge status.
- `/webfetch backend browser`:
  - if available: sets backend.
  - if configured/offline: refuses to switch and prints install/status guidance.
  - if setup_needed: prints install URL and pairing guidance.
- CLI remains thin: no direct SQLite, no direct Primary Runtime socket.

### Browser extension

新增目录：

```text
src/web-bridge-extension/
  package.json
  tsconfig.json
  manifest.chrome.json
  src/
    background.ts
    bridge.ts
    content.ts
    extract.ts
    permissions.ts
    types.ts
  scripts/
    build.ts
```

Build output:

```text
src/web-bridge-extension/dist/chrome/
src/web-bridge-extension/dist/deepcli-web-bridge.zip
```

Manifest V3 permissions:

```json
{
  "permissions": ["tabs", "scripting", "storage"],
  "host_permissions": []
}
```

No default `<all_urls>`. On first fetch for an origin:

- extension requests per-origin host permission with
  `chrome.permissions.request({ origins: ["https://example.com/*"] })`;
- if denied, it returns `permission_denied` and closes the managed tab if opened.

Extension responsibilities:

- Connect to `ws://127.0.0.1:<bridge-port>/web-bridge`.
- Pair with one-time token and store long-term secret in extension storage.
- Send heartbeat every 15 seconds while connected.
- Reconnect with exponential backoff when service worker wakes.
- Handle one `fetch_tab` at a time.
- Create inactive tab: `chrome.tabs.create({ url, active: false })`.
- Wait for `tabs.onUpdated` status `complete`, then a short DOM idle window.
- Inject content script with `chrome.scripting.executeScript`.
- Extract:
  1. Readability text when bundled parser succeeds.
  2. `document.body.innerText`.
  3. metadata-only fallback.
- Return title, final URL, text, optional capped HTML, metadata, signals.
- Always close managed tab on success, error, timeout, disconnect, or cancellation.

## Bridge protocol

Protocol version:

```text
web-bridge.v1
```

All messages are JSON objects with:

```json
{
  "id": "msg-...",
  "type": "...",
  "protocolVersion": "web-bridge.v1"
}
```

Extension -> Kernel connect:

```json
{
  "id": "hello-...",
  "type": "hello",
  "protocolVersion": "web-bridge.v1",
  "extensionId": "chrome-extension-id-or-dev",
  "browser": {"name": "Chrome", "version": "124.0.0.0"},
  "pairingToken": "123456",
  "secret": null
}
```

Kernel -> Extension hello response:

```json
{
  "id": "hello-...",
  "type": "hello_ack",
  "ok": true,
  "secret": "new-long-random-secret-only-on-pairing",
  "heartbeatMs": 15000
}
```

Kernel -> Extension fetch:

```json
{
  "id": "fetch-...",
  "type": "fetch_tab",
  "protocolVersion": "web-bridge.v1",
  "url": "https://example.com/article",
  "timeoutMs": 45000,
  "maxHtmlBytes": 200000,
  "maxTextChars": 50000,
  "extract": {
    "html": false,
    "text": true,
    "readability": true,
    "metadata": true,
    "screenshot": false
  }
}
```

Extension -> Kernel success:

```json
{
  "id": "fetch-...",
  "type": "fetch_result",
  "ok": true,
  "url": "https://example.com/article",
  "finalUrl": "https://example.com/article",
  "title": "Article title",
  "text": "...",
  "readabilityText": "...",
  "html": null,
  "metadata": {"description": "...", "siteName": "..."},
  "signals": {
    "loginRequired": false,
    "captchaDetected": false,
    "cookieBannerSeen": true
  },
  "extractionMethod": "readability"
}
```

Extension -> Kernel error:

```json
{
  "id": "fetch-...",
  "type": "fetch_result",
  "ok": false,
  "error": "permission_denied|timeout|captcha_detected|login_required|empty_content|tab_closed|internal_error",
  "message": "Human-readable reason",
  "signals": {
    "loginRequired": false,
    "captchaDetected": true,
    "cookieBannerSeen": false
  }
}
```

Security rules:

- Bridge binds only `127.0.0.1`.
- Reject non-loopback peer addresses.
- Reject missing/unsupported `protocolVersion`.
- Reject missing token/secret.
- Use constant-time comparison for token/secret.
- Reject browser-origin HTTP calls to bridge; bridge is WebSocket-only and
  validates handshake path.
- Do not log secrets, cookies, localStorage, page HTML, or full extracted text.
- Pairing token TTL: 10 minutes.
- One active extension connection per paired identity; newer valid connection
  replaces older connection and cancels pending fetches.

## Acceptance Tests

Unit tests:

- Browser bridge protocol schema validates success/error/invalid messages.
- Pairing token accepts once, expires, and uses secret ref after pairing.
- Browser backend status maps to `setup_needed/configured/available/current/unavailable`.
- `WebFetchBackendName` and Tool schema accept `"browser"`.
- `get_backend_by_name("browser")` works; auto backend list excludes browser.
- Browser response converts to `FetchResult` without leaking raw HTML into LLM content.
- Browser backend timeout returns `FetchResult(error=...)`.
- Extension managed tab close guarantee is covered with mocked Chrome APIs.

Integration tests:

- Fake extension websocket pairs with real WebBridgeManager.
- Fake extension heartbeat changes status to `available`.
- Fake extension disconnect changes status to `configured`.
- Fake extension receives `fetch_tab` and returns deterministic body.
- `/webfetch backend browser` refuses setup when not paired and includes install URL.
- Agent Hub `agent.tools_request` forwards WebBridge management methods.

E2E / probes:

- `tests/e2e/test_web_bridge_webfetch_e2e.py`
  - starts real Kernel through `scripts/run-kernel.sh`;
  - calls `_mustang.agent/web_bridge/pair_start`;
  - starts fake extension websocket client;
  - verifies `_mustang.agent/web_bridge/status` reports `available`;
  - sets backend to `browser`;
  - drives `WebFetch(url="https://example.com", backend="browser")`;
  - asserts tool result includes `backend=browser` and expected fake page text.
- `tests/e2e/test_web_bridge_install_page_e2e.py`
  - starts real Kernel;
  - `GET /web-bridge/install`;
  - asserts page includes the stepper, `chrome://extensions`, copyable unpacked
    path, no long-term secret, and zip download fallback;
  - `GET /web-bridge/status.json`;
  - asserts status JSON exposes paired/connected/install-path fields without secret;
  - downloads zip and verifies manifest exists.
- `scripts/probe_web_bridge_chrome.py`
  - manual real Chrome probe;
  - prints `paired=True`, `connected=True`, `tab_closed_success=True`,
    `tab_closed_timeout=True`, `example_text_seen=True`, `result=PASS`.

Closure seams to enumerate before completion:

- ToolManager startup/shutdown -> WebBridgeManager.
- BrowserFetchBackend -> WebBridgeManager `fetch_tab`.
- ACP routing -> ToolManager WebBridge status/pairing methods.
- Access Agent install HTTP endpoint -> Primary Runtime WebBridge status metadata.
- Extension websocket -> WebBridgeManager protocol handler.
- Extension background -> Chrome tabs/scripting APIs.
- WebFetchTool -> `fetch_with_fallback(preferred="browser")`.
- Agent Hub `agent.tools_request` -> Primary Runtime tool-management dispatcher.

Completion report must paste output from:

```bash
uv run --project src/kernel pytest tests/kernel/tools tests/kernel/protocol tests/kernel/agent_hub -q
uv run --project src/kernel pytest -m e2e tests/e2e/test_web_bridge_webfetch_e2e.py tests/e2e/test_web_bridge_install_page_e2e.py -q
bunx tsc --noEmit --pretty false
bun run src/web-bridge-extension/tests/<extension-test-entry>.ts
uv run --project src/kernel python scripts/probe_web_bridge_chrome.py
```

If the Chrome probe cannot run in CI, it still must run once on the user's
machine before claiming done.

## Definition of Done

Done means all of these are true:

- `/webfetch backend` lists `browser`.
- `/webfetch browser install` gives a working URL:
  `http://127.0.0.1:<access-port>/web-bridge/install`.
- Install page loads in a browser as a guided installer, shows how to open
  `chrome://extensions`, tells the user to enable Developer mode, shows the
  unpacked extension path to load, and keeps zip download as fallback.
- Pairing succeeds through the install page token flow.
- `browser` status transitions:
  `setup_needed -> configured -> available -> configured` are observable.
- `WebFetch(..., backend="browser")` works through fake extension E2E.
- Real Chrome extension fetches `https://example.com`.
- Managed tab closes on success, extension error, permission denial, and timeout.
- Browser backend does not enter auto fallback.
- No cookies, localStorage, browser history, long-term secret, or raw uncapped HTML
  is returned to Kernel logs, ACP responses, or LLM content.
- All closure-seam probes have run and their output is pasted in the report.
- `docs/plans/progress.md` is updated after implementation.
