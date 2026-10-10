# 开发规范

版本：0.1
目标：让新增功能沿稳定路径扩展，避免路由、SQL、模型调用和业务规则互相缠绕。

## 1. 专业开发者视角

高可维护性来自边界与反馈速度，不来自堆叠抽象。项目只为已经存在的变化点建立接口：模型、解析器、存储、任务队列；普通 CRUD 不强行套多层泛型 Repository。

### 1.1 基本规则

- 路由只负责协议转换、鉴权依赖、调用用例和映射响应。
- 业务规则写在领域或 application 用例中，不写在 ORM event、路由或后台任务入口。
- SQLAlchemy model 不直接作为 API schema 返回。
- 一个数据库事务对应一个清晰用例；外部网络调用放在事务外。
- 模块拥有自己的表访问逻辑；跨模块通过用例或明确查询接口协作。
- 删除重复代码前先确认语义相同；不要用巨型“工具类”隐藏不同业务概念。

## 2. 推荐目录与命名

每个领域按需创建，不为了对称生成空目录：

```text
documents/
  domain/
    entities.py
    enums.py
    errors.py
  application/
    commands.py
    queries.py
    ports.py
  infrastructure/
    models.py
    repository.py
    parser.py
  api.py
  schemas.py
```

- 文件、函数、变量使用 `snake_case`；类使用 `PascalCase`。
- 用例采用动词：`CreateDocument`、`StartIndexing`、`DeleteDocument`。
- 避免模糊名称：`data`、`info`、`manager`、`helper`、`utils`。
- 时间字段统一 `_at`，布尔字段使用 `is_`/`has_`，外键使用 `<entity>_id`。

## 3. API 约定

- 路径带版本：`/api/v1/documents`。
- 资源名使用复数；动作尽量用状态资源表达。无法自然表达时可使用 `/documents/{id}:reindex`。
- 创建同步资源返回 `201`；异步任务返回 `202` 和可查询的 job。
- 删除默认幂等；重复删除不造成 500。
- 列表使用游标分页，返回 `items` 与 `next_cursor`。
- 写请求支持 `Idempotency-Key`，特别是上传确认、生成 Quiz、提交答案。
- 错误格式稳定，不向客户端暴露堆栈：

```json
{
  "error": {
    "code": "DOCUMENT_NOT_READY",
    "message": "资料仍在处理中",
    "request_id": "...",
    "details": {}
  }
}
```

Pydantic schema 对长度、枚举、数量和嵌套深度设置上限。OpenAPI 是前后端契约的一部分，破坏性变更必须版本化。

## 4. 配置与依赖

- 使用 `pydantic-settings` 集中加载配置，启动时完成必要校验。
- `.env` 仅供本地开发；生产密钥来自秘密管理服务。
- 直接使用的包必须在 `pyproject.toml` 声明，不能依赖传递依赖“碰巧存在”。
- 锁文件必须提交；Docker 使用 `uv sync --frozen` 保证可重复构建。
- 外部 SDK 只出现在 infrastructure adapter 中。

配置按环境变化，业务规则不按环境分叉。不要写 `if production:` 来改变授权、事务或核心语义。

## 5. 数据访问

- application 层控制事务边界，Repository 不自行提交事务。
- 查询显式加载所需关系，避免隐式 lazy loading 与 N+1。
- 所有用户数据查询必须带 workspace scope；通过共享的 scope 类型或查询构造器降低漏写概率。
- 批量分块和 Embedding 使用批量写入，禁止逐条 commit。
- 使用 Alembic 管理 schema；应用启动时不调用 `create_all()`。
- 数据库异常映射为领域/应用错误，API 层再映射为稳定 HTTP 错误。

## 6. 异步与后台任务

