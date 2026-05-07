# TUI OOBE 计划

状态：**待审阅草案**

日期：**2026-05-07**

## 目标

TUI OOBE 的目标不是阻止用户进入 DeepCLI，而是在合适的时候给用户一个最短的可用配置路径。

第一版只处理 LLM 配置引导：

- 如果用户已经有可用的 current/default 模型配置，不展示 OOBE。
- 如果没有，展示一个轻量引导，让用户可以快速配置 DeepSeek、配置其他 provider，或跳过进入主界面。
- 没有模型仍然允许进入主界面。真正需要模型时，再提示用户运行 `/model` 配置。

## 非目标

- 不做完整设置中心。
- 不把 OOBE 变成启动门禁。
- 不在 OOBE 里重新实现 provider/model 表单。
- 不把 WebFetch、Launcher、Memory、Skills 等能力塞进 OOBE。
- 不复制 Claude Code 的一次性 onboarding 状态模型。

## 参考风格

Claude Code 的 onboarding 可以借鉴的是界面和文案风格，而不是状态模型：

- 一步一屏。
- 文案短，直接说明为什么要做这一步。
- 主动作明确，次动作是 skip/later。
- 完成后不做复杂总结，直接进入主界面。
- 具体配置动作复用长期可用的管理命令。

DeepCLI 应该沿用这种呈现方式，但使用自己的版本化 OOBE 检查模型。

## 运行时机

每次启动 TUI 时都运行 OOBE checker，但不一定展示 OOBE。

流程：

```text
启动 TUI
  ↓
读取当前 OOBE revision 和本地 OOBE state
  ↓
如果当前 revision 已 skipped/satisfied
  → 直接进入主界面
  ↓
否则运行当前 revision 的 OOBE checks
  ↓
如果所有 item 已完成
  → 写入 satisfied(current revision)，进入主界面
  ↓
否则展示 OOBE
  ↓
用户完成 / 跳过
  ↓
进入主界面
```

## 状态模型

使用独立的 OOBE revision，不直接绑定产品版本。

```ts
const OOBE_REVISION = 1;
```

CLI 本地配置建议保存：

```ts
type OobeState = {
  revision: number;
  status: "satisfied" | "skipped";
  checkedAt?: string;
  skippedAt?: string;
};
```

语义：

- `satisfied`：checker 确认当前 revision 的所有事项已经完成。
- `skipped`：用户明确跳过当前 revision。
- 下一个 OOBE revision 发布后，旧的 `skipped/satisfied` 不再自动生效，需要重新检查。

OOBE 只服务 LLM 首次配置。即使后续有新的 product tips，也不应该扩展 OOBE item list；那些内容应该放进主界面的 Tips、Welcome feed，或对应的长期管理命令。

## 第一版检查项

第一版只有一个 recommended item：

```ts
{
  id: "llm.current.default",
  severity: "recommended",
  doneWhen: "Kernel reports a current/default model is configured"
}
```

如果该 item 已完成：

- 写入 `satisfied`。
- 不展示 OOBE。

如果未完成：

- 展示 OOBE。
- 用户可以配置模型，也可以跳过。

因为没有模型也允许进入主界面，所以该 item 不是 blocking。

## 第一屏

第一版第一屏主推 DeepSeek，因为它是最短的新手路径。

建议文案：

```text
Welcome to DeepCLI

DeepCLI needs an LLM model for chat and agent work.
DeepSeek is the quickest way to get started, and you can change this later with /model.
```

选项：

```text
-> Set up DeepSeek
   Set up others
   Skip to main window
```

选项含义：

- `Set up DeepSeek`：进入 DeepSeek 优先的 model add flow。
- `Set up others`：进入普通 `/model add` flow。
- `Skip to main window`：写入当前 revision 的 `skipped`，进入主界面。

## DeepSeek 引导

选择 `Set up DeepSeek` 后，不应该要求用户先理解 provider/model 架构，也不应该要求用户手动填写 DeepSeek model id。

DeepSeek 路径应该是一个特化 preset：用户只需要填写 API key，OOBE 直接创建 DeepSeek V4 Pro 和 DeepSeek V4 Flash 两个模型，并设置好默认 roles。

建议界面：

```text
Get a DeepSeek API key

Create an API key in DeepSeek Platform, then paste it below.
https://platform.deepseek.com/api_keys

Provider Settings
   Name:           deepseek
   Type:           deepseek
-> API key:        <empty>
   Base URL:       https://api.deepseek.com

Models to add
   [x] DeepSeek V4 Pro <1M> · default, memory
   [x] DeepSeek V4 Flash <1M> · compact
```

说明：

- Provider name/type 默认是 `deepseek`。
- Base URL 显示 Kernel 返回的 effective default 值，而不是 `<default>`。
- API key 直接显示和编辑真实值。我们防的是 LLM，不是本机用户。
- API key 页面放在界面最上方，作为用户动作提示，而不是混在 Provider Settings 里当成配置字段。
- Prompt 文案保持短：`Create an API key in DeepSeek Platform, then paste it below.`
- DeepSeek 官方入口：`https://platform.deepseek.com/api_keys`。
  - 第一版可以只显示 URL，不需要自动打开浏览器。
  - 后续可以支持 `Enter` 或一个明确选项打开链接。
