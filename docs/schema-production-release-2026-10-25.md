# Alembic 生产接入工具：2026-10-25

当前状态：**Alembic 基线已正式接入生产，backend 已切换到已验证镜像，发布检查通过；用户也确认前端测试没有问题。** 连续自动备份与生产日志轮转配置随后已验收通过；阶段 0 仍在进行中，实际失败回退演练及监控/CI 等仍待验收。

用户前端回归反馈见 `evidence/frontend-user-feedback-2026-10-25.json`。这是上线后的手工确认，未提供逐项执行明细，不记作助手运行的自动化浏览器 E2E 或跨浏览器覆盖。后续只读检查确认连续 7 天自动备份、8 份备份完整性及三个容器的日志轮转配置均通过，见 [连续备份及日志轮转](backup-and-log-verification-2026-10-25.md)。

## 正式上线结果

- 生产数据库已登记基线 `20261018_01`，生产启动与 readiness 的迁移版本检查已生效。
- 生产 backend 镜像：`sha256:89a426a296110d25c620a68caae2253c4b8ef71551f8ea5696ee3718a92fcd46`。
- 已收到 `PRODUCTION_BASELINE_REGISTERED`、`BACKEND_SWITCH_STARTING` 和最终 `PRODUCTION_ALEMBIC_RELEASE_VERIFIED`。
- 根据本次已核对 SHA-256 的脚本成功路径：后端源码同步、backend 健康/镜像和数据库版本检查、本机及公网四条路径检查、公网安全头检查均通过；原 frontend/MySQL 容器及受保护配置保持不变。用户未另行回传新 backend 容器 ID 或完整私有计划内容，不补造这些数据。
- 证据见 `evidence/schema-production-deployed-2026-10-25.json`。本次没有触发失败回退，不能记作实际回退演练通过。

**以下 prepare/deploy 命令是本次已完成操作的记录，不重复执行。** 保留发布计划、新备份、源码/配置快照和旧镜像标签，用于后续诊断或必要的回退。

## 服务器准备结果已回传

- 已收到 `PRODUCTION_RELEASE_PREPARED_NOT_DEPLOYED`；源码/配置快照、新备份及候选镜像对当前生产结构的只读检查通过。
- 发布计划：`/opt/monipan/backups/schema-release-20261018-kn9o5neq/production-20261025-5962df49b371ef9d9236d88e/deployment-plan.json`。
- 新备份：该计划目录内 `database/monipan-20261025T065725Z.sql.gz`，命名时间对应北京时间 2026-10-25 14:57:25，非完成耗时。
- 备份 SHA-256：`5d0dc866c89c59951365969416bac90d303f75a975c11c38b9d13b49c960b276`。
- 证据见 `evidence/schema-production-prepared-2026-10-25.json`。下面的 prepare 命令已执行完成，无需在计划有效期内重复。

## 已确认的发布依据

- 候选镜像：`sha256:89a426a296110d25c620a68caae2253c4b8ef71551f8ea5696ee3718a92fcd46`，基线 `20261018_01`。
- 旧镜像：`sha256:2bc78105792958fe58f54438a4d892067f28cb9f62d9ee247515d09136a2b2e2`；保留标签 `monipan-backend:before-schema-20261018-da89fb699afd45fa19b58970`。
- Compose 2.32.4；backend、frontend、MySQL 均 healthy，位于 `monipan_default`。
- backend 为 build-only 配置，无自定义启动命令、端口或源码挂载；健康检查使用 `/readyz`，停止宽限 5 分钟。
- 盘点证据见 `evidence/schema-production-preflight-2026-10-25.json`。这些是当时的读取结果，工具每次执行仍会重新核对。

## 新交付文件

- 桌面：`C:/Users/Czh13/Desktop/monipan/monipan-schema-deploy-20261025.py`。
- 字节数：`44558`。
- SHA-256：`8180dddd780b1c20a2e6611368e3675d2e248b79cfacf54295b2a5bf9688eadd`。
- 仍需保留服务器已有的 `/home/ubuntu/monipan-schema-retry-20261025.py`，新工具会先核对其 SHA-256，只加载已验证的读取/校验函数，不重新执行候选镜像重试。

工具入口为 `prepare`、`deploy`、`rollback`；默认是 `prepare`。本次 prepare 和 deploy 已完成，原准备计划已被消费，不用于下一轮发布。

## 第一步：新备份和生产结构检查

将新交付文件上传到 `/home/ubuntu/`，在当前 root 终端执行：

```sh
printf '%s\n' '8180dddd780b1c20a2e6611368e3675d2e248b79cfacf54295b2a5bf9688eadd  /home/ubuntu/monipan-schema-deploy-20261025.py' | sha256sum -c - &&
python3 -u /home/ubuntu/monipan-schema-deploy-20261025.py prepare
```