- `async def` 中禁止直接执行 CPU 密集解析和阻塞 SDK；使用 Worker 或线程/进程边界。
- 任务 payload 只包含稳定 ID 和版本，不传整个文件或大段文本。
- 每个任务声明幂等键、超时、重试次数、可重试错误和终止错误。
- 重试不覆盖第一次失败记录；保存 attempt、错误类别和下一次重试时间。
- 用户取消或删除时，Worker 在每个阶段检查当前状态，避免继续产生无用费用。

## 7. RAG 与模型开发规范

跨资料隔离、外部调用恢复、引用语义质量、作答并发和部署兼容性的失败场景集中见[技术难点与验证路径](./technical-challenges.md)；选测试时按对应的用户后果裁剪。

LangGraph 迁移按 [接入规划](./langgraph-integration-plan.md) 和 [工作流开发设计](./agent-workflow-development.md) 分阶段开发。组合学习、跨交互等待、业务检查点和草稿修订保护已实现；启用前验证恢复不会重复发布或重置预算。

当前实现、独立数据库集成检查和云端连接检查见 [RAG 实现与验收](./rag-implementation.md)。普通测试使用假模型；`scripts.verify_model_access` 会调用真实服务，单独执行。供应商错误只记录稳定错误码；配置校验隐藏原始输入，避免异常消息泄露密钥。

- Prompt 使用独立模板和版本号，不在 Python 字符串中到处拼接。
- 模型输入和输出有 schema；解析失败不能静默降级为未经验证的文本。
- 分块策略、Embedding 模型、检索参数和重排版本全部进入 `index_version`。
- 建立小型固定评测集，包含答案、应命中的来源和拒答样例。
- 每次调整分块/检索/Prompt，至少比较召回、引用准确率、拒答质量、延迟和成本。
- 测试中使用 fake provider，不调用真实付费模型；单独的评测任务才访问外部模型。

## 8. 测试策略

| 层级 | 测试内容 | 是否访问真实基础设施 |
| --- | --- | --- |
| 单元 | 领域规则、状态机、分块纯函数、Quiz 校验 | 否 |
| 集成 | Repository、迁移、pgvector 查询、对象存储 adapter | 使用临时容器 |
| 契约 | LLM/Embedding/解析器 adapter 的输入输出契约 | 使用录制响应或沙盒 |
| API | 鉴权、错误码、分页、幂等、权限隔离 | 测试数据库 |
| E2E | 上传到问答/Quiz 的关键路径 | 独立测试环境 |
| 评测 | RAG 召回、回答引用、Quiz 质量 | 版本化数据集 |

测试命名描述行为，例如 `test_deleted_document_is_excluded_from_retrieval`。Bug 修复先添加可复现测试。时间、随机数、模型与存储通过依赖注入保持可重复。

## 9. 代码质量门禁

Agent 改动同时验证 `uv run python -m scripts.evaluate_agent` 的版本化合成数据集；真实服务对比需显式 `--live`，不进入普通测试。当前统一对话任务需要先升级 `0014_agent_workflows`（包含 `0013` 对话结果和 `0012` Quiz 审计），空库与上一版本升级验证方法见 [Agent 开发计划](./agent-development-plan.md)。

资料库增强使用 `uv run python -m scripts.verify_frontend_learning` 在独立、空测试数据库验证：服务端全量搜索、字面通配符、两种游标顺序、集合过滤、批量创建回滚、继续学习及工作区隔离。先配置独立 PostgreSQL/pgvector，使用迁移凭据升级到 head，再用非 schema 所有者的 runtime 凭据运行脚本；脚本拒绝含已有工作区的数据库并清理自身合成数据。此增强没有 schema 变更。浏览器验收在 `web/e2e/frontend-experience.spec.ts`，覆盖历史阅读位置、新回复提示、引用预览、题号导航、URL 筛选及 320/390px 布局；组件测试检查失败重试保留选择和精确来源版本。

建议逐步引入：

