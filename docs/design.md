# DocScope 项目设计文档

> **状态**：Design v0.2（2026-10-24 需求审查确认；DOCS-001 离线数据合同已实现，在线系统能力仍为待实现目标）<br>
> **日期**：2026-10-24<br>
> **项目名称**：DocScope — 面向企业多文档知识分析的 Agentic RAG 系统<br>
> **GitHub 仓库规划**：`N1rv2na/DocScope`<br>
> **用途**：记录项目级需求、范围、架构、评测合同与阶段验收，作为后续 `/to-tickets` 拆分的输入；本文不规定 Issue 级函数或类设计。

长期维护入口：[项目上下文](CONTEXT.md) · [系统架构](ARCHITECTURE.md) · [未决问题](OPEN_QUESTIONS.md) · [架构决策](adr/)。本文保留完整审查依据与阶段验收基线。

## 0. 阅读说明与决策状态

本文是**目标系统的设计**，不是完成情况报告。除特别注明外，本文涉及的系统功能均为待实现目标，不应在 README、简历或 Demo 中宣称已经交付。审查确认的是设计决策，不代表实现或指标已经验证。

- **[已确定]**：前期讨论中达成的方向性决策；`grill-me` 默认予以保留，除非发现关键矛盾或交付风险。
- **[建议默认]**：帮助落地的初步方案或参数；可在需求审查或对应 Issue 中调整。
- **[待审查]**：需要在拆分 Tickets 前澄清的关键问题。
- **[非 MVP]**：当前有意排除的内容，不应被自动扩展进首批任务。

原则：**先做到可运行、可评测、可演示的端到端闭环，再迭代检索、Agent 和记忆；以秋招作品集交付风险最小化为约束。**
本轮审查后没有尚待用户确认的项目级决策；§11 列的是进入对应开发阶段前必须验证或冻结的实施选择。

### 0.1 审查后交付口径 [已确定]

- **M1 基础闭环**是阶段成果：英文文本型 PDF/Markdown → 导入与索引 → Dense 问答 → 可定位引用 → Web 展示与离线检索基线。它不等于秋招展示版已经完成。
- **秋招展示版完成线为 M3**：在基础闭环上完成 BM25 + Dense + RRF、Cross-Encoder Reranking、证据驱动的有界多跳 Agent、拒答、公平对照评测，以及 Web SSE 回答和简要轨迹。
- **会话级 Memory 推迟到 M4**。M3 可以连续提交独立问题，但不把聊天历史当作问答上下文，也不宣称具备多轮记忆。
- **效果结论另行验收**：工程完成不预设优于 Baseline；只有封存测试集的实际结果支持时，才报告提升。

## 1. Project Goals & Non-goals：项目目标与非目标

### 1.1 问题背景

企业技术知识分散在 PDF、Markdown 等多份文档中。复杂问题可能需要跨文档定位事实、比较不同版本资料，并通过多步检索补齐证据。传统单轮 RAG 的一次查询、一次检索、一次生成流程，可能在指代依赖、多跳检索、证据缺失和长对话场景中表现不足。

### 1.2 产品定位 [已确定]

DocScope 是一个**具备 Web 界面的企业多文档知识分析问答系统**，以 Agentic RAG 为核心，提供：

1. 多文档导入与可检索知识索引；
2. BM25 + Dense 混合检索、RRF 融合和 Reranking；
3. 在有界预算内，根据中间证据动态决定下一步检索的多跳 Agentic RAG；
4. 证据溯源、引用展示及证据不足时的拒答；
5. 会话级短期记忆、长对话压缩和上下文相关的查询改写；
6. 基于 MultiHop-RAG 的可复现检索/回答评测与消融分析。

### 1.3 项目目的 [已确定]

- **用户价值**：对复杂跨文档问题给出有出处的回答，而不是仅提供语义相似段落。
- **工程价值**：展示从文档摄取、检索服务、Agent 工作流、记忆管理到 Web 交互和评测的完整工程链路。
- **求职展示**：与 CodeRCA 的 Agent Runtime / Tool Calling / 自动修复形成互补；M3 重点展示 RAG、Agentic Retrieval、Evaluation，M4 后再展示 Memory。

**避免偏离定位**：项目不是复杂企业知识管理平台，也不是为展示框架数量而拼装的技术栈。

## 2. 目标用户与代表性用例

**[已确定] 目标用户与环境**：需要分析技术资料的研究人员、软件工程师或知识工作者；展示版面向单个使用者、本地部署。M3 能力验收限于**英文文档与英文问题**；中文支持为后续迭代，不能仅凭中文 UI 文案宣称双语问答能力。

| 用例 | 用户输入示例 | 预期行为 |
|---|---|---|
| 文档事实问答 | “What was Product A's key metric in 2025?” | 检索证据并回答，附文档/页码/片段引用 |
| 跨文档对比 | “How do Product A and B differ?” | 从多个文档收集并对齐证据，再综合回答 |
| 依赖中间事实的多跳检索 | “Which later projects involved the team behind Technology X?” | 先找团队，再利用新事实查询其他项目 |
| 多轮追问 [M4] | “What about the year before?” | 消解指代和时间上下文，将问题改写为可检索查询 |
| 证据不足 | “Give a definite conclusion about an undocumented feature.” | 明确说明证据不足，不伪造引用 |
| 长对话 [M4] | 多轮讨论多个版本产品 | 保持近期对话和压缩摘要，不无限增长模型输入 |

