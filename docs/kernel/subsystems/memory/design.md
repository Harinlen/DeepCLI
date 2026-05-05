# Memory Subsystem — 设计方案

> 综合 13 个竞品项目分析（见 [research.md](research.md)）、
> 用户明确需求、D17 架构决策，以及 DeepCLI kernel 约束。
>
> **设计哲学**："人能看见的文件"为基础，
> "会不会用比记了什么更重要"（MetaMem）为指导，
> 零外部依赖，多语言原生支持。

---

## 一、设计原则

### 用户明确要求（不可违背）

1. Claude Code memory 经常幻觉和遗忘 → **每个决策都必须验证是否避免了这些问题**
2. 记忆必须分层分类存储 → 语义/情景/专业/长短期/用户画像**分开保存**
3. MD 文件直接保存 → 透明、可编辑、git 友好
4. 后台 agent 可选配置独立 LLM → LLMManager 支持 `memory_model`，不配则用默认
5. 检索必须有结构化 scoring → 绝不能像 CC 那样直接选文件名
6. 多语言同等有效 → 中英文检索质量不能有落差

### 采纳的项目思路

| 来源 | 采纳内容 | 拒绝内容 |
|------|---------|---------|
| Claude Code | 双通道注入、staleness caveat、guardrail | LLM-only 检索（幻觉源头）|
| OpenClaw | Hybrid search、MMR、时间衰减、pre-compaction flush | 重量级索引、tool-based 读取 |
| Hermes | Fence 注入、prefetch-once、atomic write | 冻结快照延迟生效 |
| MetaMem | **核心论点**：会不会用 > 记了什么 | 双模型评判循环 |
| Letta | **记忆树组织** | PG/Git 重基础设施 |
| MemU | **显著性感知**（access_count）、后台 agent 概念 | 双 agent 架构 |
| ReMe | MD 文件存储、when_to_use 检索、delta watcher | ReAct agent 写入成本 |
| OpenViking | L0/L1/L2 分层思路 | 三套模型依赖 |
| Hindsight | Reflect 合成、4 路 RRF | PG+pgvector 依赖 |
| MeMOS | 两阶段异步写入、幻觉过滤 | 5 套基础设施 |
| Text2Mem | Lock 语义、confirmation 安全阀 | 形式化 IR |
| Second-Me | Source 来源标记（from ConfidenceLevel）、变化追踪 | 数字孪生定位 |
| Mem0 | 双 prompt 策略、UUID 幻觉处理 | 20+ 后端全家桶 |

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      MemoryManager                          │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ MemoryStore  │  │  Relevance   │  │ BackgroundAgent   │  │
│  │ (IO + tree)  │  │  Selector    │  │ (optional cheap   │  │
│  │              │  │  (scoring)   │  │  LLM, async task)  │  │
│  └──────┬───────┘  └──────┬───────┘  └─────────┬─────────┘  │
│         │                 │                     │            │
│  ┌──────▼─────────────────▼─────────────────────▼────────┐  │
│  │                    MemoryIndex                         │  │
│  │  (in-memory cache of all frontmatter across tree)      │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────┬─────────────────────────┬─────────────────────┘
               │                         │
    MemorySource Protocol         MemoryTools (5 tools)
    (read-only → Orchestrator)    (write → ToolManager)
```

### 与 Kernel 的集成

- **Startup 位置 9**（在 ToolManager 之后、SessionManager 之前）
- **失败策略**：degrade, not abort — `deps.memory = None` 时 PromptBuilder 跳过
- **Orchestrator 只持有** `deps.memory: MemorySource | None`（narrow Protocol）
- **LLM 使用**：通过 `LLMManager.get_model("memory")` 获取 memory model。
  用户可选配置为更便宜的模型（如 Haiku）；**不配置则使用默认对话 model**

---

## 三、记忆分层存储

### 记忆分类体系

结合 Hindsight 的认知科学映射 + 用户要求的分层分类：

| 类别 | 认知映射 | 生命周期 | 存储目录 | 示例 |
|------|---------|---------|---------|------|
| **semantic** | 语义记忆 | 长期 | `semantic/` | "用户是后端工程师"、"项目用 FastAPI" |
| **episodic** | 情景记忆 | 中期 | `episodic/` | "2026-04-15 修复了认证 bug"、"上次部署失败因为配置" |
| **procedural** | 程序性知识 | 长期 | `procedural/` | "这个项目的 PR 流程"、"用户偏好的代码风格" |
| **profile** | 用户画像 | 长期 | `profile/` | 客观："用户名 Saki"；主观："厌恶冗长输出"；变化："以前偏好 tabs，现在改用 spaces" |

### 存储结构（记忆树）

```
~/.mustang/memory/                    # Global scope
├── index.md                          # 全局索引（常驻 system prompt）
├── log.md                            # 审计日志（不注入 prompt）
│
├── profile/                          # 用户画像（长期）
│   ├── identity.md                   #   身份、角色（客观事实）
│   ├── preferences.md                #   偏好、习惯（主观偏好）
│   └── history.md                    #   变化追踪（"以前说X，后来改为Y"）
│
├── semantic/                         # 语义知识（长期）
│   ├── tech_stack.md                 #   技术栈事实
│   └── team_context.md              #   团队背景
│
├── episodic/                         # 情景记忆（中期，可衰减）
│   ├── incident_auth_bug.md         #   具体事件
│   └── decision_api_migration.md    #   决策记录
│
└── procedural/                       # 程序性知识（长期）
    ├── workflow_pr.md               #   流程经验
    └── coding_patterns.md           #   编码模式

