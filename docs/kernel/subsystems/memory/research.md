# Memory 竞品研究

> **Quick header**
> - **Role**: research basis for MemoryManager design.
> - **Current design owner**: [`design.md`](design.md).
> - **Use for**: rationale, tradeoffs, and source-project comparison.
> - **Do not use for**: current implementation paths without checking `kernel.agents.mustang.memory.*`.

> 13 个项目的完整分析——3 个参考实现（Claude Code / OpenClaw /
> Hermes Agent）+ 10 个专项 Memory 架构。
>
> 综合了代码级逆向分析与外部独立评测（视频系列逐字稿），
> 两个视角互相印证和补充。
>
> 目标：提取每个项目的核心思路、优缺点，为 DeepCLI Memory 设计提供
> 全面的决策依据。

---

## 一、研究对象总览

### 分类

| 类别 | 项目 | 一句话定位 |
|------|------|-----------|
| **参考实现** | Claude Code | 双通道注入 + forked agent 后台提取 |
| | OpenClaw | Hybrid search (vector+BM25) + pre-compaction flush |
| | Hermes Agent | 冻结快照 prefix cache + HRR 代数检索 |
| **检索与推理** | Hindsight | 三层仿生记忆 + reflect 合成洞察 |
| | MetaMem | 元记忆——学习"怎么用记忆"的策略 |
| **自主管理** | Letta (MemGPT) | 操作系统虚拟内存思想搬进 agent |
| | ReMe | Memory-as-files + ReAct agent 写入 |
| **可插拔基础设施** | Mem0 | 工程化全家桶——五大工厂 + 双存储并行 |
| | Text2Mem | Memory 操作的类型化 IR + 治理 |
| **上下文管理** | OpenViking | 虚拟文件系统 + L0/L1/L2 分层抽象 |
| | MemU | Memory 本身是一个 agent（双 agent 架构） |
| | MeMOS | 记忆操作系统——内核级调度 |
| **个人化** | Second-Me | 本地微调的 AI 分身（数字孪生） |

> **勘误**：初版分析中 `memory/memos/` 目录下是错误的源代码（笔记应用）。
> 现已替换为正确的 MeMOS (Memory Operating System) 代码
> （`memory/MemOS/`），来自 MemTensor + 上海交大/浙大 39 人团队。
> 以下分析基于实际代码逆向。

### 五条技术路线（外部视角总结）

外部分析者将 10 个项目归纳为五条截然不同的设计哲学：

| 路线 | 代表项目 | 核心主张 |
|------|---------|---------|
| **记忆的语言** | Text2Mem | 先定义操作语言，再谈实现 |
| **记忆的中间件** | Mem0 | 极致可插拔，工厂模式全家桶 |
| **管理记忆的 agent** | Letta | 操作系统虚拟内存，agent 自治 |
| **人能看见的记忆** | ReMe | 文件即记忆，控制权还给用户 |
| **记忆本身是 agent** | MemU | 从"agent 有记忆"到"记忆是 agent" |
| **记忆是 OS 资源** | MeMOS | 内核调度、页面置换、进程管理 |
| **agent 应该学习** | Hindsight | 不止记住，而是后台整合和学习 |
| **数字孪生** | Second-Me | 训练第二个你，而非做更好的助手 |
| **学会使用记忆** | MetaMem | 问题不在记了什么，在于会不会用 |

### 问题域覆盖

```
                    存储    提取    检索    注入    衰减/遗忘  治理
Claude Code          MD      fork    LLM     双通道    文字      guardrail
OpenClaw             SQLite  flush   hybrid  tool      exp       -
Hermes               MD+SQL  tool    HRR     freeze    trust     -
Hindsight            PG+vec  LLM     4路RRF  -         -         tenant
Letta                PG+git  agent   embed   in-ctx    summary   git audit
Mem0                 vec+SQL LLM     hybrid  -         -         scope
MeMOS                Neo4j   2阶段   tree+vec sys-prompt FIFO      暂存+RBAC
MetaMem              Qdrant  -       embed   策略注入  -         -
OpenViking           vec+FS  LLM     层级    L0/L1     -         -
MemU                 SQL     bot     漏斗    sidecar   强化计数  -
ReMe                 MD      ReAct   hybrid  hook      -         -
Text2Mem             SQLite  -       hybrid  -         expire    lock
Second-Me            embed   L0→L2   embed   finetune  -         -
```

---

## 二、参考实现详细分析

### 2.1 Claude Code

**存储**：Markdown + YAML frontmatter，每个 memory 一个文件。
`MEMORY.md` 是 index（pointer 列表，限 200 行 / 25KB）。
路径按 git root 隔离：`~/.claude/projects/<sanitized-root>/memory/`。

**写入（双路径互斥）**：
- Path A：主 agent 直写 memory 目录（检测到后跳过 Path B）
- Path B：`extractMemories` forked agent——query loop 结束时 fork，
  共享 parent prompt cache，最多 5 turn，工具限 Read/Grep/Glob +
  memory 目录的 Edit/Write

**检索**：LLM-based（Sonnet）。`scanMemoryFiles()` 读所有文件的
frontmatter（前 30 行），构建 manifest，side query 让 Sonnet 从
description 列表中选至多 5 个文件名。无 embedding/BM25。

**注入（双通道）**：
- Channel A：`MEMORY.md` 内容作为 `systemPromptSection('memory')`
  常驻 system prompt，session 级缓存
- Channel B：`findRelevantMemories()` per-turn 选出相关文件，
  作为 `<system-reminder>` user message 注入，带 staleness caveat

**后台整理**：AutoDream——24h + 5 session 门槛触发，读 session
transcript 合并为 topic memory。

**优点**：
1. 双通道兼顾全局概览和按需精准
2. Forked agent 共享 prompt cache，提取成本极低
3. Staleness caveat + verify-before-act
4. 主 agent 直写 + 后台提取互斥，不重复
5. PermissionEngine 硬编码 guardrail
6. AutoDream 跨 session 合并

**缺点**：
1. LLM-only 检索，200+ 文件退化，成本高
2. 无结构化 relevance score，binary 选或不选
3. 无衰减模型，旧 memory 同权竞争 top-N
4. 无真正的 global vs project 分层

---

### 2.2 OpenClaw

**存储**：Markdown 文件 + SQLite 索引（FTS5 + sqlite-vec）。
per-agent 一个 `.sqlite` 文件。`MEMORY.md` 是长期记忆，
`memory/YYYY-MM-DD.md` 是日增日志。