- Ruff：格式、导入和静态规则。
- Pyright 或 mypy：严格类型检查，从 domain/application 开始。
- pytest + coverage：覆盖关键规则，不以单一百分比代替测试质量。
- Bandit/pip-audit 或等效工具：代码与依赖安全扫描。
- pre-commit：在提交前执行快速检查。

CI 最少执行：锁文件一致性、格式/静态检查、类型检查、单元测试、数据库迁移测试、镜像构建与漏洞扫描。

## 10. 日志与错误处理

- 使用结构化日志，不用 `print`。
- 捕获异常时保留原始异常链；不要 `except Exception: pass`。
- 可预期业务错误使用稳定类型和错误码；未知错误统一返回 500 并关联 `request_id`。
- 日志对文件名、邮箱、正文、Prompt、回答和访问令牌做删除或脱敏。
- 同一种失败只在责任边界记录一次，避免 API、service、repository 重复打三遍错误日志。

## 11. 安全开发清单

- 每个读取和写入接口都有资源所有权测试。
- 上传同时检查扩展名、MIME、魔数、压缩炸弹风险与大小限制。
- 下载使用短期签名 URL 或鉴权流，不暴露内部对象键。
- 对富文本和模型输出做安全渲染，不直接注入 HTML。
- 数据库使用参数化查询；任何动态排序字段必须来自允许列表。
- 对登录、上传、生成、反馈接口分别限流。
- CORS、Cookie、CSRF 策略随认证方式一起设计，不能使用宽泛生产默认值。

## 12. Definition of Done

一项功能完成需同时满足：

- 产品验收标准与异常/空状态已实现。
- 权限、输入上限、幂等和失败恢复已考虑。
- 单元/集成/API 测试覆盖核心行为。
- 有数据库变化时包含可前滚迁移和回滚/恢复说明。
- 关键日志、指标和错误码可观测。
- OpenAPI、用户文案和相关文档同步更新。
- 代码通过质量门禁，容器可构建，未提交密钥或真实用户资料。

## 13. 本地开发

```bash
python3 scripts/init_local_env.py
uv sync
uv run pytest
docker compose up --build -d
docker compose ps
```

已有早期 `.env` 时先运行 `python3 scripts/upgrade_local_env.py`。升级脚本保留现有数据库所有者凭据，并添加独立的迁移与运行账号随机密码。

本地 API 与数据库端口只绑定回环地址。`compose.override.yaml` 发布数据库端口供本地工具使用。单台云服务器生产运行使用独立的 `compose.production.yaml`：独立站点可按 [上线与数据恢复手册](./production-operations.md)由 Caddy 发布 80/443；当前 fisher-ai.com 使用宝塔 Nginx 与门户 overlay，实际参数见[部署记录](./deployment-fisher-ai.md)。部署前检查秘密文件、备份状态、域名和端口暴露；不能叠加本地 `compose.override.yaml`。API 镜像使用固定的非 root UID/GID，并在 Compose 中启用只读根文件系统、移除 Linux capabilities 和 `no-new-privileges`。

`database-init` 是可重复执行的一次性容器，先以管理员账号幂等配置角色与 schema，再以迁移账号运行 Alembic。API 只接收运行账号凭据。手动执行迁移使用 `docker compose run --rm database-init`，不要从 API 启动流程调用 `create_all()`。当前 `0001_database_baseline` 只建立 Alembic 版本基线，不创建尚未定稿的业务表。

`0002_document_ingestion` 创建资料闭环所需的五张表。`0003`–`0007` 逐步增加游标索引、租户完整性、Worker 心跳与统一 citation locator；`0008_cited_rag` 增加索引、分块和独立问答任务；`0009_learning_core` 增加集合、连续对话、反馈、Quiz 与作答表；`0010_user_auth` 增加账号、成员、密码哈希、会话及登录限流；`0011_version_purge_trigger` 将学习记录清理限定于解析版本实际删除。每次改模型后运行 `docker compose run --rm database-init alembic check`，并从空库与上一 revision 验证升级。

