# DOCS-008 — 建立 Dense Baseline 与双粒度检索评测

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M1

## Background

检索改进需要固定 Baseline；官方文章证据不能直接当作 Chunk 命中。

## Goal

对同一语料运行可复现的 Dense 评测，分别报告文章级和支持事实片段级结果。

## Scope

- 评测文章经标准摄取路径索引，建立 fact 到 Chunk 的映射和失败分类。
- 运行全量离线检索、评测前固定的答案子集，保存逐题结果；子集用于可复现比较，不受项目级累计金额上限驱动。

## Out of Scope

BM25、Rerank 和 Agent 效果结论。

## Technical Approach

复用冻结问题集合和 Dense 问答路径；null_query 不进入有证据 Recall 分母，无法映射 fact 单列。

## Implementation Steps

1. 冻结映射与指标分母。
2. 运行 Dense 全量检索和固定答案子集。
3. 保存配置、逐题结果与错误案例。

## Acceptance Criteria

- [ ] 同版本和配置产生相同题目集合、指标分母与结果字段。
- [ ] 文章级与片段级指标分别输出；映射失败和 null_query 独立计数。
- [ ] 答案及证据标注不进入在线索引或 Prompt。
- [ ] 答案子集题数、类型和真实调用费用有记录。

## Tests & Validation

指标计算测试、映射人工抽查、重复运行与标签隔离检查。

## Dependencies

- DOCS-001 — 冻结 MultiHop-RAG 数据划分与标签隔离
- DOCS-004 — 让 Dense 问答 API 返回回答或拒答及合法引用

## Deliverables

Dense Baseline 入口、固定配置、逐题结果与简要报告。