- OOBE 默认创建两个模型：
  - `deepseek-v4-pro`，显示名 `DeepSeek V4 Pro`，roles 为 `default`、`memory`。
  - `deepseek-v4-flash`，显示名 `DeepSeek V4 Flash`，roles 为 `compact`。
- Context window 使用 Kernel 返回的 DeepSeek provider default/context metadata。当前 Kernel 已记录 V4 Pro 和 Flash 为 `1_000_000` tokens，CLI 只负责显示。
- 保存后自动将 `deepseek/deepseek-v4-pro` 设为 `current/default`，并将 `deepseek/deepseek-v4-flash` 设为 `current/compact`。
- 第一版不需要让用户在 OOBE 里编辑这两个 preset 的 roles。用户后续可以用 `/model list` 进入编辑界面调整。
- 如果已有 `deepseek` provider，DeepSeek OOBE 复用该 provider；用户可以更新 API key，OOBE 只补齐缺失的 V4 Pro / Flash 模型。
- 如果其中一个模型已经存在，保存时应该幂等更新它的 display/context/roles，而不是创建重复项。

DeepSeek 页面底部提示保持短：

```text
<↑/↓> field  <Enter> save  <Esc> back
```

## 其他 Provider 引导

选择 `Set up others` 后，复用现有 `/model add` flow：

```text
Choose a provider

-> New provider
   deepseek
   nvidia
   bedrock
```

如果选已有 provider：

- Provider Settings 只读。
- Model Settings 可编辑。
- 保存后自动设为 current/default。

如果选 `New provider`：

- 使用完整 Provider Settings + Model Settings 表单。
- Provider type 使用 Kernel 返回的 provider type options。
- provider-specific 字段由 Kernel schema 决定，CLI 不硬编码 AWS 字段或其他 provider 字段。

## 跳过行为

选择 `Skip to main window`：

- 写入：

```json
{
  "revision": 1,
  "status": "skipped",
  "skippedAt": "..."
}
```

- 进入主界面。
- 当前 OOBE revision 内不再自动展示。
- 如果后续用户手动通过 `/model` 配置完成模型，下一次 checker 可以把状态更新为 `satisfied`。
- 下一个 OOBE revision 仍然会重新检查。

如果用户跳过后触发需要模型的功能，提示应该指向 `/model`：

```text
No current model is configured. Run /model add to set one up.
```

## 完成行为

通过 OOBE 成功保存模型后：

1. Kernel 保存 provider/model。
2. Kernel 或 CLI 调用现有 model use 能力，将该模型设为 `current/default`。
3. OOBE checker 重新检查 `llm.current.default`。
4. 如果通过，写入 `satisfied(current revision)`。
5. 显示一条短状态后进入主界面：

```text
Model configured. You can change it later with /model.
```

## 架构边界

OOBE 不拥有配置逻辑。

应该复用现有能力：

- `/model add`
- `/model list`
- `/model current`
- `/model use`
- Kernel LLM manager 的 provider/model CRUD
- Kernel 返回的 provider schema/default/effective values

OOBE 只负责：

- 判断是否应该展示。
- 展示第一屏选择。
- 为 DeepSeek 提供更短的预填入口。
- 在保存成功后标记当前 OOBE revision 状态。

## 实现草图

CLI 侧：

```text
src/cli/src/startup/oobe.ts
src/cli/src/active-port/coding-agent/modes/components/oobe-welcome.ts
```

配置 schema：

```ts
ui?: {
  ...
}
oobe?: {
  revision: number;
  status: "satisfied" | "skipped";
  checkedAt?: string;
  skippedAt?: string;
}
```

启动接入点：

- 在 interactive TUI 进入主界面前运行 OOBE checker。
- `--print` / non-interactive 不展示 OOBE。
- 如果 OOBE 出错，不阻塞主界面；显示 warning 或记录日志即可。

Kernel/ACP 侧：

- 复用当前 model/provider list 和 model add/update/use 方法。
- 如果缺少“检查 current/default 是否存在”的明确方法，可以先使用 `/model current` 同源 ACP 能力。
- DeepSeek 的 effective Base URL/default context/provider fields 必须由 Kernel 返回，CLI 不做 fallback 猜测。

## 后续提示

WebFetch、Memory、Skills/MCP、Project setup 这类内容不进入 OOBE。

这些能力可以通过更轻的方式提示用户：

- 主界面 Tips / Welcome feed。
- 对应功能第一次失败或缺配置时的 actionable warning。
- 长期可用的管理命令，例如 `/webfetch`、`/memory`、`/skills`。

OOBE 保持单一职责：帮助用户设置第一个可用 LLM。
