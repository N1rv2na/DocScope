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

## DOCS-009 Dense/Hybrid 同批开发集对照

[固定配置](configs/hybrid_dev_v1.json)只允许 `dev` 划分，使用 DOCS-001 冻结清单与同一批 609 篇文章。上传入口、分块、CPU Embedding、文章与事实片段映射规则均沿用 DOCS-008；同一题先计算一次向量，再分别查询 Dense Top-10 和 Qdrant 原生 BM25 + Dense 双路候选各 Top-20、RRF 最终 Top-10。查询顺序按题交错，减少缓存预热造成的方向性偏差。逐题记录两路的文章级/片段级 @5/@10、稳定来源 ID、事实到 Chunk 的映射及检索耗时（每路包含共享的 Embedding 耗时）。索引或检索阶段的异常写入 `.failed.json`，不作为零分。此运行不调用远程 LLM，也不查看封存 test 的结果。

在仓库根目录、Qdrant 1.20.0 和已缓存的本地 Embedding 模型下运行：

```sh
PYTHONPATH=backend/src QDRANT_URL=http://127.0.0.1:6333 python -m docscope.evaluation.run_hybrid \
  --config evaluation/configs/hybrid_dev_v1.json \
  --output evaluation/results/hybrid_dev_v1.json
```

脚本创建临时数据目录及隔离 Qdrant 集合，完成后删除。配置中 `word`、小写和 Qdrant 内置 RRF 是本轮固定选择；结果只证明这一配置在开发集上的观测，不能声称是全局最优或封存 test 的结果。

本次授权运行的产物：[逐题 JSON](results/hybrid_dev_v1.json)、[简报](results/hybrid_dev_report.md)。

## DOCS-010 三组检索消融

[固定配置](configs/rerank_dev_v1.json)在同一冻结 `dev` 集和同一隔离 Qdrant 集合中比较 Dense、Hybrid、Hybrid+Cross-Encoder。每题只编码一次问题，Dense 与 Hybrid 查询顺序按题交错，待两路查询完成后才对 Hybrid RRF Top-20 候选进行 CPU 重排；三组最终分别以 Top-5/10 计分。重排保留原始 RRF 排名和分数，另外记录 Cross-Encoder 原始打分；逐题时延包含共享 Embedding，重排阶段时延另列。模型与修订固定为 M0 已冒烟的 `cross-encoder/ms-marco-MiniLM-L6-v2` / `233902d25c440f23af6f7d6e94d2946bac0bee0a`。本轮无远程 LLM 调用，不读取封存 test 结果。

在仓库根目录、Qdrant 1.20.0 和本地模型可用时执行：

```sh
PYTHONPATH=backend/src QDRANT_URL=http://127.0.0.1:6333 python -m docscope.evaluation.run_rerank \
  --config evaluation/configs/rerank_dev_v1.json \
  --output evaluation/results/rerank_dev_v1.json
```

脚本按现有评测合同核对原始文件哈希和 dev 清单，摄取时仅发送文章正文。生成逐题质量/时延和映射失败记录，结束时删除隔离集合；运行失败写 `.failed.json`。Top-20 是本 Issue 的固定候选预算，后续是否使用该配置应依据实际质量与 CPU 代价判断，不能预设提升。

本次授权运行产物：[逐题 JSON](results/rerank_dev_v1.json)、[消融报告](results/rerank_dev_report.md)。在本轮 dev 检索中，C 比 B 更慢且多项指标下降；报告保留改善与退化案例，不能据此宣称 Reranking 带来净增益。

## DOCS-015 Agent 固定子集评测

[固定配置](configs/agent_answer_dev_v1.json)沿用 DOCS-008 在运行前确定的 12 道 `dev` 题 ID（四类各 3 道），另有 2 道自建无证据题；依赖样本独立运行、单独计数。只把 609 篇文章的正文上传到在线索引，Agent 请求只包含问题；参考答案、证据事实、问题类型和评分标签只在离线进程中使用。封存 `test` 不运行。每个 Agent Run 的上限是两轮检索和两次模型调用；超出时评测直接失败，错误响应不计作拒答。

在仓库根目录、Qdrant 1.20.0、本地 Embedding 模型和经授权的 LLM 环境已就绪时运行：

```sh
PYTHONPATH=backend/src QDRANT_URL=http://127.0.0.1:6333 .venv/bin/python -m docscope.evaluation.run_agent_answer \
  --config evaluation/configs/agent_answer_dev_v1.json \
  --output evaluation/results/agent_answer_dev_v1.json
```

