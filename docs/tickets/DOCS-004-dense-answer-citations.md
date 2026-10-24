# DOCS-004 — 让 Dense 问答 API 返回回答或拒答及合法引用

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M1

## Background

已有路径只能返回 Evidence，尚不能完成检索、回答和引用。

## Goal

独立英文问题经 API 得到有实际 Chunk 引用的回答，或明确的证据不足结果。

## Scope

- 接入远程 LLM 单轮问答、run_id 与引用 ID 校验。
- 处理空问题、无证据和模型失败。

## Out of Scope

LangGraph 多跳、SSE、会话记忆和前端。

## Technical Approach

只把实际检索的 Evidence 交给生成；文档文本作为不可信数据，引用从本次返回的 Chunk 集合校验。

## Implementation Steps

1. 接入单轮生成。
2. 定义回答、拒答和错误的对外结果。
3. 加入引用与输入边界测试。

## Acceptance Criteria

- [ ] 固定可回答问题返回答案、run_id 及本次检索结果中的合法引用。
- [ ] 无相关证据时明确证据不足，不生成虚构引用。
- [ ] 空白问题不触发检索或 LLM 调用。
- [ ] 模型失败与证据不足可区分；伪指令文档不改变系统指令边界。

## Tests & Validation

可控模型响应的 API 集成测试；必要的真实模型冒烟检查。

## Dependencies

- DOCS-003 — 让 Markdown 上传后可经 Dense API 找回原文证据

## Deliverables

Dense 问答 API、引用校验和错误测试。
