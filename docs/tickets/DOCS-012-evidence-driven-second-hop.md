# DOCS-012 — 实现依赖前跳证据的有界第二跳检索

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M3

## Background

一次性拆分多条查询不能证明动态多跳。

## Goal

人工审查样本的第二跳查询明确使用第一跳新发现的实体或事实。

## Scope

- 基于 Evidence 的下一查询生成及至少两跳有界检索。
- 固定依赖样本与逐跳查询、新事实、来源轨迹。

## Out of Scope

Multi-Agent、无界循环、会话历史和完整拒答评测。

## Technical Approach

沿用 Retrieval Engine 边界；先用硬上限防止无界调用，下一 Issue 完善充分性与停止策略。

## Implementation Steps

1. 人工审查并固定依赖样本。
2. 增加新证据驱动的下一查询。
3. 保存和复核逐跳轨迹。

## Acceptance Criteria

- [ ] 固定样本第二跳查询含第一跳才发现的必要实体或事实。
- [ ] 轨迹指出新事实来自哪个实际检索 Chunk。
- [ ] 检索次数受硬上限控制，重复查询不形成无限循环。
- [ ] 一次性多查询规划不会被误记为证据驱动第二跳。

## Tests & Validation

依赖样本轨迹集成测试、调用上限测试与人工复核。

## Dependencies

- DOCS-011 — 让 LangGraph 单轮 Run 产生可追踪回答

## Deliverables

动态第二跳路径、审查样本和轨迹测试。
