# DOCS-007 文档生命周期合同

依据：Issue #7、[规格 FR-02/03/05](spec.md)、[ADR 0003](adr/0003-evidence-provenance-and-lifecycle.md)。

## 身份与接口

- 单个本地知识库中，普通 `POST /api/documents` 按**区分大小写的完整文件名**匹配未删除文档。同名文件属于同一逻辑文档；不同文件名默认是不同文档。
- `PUT /api/documents/{document_id}` 显式替换所选文档，可更换文件名和 PDF/Markdown 格式。更名与另一文档冲突返回 409；不存在或已删除的目标返回 404。
- `ready` 文档同文件名、同 SHA-256 内容重传返回 200，复用 Document/Chunk ID，不重新 Embedding 或写索引。新文档返回 201；实际替换成功返回 200。
- `GET /api/documents` 返回未删除文档及 `status`、`error_code`、`chunk_count`；`GET /api/documents/{id}` 查询单份状态。老数据没有 Chunk 数量时为 null，不能当成零。
- `DELETE /api/documents/{id}` 成功返回 `deleted`，移除 Qdrant 点和原始文件；保留最小状态记录供旧引用判定。重复删除同一记录返回 200，不存在的 ID 返回 404。删除后同名再上传会分配新 ID。
- `GET /api/documents/{id}/citations/{chunk_id}` 复验引用：当前 ready 文档和当前索引点匹配才返回 `valid`，被替换/删除或未发布的来源返回 `invalid`；索引服务故障返回 503 `citation_check_failed`，不能误报引用有效或已删除。

## 发布与失败恢复

采用 SQLite 状态作为检索可见性的门槛。接受替换后，先将原文档改为 `processing`，移除旧索引及源文件，再解析、Embedding、写入新索引；只有全批写入成功才能改为 `ready`。因此替换期间及失败后旧内容也不可检索。这个短暂不可用窗口换取简单、可验证的失败语义；不提供旧版本回滚。

删除先标记 `deleting`，使文档退出检索，再清理索引和源文件。任何清理失败返回 503、状态 `failed` / `delete_failed`，文档持续不可检索；用户重试删除即可继续清理，不能把清理失败显示成成功删除。

解析、Embedding、索引等失败沿用对应 `error_code`。失败时即使 Qdrant 清理也失败，残留点仍被 SQLite ready 状态和索引代号过滤。再次上传失败文档会先清理残留点，再重新索引，不走同内容短路。

每次实际重建递增 `index_version`，并将其纳入 Chunk ID。该代号只用于识别本次索引，不保存版本历史；文档 A → B → A 也不会恢复原 A 的旧引用。问答生成前后均检查全部模型输入证据的来源代号，包括最终未被引用的证据；生成期间来源变化返回 409 `source_changed`，不发布基于过期证据的回答或拒答。

## 运行与迁移边界

运行一个 FastAPI 进程/一个 Uvicorn worker，独占一份数据目录。进程内锁串行化导入、删除、检索快照及引用核验；LLM 调用不持有该锁，文档列表仍可观察处理中状态。不支持多个写进程共享同一 SQLite/Qdrant 集合。

第一次打开存储时补充索引代号和 Chunk 数量列，保留 DOCS-006 已有 Document/Chunk ID。上次进程中断遗留的 `processing` 改为 `failed/interrupted`，`deleting` 改为 `failed/delete_failed`；通过重传或重试删除恢复。此前已产生的同名多文档不会被自动删除或合并：普通同名上传返回 409，用户从列表指定 ID 替换，或删除多余记录。

Web 展示各文档状态，上传/删除过程中轮询，完成后刷新；点击“刷新文档”或窗口重新获得焦点也会刷新。回答保留在页面，引用每次展开、刷新和本页文档操作后都重新核验；失效时隐藏原文并提示重新提问，网络失败显示“无法核验”。M1 不持久化会话历史。

## 验收入口

`backend/tests/test_document_lifecycle.py` 覆盖重启后幂等、替换/删除、A→B→A 旧引用、半成品写入及清理失败、恢复、并发重传、生成期间替换和旧库迁移。