.mustang/memory/                      # Project scope
├── index.md
├── config.md                         # 项目级记忆行为配置（disposition）
├── semantic/
├── episodic/
└── procedural/
```

**与 D17 的变化**：
- D17 的 `user/feedback/project/reference` 4 分类 → 替换为认知科学驱动的
  `profile/semantic/episodic/procedural` 4 分类。映射关系：
  - `user` → `profile/`
  - `feedback` → `procedural/`（用户反馈是程序性知识——"怎么做"）
  - `project` → `episodic/`（项目动态是事件）
  - `reference` → `semantic/`（外部资源指针是事实）
- 从平铺结构 → 目录树（Letta 启发）
- 生命周期由 category 决定：episodic 有 30 天半衰期，其余 evergreen 豁免

**Profile 内部区分客观/主观（from MeMOS）**：
- `identity.md` 等文件存**客观事实**（姓名、角色、技术栈）——几乎不变
- `preferences.md` 等文件存**主观偏好**（代码风格、输出详细度、厌恶的模式）——
  可能随时间变化
- 区分的意义：客观事实可以放心引用；主观偏好需要注意是否已过时

**时间线追踪变化历史（from Second-Me）**：
- `profile/history.md` 记录用户偏好的变更轨迹，格式：
  ```
  - 2026-04-10: 偏好从 tabs 改为 spaces（用户明确要求）
  - 2026-04-15: 输出详细度从"详细"改为"简洁"
  ```
- 当后台 agent 检测到 profile memory 的内容被覆盖时，自动追加一条
  变更记录到 history.md
- 变更历史让 LLM 能理解"用户以前是什么样，现在变了"——
  避免基于过时偏好行事

**Per-project 记忆行为配置（Disposition，from Hindsight）**：

项目级 `.mustang/memory/config.md` 可配置记忆行为参数：

```markdown
---
# 记忆行为配置（disposition）
skepticism: 3        # 1-5，怀疑度：高=注入时更多 verify 提醒
recency_bias: 4      # 1-5，时效偏好：高=更偏好新记忆
verbosity: 2         # 1-5，注入详细度：低=只注入 description，高=注入完整 content
---
```

- 不同项目可以有不同的记忆行为——严肃生产项目高怀疑度，
  实验项目低怀疑度
- 全局 `~/.mustang/memory/` 也可以有一个 `config.md` 作为默认值
- 项目级 config 覆盖全局 config
- 不配置时使用内置默认值（skepticism=3, recency_bias=3, verbosity=3）
- Disposition 参数注入到 RelevanceSelector 的 scoring prompt 和
  Channel B 的注入逻辑中，影响选择和注入行为

### 文件格式

```markdown
---
name: <name>
description: |
  200-500 token 的摘要——检索 scoring 的主要目标。
  不是一行标签，而是有意义的内容概括（= OpenViking L1 级别）。
  足够让 LLM 判断这条 memory 是否与当前查询相关。
category: profile | semantic | episodic | procedural
created: 2026-04-19T10:00:00Z
updated: 2026-04-19T10:00:00Z
access_count: 3
source: user | agent | extracted
locked: false
---

