# Git 与 GitHub 管理规范

版本：0.1

## 1. 目标

保持 `main` 随时可发布，让每个变更可审查、可验证、可回滚，同时避免为了流程本身增加无价值测试和等待时间。

## 2. 分支策略

采用轻量 trunk-based development：

- `main` 为唯一长期分支，禁止直接提交和 force push。
- 功能分支保持短生命周期，通常在 1–3 天内合并。
- 命名：`feat/<topic>`、`fix/<topic>`、`docs/<topic>`、`refactor/<topic>`、`chore/<topic>`。
- 大功能使用小型垂直切片和 feature flag，不维护长期集成分支。

本仓库已约定：当仓库所有者明确要求“提交”时，同一任务中完成本地 commit 和向对应 `origin` upstream 的 push；若只希望本地提交，必须明确说明。普通代码修改不会自动产生提交。

## 3. 提交规范

使用 Conventional Commits：

```text
feat(rag): add workspace-scoped hybrid retrieval
fix(quiz): reject questions without valid sources
docs(ui): define long-reading typography tokens
chore(ci): add focused container smoke test
```

提交应表达一个可理解的决定。提交前检查 staged diff，不混入缓存、真实资料、`.env`、数据库文件或无关格式化。

## 4. Pull Request

PR 应包含：

- 用户或运维结果，而不是只列修改文件。
- 关键设计与没有选择的替代方案。
- 权限、数据、迁移、成本和回滚风险。
- 针对重要结果的验证证据。
- UI 截图/键盘检查，或数据库迁移与恢复说明（适用时）。

优先使用 squash merge，让 `main` 保持一项功能一个清晰提交。紧急修复仍通过 PR；确需例外时事后补齐审查和测试证据。

## 5. GitHub 仓库设置

创建远程仓库后，为 `main` 配置 ruleset：

- 必须通过 Pull Request 合并。
- 至少 1 名批准者；单人项目可保留自审，但仍通过 PR 留存记录。
- 要求分支在合并前解决所有对话。
- 要求最新提交通过匹配改动范围的 CI。
- 禁止 force push 和删除 `main`。
- 启用 secret scanning、push protection、Dependabot alerts 和 private vulnerability reporting（账户计划支持时）。
- 只允许 squash merge；合并后自动删除分支。

没有所有者信息前不创建 `CODEOWNERS`。多人协作后按真实模块所有权配置，避免用虚构或过时账号造成审批阻塞。

## 6. 快速、风险导向的 CI

CI 按改动路径触发，并行执行：

- `Focused quality checks`：Python 源码、测试或依赖变化时执行 Ruff、格式、mypy 和 pytest。
- `Container smoke test`：启动/镜像/依赖相关变化时构建 Compose，验证 API 能连通 PostgreSQL/pgvector。
- 纯文档变化不运行无关后端测试。

增加测试前先写出它保护的结果：越权不会发生、资料不会半索引、任务重投不会重复扣费、回答引用有效、Quiz 答案与来源有效。不要测试私有函数调用次数、模型精确措辞或重复 snapshot。

当测试规模增长时分为：PR 快速门禁（目标 10 分钟内）、按路径触发的集成测试、定时或手动的完整 RAG 评测。PR 只阻塞高信号、稳定的检查；不稳定测试必须隔离并修复，不能长期重跑碰运气。

## 7. Release 与回滚

- 使用语义化 tag 和自动生成 release notes；MVP 前可保持 `0.x`。
- 发布物必须来自 CI 构建的不可变 commit/image digest，不在服务器临时构建。
- 数据库变更遵循 expand/migrate/contract，使上一版应用在迁移窗口内仍可运行。
- 每次发布记录应用版本、迁移版本、Prompt/模型版本和回滚限制。

## 8. 当前仓库接入步骤

1. 本地初始化 `main`，完成首个经过检查的提交。
2. 在 GitHub 创建私有仓库，不自动生成 README 或 `.gitignore`。
3. 添加 `origin` 并推送 `main`。
4. 在 Actions 中确认两个 workflow 首次成功。
5. 配置 ruleset、安全能力和默认 squash merge。
6. 创建第一个小功能分支，走完整 PR 流程验证设置。

当前公开远程仓库为 `https://github.com/fisheree1/review_agent.git`。修改 GitHub ruleset、安全设置或合并 PR 仍属于独立外部操作，需要仓库所有者明确授权。
