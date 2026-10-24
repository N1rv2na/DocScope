# DOCS-002 — 验证单机模型、Qdrant 混合查询与费用边界

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M0

## Background

远程 LLM、本地 Embedding/Reranker 和 Qdrant 原生 BM25 已确定，但具体版本、CPU 可运行性与调用成本尚未验证。

## Goal

证明选定组合可运行，并给出 100 元人民币总预算的实测依据。

## Scope

- 固定候选模型和服务版本，验证本地推理、远程调用及用量记录。
- 在同批示例 Chunk 上运行 Dense、全文 BM25 和 RRF 最小查询。

## Out of Scope

完整检索服务、Provider 抽象和效果调优。

## Technical Approach

用直接的最小调用验证已选服务，不建立模型插件层；Qdrant 版本必须支持已确定的原生全文 BM25 路径。

## Implementation Steps

1. 固定环境和模型候选。
2. 执行模型调用与混合查询。
3. 记录单题成本并制定开发/最终评测费用分配。

## Acceptance Criteria

- [ ] 固定版本的 Qdrant 可在同批示例 Chunk 上执行 Dense、全文 BM25 和 RRF 查询。
- [ ] 本地 Embedding/Reranker 与远程 LLM 最小调用成功，版本和耗时有记录。
- [ ] 远程 Token/费用计算有真实样本依据，形成不超过 100 元的初步方案。
- [ ] 凭据未进入仓库或前端配置。

## Tests & Validation

可重复能力检查命令；环境示例的凭据检查。

## Dependencies

None — can start immediately.

## Deliverables

固定版本、能力检查、成本记录与本地运行前提。
