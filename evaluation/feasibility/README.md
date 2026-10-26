# DOCS-002：M0 能力与费用试运行

本目录只记录一次性能力检查，不是 DocScope 的检索服务、模型路由层或完整评测器。运行前确认 Python 3.10、CPU 内存和本机 Docker 可用。Qdrant 使用 `qdrant/qdrant:v1.20.0`；客户端使用 Python 标准库 `urllib` 调用该版本 REST API，不安装 Qdrant SDK。脚本创建唯一临时集合并在结束时删除，不触碰业务集合。

## 固定候选

| 用途 | 候选与版本 |
|---|---|
| Embedding | `sentence-transformers/all-MiniLM-L6-v2`，revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |
| Reranker | `cross-encoder/ms-marco-MiniLM-L6-v2`，revision `233902d25c440f23af6f7d6e94d2946bac0bee0a` |
| Python 推理 | `sentence-transformers==5.1.2`、`torch==2.5.1+cpu`；其余传递依赖版本由实测记录，不作为 M1 锁文件 |
| Qdrant | 服务端 `1.20.0`，公开镜像 `sha256:9bfc89f9914fc14876a88c05270685921a55b8877b254a98bcc1cc2f0a424cd2` |
| 远程 LLM | 开发用 OpenAI-compatible Magpie `group/docscope-free`，仅作为外部网关；渠道由用户已做 smoke test。正式 A–E 消融必须冻结**单一模型与渠道**。|

模型权重的 revision 是候选冻结，不代表当前环境均已成功推理；结果以本页下方运行记录为准。

## 可复现命令

在仓库根目录运行：

```bash
python3 -m pip install --target /tmp/docscope-002-deps \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  'torch==2.5.1+cpu' 'sentence-transformers==5.1.2' 'openai==2.32.0'
export PYTHONPATH="/tmp/docscope-002-deps:backend/src"
python3 -m docscope.feasibility local

docker run --rm -d --name docscope-m0-qdrant -p 127.0.0.1:6333:6333 qdrant/qdrant:v1.20.0
python3 -m docscope.feasibility qdrant --url http://127.0.0.1:6333
docker stop docscope-m0-qdrant

export LLM_BASE_URL=http://127.0.0.1:3425/v1
export LLM_API_KEY=YOUR_LOCAL_GATEWAY_TOKEN
export LLM_MODEL=group/docscope-free
python3 -m docscope.feasibility llm --scenario evidence > /tmp/docscope-m0-llm-usage.json
```

`LLM_API_KEY` 只从后端进程环境读取；将占位符替换为本机 Magpie Gateway 的实际调用标识，不写入 Git 文件。若报告 `environment_unreachable`，含义是网关端口不可达；这不能证明模型调用失败。调用成功会输出模型名、Token 用量与耗时，不输出回答正文或调用标识。

也可运行默认 `llm` 短文本请求；预算应优先使用 `--scenario evidence` 的用量，且需要在 M3 用真实多跳开发题重新测量。脚本只检查有文本及用量，不评价答案准确性或引用支持性。

## 100 元人民币预算办法

必须先从成功的真实 LLM 响应取得 `prompt_tokens`、`completion_tokens`；再填入**该单一评测渠道实际适用的人民币输入/输出单价**（每百万 Token）。若渠道免费，可填 0，但仍须记录 Token、调用次数和实际账单；免费额度耗尽或渠道切换不得自动计为同一正式实验。

```bash
python3 -m docscope.feasibility budget \
  --usage-json /tmp/docscope-m0-llm-usage.json \
  --input-cny-per-million 0 --output-cny-per-million 0 \
  --development-calls 50 --evaluation-calls 100 \
  --spent-cny 0 --reserve-cny 10
```

上面的 `0` 单价、`50/100` 次数仅是**命令格式示例**，不是实测费用或已冻结评测规模。计算式：单次估计费用 = `(输入 Token × 输入单价 + 输出 Token × 输出单价) / 1,000,000`；预计总占用 = 已花费用 + 单次估计 × 计划调用数 + 预留 10 元。超过 100 元时命令失败。每次扩大实验前，用开发集代表性问题重新测量；对更长输入、更多检索轮次和输出长度单独设置较高 Token 上界。正式 A–E 子集与每组调用上限留到 M3 冻结，实际花费必须累计，而不是把单个示例外推当作账单。

## 当前运行证据

见 [result.md](result.md)。DOCS-002 的用户前期 Magpie smoke test 只作为外部证据；本脚本必须独立完成调用才可勾选对应验收。
