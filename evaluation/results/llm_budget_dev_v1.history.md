# DOCS-023 历史输出截断分析

来源：冻结 dev 子集的 DOCS-016 v3 Magpie-on 结果；这是历史观察，不是 DOCS-023 新配置的效果。
总调用 86，截断 12；复测固定输入 24 条（12 条历史截断、12 条成功对照）。

| 阶段 | 调用 | 截断 | Reasoning 用量可得 | Completion 用量可得 |
| --- | ---: | ---: | ---: | ---: |
| answer | 58 | 9 | 58 | 58 |
| plan_next | 14 | 3 | 14 | 14 |
| plan_once | 14 | 0 | 13 | 13 |

| 样本/组 | 阶段 | 输出上限 | Completion | Reasoning | 耗时秒 |
| --- | --- | ---: | ---: | ---: | ---: |
| mhr-q-62607bbbdf8d4daa782b/D | answer | 2048 | 2048 | 2048 | 15.48 |
| mhr-q-692ecf2907b3b65bc418/B | answer | 2048 | 2048 | 2048 | 16.011 |
| mhr-q-692ecf2907b3b65bc418/C | answer | 2048 | 2048 | 2048 | 16.252 |
| mhr-q-692ecf2907b3b65bc418/E | plan_next | 4096 | 4096 | 4096 | 28.791 |
| mhr-q-71c3f55cd1150d560470/B | answer | 2048 | 2048 | 2048 | 15.96 |
| mhr-q-71c3f55cd1150d560470/D | answer | 2048 | 2048 | 2037 | 16.011 |
| mhr-q-816c6ee52abe20ca42e6/D | answer | 2048 | 2048 | 2048 | 15.37 |
| mhr-q-816c6ee52abe20ca42e6/E | plan_next | 4096 | 4096 | 4096 | 29.654 |
| mhr-q-9059b74afac51ed7cab5/E | plan_next | 4096 | 4096 | 4096 | 29.108 |
| mhr-q-d527c115ed7cfd8f14b1/A | answer | 2048 | 2048 | 2048 | 18.648 |
| mhr-q-d527c115ed7cfd8f14b1/B | answer | 2048 | 2048 | 2048 | 15.427 |
| mhr-q-d527c115ed7cfd8f14b1/C | answer | 2048 | 2048 | 2048 | 15.395 |

截断调用的输出上限均已耗尽；Reasoning 用量占比极高。历史结果不能证明调大上限必然改善。
Prompt 重放只复核结构化完成、Schema 和引用合同，不计算答案语义正确率；不使用封存 test。
E 组首跳证据由保存的候选与所选 Chunk ID 重建，历史结果中所有已保存 Chunk 的 section 为 Document；无法证明字节级 Prompt 与历史完全相同。
历史网关没有暴露实际渠道，费用未知；后续新运行也须单独披露这些限制。
