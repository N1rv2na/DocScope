# DOCS-007 — 实现文档状态、重传替换与删除

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M1

## Background

只追加索引会留下重复或过期 Chunk，旧引用可能错误指向新材料。

## Goal

使用者能查看状态、重传替换和删除文档，检索与引用反映当前材料。

## Scope

- 文档列表/状态、同内容重传幂等、变化内容替换及删除。
- 失败状态和旧引用失效提示。

## Out of Scope

版本历史、回滚、批量管理和企业同步。

## Technical Approach

沿用稳定的 Document/Chunk 来源关系；本 Issue 冻结替换可见性与失败恢复方式，不让旧引用静默改指新 Chunk。

## Implementation Steps

1. 冻结生命周期合同。
2. 接入 API 和 Web 操作。
3. 验证重复、替换、删除与中途失败。

## Acceptance Criteria

- [ ] 同内容重传不增加可检索重复 Chunk。
- [ ] 内容变化后旧文本不可检索、新文本可检索；删除后均不可检索。
- [ ] 索引失败文档不显示为可用且不暴露半成品索引。
- [ ] 替换或删除后的旧引用明确失效，不跳到新内容。

## Tests & Validation

API/Web 生命周期集成测试；索引故障注入；旧引用复验。

## Dependencies

- DOCS-005 — 在 Web 完成 Markdown 上传、提问与引用查看

## Deliverables

状态与生命周期行为、界面和测试。
