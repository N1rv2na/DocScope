# DocScope 未决问题

> 仅列尚未确认、需要在对应阶段开始前回答的问题。已确认的范围和技术方向见 [design.md](design.md)、[ARCHITECTURE.md](ARCHITECTURE.md) 与 [ADR](adr/)。这里的 **Assumption** 是待验证的临时设想，不是决策。

| 最迟阶段 | 未决问题 | 当前 Assumption / 决定所需证据 |
|---|---|---|
| M3 | 外部 Magpie 顺序路由在额度耗尽、429、流式中断时如何表现？如何可靠记录实际选中渠道并审计 A–E 的路由分布？ | 正式评测已决定使用 `group/docscope-free`；用户验证基础调用/JSON/SSE，DOCS-002 实测两类请求。若网关只返回模型名而无法区分渠道，须如实标记未知并限制严格同渠道结论。 |
| M0 | 标注 `fact` 如何映射到 Chunk；匹配失败、跨 Chunk 事实、指标 K/分母和答案规范化怎样定义？ | 需先用少量样本验证映射和人工复核，再冻结离线评测合同。 |
| M3 | Answer/Agent 固定分层子集、A–E 各实验组的公平预算和最终报告覆盖量是多少？ | DOCS-013 在线 Agent 已固定最多两轮检索、两次模型调用；正式消融组间的对照预算仍须以代表性开发集用量冻结。子集用于公平比较与可复核性，不受项目级累计金额上限驱动。 |
| M1 | Chunk 长度/重叠、PDF 页码和 Markdown 章节映射采用什么具体规则？ | **Assumption**：先用可复现的长度/重叠分块；需通过引用定位和开发集验证。 |
| M3 | 开发集上 Cross-Encoder 重排中位耗时从 Hybrid 的 18.9 ms 增至 406.7 ms，且降低多项检索指标后，Agent 的默认检索配置如何选择？ | DOCS-010 已固定模型和 Top-20 并记录[三组消融](../evaluation/results/rerank_dev_report.md)；M3 应结合回答、多跳质量与预算再决定默认路径，不能预设重排有净收益。 |
| M3 | DOCS-015 的引用内容支持性人工复核采用什么样本规模和判定规则？ | DOCS-012 的依赖型样本已由 Owner 确认；引用 ID 合法性已由程序校验，内容是否支持结论仍须在回答评测中单列复核。 |
| M4 | Checkpointer 版本、完整消息与 Checkpoint 的存储职责、`thread_id` 生命周期、摘要失败恢复如何划分？ | **Assumption**：LangGraph Checkpointer + SQLite；以会话连续性、删除和暂态重置测试决定。 |
