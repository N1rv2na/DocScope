# DOCS-006 — 导入文本型 PDF 并展示准确页码引用

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M1

## Background

M1 承诺文本型 PDF 与 Markdown，当前路径只覆盖 Markdown。

## Goal

浏览器上传文本型 PDF 后，回答引用能定位到正确页码和原文片段。

## Scope

- PDF 文本提取与页码映射，复用现有摄取/问答路径。
- 前端展示页码并处理不可解析输入。

## Out of Scope

OCR、图片、表格高精度解析和 Office 格式。

## Technical Approach

PDF Chunk 保留原文及可用页码，不建立与 Markdown 不兼容的第二套检索身份。

## Implementation Steps

1. 加入文本提取和来源映射。
2. 接入现有上传与引用界面。
3. 用固定跨页和无文本样例验证。

## Acceptance Criteria

- [ ] 固定文本型 PDF 的问题返回预期原文与正确页码引用。
- [ ] 无可提取文本或损坏 PDF 不显示为可用文档。
- [ ] PDF 与 Markdown 继续使用同一 Evidence/Citation 合同。
- [ ] 浏览器区分页码与章节来源。

## Tests & Validation

PDF 上传—问答—引用端到端测试；损坏/无文本 PDF 测试。

## Dependencies

- DOCS-005 — 在 Web 完成 Markdown 上传、提问与引用查看

## Deliverables

PDF 摄取、页码映射、UI 更新、样例与测试。
