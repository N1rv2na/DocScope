# DOCS-023 固定 dev 提示输入预算对照

24 条预先固定的调用输入，三组各一次，不运行检索或封存 test。样本故意包含全部 12 条历史截断，不能外推为总体失败率。
第一、二组仅预算不同；第二、三组仅输出指令不同。所有组使用相同的阶段超时和请求模型，实际渠道由网关决定。
结构成功仅指 JSON、Schema 与引用合同通过，不等于答案语义正确。无自动重试。

| 组 | 调用 | 结构成功 | 截断 | 请求超时 | Completion 已知/缺失 | Reasoning 已知/缺失 | 中位耗时 ms |
| --- | ---: | ---: | ---: | ---: | --- | --- | ---: |
| historical_budget_prompt | 24 | 23 | 0 | 1 | 16057/1 | 14633/1 | 10088.281 |
| larger_budget_same_prompt | 24 | 24 | 0 | 0 | 28126/0 | 26262/0 | 13108.029 |
| larger_budget_revised_prompt | 24 | 24 | 0 | 0 | 28445/0 | 26857/0 | 10231.499 |

## 分阶段与配对开销

| 组 | 阶段 | 结构成功/调用 | 截断 | 已知 Completion | 中位耗时 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| historical_budget_prompt | answer | 17/18 | 0 | 9459 | 5713.452 |
| historical_budget_prompt | plan_next | 6/6 | 0 | 6598 | 30260.577 |
| larger_budget_same_prompt | answer | 18/18 | 0 | 19839 | 10400.496 |
| larger_budget_same_prompt | plan_next | 6/6 | 0 | 8287 | 18187.126 |
| larger_budget_revised_prompt | answer | 18/18 | 0 | 15274 | 6813.336 |
| larger_budget_revised_prompt | plan_next | 6/6 | 0 | 13171 | 19513.677 |

只在两组均结构成功且 Completion 用量可得的相同输入上比较 Token；超时调用不计入此配对分母。
| 对照 | 可比输入/全部 | Completion 左/右 | 中位耗时左/右 ms |
| --- | ---: | ---: | ---: |
| historical_budget_prompt → larger_budget_same_prompt | 23/24 | 16057/27051 | 8052.312/11880.457 |
| larger_budget_same_prompt → larger_budget_revised_prompt | 24/24 | 28126/28445 | 13108.029/10231.499 |

逐条状态、诊断、配置与 Prompt 哈希见同名 JSON。网关账单和实际渠道不可见时记为未知。
不同时间窗口、未知渠道及模型随机性限制因果解释；未测得改善时不得声称新预算更优。