## 3. Functional Requirements & Scope：功能需求与范围

### 3.1 M1 基础闭环：最早可运行版本 [已确定]

首个可运行版本只要求：

- 导入**英文文本型 PDF 和 Markdown**，完成解析、Chunking、Embedding 与 Dense 索引；提供上传、索引状态、重传替换和删除。重传不产生重复 Chunk；删除后不再可检索。
- 通过 FastAPI 提供知识检索和问答能力，返回稳定的引用标识；
- React Web 页面支持文档导入、单轮提问、回答显示与引用查看。引用应能定位原文页码/章节与片段；来源失效时明确提示。
- 接入 MultiHop-RAG 的语料与问答适配器，至少能运行 Dense Baseline 并输出评测记录；
- 提供本地运行步骤、基础自动化测试和可诊断的导入/问答失败状态。

**阶段边界**：M1 不承诺 Agentic RAG、Hybrid、Memory 或 SSE；它是后续迭代的可验证底座。

### 3.2 秋招展示版：M3 完成线 [已确定]

在 M1 之上依次增加：

- **检索增强**：BM25 + Dense + RRF、Cross-Encoder Reranking；
- **Agentic RAG**：LangGraph 有界多跳检索、Query Planning、Evidence Evaluation、Citation / Abstention；
- **Web 体验**：单轮独立问题、SSE 流式响应、引用查看和简要检索轨迹；
- **评测完成**：多维评测、实际效果与等预算对照、消融、成本记录与误差分析；
- **可复现交付**：至少提供锁定配置与本地运行/评测步骤，能重放一条固定演示路径。

### 3.3 后续迭代 [已确定]

- **M4 Conversation Memory**：Checkpointer、Recent Window、Rolling Summarization、History-aware Query Rewriting、多轮会话和会话切换；配套独立多轮测试集。
- **M5 交付完善**：Docker Compose、完整 README/架构说明与 Demo 复盘；SSE 的更完整体验与 UI 打磨。Docker Compose 是目标交付能力，不是阻塞 M3 Agentic 效果评测的前提。
- **可选后续**：中文文档与问题支持，须另选模型、分词和中文评测样本；文档版本历史只有出现明确需要时再设计。

### 3.4 非目标 [非 MVP]

- Multi-Agent 群体协作；GraphRAG/知识图谱平台；复杂任务委派；
- 跨会话长期语义记忆、用户画像及个性化推荐；
- 多租户、RBAC、SSO、企业权限继承和生产级租户隔离；
- 扫描件 OCR、表格/图片高精度结构化解析、复杂 Office 文档全格式覆盖；
- 实时联网搜索、搜索引擎爬取、企业知识自动同步和大规模异步调度平台；
- 生产级高并发/高可用、分布式微服务、GPU 服务平台；
- 为追求华丽 UI 而开发复杂管理后台。

## 4. System Architecture：总体系统架构

### 4.1 逻辑分层 [已确定]

```text
┌─────────────────────────────────────────────────────────────┐
│ Web Frontend: React + TypeScript + Vite + Tailwind CSS      │
│ Document UI | Chat/SSE | Citations | Trace | M4 Sessions    │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST + SSE
┌───────────────────────────▼─────────────────────────────────┐
│ API Service: FastAPI + Pydantic                             │
│ Documents | Chat Stream | Status | M4 Conversations         │
└───────────────┬──────────────────────────────┬──────────────┘
                │                              │
┌───────────────▼───────────────────┐   ┌──────▼────────────────┐
│ LangGraph Agentic RAG             │   │ Ingestion Pipeline    │
│ M4 Context / Memory               │   │ Parse → Chunk → Embed │
│ M4 History-aware Query Rewriting  │   │ → Index               │
│ Plan → Retrieve → Evaluate ↺      │   └──────┬────────────────┘
│ Answer → Citation / Abstention    │          │
└───────────────┬───────────────────┘          │
                │                              │
┌───────────────▼──────────────────────────────▼───────────────┐
│ Retrieval Engine: Qdrant Dense + BM25 → RRF → Reranker       │
└───────────────────────┬─────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────┐
│ Qdrant: 同批 Chunk 的 Dense 向量、BM25 全文索引与 metadata │
│ SQLite: 文档状态/轻量元数据；M4 会话/Checkpoint 边界待定    │
│ Local Document Storage: 原始文件及解析产物                 │
└─────────────────────────────────────────────────────────────┘

Evaluation（独立离线执行）：MultiHop-RAG + 自建证据依赖/导入场景
  → Retrieval / Answer / Citation / Abstention / Cost Metrics
  → M4 Memory Metrics
  → Baseline Comparison & Ablation
```

### 4.2 设计原则 [已确定]

