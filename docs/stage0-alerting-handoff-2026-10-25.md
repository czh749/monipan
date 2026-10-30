# MoniPan 阶段 0 交接：接续监控与告警

交接日期：2026-10-25。工作区：`D:/monipan`，本机为 Windows / PowerShell。

**本文件记录交接时的断点：当时只做了只读排查，尚未编写、测试或部署告警功能，渠道未确定。接续进度以紧随其后的更新及链接为准。**

> 2026-10-25 接续更新：用户已选择 SMTP 邮件，并要求个人项目保持简单、降低频率。本地已准备独立脚本与 systemd 定时器：每 5 分钟检查，连续两次异常才通知，持续故障每 24 小时提醒、连续两次正常后通知恢复；78 项相关检查通过。用户已上传并回传服务器只读预检全部通过，最近备份约 16.96 小时前，证据见 `docs/evidence/production-monitor-server-preflight-2026-10-25.json`。下一步按 [轻量邮件告警操作说明](production-monitoring-2026-10-25.md) 第 2 步安装，再私有配置 SMTP 和测试收件；尚未收到安装、真实邮件或定时器启用证据，不重复原发布或备份安装。

> 最新断点：用户选用 QQ 邮箱，曾担心隐藏输入处重复粘贴授权码；`test-mail` 返回 `SMTPServerDisconnected`。随后诊断确认主机和 SSL/465 正确，保存的授权码却为 63 字符，诊断在 CONFIG 阶段即停止。下一步隐藏重填唯一的 `smtp.password` 字段，校验 16 字符后原子保存，再测试邮件；此前断开是否仅由错误授权码引起仍待复测。完整 unit 安装状态、真实收件与 timer 启用尚未验收，不重复安装。证据见 `docs/evidence/production-monitor-qq-config-diagnostic-2026-10-25.json`。

> 2026-10-26 补充：用户在授权码修复与邮件复测步骤后反馈“好了”；该简短反馈未明确两封测试邮件及 timer 状态，历史失败不再视为当前配置的确证，下一步收集实际收件和自动调度记录。本地 Git 整理在 `codex/stage0-git-20261026` 分支进行，不推送或重复发布，见 [Git 整理记录](git-organization-2026-10-26.md)。

## 1. 新会话先读什么

1. 本文件。
2. 阶段规划：`C:/Users/Czh13/Desktop/monipan/MoniPan_01_阶段0_线上生产底座加固.md`。第 9 节是 2026-10-18 的历史断点，第 10 节记录正式上线，第 11 节是本次交接；以最新记录为准。
3. 生产发布记录：`D:/monipan/docs/schema-production-release-2026-10-25.md`。
4. 连续备份和日志配置验收：`D:/monipan/docs/backup-and-log-verification-2026-10-25.md`。
5. 运行手册：`D:/monipan/docs/production-hardening.md`；每日备份细节：`D:/monipan/docs/daily-backup-2026-10-18.md`。

旧笔记中“生产没有登记基线”“最终镜像权限仍阻塞”“连续备份待观察”等结论已被本次服务器回传结果更新，不能作为当前断点。

## 2. 已经完成并取得证据的工作

| 工作 | 已确认结果 | 验证边界 |
| --- | --- | --- |
| 公网入口与会话安全 | HTTPS / 可信代理、安全 Cookie、安全响应头、登录入口限流及证书续期模拟检查通过 | 其他限流规则、两个公网客户端隔离等边界仍有待验收 |
| 每日自动备份 | 上海时间 03:00；2026-10-19 至 25 日连续 7 天定时成功；现存 8 份备份 gzip / SHA-256 均通过 | 8 份未逐一导入；超过保留阈值后的清理尚未实测 |
| 真实隔离恢复 | 使用生产备份导入隔离 MySQL，核验账户、资金账本、持仓、交易和 AI 数据，通过应用 API 检查 | 不是对生产库还原，也不是整机灾难恢复 |
| Alembic 正式上线 | 生产登记基线 `20261018_01`；新后端已部署，启动及 readiness 检查版本 | 本轮为既有数据库接入版本管理；未来结构变更仍须单独迁移与验收 |
| 受控生产发布 | 最终镜像先经隔离核验，生产备份后仅替换 backend；本机/公网检查通过 | 保留旧镜像、快照与回退工具；真实失败回退尚未演练 |
| 前端回归 | 用户明确反馈“前端测试没有问题” | 用户手工确认，未提供逐项明细，不记为助手自动化 E2E |
| 健康检查与日志配置 | request ID、`/livez`、带数据库及迁移版本检查的 `/readyz`；三个容器实际配置均为 `json-file`、`max-size=10m`、`max-file=5` | 尚未接入主动告警；未单独制造日志达到阈值来实测滚动过程 |

**阶段 0 整体仍为进行中。** 结构化日志与监控告警、依赖锁定、CI、真实回退及部分边界验收仍未完成；不能因本轮发布成功而全部勾选。