`0016_conversation_memory` 为对话添加可空的同资料范围记忆投影。发布顺序为迁移、兼容 v1/v2 的 Worker、创建 v2 运行的 API/Web；避免旧 Worker 领取并拒绝新运行。旧运行仍按 `study-graph-v1` 恢复；回退应用时保留新增列，避免丢失会话数据。知识点 PDF 使用随应用镜像分发的 Noto Sans SC 字体和 ReportLab，不依赖服务器系统字体。

当前 Worker 直接领取 PostgreSQL 中的持久任务，使用短事务、租约、尝试上限和幂等键。PDF 解析在禁止网络的子进程执行，API 只做流式上传与结构校验，不解析正文。将 Redis 用作任务队列或引入 Celery 前先依据 [ADR-0001](./adr/0001-postgresql-document-jobs.md) 的迁移门槛评审，不能形成第二份任务状态。

Redis 当前只做 API 限流：登录按邮箱摘要，上传、索引、问答、Quiz 和反馈按工作区计数。限流脚本原子设置过期时间，并允许同一工作区和动作的幂等键重放。超额返回 `RATE_LIMITED`（429）；Redis 不可用返回 `RATE_LIMIT_UNAVAILABLE`（503），不继续执行高成本操作。开发模式直接运行 API 时默认关闭 Redis 限流；Compose 中开启。生产模式必须启用并配置 Redis 密码。

当前固定窗口额度：登录每邮箱 15 分钟 20 次；上传和重新处理每工作区每小时 30 次；索引每小时 30 次；问答每小时 60 次；Quiz 创建及重评每小时 20 次；反馈每小时 120 次。幂等键重放不重复扣额。Redis 键使用带服务端秘密的 HMAC 摘要，不保存邮箱或资料内容；Redis 重启后额度重置。

当前健康检查：`/health/live` 验证 API 进程存活，`/health/ready` 验证数据库和 pgvector 可用，`/health/worker` 验证文档 Worker 最近心跳。API 就绪与 Worker 就绪分开，避免后台处理故障把只读 API 误判为不可用；外部模型故障通过降级和指标展示，不应让整个 API 不健康。

React 前端位于 `web/`。服务端状态由 TanStack Query 管理，当前资料与 PDF 页码进入 URL。PDF 使用授权原文件接口和按需加载的 PDF.js 预览；历史非 PDF 资料只显示兼容提示，前端不请求或展示整页提取正文。引用只显示回答中已保存的摘录；后台解析正文仍供检索、问答和 Quiz 使用。筛选和出题设置统一使用项目的 `SelectField` 自定义下拉组件，保留键盘与焦点交互。开发与预览代理只转发同源 `/api` 请求，浏览器通过 HttpOnly 会话 Cookie 认证，写请求携带从登录响应取得的 CSRF 标记。组件测试使用 Vitest/Testing Library，真实上传预览旅程使用 Playwright。CI 同时执行前端类型检查、组件测试和生产构建，不能只验证后端。

## 学习图开发与验证