1. **单 Agent，有界循环**：不额外引入多个协同 Agent；每一跳必须基于当前状态和证据。
2. **检索与决策分层**：Retrieval Engine 负责召回/重排，LangGraph 决定何时、为何、以什么查询检索。
3. **证据与对话记忆分离**：会话摘要用于理解意图，外部文档证据用于支持事实性回答。
4. **历史持久化与工作上下文分离**：保存完整会话并不意味着每轮把完整历史输入模型。
5. **评测先行**：第一版就建立 Baseline 与固定输入，后续优化必须可对照。
6. **可解释边界**：清楚记录 Agent 调用了什么工具、检索了什么证据、为何继续或停止；不要求披露模型内部推理链。
7. **单一 Chunk 身份**：导入、Dense/BM25 检索、Reranking、引用和评测共用稳定的文档与 Chunk 标识；Agent 只消费 Retrieval Engine 返回的证据，不直接操作索引。

## 5. Key Technical Decisions：技术栈与选择依据

| 层次 | 技术 | 状态 | 说明 |
|---|---|---|---|
| 前端 | React + TypeScript + Vite + Tailwind CSS | [已确定] | 轻量 SPA；不引入复杂全栈框架 |
| API | Python + FastAPI + Pydantic | [已确定] | 请求/响应合同、文档和会话接口 |
| 流式通信 | SSE | [已确定] | M3 展示版必须支持文本、阶段与结束/错误事件 |
| Agent 编排 | LangGraph | [已确定] | 有界循环、状态管理、节点编排 |
| 会话持久化 | LangGraph Checkpointer + SQLite | [建议默认] | M4 引入；会话/消息职责边界届时确定 |
| 检索存储 | Qdrant | [已确定] | Dense 向量、原文 payload、BM25 全文索引及 metadata |
| Embedding | SentenceTransformers 体系 | [建议默认] | **具体模型待选**，记录版本与维度 |
| 词法检索 | Qdrant 原生全文 BM25 | [已确定] | 固定支持全文 BM25 的 Qdrant 版本；同一批 Chunk 建索引 |
| 融合算法 | RRF | [已确定] | Dense/BM25 排名融合，具体参数由开发集验证 |
| 重排 | Cross-Encoder Reranker | [已确定] | **具体模型与 Top-N 待测** |
| 评测 | MultiHop-RAG + pytest + 自定义评测代码 | [已确定] | 主 Benchmark + 单元/集成测试 |
| 部署 | Docker + Docker Compose | [已确定] | M5 完善本地可复现启动；M3 提供本地运行步骤 |
| LLM | 远程 API | [已确定] | 总调用费用上限 100 元人民币；具体模型/API 合同在 M0 验证 |
| Embedding/Reranker | 本地推理 | [已确定] | 具体模型、版本与 CPU 时延在 M0/M2 验证 |

