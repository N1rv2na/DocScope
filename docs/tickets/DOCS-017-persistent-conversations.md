# DOCS-017 — 实现可恢复、可切换和可删除的会话

**Status:** ready-for-agent
**Priority:** P1
**Planned milestone:** M4

## Background

M3 只处理独立问题；M4 才需要按会话组织历史与 Checkpoint。

## Goal

用户可创建、切换、恢复和删除会话，消息与 Checkpoint 清理一致。

## Scope

- thread_id 生命周期、消息与 Checkpointer 职责划分。
- 会话列表/历史 UI 及删除清理。

## Out of Scope

查询改写、滚动摘要和跨会话语义记忆。

## Technical Approach

冻结 SQLite/Checkpointer 职责；Run 暂态与供 UI 展示的完整消息分离。

## Implementation Steps

1. 确定持久化边界。
2. 打通创建、切换、恢复及删除。
3. 验证重启和清理。

## Acceptance Criteria

- [ ] 切换并重新打开会话时正确显示各自历史，不串会话。
- [ ] 删除后消息和对应 Checkpoint 不再可恢复。
- [ ] 新 Run 不继承上轮检索计划、Evidence 或轮数。

## Tests & Validation

会话 API/Web 集成、重启恢复和删除清理测试。

## Dependencies

- DOCS-014 — 通过 SSE 在 Web 展示 Agent 回答、引用与轨迹

## Deliverables

会话持久化与 UI、测试和状态边界说明。
