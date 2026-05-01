# ToolAuthorizer — Design

Status: **pending** —— 基于 Claude Code 的 permission 系统蓝图,mustang
侧按 kernel 架构适配。与 [ToolManager](tools.md) 紧密配对:
ToolAuthorizer 决定"**允不允许调**",但很多决策**信息源**来自 Tool 本身
(`default_risk` / `prepare_permission_matcher` / `is_destructive` / `aliases`)。

> 前置阅读:
> - AuthN/AuthZ 拆分决策: [decisions.md D22](../../reference/decisions.md#d22--authn--authz-split-into-two-subsystems)
> - 姐妹子系统(AuthN): [kernel/subsystems/connection_authenticator.md](../../kernel/subsystems/connection_authenticator.md)
> - **信息源侧契约**: [tools.md § 3.1 Tool 接口](tools.md) § 12.7 决策顺序
> - Claude Code 蓝图: `~/Documents/alex/claude-code-main/src/utils/permissions/`
>   + `src/tools/BashTool/bashPermissions.ts` + `src/Tool.ts`

---

## 1. 核心概念

**ToolAuthorizer 是 "每次 tool call 放不放行" 的唯一**仲裁者**(不是唯一
信息源)**。它暴露:

1. 一个异步方法 `authorize(tool_name, tool_input, ctx) → PermissionDecision`
2. 三态决策: `allow` / `deny` / `ask`
3. 一个 `on_permission` 回调契约 —— `ask` 时由调用方(orchestrator)把
   决策请求抛给 Session 层走 ACP `session/request_permission` 往返
4. 两个 hook 事件(`permission_requested` / `permission_denied`),由
   authorizer fire,供 HookManager 订阅审计
5. 一个 **`filter_denied_tools(tool_names) → set[str]`** 查询接口,让
   ToolManager 在 `snapshot_for_session()` 组装 tool pool 时提前剥离
   被 server-level deny rule 屏蔽的工具,**LLM 看都看不到**

它**不**做:
- 工具执行(`Orchestrator.ToolExecutor` 负责)
- 工具本身的输入 schema 校验(`Tool.validate_input` 负责,在 authorize 之前)
- **tool-specific 的"这次调用危不危险"领域判断**(`Tool.default_risk` 负责)
- **tool-specific 的"pattern 怎么 match 我的 input"**(`Tool.prepare_permission_matcher` 负责)
- "ask" 的 UI 渲染(ACP 协议侧 `session/request_permission` 负责)
- 权限审计落盘(`HookManager` 订阅 hook 事件后决定)
- 凭证验证(`ConnectionAuthenticator` 负责,AuthN 不是 AuthZ)

**设计原则**:ToolAuthorizer 只做 **DSL 解析 + 综合仲裁 + LLMJudge fallback**。
每个 tool 怎么理解自己的 input / 怎么判断自己的调用风险,是 Tool 的
领域知识,由 Tool 通过固定契约告诉 Authorizer。

---

## 2. 职责边界(对比 Claude Code)

Claude Code 的 permission 层把规则解析、决策、ask UI、落盘、hook
触发混在一起([permissions.ts](../../../../../projects/claude-code-main/src/utils/permissions/permissions.ts) + [Tool.ts](../../../../../projects/claude-code-main/src/Tool.ts) +
[PermissionUpdate.ts](../../../../../projects/claude-code-main/src/utils/permissions/PermissionUpdate.ts))。mustang 借用它的 **数据模型 +
算法**,按 kernel 分层归位:

| Claude Code 关注点 | mustang 归属 |
|---|---|
| Rule DSL 解析 | ✅ ToolAuthorizer `RuleParser` |
| Rule 层合并 | ✅ ToolAuthorizer `RuleStore` |
| 决策主循环 | ✅ ToolAuthorizer `authorize()` + `RuleEngine` |
| Tool 的 argv 解析 / safe-list / 领域风险判断 | ❌ → **Tool.default_risk** (每个 Tool 自己懂) |
| Tool 的 rule pattern 匹配器 | ❌ → **Tool.prepare_permission_matcher** |
| Tool 的不可逆性判断 | ❌ → **Tool.is_destructive** |
| Session grant 缓存 | ✅ ToolAuthorizer `SessionGrantCache` |
| 规则落盘(`PermissionUpdate.persist*`)| ❌ → **ConfigManager**(复用既有 user/project layer)|
| Ask UI 渲染 | ❌ → ACP `session/request_permission` 由 Session 层产出 |
| Server-level MCP deny 的 pool-time filter | ✅ ToolAuthorizer 提供 `filter_denied_tools()`,ToolManager snapshot 时调 |
| LLMJudge(bash 不确定时走 LLM)| ✅ ToolAuthorizer `BashClassifier`(只含 LLMJudge 调度 + denial tracking) |
| `permission_denied` 审计 | ❌ → **HookManager** 订阅事件自行落盘 |
| Channel permission 投票(Telegram/iMessage) | ❌ → 不需要,ACP 已统一往返 |
| LLMJudge feature gate(`bun feature()`)| ❌ → 用 FlagManager 而非构建期门控 |

---

## 3. 与 Tool 的契约对接点

authorize() 执行过程中,Authorizer 通过以下 **Tool 接口**读取每次调用的
领域信息。这些接口的定义 owner 是 [ToolManager](tools.md) 文档,
此处只列出 Authorizer 侧怎么用。

### 3.1 Tool.default_risk(input, ctx)

```python
# 由 Tool 实现, Authorizer 消费
def default_risk(input, ctx) -> PermissionSuggestion:
    ...

class PermissionSuggestion:
    risk: Literal["low", "medium", "high"]
    default_decision: Literal["allow", "ask", "deny"]
    reason: str
```

Authorizer 在 `authorize()` 的流程中 **无条件调**(对齐 CC 的
[`permissions.ts:1216`](../../../../../projects/claude-code-main/src/utils/permissions/permissions.ts)—— `checkPermissions` 每次都调)。结果参与最终
仲裁(deny rule > ask rule > default_risk.deny > default_risk.ask > allow rule > default_risk.allow > fallback ask)。

**领域示例**:
- `BashTool.default_risk("rm -rf /")` → `(high, deny, "dangerous pattern match")`
- `BashTool.default_risk("git status")` → `(low, allow, "safe allowlist")`
- `FileEdit.default_risk(path="/etc/passwd")` → `(high, ask, "writes outside cwd")`

**BashTool 的 argv 解析 + safe/dangerous 清单是 `default_risk` 的内部
实现**,不是 Authorizer 的代码。Authorizer 只看结果 struct。

### 3.2 Tool.prepare_permission_matcher(input)

```python
def prepare_permission_matcher(input) -> Callable[[str], bool]:
    ...
```

用户规则长这样:`"Bash(git:*)"` / `"FileEdit(/src/**/*.ts)"`。
RuleParser 把括号里的 pattern 抽出来(`"git:*"` / `"/src/**/*.ts"`),
RuleEngine 调 `tool.prepare_permission_matcher(input)` 拿到一个 matcher
闭包,然后问 `matcher(pattern)` —— 闭包内部知道这个 pattern 该怎么
apply 到这个具体 tool 的 input。

Authorizer **不**维护 `BashPrefixMatcher` / `GlobMatcher` 这样的子类,
因为"pattern 怎么 apply"的领域语义无法抽象——只有 Tool 自己懂。

### 3.3 Tool.is_destructive(input)

```python
def is_destructive(input) -> bool:
    ...
```

**用途**:控制 `PermissionAsk.suggestions` 里"Allow always" 按钮的**出现**
—— 对齐 Claude Code `Tool.ts:406` 的 `isDestructive?()` 查询时机:
"**queried before the permission prompt is shown, not during persistence**"。

具体语义:
- RuleEngine 在产出 `PermissionAsk` 时调 `tool.is_destructive(input)`
- True → `suggestions` 列表里**不包含** `allow_always` 按钮(用户只看到
  "Allow once" / "Deny"),这样 destructive 工具永远不会被 grant
- False → `suggestions` 包含 "Allow once" / "Allow always" / "Deny" 三个

`authorizer.grant()` 本身**不**做 is_destructive 检查——grant 是无脑持久化
(对齐 CC `PermissionUpdate.ts:349` 的 `persistPermissionUpdates`)。
防线 100% 在 suggestions 构造时,减少重复判断 + 避免 caller 忘记护栏。

典型实现:
- `BashTool.is_destructive` 返 True 当命令含 `rm -rf` / `dd of=/dev/` / `git push --force` 等不可逆操作
- `FileWrite.is_destructive(path)` 返 True 当目标文件已存在且会被覆盖
- 绝大多数 read-only tool 返 False

### 3.4 Tool.aliases 和 `matches_name()` helper

规则匹配 tool name 时,rule `"Bash(...)"` 必须同时匹配 primary name
`"Bash"` 和所有 aliases。这个逻辑由 `matches_name()` helper 实现,
**ToolRegistry 和 ToolAuthorizer 共用同一份**(对齐 CC 的 `toolMatchesName`
同时服务 tool lookup 和 rule matching)。

放在 `kernel/tools/matching.py` 作为 shared utility,两个子系统 import:
```python
def matches_name(tool: Tool, candidate: str) -> bool:
    return candidate == tool.name or candidate in tool.aliases
```

---

## 4. authorize() 接口

### 4.1 方法签名

```python
class ToolAuthorizer(Subsystem):
    async def authorize(
        self,
        *,
        tool: Tool,
        tool_input: dict[str, Any],
        ctx: AuthorizeContext,
    ) -> PermissionDecision:
        """Decide whether this tool call may proceed.

        注意: 入参是 Tool 实例(不是 tool_name 字符串), 因为决策需要调
        tool.default_risk / tool.prepare_permission_matcher / tool.is_destructive。
        Caller(Orchestrator.ToolExecutor)从 ToolManager snapshot 里拿到
        Tool 实例后传入。
        """
```

### 4.2 AuthorizeContext

```python
@dataclass(frozen=True)
class AuthorizeContext:
    session_id: str
    agent_depth: int                    # 0=root, ≥1=sub-agent
    mode: Literal["default", "plan", "bypass"]
    cwd: Path                           # BashTool.default_risk 要看上下文
    connection_auth: AuthContext        # 只读引用, 未来企业 IAM 用

    should_avoid_prompts: bool = False
    """True 时所有 `ask` 决策自动转 `deny`。

    **动态判定**(对齐 CC 的 shouldAvoidPermissionPrompts 行为):由
    Session 层在构造 ctx 时决定,信号是"此时有没有能把 permission 请求
    路由回人类的通道"。具体规则:

    | 场景 | should_avoid_prompts |
    |---|---|
    | WS `/session` 有 ≥1 个 active connection | False(能走 ACP `session/request_permission`)|
    | WS 全部断开(用户临时离线)                    | True  |
    | Gateway adapter 声明支持 interactive(Discord/Telegram 的 reaction vote)| False |
    | Gateway adapter 声明不支持(cron / CI / `mustang exec --oneshot`)| True |

    **Sub-agent 的特殊处理**:sub-agent 不直接持 WS,但它的权限请求通过
    **根 session 的**同一条 ACP 通道冒泡给用户。所以判定依据是
    `SessionManager.active_connection_count(root_session_id) > 0`,**不是**
    sub-agent 的独立 session_id。Option C(动态判定)就是这个意思 ——
    不硬编码 sub-agent 一律 deny,而是看根 session 当下是否 interactive。
    """
```

### 4.3 决策流程(对齐 CC `permissions.ts:1158-1224` + ToolManager §12.7)

```
authorize(tool, input, ctx):
  [短路 1] SessionGrantCache 命中? → allow + ReasonSessionGrant
            (永远不进入下面的主流程, 和 CC 的 forceDecision 等价)

  [主流程]
    1. Mode override:
       - ctx.mode == "plan" 且 tool.kind ∈ {edit,delete,execute} → deny + ReasonMode
       - ctx.mode == "bypass" → allow + ReasonMode
    2. rules = RuleStore.snapshot()
    3. RuleEngine.decide(rules, tool, input):
       a. deny rule 命中 → deny + ReasonRuleMatched (短路)
       b. ask rule 命中 → 记下 pending_ask
       c. 无条件调 tool.default_risk(input, ctx) → suggestion
       d. 无条件调 tool.is_destructive(input) → destructive
       e. 综合仲裁:
             deny rule          > suggestion.deny
          >  ask rule ∨ suggestion.ask
          >  allow rule         > suggestion.allow
          >  fallback "ask"
    4. 若仲裁结果是 "ask":
       4a. ctx.should_avoid_prompts == True → deny + ReasonNoPrompt
       4b. tool.name == BASH_TOOL_NAME 且 LLMJudge 启用(§12.1):
           speculative LLM → safe 立即 allow, unsafe 改为 deny,
           unknown / budget_exceeded 继续 ask
       4c. 构造 suggestions 列表:
           - 必含 "Allow once" / "Deny"
           - tool.is_destructive(input) == False → 加入 "Allow always"
           - tool.is_destructive(input) == True  → **不**加 "Allow always"
             (§3.3 的防线)
       4d. 返回 PermissionAsk(message, suggestions, ReasonRuleMatched/BashClassifier)
           由 caller 的 on_permission 回调获取用户决定
    5. fire hook(§14.2 的顺序:deny → permission_denied;
                 ask → permission_requested)
    6. 返回 PermissionDecision
```

Caller(Orchestrator.ToolExecutor)拿到 `PermissionAsk` 后的处理:

```python
response = await on_permission(decision)   # Session 层往返
match response.outcome:
    case "allow_once":
        pass  # 本次放行
    case "allow_always":
        authorizer.grant(tool, input, ctx)  # 写 SessionGrantCache, 无脑持久化
    case "deny":
        return deny_tool_result
# 进入 pre_tool_use hook + tool.call()
```

**is_destructive 不在这里检查**,因为 `PermissionAsk.suggestions` 里已经
把 "Allow always" 按钮剔除了(§3.3)—— UI 上根本看不到这个选项,用户
没法选,caller 也就不用判断。对齐 CC 的做法:一次过滤 vs 重复判断。

---

## 5. PermissionDecision

Tagged union,三种 variant,字段对齐 CC [`types/permissions.ts:241-324`](../../../../../projects/claude-code-main/src/types/permissions.ts):

```python
@dataclass(frozen=True)
class PermissionAllow:
    behavior: Literal["allow"] = "allow"
    updated_input: dict[str, Any] | None = None
    """若非 None,ToolExecutor 用这个替换原始 tool_input 后再调 Tool.call()。
    用于"允许但改写"场景,如 BashClassifier 把 rm -rf /* 改成 rm -rf /tmp/*。

    **无 feature flag** —— 对齐 Claude Code `permissions.ts:423` 的
    `const finalInput = decision.updatedInput ?? input`,CC 不提供关闭
    此行为的开关。Authorizer 的 `updated_input != None` 路径必须由
    security review 守门:单测证明(a) 改写可见于 DecisionReason;
    (b) 改写只能"收紧不放松"(见 §16.2)。"""

    decision_reason: DecisionReason


@dataclass(frozen=True)
class PermissionDeny:
    behavior: Literal["deny"] = "deny"
    message: str
    """给 LLM 看的 tool_result 错误文案。"""
    decision_reason: DecisionReason


@dataclass(frozen=True)
class PermissionAsk:
    behavior: Literal["ask"] = "ask"
    message: str
    """给用户看的描述文本,如 "Run bash command: git push --force"。"""
    decision_reason: DecisionReason
    suggestions: list[PermissionSuggestion] = field(default_factory=list)
    """UI 可选的"快捷决策"按钮。Session 层映射到 ACP options 字段。"""


PermissionDecision = PermissionAllow | PermissionDeny | PermissionAsk
```

---

## 6. DecisionReason

```python
class ReasonRuleMatched(BaseModel):
    type: Literal["rule"] = "rule"
    rule_id: str                        # "user:0" "project:4"
    rule_behavior: Literal["allow", "deny", "ask"]
    matched_pattern: str                # "Bash(git:*)" 原始 DSL
    layer: Literal["user", "project", "local", "flag"]

class ReasonDefaultRisk(BaseModel):
    """对齐 Claude Code `types/permissions.ts:322` 的 `type: "other"` variant:
    当 tool 自身的 checkPermissions 主导决策、没有 rule 直接命中时,
    用这个。mustang 起了更描述性的名字,语义等价。"""
    type: Literal["default_risk"] = "default_risk"
    risk: Literal["low", "medium", "high"]
    reason: str                         # 来自 tool.default_risk 的 reason 字段
    tool_name: str

class ReasonSessionGrant(BaseModel):
    type: Literal["session_grant"] = "session_grant"
    granted_at: datetime
    signature: str                      # grant cache 的 key

class ReasonMode(BaseModel):
    type: Literal["mode"] = "mode"
    mode: Literal["plan", "bypass"]

class ReasonNoPrompt(BaseModel):
    type: Literal["no_prompt"] = "no_prompt"
    """should_avoid_prompts=True 时 ask → deny 的标签"""

class ReasonBashClassifier(BaseModel):
    type: Literal["bash_classifier"] = "bash_classifier"
    verdict: Literal["safe", "unsafe", "unknown", "budget_exceeded"]
    model_used: str | None = None

class ReasonFailClosed(BaseModel):
    type: Literal["fail_closed"] = "fail_closed"
    error_class: str                    # 不暴露 detail 给 LLM, 仅给 debug log

DecisionReason = (
    ReasonRuleMatched | ReasonDefaultRisk | ReasonSessionGrant
    | ReasonMode | ReasonNoPrompt | ReasonBashClassifier | ReasonFailClosed
)
```

---

## 7. on_permission 回调契约

`authorize()` 自己**不发送** `session/request_permission` —— 它只产出
`PermissionAsk`。往返语义由 orchestrator 注入的回调实现:

```python
OnPermissionCallback = Callable[
    [PermissionAsk],
    Awaitable[PermissionResponse],
]

@dataclass(frozen=True)
class PermissionResponse:
    outcome: Literal["allow_once", "allow_always", "deny"]
    user_message: str | None = None
```

Session 层接 ACP `session/request_permission` → 等 Future → 返 response,
详见 [session.md § Permission Round-trip](../../kernel/subsystems/session.md)。

---

## 8. Rule 数据模型

### 8.1 DSL 语法(抄 Claude Code)

| DSL 示例 | 含义 |
|---|---|
| `"Bash"` | 匹配 `Bash` 工具的所有调用 |
| `"Bash(git:*)"` | 匹配 `Bash` 且 `tool.prepare_permission_matcher(input)("git:*")` 返 True |
| `"FileEdit(**/*.py)"` | 匹配 `FileEdit` 且 matcher 闭包对 glob 返 True |
| `"mcp__slack"` | 匹配整个 slack MCP server(见 §13) |
| `"mcp__slack__channel_create"` | 匹配单个 MCP tool |
| `"Bash(rm -rf \\(dangerous\\))"` | 括号内用 `\(` `\)` 转义 |

转义规则照搬 [`permissionRuleParser.ts:93-152`](../../../../../projects/claude-code-main/src/utils/permissions/permissionRuleParser.ts):`\(` / `\)` / `\\` 为唯一需
转义字符。

### 8.2 PermissionRule 结构

```python
class PermissionRuleValue(BaseModel):
    tool_name: str                          # "Bash" / "mcp__slack" / etc.
    rule_content: str | None = None         # 括号内内容, None 表示整工具匹配

class PermissionRule(BaseModel):
    source: RuleSource
    layer_index: int
    rule_id: str                            # f"{source}:{layer_index}"
    behavior: Literal["allow", "deny", "ask"]
    value: PermissionRuleValue
    raw_dsl: str

class RuleSource(str, Enum):
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"
    FLAG = "flag"
```

### 8.3 Parser + fail-closed 策略

单条规则解析失败 → 替换为隐式 `deny(tool_name="<unparsed>")`,其他
规则照常生效。日志记录原始字符串 + 错误原因。**Fail-closed**:空
`tool_name`、非法转义、未闭合括号 → 一律 deny。

---

## 9. RuleStore(内部组件)

### 9.1 4 层来源

| 层 | 路径 | 用途 |
|---|---|---|
| `user` | `~/.mustang/config/config.yaml` 的 `permissions:` 段 | 个人默认规则,跨 project 复用 |
| `project` | `<cwd>/.mustang/config.yaml` | 项目级规则,check in 到 git |
| `local` | `<cwd>/.mustang/config.local.yaml` | 本地覆盖,gitignored |
| `flag` | `--permission-rule` CLI 参数 + `MUSTANG_PERMISSION_RULES` env | 运行时 override,不落盘 |

**不设 machine-level policy 层**(CC 有 `managed-settings.json` 但我们
显式放弃):mustang 是 per-user kernel,没有"多用户共用一台机器 + 企业
IT 锁规则"这种场景。真要企业锁定的未来需求,`user` / `project` 层 + 外部
部署治理(容器镜像、CI 校验)已经够用。

### 9.2 优先级 + 合并

按下面顺序,后层覆盖前层:

```
user(低) → project → local → flag(高)
```

**behavior 之间仲裁**:同一 `(tool, input)` 命中多条规则 → `deny > ask > allow`
(对齐 CC [`permissions.ts:1169-1319`](../../../../../projects/claude-code-main/src/utils/permissions/permissions.ts))。

### 9.3 热更新

| 层 | 热更新机制 |
|---|---|
| `user` / `project` / `local` | ConfigManager Signal 订阅 —— yaml 改变 → 推送到 RuleStore → 原子替换 rule 表 |
| `flag` | CLI / env 变量,kernel 启动时一次性读入,运行期不可变(对齐 FlagManager runtime-frozen 契约)|

**不需要 mtime 轮询 / file watcher** —— 所有会变动的层都走 ConfigManager
Signal 机制,简洁统一。

### 9.4 Parse 失败 graceful degradation

**user/project/local**:重 parse 整体失败(yaml 语法错、Pydantic 验证
错)→ **保留旧 rule 表**,log + fire hook `rule_parse_failed`;用户
修好之前不会陷入"全线 deny"。

**flag** 层启动期一次性读入,失败 → log warning + 该 flag 层留空(
user/project/local 照常生效)。不 abort boot。

### 9.5 与 ConfigManager 的接口

```python
class PermissionsSection(BaseModel):
    """bound section in ConfigManager"""
    allow: list[str] = []
    deny: list[str] = []
    ask: list[str] = []
    bash_llm_judge_enabled: bool = True                # §11 LLMJudge 的 gate
    bash_llm_judge_fail_closed: bool = True            # LLM API 失败时的降级(对齐 CC tengu_iron_gate_closed)
```

**注**:LLMJudge 用**哪个模型**不在 PermissionsSection 里配置,而是通过 LLMManager 的
`current_used` role 机制([llm/config.py](../../../src/kernel/kernel/llm/config.py) 的
`CurrentUsedConfig.bash_judge`)。用户在 `kernel.yaml` 里写:

```yaml
llm:
  current_used:
    default: claude-opus
    bash_judge: haiku         # ← LLMJudge 用这个模型
```

BashClassifier 通过 `llm_manager.model_for("bash_judge")` 取用。未配置(None)时
视作 LLMJudge 未就绪,fallback 到 `ask`。

---

## 10. RuleEngine(内部组件)

### 10.1 纯函数接口

```python
class RuleEngine:
    def decide(
        self,
        rules: list[PermissionRule],
        tool: Tool,                         # 不是 tool_name, 拿到 Tool 实例
        tool_input: dict[str, Any],
    ) -> _EngineOutcome:
        """
        遍历 rules, 用 tool 的 prepare_permission_matcher 做 per-tool
        匹配, 再综合 tool.default_risk 产出结果。
        """
```

`_EngineOutcome` 是 engine 内部中间态(含 matched rule / default_risk
结果 / destructive 标记),authorize() 主流程据此仲裁最终 `PermissionDecision`。

### 10.2 规则匹配算法

对每条 rule,执行:

```
1. matches_name(tool, rule.value.tool_name)?  否 → skip
2. rule.value.rule_content is None?           → 工具级命中 (不看 input)
3. 调 tool.prepare_permission_matcher(tool_input) 拿闭包 m
4. m(rule.value.rule_content)?                → 精细命中
```

**不再有** `BashPrefixMatcher` / `GlobMatcher` / `MCPWildcardMatcher`
子类 —— "pattern 怎么 apply"完全下放给 `tool.prepare_permission_matcher`。
Engine 本体是 tool-agnostic 的纯遍历器。

MCP server-level wildcard (§13) 是例外:`"mcp__slack"`(`rule_content is None`
且 `tool_name` 以 `mcp__` 开头且无第三段)直接匹配整个 server,
不需要调 prepare_matcher。

### 10.3 综合仲裁(对齐 ToolManager §12.7)

```
rules 匹配结果: rule_deny | rule_ask | rule_allow | none
无条件调 tool.default_risk(input, ctx) → suggestion
无条件调 tool.is_destructive(input)    → destructive flag

最终决策优先级:
  rule_deny                      → PermissionDeny
  > suggestion.default_decision == "deny"   → PermissionDeny + ReasonDefaultRisk
  > rule_ask ∨ suggestion == "ask"          → PermissionAsk
  > rule_allow                              → PermissionAllow(updated_input=None)
  > suggestion.default_decision == "allow"  → PermissionAllow
  > fallback                                → PermissionAsk
```

### 10.4 Mode override

进入上面的流程**之前**:
- `ctx.mode == "plan"` 且 `tool.kind ∈ {edit, delete, execute}` →
  `PermissionDeny("plan mode forbids side effects")` + `ReasonMode(mode="plan")`
- `ctx.mode == "bypass"` → `PermissionAllow` + `ReasonMode(mode="bypass")`
- `ctx.mode == "default"` → 正常走

---

## 11. SessionGrantCache(内部组件)

### 11.1 Scope

**单 session 内存 only,不落盘**。用户勾 "Allow always" 只影响当前
session;跨 session 想永久记住的,通过"建议用户手动写入 user/project
config"路径(UI 可以提供 "Save to project settings" 按钮,最终效果
是写一条规则进 `config.yaml`,走 RuleStore 那一路)。

