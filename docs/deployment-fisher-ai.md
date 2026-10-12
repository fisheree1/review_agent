# fisher-ai.com 部署记录

首次部署：2026-09-27；最近一次完整升级：2026-10-12（`9745bf4`，迁移 `0020_agent_run_events`）。入口：<https://fisher-ai.com/review/>。

## 当前运行方式

- 宝塔 Nginx 保留现有 TLS 证书、门户、LightReader 和招聘项目，新项目使用 `/review/` 子路径。
- 应用目录 `/opt/review-agent` 是 GitHub 仓库的 git 检出（分离 HEAD 指向发布提交），Compose 项目名 `review-agent-prod`。当前应用、前端镜像标签均为 `20261012-9745bf4`（`APP_IMAGE_TAG`）。
- 仅入口映射到 `127.0.0.1:18081`；API、PostgreSQL、Redis 和文件存储没有新建公网端口。
- 配置 `/etc/review-agent/production.env`；秘密文件 `/etc/review-agent/secrets`。模型凭据只挂载给 Worker。
- 复用现有单管理员门户。项目会话映射到专属学习空间，不复制门户密码或签名密钥；每次项目访问均检查门户会话。项目密码登录和自行注册关闭。
- 前端路由、认证、上传和业务请求均支持 `/review/`。入口卡片沿用现有门户样式。
- 本次按用户要求不启用异机备份。没有进行云盘加密核验或恢复演练，不能据此承诺 RPO/RTO。
- 服务器 `DEPLOYMENT.json`（未纳入 git）记录当前与上一版本的提交号、镜像标签、迁移版本及备份位置。2026-09-27 的首次部署使用含工作区改动的源码包；此后按发布提交检出。
- Agent 工作流始终开启，每条对话消息都创建持久运行；`production.env` 中残留的 `AGENT_GRAPH_ENABLED` 会被忽略。运行进度经 `/review/api/agent-runs/{id}/events`（SSE）推送：宝塔 Nginx 的 `/review/api/` 使用 HTTP/1.1、读超时 120 秒，应用返回 `X-Accel-Buffering: no`，容器内 Caddy 对 `text/event-stream` 即时转发。

## 版本记录

| 日期 | 发布提交 | 镜像标签 | 迁移 | 范围 |
|---|---|---|---|---|
| 2026-09-27 | 工作区源码包 | `20260927` | `0014_agent_workflows` | 首次部署 |
| 2026-09-28/29 | `cd9e7ab` / `c6b2109` | 后端 `20260928-cd9e7ab`，前端 `20260929-c6b2109` | `0015_learning_organization` | 后端与仅前端更新 |
| 2026-10-12 | `9745bf4` | `20261012-9745bf4` | `0020_agent_run_events` | 完整升级（PR #17–#24） |

## 2026-10-12 升级验证

- 升级前确认没有进行中的运行、消息、出题、评分或文档任务；先停止旧 Worker，再由迁移角色执行 `0016`–`0020`（含 `0019` 旧消息交接与 `0020` 运行变化通知触发器）。
- 新镜像的 API、Worker 健康检查通过；Worker 三个通道（interactive×2、agent×1、ingest×1）启动；启动后 10 分钟内 API、Worker、edge 日志无错误。
- 未登录访问 `/review/` 仍跳转门户登录；`/review/api/v1/auth/me`、资料列表和运行进度流均返回 401；伪造网关头返回 401。
- 未在生产执行登录后的问答、出题与复习（需管理员门户登录）；对应流程已在本地 Docker 环境用真实模型端到端验证。

## 2026-09-27 首次部署验证

