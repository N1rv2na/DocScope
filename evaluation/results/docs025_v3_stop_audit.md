# DOCS-025：历史 v3 错误拒答停止分支核查

来源：[2026-11-04 A–E 逐题运行](ablation_dev_v3_magpie_on_20261104.json)。本文件是对既有 70 条记录的只读归因，未运行新模型或改写历史指标。

原报告在 45 个可回答题次中标记 15 次 `false_abstention`。按当时 Run 的最终 `stop_reason` 和 Trace 复核：

| 旧停止原因 | 次数 | 本 Issue 影响 |
| --- | ---: | --- |
| `missing_fact_unresolved` | 2 | E 组的 `mhr-q-71c3f55cd1150d560470` 和 `mhr-q-f4da8c5620114c384e0b`；首跳均有 5 条 Evidence，但程序在 Planner 后直接拒答，没有调用 Answer。新控制流可使这两条进入 Answer 判断。 |
| `model_abstained` | 13 | 已进入 Answer 并由模型拒答；本 Issue 不改变模型判断、检索召回或 Prompt，不能据此预期改善。 |

上述 15 条中，历史 Run 没有以 `no_new_evidence` 或 `repeated_query` 为最终停止原因的记录；这两个分支由固定依赖样本和故障注入测试覆盖。两条 `missing_fact_unresolved` 的自动标签只能说明**有机会**让 Answer 复核现有证据，不能证明现有证据足够，也不能推断新代码会正确回答。旧版 A–E 的模型、配置、调用预算和结果均保持原样；真实稳定性及语义质量变化须在 DOCS-026 的固定样本复测中确认。