对齐 CC 的 `PermissionUpdate.destination="session"`([Tool.ts:123-148](../../../../../projects/claude-code-main/src/Tool.ts)),
CC 还支持 `destination="userSettings"` 直接写 `~/.claude/settings.json`;
mustang 走 ConfigManager 统一落盘,不在 grant 层做第二条路径。

**跨 session 复用(cross-session persistence)暂不做** —— 如未来用户
反馈"每次新 session 都要重新 approve"很烦,再引入一个 opt-in 的
project-scope cache 文件。目前严格的"关了电脑就忘"更安全。

### 11.2 allow_always 匹配语义

写入规则(由 `ToolExecutor` 调 `authorizer.grant()` 触发):

```
def grant(tool, input, ctx):
    # 无脑持久化 —— 对齐 CC PermissionUpdate.ts:349 persistPermissionUpdates 行为。
    # is_destructive 的护栏在 PermissionAsk.suggestions 构造时(§3.3),
    # 走到这里意味着用户从 UI 上选了 allow_always, 且 UI 上存在该按钮,
    # 所以工具必然非 destructive, 不需要再检查。
    signature = _compute_signature(tool, input)
    cache[ctx.session_id][signature] = grant_entry
```

命中判定(`authorize()` 短路 1):

```
signature = _compute_signature(tool, input)
if signature in cache[ctx.session_id]:
    return PermissionAllow(reason=ReasonSessionGrant(...))
```