**写入**：LLM 在 flush turn 写入——pre-compaction flush，
context 压缩前触发一个 `NO_REPLY` silent sub-turn。

**检索（Hybrid）**：
- Vector（cosine，权重 0.7）+ BM25（FTS5，权重 0.3）
- 4x 候选过采样 → 融合 → 可选 MMR re-ranking（Jaccard diversity）
- 可选 temporal decay：`exp(-λ * age_days)`，半衰期 30 天
- minScore 阈值 0.35

**注入**：Tool-based——`memory_search` + `memory_get`，LLM 需主动调用。
System prompt 只加一段 `## Memory Recall` 引导。

**优点**：
1. Hybrid search 比纯 LLM 或纯 keyword 更稳健
2. MMR 保证结果多样性
3. 指数时间衰减 + evergreen 豁免
4. Pre-compaction flush 时机精准
5. 原子 reindex（temp DB → rename swap）
6. Embedding cache（SHA-256 key）

**缺点**：
1. Tool-based 读取，LLM 容易忘记主动搜索
2. 需要 embedding provider + sqlite-vec + FTS5
3. 只在 flush turn 提取，短 session 可能不触发
4. Agent-scoped 而非 project-scoped

---

### 2.3 Hermes Agent

**存储**：Built-in 用 Markdown（`MEMORY.md` + `USER.md`，
`\n§\n` 分隔，char budget 2200/1375）。Holographic plugin 用
SQLite（facts + entities + HRR vectors）。

**写入**：专用 `memory` tool（add/replace/remove）+
`_scan_memory_content` 安全扫描 + atomic `os.replace()` +
`fcntl.flock` 文件锁。Holographic 有 auto_extract（regex
匹配 "I prefer/like/use..."）。

**检索**：
- Built-in：无 scoring，按插入顺序
- Holographic：`FTS5(0.4) + Jaccard(0.3) + HRR(0.3)`，
  乘以 trust_score，可选 temporal decay

**HRR 代数操作**：
- `probe(entity)`：解绑向量，找关联事实
- `reason([e1, e2])`：多实体 AND 查询（向量空间 JOIN）
- `contradict()`：高实体重叠 + 低内容相似 → 矛盾检测

**注入（双层）**：
- System prompt：session 开始时冻结快照，mid-session 不更新（KV cache 稳定）
- Per-turn：`<memory-context>` fence 注入 user message，prefetch-once 缓存整轮

**优点**：
1. 冻结快照 → prefix cache 稳定，推理成本低
2. Fence 注入防混淆
3. Prefetch-once per turn
4. HRR 代数检索（probe/reason/contradict）
5. 信任分非对称更新（+0.05/-0.10）
6. Provider 隔离 + circuit breaker
7. on_pre_compress hook

**缺点**：
1. 无 project scope
2. Built-in 无 scoring
3. Char budget 太小
4. 快照延迟生效（写入当前 session 不可见）

---

## 三、10 个 Memory 架构专项分析

### 3.1 Hindsight — 三层仿生记忆 + Reflect

**解决的问题**：传统向量存储丢失关系和时间上下文，agent 只能"回忆"
不能"学习"。

> 外部评价："让 agent 学习，而不仅仅是记住——这不是口号，
> 它在架构层面做出了真正的区分。"

**架构**：
- 三层记忆，有明确的认知科学映射：
  - World Facts → **语义记忆**（客观世界知识，如"火炉会很烫"）
  - Experience Facts → **情景记忆**（agent 亲身经历，含 what/when/where/who/why）
  - Mental Models → **程序性/专家知识**（反思合成的综合理解）
  - 中间层 Observations：由 consolidation 引擎从原始事实中自动提炼
- 存储：PostgreSQL + pgvector，多租户 schema 隔离
- 摄入：LLM fact extraction（单文件 2097 行）→ spaCy entity resolution → 归类三层
- 检索：4 路并行 → RRF(k=60) → cross-encoder reranking → token budget trim

**检索四路详解**（外部分析补充）：

| 路径 | 技术 | 解决的查询盲区 |
|------|------|--------------|
| Semantic | pgvector | 措辞变化鲁棒，但精确名称弱 |
| BM25 | pg trigram | 精确词汇高精度，但同义词低召回 |
| **MPFP 图检索** | 自研算法 | **隐性关联**——语义不相似但实体相关的记忆 |
| Temporal | 时间维度检索 | 时序推理 |

> **MPFP (MetaPath Forward Push)** 的关键创新（外部分析补充）：
> 4 种 metapath 模式并行——共同实体关联、因果链追溯、语义相似、
> 时间窗口临近。**次线性时间复杂度**，与图规模无关，且遍历过程
> 不需要任何 LLM 调用。例如："Alice 因感冒请假"和"团队会议缺少
> 关键人员"语义相似度很低，向量检索几乎不可能关联，但 MPFP 通过
> 实体链接和时间链接可以强关联。

**Consolidation 引擎**（外部分析补充）：
- retain 完成后异步触发，类比**人类睡眠期间的记忆巩固**
- 后台异步 = 睡眠期间的非干扰性整合
- 新事实与现有 observations 对比 = 海马体与新皮层的知识协商
- Experience Facts → Observations 的转化 = 情景记忆→语义记忆的转化
- 这是"有损重构"哲学——故意丢失细节，换来经过整合的高质量洞察

**Disposition（外部分析补充）**：
三个性格维度（怀疑度/字面主义/共情力，各 1-5 分），同一知识库
配置不同 disposition 产生不同反思结论：
- 高怀疑+低字面+低共情 → 批判分析型 agent
- 低怀疑+高字面+高共情 → 支持陪伴型 agent

**优缺点**：

| 优点 | 缺点 |
|------|------|
| reflect 从记忆推理新洞察 | 每次 retain 都要 LLM fact extraction |
| 4 路 RRF，无单策略盲区 | 核心文件 7674 行，可维护性堪忧 |
| MPFP 次线性图检索 | 依赖 PostgreSQL + pgvector |
| 等权 RRF 无需动态调权仍 SOTA | 默认英文优化，中文 BM25 几乎失效 |
| Disposition 一库多场景 | reflect 需显式调用 |
| LongMemEval SOTA，独立复现 | 中文向量精度下降 40-60% |

**对 DeepCLI 的启发**：
- reflect 思路 → `/memory lint` 增强：不只矛盾检测，还能合成洞察
- 4 路 RRF → Phase 3 hybrid search 参考模型
- Disposition → per-project memory 行为配置
- 等权 RRF 仍 SOTA → **简单方法在信息不充分时更鲁棒**，验证了
  DeepCLI Phase 1 用 LLM scoring 而非复杂融合的合理性