技术选型要服务于评测和演示。LangGraph 是 DocScope 的目标框架；**不应为了增加简历关键词而把整个项目强行绑定多余的 LangChain 抽象**。
Qdrant 的全文 BM25 评分从 v1.20.0 提供；M0 要固定服务端与客户端版本并做能力检查。若固定版本不支持该路径，应记录兼容性问题并重新审查，不在 M2 临时引入第二套索引。参见 [Qdrant BM25 索引文档](https://qdrant.tech/documentation/manage-data/indexing/) 与 [Hybrid 查询文档](https://qdrant.tech/documentation/search/text-search/hybrid-search/)。

## 6. 核心模块详细设计

### 6.1 Document Ingestion：文档导入、分块与索引

**输入**：本地上传的英文文本型 PDF、Markdown，以及 MultiHop-RAG 的英文新闻文档语料。<br>
**输出**：可检索的 Chunk、Embedding、索引及可回溯元数据。

**[建议默认] 流程**：

```text
Input Document → Format Parsing → Text Normalization
               → Chunking → Metadata Association
               → Embedding → Index Upsert
```

最低元数据字段建议包括：

- `document_id`：稳定文档标识；
- `document_title` / `source_type` / `source_path`；
- `chunk_id`：稳定片段标识；
- `page_number`（PDF 可用时）、`section`（Markdown 可用时）；
- `chunk_text`、`content_hash`、`index_version`。

**[建议默认]** M1 先用可复现的长度/重叠分块，保留原文片段及来源位置；更复杂的标题/段落感知策略只在开发集显示需要时尝试。Chunk 边界不得使引用失去原文定位。扫描件、表格和图片不做 OCR 或高精度解析。

**[已确定] 生命周期合同**：M1 支持上传、状态查看、同一文档重传替换和删除；“更新”即替换现有索引，不保留版本历史。应使重传幂等、删除后不可检索；解析/Embedding/索引任一步失败都返回可诊断状态，不把未完成导入显示为可用。引用源文件被删除或替换后，UI 明确显示其已失效，不能静默跳到其他内容。文档身份、Chunk 身份及源位置需稳定且可追溯。

**验收方向**：固定 PDF/Markdown 样例能导入并定位引用；重复导入不增加重复 Chunk；替换后旧内容不参与检索；删除后不再返回相应 Chunk；失败可恢复或重试。具体原子替换机制留到 Ingestion Issue。

### 6.2 Retrieval Engine：Hybrid Retrieval + Reranking

**[已确定] 目标链路**：

```text
Normalized Query
  ├─ Dense Retrieval ─┐
  └─ BM25 Retrieval ──┼→ RRF Fusion → Dedup → Cross-Encoder Rerank
                       └───────────────────────────────────────────→ Top-K Evidence
```

- **Dense** 负责语义相似召回；**BM25** 补充术语、型号、精确字符串匹配。两路均检索 Qdrant 中的同一批 Chunk；
- **RRF** 合并两路排名；Reranker 对候选进行更精细排序；
- 检索结果必须保留 `document_id`、`chunk_id`、来源位置、检索阶段及分数/排名（仅用于分析）；
- 对每次检索限制候选数量、重排数量及最终注入的文本长度；
- 记录 Dense-only、Hybrid、Hybrid+Rerank 三种可切换的实验配置。

**[已确定]** M2 使用 Qdrant 原生全文 BM25 索引和 RRF，不维护独立 BM25 数据库；展示版只对英文检索验收。M1 保存 Chunk 原文 payload，使 M2 能在同一批数据上加建全文索引。**[待 M0/M2 Issue]** 固定 Qdrant 版本、英文分词配置、RRF 参数、Cross-Encoder 模型/Top-N 与 CPU 推理成本。**不得先假定 Hybrid/Rerank 一定改善指标**，以消融结果判断。

### 6.3 Agentic RAG：多跳检索与证据驱动停止

**[已确定]** 使用单 LangGraph Agent；关键在于**第二跳及之后的查询可以由前一跳证据中的新实体/事实决定**，不是仅对原问题一次性批量拆解。

**目标逻辑流程**：

```text
Standalone Question
       ↓
Plan / Identify Missing Facts
       ↓
Retrieve (Hybrid + Rerank)
       ↓
Update Evidence Set
       ↓
Evaluate Sufficiency ── sufficient ─→ Answer + Citations
       │
       ├─ insufficient + budget available ─→ Next Query ─┐
       │                                                  │
       └─ budget exhausted / unanswerable ─→ Abstain      │
                       ▲───────────────────────────────────┘
```

**核心状态建议**：

- 当前独立问题、检索目标、下一次检索 Query；
- 本轮检索历史、已收集 Evidence（包含 provenance）；
- 当前证据覆盖情况 / 缺失事实；
- 已执行轮次、工具预算、停止原因；
- 最终答案与引用。

**[建议默认] 执行约束**：最多 2–3 轮检索（初始建议 3）、每轮候选与最终证据均有上限；遇到重复查询、无新证据、预算耗尽或证据不足时明确停止/拒答。模型输出需经过结构化校验，不能允许无限重试。

**[已确定]** 证据充分性采用**规则约束 + LLM 评估缺失事实**：程序强制轮次/调用/上下文预算、重复查询与无新证据停止、引用 ID 合法性；LLM 评估问题所需事实是否已覆盖，以及下一跳要补什么。第二跳必须能在至少一组经过人工审查的验收样本中，明确利用第一跳发现的新实体或事实。预算耗尽而必要事实仍缺失时拒答，并记录停止原因。具体结构化输出、提示词和阈值在 M3 Issue 中验证。

**引用约束**：输出必须引用实际检索到的 Chunk，引用 ID 由程序管理/校验；答案生成不允许引用不存在的来源。引用存在不等于内容一定正确，需配合评测检查证据是否支持具体结论。

### 6.4 Conversation Memory：多轮问答与长对话 [M4]

**[已确定]** Memory 是 M4 的后续迭代目标，仅实现**会话级 Short-term Memory**，不做跨会话 Long-term Memory 或个人语义记忆库。M3 不消费历史对话，也不提供多轮记忆能力声明。

**推荐处理链路**：

```text
Thread ID → Load Checkpoint / Recent Messages / Rolling Summary
          → History-aware Query Rewriting
          → Agentic RAG Turn
          → Persist Messages / Citations / Turn Outcome
          → Summarize Old History When Threshold Reached
```

- **Checkpointer**：按 `thread_id` 恢复会话状态；
- **Sliding Window**：仅把最近若干轮原始消息加入 Working Context；
- **Rolling Summary**：对较早的对话做增量摘要，记录主题、实体、已讨论范围和未解决问题；
- **History-aware Query Rewriting**：将“它”“之前那个版本”等上下文相关问题改写成独立查询；
- **Evidence separation**：对话摘要不是知识库 Ground Truth，不允许未经重新检索验证即据其给出新的事实性结论。

**[建议默认]** Recent Window 先试 4–6 轮；以 Token 阈值而非单纯轮数触发摘要，并限定摘要长度。每个新 Turn 重置 `retrieval_plan`、`retrieval_round`、`retrieved_evidence` 等暂态，避免跨轮污染。

**持久化边界**：数据库可保留用于 UI 展示的完整消息与来源引用；LLM 每轮仅获得受控 Working Context。用户删除会话时应能删除对应消息和 Checkpoint。

**[待 M4 Issue]** LangGraph SQLite Checkpointer 的具体集成版本、摘要失败与重复请求的恢复语义、是否需要独立消息表，以及 Thread ID 如何管理。进入 M4 前先固定多轮测试集与状态边界。

### 6.5 Web Application：前端与 API

**[已确定]** React 前端与 FastAPI 后端分离，前端只负责交互和可视化，不实现检索或 Agent 决策。

**必需 Web 功能（按阶段）**：

1. M1：文档上传、列表、索引状态、重传替换与删除；单轮问题及可定位引用；
2. M3：SSE 流式回答（含错误和完成事件）、简要检索过程 Trace（轮次、查询、检索完成、引用集合及停止原因）；
3. M4：新建/切换会话、多轮聊天历史和上下文相关追问。

**[建议默认] 页面**：M3 以文档入口、问答、Evidence/Citation/Trace 为主；M4 再加入会话列表与切换。窄屏允许收起侧栏；不为实现三栏样式提前建设无用的会话状态。

**[建议默认] API 草案**（供审查，不冻结路径）：

| 类型 | 示例接口 | 功能 |
|---|---|---|
| REST | `POST /api/documents` | 上传并触发导入 |
| REST | `GET /api/documents` | 文档与索引状态列表 |
| REST | `DELETE /api/documents/{id}` | 删除文档和索引 |
| REST [M4] | `GET /api/conversations` | 会话列表 |
| REST [M4] | `POST /api/conversations` | 创建会话 |
| REST [M4] | `GET /api/conversations/{id}` | 历史消息/引用 |
| SSE | `POST /api/chat/stream`（或等价实现） | 对话流式事件 |

M3 的 SSE 事件至少区分 `stage`、`token`、`citation`、`done`、`error`，并携带 `run_id` 和本轮消息 ID；M4 再加入 `thread_id`。浏览器接入方式（`fetch` 流解析或其他客户端）需与 POST 接口兼容，避免预设 `EventSource` 能直接发送 POST。流中断或模型失败须给出明确终态，不将部分回答误标为成功；重试/幂等的具体协议在 Web Issue 中冻结。

### 6.6 Trace、错误处理和安全边界

**[建议默认]** 为每次问答分配 `run_id`，结构化保存：轮次、查询、检索返回 ID、阶段状态、停止原因、耗时、Token 用量（可获得时）和最终引用。不存储模型私有推理链；敏感凭据、完整用户隐私不进入可公开 Demo/评测产物。

- 上传检查文件格式、大小和路径，防止目录穿越；
- 将外部文档视为**不可信检索内容**，避免文档文本伪装为系统指令；
- LLM 服务凭据仅放后端环境变量，不暴露到前端；
- 严格限制单次检索轮数、模型调用与上下文长度；
- Public GitHub 只能提交可公开数据、脱敏配置与聚合评测结果；
- 本项目目标为本地作品集，并不声称具备生产级安全隔离。

## 7. Evaluation Strategy：数据、指标和实验设计

### 7.1 数据来源 [已确定]

1. **MultiHop-RAG [已确定]**：主要英文多文档检索与问答 Benchmark。公开数据包含文章 `body`、问题、答案、问题类型，以及以文章元数据和 `fact` 表示的支持证据；没有 DocScope 的 `chunk_id`。公开数据页只有一个 `train` 划分，因此由本项目冻结开发集和封存测试集。按项目所有者 2026-10-25 的更新决定，固定上游原始文件纳入本地 Git；数据集标注 ODC-BY，但第三方新闻正文的独立再分发权尚未核清，公开推送前需单独判断。参见 [官方数据卡](https://huggingface.co/datasets/yixuantt/MultiHopRAG)、[官方仓库](https://github.com/yixuantt/MultiHop-RAG)及[数据归属说明](../evaluation/datasets/raw/README.md)。
2. **证据依赖型样本 [已确定]**：从公开或自建材料中人工审查一组问题，明确第一跳的待发现事实、第二跳应利用的新实体/事实及全部支持证据。它专门验证动态查询，而不把“涉及多篇文章”直接等同于“必须迭代检索”。保留逐题轨迹供复核。
3. **自建企业文档样例 [已确定]**：使用**可公开或自创**的英文技术文档验证 PDF/Markdown 导入、页码/章节定位、多文档问答和少量无证据问题；不使用前雇主保密材料。
4. **Multi-turn QA 小集 [M4]**：从公开/自建材料构造追问测试，覆盖指代、省略、切换话题和长上下文；数量由 M4 评测成本确定。

**数据隔离要求 [已确定]**：文章正文可以作为检索语料；参考答案、`evidence_list.fact` 标注、人工结论及不可回答标签仅供离线 Evaluation 使用，不能进入 Agent Prompt、检索索引的答案字段或在线问答接口。M0 按问题类型固定开发/测试划分、种子、样本 ID 与数据版本，并检查相同或高度相似问题跨组重复。开发集用于选型和调参；封存测试集在方案冻结后用于最终报告，不反复调参。官方只有一个公开划分，报告不得称自建测试集为“官方测试集”。

**成本分层 [已确定]**：全部 MultiHop-RAG 问题参与离线检索运行；有标注证据的问题用于 Recall/All-Evidence 指标，`null_query` 无支持证据，不纳入这些指标的分母，而用于拒答评测。Answer、Agent 与消融使用评测前固定的分层子集。先在开发集测量单题 Token、调用量和费用，再按**远程 LLM 总费用不超过 100 元人民币**确定最终子集与调用预算。报告必须标明每组题数、覆盖的问题类型、模型版本与实际费用；不能把子集结果写作全量 Benchmark 成绩。

### 7.2 指标定义

| 维度 | 候选指标 | 特别注意 |
|---|---|---|
| 文章级 Retrieval [M1 起] | Recall@K、All-Evidence Recall@K | 以标注证据所属文章为相关单位；所有必要文章被召回才算 All-Evidence 命中 |
| 片段级 Retrieval [M1 起] | 支持事实覆盖率、All-Fact Coverage@K | 将标注 `fact` 映射至实际注入回答的 Chunk；记录无法映射的样本，抽样人工核查文本匹配，避免把整篇命中当作片段命中 |
| Answer [M1 起] | Exact Match、Token F1、人工/固定规则准确性检查 | 规范化规则与不可回答类型先冻结；短答案自动指标不能单独证明复杂回答正确 |
| Citation [M1 起] | ID 合法率、Support Rate/人工核查 | 引用必须来自实际检索 Chunk，且原文须支持具体结论；二者分别报告 |
| Abstention [M3] | 适当拒答率、错误拒答率 | MultiHop-RAG 的 `null_query` 加自建无证据问题；同时在可回答集检查过度拒答 |
| Memory [M4] | 指代消解/追问回答正确率、历史污染率 | 用独立多轮测试集，不能借用单轮成绩 |
| Efficiency [M1 起] | Latency、Token、LLM Calls、检索轮数、实际费用 | 按题记录，报告分布及预算超限/失败率，而非只给总均值 |

**[已确定]** 官方证据含文章标识信息和 `fact` 文本，需建立来源文章 ↔ `document_id` ↔ `chunk_id` 映射。文章级与片段级指标各自固定分母、K 和评测位置（召回候选 / 重排后 / 最终注入证据），分别报告，禁止混算。无法可靠匹配支持事实的样本单列为映射失败，不能当作模型漏检。

### 7.3 实验对照与消融 [已确定的方向]

保持相同语料切分、Embedding、LLM、评测集、模型参数和可比较的最终证据 Context Budget，按阶段比较：

| 实验组 | 目的 |
|---|---|
| A. Dense-only RAG | 最小 Baseline |
| B. Dense + BM25 + RRF | 检验 Hybrid Retrieval 净增益 |
| C. Hybrid + Cross-Encoder | 检验 Reranking 净增益与延迟代价 |
| D. C + 单轮查询规划 | 一次规划多个查询；不读取中间证据来改写后续查询，用作等预算对照 |
| E. C + 有界迭代多跳 Agent | 后续查询利用前跳证据；检验动态检索是否有效 |

**[已确定] 双口径比较**：一方面报告 A–E 在各自实际运行配置下的效果、延迟、Token、LLM Calls 与费用；另一方面对 D/E 设定相近的最大检索次数、候选数、最终注入证据长度和模型调用预算，以区分动态决策的作用与额外预算的作用。若预算不能完全相等，披露差异而不宣称严格等预算。逐题保存配置版本、检索轨迹、停止原因和结果；不预设提升百分比。Memory 独立对照留到 M4：最新问题-only / 最近 N 轮 / Window + Summary + Query Rewriting。

### 7.4 验收原则 [已确定]

- **功能验收**与**效果评测**分开：功能能跑通不代表指标更优；
- Baseline 可重复运行，保存固定配置、数据切分、错误案例与逐题结果；
- 多跳 Agent 至少存在**真实依赖前跳结果**的测试例，而非只展示并行子查询；
- 引用来自实际检索证据，前端能够定位原始材料；
- M3 的 SSE 流在完成和错误时均给出明确终态；Trace 能显示检索轮次、查询与停止原因；
- M4 Memory 场景验证会话连续性、长历史预算限制和话题切换；
- 最终简历只能使用实际跑出的数据；**不要预填 Recall、准确率或提升幅度**。

**效果门槛 [已确定]**：M3 的工程验收要求能力真实运行、引用/拒答/预算边界正确、对照与误差分析可复现；不要求预先指定提升幅度。若封存测试集没有提升，M3 工程仍可完成，但报告和简历不得声称 Agentic 优于 Baseline；应列出失败类型与后续改进任务。

## 8. Development Milestones & Acceptance Criteria：开发阶段与验收

各阶段须保存可独立运行的交付成果。下表是项目级退出门槛；测试样本、具体阈值和操作步骤在对应 Issue 冻结，不能以未实现的下一阶段能力代替本阶段验收。

| 阶段 | 目标与范围 | 可验证交付与完成标准 |
|---|---|---|
| **M0 设计与评测合同** | 冻结范围、数据和成本边界 | 固定 MultiHop-RAG 版本/来源、开发/测试样本 ID、证据映射与指标定义（包括 `null_query` 的分母排除）；用最小样本验证适配器；确认 Qdrant BM25 能力、远程 LLM/本地模型可调用并记录单题费用；100 元预算内的评测分层方案可执行 |
| **M1 Dense RAG Web** | 英文 PDF/Markdown 摄取、Dense 问答、引用、基础 Web | 固定公开样例跑通“上传 → 索引 → 问答 → 可定位引用”；重传无重复、替换后旧内容不再检索、删除后不再返回；无效格式与导入失败有明确状态；Dense Baseline 能在冻结输入上重复运行并保存逐题结果 |
| **M2 Hybrid + Rerank** | 同一批 Chunk 上的 Qdrant BM25、Dense、RRF 和本地 Cross-Encoder | Dense-only、Hybrid、Hybrid+Rerank 在相同开发集及语料配置下可切换；输出文章级/片段级检索指标、延迟和失败案例；不以指标必须提升作为功能完成条件 |
| **M3 Agentic RAG + SSE（秋招展示版）** | LangGraph 单 Agent、有界动态多跳、证据充分性、拒答、SSE/Trace | 人工审查样本中可复核第二跳对第一跳新事实的依赖；重复/无新证据/预算耗尽时停止并记录原因；引用 ID 合法、必要证据不足时拒答；SSE 正常/错误均有终态且 UI 能查看引用与轨迹；完成实际效果和等预算对照、误差分析、费用记录及固定演示路径 |
| **M4 Conversation Memory（后续）** | Checkpointer、Window、Summary、Rewrite、会话 UI | 多轮追问、话题切换、长历史预算与暂态重置的回归测试通过；删除会话时消息和 Checkpoint 可清理；独立报告 Memory 对照指标 |
| **M5 交付完善（后续）** | Docker Compose、README、架构说明、Demo 复盘及 UI 完善 | 新环境按文档启动和运行评测；公开材料/配置经过检查；成果叙述与实际运行及评测结果一致 |

**范围收敛规则 [已确定]**：M1 是第一个 Walking Skeleton；M3 是秋招展示版完成线。不得以 M4 Memory 或 M5 打磨阻塞 M3；也不得以 M1 的 Dense 闭环宣称 Agentic RAG 已完成。需要缩减工作时，优先压缩样本量和 UI 打磨，并如实报告评测范围；保留 Hybrid、真实动态多跳、引用与公平评测。

## 9. Risks & Trade-offs：风险、取舍和缓解

| 风险 | 影响 | 缓解与需披露的取舍 |
|---|---|---|
| 展示版范围过大，且 M3 加入 SSE | 前后端联调推迟核心评测 | M1/M2 独立可运行；M3 SSE 只暴露必要事件，复杂恢复语义留在 Web Issue；Memory 不进入完成线 |
| Qdrant 全文 BM25 版本或配置不符 | M2 无法按选定路径集成 | M0 固定服务端/客户端版本并做最小 BM25+Dense+RRF 能力检查；英文分词设置留有配置记录 |
| Hybrid/Rerank 收益有限 | 延迟增加，作品集结论不成立 | 固定消融与延迟指标；按结果决定默认配置，不能预设收益 |
| MultiHop-RAG 多文档问题不能证明动态迭代 | Agentic 亮点可能只是并行查询 | 独立证据依赖型样本，审查逐跳查询和新事实来源 |
| 官方只有单一公开划分、标签可能泄漏 | 测试结果偏乐观 | 冻结项目自建开发/测试集，检查重复；答案/证据标签仅进离线评测器 |
| 文章级命中掩盖片段缺失 | Retrieval 成绩高但回答缺证据 | 并报文章级与支持事实片段级指标，人工核查事实映射样本 |
| 远程 LLM 总预算 100 元、本地无可用 GPU | 消融样本有限、CPU Reranking 慢 | 全量离线检索 + 固定分层回答子集；先测单题成本，控制 Top-N；报告样本范围和费用 |
| 多跳决策发散或过度拒答 | 时延/费用失控，正确问题被拒 | 预算、重复/无新证据停止；同时评适当拒答和错误拒答 |
| PDF 提取差异、重传/删除使旧引用失效 | 页码不准或 UI 指向错误材料 | 只支持文本型 PDF，保留页码/原文；引用与索引生命周期一致，失效时明示 |
| M4 摘要失真和历史污染 | 多轮答非所问或编造事实 | 摘要只负责意图，事实重新对照检索证据；暂态每轮重置 |
| Public Repo 的数据安全与新闻语料再分发 | 泄漏凭据/保密材料或许可问题 | 固定上游数据已纳入本地 Git 并注明 ODC-BY；第三方文章正文权利尚待公开推送前单独判断。其他样例仅用公开/自建材料，配置与轨迹脱敏 |

## 10. 推荐的仓库布局 [建议默认]

```text
DocScope/
├── README.md
├── docs/
│   ├── design.md                 # 本文
│   ├── adr/                      # 重要技术决策及取舍
│   └── evaluation-plan.md        # 指标合同细化后可拆出
├── backend/
│   ├── src/docscope/
│   │   ├── api/
│   │   ├── ingestion/
│   │   ├── retrieval/
│   │   ├── agent/
│   │   ├── memory/               # M4 再建立
│   │   └── evaluation/
│   └── tests/
├── frontend/                     # Vite React TS
├── evaluation/
│   ├── configs/
│   ├── datasets/                 # 固定上游数据及归属说明；在线索引不读取标签
│   └── reports/                  # 脱敏、可公开报告
├── docker-compose.yml            # M5
└── .env.example
```

此结构是建议，不是现在必须生成的空目录；M0 不需建立占位代码。Evaluation 依赖 Ingestion 的标准化 Chunk 与 Retrieval 的可切换配置，不与在线 API 的回答逻辑混写。SQLite 只保管轻量状态；Qdrant 保管索引；原始文件保存在本地文件存储，各层由稳定 ID 关联。

## 11. Deferred Decisions：按阶段解决的未定事项

以下是**实施前的阶段关口**，而非重新开启已确认的范围讨论：

| 最迟阶段 | 待确定内容 | 判断依据 |
|---|---|---|
| M0 | 远程 LLM Provider/模型及结构化输出能力；本地 Embedding 模型/维度；Qdrant 服务端/客户端版本 | 能完成最小端到端调用，并可估算 100 元总费用；失败时可替换模型而不改模块边界 |
| M0 | 证据 `fact` 到 Chunk 的映射规则、K/分母、回答规范化与评测子集；数据版本和项目自建开发/测试分割已由 DOCS-001 冻结 | 映射失败可单列；开发集足以估算成本，测试集封存；数据合同见 [evaluation/README.md](../evaluation/README.md) |
| M1 | Chunk 长度/重叠、文档身份与替换原子性、PDF 页码和 Markdown 章节定位、引用失效表示 | 固定文档的导入、重传、删除、引用验收通过 |
| M2 | 英文 BM25 文本处理、RRF 参数、本地 Reranker 模型/Top-N | 开发集检索指标与 CPU 时延，固定配置后跑封存测试集 |
| M3 | 证据覆盖结构化输出、无新信息判定细则、SSE 事件与断连/重试语义、固定依赖样本及人工审查规程 | 有界循环与错误终态可测；轨迹证明真实动态第二跳 |
| M4 | Checkpointer 版本、消息表与 Checkpoint 职责、Thread ID、摘要失败恢复、多轮样本规模 | 会话连续性和暂态重置回归测试 |

不在项目级设计中提前冻结类名、函数签名、数据库完整字段、像素级 UI 或精细模型参数。生产级认证授权、OCR、多租户和跨会话长期记忆仍属非目标。

## 12. 决策依据与追溯

| 审查决策 | 结论 | 设计依据与落点 |
|---|---|---|
| Q1 完成线 | M1 Dense 闭环；M3 Hybrid + 动态多跳 + Evaluation 为展示版；Memory 后移 | 保留核心亮点并控制秋招交付风险；§3、§8 |
| Q2 多跳证明 | MultiHop-RAG 总体对照 + 人工审查的证据依赖样本 | 多文档问题不必然要求后续查询依赖前跳；§7.1、§8 |
| Q3 模型边界 | 远程 LLM + 本地 Embedding/Reranker | 当前本地环境无可用 GPU；降低开发/评测等待时间；§5、§9 |
| Q4 语言 | M3 只承诺英文文档与英文问题 | 与英文主 Benchmark 一致，避免无中文指标却声称双语能力；§2、§3 |
| Q5 BM25 | Qdrant 原生全文 BM25、同批 Chunk 上 RRF | 减少双索引同步与更新/删除复杂度；§5、§6.2 |
| Q6 证据粒度 | 文章级与支持事实片段级分别报告 | 官方标注为文章元数据 + `fact`，没有本项目 Chunk ID；§7.2 |
| Q7 公平对照 | 实际效果 + D/E 等预算对照 | 区分动态决策效果与额外调用预算；§7.3 |
| Q8 证据充分 | 程序规则约束 + LLM 评估缺失事实 | 控制无意义循环，同时保留复杂问题适应性；§6.3 |
| Q9 文档生命周期 | M1 重传替换、删除、状态；不做版本历史 | 避免重复/陈旧索引和失效引用；§3.1、§6.1 |
| Q10 数据划分 | 自建固定开发集和封存测试集 | 公开数据只有一个 `train` 划分，防止测试集调参；§7.1 |
| Q11 Web 门槛 | M3 必须 SSE、阶段事件与 Trace | 完整展示 Agent 过程；对应成本进入 M3；§3.2、§8 |
| Q12 拒答样本 | `null_query` + 自建无证据问题 | 同时覆盖基准与上传文档场景；§7.1–7.2 |
| Q13 费用 | 远程 LLM 总额 ≤100 元人民币 | 预算上限约束模型选型与评测题数；§5、§7.1 |
| Q14 评测规模 | 全量离线 Retrieval；固定分层子集 Answer/Agent | 在预算内保留检索覆盖和诚实的回答成本报告；§7.1 |
| Q15 完成标准 | 工程验收与实测效果分开 | 不虚构提升，失败案例也进入报告；§7.4、§8 |

**后续使用方式**：以 §8 的阶段退出门槛和 §11 的阶段关口拆分 Issues。先建立“上传文档 → 索引 → 检索 → 回答 → 引用 → 评测”的 M1 路径，再扩展 M2/M3。本文不表示任何阶段已经交付。