### 11.3 Input signature 算法

对齐 Claude Code 的 "exact command string" 策略(`PermissionRuleValue.ts:67-70`):
grant 存整条精确 input,不做 argv 前缀合并。

- **所有 tool 统一**:`sha256(tool.name + ":" + canonical_json(tool_input))`
  - canonical_json: key 字典序 + UTF-8 + 无空白
  - Bash 工具 `{"command": "npm install"}` 和 `{"command": "npm install -g"}`
    产生**不同** signature,各需单独 grant。用户想要"所有 npm install
    变体自动 allow"必须去 config 写 `"Bash(npm install:*)"` 规则

CC 走 rule-based matching(按 pattern 命中多种 input),session grant
里存的是**具体 allowed 过的那一次 input**,不是 pattern。mustang MVP
精确一致:grant cache 服务"**就这一条输入**再点 allow_always"的精确
复用;用户想要泛化,必须去 `config.yaml` 写 rule。

### 11.4 生命周期

- **创建**:`SessionHandler.new(session_id)` → `authorizer.on_session_open(session_id)`
- **使用**:每次 `authorize()` 查短路 1
- **清理**:`SessionHandler.on_close(session_id)` → `authorizer.on_session_close(session_id)` → 弹出 entry 释放内存
- **Session resume**:`session/load` 时 grant cache 空,用户需重新回答