---

### 3.2 Letta (MemGPT) — 操作系统虚拟内存

**解决的问题**：LLM 天生无状态，每次对话从零开始。

> 外部评价："如果 Mem0 是中间件，Letta 就是操作系统。"

**架构**（整合两个视角）：
- **Core Memory**（= RAM）：始终 in-context 的 key-value block，
  每个 block 是三元组 `{label, description, value}`，
  **limit 默认 10 万字符**——这是强制信息压缩约束，逼迫 agent 主动
  做信息蒸馏。Agent 用 `core_memory_append`/`replace` 手术式编辑。
- **Recall Memory**（= 日志系统）：对话历史，context 满时触发
  Summarizer **默认驱逐 30% 消息**。被驱逐的消息写进 recall memory，
  仍可通过 `conversation_search` 检索——**不是真的消失，只是从
  in-context 移到 out-of-context**，这才是真正的虚拟内存无损分层。
- **Archival Memory**（= 磁盘）：无限容量向量存储，agent 主动 insert/search

**Git-backed Memory（外部分析大幅补充）**：
`GateEnabledBlockManager` 把真正的 Git 引入 agent 记忆管理：
- **双存储**：Git 是 source of truth，PostgreSQL 是快速读缓存
- 每次记忆变更 = 一次 git commit（agent ID + 时间戳 + 变更原因）
- 不可变性：内容寻址存储，防止静默损坏
- 完整历史：每次修改都能回溯
- 并发安全：多个 sleeptime agent 可用 git worktree 隔离修改
- 可审计：每个 commit 就是一条审计记录
- memFS：记忆组织为真正的目录树——人格设定/用户偏好/技能经验各一个目录，
  每份记忆是一个独立的 markdown 文件

**Sleeptime Agent（外部分析补充）**：
- 主 agent 只负责推理和回复（低延迟）
- 每 5 步触发一次 sleeptime agent 更新 memory block
- 三个好处：主路径低延迟；后台可用更大 token 预算做深度反思；
  充分利用空闲时间

**优缺点**：

| 优点 | 缺点 |
|------|------|
| Agent-as-memory-manager 范式优雅 | `core_memory_replace` 精确子串匹配脆弱 |
| Sleeptime Agent 真正的离线思考 | Summarization 有损，agent 不知道忘了什么 |
| Git-backed 记忆版本化+审计 | Archival search 纯 embedding，无 hybrid |
| Shared Memory Block 多 agent 协调 | 80+ 依赖包，认知成本高 |
| 虚拟内存无损分层（驱逐≠丢失） | 重度 PostgreSQL 依赖 |

**对 DeepCLI 的启发**：
- Sleeptime Agent → 我们的 AutoDream / 跨 session 合并
- Shared Memory Block → 多 session 间共享的 global memory
- Core Memory "always in-context" → Channel A index 常驻
- **Git-backed 审计** → 我们的 log.md 是轻量版；如果需要真正的
  版本控制，Git 是更彻底的方案
- **10 万字符 limit 作为压缩约束** → 我们的 index.md 200 行限制
  起类似作用

---

### 3.3 Mem0 — 工程化全家桶

**解决的问题**：给任何 AI 应用加一层持久化的事实记忆。

> 外部评价："Mem0 真正强的地方不是某一项黑科技，
> 而是工程化的全家桶。"

**架构**（整合两个视角）：

三层：Memory API → LLM 推理+向量检索逻辑层 → 存储层。
五大工厂模式体现工程成熟度：

| 工厂 | 支持数量 |
|------|---------|
| LLM Factory | 17+ 提供商 |
| Embedder Factory | 11+ 模型 |
| Vector Store Factory | 22+ 种 |
| Graph Store Factory | 4 种 |
| Reranker Factory | 5 种 |

用 `importlib` 动态加载——没装 Pinecone 包也没关系，只要用的不是
Pinecone 就不会挂。

**三种记忆类型（外部分析补充）**：
- **语义记忆**：抽象事实性知识
- **情景记忆**：具体事件
- **程序记忆**：agent 执行的完整步骤，要求**逐字保留**——
  使用场景是 agent 崩溃后恢复执行状态

**关键设计细节（外部分析补充）**：

1. **UUID 幻觉处理**：LLM 对长 UUID 处理能力差会幻觉。Mem0 把
   LLM 看到的 memory ID 临时映射成 0/1/2 简单整数，LLM 决策后
   再映射回真实 UUID。
2. **双 Prompt 策略**：两个独立的记忆提取 prompt——
   `User Memory Extraction Prompt`（只看用户消息）和
   `Agent Memory Extraction Prompt`（只看助手消息）。
   职责分离防止 AI 的自我表达污染用户记忆，同时允许 AI 积累
   "我是什么样的 AI"的自我认知。
3. **双存储并行**：向量存储（语义相似搜索）+ 图存储（关系推理），
   用 `ThreadPoolExecutor` 并行跑，结果合并返回。
4. **多层级作用域隔离**：user_id / agent_id / run_id 三层隔离，
   直接支持多用户多 agent 会话隔离。

**成本瓶颈（外部分析补充）**：
每次 add 调用完整模式下会调用 2-5 次 LLM。更麻烦的是，
随着用户历史记忆增多，token 数随记忆规模**线性增长**。
不适合高频实时写入，更适合用户维度、对话粒度的中低频写入
（AI 助手、客服、健康追踪）。

**优缺点**：

| 优点 | 缺点 |
|------|------|
| ADD-only 消除脆弱的 UPDATE/DELETE | 过时事实堆积，只增不删 |
| Entity linking 独立检索信号 | spaCy 依赖静默降级 |
| UUID→整数映射解决幻觉 | token 数随记忆线性增长 |
| 双 prompt 防用户/agent 记忆污染 | 不适合高频实时写入 |
| 20+ 后端，极强可插拔性 | 旧数据对 BM25 不可见 |

**对 DeepCLI 的启发**：
- ADD-only 提取的简洁性（但需补矛盾/过时检测）
- **UUID 幻觉处理** → 如果 memory 文件名过长，selector prompt 中
  可用短 alias
- **双 prompt 策略** → 后台提取时区分用户发言 vs agent 发言，
  避免 agent 自我表达污染用户记忆
- Entity boost → Phase 3 可考虑
- Sigmoid 归一化 BM25 → hybrid search 融合参考

---

