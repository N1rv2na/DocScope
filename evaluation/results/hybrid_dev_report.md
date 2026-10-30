# DOCS-009 同批 Dense/Hybrid 开发集对照

2026-10-30 在本地 CPU Embedding 与 Qdrant 1.20.0 上运行。609 篇文章经现有 Markdown 上传路径索引；冻结 `dev` 的 2,045 道题逐题运行 Dense 和 Hybrid。原始逐题记录见 [JSON](hybrid_dev_v1.json)，固定参数见 [配置](../configs/hybrid_dev_v1.json)。未查询封存 test，未调用远程 LLM。

配置为 `chunk_text` 原生 BM25、英文 `word` 分词及小写、Dense/BM25 各 Top-20 候选、Qdrant 内置 RRF、最终 Top-10。两组使用相同 Chunk、同一问题向量及同一事实映射；查询先后按题交错。此处报告的是**一个固定配置的开发集观测**，并非分词或 RRF 参数的全局最优性证明。

| 指标 | 分母 | Dense @5 / @10 | Hybrid @5 / @10 |
|---|---:|---:|---:|
| 文章 Recall | 1,804 | 0.4959 / 0.6412 | 0.6065 / 0.7347 |
| All-Evidence | 1,804 | 0.1896 / 0.3470 | 0.3016 / 0.4534 |
| Fact Coverage | 1,282 | 0.2859 / 0.3990 | 0.4258 / 0.5351 |
| All-Fact | 1,282 | 0.0679 / 0.1365 | 0.1607 / 0.2605 |

题目映射分类：`mapped` 1,282、`unmapped` 522、`null_query` 241；与 DOCS-008 的 dev 分母一致。逐题 Dense 指标与 [DOCS-008 原始结果](dense_baseline_all_v1.json) 的 dev 行逐一相等；本轮的在线文档 ID 不同，报告使用稳定来源 ID 对齐。

每题检索耗时包含同一次共享 Embedding：Dense 中位数 15.9 ms、P95 18.7 ms；Hybrid 中位数 16.4 ms、P95 19.1 ms。最大值分别约 16.039 s 和 16.036 s，与本地连接超时/重试的量级相符；逐题记录未单独标记异常原因。受这些异常值影响，均值分别为 84.8 ms 和 51.9 ms，不能据此断言 Hybrid 一般比 Dense 快。完整运行约 416.7 s，包含模型加载、摄取、映射和查询。候选数与时延是本机本次观测，非服务承诺。

Hybrid 并非逐题更好：文章 Recall@5 在 592 题提高、153 题下降，其余 1,059 题不变；Fact Coverage@5 在 449 题提高、46 题下降，其余 787 题不变。下降样例 `mhr-q-000d7b377fb68bf153d7` 可在逐题 JSON 中复核；片段映射失败的题不进入 Fact Coverage 分母。

验证：冻结 dev ID 与 2,045 条结果完全一致；两路各题返回不超过 10 个且 Chunk ID 无重复；每题映射状态、成功映射的稳定 Chunk ID 与 DOCS-008 基线一致；Dense 逐题指标完全一致；交错顺序为 1,023/1,022 题。隔离 Qdrant 集合已清理。此次结果只能支持本配置在开发集上的检索比较；Reranking、Agent 和正式封存测试仍待后续 Issue。