<content body — 完整内容，注入给 LLM 看>
```

字段说明：

| 字段 | 来源 | 用途 |
|------|------|------|
| `description` | ReMe(when_to_use) + OV(**L1**) | **检索 scoring 目标**——200-500 token 摘要，不是一行标签。直接作为 BM25 索引和 LLM scoring 的输入。注入时如果 content body 太长可以只注入 description |
| `category` | 用户要求 + Hindsight | 分类存储、type-aware 检索 |
| `access_count` | MemU(显著性) | 排名因子 `log(access_count+1)` + OpenViking hot/warm/cold 分档 |
| `source` | Second-Me(ConfidenceLevel) | 来源标记，写入时确定不变。`user`=1.0, `agent`=0.8, `extracted`=0.6 |
| `locked` | Text2Mem | 用户创建的 memory 防止后台 agent 修改 |

### Index 格式

`index.md` 按分类分组。每条包含 name + description 摘要的**首句**
（完整 description 在文件 frontmatter 中，index 只放首句作为导航）：

```markdown
## profile
- [identity](profile/identity.md) — 后端工程师，5 年经验，主要使用 Python/Go
- [preferences](profile/preferences.md) — 厌恶冗长输出，偏好简洁代码风格
- [history](profile/history.md) — 偏好变更追踪记录

## semantic
- [tech_stack](semantic/tech_stack.md) — 项目用 FastAPI + PostgreSQL + React

## episodic
- [auth_bug](episodic/incident_auth_bug.md) — 2026-04-15 认证 bug 根因是 token 过期配置

## procedural
- [pr_workflow](procedural/workflow_pr.md) — PR 必须有 test plan，squash merge
```

分组让 LLM 和人类都能快速定位。Index 上限 200 行。

---

## 四、检索机制

### 核心原则

> **绝不能像 Claude Code 那样直接选文件名**——那是幻觉的源头。

检索必须有结构化 scoring + 阈值过滤 + 多语言支持。

### 默认策略 — BM25 Pre-filter + LLM Scoring

```python
@dataclass
class ScoredMemory:
    filename: str
    relevance: int      # 1-5 结构化分数
    reason: str         # 一句话解释
    category: str       # 来自哪个分类

class RelevanceSelector:
    """BM25 pre-filter + LLM scoring with structured output."""

    SCORE_THRESHOLD = 2

    async def select(
        self,
        query: str,
        candidates: list[MemoryHeader],
        *,
        top_n: int = 5,
    ) -> list[ScoredMemory]:
        # 1. 按 category 分组构建 manifest：
        #    ## profile
        #    - [0] (3 days ago): 后端工程师，5年经验，主要使用Python/Go...
        #    ## semantic
        #    - [1] (10 days ago): 项目用FastAPI做后端，PostgreSQL...
        #    ...
        #    - 每条使用完整 description（200-500 token），不是一行
        #    - 文件名映射为短整数 alias [0],[1],[2]...（from Mem0）
        #      LLM 返回 alias，系统再映射回真实文件名——
        #      防止 LLM 对长文件名产生幻觉
        #
        # 2. BM25 pre-filter（对 description 字段）：
        #    - CJK 分词：jieba 处理中文（非 whitespace split）
        #    - 取 top 30 候选
        #    - 候选不足 30 条时，补充 access_count 最高的 memory（hot cache）
        #
        # 3. Sufficiency check（from MemU，纯规则，不调 LLM）：
        #    BM25 候选集为空 or 全部 BM25 分数低于阈值
        #    → 直接返回空列表，跳过后续 LLM scoring 调用
        #
        # 4. Side query to memory_model（默认用主 LLM，可配为 Haiku）：
        #    - 返回 JSON: [{alias, relevance: 1-5, reason}]
        #    - Prompt 包含策略规则（MetaMem 启发）：
        #      * "Newer memories preferred when relevance is similar"
        #      * "Ensure topic diversity across categories"
        #      * "Profile memories are almost always relevant"
        #      * "Cross-validate: if two memories contradict, note it"
        #
        # 5. 综合排名（衰减只影响排名，永不自动删除）：
        #    salience = log(access_count + 2)              # from MemU, +2避免冷启动
        #    time_decay = 1.0 if evergreen else             # from OpenClaw
        #                 exp(-0.693 * age_days / 30)       # from MemU, 30天半衰期
        #    source_weight = {user:1.0, agent:0.8, extracted:0.6}  # from Second-Me
        #    final_score = llm_relevance * salience * time_decay * source_weight
        #    evergreen = category in (profile, semantic, procedural)
        #
        # 6. 过滤 relevance < SCORE_THRESHOLD
        # 7. 按 relevance 降序，取 top_n
        # 8. 更新被选中 memory 的 access_count（显著性感知）
        ...
