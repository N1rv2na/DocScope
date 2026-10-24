# DOCS-014 — 通过 SSE 在 Web 展示 Agent 回答、引用与轨迹

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M3

## Background

M3 必须具备 SSE 与可见检索轨迹，仅 API Agent 不足以完成 Web 门槛。

## Goal

用户在浏览器看到回答流、阶段、引用和停止原因，并能辨认失败。

## Scope

- POST 兼容的 SSE 消费与 stage/token/citation/done/error 事件。
- 关联 run_id，展示引用和简要轨迹；断连不误报完成。

## Out of Scope

多轮会话、私有推理链与复杂流式基础设施。

## Technical Approach

冻结客户端断连/重试最小语义；前端仅展示后端阶段和 Evidence，不参与 Agent 判断。

## Implementation Steps

1. 确定事件载荷及终态。
2. 接通 API 与 Web。
3. 验证正常、故障、断连与可访问性。

## Acceptance Criteria

- [ ] 正常 Run 的事件可按 run_id 关联，页面最终展示答案、引用及轮次轨迹。
- [ ] 已捕获故障显示 error；未收到完成信号时不将部分回答标为成功。
- [ ] 页面展示查询、检索完成、引用集合及停止原因，不展示私有推理链。
- [ ] 流式状态和错误可被键盘及辅助技术获知，连续 Token 不逐字符播报。

## Tests & Validation

SSE 正常/故障/断连端到端测试及键盘/状态播报检查。

## Dependencies

- DOCS-013 — 完成证据充分性、停止、拒答与引用校验

## Deliverables

SSE API/Web 路径、事件合同和测试。
