# MoniPan 线上加固运行手册

本文记录不引入新运行时依赖的第一批生产保障。仓库无法看到腾讯云负载均衡、宿主机反向代理、安全组和定时任务，因此这些项目必须在服务器侧核对，不能仅凭 Compose 配置推断。

## 1. 首次盘点

在修改服务器前记录：

- 域名、证书终止位置和 HTTP 到 HTTPS 跳转位置；
- 公网入口到 `127.0.0.1:8080` 之间的全部代理层；
- 每层写入的 `X-Forwarded-For`、`X-Real-IP` 和 `X-Forwarded-Proto`；
- 当前 Compose project name、MySQL 卷的真实名称和挂载位置；
- 当前镜像标签、Git 提交和 `.env` 文件位置；
- 最近一次可用备份及其保存位置。

不要在未确认现有卷名时给卷新增固定 `name:`。Compose project name 改变会创建另一个空卷，外观上很像数据丢失。

## 2. 生产模式

生产 `.env` 至少需要：

```dotenv
MONIPAN_ENVIRONMENT=production
MONIPAN_COOKIE_SECURE=1
MONIPAN_MYSQL_PASSWORD=唯一且足够强的密码
MONIPAN_MYSQL_ROOT_PASSWORD=另一个唯一密码
```

应用在显式 production 模式下会拒绝 SQLite、开发默认数据库密码和非 Secure Cookie。已有 MySQL 卷初始化后，直接修改 `.env` 不会同步修改数据库用户密码；必须先在数据库中完成密码轮换并验证，再更新应用连接配置。

当前 Compose 会把应用数据库密码直接拼入 SQLAlchemy URL，因此应用用户密码暂时应使用至少 32 位的 URL-safe 随机字符（`A-Z`、`a-z`、`0-9`、`_`、`-`）。`@`、`:`、`/`、`#`、`%` 等字符需要 URL 编码，直接填入可能导致连接串解析失败。root 密码不进入该 URL，不受此限制。

AI 和新闻密钥缺失不会阻止核心服务启动，对应能力会按现有逻辑明确降级。

## 3. 健康检查

- `/health`：兼容原有外部探针，只证明应用进程能响应；
- `/livez`：容器存活检查，不访问数据库；
- `/readyz`：执行轻量 `SELECT 1`，数据库不可用时返回通用 503；
- 行情、新闻和 AI 不进入核心 readiness，它们应单独显示 degraded/unavailable。

Compose 的 backend healthcheck 使用 `/readyz`，frontend healthcheck 只检查本容器静态页面。

## 4. 代理和真实 IP

当前内部 Nginx 会用 `$remote_addr` 和 `$scheme` 写入转发头。如果站点前面还有腾讯云负载均衡或宿主机 Nginx，必须先确认实际代理源地址，再配置可信代理。

上线前至少验证：

1. HTTPS 页面中的后端请求被识别为 HTTPS；
2. 两个不同公网客户端不会被记录成同一个代理 IP；
3. 浏览器伪造的转发头会被最外层代理覆盖；
4. 后端 8000 和 MySQL 3306 无法从公网直接访问。

不要使用 `set_real_ip_from 0.0.0.0/0`，也不要在不知道腾讯云实际网络拓扑时猜测可信 CIDR。

## 5. 备份

默认手动执行：

```sh
sh scripts/backup_mysql.sh
```

脚本面向 Linux 服务器，需要 Docker Compose、`sh`、gzip、GNU coreutils（`sha256sum`）和 GNU findutils。仓库通过 `.gitattributes` 强制脚本保持 LF 行尾。

生产服务器建议指定独立目录和保留天数：

```sh
MONIPAN_BACKUP_DIR=/srv/monipan-backups \
MONIPAN_BACKUP_RETENTION_DAYS=14 \
sh scripts/backup_mysql.sh
```

脚本会：

- 使用 MySQL 容器自带的 `mysqldump`；
- 采用单事务逻辑备份；
- 先写临时文件，校验 gzip 后再原子改名；
- 生成 SHA-256 校验文件；
- 只清理指定目录中由本脚本生成且超过保留期的备份。

脚本使用备份目录内的原子目录锁防止两个任务并行执行。若主机断电或进程被强制终止，可能遗留空的 `.monipan-backup.lock`；确认没有备份进程后，可用 `rmdir` 删除该空目录再重试。只有 `.sql.gz` 与 `.sha256` 成对存在并通过校验，才视为一次成功备份。

