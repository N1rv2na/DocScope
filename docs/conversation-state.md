# DOCS-017 会话状态边界

DOCS-017 提供会话目录、历史恢复和删除；它不提供利用历史理解追问的能力。

- `POST /api/conversations` 创建服务端 UUID `thread_id`；`GET /api/conversations` 列表，`GET /api/conversations/{thread_id}` 返回完整已完成消息及引用，`DELETE` 删除。
- `POST /api/conversations/{thread_id}/runs/stream` 沿用 Agent SSE 事件合同并增加 `thread_id`。`done` 仅在完整 Run 的消息写入成功后发送。失败 Run 返回 `error`，不写入完成消息。原独立问答接口继续可用。
- `conversations.sqlite3` 保存会话元数据和供 UI 展示的用户/助手消息、引用及结果状态。`checkpoints.sqlite3` 由 `langgraph-checkpoint-sqlite==3.1.3` 保存 LangGraph 图状态；它不是 UI 消息的唯一来源。每轮使用相同 `thread_id`，但在图输入显式重置问题、查询、Evidence、检索轮数、调用预算、回答和停止状态。事实问题每轮重新检索。
- 删除先将会话标记为 `deleting`，此时不再可读取或运行；再删除对应 Checkpoint，最后删除消息和会话元数据。清理失败可重试；服务重启后会继续清理标记为 `deleting` 的会话。单进程中会话 Run 与删除串行化。
- 本阶段没有历史上下文输入、Window、摘要或指代改写。历史引用保留原 Chunk ID；源文档替换或删除后仍按既有引用有效性接口复验，不把旧引用当作当前事实。

**Assumption**：当前为单进程、单用户本地部署。多进程并发与跨用户访问控制不在 DOCS-017 范围内。摘要失败和重复请求的恢复语义留待后续 M4 Issue 决定。