脚本使用独立临时 Qdrant 集合和临时源目录；额外的依赖样本使用第二个隔离集合，以免污染 MultiHop-RAG 子集。执行前复核原始数据哈希、清单、12 道题 ID、模型修订及单次运行预算。异常会写 `.failed.json`，不覆盖成功结果。逐题 JSON 记录响应、检索轨迹、引用、错误、调用次数、实际响应模型、完成原因、Token 与耗时。路由成员列表来自项目所有者提供的配置，仅作为声明记录；若网关没有返回实际渠道或费用，则明确记为 `null` 和未知，不能推断渠道或零费用。正式 A–E 消融仍须另用冻结的单一模型及渠道。
为保护已完成人工审查的记录，成功结果或审查表已存在时，脚本拒绝覆盖；复跑时请给 `--output` 一个新文件名。

汇总分别给出可回答题错误拒答率、`null_query` 加自建无证据题的适当拒答率、支持文章检出率、引用 ID 合法率，均带分子和分母。`null_query` 和自建无证据题不进入支持文章检出分母。Exact Match 和 token F1 是自动诊断，不能代替引用事实支持性审查；模型错误保留在题目分母中，并单列错误数。依赖样本记录实际首跳桥接事实、来源 Chunk、第二跳查询和第二跳新增来源；如果真实运行没有形成符合样本方向的第二跳，`dependency_observed` 为 `false`，不能把预期路径当作实测。

同名 `.review.json` 是逐条引用的人工审查表。审查者应针对每条引用核对 Chunk 文本是否直接支持回答，并填写 `support`（`supported` / `unsupported` / `uncertain`）、`reviewer` 和 `rationale`；依赖样本另核对桥接事实、第二跳查询和支持来源。所有引用明确核查之前，支持率保持未知。此结果仅代表 14 道固定题和 1 个独立依赖样本，不是全量 Answer Benchmark 成绩。

本次获授权的真实运行产物见[逐题 JSON](results/agent_answer_dev_v1.json)、[人工审查表](results/agent_answer_dev_v1.review.json)和[结果报告](results/agent_answer_dev_report.md)。该次运行的依赖样本在第一跳同时检出两篇来源，因而未发生第二跳；报告明确保留这一未达成项。
本轮实际生成的四条引用已由项目所有者逐条确认支持对应回答，审查表记录了结论与依据；这不代表未回答问题的引用质量。

## DOCS-016 A–E 固定 dev 子集对照

[冻结配置](configs/ablation_dev_v2.json)沿用 DOCS-015 的 12 道 `dev` 题与 2 道自建无证据题、相同数据清单和同一批标准分块。一次导入 609 篇文章正文到隔离 Qdrant 集合，五组交错运行每道题；参考答案、证据标注和题目类型只在离线评分侧使用，封存 `test` 不参与。

- A：Dense Top-5；B：Qdrant BM25 + Dense 的 RRF Top-5；C：Hybrid Top-20 经固定本地 Cross-Encoder 重排至 Top-5。
- D：在看到任何检索证据之前用同一 LLM 一次规划第二查询；第一查询固定为原问题。两次查询均使用 C 的检索链，最终将去重候选重新排序到最多 5 条证据。
- E：复用现有有界 LangGraph Agent，但离线注入 C 的检索链；规划器可在首跳证据之后决定是否提出第二查询。最终证据同样最多 5 条。Web Agent 的默认 Hybrid 路径不因本评测改变。

D/E 同享最大两次检索、两次模型调用、每轮最多 20 个待重排候选和最终 5 条证据；实际使用量允许不同，报告分别给出自然运行与实际预算完全匹配的 D/E 子集。无匹配题时不作净增益推断。A–E 使用同一请求模型和路由组配置；实际渠道若网关不返回则记为未知，不宣称严格同渠道控制。报告中的已知 Token 仅累计提供用量的调用，未知费用不当作零费用。质量分母包含失败和拒答，只有整题事实映射成功才进入片段级事实覆盖分母。DOCS-024 的在线 Agent 默认可额外重试一次；此处既有 A–E runner 显式设置 `extra_model_requests=0`，因此历史结果与原两次请求预算保持一致。后续若评测启用恢复，须冻结新配置、重跑并单独报告额外请求、Token 与渠道差异，不能与旧结果直接称为同预算对照。

