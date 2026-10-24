# DOCS-003 — 让 Markdown 上传后可经 Dense API 找回原文证据

**Status:** ready-for-agent
**Priority:** P0
**Planned milestone:** M1

## Background

仓库尚无业务实现，需要一条可独立运行的摄取与检索路径。

## Goal

上传固定英文 Markdown 后，问题可经 API 找回带文档和章节位置的 Dense Evidence。

## Scope

- 建立最小 FastAPI 入口及 Markdown 校验、解析、分块、Embedding、Qdrant 索引。
- 保存源文件、可追溯 ID，并提供返回 Evidence 的检索入口。

## Out of Scope

LLM 回答、前端、PDF、重传替换和 BM25。

## Technical Approach

使用固定公开样例打通 Document → Chunk → Evidence；索引未完成不得标记为可用。

## Implementation Steps

1. 建立最小服务和本地启动步骤。
2. 打通上传、分块、Embedding、索引和查询。
3. 加入来源映射与故障集成测试。

## Acceptance Criteria

- [ ] 固定 Markdown 上传后，指定查询返回预期原文 Chunk、document_id、chunk_id 和章节来源。
- [ ] 空文件、不支持格式与不安全路径不能产生可用索引。
- [ ] 摄取失败时可观察到失败状态，查询不命中未完成 Chunk。
- [ ] 本地启动与固定样例操作可重放。

## Tests & Validation

上传—索引—检索集成测试；无效输入及摄取故障测试。

## Dependencies

- DOCS-002 — 验证单机模型、Qdrant 混合查询与费用边界

## Deliverables

最小 API、Markdown/Dense 路径、测试和启动说明。