### 11.5 Sub-agent grant 继承(对齐 CC `runAgent.ts:470-479`)

当 AgentTool 递归 spawn 一个 sub-agent,sub-agent 的 session grant cache
**为空**,不继承父 session 的 `alwaysAllowRules.session`。只有来自
**flag 层**(CLI `--permission-rule` 参数 + `MUSTANG_PERMISSION_RULES` env)
的规则自然继承,因为它们通过 RuleStore 的 flag 层全进程共享 —— 不是
session grant cache 的机制。

**为什么 CC 这样做**:session grant 代表"用户亲口对**当前 root
session** 的这次输入说 allow";sub-agent 是另一个"身份"(不同 agent_depth,
理论上可能跑别的任务),不应该继承 root 用户的一次性授权。想要跨
agent 的授权,用户必须通过 CLI flag 或写进 project/user config 持久化。

**实现层面**:
```
# AgentTool.call 里(tool-manager.md §6.4)
sub_session_id = _derive_subagent_session_id(ctx.session_id, ctx.agent_depth + 1)
await authorizer.on_session_open(sub_session_id)  # 空 grant cache
async for event in spawn_subagent(...):
    ...
await authorizer.on_session_close(sub_session_id)  # 回收
```

**`should_avoid_prompts` 继承语义**(Option C,不硬编码):
sub-agent 的 `AuthorizeContext.should_avoid_prompts` 由 Session 层计算,
信号是 "根 session 此刻能不能路由 permission 请求给人类"。

