# Review Agent 文档中心

这里集中说明产品目标、当前实现、开发约束和运维方式。文档与代码出现差异时，先核对实际行为，再在本次改动范围内修正文档或实现，并记录尚未解决的差异。设计目标不等于已经交付。

## 按任务阅读

| 任务 | 先读 | 继续查阅 |
| --- | --- | --- |
| 理解产品与验收 | [产品需求](./product-requirements.md) | [学习闭环](./learning-core.md)、[技术难点](./technical-challenges.md) |
| 修改 API、Worker 或数据流 | [系统架构](./architecture.md)、[开发规范](./development-guide.md) | [数据库设计](./database-design.md)、[ADR](./adr/0001-postgresql-document-jobs.md) |
| 修改检索、Agent 或练习 | [学习闭环](./learning-core.md)、[技术难点](./technical-challenges.md) | [RAG 实现](./rag-implementation.md)、[Agent 工作流设计](./agent-workflow-development.md)、[评测](./ca6000-rag-evaluation.md) |
| 修改界面 | [UI 与阅读体验](./ui-design-system.md) | [产品需求](./product-requirements.md)、[学习闭环](./learning-core.md) |
| 部署或恢复 | [生产运维](./production-operations.md) | [当前站点部署记录](./deployment-fisher-ai.md)、[数据库设计](./database-design.md) |
| 提交或评审 | [Git 与 GitHub 规范](./git-github-workflow.md) | [开发规范](./development-guide.md)、[工程审查记录](./project-review.md) |

## 当前状态如何理解

- **产品基线**：[产品需求](./product-requirements.md)、[系统架构](./architecture.md)、[数据库设计](./database-design.md)、[UI 规范](./ui-design-system.md)既包含已实现行为，也包含后续目标；以各节的“当前”说明区分。
- **本地工作区**：迁移文件已到 `0015_learning_organization`，对应对话分组与个人学习打卡。相关代码和文档仍有未提交改动，不能据此推断线上版本或生产验收已经完成。
- **线上记录**：[fisher-ai.com 部署记录](./deployment-fisher-ai.md)记录 2026-09-27 的已验证版本，当时迁移至 `0014_agent_workflows`；新的本地功能需单独部署与验收。
- **Agent 工作流**：[ADR-0002](./adr/0002-agent-business-checkpoints.md)是已采纳的持久化决策；[工作流开发设计](./agent-workflow-development.md)记录实现与待验证的 Beta 指标。[LangGraph 接入规划](./langgraph-integration-plan.md)保留方案演进背景，不能当作当前运行手册。
- **质量证据**：[CA6000 评测](./ca6000-rag-evaluation.md)区分历史单资料结果与尚待完成的多资料真实课件验收；[技术难点](./technical-challenges.md)列出剩余风险及完成判据。

## 专题与历史记录

- [资料范围内 Agent 开发计划](./agent-development-plan.md)：早期分阶段计划和受控工具边界。
- [Agent 工作流开发设计](./agent-workflow-development.md)：组合学习的状态、接口、恢复与交付记录。
- [LangGraph 接入规划](./langgraph-integration-plan.md)：框架取舍和分阶段规划。
- [ADR-0001：PostgreSQL 文档任务](./adr/0001-postgresql-document-jobs.md)：当前任务投递方案与迁移门槛。
- [ADR-0002：Agent 业务检查点](./adr/0002-agent-business-checkpoints.md)：恢复、未知调用和清理的决策。
- [工程审查记录](./project-review.md)：一次性审查结论，不代替持续更新的实现状态。

## 待确认的产品与运维决策

- 是否开放公开注册，以及何时提供团队成员管理。
- 模型、Embedding 和预算策略，以及资料发送给第三方模型的用户告知方式。
- 单文件大小、页数、资料库容量和支持语言的产品上限。
- 文件保留期、账号注销后的删除时限和审计要求。
- Quiz 是否扩展到正式考试、防作弊与证书。
- 上线版本的真实资料质量、费用、性能和异机备份恢复目标；现有部署记录没有完成这些验收。
