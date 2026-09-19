# Review Agent

面向学习资料的 AI 学习平台。当前已具备 React 资料库与阅读器、FastAPI 文档接口、私有文件存储和后台 PDF 解析；后续将扩展可追溯 RAG 与按要求生成 Quiz。

## 项目文档

开始开发业务功能前，请先阅读 [文档中心](./docs/README.md)：

- [产品需求文档](./docs/product-requirements.md)
- [系统架构文档](./docs/architecture.md)
- [开发规范](./docs/development-guide.md)
- [数据库设计](./docs/database-design.md)
- [UI 与长时间阅读体验规范](./docs/ui-design-system.md)
- [Git 与 GitHub 管理规范](./docs/git-github-workflow.md)

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

### 建议的后续模块边界

```text
app/
  api/           # HTTP 接口与请求/响应模型
  core/          # 配置、数据库、日志、安全
  documents/     # 上传、解析、文档生命周期
  rag/           # 分块、Embedding、检索、引用
  agents/        # Agent 工作流和模型适配器
  quizzes/       # 出题、校验、评分、解析
  jobs/          # 异步索引任务
```

文档解析与模型调用建议放入异步任务队列，避免大文件处理占用 API 请求。模型提供商、Embedding 模型与文件存储应通过接口隔离，方便后续替换。

## 当前已完成的基础设施

- FastAPI 服务及 Swagger 文档。
- PostgreSQL 17 + pgvector。
- Alembic 迁移、数据库管理员/迁移/运行账号分离。
- 私有 MinIO 原文件存储，管理凭据与应用 bucket 凭据分离。
- 独立 Worker、持久化任务租约与幂等重试。
- 文本型 PDF 流式上传校验、逐页正文解析、状态查询、失败说明和删除。
- React 资料库、上传进度、处理状态、按页正文阅读和来源定位面板。
- API 与数据库存活/就绪健康检查。
- 基础自动化测试。

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
```

阅读页面：<http://127.0.0.1:5173>

接口文档：<http://localhost:8000/docs>

Web 容器通过同源代理访问 API，并在服务端附加本地开发令牌；令牌不会写入浏览器包。当前代理只用于回环地址上的单用户开发环境，生产部署需接入正式认证网关。

Compose 启动时先运行两个幂等初始化任务：`database-init` 配置最小权限数据库角色并执行 Alembic；`storage-init` 创建私有 bucket 和仅可访问该 bucket 的应用账号。API 与 Worker 不接收数据库或对象存储管理凭据。需要单独重跑初始化与迁移时使用：

```bash
docker compose run --rm database-init
docker compose run --rm storage-init
docker compose exec -T api python -m scripts.verify_runtime_database_access
docker compose exec -T api python -m scripts.verify_storage_access
```

### 上传并查看 PDF 正文

本地令牌来自 `.env`，不要把它复制进代码、文档或 Git：

```bash
set -a
source .env
set +a

curl -X POST http://127.0.0.1:8000/api/v1/documents \
  -H "Authorization: Bearer ${LOCAL_API_TOKEN}" \
  -H "Idempotency-Key: my-first-pdf" \
  -F "file=@/absolute/path/to/material.pdf;type=application/pdf"

curl http://127.0.0.1:8000/api/v1/documents/<document-id> \
  -H "Authorization: Bearer ${LOCAL_API_TOKEN}"

curl http://127.0.0.1:8000/api/v1/documents/<document-id>/pages \
  -H "Authorization: Bearer ${LOCAL_API_TOKEN}"
```

失败资料可用 `POST /api/v1/documents/{id}:retry` 并携带稳定的 `Idempotency-Key` 重试；删除使用 `DELETE /api/v1/documents/{id}`。当前只支持具有可提取文字的 PDF，扫描版会返回 `PDF_TEXT_NOT_FOUND` 并提示后续需要 OCR。

可用一份本地 PDF 运行完整验收；脚本不会把资料加入 Git，成功解析的样例会保留在本机资料库：

```bash
uv run python -m scripts.verify_document_flow /absolute/path/to/material.pdf
```

停止服务：

```bash
docker compose down
```

数据库和原文件分别保存在 Docker volumes 中；只有执行 `docker compose down -v` 才会删除这两类本地数据。

默认本地配置只把 API、PostgreSQL 和 MinIO API 发布到 `127.0.0.1`。`compose.override.yaml` 仅供本地数据库/存储检查；生产或类生产环境应显式使用 `docker compose -f compose.yaml ...`，基础配置不发布数据库与对象存储端口。生产密钥应由秘密管理服务注入，而不是使用 `.env`。管理凭据只提供给基础设施和一次性初始化容器；API/Worker 只获得数据库 DML 权限和指定 bucket 的对象读写删除权限。

## 本地开发与测试

```bash
uv sync
uv run fastapi dev app/main.py
uv run pytest

pnpm --dir web install --frozen-lockfile
LOCAL_API_TOKEN=<本地令牌> pnpm --dir web dev
pnpm --dir web typecheck
pnpm --dir web test
```
