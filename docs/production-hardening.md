# MoniPan 线上加固运行手册

本文记录第一批生产保障及 2026-10-17 线上修复的源码配置。已确认链路为浏览器 → 腾讯云宿主机 Nginx（TLS）→ frontend Nginx → backend。历史验收不代表证书续期、自动备份、迁移和告警已经完成，后续拓扑变化仍需重新检查。

2026-10-25 最新状态：生产 Alembic 基线已登记，backend 发布与本机/公网检查通过，见 `schema-production-release-2026-10-25.md`。连续 7 天自动备份、现存 8 份备份完整性及三个容器实际日志轮转配置也已验收通过，见 `backup-and-log-verification-2026-10-25.md`。本次保留了线上原 Compose、Nginx 和 `.env`；下面的仓库标准配置示例不代表已整体覆盖存量服务器。阶段 0 其他验收仍待完成。

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

新环境采用仓库标准部署时使用以下 `.env` 配置示例；存量生产本轮继续沿用已校验的原 Compose 与配置，后续合并示例须单独核对：

```dotenv
COMPOSE_FILE=compose.yaml:compose.production.yaml
COMPOSE_PROJECT_NAME=monipan
MONIPAN_ENVIRONMENT=production
MONIPAN_COOKIE_SECURE=1
MONIPAN_FORWARDED_ALLOW_IPS=172.18.0.0/16
MONIPAN_MYSQL_PASSWORD=唯一且足够强的密码
MONIPAN_MYSQL_ROOT_PASSWORD=另一个唯一密码
```

以上 `COMPOSE_FILE` 使用 Linux 的冒号分隔，供 `/opt/monipan` 的生产部署使用，使普通 `docker compose` 命令和备份脚本都自动加载生产覆盖文件。本地 Windows HTTP 开发只使用 `compose.yaml`，不要复制生产 `.env`。生产覆盖文件强制 production 与 Secure Cookie，并要求显式提供后端可信来源。现有线上项目名为 `monipan`；其他部署必须保留自己的原项目名，不能通过改名切换到空数据卷。

网段来自 2026-10-17 的实际检查，并非 Docker 的固定默认值；发布前确认仍为 `172.18.0.0/16`、网关 `172.18.0.1`，且只有受控项目容器。不匹配时先调整可信配置，不要重建数据库或网络来迎合示例。

应用在显式 production 模式下会拒绝 SQLite、开发默认数据库密码和非 Secure Cookie。已有 MySQL 卷初始化后，直接修改 `.env` 不会同步修改数据库用户密码；必须先在数据库中完成密码轮换并验证，再更新应用连接配置。

当前 Compose 会把应用数据库密码直接拼入 SQLAlchemy URL，因此应用用户密码暂时应使用至少 32 位的 URL-safe 随机字符（`A-Z`、`a-z`、`0-9`、`_`、`-`）。`@`、`:`、`/`、`#`、`%` 等字符需要 URL 编码，直接填入可能导致连接串解析失败。root 密码不进入该 URL，不受此限制。

AI 和新闻密钥缺失不会阻止核心服务启动，对应能力会按现有逻辑明确降级。

## 3. 健康检查

- `/health`：兼容原有外部探针，只证明应用进程能响应；
- `/livez`：容器存活检查，不访问数据库；
- `/readyz`：执行轻量 `SELECT 1` 并核对数据库迁移版本；数据库不可用或迁移版本不匹配时返回 503，不输出连接信息；
- 行情、新闻和 AI 不进入核心 readiness，它们应单独显示 degraded/unavailable。

Compose 的 backend healthcheck 使用 `/readyz`，frontend healthcheck 只检查本容器静态页面。

迁移版本检查已于 2026-10-25 随新 backend 正式发布，生产基线为 `20261018_01`。生产启动只接受与发布版本匹配的数据库，不再自动执行 `create_all` 或手写 DDL。最终镜像隔离验收、生产登记及正常发布检查均通过，前端回归获用户手工确认；实际回退演练仍待验收。证据见 `schema-production-release-2026-10-25.md`。

## 4. 代理和真实 IP

`frontend/nginx.conf` 使用两项独立判断：先依据原始连接来源 `$realip_remote_addr` 判断入口是否可信，再只接受可信入口提供的 `http` / `https` 协议值；其他情况回退到当前连接的 `$scheme`。向后端重新写入 `X-Real-IP` 和单值 `X-Forwarded-For`，不保留客户端提供的地址链。

- 本地镜像和基础 Compose 使用 `frontend/proxy-trust.conf`，不信任外部转发头。
- 生产覆盖文件将 `deploy/nginx/proxy-trust.production.conf` 挂载到同一位置，只信任已确认的宿主机入口 `172.18.0.1`，以 `X-Real-IP` 恢复客户端地址，关闭递归处理。
- 后端通过 `FORWARDED_ALLOW_IPS` 信任受控项目网段 `172.18.0.0/16`，不使用 `*`。增加其他容器、CDN 或负载均衡，或者重建网络后，都要重新核对两处可信配置。

