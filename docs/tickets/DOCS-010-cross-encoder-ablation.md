# DOCS-010 — 接入本地 Cross-Encoder 并完成检索消融

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M2

## Background

Reranking 的收益与 CPU 代价未验证。

## Goal

在受限候选上运行本地重排，得到 Dense、Hybrid、Hybrid+Rerank 可比结果。

## Scope

- 固定模型版本和 Top-N，限制重排候选。
- 输出三组排序、文章/片段指标、延迟和错误案例。

## Out of Scope

额外检索模型、GPU 平台和 Agent。

## Technical Approach

只对 RRF 候选重排；保留 Chunk 身份及各阶段排名，不混算不同评分。

## Implementation Steps

1. 测试 CPU 模型与候选上限。
2. 接入可切换重排。
3. 运行三组同输入消融。

## Acceptance Criteria

- [ ] 三组配置在同批 Chunk、Embedding 和开发集上可重复运行。
- [ ] 输出每组检索指标、逐题延迟和可复核错误案例。
- [ ] 重排不改变来源 ID，也不超候选/证据长度上限。
- [ ] 报告观察到的收益或退化，不写预设提升。

## Tests & Validation

候选数量及身份测试、CPU 冒烟、三组 Benchmark。

## Dependencies

- DOCS-009 — 在同批 Chunk 上加入 Qdrant BM25 与 RRF

## Deliverables

本地 Reranker、固定配置、测试和 M2 消融报告。
