# DOCS-009 — 在同批 Chunk 上加入 Qdrant BM25 与 RRF

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M2

## Background

M1 只有 Dense；展示版要求 Hybrid，且不能另建需同步的 BM25 系统。

## Goal

现有文档在同批 Chunk 上获得 Dense-only 与 Hybrid 两种可切换结果。

## Scope

- Qdrant 原生全文 BM25、英文文本处理、两路候选和 RRF。
- 重传/删除后的检索一致性及 Dense 对照评测。

## Out of Scope

Cross-Encoder、Agent、中文分词和独立 BM25 数据库。

## Technical Approach

遵守 ADR 0001；使用 M0 验证版本，在开发集选择英文分词和 RRF 配置。

## Implementation Steps

1. 建全文索引与查询。
2. 融合并保持 Chunk 身份。
3. 对照 Dense 指标和生命周期行为。

## Acceptance Criteria

- [ ] 同一查询可选择 Dense-only 或 Hybrid，返回来源一致的 Chunk。
- [ ] 两路候选与最终数量受配置上限控制。
- [ ] 重传/删除后两路均不返回过期 Chunk。
- [ ] 同一开发集输出两组文章/片段指标与逐题延迟，不预设提升。

## Tests & Validation

混合查询集成测试、生命周期回归和 Dense/Hybrid 对照。

## Dependencies

- DOCS-007 — 实现文档状态、重传替换与删除
- DOCS-008 — 建立 Dense Baseline 与双粒度检索评测

## Deliverables

Hybrid 检索路径、可切换配置、测试与对照结果。
