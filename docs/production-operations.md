# 单台云服务器上线与数据恢复

此配置适用于一台 Linux 云服务器。公网只开放 80/443；Caddy 自动申请和续期证书，托管构建后的静态页面，并把 `/api/*` 转发给内部 API。PostgreSQL、MinIO、Redis、API 和 Worker 不发布主机端口。生产秘密通过 Compose secret 文件分别挂载给需要的容器，浏览器和 Web 镜像都不接收模型或数据库凭据。

这是一套可执行的上线基线，不保证服务器或云服务商故障绝不会造成数据丢失。上线前必须完成异机备份、一次恢复演练、磁盘加密与监控告警。生产目标先按 [数据库设计](./database-design.md) 的 RPO ≤ 24 小时、RTO ≤ 4 小时验收；实际值以演练测得的结果为准。

## 1. 服务器准备

- 为服务分配真实域名，DNS A/AAAA 记录指向服务器；安全组和主机防火墙只向公网开放 80/443 和受限来源的 SSH。不要公开 Docker API、5432、6379、9000、8000 或 MinIO 管理端口。
- 启用云盘加密和定期系统安全更新。Docker、Compose、Python 3、restic 和 Git 安装在服务器上；备份目的地必须位于另一台服务器或另一账号，不能是同机 MinIO、同一块云盘或同一项目卷。
- 将此仓库固定到经过测试的提交，部署于 `/opt/review-agent`。不要把本地 `.env`、真实资料或备份存储在 Git 工作树内。`compose.production.yaml` 是独立配置，不要叠加本地 `compose.override.yaml`。
- 生产 Compose 使用独立卷。若现有资料位于本地 Compose 或其他服务器，先制定并验证数据迁移，再开放新域名；直接启动此配置不会自动带入旧数据。
- 备份仓库的删除权限应和日常写入权限分离；能使用对象锁或不可变保留策略时启用，以降低服务器凭据被盗后备份同时被删的风险。备份密码另存于服务器外的受控位置。

## 2. 首次配置

在 `/opt/review-agent` 下执行。下面两个配置文件只保存域名、路径和服务名；真实凭据进入单独的 secret 文件。把示例中的域名、备份仓库和云存储访问方式换成真实值。

```bash
sudo install -d -o root -g root -m 0700 /etc/review-agent /var/lib/review-agent-backup
sudo install -o root -g root -m 0600 deploy/production.env.example /etc/review-agent/production.env
sudo install -o root -g root -m 0600 deploy/backup.env.example /etc/review-agent/backup.env
sudoedit /etc/review-agent/production.env
sudoedit /etc/review-agent/backup.env
sudo python3 scripts/init_production_secrets.py /etc/review-agent/secrets
sudo sh -c 'umask 077; openssl rand -base64 48 > /etc/review-agent/restic-password'
```

秘密初始化程序会生成独立的数据库、MinIO 和 Redis 凭据及受限 Redis ACL，并从终端无回显读取 DeepSeek、百炼密钥及百炼 Embedding URL；已有文件会被拒绝覆盖。秘密目录为 root 所有、`0700`，文件为 root:`10001`、`0640`，供容器内非 root 进程读取。`/etc/review-agent/backup.env` 为 `0600`，其中的备份后端访问凭据也属于高敏感信息。不要把这些文件复制到仓库、镜像、工单或普通日志。

先初始化异机 restic 仓库，并验证仓库密码已在服务器外安全保存：

```bash
sudo -i
set -a
. /etc/review-agent/backup.env
set +a
restic init
exit
```

首次部署前运行强制检查，再启动服务：

```bash
sudo python3 -m scripts.check_production \
  --env-file /etc/review-agent/production.env \
  --backup-env-file /etc/review-agent/backup.env
sudo docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml up --build -d --wait
sudo docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml exec -T api python -m scripts.verify_redis_rate_limit
```

首次账号在终端交互创建，使用真实邮箱；新部署不带 `--claim-local-workspace`：

```bash
sudo docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml run --rm --no-deps api \
  python -m scripts.create_user --email you@example.com
```

默认关闭自行注册。完成后检查 `https://域名/` 可以登录、`/api/v1/auth/me` 在未登录时返回 401，`/docs` 和 `/openapi.json` 不暴露 API 文档。生产 Cookie 带 Secure、HttpOnly、SameSite=Strict；写请求有 CSRF 与来源校验。

## 3. 加密异机备份与告警

`scripts.backup_production` 使用独占锁；要求生产服务已运行，然后暂停公网入口、API、Worker 和 MinIO 写入。它对 PostgreSQL 做一致性自定义格式导出，验证归档可读取，再把数据库归档、MinIO 数据卷、Caddy 证书卷、恢复所需 secret 文件及非秘密配置放入**同一个 restic 加密快照**。即使捕获中途失败，程序也会尝试恢复服务；快照完成后检查仓库结构。备份期间服务短暂不可用，安排在低流量时段并在真实数据量上测量停机时间。Redis 只保存短期计数，不包含业务事实，不进入备份；恢复后计数从空状态开始。Redis 故障时登录及受限写请求返回 503，普通读取不依赖 Redis。

