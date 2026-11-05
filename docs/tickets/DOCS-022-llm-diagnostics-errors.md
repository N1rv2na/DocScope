# DOCS-022 — 建立 LLM 调用诊断与结构化错误分类

**Status:** ready-for-agent
**Priority:** P0

## Background

DOCS-016 的 70 个题次中有 14 次运行错误，其中 12 次模型输出截断、1 次超时、1 次规划格式错误。当前 LLM 层将多种失败转换为通用异常，Agent 难以区分 Token 截断、格式错误和请求失败，影响问题定位及后续恢复策略。

## Goal

为 Planner 和 Answer 建立可追溯的调用诊断与结构化错误分类，使模型失败原因能够准确关联到 Run。

## Scope

- 区分输出截断、JSON 解析失败、Schema 校验失败、请求超时及其他请求异常。
- 记录调用阶段、请求模型、响应模型、finish_reason、Token 用量及耗时。
- 将明确的错误代码关联到 Agent Run Trace，保持现有 API 错误响应兼容。

## Out of Scope

Prompt 优化、Token 预算调整、自动重试、模型切换和检索策略变更。

## Technical Approach

在 llm.py 中建立类型化异常与调用诊断；由 agent_run.py 映射为稳定的错误代码。保留现有结构化输出及引用校验合同，避免日志泄露 API Key 和原始敏感内容。

## Implementation Steps

1. 定义 LLM 异常类型及错误代码映射。
2. 完善 Planner/Answer 调用诊断与 Run Trace。
3. 增加不同模型失败场景的故障注入测试。

## Acceptance Criteria

- [ ] Token 截断、非法 JSON、Schema 错误、请求超时和其他请求异常可分别识别。
- [ ] Planner 与 Answer 的失败均能追溯到对应阶段和错误代码。
- [ ] 可获得的 Token、finish_reason、模型和延迟信息被记录；缺失信息不伪造。
- [ ] 现有 API 响应、拒答和引用校验行为保持兼容。
- [ ] 日志及持久化诊断记录不泄露凭据或不必要的敏感文本。

## Tests & Validation

LLM 模拟响应、异常分类单元测试、Agent Run Trace 回归及日志安全检查。

## Dependencies

- DOCS-016 — 完成 A–E 实际与相近预算对照报告

## Deliverables

结构化错误分类、调用诊断、Run Trace 增强及故障测试。