基础 Compose 将内部 Nginx、安全头和本地可信配置以只读方式挂载；生产覆盖文件仅替换可信配置的来源，不改变数据库、数据卷、网络或端口映射。基础前端镜像也包含本地默认配置。

宿主机配置以两个片段归档：

- `deploy/nginx/host-proxy.conf`：现有 HTTPS `server` 中 `location /` 的代理配置；宿主机必须覆盖客户端提供的真实 IP 与协议头。
- `deploy/nginx/host-hsts.conf`：现有 HTTPS `server` 中的一年 HSTS，不带 `includeSubDomains` 或 `preload`。

片段用于对照或由原站点配置引用，不是完整的 Certbot 站点文件；不覆盖现有证书路径、HTTP 跳转及续期配置。现有站点若已包含相同指令，避免重复添加。HSTS 只在终止 TLS 的宿主机配置，内部 HTTP 容器不添加。容器安全头已使用强制 `Content-Security-Policy`，策略内容与 2026-10-17 验收版本相同。

上线前至少验证：

1. HTTPS 页面中的后端请求被识别为 HTTPS；
2. 两个不同公网客户端不会被记录成同一个代理 IP；
3. 浏览器伪造的转发头会被最外层代理覆盖，未受信任的直连请求无法伪造协议或 IP；
4. 后端 8000 和 MySQL 3306 无法从公网直接访问。

不要使用 `set_real_ip_from 0.0.0.0/0`，也不要在不知道腾讯云实际网络拓扑时猜测可信 CIDR。

2026-10-17 已验收：尾斜杠 API 的跳转保留 HTTPS、生产 Cookie、伪造 IP 对照、Nginx 登录入口限流，以及首页 200、匿名接口 401、文档路径 404 的安全头。两个独立公网客户端的隔离、其他限流规则和完整登录态业务回归仍需补齐。同步依据和边界见 `docs/production-sync-2026-10-18.md`。

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

针对现有 `/opt/monipan` 生产项目，`deploy/systemd/monipan-backup.service`、`monipan-backup.timer` 和 `scripts/install_backup_timer.py` 已于 2026-10-18 安装并通过首次备份验证。2026-10-25 回传证据确认：2026-10-19 至 2026-10-25 连续 7 天在上海时间 03:00 自动运行成功，现存 8 份备份 gzip 与 SHA-256 校验通过，timer 为 enabled、active。输出独立目录为 `backups/daily`，保留阈值 14 天；当前尚未实测阈值到达后的清理。操作与状态见 `docs/daily-backup-2026-10-18.md`。

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

2026-10-18 已使用新的生产备份完成隔离 MySQL 实际导入、9 个账户资金对账及应用 API 读取、持仓数量重放、3 条 AI 记录隔离、合成账号认证与原始业务行不变性检查，临时容器和匿名卷已清理，当时正式服务均 healthy。结果见 `docs/backup-restore-2026-10-18.md`。2026-10-25 已补齐连续 7 天自动备份证据，但未逐一恢复现存 8 份备份；加密独立副本保持用户暂缓。这些检查不代替整机灾难恢复。

## 7. 发布与回滚清单

发布前：

