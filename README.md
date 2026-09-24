# Review Agent

面向学习资料的 AI 学习平台。当前具备 React 资料库与阅读器、私有文件存储、PDF/DOCX/PPTX 后台解析、百炼 Qwen Embedding + DeepSeek 引用问答、资料集合、连续对话、回答反馈与 Quiz 作答复习。

RAG 的配置、使用、接口、限制与验证步骤见 [可引用 RAG 实现文档](docs/rag-implementation.md)。在本地 `.env` 配置 `DASHSCOPE_API_KEY`、`DASHSCOPE_EMBEDDING_URL`、`DEEPSEEK_API_KEY` 后重新构建启动，在阅读页选择“基于此资料提问”→“准备资料问答”。真实密钥只注入 Worker，不进入浏览器。

本机 CA6000 课件的单资料真实评测及多资料能力的后续真实评测计划，见 [CA6000 RAG 评测](docs/ca6000-rag-evaluation.md)。

进入“学习问答”可创建最多包含 5 份已索引资料的集合或对话，在对话中切换后续问题范围，并对已保存的回答反馈。进入“Quiz”可按题型数量、难度、语言和知识点出题；作答后查看评分、解析、薄弱知识点及原文。任务由 Worker 异步处理，页面会显示处理状态。

## 项目文档

开始开发业务功能前，请先阅读 [文档中心](./docs/README.md)：

- [产品需求文档](./docs/product-requirements.md)
- [系统架构文档](./docs/architecture.md)
- [开发规范](./docs/development-guide.md)
- [数据库设计](./docs/database-design.md)
- [UI 与长时间阅读体验规范](./docs/ui-design-system.md)
- [Git 与 GitHub 管理规范](./docs/git-github-workflow.md)
- [单台云服务器上线与数据恢复](./docs/production-operations.md)

## 需求梳理

### 核心用户流程

1. 用户上传 PDF、DOCX 或 PPTX 学习资料。
2. 系统校验文件，提取正文、页码/章节/幻灯片等结构信息。
3. 后台任务将内容清洗、分块、向量化，并写入知识库。
4. 用户基于一份或多份资料提问；Agent 检索相关片段后作答，并返回来源引用。
5. 用户指定题型、数量、难度、范围和语言，系统生成 Quiz。
6. 用户作答后获得评分、解析、薄弱知识点和可追溯出处。

### 第一版范围（MVP）

- 文档：PDF、DOCX、PPTX 上传、状态查询、删除与重新索引。
- RAG：文本提取、分块、Embedding、向量检索、引用来源。
- Agent：问答编排、上下文选择、回答生成、失败重试与基础审计日志。
- Quiz：单选、多选、判断、简答；支持数量、难度和资料范围约束。
- 数据：文档元数据、文本块、向量、对话、Quiz、作答记录。
- 运维：FastAPI、PostgreSQL + pgvector、Docker Compose、健康检查、环境变量。

### 当前主要模块边界

```text
app/
  auth/          # 账号、会话与工作区身份
  core/          # 配置、数据库、日志、安全
  documents/     # 上传、解析、文档生命周期与接口
  rag/           # 分块、Embedding、检索、引用
  learning/      # 集合、连续对话、反馈、Quiz 与作答
  jobs/          # Worker 入口与心跳
```

文档解析与模型调用由独立 Worker 领取 PostgreSQL 中的持久任务；模型与文件存储通过应用接口隔离。

## 当前已完成的基础设施

- FastAPI 服务及开发环境 Swagger 文档。
- PostgreSQL 17 + pgvector。
- Alembic 迁移、数据库管理员/迁移/运行账号分离。
- 私有 MinIO 原文件存储，管理凭据与应用 bucket 凭据分离。
- 独立 Worker、持久化任务租约与幂等重试。
- PDF、DOCX、PPTX 流式上传校验、正文解析、状态查询、失败说明和删除。
- PDF 页码、DOCX 标题路径、PPTX 幻灯片编号使用统一 citation locator。
- React 资料库、上传进度、处理状态、按来源位置阅读和引用面板。
- 资料集合、多资料问答、范围切换、回答反馈，以及有来源校验的 Quiz 生成、评分与复习。
- API、数据库与文档 Worker 存活/就绪健康检查。
- 后端、前端与容器闭环自动化测试。
- Redis 短期限流及单台云服务器生产 Compose、加密异机备份和恢复手册；真实云服务器部署仍需单独验收。

## 启动

首次启动先生成只保存在本机的 `.env`。脚本会生成数据库角色、API 令牌、工作区 ID、对象存储管理账号和受限应用账号所需的随机值，并把文件权限设置为 `0600`：

```bash
python3 scripts/init_local_env.py
```

如果 `.env` 已存在，脚本会拒绝覆盖，避免意外轮换正在使用的数据库密码。

