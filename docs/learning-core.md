# 学习闭环实现与验收

`0009_learning_core` 提供资料集合、多资料范围问答、连续对话、回答反馈、Quiz 生成、作答、评分和复习。学习记录按 workspace 隔离；`0010_user_auth` 通过账号成员关系和服务端会话确定当前 workspace。

## 页面与接口

- `/study`：管理集合，创建对话，选择最多 5 份已完成索引的资料。统一输入框接受问答、总结、比较、出题、错题复习和薄弱点练习；Quiz 可在消息卡片内作答/提交/核对来源，也能打开独立页面。切换范围只影响后续任务；每条历史消息保留其发送时的范围。回答可反馈“有帮助”“无帮助”“引用不准确”，可停止尚未完成的任务。
- `/quizzes`：配置单选、多选、判断、简答各自数量，总数 1–10，另选难度、语言、知识点及资料范围。生成是异步任务；只发布经过题型、答案和原文摘录验证的题，页面展示实际通过数量。
- `/quizzes/{quiz_id}/attempts/{attempt_id}`：逐题作答并提交；客观题自动评分，简答题给模型评分提示；提交后显示答案、解析、来源、总分与薄弱知识点。模型评分失败可重新尝试。
- 主要 API 位于 `/api/v1/collections`、`/api/v1/conversations` 和 `/api/v1/quizzes`，OpenAPI `/docs` 展示完整请求和响应。创建对话可选、创建 Quiz、提问、开始作答及回答反馈使用 `Idempotency-Key`。阅读原文可向 `/api/v1/documents/{id}/content` 传 `version_id` 和 `ordinal`，按引用时的解析版本定位。

对话组织接口：`GET/POST /api/v1/conversation-groups`、`PATCH/DELETE /api/v1/conversation-groups/{id}`、`PATCH/DELETE /api/v1/conversations/{id}`。分组属于当前工作区，删除分组保留对话；删除对话清理其消息和 Agent 运行。`GET /api/v1/conversations?before={id}` 按最近更新游标读取每页最多 50 条。直接新建对话允许空资料范围，首条提问前必须选择有效资料；默认标题可从首条提问生成。个人打卡接口 `GET /api/v1/learning/checkins?month=YYYY-MM&timezone=Asia/Shanghai` 与 `POST /api/v1/learning/checkins`（`{timezone}`），仅对当前登录成员生效，重复打卡保持一次。服务器根据时区认定当天，客户端不能提交任意日期。

## 正确性边界

### 资料库辅助接口

- `GET /api/v1/documents` 增加可选 `search`（文件名、不区分大小写、最长 160 字符）、`status`（`ready/processing/failed/deleting`）、`sort`（`newest/oldest`，默认 `newest`）和 `collection_id`。搜索中的 `%`、`_` 为字面字符。所有条件在工作区授权之后、游标分页之前应用；改变条件时客户端重置游标。
- `POST /api/v1/collections` 支持可选 `document_ids`，默认空，最多 100 个且不重复。集合与成员在同一事务创建；其他工作区、不存在或已删除资料返回 404，不产生空集合。此操作创建新集合，不覆盖已有成员。
- `GET /api/v1/learning/resume` 返回可空 `conversation: {id,title,working}` 和 `attempt: {id,quiz_id,title,status}`。对话优先选择有排队或执行中消息的记录，否则取最近对话；作答取最近 `in_progress/grading` 且所属 Quiz 就绪的记录。所有关联均限定当前工作区，未登录返回 401，工作区不存在返回 404。
- 引用预览只展示回答中已保存并校验的摘录，不向前端请求整页解析正文；PDF 的“查看原文”定位到原文件页码，DOCX/PPTX 显示暂不支持预览。提交 Quiz 前仍不提供答案、解析或来源。

选择范围时展开集合并确认资料属于当前 workspace、未删除且对应当前解析版本的索引已就绪。检索 SQL 在构建模型上下文前限制 workspace、所选版本、当前有效版本和删除状态；混合检索逐份资料取候选，避免其中一份资料挤出其他来源。最近的同范围消息用于追问上下文；切换范围后旧问题与旧答案不会作为新问题的上下文。

Worker 在短事务中领取带租约的任务，模型调用位于事务外，完成时以租约标识检查并发布。回答引用和 Quiz 来源须指向本次检索证据，摘录须是原文连续子串；找不到有效依据时返回证据不足或生成失败。单纯的子串校验不能保证语义支持，因此真实资料仍需人工和版本化评测集检查。

