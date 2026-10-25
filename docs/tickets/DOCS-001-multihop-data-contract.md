# DOCS-001 — 冻结 MultiHop-RAG 数据划分与标签隔离

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M0

## Background

公开数据没有可直接采用的独立测试集；参考答案与支持 fact 若进入在线索引，会使评测失真。

## Goal

得到可复现的数据版本、开发/封存测试划分和在线语料与离线标签边界。

## Scope

- 核对语料与问题字段，建立最小适配和稳定样本 ID。
- 按问题类型固定划分并检查重复问题，记录来源、版本和许可处理。

## Out of Scope

Dense 指标、Agent 评测和全量答案生成。

## Technical Approach

在线侧只接收文章正文及检索必需元数据；答案、fact 和不可回答标签只供离线评测器读取。按 2026-10-25 的用户更新决定，固定上游数据纳入本地 Git 并注明数据库许可；第三方新闻正文的公开再分发权仍需单独判断。

## Implementation Steps

1. 核对字段、来源和版本。
2. 冻结划分、样本 ID 与重复检查结果。
3. 用小样本验证在线/离线数据隔离。

## Acceptance Criteria

- [ ] 相同数据版本和划分配置生成相同的开发/测试样本 ID。
- [ ] 在线语料样本不含参考答案、支持 fact 或不可回答标签。
- [ ] 缺失必要字段或版本不匹配时适配过程明确失败。
- [ ] 记录数据来源、使用限制与原始语料处理方式。

## Tests & Validation

适配器字段测试、划分复现测试、标签隔离检查。

## Dependencies

None — can start immediately.

## Deliverables

数据适配代码、固定划分产物、测试及数据说明。
