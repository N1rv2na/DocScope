# DOCS-018 — 实现近期窗口与历史相关查询改写

**Status:** ready-for-agent
**Priority:** P1
**Planned milestone:** M4

## Background

有会话历史仍不足以处理省略追问；完整历史入模又增加上下文成本。

## Goal

会话追问基于受控近期上下文形成独立查询，再从文档检索事实。

## Scope

- Recent Window、历史相关查询改写。
- 指代、话题切换和暂态重置测试。

## Out of Scope

滚动摘要、跨会话记忆和摘要作为事实证据。

## Technical Approach

在 M4 开发集确定窗口预算；改写只解决意图和指代，答案仍由文档 Evidence 支持。

## Implementation Steps

1. 固定追问样本。
2. 接入窗口和改写。
3. 验证指代、话题切换及历史污染。

## Acceptance Criteria

- [ ] 固定追问产生包含必要实体的独立检索问题。
- [ ] 话题切换不把前主题实体错误带入新问题。
- [ ] 回答引用重新检索的 Chunk，不仅据聊天历史给出新事实。
- [ ] 不同 Run 的检索暂态互不污染。

## Tests & Validation

多轮追问端到端回归与改写样本审查。

## Dependencies

- DOCS-017 — 实现可恢复、可切换和可删除的会话

## Deliverables

近期窗口、改写路径、固定样本和测试。
