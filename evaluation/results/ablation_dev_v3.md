# DOCS-016 A–E 固定 dev 子集消融报告

本轮使用同一 609 篇文章和 14 道固定子集题；结果仅针对该子集，**非全量 Benchmark**，封存 test 未参与。
运行窗口 UTC：2026-11-04T14:48:55.110914+00:00 — 2026-11-04T15:02:28.648925+00:00。
请求路由：`group/docscope-free`；组成员来自所有者声明，运行时未验证组配置。
D/E 最终合并策略：`reserve_second_hop_one`；第二跳预留仅影响 D/E 的最终证据选择。
离线评测调用上限：回答 2048 Tokens，规划 4096 Tokens；五组共享该配置。若与线上默认值不同，本报告仅代表离线配置。

## 实际运行配置与结果

A=Dense；B=Hybrid RRF；C=Hybrid+Cross-Encoder；D=C+问题阶段一次规划；E=C+证据驱动迭代。
所有组使用同一文章切分、Embedding、LLM 请求配置及最终最多 5 条证据。D/E 各最多两轮检索、两次模型调用，单轮检索最多 20 个待重排候选。

| 组 | 题数 | 答案 EM/可回答题 | 适当拒答 | 错误拒答 | 全部支持文章 | 全部映射事实 | 引用 ID 合法 | 错误 | 检索/LLM 调用 | 平均延迟 s | 已知输入/输出 Token | 账单 CNY |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 14 | 0/9 | 4/5 | 3/9 | 1/9 | 0/7 | 10/10 | 3 | 14/14 | 4.637 | 17986/5418 | 未知 |
| B | 14 | 0/9 | 4/5 | 2/9 | 0/9 | 0/7 | 5/5 | 6 | 14/14 | 6.489 | 18744/7995 | 未知 |
| C | 14 | 0/9 | 4/5 | 2/9 | 1/9 | 0/7 | 8/8 | 5 | 14/14 | 5.964 | 18879/7479 | 未知 |
| D | 14 | 0/9 | 3/5 | 1/9 | 3/9 | 2/7 | 12/12 | 5 | 20/24 | 11.98 | 19185/15694 | 未知 |
| E | 14 | 2/9 | 4/5 | 2/9 | 1/9 | 0/7 | 8/8 | 5 | 18/18 | 14.797 | 24999/18787 | 未知 |

答案 EM 的分母包含所有可回答题；错误和拒答计为未命中。`null_query` 与自建无证据题不进入支持文章/事实分母。事实映射失败单列，不作为事实检索漏检。答案 EM 是严格字符串匹配，长答案即使语义正确也可能未命中；不能把 EM 当作人工事实准确率。Token 合计仅覆盖网关报告用量的调用；未知账单不等于零费用。

## D/E 相近预算对照

两组采用相同**最大**预算：每题最多 2 轮检索、2 次模型调用，每轮最多 20 个重排候选，最终最多 5 条证据。
实际预算完全匹配且两组均非错误的题目：3/14。匹配规则要求检索/模型调用数、已得候选数和最终证据数均相同。
匹配题 ID：mhr-q-7e6673c52727a5c37faf, mhr-q-9059b74afac51ed7cab5, mhr-q-a70bac4fbd4e8ee99073。
匹配题答案 EM：D 0/3，E 2/3。样本量不足时不推断动态策略净增益。

| 样本 ID | D 检索/模型/候选/证据 | E 检索/模型/候选/证据 | 匹配 |
| --- | --- | --- | --- |
| mhr-q-62607bbbdf8d4daa782b | 0/1/0/0 | 1/1/20/5 | 否 |
| mhr-q-66a99ee476b80008b802 | 0/1/0/0 | 1/1/20/5 | 否 |
| mhr-q-692ecf2907b3b65bc418 | 0/1/0/0 | 1/1/20/5 | 否 |
| mhr-q-71c3f55cd1150d560470 | 2/2/40/5 | 1/1/20/5 | 否 |
| mhr-q-7e6673c52727a5c37faf | 2/2/40/5 | 2/2/40/5 | 是 |
| mhr-q-816c6ee52abe20ca42e6 | 2/2/40/5 | 1/1/20/5 | 否 |
| mhr-q-9059b74afac51ed7cab5 | 2/2/40/5 | 2/2/40/5 | 是 |
| mhr-q-9a4d7197bf10c336946a | 2/2/40/5 | 1/1/20/5 | 否 |
| mhr-q-a70bac4fbd4e8ee99073 | 2/2/40/5 | 2/2/40/5 | 是 |
| mhr-q-c81a29ee075e961b2eb0 | 0/1/0/0 | 1/1/20/5 | 否 |
| mhr-q-d527c115ed7cfd8f14b1 | 2/2/40/5 | 2/2/40/5 | 否 |
| mhr-q-f4da8c5620114c384e0b | 2/2/40/5 | 1/1/20/5 | 否 |
| docs015-fictional-telescope-93457 | 2/2/40/5 | 1/1/20/5 | 否 |
| docs015-fictional-mission-93457 | 2/2/40/5 | 1/1/20/5 | 否 |

