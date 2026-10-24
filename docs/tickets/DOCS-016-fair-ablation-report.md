# DOCS-016 — 完成 A–E 实际与相近预算对照报告

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M3

## Background

Agent 多调用可能改变效果，仅与单次检索比较无法识别动态决策自身贡献。

## Goal

提供可复核的 A–E 消融、实际效果及 D/E 相近预算对照和失败分析。

## Scope

- 一次性查询规划 D 对照，A–E 同数据/模型配置。
- 相近检索次数、候选和证据预算；质量、延迟、Token、调用量和费用报告。

## Out of Scope

在测试集反复调参、预设提升百分比和额外 Benchmark。

## Technical Approach

D 一次规划多个查询但不读前跳证据改写；E 可依证据迭代。无法完全匹配预算时披露差异。

## Implementation Steps

1. 固定 D 对照合同。
2. 冻结 A–E 输入及预算后运行。
3. 汇总逐题结果与误差类型。

## Acceptance Criteria

- [ ] A–E 使用同一冻结样本、语料切分、Embedding 和 LLM 配置。
- [ ] 报告分别给出实际质量/成本和 D/E 相近预算比较。
- [ ] 逐题结果可追溯配置、轨迹、引用及费用；预算差异有披露。
- [ ] 未测得提升时报告退化或无差异，不声称 Agent 优于基线。

## Tests & Validation

配置一致性检查、结果聚合复算和逐题样本审查。

## Dependencies

- DOCS-015 — 建立 Agent 回答、拒答和引用的固定样本评测

## Deliverables

固定对照配置、逐题产物、M3 消融与误差报告。