多资料对话现由受限 Agent 选择最多两次范围内搜索、三次读取命中来源，再决定回答或证据不足。工具参数有严格 schema，来源 ID 必须来自该消息的检索结果；旧范围、其他工作区、删除资料与未就绪版本仍在 SQL 层排除。Worker 限制步骤、模型调用、计费 Token、相对费用单位和总时长，并仅保存不含正文的工具轨迹。单资料旧问答接口保持原固定流程；阶段计划与真实模型验收条件见 [Agent 开发计划](./agent-development-plan.md)。

出题蓝图、Prompt、schema 和索引 profile 各自带版本。用户提交前的 Quiz 接口隐藏答案、解析和来源。客观题分数由确定性规则计算；简答题分数仅是模型提示。删除资料解析版本时，数据库清除曾引用它的对话、Quiz、反馈和作答，避免保留原文副本。

Quiz 创建支持 `config.generation_mode`：省略或 `standard` 使用原流程；`agent` 使用实验知识点规划，选择检索、读取来源和生成候选题。问答工具与 Quiz 工具不能互相调用。候选校验和发布仍由应用负责，模型不能修改题型蓝图或自行保存；证据不足的实验任务失败并返回 `QUIZ_NO_EVIDENCE`。`0012` 增加私有生成审计列，公共 Quiz 响应不暴露它或作答前的答案来源。默认模式与历史配置保持等价，切换模式复用请求标识会触发幂等冲突。问答和实验出题在每步及最终生成前检查任务有效性，取消或删除后不启动后续付费调用。

## 验证

### 统一对话任务契约

`POST /api/v1/conversations/{id}/messages` 继续接收 `{question}` 与 `Idempotency-Key`，异步返回消息。`MessageResponse` 新增可空 `task_result`：`kind` 为 `quiz`、`review` 或 `clarification`，包含 `text`，可选 `title`、`quiz_id`、`attempt_id`。任务结果的 `answer` 为 NULL；只有带引用的事实回答接受回答反馈。Quiz 结果只公开资源 ID，不包含作答前答案或来源。稳定规划错误为 `TASK_PLAN_INVALID`；配额为 `QUIZ_LIMIT`，无有效来源仍为 `QUIZ_NO_EVIDENCE`。

Worker 路由按版本化 schema 校验；一条请求执行一项任务，混合/不支持的要求给出澄清。最近同范围消息参与指代解析；错题复习只读同空间、精确同范围最近提交的作答。薄弱点练习使用数据库计算的知识点而非模型自选主题。建议按钮只填写输入框，发送才授权执行。取消受租约保护；Quiz、题目、审计与消息结果原子发布，幂等重投不产生第二份 Quiz。出题配额在数据库内对独立 Quiz 和正在生成的对话 Quiz 统一预留。

`tests/test_conversation_tasks.py` 验证工具/蓝图限制、真实薄弱点、共享预算和取消；`web/e2e/conversation-tasks.spec.ts` 验证同一对话中的出题、作答、复习、建议草稿、键盘发送及窄屏；隔离数据库流程增加发布幂等、取消无孤立 Quiz、任务反馈拒绝、跨空间外键和共享配额检查。

在独立测试数据库执行 `python -m scripts.verify_learning_flow`，覆盖集合、范围切换、跨空间访问、引用、反馈幂等、取消任务、Quiz 发布/答案隐藏/评分、历史版本定位和来源删除清理。`tests/test_learning_domain.py` 检查无效题、重复题、答案泄漏、蓝图边界及评分规则。`web/e2e/learning.spec.ts` 覆盖切换范围、反馈和 Quiz 作答页面流程。质量与成本的真实模型基线沿用 [CA6000 评测计划](./ca6000-rag-evaluation.md)，需要在新增能力上重新测量。

## 组合 Agent API（0014）

新消息含可选 `run_id`。新增 `GET /api/v1/conversations/{conversation_id}/agent-runs?cursor=0&limit=30`、`GET /api/v1/agent-runs/{id}`、`POST /api/v1/agent-runs/{id}:cancel`、`POST /api/v1/agent-runs/{id}:respond`。澄清使用 `Idempotency-Key` 和 `{answer, expected_revision}`；只接受等待澄清的运行，跨 workspace 为 404，陈旧修订为 409。运行响应只含公开阶段、范围、成果及稳定错误，不包含调用回执。

作答保存增加可选 `expected_revision`，返回 `{question_id,response,revision}`；冲突为 `ANSWER_REVISION_CONFLICT`。相同已保存值允许丢失确认后的重放。含简答的组合任务由 Agent Worker 评分，真实评分完成后排队 review；停止的组合任务不可用旧重新评分入口恢复，可开始新作答。版本化图与兼容开关详见 [开发设计](./agent-workflow-development.md#12-本次交付与设计调整)。