`frontend/tests/document-lifecycle.spec.ts` 覆盖列表操作、保留旧回答并标记引用失效、失败删除重试；`main-flow.spec.ts` 继续验证 PDF/Markdown 和键盘主路径。API/浏览器测试使用可控的索引/模型边界，不要求调用真实模型。

真实 Qdrant 检查使用 `backend/tests/test_qdrant_lifecycle.py`，通过 `QDRANT_TEST_URL` 显式启用，使用独立测试集合并清理。运行命令见 [backend/README.md](../backend/README.md#tests)。

## DOCS-007 验证记录（2026-10-29）

| 检查 | 实际结果 |
|---|---|
| `PYTHONPATH=backend/src .venv/bin/python -m unittest discover -s backend/tests -q` | 50 项中 49 项通过，1 项真实 Qdrant 检查按默认配置跳过；包含未引用证据变更后的回答/拒答回归 |
| `QDRANT_TEST_URL=http://127.0.0.1:6337 PYTHONPATH=backend/src .venv/bin/python -m unittest backend.tests.test_qdrant_lifecycle -v` | 1 项通过，服务端 Qdrant 1.20.0 |
| `cd frontend` 后执行 `npm run test:e2e -- --reporter=line` | 10 项浏览器测试通过 |
| `cd frontend` 后执行 `npm run build` | TypeScript 检查和生产构建通过 |
| 临时 Chromium → FastAPI → Qdrant 联跑（无 API 拦截） | 上传、同内容重传、问答引用、替换、旧引用失效、新证据与删除均通过；独立临时目录和集合 |

上述回归检查没有执行真实 Embedding 或 LLM 推理。真实 Qdrant 与浏览器联跑仍使用固定向量/回答，只验证生命周期与接口连接，不用于证明检索质量或回答准确率。

### 用户授权后的真实模型验收（2026-10-29）

用户明确授权后，从 `~/.zshrc` 加载网关配置，以真实 CPU Embedding、真实 Qdrant 1.20.0 和项目原有 `OpenAIResponder` 执行 API 集成验证。SDK 调用仅加旁路计时与 usage 记录，不替换模型响应。原始运行证据见 [DOCS-007-real-model.json](validation/DOCS-007-real-model.json)，不含 API Key。

- 请求地址：`http://127.0.0.1:3425/v1`；请求模型：`group/docscope-free`。4 次调用返回的模型标识均为 `loomy/DeepSeek-V4.1-Flash`，未独立审计网关内部路由。
- Embedding：`sentence-transformers/all-MiniLM-L6-v2`，revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`，CPU，使用本地缓存且禁用下载。
- 依赖：`openai 2.32.0`、`sentence-transformers 5.1.2`、`torch 2.5.1+cpu`、`fastapi 0.115.14`、`pypdf 6.21.2`。
- 20 个 API 步骤及其断言通过：Markdown 导入/Dense 检索、回答与引用核验、同内容幂等、替换前后索引和引用检查、无支持证据拒答、删除后无检索结果、空库拒答不调用模型，以及 PDF 第 2 页引用和删除清理。
- 初始回答为 `North Observatory`，替换文档后为 `East Observatory`；删除后引用无效。关于建造费用的问题返回 `insufficient_evidence`。PDF 回答为 `The North Observatory built the Orion telescope.`，引用含第 2 页原文。
- API usage 合计：prompt 1,281、completion 307、total 1,588 Token；completion 中含 155 reasoning Token，不再重复累加。4 次 SDK 调用耗时分别为 3.672、1.442、0.994、1.168 秒。未获取实际账单金额，不将免费渠道描述为已核验的零费用。
- 总流程 48.312 秒；首次上传 38.230 秒（包含本地 Embedding 首次加载），同内容重传 0.002 秒，替换 0.042 秒。均为单次观测，不是性能基准。
- 本轮通过 TestClient 进入实际 FastAPI 路由并访问真实外部依赖；未重新执行真实模型浏览器交互，也未验证额度耗尽、429 或跨渠道 fallback。故障注入与并发边界沿用前述自动化回归证据。
- 使用独立临时目录和集合；最终 Qdrant 点数为 0，测试集合、源文件及临时容器均已清理。
