# 可引用 RAG：实现与验收计划

本文件记录 `0008_cited_rag` 单份资料的“准备索引 → 提问 → 带引用回答 → 原文定位”闭环。其后新增的集合、多资料对话与 Quiz 见 [学习闭环实现](./learning-core.md)。

## 交付步骤

1. 配置与服务适配：百炼 `qwen3.7-text-embedding` / 1024 维，DeepSeek `deepseek-flash`；模型密钥仅进入 Worker。
2. 增量迁移 `0008_cited_rag`：版本化索引、分块、持久化问答任务。
3. 有界后台处理：按来源单元切块、分批向量化、检查点恢复、完整后发布。
4. 检索与回答：在 workspace、当前文档版本和已完成索引范围内检索，生成后核验引用。
5. 阅读界面：索引进度、提问、失败说明、停止回答、最近 20 条记录、引用预览和原文跳转。
6. 验收：单元/契约、数据库隔离与恢复、浏览器键盘/窄屏旅程，以及单独执行的小规模真实模型评测。

## 配置与启动

本地 `.env` 配置 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY` 和 `DASHSCOPE_EMBEDDING_URL`；可选 `DEEPSEEK_MODEL=deepseek-flash`、`DASHSCOPE_MODEL=qwen3.7-text-embedding`。北京地域的向量接口格式为 `https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding`；从百炼控制台的 OpenAI 兼容地址保留同一主机名，改用上述 DashScope 路径。使用原生接口是为了区分 `query` 与 `document` 向量。真实密钥和私有 Workspace 地址不写入 `.env.example`，不使用 `VITE_` 前缀，不把整个 `.env` 挂载给前端或 API。

```bash
docker compose up --build -d --wait
docker compose run --rm database-init alembic check
docker compose exec -T api python -m scripts.verify_runtime_database_access
```

升级保留当前 PDF/DOCX/PPTX、正文及旧 Voyage 索引；已有资料不自动发送到云端。新 Embedding profile 不读取旧向量。打开资料，选择“基于此资料提问”，再选择“准备资料问答”，才会用百炼重新建立该资料的索引。正文与检索问题发送到百炼，问题和检索到的最多 6 个片段发送到 DeepSeek。

可单独执行 `docker compose exec -T worker python -m scripts.verify_model_access` 检查两个服务。此命令各发一个短合成文本请求，可能消耗额度，只输出服务状态、维度与生成 Token 数，不输出密钥或正文。普通测试不调用真实 API。

若只想确认 `qwen3.7-text-embedding` 是否可调用，可在项目根目录运行 `uv run python -m scripts.verify_model_access --embedding-only`；它从本地 `.env` 读取密钥，发送一段合成文本，验证返回 1024 维向量。更完整的付费质量检查需在仓库根目录本机运行 `uv run python -m scripts.evaluate_rag`（容器镜像不打包 `evals/`）；它使用 `evals/rag-v1.json` 中的合成资料，检查预期来源召回、拒答和提示注入不被引用；遇到限流时稍后重试。两项检查都不会读取私人资料。

## 当前技术决策

- `document_indexes` 绑定不可变解析版本和 embedding profile；唯一键保证同版本同策略不重复索引。索引未完成时不能提问，旧 Voyage profile 保留但不被新百炼 profile 读取；更换策略不会原地覆盖旧向量。不需要数据库迁移或清空旧索引。若确定不再需要旧索引，应另做受控清理，而非在此切换中删除。
- 分块 `source-window-1500-180-v1`：每块最多 1500 字符、重叠 180 字符，不跨 PDF 页/DOCX 标题段/PPTX 幻灯片。保存内容单元、字符起止、原文；首版不是模型 Token tokenizer，后续按评测调整。单份资料最多 5000 块。
- 每次领取处理最多 16 块，提交结果后让出执行机会；已保存向量跳过。只有全部块完成才原子切换为 `ready`，正文 `ready` 不受索引失败影响。
- 任务使用 180 秒租约和唯一执行令牌，迟到的旧执行者不能发布结果；模型调用在事务之外。问答总时间上限 110 秒，输出上限 3000 Token；每个空间最多同时 3 个问题、每小时 60 个。
- 明确的模型故障进入可解释失败；不自动重发结果未知的付费请求。用户重试可复用已保存向量，但网络中断/进程崩溃后服务商可能已经计费，不能保证跨服务 exactly-once。
- 当前最多 5000 块的单资料范围采用精确 cosine 检索，结合 PostgreSQL `simple` 全文排名，以 RRF 融合；候选各 12，返回 6。中文主要依靠向量检索，`simple` 不是中文分词器。暂不添加 HNSW 或独立重排模型，以免产生过滤后召回损失或额外服务成本。
- `public` 是管理员控制的 pgvector 扩展所在 schema，加入运行角色 search_path 的最后一项；仍撤销 PUBLIC 的 CREATE，运行账号无 DDL 权限。
- 回答结构为逐条 claim + 来源 ID + 原文摘录；每个引用必须属于此次检索结果，摘录必须是原文连续子串。此校验保证引用可定位，但不能数学证明“引用支持整个结论”，因此保留语义评测与用户核对入口。
- 删除中资料立即退出检索/发布；删除解析版本时级联清理索引、向量、提问和回答。已发出的网络请求无法撤回，但迟到结果不能写回。
- 最近问答仅展示当前解析版本；停止回答后不发布迟到结果。当前不做自动对话记忆，多次提问均独立检索。模型结果通过引用校验后整段显示，不流出尚未校验的内容。

## API 契约

所有接口都需认证，并由服务端确定 workspace。路径前缀为 `/api/v1/documents/{document_id}`。

| 方法与路径 | 结果 |
| --- | --- |
| GET `/index` | 未建立/排队/处理/完成/失败，以及完成块数 |
| POST `/index` | 创建或重试索引，202；需 `Idempotency-Key` |
| POST `/questions` | 保存问题并排队，202；需 `Idempotency-Key`，问题 2–2000 字 |
| GET `/questions` | 当前版本最近 20 条问题与结果 |
| GET `/questions/{id}` | 查询单次回答状态 |
| POST `/questions/{id}:cancel` | 幂等取消尚未完成的回答 |

相同提问幂等键和请求返回同一问题；键冲突返回 409；未完成索引返回 409；越权文档/问题返回 404；认证失败 401；问题输入过长 422，请求体超过 16 KiB 为 413。模型错误只返回稳定错误码，不返回服务商原始内容。

## 验证与恢复

`scripts.verify_rag_flow` 使用独立测试 workspace 与合成正文、假模型，检查部分索引不可见、批次恢复、数据库约束、API 权限、幂等、取消和删除。脚本用应用账号运行，结束清理自己创建的数据。建议在隔离 Compose 项目中执行，不与生产 Worker 共享队列。

```bash
docker compose -p review-agent-rag-test -f compose.yaml up -d --wait db
docker compose -p review-agent-rag-test -f compose.yaml run --rm database-init
docker compose -p review-agent-rag-test -f compose.yaml run --rm --no-deps api python -m scripts.verify_rag_flow
```

从 `0007` 升级只增表，不改动已有正文。生产回退优先回退应用并保留新表，或前滚修复；`downgrade 0007` 会删除所有 RAG 向量和问答，仅可在可丢弃环境演练。向量可重建，但需再次调用相应 Embedding 服务。上线备份应包含问答与索引表。

## 后续范围

多资料选择、同范围最近消息上下文与 Quiz 已在 `0009_learning_core` 中实现；`0010_user_auth` 提供多用户登录与工作区隔离。真实资料语义评测、精确 Token 预算与重排仍待完成。公开部署前仍需完成服务商使用预算/告警、备份恢复演练和入口 HTTPS 配置。