首次真实运行使用了[原始 v1 配置](configs/ablation_dev_v1.json)的线上默认输出上限（回答 400、规划 2048 Tokens）。[v1 逐题结果](results/ablation_dev_v1.json)中 85 次模型调用有 33 次以 `finish_reason=length` 结束，D/E 无实际预算匹配且均非错误的题目；[v1 报告](results/ablation_dev_v1.md)保留这次失败诊断。v2 在**离线评测**中将回答/规划上限统一提高至 2048/4096 Tokens，五组同设，线上 API 不变。两版的 LLM 参数不同，不应将跨版本分数差解释成检索策略收益。

已获授权完成的[v2 逐题结果](results/ablation_dev_v2.json)与[v2 报告](results/ablation_dev_v2.md)：70/70 题次、89 次模型调用，7 次输出截断、3 次请求超时。A/B/C/D/E 在 9 道可回答题上的答案 EM 分别为 2/9、1/9、0/9、1/9、0/9；D/E 有 5/14 道实际预算匹配，二者在该子集均为 0/5。五组没有观察到 Agent 对 Dense 的 EM 提升；这不是全量 Benchmark，也不证明动态规划无效。所有模式的已映射事实最终上下文覆盖均为 0/7，且长答案的严格 EM 可能低估语义正确性。网关未提供可识别的实际渠道和账单费用，报告只给出响应模型 ID、可得 Token 与耗时，不宣称严格同渠道控制或零费用。

后续对本轮产物的[回答语义复核](results/ablation_dev_v2.semantic_review.md)和[拒答、错误排查](results/ablation_dev_v2.failure_analysis.md)分开记录核心答案是否正确、标注来源是否覆盖，以及第二跳新增证据是否进入最终上下文；两份审查均未修改原始结果或重新运行模型。

获得真实模型运行授权后，在仓库根目录以 Qdrant 1.20.0、已缓存的 CPU 模型和有效 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 环境执行 v2：

```sh
PYTHONPATH=backend/src QDRANT_URL=http://127.0.0.1:6333 .venv/bin/python -m docscope.evaluation.run_ablation \
  --config evaluation/configs/ablation_dev_v2.json \
  --output evaluation/results/ablation_dev_v2.json
```

成功时生成同名 JSON 与 Markdown 报告；已有结果不会被覆盖，复跑须指定新输出文件名。运行失败写 `.failed.json` 并清理隔离集合，不把失败题伪装成漏检或拒答。

### DOCS-016 检索候选与第二跳去向诊断

新运行的逐题 JSON 会在每轮 `retrievals[].candidates` 记录 Qdrant RRF 候选的原始顺序、分数、文章来源、完整 Chunk、Cross-Encoder 分数和是否进入该轮前 5；D/E 的 `final_ranking` 记录两轮合并后的完整重排顺序、来源轮次和是否进入回答上下文。旧版 v1/v2 结果没有这些字段，不能把旧运行中被丢弃候选的正文或分数倒填成事实。

已获授权完成的[检索重放 JSON](results/ablation_dev_v2.retrieval_replay.json)和[定位报告](results/ablation_dev_v2.retrieval_replay.md)记录了两道典型题的 D/E 候选与重排去向。仅重放旧 v2 **已保存的查询**时，脚本会重新索引相同的 609 篇文章，运行 CPU Embedding、Qdrant 和 Cross-Encoder；不调用 Magpie，也不生成新答案。输出是一次**新检索运行**，在线文档和 Chunk UUID 会改变，应按冻结来源 ID、文章与文本比较，不把它冒充原始 v2 轨迹。

```sh
PYTHONPATH=backend/src QDRANT_URL=http://127.0.0.1:6333 .venv/bin/python -m docscope.evaluation.run_ablation_replay \
  --source evaluation/results/ablation_dev_v2.json \
  --output evaluation/results/ablation_dev_v2.retrieval_replay_new.json \
  --sample-id mhr-q-f4da8c5620114c384e0b \
  --sample-id mhr-q-62607bbbdf8d4daa782b
```

逐题 `support_article_path` 区分标注来源未进入融合候选、被单轮重排筛掉、被最终合并重排筛掉或已保留。候选和标注的对照只在离线侧进行；标注不会发送给检索模型。隔离 Qdrant 集合在完成或报错后清理，已有输出文件不会被覆盖。

### DOCS-016 第二跳证据保留实验（离线 v3）

[已确认的实验设计](../docs/superpowers/specs/2026-11-04-docs016-second-hop-evidence-slot-design.md)仅改变离线 D/E 的最终证据选择：两轮各自 Top-5 去重后，用原问题给合并候选打分；若第二轮有相对首轮新增的 Chunk，先为该轮最高名次的新增 Chunk 留一个位置，再按原问题得分填满最多 5 条。无第二轮或无新增时沿用旧规则。A/B/C、线上 Web Agent、检索与模型调用上限不变。新运行用[独立 v3 配置](configs/ablation_dev_v3.json)；v1/v2 配置和结果不覆盖。

