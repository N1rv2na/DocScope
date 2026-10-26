# DOCS-002 运行记录

> 当前环境：WSL/Linux，Python 3.10.12；日期 2026-10-26。以下结果仅来自本仓库实际执行的命令，空缺项保持未验证。

| 检查 | 状态 | 实测结果 / 原因 |
|---|---|---|
| 纯规则单元测试 | 通过 | `PYTHONPATH=backend/src python3 -m unittest backend.tests.test_feasibility -v`：5/5。 |
| 本地 Embedding/Reranker | 通过 | CPU；Embedding 384 维，首次加载 24.067 秒、4 条编码 0.019 秒；Reranker 首次加载 21.39 秒、3 对预测 0.018 秒。权重 revision 见 [README.md](README.md)。 |
| Qdrant Dense/BM25/RRF | 通过 | Qdrant 1.20.0、同批 3 Chunk；Dense `[1,2,3]`，BM25 查询 `Orion` 为 `[1]`，RRF `[1,2,3]`；二次运行含模型热加载共 3.941 秒。 |
| 远程 LLM 项目最小调用 | 通过 | 本环境默认沙箱无法访问 `127.0.0.1:3425`，授权的本地网络可访问。OpenAI-compatible `group/docscope-free` 两次均返回 `loomy/DeepSeek-V4.1-Flash`；短文本 1.389 秒、含证据请求 2.285 秒。用户前期 JSON/SSE Smoke Test 为单独证据。 |
| Token/费用真实样本 | 部分通过 | [短请求](llm-sample.json)真实输入 37、输出 16 Token；[含证据请求](llm-evidence-sample.json)真实输入 103、输出 136 Token。免费渠道按用户给出的零价假设，单次人民币估算 0；尚无渠道账单、额度耗尽或正式单渠道的价格证据。 |

实测包版本：`sentence-transformers==5.1.2`、`transformers==4.57.6`、`torch==2.5.1+cpu`；LLM 客户端 `openai==2.32.0`（系统环境），Qdrant 客户端为 Python 3.10.12 标准库 `urllib` REST。上述秒数是这台机器的样例，不是性能承诺。

预算计算示例（基于真实含证据请求的 103/136 Token，但**价格为假设**）：输入 2 元、输出 8 元/百万 Token 时，单次约 `0.001294` 元；50 次开发调用 + 100 次正式调用约 `0.1941` 元，另预留 10 元，预计合计 `10.1941` 元。此样本仍不能代表多跳 Agent 的多次调用；正式评测前必须用代表性问题重测 Token 上界和实际价格，再冻结调用次数。

正式 A–E 实验必须固定单一模型及渠道，`group/docscope-free` 的顺序路由只用于日常开发。429、额度耗尽和流式中断后的回退行为尚未证实。
