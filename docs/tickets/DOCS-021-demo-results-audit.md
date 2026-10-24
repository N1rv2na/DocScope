# DOCS-021 — 完成 README、Demo 与实测结论审计

**Status:** ready-for-agent
**Priority:** P1
**Planned milestone:** M5

## Background

作品集描述必须与实现和评测一致，不能把 M1 能力或子集结果表述为完整 Agentic 成果。

## Goal

形成可供复现及求职展示的准确说明和 Demo 路径。

## Scope

- README、架构与运行说明、固定演示步骤。
- 评测表、失败案例、数据许可和公开产物检查。

## Out of Scope

新功能、额外 Benchmark 和虚构提升数字。

## Technical Approach

逐条对照 M0–M5 验收与逐题结果；区分已实现、已评测和仍规划能力。

## Implementation Steps

1. 整理启动及 Demo。
2. 核对指标来源和样本范围。
3. 审计公开材料及项目表述。

## Acceptance Criteria

- [ ] README 每项能力对应实测/验收或明确标为计划。
- [ ] 指标标明划分、题数、模型/配置、费用和复现命令。
- [ ] 公开产物无凭据、保密材料或未经许可原始数据。
- [ ] 新环境可重放固定 Demo，没有证据支持的提升主张被删除。

## Tests & Validation

文档命令重放、结果与逐题产物抽查、公开材料检查。

## Dependencies

- DOCS-020 — 用 Docker Compose 复现启动与评测

## Deliverables

README、Demo 指南、架构/评测说明和审计记录。