```

**LLM 配置**：
```python
# MemoryManager startup 时：
self._llm = deps.llm_manager.get_model("memory")
# 可选配置为 Haiku 等便宜模型
# 不配置时 fallback 到默认对话 model
```

**CJK 分词**：BM25 对中文几乎失效的根因是 whitespace 分词。
引入 `jieba`（纯 Python，零外部依赖）解决。

**BM25 分数归一化（from Mem0）**：BM25 原始分数范围不固定（取决于
文档集大小和词频分布），需要 sigmoid 归一化到 0-1 才能与 LLM 的
1-5 分合理融合。midpoint 和 steepness 根据 query 长度自适应调整——
短 query 的 BM25 分数天然低，需要更宽松的归一化参数。

**多语言保证**（用户要求 #6）：
- LLM scoring 天然多语言——LLM 处理中英文同样好
- BM25 + jieba 分词保证中文关键词匹配
- Manifest 中的 description 可以是任何语言

### 可选扩展 — Embedding Hybrid（需配置 embedding model）

**Embedding model 是可选配置**——用户不配置则不启用，
系统使用默认的 BM25 + LLM scoring。

```python
# config 示例 — embedding 是可选配置
{
    "llm": {
        "default": "claude-sonnet-4-6",
        "memory": "claude-haiku-4-5",       # 可选
        "embedding": "multilingual-e5-large" # 可选：不配则不启用 embedding
    }
}

# RelevanceSelector 启动时检测
embedding_model = deps.llm_manager.get_model("embedding")
if embedding_model:
    self._strategy = EmbeddingHybridStrategy(embedding_model, bm25, llm)
    # Embedding + BM25 + LLM scoring 三路融合
    # MMR re-ranking（Jaccard 去重，from OpenClaw）
    # 可选 Entity boost（from Mem0）
else:
    self._strategy = BM25PlusLLMStrategy(bm25, llm)  # 默认策略
```

---

## 五、双通道注入

### Channel A — Index 常驻 system prompt（cacheable）

```python
# PromptBuilder.build()
index_text = await deps.memory.get_index_text()
if index_text:
    sections.append(PromptSection(
        text=f"# Memory\n\n{index_text}",
        cache=True,
    ))
```

- Index 按 category 分组（见上方格式），不含 content
- `cache=True`：只在写操作后 invalidate，绝大多数 turn 命中缓存
- 上限 200 行，超出时 truncate 并警告

### Channel B — 相关文件 per-turn 注入

```python
# Orchestrator query 开始时 prefetch-once
relevant = await deps.memory.query_relevant(user_message_text, top_n=5)

# 注入为 fence-marked user message
for entry in relevant:
    # Staleness caveat 只对 episodic 类生效——
    # profile/semantic/procedural 是 evergreen，不需要 verify 提醒
    age_caveat = (
        f"\n⚠ This memory is {entry['age_days']} days old. "
        "Verify before acting on it."
        if entry["category"] == "episodic" and entry["age_days"] > 7 else ""
    )
    inject = (
        f"[Memory recall — {entry['category']}] {entry['name']}\n"
        f"{entry['content']}{age_caveat}"
    )
    # 包装为 <system-reminder> user message
```

### Channel C — 记忆使用策略（MetaMem 启发）

作为独立 cacheable prompt section 注入 200-500 token 的**策略规则**，教 LLM 怎么用记忆：

```
# 记忆使用策略（prompts/memory_strategy.txt）

当你收到记忆注入时：
- Profile 类记忆几乎总是相关的——用它来调整你的回复风格和详细程度
- Episodic 类记忆有时效性——检查 age，超过 7 天的事件可能已经过时
- Procedural 类记忆是经验教训——当你做类似操作时主动应用
- 如果两条记忆矛盾，优先较新的那条，并提醒用户
- 不要在回复中逐条引用记忆——自然地融入你的回答
- 如果记忆中的信息与当前代码状态矛盾，以代码为准
```

这不是 MetaMem 那样的双模型评判循环——只是在 prompt 中嵌入静态策略规则。
规则可以基于使用经验手动迭代更新。

---

## 六、写入机制

### Memory Tools（5 个工具）

```python
# 注册到 ToolManager，permission = NONE（沙盒化）

memory_write(name, category, description, content)
    # source 字段从调用上下文自动推断，不是参数：
    #   - 主 agent 调用 → source="agent"
    #   - 后台 agent 提取 → source="extracted"
    #   - 用户通过 /memory 或手动编辑 → source="user"
    # 写入新 memory 到对应 category 子目录
    # 如果同名文件存在：
    #   - locked=true → 拒绝，提示用户手动编辑
    #   - locked=false → 覆盖，旧版本记入 log.md
    # 自动更新 index.md
    # 原子写入：temp file → os.replace()

