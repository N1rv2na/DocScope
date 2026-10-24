# DOCS-020 — 用 Docker Compose 复现启动与评测

**Status:** ready-for-agent
**Priority:** P1
**Planned milestone:** M5

## Background

M3 可按本地步骤演示；M5 要验证新环境可重放完整系统。

## Goal

从干净环境按文档启动应用和 Qdrant，并执行固定冒烟与评测命令。

## Scope

- Compose 配置、环境变量示例、数据持久化位置。
- 启动/停止及健康检查说明。

## Out of Scope

生产级编排、高可用、GPU 服务和多租户权限。

## Technical Approach

保持单机部署；远程 LLM 凭据由运行环境提供，不写入镜像或仓库。

## Implementation Steps

1. 固定服务及数据卷。
2. 从新环境启动。
3. 重放上传、问答、引用及评测样本。

## Acceptance Criteria

- [ ] 新环境按说明启动 Web、API、Qdrant 及必要本地状态存储。
- [ ] 固定样例完成上传、问答、引用，评测命令可执行。
- [ ] 重启后按持久化规则恢复数据，凭据不入仓库或镜像配置。
- [ ] 启动失败有可定位检查步骤。

## Tests & Validation

干净环境部署冒烟与重启测试。

## Dependencies

- DOCS-016 — 完成 A–E 实际与相近预算对照报告
- DOCS-019 — 实现滚动摘要与长对话独立评测

## Deliverables

Compose 配置、环境示例、部署说明与冒烟记录。