在**已保存的同一批检索候选**上、不调用本地模型或 Magpie 的对照命令：

```sh
PYTHONPATH=backend/src .venv/bin/python -m docscope.evaluation.compare_ablation_selection \
  --replay evaluation/results/ablation_dev_v2.retrieval_replay.json \
  --config evaluation/configs/ablation_dev_v3.json \
  --output evaluation/results/ablation_dev_v3.selection_comparison_new.json
```

本次[同候选 JSON](results/ablation_dev_v3.selection_comparison.json)及[摘要](results/ablation_dev_v3.selection_comparison.md)只覆盖已获授权重放的两道题、四条 D/E 路径：第二轮新增 Chunk 最终保留数由 0/15 变成 4/15；Canelo 的 D/E 与 Sportsbooks 的 D 恢复了缺失的**文章**，Sportsbooks 的 E 仍缺目标文章。四条路径的标注事实都不能唯一映射到单个标准 Chunk，故片段级覆盖不可计。这是**同一批已保存候选**上的规则对照，不能单独说明回答质量。

另获授权完成的[v3 逐题结果](results/ablation_dev_v3.json)与[v3 报告](results/ablation_dev_v3.md)包含 609 篇文章、14 道固定 dev 题、70/70 个 A–E 题次和 84 次 Magpie 调用。D/E 两轮实际发生于 10/14 与 4/14 道题，其中预留 Chunk 原本在全局前 5 之外的情况分别有 9 与 4 道；Canelo 题的 D 路径预留原全局第 7 位的第二轮 Chunk，最终覆盖两篇支持文章，并回答且引用两篇原文。该题 E 只有一轮，仍拒答。整轮有 24/70 个错误题次，其中请求异常含 15 次 502 和 1 次超时，另有 8/84 次模型输出截断；D/E 实际预算匹配且非错误的仅 3/14 道。实际渠道和账单均不可辨，v3 与 v2 的规划查询、网关返回也不同，**不能把跨轮次 EM 或拒答差异归因于预留规则**，也不能宣称严格同渠道的 Agent 净收益。封存 test 未运行。

项目所有者随后确认上述运行时 Magpie 未启动，并在启动后授权用**同一 v3 配置**完整重跑。新[逐题 JSON](results/ablation_dev_v3_magpie_on_20261104.json)、[自动报告](results/ablation_dev_v3_magpie_on_20261104.md)及[核心答案语义复核](results/ablation_dev_v3_magpie_on_20261104.semantic_review.md)均单独保存，不覆盖首次 v3。预检 `GET /models` 为 HTTP 200，一笔额外的 Chat Completions 预检以 `finish_reason=stop` 完成；正式评测仍只统计其自身 86 次调用。正式运行完成 70/70 个题次，错误由 24/70 降为 14/70，原先 15 次 502 本轮为 0；剩余错误为 12 次输出达到上限、1 次请求超时、1 次规划结构不合格。D/E 全部支持文章覆盖分别为 5/9 与 2/9，但实际预算匹配且两组均非错误的题目仅 2/14。17 个已返回回答的**核心结论**经人工与参考答案比较均一致；其余可回答题仍有拒答或错误，附加断言及引用支持性尚未逐句审查。实际渠道和账单依旧不可辨，不能宣称严格同渠道的净增益或把两次真实运行的差异归因于预留规则。封存 test 未运行。

复现新的完整真实评测需另行取得模型运行授权；使用 `run_ablation --config evaluation/configs/ablation_dev_v3.json --output <新文件>`，不得覆盖现有结果。旧 v2 已保存候选不完整，不能用它倒填新规则的原始运行结果。

## DOCS-023 LLM 输出预算诊断与固定提示输入对照

[历史分析](results/llm_budget_dev_v1.history.md)复算已保存的 DOCS-016 v3 Magpie-on 结果：86 次调用中 12 次截断，Answer 9/58、证据驱动 Planner 3/14；12 次均报告输出达到上限，Reasoning Token 占据了几乎全部 Completion。历史数据不能确定提高上限后是否改善。