## 3. 服务器操作方式与生产资源

- 用户使用腾讯云网页终端，普通账号为 `ubuntu`，通过 `sudo -i` 切换 root。助手没有已验证可用的直接 SSH 连接。
- 交付方式沿用：本地准备文件及操作说明 → 复制到桌面 `C:/Users/Czh13/Desktop/monipan` → 提供 SHA-256 → 用户上传并在网页终端执行 → 用户回传结果。
- 上传文件不一定在 `/root`；本轮文件实际位于 `/home/ubuntu/`。执行前核对实际路径与校验值。
- 生产目录 `/opt/monipan`；Compose 项目名 `monipan`；生产 Compose 文件 `/opt/monipan/compose.yaml`。
- 公网链路：浏览器 → 宿主机 Nginx（TLS）→ frontend Nginx → backend；公网域名 `monipan.top`。
- 每日备份目录 `/opt/monipan/backups/daily`；systemd 单元 `monipan-backup.timer` / `monipan-backup.service`。
- 2026-10-25 回传时 timer 为 enabled、active，最近任务 `Result=success`、`ExecMainStatus=0`。这些是当时的验收结果，后续配置生产告警前应重新只读核对。

### 已成功发布的资源身份，仅供核对或回退定位

- 当前已验证生产后端镜像：`sha256:89a426a296110d25c620a68caae2253c4b8ef71551f8ea5696ee3718a92fcd46`。
- 基线：`20261018_01`。
- 旧镜像：`sha256:2bc78105792958fe58f54438a4d892067f28cb9f62d9ee247515d09136a2b2e2`。
- 旧镜像保留标签：`monipan-backend:before-schema-20261018-da89fb699afd45fa19b58970`。
- 发布状态：`/opt/monipan/backups/schema-release-20261018-kn9o5neq/release-state.json`。
- 已发布计划：`/opt/monipan/backups/schema-release-20261018-kn9o5neq/production-20261025-5962df49b371ef9d9236d88e/deployment-plan.json`。
- 发布前新备份：该计划目录内 `database/monipan-20261025T065725Z.sql.gz`，SHA-256：`5d0dc866c89c59951365969416bac90d303f75a975c11c38b9d13b49c960b276`。
- 正式成功标记：`PRODUCTION_BASELINE_REGISTERED`、`PRODUCTION_ALEMBIC_RELEASE_VERIFIED`。

本次 prepare / deploy 已完成，不重复执行；候选镜像验收时的隔离 MySQL 及匿名卷已经清理，不再视为可复用资源。

## 4. 告警排查已经看到了什么

在本次检索范围内未发现已实现的告警模块。只读检索了 `D:/monipan/backend/app`、`D:/monipan/deploy`、`D:/monipan/scripts`、环境变量示例、Compose 和运维文档，没有找到已有的通知渠道配置或通知模块；没有读取、打印真实 `.env` 密钥，也没有向用户询问渠道。

| 文件入口 | 当前实现与接续用途 |
| --- | --- |
| `D:/monipan/backend/app/main.py` | `add_request_observability` 中间件约第 326 行；记录请求 ID、方法、路径、状态和耗时，异常用 `logger.exception` 记录后继续抛出。当前是文本日志，logger 为 `uvicorn.error` |
| `D:/monipan/backend/app/main.py` | `/livez` / `/readyz` 约第 403 / 410 行；可作为应用与数据库可用性信号，具体实现继续阅读确认 |
| `D:/monipan/deploy/systemd/monipan-backup.service` | root / oneshot，输出 journal，单次超时 30 分钟；现无 `OnFailure` 通知钩子 |
| `D:/monipan/deploy/systemd/monipan-backup.timer` | 每日 03:00 Asia/Shanghai，`Persistent=true`；已在线运行，不能因新增告警随意覆盖或重复安装 |
| `D:/monipan/scripts/install_backup_timer.py` | 既有受保护安装方式，可参考其预检、私有权限、校验和交付模式；不要重新运行已完成的备份安装 |
| `D:/monipan/scripts/backup_mysql.sh` | 现有备份和完整性校验；应阅读其退出码及清理逻辑，再决定告警接入点 |
| `D:/monipan/compose.yaml` | 三个服务的日志轮转及 healthcheck 定义；存量服务器配置须单独核对，不能整体覆盖 |

## 5. 下一步路线：尚未实施的建议

