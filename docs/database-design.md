# 数据库设计文档

版本：0.1
数据库：PostgreSQL 17 + pgvector
状态：`0001`–`0007` 建立最小权限、资料摄取与统一引用定位；`0008_cited_rag` 增加版本化索引、1024 维分块和单资料问答任务；`0009_learning_core` 增加集合、连续对话、反馈、Quiz 与作答；`0010_user_auth` 增加正式账号、成员、密码哈希与服务端会话；`0011_version_purge_trigger` 修正来源版本删除的清理触发条件。

## 当前 RAG 实现与目标设计的差异

以下实体章节保留长期目标。当前 `0008` 只增加 `document_indexes`、`document_chunks`、`rag_questions` 三张表：索引由 `(document_version_id, profile)` 唯一标识，分块由 `(index_id, ordinal)` 唯一标识；复合外键从分块经索引绑定到同一 workspace 的不可变解析版本。来源单元与字符起止保存在分块中，locator 通过固定版本的 `document_pages` 读取。向量固定 `vector(1024)`。

`rag_questions` 是独立提问任务与结果，不冒充连续对话；绑定 workspace 和解析版本，按空间强制请求幂等。回答 JSON 仅保存已验证的逐条 claim、原文摘录和来源位置，usage 保存模型、策略版本与生成 Token。版本被删除时三张表级联清理，不保留悬空原文副本。

当前数据量使用带范围索引的精确向量查询与动态全文排名，尚未创建目标设计中的 HNSW/GIN；需要以召回、查询计划和规模证据决定何时加入。当前百炼 `qwen3.7-text-embedding` 固定输出 1024 维，profile 与旧 Voyage 索引不同；切换不修改表结构、不删除旧向量，已有资料按需重新索引。升级、回退和集成检查见 [RAG 文档](./rag-implementation.md)。

## 1. 设计目标

- 数据隔离优先于开发便利，所有用户资源都能追溯到 workspace。
- 业务事实只保存一份，派生数据可重建并明确版本。
- 支持异步任务的幂等、重试、审计和故障恢复。
- 常见列表、权限过滤和 RAG 检索有稳定索引路径。
- schema 通过迁移演进，支持先部署兼容结构、再切换代码、最后清理旧结构。

## 2. 数据分类与存储位置

| 数据 | 存储 | 说明 |
| --- | --- | --- |
| 用户、空间、文档元数据 | PostgreSQL | 权限与业务事实 |
| 原始 PDF/DOCX/PPTX | 对象存储 | 私有 bucket，数据库保存对象键和哈希 |
| 当前有序正文 | PostgreSQL `document_pages` | 兼容表名；保存页、标题段或幻灯片及统一 locator |
| 文本分块与来源定位 | PostgreSQL | RAG 的可追溯最小单元 |
| Embedding | pgvector | 派生数据，带模型与索引版本 |
| 对话、Quiz、作答 | PostgreSQL | 业务记录与学习历史 |
| 当前处理任务 | PostgreSQL `processing_jobs` | 可租约领取、审计和幂等重试；当前不依赖 Redis |
| 密钥 | Secret Manager | 禁止保存明文 API key 到业务表 |

## 3. 命名与通用字段

- 表名、列名使用 `snake_case`，表名使用复数。
- 内部主键优先 `bigint generated always as identity`，保证索引紧凑和连接性能。
- 对外暴露不可枚举的 `public_id uuid`，默认 `gen_random_uuid()`，并设唯一索引。
- 时间使用 `timestamptz`、UTC；命名 `created_at`、`updated_at`、`deleted_at`。
- 状态使用数据库可约束的 `text + CHECK`；高频变更状态不使用 PostgreSQL ENUM，避免迁移摩擦。
- 金额/Token 使用整数；模型费用可保存最小货币单位或高精度 numeric。
- 业务表包含 `workspace_id`；API 不接受客户端指定不属于自己的 workspace。
- 乐观并发使用 `version integer`，更新时比较版本防止静默覆盖。

## 4. 实体关系