[冻结复测配置](configs/llm_budget_dev_v1.json)从上述结果中预先选出全部 12 条截断调用和 12 条完成调用，覆盖固定 dev 子集的 14 道题。每条输入在三组各运行一次：历史指令与 2048/4096 上限、相同指令与 3072/5120 上限、新增简短 JSON 输出约束与 3072/5120 上限。各组的 Answer/Planner 请求超时统一为 60 秒，便于分别观察预算和指令的变化。`source_sha256` 防止静默改动历史输入。复测只调用 LLM，不重建 Qdrant，也不发送参考答案、支持 `fact` 标注或封存 test；只验证结构、引用 ID 和规划字段，不把结构成功当作语义正确。

在仓库根目录先只读复算历史数据：

```sh
PYTHONPATH=backend/src .venv/bin/python -m docscope.evaluation.llm_budget --history-only
```

复现真实复测须重新取得模型运行授权，并先配置有效的 `LLM_BASE_URL`、`LLM_API_KEY` 与 `LLM_MODEL=group/docscope-free`。授权后运行：

```sh
PYTHONPATH=backend/src .venv/bin/python -m docscope.evaluation.llm_budget \
  --config evaluation/configs/llm_budget_dev_v1.json \
  --output evaluation/results/llm_budget_dev_v1.json
```

程序拒绝覆盖已有输出，运行前检查不超过 72 次请求；生成逐调用 JSON 与同名 Markdown 报告。实际渠道和账单若网关不返回，一律记为未知。12 条历史失败被刻意过采样，不能把该子集的截断率推广为完整 A–E 的总体截断率；E 组首跳元数据由已保存候选重建，不保证与历史 Prompt 字节级一致。不同运行时间窗口及无法辨识的渠道也限制因果解释。

经项目所有者单独授权，已完成[本轮逐调用 JSON](results/llm_budget_dev_v1.json)与[预算对照报告](results/llm_budget_dev_v1.md)：三组各 24 次，历史预算组结构成功 23/24（1 次请求超时），较高预算两组各 24/24；三组均无截断。23 条两组均成功且用量可得的配对输入，原预算与较高预算分别消耗 16,057 和 27,051 Completion Tokens。提高到 3072/5120 没有观察到截断收益，输出 Token 却更多；新增简短 JSON 指令也没有观察到结构成功率提升。线上默认因此选用已在历史离线评测使用的 Answer 2048、Planner 4096 Tokens，并将两阶段请求超时设为本次对照使用的 60 秒；不是保证零截断，且较 v1 的低上限增加了潜在用量和等待时间。当前复测只检验结构、来源 ID 和规划字段，不评判答案语义，也不能从一次未知渠道运行推断严格的因果收益。

项目所有者再次授权后，使用同一批 24 条固定输入补跑**当前线上默认组合**（新版指令、Answer 2048、Planner 4096、两阶段 60 秒超时），每条只调用一次，无自动重试。运行前检查默认值、dev 来源、样本数和输出文件不存在；`--online-default-only` 将请求数锁定为 24。结果见[逐调用 JSON](results/llm_budget_online_default_dev_v1.json)与[单组报告](results/llm_budget_online_default_dev_v1.md)：结构成功 **23/24**，其中 Answer 18/18、Planner 5/6；1 次 Planner 请求在约 60 秒超时；**0 次截断**。成功调用已知 Completion 共 22,542 Tokens、Reasoning 共 21,240 Tokens；超时调用用量未知。24 条与先前运行的输入 ID 完全一致；新版指令的 Prompt 哈希与先前较高预算新版指令组 24/24 一致。跨运行配对的 22 条共同成功输入，历史指令同预算与新版指令同预算分别消耗 15,426、20,532 Completion Tokens；但运行时间不同、实际 Magpie 渠道未知，**不能将差异归因于指令**。本次结果证实默认组合能在多数固定输入上完成结构化输出，也显示 60 秒内仍可能超时；它不代表完整 Agent 成功率或答案语义正确率。

已获授权的默认组合补跑命令如下；重新执行必须指定未使用的新输出路径并重新取得模型运行授权：

```sh
PYTHONPATH=backend/src .venv/bin/python -m docscope.evaluation.llm_budget \
  --online-default-only --config evaluation/configs/llm_budget_dev_v1.json \
  --output evaluation/results/llm_budget_online_default_dev_v1.json
```

在本轮正式复测前，测试隔离缺陷曾在受限环境发起 75 次本地网关请求尝试，均返回 `request_failed`，未取得可用模型响应；它们不计入上述正式 72 次结果，网关是否接收或计费不可核。随后修复了测试隔离，增加执行前 72 次硬上限，并在重新授权后运行正式对照。
