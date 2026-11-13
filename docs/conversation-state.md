# M4 会话状态边界（DOCS-017/018/019）

DOCS-017 提供会话目录、历史恢复和删除；DOCS-018/019 在现有会话路径增加近期窗口、历史相关查询改写与滚动摘要。

- `POST /api/conversations` 创建服务端 UUID `thread_id`；`GET /api/conversations` 列表，`GET /api/conversations/{thread_id}` 返回完整已完成消息及引用，`DELETE` 删除。
- `POST /api/conversations/{thread_id}/runs/stream` 沿用 Agent SSE 事件合同并增加 `thread_id`。`done` 仅在完整 Run 的消息写入成功后发送。失败 Run 返回 `error`，不写入完成消息。原独立问答接口继续可用。
- `conversations.sqlite3` 保存会话元数据和供 UI 展示的用户/助手消息、引用及结果状态。`checkpoints.sqlite3` 由 `langgraph-checkpoint-sqlite==3.1.3` 保存 LangGraph 图状态；它不是 UI 消息的唯一来源。每轮使用相同 `thread_id`，但在图输入显式重置问题、查询、Evidence、检索轮数、调用预算、回答和停止状态。事实问题每轮重新检索。
- 删除先将会话标记为 `deleting`，此时不再可读取或运行；再删除对应 Checkpoint，最后删除消息和会话元数据。清理失败可重试；服务重启后会继续清理标记为 `deleting` 的会话。单进程中会话 Run 与删除串行化。
- DOCS-018 从当前会话的已完成用户/助手消息中最多读取最近 4 轮，每条助手回答最多 1000 字符，并在序列化窗口超过 6000 UTF-8 字节时丢弃最早的完整轮次。首轮没有历史时不增加模型调用；有窗口时经 OpenAI-compatible JSON 调用生成最多 500 字符的独立问题。改写失败不启动检索，也不保存本轮消息；SSE 返回 `rewrite` 阶段和 `query_rewrite_failed`。
- 改写的问题用于本轮初始检索、证据评估和答案生成，原始问题仍保存在消息历史中。检索轮次、Evidence 和调用预算仍每轮重置。改写调用属于 M4 会话前处理，额外于 Agent 原有 Planner/Answer 请求预算；每次有历史的会话提问至多增加一次远程请求。改写只用于消解意图，不能将历史回答直接当作事实来源，最终答案仍需引用本轮从文档检索的 Chunk。
- 历史引用保留原 Chunk ID；源文档替换或删除后仍按既有引用有效性接口复验，不把旧引用当作当前事实。DOCS-018 的冻结开发样本见 [docs018_v1.json](../evaluation/memory_samples/docs018_v1.json)；它不是完整 Memory Benchmark。
- DOCS-019 在同一 SQLite 库新建 `conversation_summaries` 表，按 `thread_id` 保存摘要文本和已覆盖的完成轮次。只有最近 4 轮之前、尚未摘要的用户问题进入增量摘要模型；历史助手回答不会进入摘要请求。以 `ceil(UTF-8 字节数 / 3)` **估算**这批旧问题的 Token，达到 200 才触发；这不是网关实际 tokenizer 的精确计数。单次摘要输入最多 6000 UTF-8 字节，输出最多 1200 UTF-8 字节，每个用户请求至多一次摘要调用。短于阈值的旧问题暂不进入改写上下文；下次达到阈值后才压缩。
- 成功摘要经 JSON/长度校验后保存，再作为改写器的主题与指代线索；原始历史消息保持完整。摘要失败（包括模型、格式和数据库写入错误）仅发 `summary` 失败阶段，本轮回退到上次有效摘要和近期窗口，后续请求可重试；不保存失败摘要。删除会话时摘要随会话行级联清理。摘要既不进入检索索引，也不进入 Answer 或 Planner 的 Evidence。
- DOCS-019 的固定合成开发样本见 [docs019_v1.json](../evaluation/memory_samples/docs019_v1.json)。三组独立 Memory 对照为仅当前问题、Window+Rewrite、Window+Summary+Rewrite；报告单独记录输入配置、逐题查询/答案/引用、错误及历史污染。这一小样本不代表完整产品效果或封存 test 成绩。

**Assumption**：当前为单进程、单用户本地部署。多进程并发与跨用户访问控制不在 DOCS-017/018/019 范围内。SSE 断连后重复提交仍是新 Run，不提供跨请求幂等；已成功保存的摘要不会因重复提交而重新摘要同一批旧轮次。
