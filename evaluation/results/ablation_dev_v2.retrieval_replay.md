# DOCS-016 v2 第二跳候选去向：本地检索重放

## 运行边界

本次获得授权后，用冻结的 609 篇 `dev` 文章、v2 已保存的 D/E 查询，重新建立隔离 Qdrant 1.20.0 集合，并运行真实 CPU Embedding `all-MiniLM-L6-v2` 与 Cross-Encoder `ms-marco-MiniLM-L6-v2`。只选两道曾出现第二跳证据消失的有答案题，各重放 D/E 两组；每轮保留完整 Qdrant RRF Top-20、逐候选重排分数和单轮 Top-5，并记录两轮合并后完整重排顺序。**未调用 Magpie、未重新生成答案、未读取封存 test**。原始数据与模型精确修订见[机器记录](ablation_dev_v2.retrieval_replay.json)。

这是一次**新的检索运行**，不是 v2 原始运行的补写轨迹。在线文档 ID 和 Chunk ID 已重新生成；结论按冻结文章 ID 与文本核对。不能宣称原始 v2 每个被丢弃 Chunk 的分数与这次完全相同。

## 目标文章来源在哪里消失

| 题目与组别 | 标注中缺失的来源 | 第二轮候选与单轮重排 | 最终合并结果 | 丢失位置 |
| --- | --- | --- | --- | --- |
| Canelo / Benavidez，D | CBSSports.com | 来源进入 RRF Top-20；两个 Chunk 排到单轮第 2、4 | 两个 Chunk 经原问题重排为 −3.778、−5.493，均低于最终第 5 的 −2.860 | 两轮合并后的重排 |
| Canelo / Benavidez，E | CBSSports.com | 来源进入 RRF Top-20；四个 Chunk 排到单轮前 5，其中前两名为 3.756、3.652 | 来源在合并后最高仅 −3.778，低于最终第 5 的 −2.860 | 两轮合并后的重排 |
| Sportsbooks / Rookie of the Year，D | *The Sporting News* 的 Rookie of the Year Odds 文章 | 来源进入 RRF Top-20；三个 Chunk 排到单轮第 1、2、4 | 来源合并后最高 −2.879，低于最终第 5 的 −2.499 | 两轮合并后的重排 |
| Sportsbooks / Rookie of the Year，E | 同上 | 第二轮查询仅为 `sportsbooks`；目标来源排在 RRF 候选第 16、20，Cross-Encoder 得分 3.663、2.275，均低于该轮第 5 的 4.100 | 目标来源没有进入单轮 Top-5，因此不参与最终合并 | 第二轮单轮重排 |

这四条路径中，缺失的标注来源**均已进入 Qdrant 融合候选**，所以这两道题不能归为“Qdrant 完全找不到目标文章”。Canelo 的 D/E 及 Sportsbooks 的 D 是最终合并环节丢失；Sportsbooks 的 E 则更早丢失。后者的第二查询过宽，是一个可观察到的伴随因素，不能单凭本次记录证明它是唯一原因。

四条重放记录合计产生 15 个相对第一轮新增、已进入第二轮 Top-5 的 Chunk，**0 个进入最终 Top-5**。Canelo 的最终五条均来自 Essentially Sports。代码路径先按各轮查询重排，再用**原问题**给两轮合并候选重新打分并截取五条；重放分数显示，原问题重排将上述第二跳目标 Chunk 压到截断线以下。本次定位没有改变该选择策略，也没有证明某个来源中的单一 Chunk 足以支持完整答案；部分标注事实跨相邻 Chunk。

`support_article_path` 按**文章**判断是否保留：同一来源只要有任一 Chunk 留在最终上下文，就标为 `retained`。具体事实是否被保留仍须核对 `retrievals[].candidates[].chunk_text` 与 `final_ranking[].chunk_text`，不能把文章级覆盖直接当作事实级正确率。

## 可核验记录

- [完整 JSON](ablation_dev_v2.retrieval_replay.json)：原始 v2 文件 SHA-256、冻结模型修订、两轮查询与全部候选、各轮与最终分数、来源路径、运行时间。
- [原始 v2 逐题结果](ablation_dev_v2.json)：仅含原运行的最终证据和已选中的单轮证据；本报告未更改该文件。
- [此前拒答和错误诊断](ablation_dev_v2.failure_analysis.md)：说明为何需要记录候选和重排去向。

本地一致性检查确认四条记录各有两轮、每轮 20 个候选；单轮和最终 `selected_rank` 均与实际证据 ID 对应，原始 v2 文件 SHA-256 一致。隔离 Qdrant 集合已清理。
