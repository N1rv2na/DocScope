# DOCS-005 — 在 Web 完成 Markdown 上传、提问与引用查看

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M1

## Background

基础问答已有 API，但项目定位要求 Web 可演示路径。

## Goal

使用者仅在浏览器中完成 Markdown 上传、单轮提问和原文引用查看。

## Scope

- 构建 React/TypeScript 基础页面与上传状态、回答、引用和错误展示。
- 使主路径可用键盘操作。

## Out of Scope

PDF、替换/删除、SSE、多轮会话和复杂后台。

## Technical Approach

前端调用现有 API，不复制检索逻辑；引用展示文档、章节与原文片段。

## Implementation Steps

1. 建立最小页面。
2. 接入上传、问答和引用。
3. 验证错误状态与键盘操作。

## Acceptance Criteria

- [ ] 固定 Markdown 可从浏览器上传，索引完成后可提问并查看预期引用片段。
- [ ] 上传、提问和打开引用可仅用键盘完成且焦点可见。
- [ ] 空问题、导入失败和模型失败显示为不同的非成功状态。
- [ ] 前端不包含 LLM 凭据。

## Tests & Validation

浏览器主路径与错误路径端到端测试；键盘检查。

## Dependencies

- DOCS-004 — 让 Dense 问答 API 返回回答或拒答及合法引用

## Deliverables

基础 Web 页面、端到端测试与前端启动步骤。
