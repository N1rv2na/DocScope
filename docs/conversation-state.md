# M4 会话状态边界（DOCS-017/018）

DOCS-017 提供会话目录、历史恢复和删除；DOCS-018 在现有会话路径增加近期窗口与历史相关查询改写。

- `POST /api/conversations` 创建服务端 UUID `thread_id`；`GET /api/conversations` 列表，`GET /api/conversations/{thread_id}` 返回完整已完成消息及引用，`DELETE` 删除。
- `POST /api/conversations/{thread_id}/runs/stream` 沿用 Agent SSE 事件合同并增加 `thread_id`。`done` 仅在完整 Run 的消息写入成功后发送。失败 Run 返回 `error`，不写入完成消息。原独立问答接口继续可用。
- `conversations.sqlite3` 保存会话元数据和供 UI 展示的用户/助手消息、引用及结果状态。`checkpoints.sqlite3` 由 `langgraph-checkpoint-sqlite==3.1.3` 保存 LangGraph 图状态；它不是 UI 消息的唯一来源。每轮使用相同 `thread_id`，但在图输入显式重置问题、查询、Evidence、检索轮数、调用预算、回答和停止状态。事实问题每轮重新检索。
- 删除先将会话标记为 `deleting`，此时不再可读取或运行；再删除对应 Checkpoint，最后删除消息和会话元数据。清理失败可重试；服务重启后会继续清理标记为 `deleting` 的会话。单进程中会话 Run 与删除串行化。
- DOCS-018 从当前会话的已完成用户/助手消息中最多读取最近 4 轮，每条助手回答最多 1000 字符，并在序列化窗口超过 6000 UTF-8 字节时丢弃最早的完整轮次。首轮没有历史时不增加模型调用；有窗口时经 OpenAI-compatible JSON 调用生成最多 500 字符的独立问题。改写失败不启动检索，也不保存本轮消息；SSE 返回 `rewrite` 阶段和 `query_rewrite_failed`。
- 改写的问题用于本轮初始检索、证据评估和答案生成，原始问题仍保存在消息历史中。检索轮次、Evidence 和调用预算仍每轮重置。改写调用属于 M4 会话前处理，额外于 Agent 原有 Planner/Answer 请求预算；每次有历史的会话提问至多增加一次远程请求。改写只用于消解意图，不能将历史回答直接当作事实来源，最终答案仍需引用本轮从文档检索的 Chunk。
- 历史引用保留原 Chunk ID；源文档替换或删除后仍按既有引用有效性接口复验，不把旧引用当作当前事实。DOCS-018 的冻结开发样本见 [docs018_v1.json](../evaluation/memory_samples/docs018_v1.json)；它不是完整 Memory Benchmark。

**Assumption**：当前为单进程、单用户本地部署。多进程并发与跨用户访问控制不在 DOCS-017/018 范围内。摘要失败和重复请求的恢复语义留待后续 M4 Issue 决定。