### 3.4 MeMOS — 记忆操作系统

> MeMOS 全称 Memory Operating System (MemOS 2.0 "Stardust")，
> 来自 MemTensor + 上海交大/浙大，39 位作者署名（arXiv 2507.03724）。
> 代码：`memory/MemOS/`，Python，Apache 2.0。

**解决的问题**：现有记忆系统只是带检索功能的存储库，解决了"放在哪里"，
但没解决"由谁管理、怎么调度、怎么演化"。

> 外部评价："记忆应该是操作系统的一等公民资源。"

**架构**（6 层 OS 映射，代码验证）：

| MeMOS 层 | 对应 OS 概念 | 代码实现 |
|---------|-------------|---------|
| API 接口层 | System call | `src/memos/api/` — FastAPI + OpenAI 兼容路由 |
| MOS Core | 内核 | `mem_os/core.py:38` — `MOSCore` 类，管理 MemCube dict |
| MemSchedule | 进程调度器 | `mem_scheduler/` — 异步 dispatcher + workers |
| MeCube | 容器 | `mem_cube/general.py` — 4 slot 容器（text/act/para/pref） |
| Memory 存储层 | 文件系统 | `memories/` — textual / activation / parametric |
| 基础设施层 | 硬件 | Neo4j + Qdrant/Milvus + Redis + MySQL + RabbitMQ |

`MOSCore` 持有 `dict[cube_id, GeneralMemCube]`。每个 MemCube
包含 4 个可选 memory slot。多用户通过 SQLite-backed `UserManager`
做 RBAC——用户 own 或 share cube。`search()` 可跨所有可访问 cube
并行查询（`ContextThreadPoolExecutor(max_workers=2)`）。

**三类记忆体系**（代码验证）：

| 类型 | 实现状态 | 代码位置 | 说明 |
|------|---------|---------|------|
| **文本记忆** | 完整实现 | `memories/textual/tree.py` | Neo4j 图存储，4 层树形结构 |
| **激活记忆** | 已实现（仅 HuggingFace） | `memories/activation/kv.py` | `DynamicCache` pickle 存储，`_concat_caches` 逐层 `torch.cat` 合并 KV tensor。API-only LLM 用户会得到 runtime error |
| **参数记忆** | **确认占位符** | `memories/parametric/lora.py:1-6` | 文件头注释 `"This file currently serves as a placeholder"`，`dump()` 写 `b"Placeholder"` |

**树形记忆详解**（代码验证 + 外部分析）：

| 层 | 默认上限 | 代码位置 | 说明 |
|----|---------|---------|------|
| WorkingMemory | 20 | `manager.py:76` | 临时暂存区（FIFO 淘汰）|
| LongTermMemory | 1500 | config | 持久存储 |
| UserMemory | 480 | config | 主观/偏好事实 |
| RawFileMemory | 1500 | config | 原始文档分块（与摘要分离）|

每个 Neo4j 节点字段：`id, memory(text), metadata{embedding, memory_type,
status, tags, key, sources, background, confidence, type}`。
边类型：`MATERIAL, SUMMARY, FOLLOWING, PRECEDING`（文档溯源链）。

**Memory Reader vs Memory Manager — 最关键的架构创新**：

```
对话 → Memory Reader (LLM) → 分类+提取 → Memory Manager (执行) → Neo4j
         ↑ 决策层                              ↑ 执行层
```

- **Memory Reader** (`SimpleStructMemReader`)：LLM 驱动的提取流水线，
  决定 memory_type / tags / key / confidence。两种模式：
  - `fast`：无 LLM 调用，原始文本直接存为 `mode:fast` 节点
  - `fine`：LLM 生成结构化 JSON，每条分类并提取
- **Memory Manager** (`organize/manager.py`)：纯执行引擎，写入 Neo4j，
  管理 tier cap，触发 reorganization。**不做任何 LLM 调用**。

**两阶段异步写入**（代码验证的关键创新）：

```
用户消息 → fast 写入（ms 级延迟）→ WorkingMemory 暂存
                                       ↓ (async)
              scheduler picks up → LLM fine 重处理 → 精炼节点写入 + 原始删除
```

- `_add_memories_batch` (manager.py:138)：**所有 item 先写为
  WorkingMemory**（不管最终 tier），同时写入目标 tier
- `working_binding:<uuid>` 嵌入 `metadata.background`，让异步
  cleaner 找到并删除临时 working 节点
- 保证 API 延迟在毫秒级，LLM 精炼在后台完成

**Graph 重组器**（代码验证）：
`GraphStructureReorganizer` 在后台线程运行，用余弦相似度
合并语义相近节点（阈值 0.80，合并阈值 0.92）。

**幻觉过滤**（代码发现的新细节）：
`filter_hallucination_in_memories()` (simple_struct.py:581)——
可选的二次 LLM pass（env `SIMPLE_STRUCT_ADD_FILTER=true`），
检测并丢弃与源对话矛盾的提取记忆。

**偏好记忆**（代码发现的新细节）：
单独的 `pref_mem` slot，区分 `objective_memory`（人口统计事实）
和 `subjective_memory`（情绪、回复风格），24 个预定义 key
(mem_reader/memory.py:83-110)。

**MemSchedule 调度引擎**（代码验证）：
- 内部队列：Python `Queue`（进程内）或 `SchedulerRedisQueue`（Redis Streams）
- RabbitMQ：可选，用于跨进程消息广播
- 线程池：默认上限 50 worker（非论文所说的 30，那是示例配置）
- 5 种任务类型：`ADD, MEM_READ, QUERY, ANSWER, PREF_ADD`
- `SchedulerDispatcher` 批量消费（`consume_batch=3`, interval=0.01s）

**检索流水线**（代码验证）：
`TreeTextMemory.search()` → `AdvancedSearcher` 串行执行：
`TaskGoalParser → MemoryPathResolver → GraphMemoryRetriever →
MemoryReranker → MemoryReasoner`。
支持：向量相似度、BM25（可选）、互联网检索（BochaSearch/Tavily）。
`fine` 模式调 LLM 对候选做上下文推理。

**注入**：`MOSCore.chat()` 调 `text_mem.search()`，结果格式化为
编号列表追加到 system prompt (core.py:354-388)。

**性能**（代码中有完整评测脚本 `evaluation/scripts/`）：

| 基准 | 结果 |
|------|------|
| LoCoMo | 75.80 |
| LongMemEval | +40.43% vs OpenAI Memory |
| PrefEval-10 | +2568%（基线极低） |
| PersonaMem | +40.75% |
| Token 节省 | 35.24% reduction |

