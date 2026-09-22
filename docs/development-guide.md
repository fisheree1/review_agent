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

本地 API 与数据库端口只绑定回环地址。`compose.override.yaml` 发布数据库端口供本地工具使用；生产运行只使用基础 `compose.yaml`，不发布数据库端口。API 镜像使用固定的非 root UID/GID，并在 Compose 中启用只读根文件系统、移除 Linux capabilities 和 `no-new-privileges`。

`database-init` 是可重复执行的一次性容器，先以管理员账号幂等配置角色与 schema，再以迁移账号运行 Alembic。API 只接收运行账号凭据。手动执行迁移使用 `docker compose run --rm database-init`，不要从 API 启动流程调用 `create_all()`。当前 `0001_database_baseline` 只建立 Alembic 版本基线，不创建尚未定稿的业务表。

`0002_document_ingestion` 只创建资料闭环所需的 `workspaces`、`documents`、`document_versions`、`document_pages` 和 `processing_jobs`。`0003_document_list_index` 增加资料库游标查询索引；`0004_document_tenant_integrity` 和 `0006_document_version_owner` 把内容、版本、文档的租户归属落实为数据库约束；`0005_worker_heartbeat` 增加后台 Worker 心跳；`0007_unified_citation_locator` 为已有 PDF 数据回填页码 locator，并增量支持 DOCX 标题与 PPTX 幻灯片定位；`0008_cited_rag` 才增加索引、分块和独立问答任务表，仍不预建多轮对话或 Quiz 表。每次改模型后运行 `docker compose run --rm database-init alembic check`，并从空库与上一 revision 验证升级。

当前 Worker 直接领取 PostgreSQL 中的持久任务，使用短事务、租约、尝试上限和幂等键。PDF 与 OOXML 解析在禁止网络的子进程执行，API 只做流式上传与结构校验，不解析正文。引入 Redis/Celery 前先依据 [ADR-0001](./adr/0001-postgresql-document-jobs.md) 的迁移门槛评审，不能形成第二份任务状态。

当前健康检查：`/health/live` 验证 API 进程存活，`/health/ready` 验证数据库和 pgvector 可用，`/health/worker` 验证文档 Worker 最近心跳。API 就绪与 Worker 就绪分开，避免后台处理故障把只读 API 误判为不可用；外部模型故障通过降级和指标展示，不应让整个 API 不健康。

React 前端位于 `web/`。服务端状态由 TanStack Query 管理，当前资料与内容单元序号进入 URL，正文按当前单元读取，界面根据 citation locator 显示页码、标题路径或幻灯片；主题、字号和行宽只保存在本地。开发与预览代理从 Node 进程环境读取 `LOCAL_API_TOKEN` 并为 `/api` 请求附加认证头；禁止使用 `VITE_` 前缀暴露令牌。组件测试使用 Vitest/Testing Library，真实上传阅读旅程使用 Playwright。CI 同时执行前端类型检查、组件测试和生产构建，不能只验证后端。