memory_append(name, content)
    # 追加到已有 memory 文件末尾
    # locked 文件也允许 append（只禁止覆盖）

memory_delete(name, confirmation=True)
    # 需要 confirmation=True（from Text2Mem 安全阀）
    # 删除文件 + 从 index.md 移除
    # 写 log.md 审计行

memory_list(category=None)
    # 返回 name + description + category + age + access_count
    # 可选按 category 过滤
    # 无需读取 content，从 MemoryIndex cache 返回

memory_search(query, top_n=5)
    # 显式搜索（给 LLM 主动使用）
    # 调用 RelevanceSelector，返回 top_n 结果
    # 与 Channel B 的自动注入互补
```

### 安全 guardrail

| 措施 | 来源 | 说明 |
|------|------|------|
| PermissionEngine guardrail | CC + D17 | `~/.mustang/memory/**` 的 file_edit/file_write 硬编码拒绝 |
| 写入扫描 | Hermes | `_scan_content()` 拒绝 prompt injection 模式 |
| 原子写入 | Hermes | `temp → os.replace()` + `fcntl.flock` |
| Confirmation | Text2Mem | delete 操作需 `confirmation=True` |
| Lock | Text2Mem | `locked: true` 的文件拒绝覆盖 |
| 文件名 sanitize | D17 | 只允许 `[a-z0-9_-]`，沙盒化 |

---

## 七、记忆生命周期

> **核心原则：衰减只影响检索排名，永不自动删除。**
> 13 个竞品中只有 2 个敢做自动删除（MeMOS 的 FIFO、Text2Mem 的 TTL），
> 其余全部靠手动。MemU 和 OpenViking 验证了"衰减影响排名不影响存在"
> 的模式。我们遵循这个行业共识。

### 记忆诞生与来源标记

`source` 字段标记 memory 的**来源**，写入时确定，之后不变
（不做升级——来源是事实，不会改变）：

| 来源 | source 值 | source_weight | 说明 |
|------|----------|---------------|------|
| 用户通过 `/memory` 或手动编辑 | `user` | 1.0 | 最可信——用户亲自写的 |
| 主 agent 通过 memory_write 创建 | `agent` | 0.8 | agent 主动判断写入 |
| 后台 agent 自动提取 | `extracted` | 0.6 | 自动提取，通过检索证明价值 |

> **为什么不做 confidence 升级？** 13 个竞品中没有任何一个实现了
> access_count 驱动的离散升级机制。MemU 和 OpenViking 都是让
> access_count 通过 `log()` **连续地**影响排名——不需要人为设定
> "3 次升 medium、10 次升 high"这样没有验证的阈值。
> source_weight 标记的是来源可信度（from Second-Me ConfidenceLevel），
> 这是一个不变的事实，不需要升级。

### 检索排名公式

整合 MemU（benchmark 验证）+ OpenClaw（evergreen 豁免）+ 我们的 LLM scoring：

```python
# 基础公式（from MemU, LoCoMo 91.2% 验证）
salience = log(access_count + 2)  # +2 而非 +1，避免新 memory 冷启动为 0
time_decay = 1.0 if evergreen else exp(-0.693 * age_days / 30)

# 来源权重（from Second-Me ConfidenceLevel）
source_weight = {"user": 1.0, "agent": 0.8, "extracted": 0.6}[source]

# 综合排名
final_score = llm_relevance * salience * time_decay * source_weight
```

| 因子 | 来源 | 计算方式 | 说明 |
|------|------|---------|------|
| `llm_relevance` | 原创 | LLM scoring 1-5 分 | 语义相关度 |
| `salience` | MemU | `log(access_count + 2)` | 高频访问抵抗衰减，+2 避免冷启动为 0 |
| `time_decay` | MemU + OpenClaw | `exp(-0.693 * age_days / 30)` or `1.0` | 30 天半衰期，evergreen 豁免 |
| `source_weight` | Second-Me | `{user: 1.0, agent: 0.8, extracted: 0.6}` | 来源可信度 |

**Evergreen 豁免规则（from OpenClaw）**：

| category | evergreen? | 说明 |
|----------|-----------|------|
| profile | 是 | 用户身份不过期 |
| semantic | 是 | 技术事实不过期 |
| procedural | 是 | 流程经验不过期 |
| episodic | **否** | 事件有时效性，30 天半衰期（from MemU） |

### Hot / Warm / Cold 三档（from OpenViking）

基于**不含 llm_relevance 的静态分**做 hot cache 决策：

```python
# 静态 hotness（不依赖具体 query，可预计算）
salience = log(access_count + 2)
hotness = salience * time_decay * source_weight

if hotness > 0.6:   # hot — 跳过 BM25+LLM scoring，直接进入注入候选
    ...
elif hotness < 0.2: # cold — 仍存在磁盘，但不进入自动注入候选集
    ...             # 用户可通过 memory_search 主动搜索到
else:               # warm — 正常走 BM25 + LLM scoring 流程
    ...
```

- 阈值 0.2 / 0.6 来自 OpenViking（有 benchmark 验证）
- Hot memory 直接注入 → 减少 LLM scoring 调用
- Cold memory 只能被主动搜索 → 相当于"软遗忘"

### 低排名 memory 的处理

低 access_count + 高 age + extracted source 的 memory 会自然沉到 cold 区，
实际效果等同于"遗忘"——不会被自动注入，但文件仍在磁盘上。

- `/memory lint` 会报告 cold memory 列表，提示用户可手动清理
- 用户执行 `memory_delete` 是唯一的删除路径（需 confirmation）
- **不存在任何自动删除逻辑**

---

## 八、后台 Memory Agent

### 设计原则

- **可选配置独立 LLM**（用户要求 #4）——不配置则用默认 model，配置后可减少成本
- **单个轻量 async task**——不是 MemU 那样的双 agent 架构
- **三层提取策略，互斥避免重复**

### 三层提取

```
Layer 1: 主 agent 直写（最即时）
  ↓ 检测到本轮已写 memory → 跳过 Layer 2/3

Layer 2: Pre-compaction flush（最精准，from OpenClaw）
  ↓ context 压缩前，用 memory model 分析即将丢失的消息

Layer 3: 定期后台整理（最全面，from Letta sleeptime）
  ↓ session 空闲时 / 每 N 轮，用 memory model 整理和合并
```

### Layer 2 — Pre-compaction Flush

```python
async def on_pre_compact(self, messages: list[Message]) -> None:
    """Called before context compaction. Uses memory model."""
    # 用 memory model 分析即将被压缩的消息：
    # - 提取值得保留的事实 → semantic/
    # - 提取事件和决策 → episodic/
    # - 提取用户反馈和偏好 → profile/ 或 procedural/
    # - 幻觉过滤（MeMOS 启发）：二次验证提取结果是否与源消息矛盾
    # - 双 prompt 策略（Mem0 启发）：分离用户发言 vs agent 发言的提取
    ...
```

### Layer 3 — 定期后台整理

```python
async def _background_consolidation(self) -> None:
    """Periodic background task. Uses memory model."""
    # 触发条件（轻量，不频繁）：
    #   - session 结束时
    #   - 或每 10 轮对话后
    #   - 或 memory 文件数超过阈值
    #
    # 任务（用 memory model）：
    # 1. 去重合并：找语义重复的 memory，合并为一条
    #    （OpenViking 4 类决策：skip/create/merge/delete）
    # 2. Hotness 计算：更新所有 memory 的 hotness 分档
    #    （hot/warm/cold，from OpenViking 阈值 0.6/0.2）
    # 3. 矛盾检测：找描述冲突的 memory pairs，标记待解决
    # 4. Index 重建：确保 index.md 与文件系统一致
    #
    # 注意：不自动删除任何 memory。衰减只影响检索排名。
    # cold 区的 memory 不会被自动注入但仍在磁盘上，
    # 用户随时可以通过 memory_search 主动搜索到。
    ...
```

### LLM 配置

```python
# config 示例 — memory model 是可选配置
{
    "llm": {
        "default": "claude-sonnet-4-6",
        "memory": "claude-haiku-4-5"  # 可选：不配则用 default
    }
}

# MemoryManager 内部
self._memory_llm = deps.llm_manager.get_model("memory")
# get_model("memory") 先查 config["llm"]["memory"]
# 找不到则 fallback 到 config["llm"]["default"]
# 用于：relevance scoring、后台提取、去重合并、矛盾检测
```

---

## 九、MemorySource Protocol

```python
class MemoryEntry(TypedDict):
    path: str
    name: str
    description: str
    category: str       # profile | semantic | episodic | procedural
    content: str
    age_days: int
    access_count: int
    source: str         # user | agent | extracted

class MemorySource(Protocol):
    """Read-only interface for Orchestrator / PromptBuilder."""

    async def get_index_text(self) -> str:
        """Return index.md content for system prompt (cacheable)."""
        ...

    async def query_relevant(
        self, prompt_text: str, *, top_n: int = 5
    ) -> list[MemoryEntry]:
        """Score and return top-N relevant memories.
        
        Called once per turn (prefetch-once pattern).
        Uses memory_model for scoring.
        """
        ...
```

---

## 十、`/memory` 命令

```
/memory                    # 按 category 分组列出所有 memory
/memory show <name>        # 显示完整内容
/memory delete <name>      # 删除（需确认）
/memory lint               # 一致性 + 矛盾检测 + hotness 报告
/memory tree               # 显示目录树结构（from Letta）
```

`/memory lint` 的能力（用 memory_model）：
- 检查 index.md 与文件系统一致性
- 检测语义矛盾的 memory pairs
- 合并语义重复的 memory
- 从记忆中 reflect 合成高阶洞察（Hindsight 启发）
- 报告 cold 区 memory（hotness < 0.2），
  但**不自动删除**——仅提示用户可手动清理

---

## 十一、代码结构

遵守 D18（prompt 文本外置到 `.txt`）：

```
src/kernel/kernel/memory/
├── __init__.py              # MemoryManager (Subsystem)
├── store.py                 # MemoryStore (IO layer, atomic write)
├── index.py                 # MemoryIndex (in-memory frontmatter cache)
├── selector.py              # RelevanceSelector → HybridSelector
├── background.py            # BackgroundAgent (extraction + consolidation)
├── tools.py                 # 5 memory tools
├── types.py                 # MemoryEntry, MemoryHeader, ScoredMemory, MemorySource
└── prompts/
    ├── extraction.txt       # 后台提取 prompt
    ├── selection.txt        # relevance scoring prompt
    ├── consolidation.txt    # 去重/合并/矛盾检测 prompt
    └── memory_strategy.txt  # 注入为独立 cacheable 使用策略 section
```

---

## 十二、Startup / Shutdown

```python
class MemoryManager(Subsystem):
    """Position 9. Failure → degrade, not abort."""

    async def startup(self) -> None:
        # 1. 获取 memory model: deps.llm_manager.get_model("memory")
        #    （未配置时 fallback 到默认 model）
        # 2. 解析 memory 目录（~/.mustang/memory/）
        #    确保 4 个 category 子目录存在
        # 3. 加载 MemoryIndex（扫描所有文件 frontmatter）
        # 4. 注册 5 个 memory tools 到 ToolManager
        # 5. 暴露 MemorySource 接口给 SessionManager
        # 6. 启动后台 consolidation task（低频，不阻塞）

    async def shutdown(self) -> None:
        # 1. 取消后台 task（with 5s timeout）
        # 2. Flush dirty index 到磁盘
        # 3. 写 log.md 最终审计行
```

---

## 十三、实现范围

> **不分 Phase，一次性实现完整系统。**
> 唯一的"可选扩展"是 Embedding 检索——取决于用户是否配置了
> embedding model，不配则不启用，系统照常工作。

### 核心系统（必须全部实现）

| 模块 | 内容 |
|------|------|
| **存储** | |
| 目录树 | Global `~/.mustang/memory/` + Project `.mustang/memory/`，各含 4 个 category 子目录 |
| 文件格式 | MD + YAML frontmatter（description/category/source/access_count/locked） |
| Index | 按 category 分组的 index.md（常驻 system prompt，cacheable，200 行上限）|
| Profile 区分 | identity（客观）/ preferences（主观）/ history（变化追踪）|
| Disposition | per-project 记忆行为配置（skepticism/recency_bias/verbosity）|
| 审计 | log.md（200 行滚到 archive），profile 变更自动追加 history.md |
| **检索** | |
| BM25 pre-filter | jieba CJK 分词，缩小候选集到 ~30 条，sigmoid 归一化分数 |
| LLM scoring | memory_model side query，结构化 1-5 分 + 阈值过滤 |
| UUID 短 alias | manifest 中 `[0],[1],[2]...` 映射，防 LLM 幻觉 |
| Sufficiency check | 候选集为空时跳过 LLM 调用 |
| Hot cache | access_count 高的 memory 跳过 scoring 直接注入 |
| 时间衰减 | episodic 30 天半衰期(MemU)，其余 evergreen 豁免(OpenClaw)，只影响排名不删除 |
| 显著性 | 被选中的 memory 自动 access_count++ |
| **注入** | |
| Channel A | Index 常驻 system prompt（cacheable，write 后 invalidate）|
| Channel B | Per-turn prefetch-once，fence 标记 + staleness caveat |
| Channel C | 记忆使用策略规则（memory_strategy.txt，200-500 token）|
| **工具** | |
| 5 tools | memory_write / memory_append / memory_delete(confirmation) / memory_list / memory_search |
| **安全** | |
| 写入保护 | PermissionEngine guardrail + _scan_content() 注入检测 |
| 原子写入 | temp → os.replace() + fcntl.flock |
| Lock | `locked: true` 防覆盖（append 仍允许）|
| Confirmation | delete 操作必须 confirmation=True |
| 沙盒 | 文件名 sanitize `[a-z0-9_-]` + 目录不可逃逸 |
| **后台 Agent** | |
| 三层提取 | 主 agent 直写 > pre-compaction flush > 定期后台整理，互斥 |
| 幻觉过滤 | 后台提取时二次 LLM 验证，丢弃与源矛盾的结果 |
| 双 prompt | 分离用户发言 vs agent 发言的提取 |
| 去重合并 | 4 类决策 skip/create/merge/delete |
| 矛盾检测 | 找描述冲突的 memory pairs，标记待解决 |
| 来源标记 | source=user/agent/extracted（写入时确定，不变），影响 source_weight |
| 排名公式 | `llm_relevance × salience × time_decay × source_weight`（from MemU），**不自动删除** |
| Hot/Warm/Cold | hotness 三档（from OpenViking 阈值 0.6/0.2），hot 跳过 scoring 直接注入 |
| Profile 变更追踪 | 检测到 profile 覆盖时自动追加 history.md |
| **命令** | |
| /memory | 按 category 分组列出 |
| /memory show | 显示完整内容 |
| /memory delete | 删除（需确认）|
| /memory tree | 显示目录树 |
| /memory lint | 一致性检查 + 矛盾检测 + 去重合并 + reflect 合成洞察 |

### 可选扩展（依赖用户配置）

| 扩展 | 触发条件 | 不启用时的 fallback |
|------|---------|-------------------|
| **Embedding hybrid search** | `config["llm"]["embedding"]` 已配置 | BM25 + LLM scoring（核心系统）|
| ↳ MMR re-ranking | 跟随 embedding 启用 | LLM scoring prompt 中的多样性指令 |
| ↳ Entity boost | 跟随 embedding 启用 | 无 |
| **独立 memory model** | `config["llm"]["memory"]` 已配置 | 使用默认对话 model |

---

## 十四、设计决策追溯

| 决策 | 选择 | 为什么 | 对标拒绝 |
|------|------|--------|---------|
| 分类体系 | 认知科学 4 分类 | 用户要求分层 + Hindsight 验证 | CC 的 flat type |
| 存储结构 | 目录树 | 用户要求分开保存 + Letta 启发 | MeMOS 的 Neo4j |
| 文件格式 | MD + YAML | 用户要求透明可编辑 | Mem0 的 opaque payload |
| 后台 LLM | 可选独立 memory_model，默认用主 LLM | 降低成本但不强制 | MemU 的同模型双 agent |
| 检索 | BM25 pre-filter + LLM scoring + 时间衰减 | 用户要求不能像 CC | CC 的 binary 选文件名 |
| 多语言 | LLM scoring + jieba 分词 | 用户要求中英同效 | Hindsight 的英文优化 |
| 基础设施 | 零外部依赖 | 轻量 + 用户拒绝重实现 | MeMOS/Letta 的多组件 |
| 策略注入 | 静态规则在 prompt 中 | MetaMem 核心论点 | MetaMem 的双模型循环 |
| 排名公式 | MemU 公式 + LLM relevance + source_weight | 每个数字都有竞品验证 | 自编的 confidence 升级阈值 |
| 来源标记 | source 写入时确定不变 | Second-Me ConfidenceLevel 模式 | 自编的离散升级（3→medium, 10→high）|
| 衰减 | 30 天半衰期(MemU) + evergreen 豁免(OpenClaw) | 竞品验证的参数 | 自编的 90/180 天半衰期 |
| Hot cache | OpenViking 三档（0.6/0.2 阈值）| 竞品验证的阈值 | 无 hot cache |
| Disposition | per-project 行为配置 | 同一知识库不同场景 + Hindsight | 无配置一刀切 |
| 安全 | guardrail + scan + lock + confirm | 多项目最佳实践组合 | 单一措施不够 |