**优缺点**：

| 优点 | 缺点 |
|------|------|
| 三类记忆框架最全面 | LoRA 记忆确认占位符 |
| 两阶段异步写入（ms 延迟 + 后台精炼） | 需要 Neo4j + Qdrant + Redis + RabbitMQ + MySQL 五套基础设施 |
| 幻觉过滤（二次 LLM 检验提取结果） | 激活记忆仅支持 HuggingFace，API 用户不可用 |
| Graph 重组器自动去重合并 | Memory Reader 决策不在 Manager 层 |
| 完善的评测体系（5 个基准） | 偏好记忆 24 key 硬编码 |
| WorkingMemory 作为暂存区的双写模式 | |
| 偏好记忆区分客观/主观 | |

**对 DeepCLI 的启发**：
- **两阶段异步写入**最有价值——fast write 保证 API 延迟，
  后台 LLM 精炼保证质量。我们的"主 agent 直写 + 后台 fork 提取"
  是同一思路的不同实现
- **幻觉过滤** → 后台提取时可加一轮验证，检查提取的 memory
  是否与源对话矛盾
- **WorkingMemory 作为暂存区** → 所有 item 先进暂存再决定去向，
  这比直接写入目标 tier 更安全（可以在暂存阶段去重/合并）
- **激活记忆 (KV cache)** 概念独特——高频记忆预编码为 cache，
  跳过重复 prefill。与我们的 "hot cache" 方向一致，但 MeMOS
  走得更远（直接到 KV cache 层），Phase 3+ 远期参考
- **偏好记忆区分客观/主观** → 我们的 user type memory 可考虑
  细分为 fact（客观）和 preference（主观）
- **"记忆层次由 LLM 判断，Manager 只执行"** → 验证了我们让 LLM
  在 prompt 中决定 memory type 的设计

---

### 3.5 Text2Mem — Memory 操作的类型化 IR

**解决的问题**：自然语言 memory 指令不精确，操作不可移植。

> 外部评价："它不是在做一个记忆系统，而是在给所有记忆系统
> 定义一套通用的操作语言。类比 CPU 的指令集架构。"

**架构**：
- JSON IR 五元结构：`{stage, op, target, args, meta}`
- 3 阶段 × 12 标准动词

| 阶段 | 操作 | 说明 |
|------|------|------|
| ENC | encode | 记忆诞生 |
| RET | retrieve, summarize | 取回 vs LLM 摘要（**刻意分开**） |
| STO | update, label, promote, demote, merge, split, delete, lock, expire | 生命周期管理 |

**retrieve vs summarize 分离的工程意义**（外部分析补充）：
retrieve 几十 ms 返回，summarize 背后是一次完整 LLM 推理（延迟
上千倍）。分成两个操作，执行引擎可以用完全不同的超时、缓存、
降级策略。

**安全设计**（外部分析补充）：
- meta 中的 `dry_run` 和 `confirmation` 两个安全字段
- 极端场景：LLM 抽风生成"把所有记忆全部删除"——Text2Mem 卡一道硬性规则：
  要么先用 dry_run 模拟一次，要么显式把 confirmation 设为 true，二选一
- 双层验证：外层查格式（JSON Schema），里层查业务逻辑（Pydantic 拦截，
  如 promote 的绝对值和相对值必须恰好有一个为空）
- **"不信任 LLM，但能兜住 LLM"** 的设计哲学

**Lock 操作详解**（外部分析补充）：
四种模式——read_only（完全冻结）、no_delete（禁删）、
append_only（只允许追加）、custom（完全可编程）。
最精妙的是 **review 机制**——本质上是 RBAC 风格的锁绕过。

**优缺点**：

| 优点 | 缺点 |
|------|------|
| Lock 语义最完备 | SQLite-only，O(n) 检索 |
| retrieve/summarize 分离 | REST API adapter 未实现 |
| dry_run + confirmation 安全阀 | NL→IR 翻译路径未定义 |
| 双层验证（Schema + Pydantic） | confirmation 标记可被客户端绕过 |
| Lineage 审计链 | |

**对 DeepCLI 的启发**：
- **"不信任 LLM 但能兜住 LLM"** → 我们的 memory tool 也应该有
  类似的安全阀（destructive op 需 confirmation）
- retrieve/summarize 分离 → memory_list（快，无 LLM）vs
  memory_search（慢，LLM scoring）
- Lock 语义 → `locked: true` frontmatter
- Lineage → log.md 审计

---

### 3.6 Mem0 已在 3.3 节整合

---

### 3.7 MemU — 记忆本身是一个 Agent

**解决的问题**：长运行 agent 的 token 成本（重放全历史太贵）。

> 外部评价："从'agent 有记忆'到'记忆本身是一个 agent'。
> 前面所有项目的记忆都是被使用的对象，MemU 把这个关系反过来。"

**架构**：
- 三层存储：Resource（原始素材）→ MemoryItem（原子事实）
  → MemoryCategory（主题摘要）
- 文件系统隐喻（**与 ReMe 不同**）：
  - ReMe：给人看的透明文本（用户主权）
  - MemU：bot 自己维护的结构化目录树（bot 自治）
  - 文件夹=category，文件=item，符号链接=交叉引用，挂载点=resource
- 双 agent 架构：Main Agent（听用户、调工具、回复）+
  MemU Bot（只负责记忆，持续盯着每次交互，后台提取整理分类）。
  实现：`asyncio.create_task` 起异步任务，共享 `conversation_messages` 列表
- 7 阶段检索漏斗 + sufficiency check 可提前退出

**显著性感知记忆（外部分析补充）**：
V1.4 引入 `salience-aware memory`——每条 MemoryItem 带
`reinforcement_count` 计数器，每次被检索+1，下次排序时加权。
越常用的记忆越容易被再次召回。**模拟人类的肌肉记忆**。

**性能**（外部分析补充）：
在 LoCoMo 基准上跨所有推理任务平均准确率 91.2%——
这正是 Mem0 当年用来打 OpenAI Memory 的同一个基准。

**优缺点**：

| 优点 | 缺点 |
|------|------|
| Sufficiency check 提前退出 | dedupe_merge 空实现 |
| 显著性感知（强化计数）| SQLite 向量暴力 cosine |
| 声称 token 成本降至 1/10 | Workflow state 无类型安全 |
| Pipeline 步骤可运行时插拔 | 双 agent 翻倍基础设施 |