```mermaid
erDiagram
    USERS ||--o{ WORKSPACE_MEMBERS : joins
    WORKSPACES ||--o{ WORKSPACE_MEMBERS : has
    WORKSPACES ||--o{ COLLECTIONS : owns
    WORKSPACES ||--o{ DOCUMENTS : owns
    COLLECTIONS ||--o{ COLLECTION_DOCUMENTS : contains
    DOCUMENTS ||--o{ COLLECTION_DOCUMENTS : belongs
    DOCUMENTS ||--o{ DOCUMENT_VERSIONS : has
    DOCUMENT_VERSIONS ||--o{ DOCUMENT_CHUNKS : splits
    DOCUMENTS ||--o{ PROCESSING_JOBS : processes
    WORKSPACES ||--o{ CONVERSATIONS : owns
    CONVERSATIONS ||--o{ MESSAGES : contains
    MESSAGES ||--o{ MESSAGE_CITATIONS : cites
    DOCUMENT_CHUNKS ||--o{ MESSAGE_CITATIONS : supports
    WORKSPACES ||--o{ QUIZZES : owns
    QUIZZES ||--o{ QUIZ_QUESTIONS : contains
    QUIZ_QUESTIONS ||--o{ QUESTION_SOURCES : cites
    DOCUMENT_CHUNKS ||--o{ QUESTION_SOURCES : supports
    QUIZZES ||--o{ QUIZ_ATTEMPTS : attempted
    QUIZ_ATTEMPTS ||--o{ QUIZ_ANSWERS : has
```

## 5. 表设计

### 5.1 身份与租户

#### `users`

| 字段 | 类型 | 约束/用途 |
| --- | --- | --- |
| id | bigint identity | PK，内部使用 |
| public_id | uuid | UNIQUE，对外 ID |
| email_normalized | varchar(254) | UNIQUE，登录标识 |
| status | varchar(20) | active / suspended |
| created_at | timestamptz | 创建时间 |

密码哈希保存在独立 `review_agent_auth.password_credentials` 表，使用 Argon2id；会话表仅存令牌 SHA-256 摘要、CSRF 标记、工作区、过期与撤销时间。`login_limits` 按邮箱摘要记录失败次数与锁定期限。认证 schema 与应用 schema 同由迁移角色拥有，运行角色只拥有 DML 权限。

#### `workspaces`

当前迁移包含 `id`、`public_id`、`name`、`status`、`created_at`、`updated_at`；成员关系由 `workspace_members` 维护。

即使 MVP 是个人空间，也保留 workspace 边界，避免未来给所有表补租户列。

#### `workspace_members`

联合唯一键 `(user_id, workspace_id)`；当前角色为 `owner/member`。会话对用户、成员和工作区状态进行校验；应用层按会话的 workspace 过滤资料、问答和 Quiz。

### 5.2 资料与索引

#### `collections`

`workspace_id`、`public_id`、`name`、`description`、时间字段和可选软删除字段。

#### `documents`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| workspace_id | bigint | 所有权，NOT NULL |
| public_id | uuid | 对外 ID |
| original_filename | text | 展示名称，不作为路径 |
| media_type | text | 服务端检测后的 MIME |
| byte_size | bigint | CHECK > 0 |
| sha256 | char(64) | 内容哈希 |
| object_key | text | 私有对象键，workspace 内唯一 |
| status | text | 文档状态机 |
| active_version_id | bigint | 当前可检索版本，可空 |
| failure_code | text | 稳定错误码，不保存敏感堆栈 |
| page_count | integer | 兼容字段；表示当前版本内容单元数，新接口暴露为 `content_count` |
| language_code | text | 可空，BCP 47 风格代码 |
| created_by | bigint | 上传用户 |
| created_at / updated_at / deleted_at | timestamptz | 生命周期 |

当前 MVP 对未删除资料使用 `(workspace_id, sha256)` 部分唯一索引，重复上传返回已有资料，避免重复解析。若产品允许同一内容以多个展示实体存在，再通过独立 content object 表分离物理去重与业务文档。

#### `document_versions`

当前保存 `document_id`、`workspace_id`、`version_no`、来源哈希、解析器名称/版本、状态、内容单元数和时间。唯一键 `(document_id, version_no)` 以及 `(document_id, source_sha256, parser_name, parser_version)` 防止任务重放重复创建版本；复合外键保证 active version 属于对应 document。