准备动作会：

1. 重新核对成功状态、生产容器、配置指纹和两个镜像；确认默认 backend 镜像引用确实是当前旧镜像，配置有变化即停止。
2. 将待替换的每个后端源码文件与正在运行的旧镜像比较，保存原始源码和配置的私有快照，拒绝覆盖未知改动。
3. 在原发布目录下创建独立 `production-20261025-<token>` 目录，生成新的 MySQL 备份，校验 SHA-256 和 gzip。
4. 用已验证候选镜像启动一次受限的临时检查容器，关闭后台任务，以 MySQL 只读会话检查当前生产结构；不导入应用生命周期，不登记版本。
5. 保存 `deployment-plan.json`，输出新备份位置、校验值及 `PRODUCTION_RELEASE_PREPARED_NOT_DEPLOYED`。

此动作会创建备份、快照和临时诊断容器，不修改生产数据库结构、后端源码或线上配置，不停服、不切换生产容器。回传完整输出后再继续正式发布。

## 第二步：正式发布记录（已完成）

发布前的准备计划有效期为两小时；本次在有效期内执行通过。以下保留实际执行的命令，记录其登记生产基线、同步后端源码及切换 backend 的行为。

```sh
printf '%s\n' '8180dddd780b1c20a2e6611368e3675d2e248b79cfacf54295b2a5bf9688eadd  /home/ubuntu/monipan-schema-deploy-20261025.py' | sha256sum -c - &&
python3 -u /home/ubuntu/monipan-schema-deploy-20261025.py deploy --plan '/opt/monipan/backups/schema-release-20261018-kn9o5neq/production-20261025-5962df49b371ef9d9236d88e/deployment-plan.json'
```

正式发布会重新校验备份、源文件、容器及生产结构，随后：

- 给现有生产库登记基线，仅写迁移元数据；不会自动执行破坏性迁移或删除业务表。
- 将候选上下文中经过校验的应用源码和 requirements 同步到服务器的对应文件；不整体 Git reset，不覆盖其他文件。
- 保留独立旧镜像标签，将 Compose 默认 backend 镜像引用更新到候选镜像；使用 `--no-build --pull never --no-deps --force-recreate --wait` 仅更新 backend。原 Compose、Nginx、`.env` 和 MySQL 数据卷不变。
- 新 backend 健康后检查并 reload 原 frontend Nginx，刷新 backend 地址解析；frontend 容器不重建。
- 核对线上迁移版本；分别检查本机入口和公网 HTTPS 的首页、readiness、匿名身份接口、文档禁用路径，检查公网强制 CSP、HSTS、nosniff 和帧保护头。

成功标志：`PRODUCTION_ALEMBIC_RELEASE_VERIFIED`。该标志覆盖上述自动发布检查；用户随后独立确认前端回归正常，全部限流规则及阶段 0 其他验收仍待完成，不扩展为自动化浏览器覆盖。

## 失败与回退边界

- 基线登记步骤失败或结果不确定时，不切换 backend，保留计划和诊断信息，输出 `BASELINE_ADOPTION_REQUIRES_INSPECTION_BACKEND_UNCHANGED`；须先查明版本记录实际状态。
- 登记成功后的发布失败会尝试恢复原源码及旧 backend 镜像，reload frontend 并复核本机/公网；保留已登记的基线，不执行 schema downgrade、不自动还原生产备份。
- 回退成功标志为 `ROLLBACK_OK_BASELINE_RETAINED`；原发布动作仍以失败退出，不能当作新版本发布成功。
- 回退也失败时输出 `ROLLBACK_FAILED_REQUIRES_ATTENTION`，停止后续动作并保留备份，回传完整输出。
- 手工回退入口为 `rollback --plan '<实际计划路径>'`。它同样核对容器、配置、源码及基线，拒绝覆盖发布后另行产生的源码改动。

## 本地验证及尚未验证的范围

**100 项相关检查通过**，其中本次新增发布工具相关 50 项，原镜像准备/重试/隔离恢复相关 50 项。关键场景同时针对源码和生成的交付文件执行：备份或配置损坏、过期计划、错误镜像引用、登记不确定、候选启动失败、公网检查失败、回退失败及保留原源码等。

```powershell
& .\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_schema_production_deployment.py backend/tests/test_schema_release_preparation.py backend/tests/test_schema_release_retry.py backend/tests/test_restore_application.py -q -p no:cacheprovider --basetemp=.docker/pytest-production-release-final-20261025
```

本地测试使用真实临时文件和 Docker/网络命令替身。服务器准备与正常发布成功已由后续用户回传证实；前端回归已获用户手工确认，实际失败回退未演练，阶段 0 仍为进行中。