**对 DeepCLI 的启发**：
- **显著性感知 / reinforcement_count** → 这就是我们 frontmatter
  `access_count` 的设计来源，验证了 hot cache 按访问频率的思路
- Sufficiency check → relevance selector 可在候选集明显不够时
  跳过 LLM scoring
- 三层存储（raw → atomic → summary）→ index description 是 L0 摘要

---

### 3.8 ReMe — Memory-as-Files + ReAct Agent 写入

**解决的问题**：context window 截断 + 跨 session 无状态。

> 外部评价："ReMe 最核心的理念——文件即记忆。记忆直接存成 markdown，
> 打开就能看见，可以直接编辑，可以 git 版本控制。
> 这把记忆的控制权和透明度还给了用户。"

**架构**：
- Markdown 文件存储：`MEMORY.md`（长期）+
  `memory/YYYY-MM-DD.md`（日志）+
  `dialog/YYYY-MM-DD.jsonl`（压缩对话）+
  `tool_result/<uuid>.txt`（TTL 缓存 tool 输出）
- 两套系统时间维度错开：
  - ReMe Light（文件记忆）→ 短期工作记忆
  - ReMe 本体（向量记忆）→ 长期语义记忆
- `pre_reasoning_hook` 链：compact_tool_result → check_context →
  compact_memory → summary_memory（async）
- 写入：ReAct Agent + file tools 自主决定写什么写哪里
- 检索：Hybrid（vector 0.7 + BM25 0.3）+ FileWatcher 实时索引

**Delta FileWatcher（外部分析补充）**：
记忆文件累积到几十 KB 后，每次变更都重新处理整个文件是对
embedding API 的巨大浪费。ReMe 先检测文件是不是纯追加模式，
如果是就只处理新增部分，**节省 92% 的 API 调用**。

**when_to_use 与 content 分离（外部分析补充）**：
向量嵌入建在 `when_to_use` 字段上而不是 `content` 上，
因为用户查询和 when_to_use 天然语义接近，大幅提升召回率。

**优缺点**：

| 优点 | 缺点 |
|------|------|
| LoCoMo SOTA（86.23） | ReAct agent 写入有 LLM 成本 |
| 文件存储可审计可移植可人读 | 仍需 embedding model |
| Async summary 不阻塞主推理 | Markdown 不适合高频并发写入 |
| Delta watcher 省 92% API | Benchmark 依赖 LLM-as-Judge |
| when_to_use 嵌入提升召回 | |

**对 DeepCLI 的启发**：
- **when_to_use vs content 分离** → 我们的 `description` 字段
  就是 when_to_use，验证了"embedding/scoring 建在 description 上
  而非 content 上"的设计
- Delta FileWatcher → Phase 2 如果加 embedding 索引，增量更新很重要
- `pre_reasoning_hook` 链 → query 前 prefetch 可扩展为多步
- Tool result 分级压缩 → 配合 D15 compaction

---

### 3.9 Second-Me — 本地微调的 AI 分身

**解决的问题**：通用 LLM 缺乏个人化身份。

> 外部评价："不是做更好的助手，而是训练第二个你。
> Me-Alignment 优先向你，而非向完美——
> 这和 RLHF 的 H 原则存在根本性张力。"

**关键实验（外部分析补充）**：
论文中的 "haystack in the needle" 推理实证表明，同时从长上下文中
检索相关信息并执行推理**几乎是不可能的任务**。现有 LLM 的有效上下文
长度远小于声称的上下文长度。所以需要专门的个人化记忆架构，
而非简单地把所有东西塞进 context window。

**架构**：
- L0：文档处理（情景记忆）——所有 prompt 注入用户 bio 上下文，
  从"了解你的老朋友"视角生成洞察（不是通用摘要，是个性化洞察）
- L1：Shade 建模（语义记忆）——embedding 聚类 → LLM 合成人格面向
  （第三人称+第一人称双视角），高度相似 shade 自动合并为超级 shade
- L2：训练数据生成（程序记忆）→ LoRA 微调（r=64, alpha=16,
  覆盖 q/k/v/o/down/up/gate 七层）→ GGUF 部署

**"100% 本地"的事实澄清（外部分析补充）**：
推理阶段确实 100% 本地。但**训练数据准备阶段必须调用外部 API**
（L0 洞察、L1 bio、SelfQA/Preference 数据生成都需要 OpenAI/DeepSeek）。
本质是一次性的知识蒸馏过程。
Network 功能宣传为去中心化，但实际所有实例注册到中心化服务器，
未实现 DHT 或 P2P 协议，实际去中心化程度约 20%。

**优缺点**：

| 优点 | 缺点 |
|------|------|
| 推理阶段真正 local-first（GGUF）| L0/L1 合成依赖外部 LLM |
| 双视角表示（客观+主观） | 小模型(0.5B-3.5B)推理质量有限 |
| 时间线追踪用户变化 | 更新需重跑训练，无增量更新 |
| 属性信心等级防虚假推断 | 推荐 16GB+ 内存，Mac Docker 无法用 Metal GPU |
| Me-Alignment：向你而非向完美 | 训练后无法删除特定记忆 |

**对 DeepCLI 的启发**：
- 信心等级 → frontmatter `confidence` 字段
- 时间线 → feedback 类型追踪变化历史
- **"有效上下文远小于声称上下文"** → 验证了不能靠塞 context 解决
  记忆问题，必须做 relevance selection

---

### 3.10 MetaMem — 学习"怎么用记忆"的策略

**解决的问题**：LLM 检索到记忆碎片后仍然用不好——缺乏使用策略。

> 外部评价："问题不在于记了什么，而在于你根本不会用你记住的东西。
> 如果 MetaMem 方向是对的，那现在这么多项目拼命优化存储和检索，
> 是不是从一开始就把力气花错了地方？"

**架构**：
- 底层用 LightMem（LLMLingua-2 压缩 + Qdrant 向量）
- Meta-memory 是 `{"M0": "rule", ...}` 字典（max 30 词/条）
- 四阶段训练流水线：
  1. Response Sampling：同一问题 5 次随机采样
  2. Self Reflection：分析推理轨迹，识别成功/失败关键因素
  3. Meta-memory Learning：比较成功 vs 失败，提取可泛化原则
  4. Meta-memory Evolution：汇总批次内所有提案，解决冲突，合并相似