从早期单账号配置升级时运行下面的命令。脚本保留原数据库所有者的用户名和密码，只增加迁移与运行账号凭据：

```bash
python3 scripts/upgrade_local_env.py
```

```bash
docker compose up --build -d
docker compose ps
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
curl http://localhost:8000/health/worker
```

阅读页面：<http://127.0.0.1:5173>

云服务器上线使用独立的 [生产部署与备份手册](./docs/production-operations.md)。本地 `compose.yaml` 和 `.env` 不直接作为公网部署配置。

Redis 在本地和生产 Compose 中只保存带过期时间的限流计数；登录、上传、索引、问答、Quiz 和反馈使用它限制突发请求。资料、会话、任务与可恢复状态仍以 PostgreSQL 为准。Redis 不发布主机端口，生产实例使用受限 ACL。

开发环境接口文档：<http://localhost:8000/docs>。生产环境关闭此入口。

Web 容器通过同源代理访问 API。浏览器使用账号密码登录和服务端会话；开发令牌仅供本地脚本兼容，生产环境不接受。首次部署默认关闭自行注册，先创建账号并接管已有本地空间：

```bash
docker compose run --rm --no-deps api python -m scripts.create_user --email you@example.com --claim-local-workspace
```

新部署无旧资料时去掉 `--claim-local-workspace`，系统会创建个人空间。密码通过终端交互输入，至少 12 字符。需要自行注册时设置 `AUTH_ALLOW_SIGNUP=true`；生产环境还须设置实际 HTTPS 页面来源 `AUTH_PUBLIC_ORIGIN`，在同源 HTTPS 入口下提供 Web 与 API。

Compose 启动时先运行两个幂等初始化任务：`database-init` 配置最小权限数据库角色并执行 Alembic；`storage-init` 创建私有 bucket 和仅可访问该 bucket 的应用账号。API 与 Worker 不接收数据库或对象存储管理凭据。需要单独重跑初始化与迁移时使用：

```bash
docker compose run --rm database-init
docker compose run --rm storage-init
docker compose exec -T api python -m scripts.verify_runtime_database_access
docker compose exec -T api python -m scripts.verify_storage_access
```

### 上传并查看资料正文

本地令牌来自 `.env`，不要把它复制进代码、文档或 Git：

```bash
set -a
source .env
set +a

curl -X POST http://127.0.0.1:8000/api/v1/documents \
  -H "Authorization: Bearer ${LOCAL_API_TOKEN}" \
  -H "Idempotency-Key: my-first-document" \
  -F "file=@/absolute/path/to/material.pdf;type=application/pdf"

curl http://127.0.0.1:8000/api/v1/documents/<document-id> \
  -H "Authorization: Bearer ${LOCAL_API_TOKEN}"

curl 'http://127.0.0.1:8000/api/v1/documents/<document-id>/content?ordinal=1' \
  -H "Authorization: Bearer ${LOCAL_API_TOKEN}"
```

界面一次可选择最多 10 份文件，逐份显示进度与结果，并可重试失败项。失败资料可用 `POST /api/v1/documents/{id}:retry` 并携带稳定的 `Idempotency-Key` 重试；删除使用 `DELETE /api/v1/documents/{id}`。PDF 文字页直接提取，扫描页由 Worker 使用英文和简体中文 OCR 识别并保留页码；完全无法识别文字时返回 `PDF_TEXT_NOT_FOUND`。DOCX 按标题层级分段，PPTX 按幻灯片分段并读取演讲者备注；Office 图片中的文字暂不执行 OCR。

可用一份本地 PDF 运行完整验收；脚本不会把资料加入 Git，成功解析的样例会保留在本机资料库：

```bash
uv run python -m scripts.verify_document_flow /absolute/path/to/material.pdf
```

停止服务：

```bash
docker compose down
```

数据库和原文件分别保存在 Docker volumes 中；只有执行 `docker compose down -v` 才会删除这两类本地数据。

默认本地 Compose 将 Web 和 API 发布到 `127.0.0.1`；`compose.override.yaml` 仅供本地数据库/存储检查，并将这两个端口也绑定到回环地址。Redis 只在 Compose 内部网络开放。生产环境使用独立的 `compose.production.yaml` 和服务器外置的秘密文件，公网只开放 HTTPS 入口；管理凭据仅提供给基础设施和一次性初始化容器。

## 本地开发与测试

```bash
uv sync
uv run fastapi dev app/main.py
uv run pytest

pnpm --dir web install --frozen-lockfile
pnpm --dir web dev
pnpm --dir web typecheck
pnpm --dir web test
pnpm --dir web build
```

CI 对后端执行 Ruff、Mypy 与 Pytest，对前端执行 TypeScript、Vitest 与生产构建，并用 Docker Compose 验证迁移、最小权限、私有存储和文档上传—解析—删除闭环。
