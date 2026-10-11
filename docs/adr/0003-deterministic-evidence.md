# ADR-0003：由应用确定性选取证据，移除模型工具循环与 LangGraph

- 状态：Accepted
- 日期：2026-10-11
- 范围：问答与 Quiz 的证据获取；部分取代 [ADR-0002](./0002-agent-business-checkpoints.md) 中“LangGraph 执行证据工具图”的决定。

## 背景

证据获取有两套实现：`agent.plan_evidence`（纯 Python）与 `LangGraphStudyExecutor`（LangGraph），二者都让模型逐步选择 `search`、`read_source` 和终止工具。每读取一条来源都要额外一次模型调用，只为选出一个 ID。v3 起普通问答已改为 `answer_focused`（一次检索加一次生成），工具循环只剩 Quiz 阶段和 v1/v2 在途运行使用。ADR-0002 已决定不用框架持久化，LangGraph 在这里只是一个 5 节点循环，却引入 23 个间接依赖。

两套 Quiz 配置构造也曾分叉：组合计划绕过 `validate_blueprint`，导致每次图出题失败。

## 决策

- 证据由应用代码选取：问答使用 `answer_focused`；Quiz 使用 `generate_scoped_quiz`，指定主题时按主题混合检索，未指定主题时在每份资料内均匀抽样（`scope.quiz_evidence`）。模型只负责规划任务和生成。
- 标准 Quiz、Agent 模式 Quiz、对话出题和工作流 Quiz 阶段共用这一条路径。标准模式保持“供应商缺少用量时仍可完成”的原有行为。
- 删除 `plan_step`/`plan_quiz_step` Prompt、工具循环、`StudyExecutor` 端口、`LangGraphStudyExecutor`，以及 `langgraph`、`langsmith` 依赖。ADR-0002 的调用回执、租约令牌、预算与业务检查点保持不变。

## 影响

- 模型调用减少（以仓库合成流程计）：Agent 模式 Quiz 4 → 1；对话出题 5 → 2；完整学习运行（规划、总结、出题、评分、薄弱点练习）≥ 10 → 5。时延与质量需用真实资料评测确认。
- 未指定主题的 Quiz 覆盖整份资料，而不只是每份资料的前 12 个分块。
- 部署：新版本 Quiz 阶段的调用顺序与旧版本不同。部署时恰好执行到一半的 Quiz 阶段，回放旧回执时会因调用类型不匹配被安全关闭为 `RUN_RESULT_UNKNOWN`，不会重复计费或发布错误结果。建议先暂停 `agent` 通道或等待其空闲，再替换 Worker。v1/v2 的 focused 阶段改用 `answer_focused` 恢复。
- 将来若需要模型自主多步检索（例如跨资料比较），应先用评测证明确定性检索不足，再在应用层以有界步骤实现，并保留调用回执与未知请求保护。