新索引完成前不改 `documents.active_version_id`。切换和版本状态更新在同一事务完成。

#### `document_pages`

该表名与 `page_number` 是 PDF 阶段留下的兼容命名，应用层把 `page_number` 解释为从 1 开始的内容单元 `ordinal`。每行还保存 `locator_kind`（page / heading / slide）、`locator_position`、可选 `locator_title` 和 JSON 标题路径 `locator_path`。旧 PDF 行由迁移回填为 page locator。`(document_version_id, page_number)` 唯一；复合外键保证内容与版本属于同一 workspace，读取仍必须同时经过 workspace、document 和 active version 过滤。

#### `document_chunks`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| document_version_id | bigint | FK，级联删除版本 |
| workspace_id | bigint | 反规范化用于强制权限过滤 |
| ordinal | integer | 版本内稳定顺序 |
| content | text | 清洗后的检索正文 |
| token_count | integer | 上下文预算 |
| source_type | text | page / heading / slide，与 citation locator 一致 |
| source_start / source_end | integer | 页码或幻灯片范围 |
| heading_path | text[] | 章节路径 |
| metadata | jsonb | 低频、非核心的解析元数据 |
| content_tsv | tsvector | 关键词检索字段 |
| embedding | vector(N) | N 在选择模型后由迁移固定 |

唯一键 `(document_version_id, ordinal)`。`workspace_id` 必须与关联 document 一致，写入由受控 Repository 保证并通过集成测试覆盖。

Embedding 维度不能模糊配置后直接上线。首个模型确定后固定 `vector(N)` 并建立索引；更换不同维度模型时创建新列/表或新版本迁移并完整重建，禁止混放不同维度。

#### `collection_documents`

联合主键 `(collection_id, document_id)`，包含 `added_at`、`added_by`。

### 5.3 任务与可靠性

#### `processing_jobs`

包含 `public_id`、`workspace_id`、`document_id`、`job_type`、`idempotency_key`、`status`、`attempt_count`、`max_attempts`、`available_at`、`lease_expires_at`、`failure_code`、`failure_message`、`retry_of_job_id` 和时间字段。

唯一键 `(workspace_id, job_type, idempotency_key)`。不要把完整异常、模型正文或文件内容塞入 `failure_detail`；详细诊断进入受控日志系统。

#### `worker_heartbeats`

保存 `worker_id`、`worker_type`、`started_at` 和 `last_seen_at`。Worker 以固定间隔 upsert；API 只按类型读取最新心跳并结合过期阈值返回就绪状态。该表仅用于可用性判断，不替代任务状态或审计记录。

#### `outbox_events`

包含 `aggregate_type`、`aggregate_id`、`event_type`、`payload jsonb`、`created_at`、`published_at`、`attempts`。业务写入与 outbox 写入同一事务；发布器使用 `FOR UPDATE SKIP LOCKED` 批量领取。

### 5.4 集合、对话与引用（`0009`）

`collections` 保存工作区内集合，`collection_documents` 使用复合外键约束集合和资料同属工作区。`conversations` 保存标题和当前范围快照；`conversation_messages` 各自保存提问时的范围、状态、校验后的回答和受限的模型用量信息。`answer_feedback` 对已完成消息保存单条反馈与幂等键。

`conversation_documents` 关联对话历史引用过的解析版本。`0011` 在解析版本实际删除前清理引用它的对话及 Quiz，避免在文档删除后保留引文副本；普通范围切换删除关联记录时不会删除对话。范围切换只影响后续消息；历史消息仍显示提问时范围。回答 JSON 包含精确解析版本、正文位置及摘录，历史原文读取可指定版本。当前没有单独的 `message_citations` 表，引用只随已校验回答保存。

### 5.5 Quiz 与作答

#### `quizzes` / `quiz_documents`

Quiz 保存工作区、标题、生成配置、原始请求范围、解析版本范围快照及状态；原始请求范围用于幂等重试冲突检查。关联表约束来源解析版本与工作区一致。删除来源版本时会清除依赖该版本的 Quiz 与作答。

