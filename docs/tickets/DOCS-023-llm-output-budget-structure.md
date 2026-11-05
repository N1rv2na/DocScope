# DOCS-023 — 优化 LLM 输出预算与结构化输出可靠性

**Status:** ready-for-agent
**Priority:** P0

## Background

DOCS-016 在 Answer 2048 Tokens、Planner 4096 Tokens 的离线预算下仍出现 12 次输出截断。Web 默认预算更低，且实际模型可能消耗较多 Reasoning Tokens。单纯提高输出上限缺少成本与稳定性依据。

## Goal

建立可靠、可配置且有成本依据的 Planner/Answer 输出配置，降低模型截断及结构化输出失败。

## Scope

- 分析 Planner/Answer 的输出 Token、Reasoning Token 和截断分布。
- 配置化各阶段输出预算及请求超时，记录实际生效配置。
- 优化结构化输出指令与本地 Schema 校验，维持原有证据和引用约束。
- 在冻结样本上比较不同预算的成功率、延迟和 Token 消耗。

## Out of Scope

自动重试、模型或 Magpie 渠道切换、Agent 状态机修改、检索优化和新 Benchmark。

## Technical Approach

沿用 OpenAI-compatible json_object 请求与本地 Pydantic 校验。通过 DOCS-022 的调用诊断分析截断原因，选择有实验依据的输出预算；不假设所有网关均支持额外的推理参数或严格 JSON Schema 模式。

## Implementation Steps

1. 分析现有截断调用及 Token 消耗。
2. 将 Answer/Planner 预算和超时配置化，验证兼容性。
3. 优化输出指令并完成固定样本对照。

## Acceptance Criteria

- [ ] Planner/Answer 的输出预算和超时可独立配置，默认值及生效值可追溯。
- [ ] 截断样本有 Reasoning Token、输出预算及失败阶段分析；不可获得的指标明确标注。
- [ ] 固定配置下结构化输出及引用合同测试全部通过。
- [ ] 新配置相对历史配置的截断率、延迟和 Token 差异有实测记录。
- [ ] 未测得改善或出现退化时如实报告，不以无限提高预算代替问题分析。

## Tests & Validation

配置边界、JSON/Schema 校验、上下文限制测试及固定 dev 样本真实模型对照。

## Dependencies

- DOCS-022 — 建立 LLM 调用诊断与结构化错误分类

## Deliverables

可配置 LLM 输出参数、结构化输出优化、测试与预算对照记录。