1. **先确认通知渠道和接收目标。** 这是当前缺失的关键信息，不能猜测邮箱、Webhook 或第三方账号。可先说明可选渠道及所需信息，用一个简短问题确定；同时继续独立的本地设计和测试。真实密钥只进入受保护配置，不写 Git，不让用户把密钥贴进聊天。
2. **确定首批可可靠识别的异常。** 优先覆盖服务 / 容器不可用、readiness / 数据库异常、磁盘空间不足，以及备份任务失败或备份过期。确认探测频率和阈值，处理备份正在运行、维护窗口、首次安装等状态。5xx、延迟、行情陈旧、抓取失败、AI 失败率和成本属于原规划覆盖范围；缺少采集信号的部分明确留待后续，不因基础告警完成而一并勾选。
3. **分清状态采集、告警判断与通知发送。** 具备故障触发、重复抑制、持续故障提醒和恢复通知；通知失败要留痕并有边界明确的重试，避免每分钟重复轰炸。通知只包含定位所需的信息，避免携带密码、完整环境变量或用户业务数据。
4. **选择与当前规模相称的部署方式。** 可以评估独立于 Web 进程的探测任务，尽量复用已存在的 healthcheck、systemd 状态和备份结果，避免在多个 worker 内重复发通知。具体方案尚未选定，也未购买任何云服务。宿主机上的自检无法在整机宕机 / 断网时自行通知；若需覆盖该故障，应另行确定外部探测落点。
5. **本地完成实现和有意义的验证后再交付。** 测试故障触发、阈值、去重、恢复、发送失败 / 超时及敏感信息处理。使用合成故障和命令替身验证，生产验收使用明确标识的测试事件，避免通过停止生产数据库制造故障。
6. **提供可校验的服务器交付件。** 若需要新增系统服务或配置，先完成预检、私有配置、重复安装保护、撤销方式和操作说明，再交由用户在网页终端执行。收到服务器安装结果、实际测试通知送达及恢复通知证据后，才标记相应告警已上线。

用户原话为“那就先补齐这一块自动报错吧”，已经授权推进该任务，无需重新询问是否开始。通知渠道选择仍须补齐。用户习惯先看清晰路线，执行过程中解释关键结果；对于明确的实现任务不要因教学持续停下提问。

## 6. 保留的范围与工程约束

- 用户此前明确暂缓加密异地备份 / COS / 个人电脑副本，以及证书续期失败和临近到期告警；本次泛指自动报错不自动改变这些具体暂缓决定。
- 不执行 `docker compose down -v`，不删除 / 重建生产 MySQL 卷，不 reset 服务器工作区，不整体覆盖 Compose、Nginx 或 `.env`。
- 不打印完整 `docker inspect`、带密钥的 Compose 展开配置或真实环境变量；诊断输出采用所需字段白名单。
- 需要服务器操作时先准备具体可审查文件与命令，再由用户执行；不要把本地测试成功写成生产送达成功。
- 目前仓库有大量前序修改和未跟踪文件，尚未整理为本轮 Git 提交。包括生产配置、Alembic、发布 / 备份脚本、测试及文档。接续时先看 `git status --short` / 差异，保留全部已有工作，不清理为“干净工作区”。
- 本次为交接，只新增 / 更新交接文档与阶段规划，没有修改告警业务代码或服务器。

## 7. 既有验证和证据位置

- 生产发布：`D:/monipan/docs/evidence/schema-production-deployed-2026-10-25.json`。
- 用户前端反馈：`D:/monipan/docs/evidence/frontend-user-feedback-2026-10-25.json`。
- 备份与日志配置：`D:/monipan/docs/evidence/backup-and-log-verification-2026-10-25.json` 及同名 `.txt`；原始终端文本 SHA-256 为 `0aa5b8ea5264e000d67f7a44568a2978ffcf62bde6c17173f0b0f6af25612464`。
- 发布前的本地关键测试曾 100 项通过，运行命令如下；这是既有结果，本次交接没有重新运行：

```powershell
Set-Location 'D:/monipan'
& './backend/.venv/Scripts/python.exe' -m pytest backend/tests/test_schema_production_deployment.py backend/tests/test_schema_release_preparation.py backend/tests/test_schema_release_retry.py backend/tests/test_restore_application.py -q -p no:cacheprovider --basetemp=.docker/pytest-production-release-final-20261025
```

这组测试验证发布 / 重试等流程，含受控命令替身；既有 100 项通过不构成新告警功能的测试证据。后续只运行与新修改相关的必要检查，不为交接重复运行隔离恢复或生产发布。

## 8. 可粘贴到新会话的开场消息

> 请在 D:/monipan 接续阶段 0 的“自动监测与告警”。先阅读 D:/monipan/docs/stage0-alerting-handoff-2026-10-25.md 和 C:/Users/Czh13/Desktop/monipan/MoniPan_01_阶段0_线上生产底座加固.md 的第 10、11 节。Alembic 已正式上线，连续 7 天自动备份及三个容器日志轮转配置均已验收，不重复部署。告警目前只做过只读排查，尚未实现或部署，通知渠道未确定；先确定渠道，同时推进独立的本地实现准备。我通过腾讯云网页终端执行你准备的服务器命令。保留现有生产配置、数据和本地未提交修改；异地备份及证书告警继续暂缓。请主动推进明确的工作，解释关键原理和验证边界。