**Partial Correctness Filter 的深层原理（外部分析大幅补充）**：
- 只处理平均奖励 0 < r < 1 的样本（有时对有时错）
- 全对 → 问题太简单或策略已够好，无学习价值
- 全错 → 系统性能力不足，策略优化无法改变结构性失败
- 部分正确 → 信息量最大：同一问题、同一检索结果、同一策略，
  分界线在于推理路径的选择——**这正是策略可以介入优化的地方**
- 这与强化学习中的策略梯度方法高度一致：只有奖励分散的样本
  才能产生较大的梯度信号。Partial correctness filter 是
  **这一原理在符号空间的等价实现**

**MAML 对应关系（外部分析补充）**：
- MAML 内层循环 = 在当前 meta-memory 下采样多次回答
- MAML 外层循环 = 基于批次内多个问题的反思结果更新 meta-memory
- MAML 用梯度反向传播，MetaMem 用自然语言规则操作——符号化替代

**跨领域迁移（外部分析补充）**：
在完全不相关的 HiResGPT 日常对话上训练的 meta-memory，
400 个样本就能超越领域特定策略。领域无关性超出预期。

**训练成本（外部分析补充）**：
至少 6 张高端 GPU，约 200GB 显存，350 个训练样本跑 5 epoch
大约需要 3 万次 API 调用。

**优缺点**：

| 优点 | 缺点 |
|------|------|
| 不需微调，任何冻结 LLM 适用 | 推理时需 30B+235B 双模型 |
| 策略可泛化、人类可读可编辑 | 3 万次 API 调用训练成本 |
| 跨领域迁移能力强 | 底层检索没捞到则策略无用 |
| 推理时仅 200-500 token 额外注入 | 无收敛保证 |
| 符号化替代 MAML 梯度 | |

**在生态中的位置**（外部分析总结）：
MetaMem 占据正交位置——不负责存储和检索，只负责优化使用。
作为可插拔策略层，可叠加到任何 RAG 系统之上。代表的是
**从"更好的记忆"到"更好的记忆使用"的认知层次跃迁**。

**对 DeepCLI 的启发**：
- 最有启发性的项目。我们可以把 meta-memory 思路融入 prompt 设计：
  在 `selection.txt` 和 `extraction.txt` 中嵌入策略规则，
  且这些规则可基于运行经验迭代更新
- 但不需要 MetaMem 那样复杂的双模型循环——规则由用户或
  `/memory lint` 产出即可
- **"推理时仅 200-500 token"** → 我们注入 meta-memory 策略
  可以作为独立 memory 使用策略 section，cost 极低

---

## 四、横向对比矩阵

### 4.1 存储层

| 项目 | 格式 | 后端 | 可人读 | 可移植 |
|------|------|------|--------|--------|
| Claude Code | MD + YAML frontmatter | 文件系统 | Y | Y |
| OpenClaw | MD + SQLite(FTS5+vec) | 文件 + DB | 部分 | N |
| Hermes | MD + SQLite(HRR) | 文件 + DB | 部分 | N |
| Hindsight | 结构化 rows | PG + pgvector | N | N |
| Letta | 结构化 rows + git | PG + GCS | N | git 可 |
| Mem0 | 向量 payload | 20+ 后端 | N | 后端相关 |
| MeMOS | Neo4j 图节点 + KV pickle | Neo4j+Qdrant+Redis+MySQL | N | N |
| MemU | 结构化 rows | SQLite/PG | N | N |
| MetaMem | dict + Qdrant | Qdrant | 部分 | N |
| OpenViking | 虚拟 FS + 向量 | 多后端 | 部分 | N |
| ReMe | MD + JSONL | 文件系统 | Y | Y |
| Text2Mem | 结构化 rows | SQLite | N | N |
| Second-Me | embedding chunks | SQLite | N | N |

**结论**：文件系统（MD）存储在可读性和可移植性上有绝对优势。
Claude Code / ReMe 是两个最好的范例。DeepCLI 沿用 MD + YAML
frontmatter 是正确选择。

### 4.2 提取（写入）机制

| 项目 | 方式 | 时机 | 去重策略 |
|------|------|------|---------|
| Claude Code | fork agent / 主 agent 直写 | turn 结束 | prompt 指令 |
| OpenClaw | flush turn | pre-compaction | cosine ≥0.95 |
| Hermes | memory tool | 主 agent 随时 | 精确匹配 + UNIQUE |
| Hindsight | LLM extraction | 每次 retain | entity resolution |
| Letta | agent tool call | 主 agent 随时 + sleeptime 每 5 步 | - |
| Mem0 | LLM ADD-only | 调 add() 时 | MD5 hash + UUID 映射 |
| MeMOS | 2 阶段（fast→fine async） | 实时+后台 | Graph 重组器(cosine 0.92) |
| MemU | MemU Bot (async task) | 持续后台 | 空实现 |
| OpenViking | LLM extraction | session 结束 | LLM skip/merge/delete |
| ReMe | ReAct agent | pre_reasoning_hook | delta watcher |
| Text2Mem | IR dispatch | 显式调用 | - |

**结论**：最佳实践是**多时机互斥提取**——主 agent 随时可写（最即时），
pre-compaction 自动提取（最精准），turn 结束后台 fork（最全面）。
三层覆盖，互斥避免重复。

### 4.3 检索机制

| 项目 | 方法 | 融合 | 多样性 | 衰减 |
|------|------|------|--------|------|
| Claude Code | LLM 选文件名 | - | - | 文字 caveat |
| OpenClaw | Vector + BM25 | 加权 | MMR(Jaccard) | exp(-λt) |
| Hermes(HRR) | FTS + Jaccard + HRR | 加权 | - | trust score |
| Hindsight | Sem+BM25+MPFP+Temporal | RRF(等权) | - | - |
| Letta | Embedding | - | - | summary 压缩 |
| Mem0 | Semantic+BM25+Entity | 加权+boost | - | - |
| MeMOS | Vector+BM25+Graph+Internet | 串行 pipeline | - | FIFO |
| MetaMem | Embedding + 策略注入 | - | - | - |
| OpenViking | 层级向量+分数传播 | best-first | - | - |
| MemU | Embedding / LLM ranking | 漏斗 | - | 强化计数 |
| ReMe | Vector+BM25 | 加权(7:3) | - | - |
| Text2Mem | Cosine+keyword+phrase | 加权 | - | expire op |

**结论**：Hybrid search（至少 Vector + BM25）是行业共识。
LLM-only（Claude Code）是最简方案但扩展性差。
Hindsight 的 4 路 RRF 是最全面的，且等权 RRF 已够 SOTA。
MMR 多样性只有 OpenClaw 做了。

### 4.4 注入机制

