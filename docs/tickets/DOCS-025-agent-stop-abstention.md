# DOCS-025 — 修正 Agent 提前拒答与停止条件

**Status:** ready-for-agent
**Priority:** P0

## Background

当前 agent_run.py 在 Planner 无法生成下一查询或第二跳没有新增 Evidence 时，可能直接返回 insufficient_evidence。无法继续检索并不必然代表现有证据不足，可能引发错误拒答。DOCS-016 中 45 个可回答题次有 15 次错误拒答，其具体来源尚需逐题归因。

## Goal

修正 Agent 停止与拒答控制流，明确区分检索停止、规划失败、证据不足和最终回答判断，减少控制流引起的错误拒答。

## Scope

- 审查充分、缺失、重复查询、无新证据和预算耗尽的状态转移。
- 对仍持有有效 Evidence 的停止分支，按剩余预算决定是否进入最终 Answer。
- 区分检索过程停止原因与最终回答状态，保留可解释 Trace。
- 保持无证据拒答、引用合法性、来源有效性及硬预算约束。

## Out of Scope

检索召回优化、第二跳查询生成策略、证据重排、模型 Prompt 调优和放宽引用约束。

## Technical Approach

修改 AgentRunner 的状态转移：检索无法继续时，不自动等同于证据不足。存在可用证据且预算允许时，可进入 Answer，由最终回答校验决定回答或拒答；没有证据或预算不可用时保留明确停止原因。

## Implementation Steps

1. 梳理现有停止分支及可回答题的错误拒答 Trace。
2. 调整无新证据、无可用下一查询等分支的状态转移。
3. 增加证据、调用预算与状态组合的回归测试。

## Acceptance Criteria

- [ ] no_evidence、no_new_evidence、repeated_query、missing_fact_unresolved 和预算耗尽具有明确停止语义。
- [ ] 有效 Evidence 不因检索停止而被无条件丢弃或直接判定为不足。
- [ ] 最终回答或拒答仍基于已有 Evidence，并通过原有引用与来源校验。
- [ ] 无证据、真实证据不足及预算耗尽场景不会被强制回答。
- [ ] 检索轮数和模型调用预算不超限，已有动态第二跳依赖样本保持通过。

## Tests & Validation

LangGraph 状态转移测试、停止分支故障注入、固定依赖样本及错误拒答案例回归。

## Dependencies

- DOCS-013 — 完成证据充分性、停止、拒答与引用校验
- DOCS-022 — 建立 LLM 调用诊断与结构化错误分类

## Deliverables

修正后的 Agent 停止控制流、拒答原因记录及状态机回归测试。