应用逻辑只依赖 `StudyExecutor`、`AgentRunPersistence`；LangGraph 与 SQLAlchemy 代码放在适配器，不向领域层导入。修改图/Prompt/计划需提升相应版本，未知版本运行进入阻塞。调用预算跨恢复累计，严禁自动重发未知请求；阶段成果与恢复位置同事务。运行角色不执行 saver setup，不开启包含原文的外部追踪。修改后的重点门禁为图单元、工作区隔离、PostgreSQL 恢复/删除、0013→0014 迁移及完整移动端流程，详见 [工作流设计](./agent-workflow-development.md#12-本次交付与设计调整)。

### 快速问答与复习的验证

真实课程 PDF 的金标准与当前对话 Agent 实测使用 `scripts.evaluate_pdf_agent.py`，流程与评分边界见 [真实 PDF 评测指南](./real-pdf-agent-evaluation.md)。私人金标准、回答和报告只存 `evals/local/`；默认预检不调用模型，`--live` 才运行付费服务。不要将关键词初筛通过率当成语义正确率。

- `pytest -q tests/test_focused_answer.py tests/test_graph_run_processor.py tests/test_agent_evaluation.py tests/test_spaced_review.py`：引用、证据不足、预算/取消、旧图兼容、生成调用数及评分规则/API。
- `python -m scripts.verify_spaced_review --isolated [--previous]`：仅在预装 pgvector 的空临时库运行；验证空库或 0016 升级、元数据一致、个人/空间隔离、重复导入、同键重放、并发 revision、到期索引、成员与来源删除。
- `pnpm --dir web test src/pages/ReviewPage.test.tsx`：答案隐藏、键盘展开、保留请求键的网络重试及加载/错误/空状态。
- `pnpm --dir web exec playwright test e2e/spaced-review.spec.ts`：从错题导入到复习自评及刷新恢复，覆盖 390px 与 1280px。需先启动测试前端。


### 长 PDF 覆盖验证

`evals/overview-v1.json` 定义长文、极密首页、可完整读取短文三个覆盖案例，`tests/test_overview.py` 比较旧 15 片段选样与新策略的实际页覆盖，并验证多文档首批配额、批次上限及知识点合并。它衡量输入来源覆盖，不能替代真实模型的语义提取评估。

`python -m scripts.verify_long_pdf_overview --isolated` 必须使用没有工作区的已迁移临时数据库。该脚本构造 72 页正文及越权/未选择资料，验证读取 64 页、8 次有界生成、跨空间隔离、调用结果回放、逐批合并、预算限制保留成果、取消及来源删除。模型为确定性 fixture，不调用外部付费服务。UI 验证使用 `AgentRunCard.test.tsx` 与 `e2e/long-pdf-overview.spec.ts`，覆盖窄屏进度和刷新恢复。


知识点联动阅读的定向检查：后端 `tests/test_rag.py`、`test_focused_answer.py`、`test_langgraph_workflow.py`、`test_graph_run_processor.py`、`test_overview.py`、`test_study_pdf.py` 检查逐点结构、范围外来源拒绝、页码解析、旧回执、分批合并及导出；前端 `StudyPage.test.tsx`、`StudySourcePanel.test.tsx`、`PdfPreview.test.tsx` 与 `web/e2e/study-linked-reading.spec.ts` 检查历史兼容、预览失败恢复、跟随开关、跨资料切换、重复下载和 390px 键盘返回。浏览器使用合成 PDF，不调用真实模型；实际讲解质量仍需真实资料评估。

### v5 PDF 生成质量与诊断验证

`pytest -q tests/test_study_generation.py tests/test_rag.py tests/test_pdf_parser.py tests/test_graph_run_processor.py tests/test_pdf_agent_evaluation.py` 验证原文摘录编号、未知编号拒绝、换行恢复的唯一性、数字/措辞不更改、一次修复的实际计费、预算和取消、未知请求不重试、混合图文与小 logo 区分、OCR 多行标签、v4 批次恢复以及跨状态快照发布后的评测读取。

隔离 PostgreSQL 脚本 `verify_long_pdf_overview --isolated` 另验证两次生成回执恢复不会再次计费、诊断去重、错误租约和额外敏感字段拒绝。fixture 不调用外部模型。当前 v5 使用已有 JSONB 和回执表，无额外迁移；采用现有 0017 临时库运行，禁止连接真实资料库。

真实验证必须固定 PDF/金标准哈希，区分中途调试运行和最终冻结版本；报告保存应用源码指纹、Prompt/图/解析版本、主问题与前置轮次用量。关键词和引文初筛通过仍需按 rubric 检查语义，不通过修改参考答案提高分数。OCR 位置不能用于宣称箭头识别正确。