- 空库迁移到 `0014_agent_workflows`；运行时数据库角色无管理权限，不能执行 DDL。
- 文件存储凭据限制在项目桶，拒绝管理操作；Redis 限流和幂等重放检查通过。
- API、数据库、Redis 和 Worker 健康检查通过；HTTPS 和现有项目的未登录访问行为正常。
- 公网伪造网关请求返回 401；可信内部网关仅映射固定账号，项目 Cookie 为 Secure/HttpOnly/SameSite=Strict，并限制路径；写请求保留 CSRF。
- 使用合成 DOCX 完成上传、后台解析、真实 Embedding、原子索引激活和带来源问答（这是 2026-09-27 的历史验收；当前版本只接受新 PDF 上传）。
- 真实 Quiz 判断题完成生成、作答、评分、解析及来源展示；提交前隐藏答案和来源。另一次无效生成返回 `QUIZ_INVALID`，未发布为就绪题目。所有联调资料已请求删除并由后台清理。
- 后端 116 项、前端原有 27 项测试通过；另补门户界面与 API 前缀测试 4 项通过；Python 类型检查、改动文件 Ruff 和前端类型检查通过。子路径登录浏览器回归通过。
- 用户自行完成现有门户登录后，已在正式浏览器检查首页项目卡片、资料库、学习空间、Quiz 和账号页；门户身份映射正常，页面和子路径导航正常。

## 查看状态、启动和升级

在服务器应用目录执行；三个 Compose 配置必须保持一致：

```bash
cd /opt/review-agent
C="--env-file /etc/review-agent/production.env -f compose.production.yaml -f compose.baota.yaml -f compose.portal.yaml"
docker compose $C ps
```

升级按以下顺序进行（`TAG` 形如 `20261012-9745bf4`，`REV` 为已通过 CI 的 `main` 提交）。先阅读新增迁移的说明和发布注意事项；构建期间旧版本继续服务，4 GB 内存的服务器构建约需 13 分钟。

1. 备份：`pg_dump` 数据库到 `/root/review-agent-before-$TAG.dump` 并用 `pg_restore --list` 确认可读；复制应用目录到 `/opt/review-agent-prev-$TAG`；复制 `production.env` 为 `production.env.before-$TAG`。
2. 检出：`git fetch origin main && git checkout --detach $REV`，确认 `git rev-parse HEAD^{tree}` 与 CI 验证的提交一致。
3. 配置：把 `production.env` 中的 `APP_IMAGE_TAG` 改为 `$TAG`，执行 `docker compose $C config --quiet`。
4. 构建：`docker compose $C build`。
5. 确认没有进行中的运行、消息、出题、评分和文档任务后，`docker compose $C stop worker`，再执行 `docker compose $C run --rm database-init`。旧 Worker 必须先停止，否则可能用旧逻辑处理任务。
6. 启动：`docker compose $C up -d --wait --wait-timeout 180`，检查 `ps`、Worker 通道日志和未登录访问行为。
7. 更新 `DEPLOYMENT.json`，记录当前与上一版本、迁移版本和备份位置。

不要把本地 `.env`、真实资料或容器数据卷带到服务器。不得运行 `down -v` 或删除生产卷。

## 回滚

1. 停止 Worker，把 `APP_IMAGE_TAG` 改回 `DEPLOYMENT.json` 中的上一镜像标签（2026-10-12 之前为后端 `20260928-cd9e7ab`、前端 `20260929-c6b2109`，镜像仍在服务器上），并 `git checkout --detach` 回上一发布提交。
2. 新迁移包含数据交接，回滚到更早的迁移版本时用升级前的数据库备份恢复，不依赖逐步降级。
3. 用同一组参数启动并检查健康状态，再更新 `DEPLOYMENT.json`。

当前没有异机备份：数据库与目录备份只在本机磁盘，需要时手动复制到其他位置。

## 撤销新网站入口

原站点配置保存在 `/etc/review-agent/nginx-before-review.conf`。关闭新入口时，先核对现有配置是否有后续人工改动；没有后续改动时可恢复该文件，验证后重载：

```bash
cp /etc/review-agent/nginx-before-review.conf \
  /www/server/panel/vhost/nginx/html_149.88.81.31.conf
/www/server/nginx/sbin/nginx -t && /www/server/nginx/sbin/nginx -s reload
```

此步骤只撤销网站入口，不删除数据库、文件或项目配置。网关 include 位于 `/etc/review-agent/nginx-review-location.conf`，包含秘密令牌，不能贴到聊天或代码仓库；令牌只应出现在指令中，不要写进注释，排查时避免直接打印整个文件。完整运维说明见 [生产运维文档](./production-operations.md)，当前模式启用备份前需要适配三个 Compose 配置及宝塔证书。