| 项目 | 方式 | 自动？ | Cache 友好？ |
|------|------|--------|-------------|
| Claude Code | system prompt + system-reminder | Y | 部分 |
| OpenClaw | LLM 主动调 tool | N | - |
| Hermes | 冻结快照 + fence user msg | Y | Y |
| Letta | Core in-context + tool search | 部分 | Y(core) |
| MeMOS | 编号列表追加 system prompt | Y | N |
| MemU | sidecar 主动注入 | Y | - |
| ReMe | pre_reasoning_hook | Y | - |
| OpenViking | L0/L1 按需加载 | 部分 | Y(L0) |
| MetaMem | 策略规则注入(200-500 token) | Y | Y(极小) |

**结论**：自动注入 >> tool-based。双通道（常驻 + per-turn）是
最佳平衡。Cache 友好需要常驻部分 cacheable + 动态部分分离。
MetaMem 的策略注入成本最低（仅 200-500 token）。

### 4.5 治理与安全

| 项目 | 写入保护 | 注入安全 | 锁/权限 | 审计 |
|------|---------|---------|---------|------|
| Claude Code | PermissionEngine guardrail | - | - | - |
| OpenClaw | - | escape + XML fence | - | - |
| Hermes | scan + atomic write | fence tag | - | - |
| Letta | - | - | shared block | git commit |
| Text2Mem | dry_run + confirmation | - | 4 种锁 + RBAC review | lineage |
| Mem0 | - | UUID→整数映射 | scope(user/agent/run) | SQLite log |
| MeMOS | 幻觉过滤(二次LLM) | - | RBAC(user own/share cube) | - |

**结论**：没有一个项目同时做好所有治理维度。DeepCLI 应该组合：
写入保护（guardrail + scan）+ 注入安全（fence）+
审计（log.md）+ 可选锁（Phase 2 `locked` frontmatter）+
destructive op 需 confirmation（from Text2Mem）。

---

## 五、对 DeepCLI 设计的关键启发

按优先级排序：

### 必须采纳（Phase 1）

| # | 启发 | 来源 | 应用到 |
|---|------|------|--------|
| 1 | 双通道自动注入（index 常驻 + per-turn 相关文件） | Claude Code + Hermes | 注入机制 |
| 2 | MD + YAML frontmatter 文件存储 | Claude Code + ReMe | 存储层 |
| 3 | 结构化 relevance score（1-5 分）而非 binary | 原创（补 CC 短板） | 检索 |
| 4 | 写入安全三件套：guardrail + scan + atomic write | CC + Hermes + OC | 治理 |
| 5 | Staleness caveat + verify-before-act | Claude Code | 注入 |
| 6 | Prefetch-once per turn | Hermes | 性能 |
| 7 | description 作为检索/scoring 目标（≠ content） | ReMe(when_to_use) + OV(L0) | 存储 |

### 应该采纳（Phase 2）

| # | 启发 | 来源 | 应用到 |
|---|------|------|--------|
| 8 | Pre-compaction flush 自动提取 | OpenClaw + ReMe | 提取 |
| 9 | 后台 fork 提取 + 主 agent 直写互斥 | Claude Code | 提取 |
| 10 | BM25 pre-filter 缩候选集 | OC + Mem0 + ReMe | 检索 |
| 11 | L1 overview 段（200-500 token summary in frontmatter） | OpenViking | 存储 |
| 12 | LLM 4 类去重决策（skip/create/merge/delete） | OpenViking | 提取 |
| 13 | `locked` frontmatter 防止自动修改用户创建的 memory | Text2Mem | 治理 |
| 14 | 双 prompt 策略（用户记忆 vs agent 记忆分离提取） | Mem0 | 提取 |
| 15 | Destructive op 需 confirmation / dry_run | Text2Mem | 治理 |
| 16 | 后台提取时幻觉过滤（二次 LLM 验证） | MeMOS | 提取 |

### 可选采纳（Phase 3+）

| # | 启发 | 来源 | 应用到 |
|---|------|------|--------|
| 17 | Hybrid search（Vector + BM25 + 可选 Entity boost） | OC + Mem0 | 检索 |
| 18 | MMR re-ranking | OpenClaw | 检索 |
| 19 | 指数时间衰减 + evergreen 豁免 | OpenClaw | 检索 |
| 20 | Confidence 字段（auto=LOW, user-confirmed=HIGH） | Second-Me | 存储 |
| 21 | Meta-memory 策略规则（200-500 token 注入） | MetaMem | prompt |
| 22 | Reflect 合成（从记忆推理洞察） | Hindsight | `/memory lint` |
| 23 | Sleeptime agent / AutoDream 跨 session 合并 | Letta + CC | 提取 |
| 24 | Sufficiency check 提前退出 | MemU | 检索 |
| 25 | 显著性感知（reinforcement_count / access_count） | MemU | hot cache |
| 26 | 激活记忆（高频 memory 预编码 KV cache） | MeMOS | 性能 |

---

## 六、十大设计哲学总结

综合代码分析和外部评测，10 个项目的本质分歧不在技术细节，
而在对"记忆应该是什么"这个问题的哲学回答：

| # | 哲学 | 代表 | 对 DeepCLI 的意义 |
|---|------|------|-----------------|
| 1 | 记忆是**被动数据库** | 传统 RAG | 我们要超越这个 |
| 2 | 记忆是**操作系统资源** | MeMOS, Letta | 内核级调度方向正确但太重 |
| 3 | 记忆是**文件系统** | OpenViking, MemU | 可观测+可操作，思路好 |
| 4 | 记忆是**人能看见的文件** | ReMe, Claude Code | **DeepCLI 选择这条路** |
| 5 | 记忆是**一个 agent** | MemU | 太激进，但后台 bot 思路值得借鉴 |
| 6 | 记忆是**标准化指令集** | Text2Mem | 太底层，但治理思路最完备 |
| 7 | 记忆是**中间件全家桶** | Mem0 | 工程化极致，但对我们太重 |
| 8 | 记忆是**可学习的知识** | Hindsight | reflect 是终极方向 |
| 9 | 记忆是**你自己** | Second-Me | 太特化，但信心等级有价值 |
| 10 | 记忆的**问题不在记了什么，在于会不会用** | MetaMem | 正交洞察，策略层可叠加 |

**DeepCLI 的定位**：以路线 4（人能看见的文件）为基础，
从路线 8（可学习）和路线 10（策略优化）中汲取长期方向，
用路线 6（治理）的安全机制保驾护航。