- 根 session 有活跃 WS 或 interactive gateway → sub-agent 也能 ask
  (permission 请求冒泡到根 session 的通道,用户看到来自 sub-agent 的问题)
- 根 session 离线 / 非 interactive → sub-agent 也走 auto-deny

这样设计的好处:sub-agent 不会因为"身份"被削弱能力(同一个用户在
同一个上下文下的工作,root 能问我就 sub 也能问),但当用户确实不
在线时(cron / 脱离终端的 gateway),整个 session 都退化到"只能
跑 pre-approved 的东西",边界一致。

---

## 12. BashClassifier(内部组件)—— 仅含 LLMJudge

**重大说明**:argv 解析 + allowlist / denylist 的**领域知识**归
`BashTool.default_risk` 拥有,**不在** BashClassifier 里(对齐 ToolManager
§11.2)。Authorizer 里的 BashClassifier 组件**只**负责:

1. LLMJudge:当 `BashTool.default_risk` 返 `(medium, ask, ...)` 且主流程
   走到 `PermissionAsk` 时,在 pop-up 之前先 speculative 跑一次 LLM 判断
   —— 若 LLM high-confidence 说 "safe",直接 allow 不弹 UI
2. Denial tracking:追踪 LLM 的 deny 计数(对齐 CC),超限后回归问用户
3. Fail-closed vs fail-open:LLM API 失败时的降级行为

