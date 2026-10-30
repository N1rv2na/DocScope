# ADR 0001：在同一 Qdrant Chunk 集合上实现混合检索

状态：Accepted（DOCS-009 已实现 BM25/RRF；DOCS-010 已接入本地 Cross-Encoder，开发集效果见消融报告）<br>
依据：[design.md §5–6.2](../design.md)、审查决策 Q5。

## Context

DocScope 需要同时展示 Dense、BM25、RRF 和 Reranking，并支持 M1 起的文档重传替换与删除。若 Dense 与 BM25 使用不同的文档身份或独立索引，更新、删除、引用和消融配置都要额外同步。Qdrant 已是确定的检索存储。

## Decision

在 Qdrant 的同一批 Chunk 上保存 Dense 向量与原文 payload；M2 为原文建立原生全文 BM25 评分索引，以 RRF 融合两路排名，再由本地 Cross-Encoder 重排。M0 固定并检查支持此能力的 Qdrant 服务端/客户端版本；当前设计依据要求支持全文 BM25 的版本。具体英文文本处理、RRF 参数和候选量由开发集决定。

## Alternatives

- **Qdrant BM25 稀疏向量**：仍在单库中，但摄取/查询时要保持稀疏表示配置一致，当前目标没有证明这部分复杂度有必要。
- **独立 BM25 索引**：实现选择更自由，但重传、删除及 Chunk ID 对齐要维护两套状态。
- **仅 Dense**：可作为 M1 基线，不能满足已确定的 Hybrid 展示目标。

## Consequences

- 同一 Chunk 身份贯穿检索、引用和评测，降低文档生命周期同步风险。
- 依赖支持原生全文 BM25 的 Qdrant 版本与正确的英文文本处理；M0 必须做兼容性验证。
- 全文索引仍消耗内存/磁盘，Hybrid 不保证优于 Dense；以 M2 消融和延迟数据判断收益。
- 中文支持不由此决策自动获得，仍属于后续范围。
