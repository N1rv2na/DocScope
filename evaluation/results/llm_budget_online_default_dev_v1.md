# DOCS-023 线上默认组合固定输入复测

与 llm_budget_dev_v1.json 使用同一批 24 条冻结 dev 输入；本轮仅运行新版指令 + Answer 2048 / Planner 4096 / 两阶段超时 60 秒，每条一次，无重试。
仅验证 JSON、Schema、引用 ID 与规划字段，不验证语义正确率或完整 Agent。

结构成功 23/24；截断 0；各状态 {'success': 23, 'request_timeout': 1}；Completion 已知总量 22542 （用量缺失 1 次）；Reasoning 已知总量 21240（用量缺失 1 次）；中位耗时 8736.525 ms。

逐调用诊断和配置见同名 JSON。与此前三组运行不在同一时间窗口，实际渠道和账单未知，不得把跨运行差异解释为 Prompt 的因果效果。