### 12.1 调用时机 + 触发判定

**触发判定**(对齐 Claude Code `bashPermissions.ts` 内部通过 tool name 匹配):

```python
# kernel/tool_authz/constants.py
BASH_TOOL_NAME: Final = "Bash"   # 与 kernel.tools.builtin.bash.BashTool.name 必须相等
```

Authorizer 用 `tool.name == BASH_TOOL_NAME` 判断是否跑 LLMJudge —— **不用
isinstance**(避免硬依赖 `BashTool` 类,跨 package 循环 import)、**不用
class flag**(CC 没这设计)。字符串相等 + 一个全 package 引用的 constant
是最简单且对齐 CC 的做法。如果将来有第二个需要 LLMJudge 的工具,改成
set lookup 即可(`tool.name in _LLM_JUDGED_TOOLS`)。

**调用流程**:

```
authorize() 主流程走到 "ask" 决策:
  if tool.name == BASH_TOOL_NAME and ctx.should_avoid_prompts == False
     and PermissionsSection.bash_llm_judge_enabled:
     verdict = await bash_classifier.classify(tool_input, ctx)
     match verdict:
       case "safe"             => return PermissionAllow + ReasonBashClassifier
       case "unsafe"           => return PermissionDeny + ReasonBashClassifier
       case "unknown"          => 继续返 PermissionAsk(由 caller 问用户)
       case "budget_exceeded"  => 继续返 PermissionAsk
```

### 12.2 Model 选择

LLMJudge 的模型由 LLMManager 的 `current_used` role 系统管理,**不在
PermissionsSection 里重复造轮子**。用户配置路径:

```yaml
# kernel.yaml
llm:
  current_used:
    default: claude-opus      # 主对话模型
    bash_judge: haiku         # ← LLMJudge 用这个
```

实现细节:
- **取用**: BashClassifier 首次需要时调 `llm_manager.model_for("bash_judge")`
- **LLMManager 懒绑定**: step 3 (authorizer) 早于 step 4 (provider),authorizer
  自己不持 LLMManager 引用;classifier 在首次 LLMJudge 调用时通过
  `module_table.get(LLMManager)` 取
- **未配置(`bash_judge=None`)**: BashClassifier 进入"disabled"状态,
  `classify()` 直接返 `"unknown"` → 用户被问
- **配置了但未就绪**: LLMManager 启动时会验证 role 引用,未知 model 导致
  kernel boot 失败 —— 用户必须修对 `models:` 表才能启动
- **首次调用路径**: `bash_classifier.py` 通过 `_resolve_model()` 调
  `llm_manager.model_for("bash_judge")`,失败(KeyError)则自降级为 "disabled"
  并 log warning(不阻塞,friendly fail)

### 12.3 Denial tracking(对齐 CC `denialTracking.ts`)

每 session 内部维护:

```python
@dataclass
class DenialCounters:
    consecutive_denies: int = 0
    total_denies: int = 0

# 常量(对齐 CC denialTracking.ts:12-14)
MAX_CONSECUTIVE = 3
MAX_TOTAL = 20
```

当 LLMJudge 返 "unsafe"(denial):
- `consecutive_denies += 1`,`total_denies += 1`
- 检查 `consecutive_denies >= MAX_CONSECUTIVE` 或 `total_denies >= MAX_TOTAL`
  → **session 内此后不再调 LLMJudge**,直接走"问用户"(verdict = "budget_exceeded")

当 LLMJudge 返 "safe":
- `consecutive_denies = 0`(连续计数清零),`total_denies` 不变

用户在 session 中手动 allow 了某条 ask 后也 reset `consecutive_denies`。
对齐 CC 的 `handleDenialLimitExceeded` 行为:**超限 fallback 到问用户,
而不是 auto-deny**。

### 12.4 缓存策略(不做应用层缓存)

CC **不做**应用层 by-command 缓存,靠 Anthropic API 的 prompt cache
(1h TTL,cache_control 打在 system prompt + CLAUDE.md 前缀)。
mustang 照抄:

- 不维护 `{cmd_string: verdict}` 的 LRU
- 调 LLM 时 prompt 模板的前缀段打 `cache_control`(交给 LLMManager/Provider
  的现有机制处理),具体命令作为 variable 后缀
- 结果:同 session 内重复 classify 同命令 ≈ cache hit(低延迟 + 低 cost),
  跨 session 不复用

这样 mustang 不需要在 ToolAuthorizer 里管 cache 生命周期,简化实现。

### 12.5 Fail-closed vs fail-open(FlagManager 控制)

LLMJudge API 调用失败(timeout / 网络 / provider 错误):

```python
# FlagManager 里
permissions.bash_llm_judge_fail_closed: bool = True  # 对齐 CC tengu_iron_gate_closed 默认
```

- `True`(默认):LLM 失败 → 等同 "unsafe" → 最终 `PermissionDeny` +
  `ReasonBashClassifier(verdict="budget_exceeded")`(日志里记 classifier
  crash)+ suggest 用户稍后重试
- `False`:LLM 失败 → 等同 "unknown" → 继续 `PermissionAsk` 问用户

对齐 CC [`permissions.ts:845-875`](../../../../../projects/claude-code-main/src/utils/permissions/permissions.ts) 的行为,flag 缺省值选 `True`(安全派)。

### 12.6 Prompt 注入防御

发给 LLM 的 prompt 包含 bash 命令原文时,**必须**用固定模板 + XML 标签
包裹,避免命令里的 `</instructions>` 等 prompt injection。模板文件
`kernel/tool_authz/bash_classifier/prompts.py`,作为 security review
的一部分强制 review。

---

## 13. MCP 规则模式

MCP tool 的 `tool_name` 格式是 `mcp__<server>__<tool>`。三种规则形态:

| DSL | 匹配 |
|---|---|
| `"mcp__slack"` | server `slack` 的**所有**工具 |
| `"mcp__slack__channel_create"` | 单个 tool |
| `"mcp__*"` | 所有 MCP 工具 |