安装每 12 小时备份和每小时新鲜度检查。备份调度最多随机延迟 15 分钟；最后一份快照超过 18 小时就应触发告警，以便在 24 小时恢复点目标之前处理故障：

```bash
sudo install -o root -g root -m 0644 deploy/systemd/review-agent-backup* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now review-agent-backup.timer review-agent-backup-health.timer
sudo systemctl start review-agent-backup.service
sudo systemctl status review-agent-backup.service review-agent-backup-health.service
```

把 `review-agent-backup.service`、`review-agent-backup-health.service` 的失败状态接入云监控告警，并对磁盘空间、数据库连接、Redis 健康与内存、容器健康、证书到期、Worker 心跳和模型费用设置告警。没有通知目标时，systemd 失败记录本身不会主动通知人员。定期用 `restic check --read-data` 检查完整数据；仓库清理和保留期需结合用户删除政策制定，不要在未核对快照前自动 `forget --prune`。备份中的已删除资料会在保留期内继续存在，应在隐私说明中明确。

## 4. 恢复演练与故障处理

至少每季度在**独立、无生产流量**的服务器演练一次。先列出快照，选择一个完成的快照并恢复到空目录：

```bash
sudo -i
set -a
. /etc/review-agent/backup.env
set +a
restic snapshots --tag review-agent-production
mkdir -m 0700 /var/lib/review-agent-restore
restic restore <snapshot-id> --target /var/lib/review-agent-restore
python3 /opt/review-agent/scripts/verify_restored_backup.py \
  --root /var/lib/review-agent-restore
exit
```

验证工具检查数据库文件 SHA-256、原文件卷、凭据和记录的代码提交号。恢复时先切换到该提交，复制快照中的 `production.env` 与 secret 文件到新服务器，并保持原有权限；备份仓库密码要从**独立保存的位置**取得，不能只存在于同一份备份里。

完整恢复只在全新服务器、空业务库和空对象卷上进行。验证工具会打印归档、MinIO 卷和秘密目录的恢复路径。先检出打印出的代码提交号；把快照中的 `production.env` 与 secret 文件复制到新服务器 `/etc/review-agent/`，保持 root 所有以及上述权限。下面示例在 root shell 中执行，使用打印路径替换尖括号中的值；**不要**对现有生产卷执行复制或导入。

```bash
cd /opt/review-agent
git checkout <snapshot-source-revision>
docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml create db minio
docker volume inspect review-agent-prod_minio_data --format '{{.Mountpoint}}'
# 确认上一步返回的新卷目录为空，然后复制 <restored-minio-path>/. 到该目录。
cp -a <restored-minio-path>/. <new-empty-minio-volume-mountpoint>/
docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml up -d database-init
# 等待 database-init Exited (0)，并确认 schema 对应快照里的代码版本。
docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --disable-triggers --single-transaction --exit-on-error' \
  < <restored-database.dump>
docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml exec -T db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DELETE FROM review_agent_auth.sessions"'
docker compose --env-file /etc/review-agent/production.env \
  -f compose.production.yaml up --build -d --wait
```

这套数据库归档限定应用和认证 schema；全新目标先执行同提交的 Alembic 迁移，再只导入业务数据，以保留迁移角色对表的所有权。`pg_restore` 使用管理员账号，仅限隔离恢复阶段。随后用只读核对检查用户、文档数、对象数量、引用定位与跨工作区隔离，确认后再切换 DNS。恢复后的旧会话会被注销，用户需要重新登录。

正式切换前应记录恢复开始/结束时间与最新可用快照时间，核对 RTO/RPO。对数据库和对象卷的恢复必须成对使用同一快照；不能把不同时间点的备份混用。若只有同服务器上的备份，服务器丢失时无法恢复，本上线检查不能视为通过。

## 5. 发布与回退

发布前备份、记录当前镜像 tag 和 Git 提交，执行迁移兼容性检查，再更新 API/Worker/网页镜像。迁移失败时先保留数据和卷，优先前滚修复；不要执行未经演练的破坏性 downgrade。`docker compose down -v` 会删除数据库及文件卷，生产环境严禁使用。日志仅记录稳定 ID、结果和错误类别；不要收集文档正文、邮件、Prompt、回答、Cookie 或供应商完整请求。

技术依据：[Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)、[Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)、[PostgreSQL pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html)、[restic 备份验证](https://restic.readthedocs.io/en/stable/045_working_with_repos.html)。