脚本不会自动创建定时任务，也不会上传对象存储。确认手动备份成功后，再由服务器的 systemd timer、cron 或云端调度调用，并把加密副本保存到与当前云盘隔离的位置。

如果线上启动 Compose 时使用过 `-p` 或 `COMPOSE_PROJECT_NAME`，执行备份脚本时也必须提供同一个 `COMPOSE_PROJECT_NAME`，否则脚本找不到正在运行的 MySQL 容器。该值应以前述首次盘点记录为准，不能临时猜测。

## 6. 恢复演练

不要直接在生产数据库上试验恢复。恢复演练应使用一次性测试数据库或隔离服务器：

1. 校验 `.sha256`；
2. 使用 `gzip -t` 校验压缩文件；
3. 创建空的临时 MySQL 数据库；
4. 解压并导入；
5. 检查用户、账户、委托、成交和 Agent 记录数量；
6. 使用临时应用连接该数据库执行登录和读取冒烟测试；
7. 记录备份时间、恢复耗时、问题和执行人；
8. 删除一次性测试环境。

校验时应在备份所在目录执行 `sha256sum -c monipan-时间戳.sql.gz.sha256`，避免 sidecar 中的相对文件名指向错误位置。

只有真实恢复成功，备份才算有效。本阶段目标为最多丢失 24 小时数据，并在 8 小时内恢复核心服务。

## 7. 发布与回滚清单

发布前：

- 确认工作区版本和镜像标签；
- 运行后端测试、前端 typecheck/build；
- 先构建包含 `/readyz` 的新 backend 镜像；不能把新 Compose 与旧 backend 镜像组合部署，否则健康检查会持续失败；
- 构建前端镜像后，按下方命令给语法检查容器临时映射 `backend` 主机名；
- 生成并校验数据库备份；
- 保存当前 `.env`、镜像标签和 Compose project name；
- 确认没有同时启动第二个 backend worker 或副本。
- v0.3 发布前确认 `backend/app/services/market/calendar.py` 包含目标年份的沪深京休市公告；未知年份工作日默认停止委托和定时行情刷新。
- v0.3 会给 `stocks`、`orders`、`trades` 增加可空的行情来源时间字段；先备份，再在隔离环境用旧数据演练启动升级。旧记录保留空值，不会伪造历史快照时间。
- v0.4 使用 `create_all` 新增 `account_daily_snapshots`、`trade_notes`、`daily_review_notes`、`stock_raw_closes` 和 `raw_close_syncs`。后台首次运行会按账户历史成交逐日建立快照，并为有成交的股票渐进补取未复权历史收盘价；先在隔离库验证旧成交、资金流水和未复权价格的覆盖率，再发布。快照可重算，笔记属于用户数据，备份与恢复时必须一起保留。

```sh
frontend_image=$(docker compose images -q frontend)
test -n "$frontend_image"
docker run --rm --add-host backend:127.0.0.1 "$frontend_image" nginx -t
```

当前 Python requirements 使用版本范围而不是完整锁文件。即使本批没有修改依赖声明，backend 在无缓存重建时仍可能解析到范围内的新版本；审核期部署应保留并复用已验证的构建缓存，检查构建日志，并先在隔离环境完成启动与登录冒烟测试。

发布后：

- 检查 `docker compose ps`；
- 检查 `/health` 和 `/readyz`；
- 冒烟验证登录、账户读取、行情状态和订单预览；
- 查看 5xx、数据库连接、行情刷新和 AI 日志；
- 观察稳定后再清理旧镜像。

失败时优先回滚到上一镜像。涉及数据库结构变化时，必须先评估旧代码是否仍兼容新结构；本批次尚未引入数据库迁移框架，因此不要在审核期执行破坏性 Schema 修改。

## 8. 当前单实例约束

每个后端进程都会启动行情刷新和历史补全任务。当前必须保持：

- 一个 backend 容器；
- 一个 Uvicorn worker；
- `MONIPAN_DISABLE_MARKET_LOOP=0` 只出现在这个实例；
- 临时诊断或第二实例必须设置 `MONIPAN_DISABLE_MARKET_LOOP=1`。

扩容前需要把后台任务拆出 Web 生命周期，或引入可靠的单执行者租约。本阶段不引入 Redis、Celery或多副本。