**Rule parsing**:`split("__")` 后段数判断(抄 CC [`mcpStringUtils.ts:19-32`](../../../../../projects/claude-code-main/src/services/mcp/mcpStringUtils.ts)):
- 2 段 → server-level
- 3 段 → tool-level
- 首段非 `mcp` → 当普通工具名处理

### 13.1 filter_denied_tools() —— 给 ToolManager snapshot 用

```python
def filter_denied_tools(tool_names: Iterable[str]) -> set[str]:
    """返回被 deny rule 屏蔽的 tool_name 子集。

    ToolManager.snapshot_for_session() 在组装 tool pool 时调一次:
        pool = [t for t in registered_tools
                if t.name not in authorizer.filter_denied_tools({t.name for t in registered_tools})]

    被屏蔽的工具**不进** snapshot.schemas, LLM 根本看不到它们 ——
    对齐 CC tools.ts:262-269 的 filterToolsByDenyRules 行为。
    """
```

这是 per-session pool 级别的过滤,和 per-call 的 `authorize()` 不同:

- `filter_denied_tools`:只看 **deny rule**,只看 **tool name**,不调
  Tool 任何方法;输入 a list of names,返回 a set of denied names。
  用于"完全隐藏"
- `authorize()`:看全部 rule 类型 + Tool 领域信息 + session grant,
  用于每次调用决策

MCP server-level deny (`deny: ["mcp__slack"]`)在这里生效 —— 整个
slack server 的 tool 都被屏蔽,LLM 看不到 `mcp__slack__channel_create`。

---

## 14. Hook 触发

### 14.1 Event schema

ToolAuthorizer 产出每个非 allow 决策时 fire 事件给 HookManager:

```python
class PermissionRequestedEvent(BaseModel):
    session_id: str
    tool_name: str
    tool_input: dict[str, Any]
    ask: PermissionAsk
    timestamp: datetime
    """ask 决策产出时 fire"""

class PermissionDeniedEvent(BaseModel):
    session_id: str
    tool_name: str
    tool_input: dict[str, Any]
    decision: PermissionDeny
    timestamp: datetime
    """deny 决策产出时 fire(含 mode=plan、fail-closed、deny rule 等各种 deny)"""
```

### 14.2 Fire 顺序(对齐 CC `toolExecution.ts`)

针对一次 tool call,整条 hook 链的严格顺序是:

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. authorize()                                                  │
│    - 产出 PermissionAllow  → 进下一步                            │
│    - 产出 PermissionAsk    → fire permission_requested;         │
│                              等 on_permission 回包               │
│                              - 用户 approve → 等同 allow         │
│                              - 用户 deny    → fire              │
│                                               permission_denied; │
│                                               **skip 步骤 2-4** │
│    - 产出 PermissionDeny   → fire permission_denied;            │
│                              **skip 步骤 2-4**                  │
├─────────────────────────────────────────────────────────────────┤
│ 2. fire pre_tool_use hook                                       │
│    - hook 可 rewrite input / block                              │
├─────────────────────────────────────────────────────────────────┤
│ 3. tool.call(input, ctx) —— 实际执行                             │
├─────────────────────────────────────────────────────────────────┤
│ 4. fire post_tool_use hook                                      │
│    - hook 可 rewrite llm_content                                │
└─────────────────────────────────────────────────────────────────┘
```

**关键不变量**(CC `toolExecution.ts` 的行为):

- `pre_tool_use` / `post_tool_use` **只在 authorize allow 时 fire**
- `permission_denied` / `permission_requested` 在 authorize 阶段就 fire,
  不等 tool 执行
- authorize 内部的多次 deny 决策(mode=plan、fail-closed、deny rule 等)
  都经 `permission_denied` 这一个统一事件,不分来源多个 event
- hook 失败/抛异常 **不影响** authorize 决策本身,只记 log

### 14.3 订阅建议

默认无 hook 订阅。用户在 `hooks.yaml` 订阅可做审计落盘、Slack 通知、
企业 audit pipeline。mustang 自身**不**内置审计文件 —— 部署场景差别
太大,交给 HookManager 生态。

---

## 15. 生命周期

### 15.1 启动顺序(step 3)

```
启动时:
  1. ConnectionAuthenticator 已就绪(step 2)
  2. ToolAuthorizer.startup():
     - bind ConfigManager "permissions" section
     - RuleStore 加载 4 层(user/project/local → ConfigManager;
       flag → CLI/env 一次性读入)
     - 订阅 ConfigManager signal
     - 构造 RuleEngine(无状态)
     - 构造空 SessionGrantCache
     - 构造 BashClassifier(不绑定 LLMManager,懒绑定)
  3. Provider(step 4) / Tools(step 5) / ... 继续启动
