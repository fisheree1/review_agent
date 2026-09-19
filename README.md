# Review Agent

面向学习资料的 AI 学习平台后端。项目计划支持上传 PDF、Word、PPT，构建可追溯的 RAG 知识库，并按用户要求生成 Quiz。

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
- API 与数据库存活/就绪健康检查。
- Docker Compose 本地开发环境。
- 基础自动化测试。

## 启动

首次启动先生成只保存在本机的 `.env`。脚本会为数据库管理员、迁移账号和应用运行账号分别创建 64 位随机密码，并把文件权限设置为 `0600`：

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

接口文档：<http://localhost:8000/docs>

Compose 启动时会先运行一次幂等的 `database-init` 任务：管理员账号创建或更新最小权限角色与 schema，随后迁移账号执行 `alembic upgrade head`。API 容器只接收运行账号凭据。需要单独重跑初始化与迁移时使用：

```bash
docker compose run --rm database-init
```

停止服务：

```bash
docker compose down
```

数据库数据保存在 Docker volume 中；只有执行 `docker compose down -v` 才会删除本地数据库数据。

默认本地配置只把 API 和 PostgreSQL 发布到 `127.0.0.1`。`compose.override.yaml` 仅用于本地数据库工具连接；生产或类生产环境应显式使用 `docker compose -f compose.yaml ...`，基础配置不会发布 PostgreSQL 端口。生产密钥应由秘密管理服务注入，而不是使用 `.env`。管理员凭据只提供给数据库和一次性初始化容器，迁移凭据只提供给初始化容器，API 只获得无 DDL 权限的运行凭据。

## 本地开发与测试

```bash
uv sync
uv run fastapi dev app/main.py
uv run pytest
```