`0012_quiz_agent_usage` 新增可空 `generation_usage` JSONB，保存实验 Quiz Agent 成功运行的模型/规划版本、累计用量与工具动作轨迹；不含查询词、正文或完整模型响应。该字段不用于筛选或排序，不新增索引；与题目一起受租约保护原子保存，随 Quiz 删除。旧记录保持 NULL，旧应用兼容新增列。公开接口不返回它，避免作答前暴露来源。先升级 schema 再部署新进程，应用回退时保留兼容列；downgrade 会删除审计数据，生产应优先保留列或前滚修复。

`0013_conversation_tasks` 为 `conversation_messages` 增加可空 `task_result` JSONB 和 `quiz_id`。结果保存类型、说明和公开资源 ID，不复制答案/解析；正文仍遵守学习记录隐私和删除策略。复合外键 `(quiz_id, workspace_id)` 指向 Quiz 所有权，禁止跨空间引用，删除 Quiz 时级联删除相关消息；`(workspace_id, quiz_id)` 索引支持引用清理。旧消息的新增字段保持 NULL，无回填。消息、已校验题目、Quiz 与用量在同一短事务内发布；模型/向量调用在事务外。复习查询按空间和精确范围限制已提交作答。

先迁移至 `0013` 再部署应用。应用回退保留兼容字段，停止新任务并等待在途任务结束；生产不使用会丢弃任务结果的 downgrade。隔离空库与 `0012` 已有 Quiz/消息升级均须检查保留数据、Alembic 元数据和跨空间外键。

#### `quiz_questions`

保存题序、题型、题干、选项、答案、解析、难度、知识点、来源和生成 schema 版本。出题先由领域规则验证题型、选项、答案、去重与来源摘录，再原子发布题目。未提交作答时 API 不返回答案、解析和来源。

当前题目来源保存在 `quiz_questions.sources` JSONB，包含 chunk ID、document ID、解析版本、locator 和原文摘录；没有可验证来源的候选题不会发布。

#### `quiz_attempts` / `quiz_answers`

attempt 保存状态、总分与薄弱知识点；answer 保存题目、用户答案、得分、反馈和评分方式。唯一键 `(attempt_id, question_id)` 防止重复答案。正式用户归属依赖后续认证方案，当前以 workspace 隔离。

## 6. 索引策略

初始索引按真实查询建立：

- 所有外键列建立 B-tree 索引。
- `documents(workspace_id, status, created_at DESC, id DESC)` 支持列表游标。
- `documents(workspace_id, sha256)` 支持重复检测。
- `document_chunks(document_version_id, ordinal)` 支持顺序读取。
- `document_chunks USING GIN(content_tsv)` 支持全文检索。
- `document_chunks USING hnsw (embedding vector_cosine_ops)` 支持向量检索。
- `processing_jobs(status, available_at)` 对待处理状态建立部分索引。
- `messages(conversation_id, created_at, id)` 支持消息游标。
- 高频软删除表使用 `WHERE deleted_at IS NULL` 的部分索引。

向量查询必须先应用 workspace、active version 和 document scope。若过滤后 HNSW 召回不足，应根据 `EXPLAIN (ANALYZE, BUFFERS)` 调整候选数量、迭代扫描或采用按租户/规模分区，而不是取消权限过滤。

索引会增加写放大；未被查询计划使用的索引应删除。上线前用代表性数据验证索引大小、写入速度和召回质量。

## 7. 安全控制

### 7.1 角色与权限

生产至少分离：

- `app_runtime`：业务表所需 SELECT/INSERT/UPDATE/DELETE，无 DDL、无超级用户权限。
- `app_migrator`：仅部署时使用，拥有迁移所需 DDL。
- `readonly_ops`：受审计的只读排障角色，不可读取高敏感列或原文正文。

应用启动时拒绝使用超级用户。生产数据库不直接暴露公网，强制 TLS，限制安全组来源和连接数。

