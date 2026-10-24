# DOCS-011 — 让 LangGraph 单轮 Run 产生可追踪回答

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M3

## Background

现有问答尚未经过目标 Agent 工作流，直接加入迭代难以定位状态与引用错误。

## Goal

独立问题经单 Agent 一次检索得到既有回答/拒答行为，并留下 Run 轨迹。

## Scope

- LangGraph 最小计划、检索、回答路径。
- 记录 run_id、检索结果 ID、阶段和停止原因。

## Out of Scope

第二跳、会话记忆、SSE 和复杂充分性策略。

## Technical Approach

Agent 调用 Retrieval Engine，不直接操作 Qdrant；保持现有 API 对外行为作为回归接缝。

## Implementation Steps

1. 建最小有界 Run。
2. 接入现有检索与回答。
3. 对照旧单轮样例检查来源与结果。

## Acceptance Criteria

- [ ] 固定单轮问题经 LangGraph 完成，引用仍来自本次 Evidence。
- [ ] Run 记录查询、返回证据 ID、阶段、停止原因和调用量。
- [ ] 故障时 Run 标为失败，不输出成功回答或无限重试。

## Tests & Validation

单轮 API 回归、Run 轨迹与故障测试。

## Dependencies

- DOCS-010 — 接入本地 Cross-Encoder 并完成检索消融

## Deliverables

最小 LangGraph 路径、轨迹记录和回归测试。
