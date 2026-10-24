# DocScope Issue 草稿总览

> 21 条本地草稿已发布为 [DocScope GitHub Issues](https://github.com/N1rv2na/DocScope/issues)，并关联 M0–M5 Milestones 和 22 条原生 blocked-by 依赖。GitHub Issue 正文中的直接依赖也已链接到前置 Issue；本地文件继续作为草稿留存。草稿不包含开发职责标注。

## Milestones

| 阶段 | 目标 | Issues | 阶段验收 |
|---|---|---|---|
| M0 | 冻结数据/运行合同 | DOCS-001–DOCS-002 | 数据划分与标签隔离可复现；本地/远程模型及 Qdrant 路径有真实能力与费用记录 |
| M1 | Dense RAG Web 基础闭环 | DOCS-003–DOCS-004–DOCS-005–DOCS-006–DOCS-007–DOCS-008 | Markdown/PDF 上传→索引→问答→引用可演示；重传/删除与失败状态正确；Dense Baseline 可复现 |
| M2 | Hybrid + Rerank | DOCS-009–DOCS-010 | 同批 Chunk 上三种检索配置可切换，文章/片段指标与逐题延迟可复核 |
| M3 | Agentic RAG + SSE 秋招展示版 | DOCS-011–DOCS-012–DOCS-013–DOCS-014–DOCS-015–DOCS-016 | 依赖前跳的第二跳、停止/拒答/引用、SSE 错误路径与公平对照全部可审查 |
| M4 | 会话级短期记忆（后续） | DOCS-017–DOCS-018–DOCS-019 | 会话恢复/切换/删除、追问改写、长历史摘要和独立评测通过 |
| M5 | 复现与成果审计（后续） | DOCS-020–DOCS-021 | Compose 新环境启动/评测可重放；README/Demo 仅陈述已实现及实测结果 |

M1 首条可运行路径为 DOCS-003 检索 API → DOCS-004 问答 API → DOCS-005 浏览器演示。M3 完成线要求 DOCS-014 的 Web SSE 与 DOCS-016 的公平对照报告均通过；M4/M5 不阻塞 M3。

## Issues 与直接依赖

| 编号 | GitHub Issue | 标题 | 阶段 | 优先级 | 直接依赖 |
|---|---|---|---|---|---|
| [DOCS-001](DOCS-001-multihop-data-contract.md) | [#1](https://github.com/N1rv2na/DocScope/issues/1) | 冻结 MultiHop-RAG 数据划分与标签隔离 | M0 | P0 | 无 |
| [DOCS-002](DOCS-002-model-qdrant-feasibility.md) | [#2](https://github.com/N1rv2na/DocScope/issues/2) | 验证单机模型、Qdrant 混合查询与费用边界 | M0 | P0 | 无 |
| [DOCS-003](DOCS-003-markdown-dense-evidence.md) | [#3](https://github.com/N1rv2na/DocScope/issues/3) | 让 Markdown 上传后可经 Dense API 找回原文证据 | M1 | P0 | DOCS-002 |
| [DOCS-004](DOCS-004-dense-answer-citations.md) | [#4](https://github.com/N1rv2na/DocScope/issues/4) | 让 Dense 问答 API 返回回答或拒答及合法引用 | M1 | P0 | DOCS-003 |
| [DOCS-005](DOCS-005-markdown-web-flow.md) | [#5](https://github.com/N1rv2na/DocScope/issues/5) | 在 Web 完成 Markdown 上传、提问与引用查看 | M1 | P0 | DOCS-004 |
| [DOCS-006](DOCS-006-pdf-page-citations.md) | [#6](https://github.com/N1rv2na/DocScope/issues/6) | 导入文本型 PDF 并展示准确页码引用 | M1 | P0 | DOCS-005 |
| [DOCS-007](DOCS-007-document-lifecycle.md) | [#7](https://github.com/N1rv2na/DocScope/issues/7) | 实现文档状态、重传替换与删除 | M1 | P0 | DOCS-005 |
| [DOCS-008](DOCS-008-dense-baseline-evaluation.md) | [#8](https://github.com/N1rv2na/DocScope/issues/8) | 建立 Dense Baseline 与双粒度检索评测 | M1 | P0 | DOCS-001、DOCS-004 |
| [DOCS-009](DOCS-009-qdrant-bm25-rrf.md) | [#9](https://github.com/N1rv2na/DocScope/issues/9) | 在同批 Chunk 上加入 Qdrant BM25 与 RRF | M2 | P0 | DOCS-007、DOCS-008 |
| [DOCS-010](DOCS-010-cross-encoder-ablation.md) | [#10](https://github.com/N1rv2na/DocScope/issues/10) | 接入本地 Cross-Encoder 并完成检索消融 | M2 | P0 | DOCS-009 |
| [DOCS-011](DOCS-011-langgraph-single-run.md) | [#11](https://github.com/N1rv2na/DocScope/issues/11) | 让 LangGraph 单轮 Run 产生可追踪回答 | M3 | P0 | DOCS-010 |
| [DOCS-012](DOCS-012-evidence-driven-second-hop.md) | [#12](https://github.com/N1rv2na/DocScope/issues/12) | 实现依赖前跳证据的有界第二跳检索 | M3 | P0 | DOCS-011 |
| [DOCS-013](DOCS-013-sufficiency-stopping-abstention.md) | [#13](https://github.com/N1rv2na/DocScope/issues/13) | 完成证据充分性、停止、拒答与引用校验 | M3 | P0 | DOCS-012 |
| [DOCS-014](DOCS-014-sse-agent-web-trace.md) | [#14](https://github.com/N1rv2na/DocScope/issues/14) | 通过 SSE 在 Web 展示 Agent 回答、引用与轨迹 | M3 | P0 | DOCS-013 |
| [DOCS-015](DOCS-015-agent-answer-evaluation.md) | [#15](https://github.com/N1rv2na/DocScope/issues/15) | 建立 Agent 回答、拒答和引用的固定样本评测 | M3 | P0 | DOCS-013 |
| [DOCS-016](DOCS-016-fair-ablation-report.md) | [#16](https://github.com/N1rv2na/DocScope/issues/16) | 完成 A–E 实际与相近预算对照报告 | M3 | P0 | DOCS-015 |
| [DOCS-017](DOCS-017-persistent-conversations.md) | [#17](https://github.com/N1rv2na/DocScope/issues/17) | 实现可恢复、可切换和可删除的会话 | M4 | P1 | DOCS-014 |
| [DOCS-018](DOCS-018-window-query-rewriting.md) | [#18](https://github.com/N1rv2na/DocScope/issues/18) | 实现近期窗口与历史相关查询改写 | M4 | P1 | DOCS-017 |
| [DOCS-019](DOCS-019-rolling-summary-memory-eval.md) | [#19](https://github.com/N1rv2na/DocScope/issues/19) | 实现滚动摘要与长对话独立评测 | M4 | P1 | DOCS-018 |
| [DOCS-020](DOCS-020-compose-reproducibility.md) | [#20](https://github.com/N1rv2na/DocScope/issues/20) | 用 Docker Compose 复现启动与评测 | M5 | P1 | DOCS-016、DOCS-019 |
| [DOCS-021](DOCS-021-demo-results-audit.md) | [#21](https://github.com/N1rv2na/DocScope/issues/21) | 完成 README、Demo 与实测结论审计 | M5 | P1 | DOCS-020 |

## 推荐执行顺序

1. DOCS-001 与 DOCS-002 可并行。
2. DOCS-003 → 004 → 005 建立最早可演示闭环；DOCS-008 在 001、004 后可与 Web/PDF 工作并行；DOCS-006 和 007 在 005 后并行。
3. DOCS-009 → 010 完成检索消融；DOCS-011 → 012 → 013 完成 Agent 核心。
4. DOCS-014 与 015 在 013 后并行；DOCS-016 在 015 后完成 M3 效果报告。M3 到此可投递与展示。
5. 后续执行 DOCS-017 → 018 → 019，再在 DOCS-016/019 后执行 020 → 021。

## GitHub 元数据

- 已创建 M0–M5 Milestones，并按本表关联各 Issue。
- 已创建并添加 `ready-for-agent` 与 `priority:P0`/`priority:P1` 标签；优先级也保留在正文中。
- GitHub Issue 正文中的 Dependencies 段已链接前置 Issue，同时设置了 22 条 GitHub 原生 blocked-by 关系。
