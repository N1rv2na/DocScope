# MultiHop-RAG 数据合同（DOCS-001）

M0 模型、Qdrant 和费用能力试运行见 [feasibility/README.md](feasibility/README.md) 与 [实测记录](feasibility/result.md)。

本目录包含[固定配置](configs/multihop_rag_v1.json)、[样本 ID 划分清单](manifests/multihop_rag_v1.json)及上游原始数据文件。按项目所有者的最新决定，原始文件纳入本地 Git；[归属和权利说明](datasets/raw/README.md)与文件同目录。DOCS-001 数据合同阶段没有检索指标、模型调用或 Answer/Agent 评测；DOCS-002 只做最小能力试运行。

## 来源与使用边界

- 上游：[yixuantt/MultiHopRAG 数据卡](https://huggingface.co/datasets/yixuantt/MultiHopRAG)；上游[项目仓库](https://github.com/yixuantt/MultiHop-RAG)。数据卡标注 **ODC-BY**；该许可覆盖数据库，并不自动授予第三方新闻正文的独立版权。原始文件在本地 Git 中保留以便复现；公开再分发仍需单独判断文章权利，不能把数据集标注当作已取得所有文章的授权。
- 固定上游修订：`71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82`。官方只发布一个 `train` 划分；这里的 `dev` 和 `test` 是 **DocScope 自建划分**，不是官方测试集。
- 原始 `corpus.json` SHA-256：`20b61b5ab84de84a927420c5d265b7ec8d859ae49980699958a787ade9e4d28f`；`MultiHopRAG.json` SHA-256：`03cfb4926461f868684903aadc8024447bdda5bb3f6804741424cce338515bff`。适配器先核对原始文件字节哈希，再验证字段。

## 输入、ID 与隔离

- 文章要求 `url`、`title`、`source`、`published_at`、`body` 非空且 URL 唯一。在线文章记录只含 `document_id` 与上述五个字段。额外的标注字段不会传到在线记录。
- 题目要求 `query`、`question_type`、`answer`、`evidence_list`。支持事实要求 `url`、`fact`，URL 必须属于已加载语料。`null_query` 的证据列表必须为空；其他三类不能为空。参考答案、问题类型、`fact` 与不可回答标签只留在离线题目记录；未来索引只能接收 `adapt_corpus` 的返回值。
- `document_id` 来源于修订哈希和文章 URL；`sample_id` 来源于修订哈希和经 NFKC、大小写折叠、词语提取后的问题文本。输入顺序变化不改变 ID 或划分。完全相同的规范化问题会报错，避免一个 ID 对应多条题目。
- 划分种子为 `20261025`，目标测试比例为 `0.2`。问题按类型分层，先用词三元组 Jaccard ≥ 0.8 **或**字符三元组 Jaccard ≥ 0.9 将近重复问题连接成组，再按稳定哈希分配整组；每种类型的实际比例可能略偏离目标。开发集用于选型和调参；封存测试集仅供冻结方案后的最终评测。清单公开意味着“封存”是一条**不看测试集调参的实验规程**，不是数据保密承诺。
- 近重复检查是可复现的词面启发式，不能保证识别所有语义改写。后续若人工发现跨组语义重复，应在最终评测前记录并更新数据合同，而不能静默修改清单或挑选结果。

## 实际冻结结果

使用上述官方修订版原始文件运行适配器：609 篇文章、2,556 道题；`dev` 2,045，封存 `test` 511。各类型数量见 JSON 清单。完全重复问题 0 对；近重复问题 345 对、329 组；跨组近重复 0 对。清单的 `audit.near_duplicate_pair_ids` 保存全部近重复样本 ID 对，便于以后与本地离线题目对应并人工复核。这些是数据检查结果，不是检索或问答性能结果。

## 复现

仓库中已有固定原始文件。在仓库根目录运行：

```bash
PYTHONPATH=backend/src python3 -m docscope.evaluation.multihop verify \
  --corpus evaluation/datasets/raw/corpus.json \
  --questions evaluation/datasets/raw/MultiHopRAG.json \
  --config evaluation/configs/multihop_rag_v1.json \
  --manifest evaluation/manifests/multihop_rag_v1.json
PYTHONPATH=backend/src python3 -m unittest discover -s backend/tests -v
```

`verify` 在文件哈希、必需字段、证据文章关联或清单划分不符时返回非零退出码。`freeze` 模式只用于首次生成清单；已有清单不同则拒绝覆盖，以免无意中重划封存集。

## DOCS-008 Dense Baseline（已实测）

固定评测配置是 [dense_baseline_v1.json](configs/dense_baseline_v1.json)：同一 609 篇文章通过现有 Markdown 上传 API 完成标准分块、CPU Embedding 和 Qdrant Dense 索引。使用冻结清单的全部 2,556 道题做离线检索；只有预先在配置中列出的 12 道 dev 题（每种类型 3 道）会调用问答 API。test 题参与检索，但不用于挑选配置或答案子集。配置中保留具体样本 ID，以便 A–E 后续对照使用相同问题。

文章按 URL 的冻结适配器生成离线 ID，上传时用该 ID 作文件名，在线文档 ID 由上传 API 返回并建立离线映射。上传的文件内容只包含文章 `body`；参考答案、`evidence_list.fact` 和题目类型留在离线进程，不进入索引。对于每个标注事实，先 NFKC 规范化、大小写折叠并压缩空白，然后要求它恰好包含于同一篇文章的一个实际 Chunk：零个匹配记 `no_chunk`，多个记 `ambiguous_chunks`，整题只要有失败就不计片段分母。跨 Chunk 事实会列为未映射，不推测它被检索到。抽查 12 道题的离线映射状态：7 道映射、3 道 `no_chunk`、2 道 `null_query`；这是规则检查，不是模型结果。正式报告仍需人工核看部分匹配文本与跨 Chunk 失败案例。

文章级 Recall@5/10 按去重后的标注文章计算，All-Evidence 需要全部标注文章命中；片段级 Fact Coverage@5/10 和 All-Fact 只在整题事实均能唯一映射时计分。`null_query` 不进入有证据指标分母，映射失败只影响片段级分母。全量检索直接使用在线路径的同一本地 Embedding 和 Qdrant Dense 查询，覆盖冻结题目中 13 道超出 Web API 500 字符限制的问题，不截断原题；评测集合只含本轮文章，故查询不发送在线服务所需的 ready ID 过滤。答案子集仍调用当前 `/api/chat` 路径。请求失败会留下失败产物，不计为漏检。逐题输出原始与稳定来源 ID、划分、映射分类、指标和耗时；分别汇总 dev/test 的分母。子集另输出回答、引用、HTTP 状态与逐次 Token、实际模型标识、有效网关配置和耗时。实际账单金额若网关不提供则为 `null`，不能认作零费用。

获授权后，在仓库根目录、Qdrant 1.20.0 和已配置的 LLM 环境下运行：

```sh
PYTHONPATH=backend/src QDRANT_URL=http://127.0.0.1:6333 python -m docscope.evaluation.run_dense \
  --config evaluation/configs/dense_baseline_v1.json \
  --output evaluation/results/dense_baseline_all_v1.json
```

运行创建独立 Qdrant 测试集合及临时源文件目录，结束时清理集合；原始数据哈希与清单在导入前复验。运行失败会把已完成题目和错误写入同目录下的 `.failed.json`，不覆盖成功产物。该命令会把 12 道题的检索证据送往配置的外部 LLM 网关；本次已获用户针对该数据外发和可能费用的明确授权。实际运行产物是 [逐题 JSON](results/dense_baseline_all_v1.json) 与 [简报](results/dense_baseline_report.md)。
