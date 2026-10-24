# DOCS-019 — 实现滚动摘要与长对话独立评测

**Status:** ready-for-agent
**Priority:** P1
**Planned milestone:** M4

## Background

长会话不能无限增加模型输入，摘要失真也可能污染事实回答。

## Goal

较早历史可压缩供意图理解，并可独立量化多轮正确性和污染风险。

## Scope

- Token 阈值触发的增量摘要、长度限制及失败恢复。
- 多轮测试集与 Window/Rewrite/Summary 对照。

## Out of Scope

用户画像、跨会话长期记忆和摘要作为文档证据。

## Technical Approach

摘要保存主题和指代线索；事实重新检索。阈值及样本量在本 Issue 成本约束内冻结。

## Implementation Steps

1. 建立长历史和话题切换样本。
2. 接入摘要及失败处理。
3. 运行独立 Memory 对照。

## Acceptance Criteria

- [ ] 超过冻结阈值的会话不无限增长模型输入，摘要有长度上限。
- [ ] 摘要失败不丢失已有消息或把失败摘要当事实。
- [ ] 多轮指标、污染案例、逐题配置和样本范围可复核。
- [ ] 删除会话可清理消息与摘要状态。

## Tests & Validation

长历史/摘要故障回归和独立 Memory Benchmark。

## Dependencies

- DOCS-018 — 实现近期窗口与历史相关查询改写

## Deliverables

滚动摘要、失败测试与 M4 评测报告。
