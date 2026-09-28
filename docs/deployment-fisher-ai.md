# fisher-ai.com 部署记录

部署日期：2026-09-27。入口：<https://fisher-ai.com/review/>。

## 当前运行方式

- 宝塔 Nginx 保留现有 TLS 证书、门户、LightReader 和招聘项目，新项目使用 `/review/` 子路径。
- 应用目录 `/opt/review-agent`，Compose 项目名 `review-agent-prod`。应用、前端镜像标签 `20260927`。
- 仅入口映射到 `127.0.0.1:18081`；API、PostgreSQL、Redis 和文件存储没有新建公网端口。
- 配置 `/etc/review-agent/production.env`；秘密文件 `/etc/review-agent/secrets`。模型凭据只挂载给 Worker。
- 复用现有单管理员门户。项目会话映射到专属学习空间，不复制门户密码或签名密钥；每次项目访问均检查门户会话。项目密码登录和自行注册关闭。
- 前端路由、认证、上传和业务请求均支持 `/review/`。入口卡片沿用现有门户样式。
- 本次按用户要求不启用异机备份。没有进行云盘加密核验或恢复演练，不能据此承诺 RPO/RTO。
- 部署源包含已授权部署的工作区改动，并非一个新发布的 Git 提交；服务器 `DEPLOYMENT.json` 记录基础提交号和源码包 SHA-256。

## 已完成验证

- 空库迁移到 `0014_agent_workflows`；运行时数据库角色无管理权限，不能执行 DDL。
- 文件存储凭据限制在项目桶，拒绝管理操作；Redis 限流和幂等重放检查通过。
- API、数据库、Redis 和 Worker 健康检查通过；HTTPS 和现有项目的未登录访问行为正常。
- 公网伪造网关请求返回 401；可信内部网关仅映射固定账号，项目 Cookie 为 Secure/HttpOnly/SameSite=Strict，并限制路径；写请求保留 CSRF。
- 使用合成 DOCX 完成上传、后台解析、真实 Embedding、原子索引激活和带来源问答（这是 2026-09-27 的历史验收；当前版本只接受新 PDF 上传）。
- 真实 Quiz 判断题完成生成、作答、评分、解析及来源展示；提交前隐藏答案和来源。另一次无效生成返回 `QUIZ_INVALID`，未发布为就绪题目。所有联调资料已请求删除并由后台清理。
- 后端 116 项、前端原有 27 项测试通过；另补门户界面与 API 前缀测试 4 项通过；Python 类型检查、改动文件 Ruff 和前端类型检查通过。子路径登录浏览器回归通过。
- 用户自行完成现有门户登录后，已在正式浏览器检查首页项目卡片、资料库、学习空间、Quiz 和账号页；门户身份映射正常，页面和子路径导航正常。

## 查看状态、启动和升级

在服务器应用目录执行；三个配置必须保持一致：

```bash
cd /opt/review-agent
docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml -f compose.baota.yaml -f compose.portal.yaml ps
docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml -f compose.baota.yaml -f compose.portal.yaml \
  up -d --wait --wait-timeout 180
```

升级前记录当前镜像与源码包，阅读迁移说明。新源码放到应用目录后，用同一组参数 `build`，再执行上述启动步骤。不要把本地 `.env`、真实资料或容器数据卷复制进源码包。不得运行 `down -v` 或删除生产卷。

## 撤销新网站入口

原站点配置保存在 `/etc/review-agent/nginx-before-review.conf`。关闭新入口时，先核对现有配置是否有后续人工改动；没有后续改动时可恢复该文件，验证后重载：

```bash
cp /etc/review-agent/nginx-before-review.conf \
  /www/server/panel/vhost/nginx/html_149.88.81.31.conf
/www/server/nginx/sbin/nginx -t && /www/server/nginx/sbin/nginx -s reload
```

此步骤只撤销网站入口，不删除数据库、文件或项目配置。网关 include 位于 `/etc/review-agent/nginx-review-location.conf`，包含秘密令牌，不能贴到聊天或代码仓库。完整运维说明见 [生产运维文档](./production-operations.md)，当前模式启用备份前需要适配三个 Compose 配置及宝塔证书。
