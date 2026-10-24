# DOCS-015 — 建立 Agent 回答、拒答和引用的固定样本评测

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M3

## Background

Agent 可运行不代表回答正确或引用受支持；null_query 不能混入有证据 Recall 分母。

## Goal

在预算内得到可复核的 Agent 回答、拒答、引用和成本逐题记录。

## Scope

- 固定分层问答子集，包含 null_query 与少量自建无证据题。
- 人工核查依赖样本及引用支持性，记录 Token/调用/费用。

## Out of Scope

A–E 最终公平对照汇总和未测量提升结论。

## Technical Approach

沿用冻结数据合同；封存测试集只在方案冻结后运行，引用合法率与支持率分报，错误拒答在可回答题统计。

## Implementation Steps

1. 按开发集成本冻结样本与预算。
2. 运行 Agent 回答/拒答。
3. 核查引用和依赖轨迹并保存逐题记录。

## Acceptance Criteria

- [ ] 固定样本 ID、问题类型、模型版本与费用可追溯。
- [ ] 适当/错误拒答、引用合法性/支持性分别有结果和分母。
- [ ] 依赖样本记录第一跳事实、第二跳查询及支持来源。
- [ ] 子集结果标明题数，不称为全量回答成绩。

## Tests & Validation

指标分母测试、标签隔离、人工审查记录与预算核对。

## Dependencies

- DOCS-013 — 完成证据充分性、停止、拒答与引用校验

## Deliverables

Agent 评测入口、固定样本、逐题结果和审查记录。
