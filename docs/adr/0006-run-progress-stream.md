# ADR-0006：运行进度推送、周期清理与学习存储拆分

- 状态：Accepted
- 日期：2026-10-11
- 范围：运行状态如何到达浏览器、过期与失效租约的回收时机、学习存储的模块边界。

## 背景

前端每 1.5–2 秒轮询运行与对话，长任务期间请求量大，状态变化也要等下一次轮询才能看到。每次领取运行都会先扫描过期运行和失效租约，其中过期扫描在 `expires_at` 上没有索引，每个 Agent 通道槽位每秒执行一次。`store.py` 约 1,500 行、49 个方法，同时承担资料集、对话、Quiz、作答与评分。

## 决策

- **推送**：迁移 `0020` 在 `agent_runs` 上加行触发器，状态、阶段、修订号或输出变化时 `pg_notify('agent_run_events', 运行 UUID)`，提交后才送达，载荷不含内容。每个 API 进程用一条专用 `asyncpg` 连接监听并分发给订阅者（`RunEventHub`，默认上限 200）。`GET /api/v1/agent-runs/{id}/events` 先授权并订阅，再发送快照，之后每次通知都重新经授权存储读取运行；15 秒心跳，运行结束、10 分钟上限、客户端断开或监听连接丢失时关闭。前端 `useRunEvents` 用 `EventSource` 写入查询缓存，连接期间轮询降为 30 秒安全网，运行结束主动关闭，连续 3 次失败后放弃并回到原有轮询。
- **清理**：过期与失效租约回收移出 `claim`，改为 `SqlAgentRunStore.sweep`，在 Agent 通道中每 30 秒执行一次（`throttled`）。未确认的外部调用仍关闭为 `blocked`，不重发。
- **拆分**：Quiz 生成与配额移到 `quiz_store.SqlQuizStore`，作答、提交与评分移到 `attempt_store.SqlAttemptStore`；`SqlLearningStore` 保留资料集、对话与消息，通过 `.quizzes`、`.attempts` 显式暴露，不使用 mixin。共享常量 `LEASE_SECONDS` 移到 `scope.py`。

## 影响

- 进度在提交后即时到达；没有推送时行为与以前相同。API 每个进程多占一个数据库连接。
- 失效租约的恢复和过期关闭最多延后约 30 秒；查询运行时仍会即时显示过期。
- 调用方必须写成 `store.quizzes.X` / `store.attempts.X`，模块边界由类型检查保证。
- 网关需不缓冲 `text/event-stream`：响应带 `X-Accel-Buffering: no`，Nginx 读超时（120 秒）大于心跳。