## 路由、费用与失败分析

- A：实际响应模型 {"unknown": 3, "group/docscope-free": 11}；实际渠道 {"unknown": 14}；用量可得 11/14 次。
- B：实际响应模型 {"unknown": 3, "group/docscope-free": 11}；实际渠道 {"unknown": 14}；用量可得 11/14 次。
- C：实际响应模型 {"unknown": 3, "group/docscope-free": 11}；实际渠道 {"unknown": 14}；用量可得 11/14 次。
- D：实际响应模型 {"unknown": 3, "group/docscope-free": 21}；实际渠道 {"unknown": 24}；用量可得 21/24 次。
- E：实际响应模型 {"unknown": 4, "group/docscope-free": 14}；实际渠道 {"unknown": 18}；用量可得 14/18 次。
- 渠道未知或实际模型/渠道未全程一致，**不能宣称严格同渠道控制的质量差异**。
- 错误题次：24/70。
- 相对 A 的答案 EM 命中数变化：B +0/9；C +0/9；D +0/9；E +2/9。仅是固定子集观察值，不能据此声称严格同渠道的因果效果。
- 模型输出截断：8/84 次；请求异常类型：{"InternalServerError": 15, "APITimeoutError": 1}。错误题次保留在全部题目分母中。
- 全部映射事实在最终上下文中的覆盖率见上表；覆盖不足可能限制回答质量，但该指标不能单独定位每道题的失败原因。
  - mhr-q-62607bbbdf8d4daa782b/A：model_failed。
  - mhr-q-62607bbbdf8d4daa782b/B：model_failed。
  - mhr-q-62607bbbdf8d4daa782b/C：model_failed。
  - mhr-q-62607bbbdf8d4daa782b/D：planner_failed。
  - mhr-q-62607bbbdf8d4daa782b/E：planner_failed。
  - mhr-q-66a99ee476b80008b802/A：model_failed。
  - mhr-q-66a99ee476b80008b802/B：model_failed。
  - mhr-q-66a99ee476b80008b802/C：model_failed。
  - mhr-q-66a99ee476b80008b802/D：planner_failed。
  - mhr-q-66a99ee476b80008b802/E：planner_failed。
  - mhr-q-692ecf2907b3b65bc418/A：model_failed。
  - mhr-q-692ecf2907b3b65bc418/B：model_failed。
  - mhr-q-692ecf2907b3b65bc418/C：model_failed。
  - mhr-q-692ecf2907b3b65bc418/D：planner_failed。
  - mhr-q-692ecf2907b3b65bc418/E：planner_failed。
  - mhr-q-71c3f55cd1150d560470/B：model_output_invalid。
  - mhr-q-816c6ee52abe20ca42e6/C：model_output_invalid。
  - mhr-q-816c6ee52abe20ca42e6/D：model_output_invalid。
  - mhr-q-816c6ee52abe20ca42e6/E：planner_failed。
  - mhr-q-9059b74afac51ed7cab5/B：model_output_invalid。
  - mhr-q-c81a29ee075e961b2eb0/D：plan_output_invalid。
  - mhr-q-d527c115ed7cfd8f14b1/B：model_output_invalid。
  - mhr-q-d527c115ed7cfd8f14b1/C：model_output_invalid。
  - mhr-q-d527c115ed7cfd8f14b1/E：model_output_invalid。

逐题 JSON 保留每组查询轨迹、来源 Chunk、引用、预算、耗时、实际响应模型与已知 Token。本报告只陈述观察结果；未测得提升时保留退化或无差异，不能据此宣称 Agent 优于基线。