```

### 15.2 降级策略

- `RuleStore` 初始 parse 失败 → log + 空规则集继续
- `LLMManager` 不可用 → LLMJudge 绕过,BashClassifier 退化为 noop,
  authorize 主流程走正常 ask
- ToolAuthorizer 整个 subsystem load 失败 → **orchestrator fallback
  到 allow-all + log warning**。此行为由 orchestrator 实现(见
  [tool-manager.md § Phase 2a](tools.md)),authorizer 自身不参与

### 15.3 Shutdown

清空 SessionGrantCache + 解订 signal。无持久化状态需要 flush。

---

## 16. Security Requirements

1. **Fail-closed 为默认准则**:解析错误、LLMJudge 异常、内部 unexpected
   exception → `ReasonFailClosed` 的 deny 决策,决策 message 仅给 LLM 看
   "permission check failed",**不** 暴露内部错误细节到 LLM 输入
2. **updated_input 安全边界**:对齐 CC(无 feature flag,always-on),
   authorizer 有权改写入参 —— 这是个强能力,所有返 updated_input 的代码
   路径必须通过 security review 关:
   - (a) 改写必须可见于 `DecisionReason`(推荐做法:在 reason 字段里
     附 `"input_mutated: --force 已被剥离"` 这样的描述)
   - (b) 改写只能"收紧不放松"—— 例:剥 `rm -rf` 的 `-f` 变成 `rm -r`
     是 OK 的(需人类确认),加一个 `--force` 是绝不允许的
   - (c) 单测覆盖所有 mutation paths,断言 mutated input 的语义等于或
     严于原 input(property-based 测试优先)
   每次新增返 updated_input 的代码路径,security review 必看
3. **凭证不进决策**:`AuthorizeContext.connection_auth` 仅提供只读引用
   (未来企业 IAM 读 `credential_type` / `remote_addr`),**不**暴露
   原始 credential 给任何 matcher / hook
4. **PII 脱敏**:HookManager 事件里的 `tool_input` 可能含敏感信息,
   authorizer 自身**不**脱敏,订阅方按需处理
5. **LLMJudge prompt 注入防御**:见 §12.6
6. **BashClassifier denial tracking 不能被 LLM 主动重置**:只有"用户手动
   allow"才 reset `consecutive_denies`;LLM 自己产生一条 `git status`
   让 classifier 返 safe 不算 reset —— 避免"LLM 先用安全命令把计数清零
   再发危险命令"的对抗

---

## 17. 实装阶段

| Phase | 任务 | 依赖 |
|---|---|---|
| 1a | 数据模型:`PermissionRule` / `PermissionDecision` / `DecisionReason` / `AuthorizeContext` Pydantic 定义 | —— |
| 1b | `RuleParser`:DSL → `PermissionRule`,含转义 + fail-closed | 1a |
| 1c | `RuleStore`:4 层(user/project/local/flag)加载 + Signal 订阅 | 1b, ConfigManager |
| 1d | `RuleEngine`:遍历 rules + 调 tool contract(prepare_matcher / default_risk / is_destructive)+ 综合仲裁 | 1a, Tool ABC 已 land |
| 1e | `SessionGrantCache` + input signature + destructive 护栏 | 1a |
| 1f | `ToolAuthorizer` Subsystem 串联:`authorize()` 主流程,mode override,短路 1,hook fire,`filter_denied_tools()` API | 1c/1d/1e |
| 2a | Orchestrator `ToolExecutor` 接入:`authorize()` 替换 `allow-all` stub;`grant()` 在 `allow_always` 时调 | 1f, Tools 落地 |
| 2b | Session 层 `session/request_permission` 往返 + `on_permission` 实现 + `should_avoid_prompts` 注入 | 2a |
| 2c | `BashClassifier` LLMJudge + denial tracking + fail-closed flag + prompt-injection safe 模板 | 1f, LLMManager |
| 2d | HookManager 接入:`permission_denied` / `permission_requested` 事件 schema + 单测 | 1f, HookManager |
| 2e | `updated_input` 首个应用案例(BashClassifier 把 dangerous flag 剥掉) + security review checklist | 1f, 2c |
| 2f | ToolManager 接入:`snapshot_for_session` 调 `filter_denied_tools` 剥离 deny-listed tool | 1f, ToolManager |
| 3 | E2E:真 ConfigManager + 真 Session + ACP `session/request_permission` + ToolManager 接入 | 2a-2f |

工作量估计:~700 行源码 + ~1200 行测试,约 4 个 dev day(ToolManager
先 land,Authorizer 跟 ToolManager 紧耦合,所以两边协同推进)。

---

## 18. 开放问题

**所有 7 个开放问题在本次讨论中全部闭合**,分别:

| # | 问题 | 决议 |
|---|---|---|
| Q1 | Rule 数据模型 | DSL + 内部结构化,抄 CC |
| Q2 | Rule 层数 | **4 层**(user/project/local/flag)—— 不做 machine-wide policy 层,mustang 是 per-user kernel 无"多用户共享一台机器"场景 |
| Q3 | Signal 订阅 vs pull | Signal 订阅(kernel 场景必需)|
| Q4 | Grant cache 跨 session 复用 | **不做**,session 内存 only |
| Q5 | `updated_input` 字段 | **加**,无 feature flag(对齐 CC `permissions.ts:423` 的 `?? input` 无条件应用,CC 不提供关闭开关)|
| Q6 | MCP server-level rule | 支持(`mcp__<server>` 即整个 server)|
| Q7 | BashClassifier 做 LLMJudge | **做**,对齐 CC denial tracking 机制 |

### 实装期再决定的细节(不阻塞 Phase 2)

1. **Sub-agent grant 继承**:已决议为**不继承** session grant cache(§11.5,
   对齐 CC `runAgent.ts:470-479`)。flag 层规则通过 RuleStore 自然继承。
   `should_avoid_prompts` **不硬编码**,由 Session 层动态判定"根 session
   此刻能否路由 permission 请求"(Option C,比 CC 的 interactive-inherit
   行为更贴合 kernel 架构)
2. **LLMJudge 触发机制**:已决议为 `tool.name == BASH_TOOL_NAME` 字符串
   相等(§12.1,对齐 CC)。将来多个工具需 LLMJudge 时改 set lookup
3. **denial tracking reset 条件的细化**:目前设计是"用户 allow 时 reset
   consecutive"。若实装后发现用户手动 allow 很少发生,考虑是否每 N 分钟
   自动 reset 一次(CC 当前无此逻辑,我们先不做)
4. **`updated_input` 的首个使用场景**:计划是 BashClassifier 剥 `--force`
   类 flag;实装时具体定哪些 flag 算"安全剥离"+ 每条规则的 security review
5. **LLMJudge 的 prompt 模板**:初版用最朴素结构,收集真实误判数据后
   迭代 few-shot 案例(CC 的真实 prompt 在 ANT-only 代码里,不可见)

---

## 19. Related

- [AuthN/AuthZ 拆分决策(D22)](../../reference/decisions.md#d22--authn--authz-split-into-two-subsystems)
- [ConnectionAuthenticator(AuthN 姐妹)](../../kernel/subsystems/connection_authenticator.md)
- [ToolManager(信息源侧 owner)](tools.md)
- [Kernel architecture § 启动顺序](../../kernel/architecture.md)
- Claude Code 对应实现(blueprint):
  - `src/utils/permissions/permissionRuleParser.ts` — DSL parser
  - `src/utils/permissions/permissionsLoader.ts` — 5-layer load
  - `src/utils/permissions/permissions.ts` — main decision loop
  - `src/utils/permissions/PermissionUpdate.ts` — grant persistence
  - `src/utils/permissions/denialTracking.ts` — LLMJudge budget
  - `src/utils/permissions/yoloClassifier.ts` — LLMJudge impl
  - `src/utils/permissions/bashClassifier.ts` — bash LLMJudge 入口
  - `src/tools/BashTool/bashPermissions.ts` — BashTool.default_risk 等价物
  - `src/Tool.ts` — Tool.checkPermissions / prepare_permission_matcher 等价物
  - `src/types/permissions.ts` — PermissionDecision / DecisionReason types