- 确认工作区版本和镜像标签；
- 首次同步本次配置时，先备份线上存在改动的 Compose、Nginx、安全头和受保护的 `.env`，再合并仓库版本。不要直接用 Git 重置丢弃昨天的配置；本地同步不代表已经完成线上部署。
- 保留 `.env` 中的真实密码与 API 密钥，仅合并新增的 `COMPOSE_FILE`、现有项目名和可信网段配置，不要用示例文件整体覆盖。
- 确认 `docker compose config --quiet` 成功，生产环境确实加载 `compose.production.yaml`，并核对可信网关、网段和配置挂载；不要把包含真实密钥的完整 config 输出贴入日志。
- 运行后端测试、前端 typecheck/build；
- 先构建包含 `/readyz` 的新 backend 镜像；不能把新 Compose 与旧 backend 镜像组合部署，否则健康检查会持续失败；
- 构建前端镜像后，先检查镜像默认配置，再检查 Compose 挂载后的有效配置；两项检查均不启动后端；
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
docker compose run --rm --no-deps --add-host backend:127.0.0.1 --entrypoint nginx frontend -t
```

以下为通用源码构建发布模板。2026-10-25 本轮已通过专用工具完成生产基线接入及 backend 发布，实际镜像和执行记录见 `schema-production-release-2026-10-25.md`，不重复执行以下模板。后续新版本仍须先构建、备份、检查迁移，再更新应用；不能把本次固定基线工具直接用于其他版本。

```sh
docker compose build backend frontend
sh scripts/backup_mysql.sh
docker compose run --rm --no-deps backend python -m app.schema check
# 仅首次接入已有无版本库、检查通过并留存备份后执行：
docker compose run --rm --no-deps backend python -m app.schema adopt-baseline
docker compose run --rm --no-deps backend python -m app.schema upgrade
docker compose up -d --no-deps --wait backend frontend
```

只读挂载新增后，即使复用原镜像也需要重建前端容器以应用挂载。不要只执行 `nginx -s reload`。整个过程保留现有 MySQL 容器、项目名、网络和数据卷。

当前 Python requirements 使用版本范围而不是完整锁文件。本轮从已知旧镜像派生，应用与 vendor 内容固定，已完成最终镜像隔离验收和正式发布；这不能替代依赖治理。将来重新解析 requirements 构建镜像仍可能获得不同依赖，需锁定版本/哈希，并重新核验实际镜像与业务行为。

发布后：

- 检查 `docker compose ps`；
- 检查 `/health` 和 `/readyz`；
- 冒烟验证登录、账户读取、行情状态和订单预览；
- 对首页、匿名 API、404 和 429 响应核对强制 CSP 及其他安全头；公网 HTTPS 还应包含一年 HSTS；确认 `/api/auth/me/`、空 JSON 的 `/api/auth/login/` 跳转均保留 HTTPS；
- 查看 5xx、数据库连接、行情刷新和 AI 日志；
- 观察稳定后再清理旧镜像。

失败时恢复对应发布的镜像和配置快照。现在 Nginx、安全头和可信配置通过挂载生效，仅回滚镜像不能恢复这些文件；还需恢复 Compose、生产覆盖文件、三个挂载源和 `.env` 中相关设置，再重建应用容器。宿主机配置回滚前后都要执行 `nginx -t`。较早的 2026-10-17 基础回滚脚本可能同时撤销后续 CSP 挂载，只回滚某一项时应使用对应快照。

浏览器缓存的一年 HSTS 不会随文件回滚立即清除；需要有效 HTTPS 返回 `max-age=0` 并由对应浏览器再次访问。涉及数据库结构变化时，必须先评估旧代码是否仍兼容新结构。Alembic 初始基线已于 2026-10-25 正式接入，基线降级已被禁止；本轮应用回退保留版本元数据，恢复兼容旧镜像和对应源码，不删除业务表。确需恢复数据库时另行制定受控恢复方案。

## 8. 当前单实例约束

每个后端进程都会启动行情刷新和历史补全任务。当前必须保持：

- 一个 backend 容器；
- 一个 Uvicorn worker；
- `MONIPAN_DISABLE_MARKET_LOOP=0` 只出现在这个实例；
- 临时诊断或第二实例必须设置 `MONIPAN_DISABLE_MARKET_LOOP=1`。

扩容前需要把后台任务拆出 Web 生命周期，或引入可靠的单执行者租约。本阶段不引入 Redis、Celery或多副本。

## 9. 轻量邮件告警（本地已准备，生产待验收）

2026-10-25 用户确定 SMTP 邮件，并要求按个人项目降低检查频率。新增独立 `scripts/monitor_production.py` 与 `monipan-monitor.service/timer`，每 5 分钟检查基础可用性、磁盘和既有备份状态；连续两次异常才通知，持续故障每日提醒、连续两次正常后通知恢复。私有 SMTP 配置放在 `/etc/monipan-monitor/`，不进入项目环境文件。

源码及单文件上传交付件相关 78 项本地检查通过，尚未安装生产或发送真实邮件。服务器步骤、收件确认、暂停方式和未覆盖范围见 [轻量邮件告警操作说明](production-monitoring-2026-10-25.md)。不重新执行既有 Alembic 发布或备份安装，不修改生产 Compose、Nginx、数据库或 `.env`；阶段 0 仍为进行中。

## 10. 2026-10-26 本地 Git 归档

阶段 0 本地变更已按生产安全配置、数据库迁移、备份恢复、受控发布、邮件监测和运行记录分组整理到 `codex/stage0-git-20261026`；当前工作区后端 361 项通过、20 项缺 Nginx 测试程序而跳过，前端 12 项回归、类型检查与构建通过。干净目录后端 327 项通过、54 项条件跳过；具体范围见 [Git 整理记录](git-organization-2026-10-26.md)。

邮件已取得用户“好了”的简短恢复反馈，但两封测试邮件实际接收、监测 timer 启用及自动运行尚缺明确证据；第 9 节为本地准备时的历史状态，最新以监测说明和用户反馈记录为准。本次只产生本地提交，没有推送或操作生产；依赖锁定、CI、真实回退及剩余边界验收仍待完成。