当前 Compose 基线使用三类凭据：管理员只供 PostgreSQL 初始化和一次性角色配置，`review_agent_migrator` 拥有应用 schema 并执行 Alembic，`review_agent_app` 只拥有应用 schema 的 DML 与序列使用权限。Alembic 版本表位于独立的 `review_agent_migrations` schema，运行账号无权读取或修改。运行账号同时被撤销数据库临时对象权限，不能执行持久或临时 DDL。

### 7.2 租户隔离

应用查询必须强制 workspace scope；生产多租户阶段建议启用 PostgreSQL Row Level Security 作为第二道防线。每个事务设置经过验证的 workspace 上下文，RLS policy 根据该上下文限制行。

RLS 不能替代应用授权；后台任务、迁移和管理员连接要明确区分并专项测试，防止拥有 `BYPASSRLS` 的连接被普通请求复用。

### 7.3 敏感数据

- 连接串、密码、模型 key 不进入表、镜像、Git 或普通日志。
- 对确需保存的 provider payload/用户标识考虑列级应用加密，并由 KMS 管理密钥。
- 备份、只读副本、导出文件与主库同级保护。
- 删除账户或 workspace 采用可审计任务清理数据库、对象存储、缓存和派生向量。

## 8. 迁移规范

使用 Alembic，迁移文件进入代码审查。生产采用 expand/migrate/contract：

1. Expand：添加可空字段/新表/兼容索引，不破坏旧代码。
2. Migrate：后台回填，带断点、限速、指标和一致性校验。
3. Switch：部署读取新结构的代码。
4. Contract：确认无旧版本运行后再删除旧列或约束。

首个 `0001_database_baseline` revision 不创建业务表，用于让空库和当前无业务表的开发数据库进入统一迁移历史。`0002_document_ingestion` 只增加第一份资料闭环所需的五张表、约束和索引，不预建全部业务表。现有数据库接入时先运行幂等角色配置，再执行 `alembic upgrade head`；升级不删除已有资料。生产回退优先用前滚修复；`0002` 的 downgrade 会删除资料表，只允许在无数据的临时环境演练。

大表索引尽量并发创建；会锁表的迁移先在生产等量数据上演练。迁移脚本不调用外部服务、不依赖应用当前业务状态，也不在单事务中执行不可控的全表重写。

## 9. 备份与恢复

- 启用自动全量备份与 WAL/PITR，保留周期由数据政策确定。
- 单台云服务器基线在暂停 API、Worker 和 MinIO 写入后，将数据库逻辑归档与 MinIO 原文件卷放入同一份加密异机快照；恢复时必须成对使用。若改用对象存储版本控制或软删除，必须明确删除资料在备份/版本中的保留期。
- 至少按季度执行恢复演练；“备份成功”不等于“可恢复”。
- 生产脚本、失败重启和快照验证步骤见 [上线与数据恢复手册](./production-operations.md)；服务器内的 Docker 卷或本机快照不计为异机备份。
- 记录 RPO/RTO 目标并在规模/商业要求明确后固化。MVP 建议目标：RPO ≤ 24 小时、RTO ≤ 4 小时，生产付费版本应进一步收紧。
- pgvector 内容可由原文和版本化配置重建，但重建成本高；正常备份仍包含向量。

## 10. 性能与容量治理

- 连接池总量按数据库上限在所有 API/Worker 副本间预算，避免每个进程使用过大默认池。
- 开启慢查询采样和 `pg_stat_statements`，定期查看高总耗时与高频查询。
- autovacuum、表膨胀、索引命中率、缓存命中率、锁等待和复制延迟进入监控。
- 分区只在表达到实测规模、维护或删除成为问题时引入；候选是 messages、jobs 和审计事件的时间分区。
- JSONB 只存低频或多态字段；需要过滤、排序、连接的核心字段提升为明确列。

## 11. 数据库上线检查表

- 所有迁移从空库和上一生产版本均能成功执行。
- 外键删除语义经过确认，未出现意外级联删除。
- workspace 隔离与越权测试通过。
- 高频查询保存 `EXPLAIN ANALYZE` 基线。
- 连接使用非超级用户、TLS 与秘密注入。
- 备份、PITR、对象存储恢复和删除流程已演练。
- 生产告警覆盖连接耗尽、磁盘、锁、慢查询、复制与备份失败。
