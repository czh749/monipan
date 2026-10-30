# 数据库迁移准备：2026-10-18

## 本轮范围

2026-10-25 接续：**生产基线 `20261018_01` 已登记、backend 已正式切换，用户回传 `PRODUCTION_ALEMBIC_RELEASE_VERIFIED`，并确认前端测试没有问题。** 生产镜像为 `sha256:89a426a296110d25c620a68caae2253c4b8ef71551f8ea5696ee3718a92fcd46`，本机/公网及迁移版本检查通过。完整记录见 [生产发布记录](schema-production-release-2026-10-25.md)，自动发布及用户手工反馈证据分别保留在 `evidence/`。后续连续自动备份和生产日志轮转配置也已验收通过；实际失败回退及阶段 0 其他事项仍待验收。下文的 2026-10-18 内容为历史断点。

用户明确跳过加密独立副本和存储桶采购，转入阶段 0 的数据库迁移工作。每日备份调度已启用，首次运行及后续连续 7 天定时运行已验证，见 `daily-backup-2026-10-18.md` 和 `backup-and-log-verification-2026-10-25.md`。

截至 2026-10-18，已完成生产结构只读预检及真实 MySQL 隔离演练，当时生产基线和正式发布尚未完成。2026-10-25 的正式生产接入结果见上方最新记录；仍须区分隔离演练与生产验收证据。

## 2026-10-18 线上旧版本行为（历史）

- `backend/app/main.py` 的生命周期启动会执行 `Base.metadata.create_all()` 和 `migrate_database()`。
- `backend/app/database.py` 的手写迁移会补字段、建立唯一索引、为官方文档回填日期、移除 MySQL 冗余索引，并将股票成交量扩宽为 BIGINT。
- `backend/app/invites.py` 的邀请管理 CLI 也会调用 `create_all()`。
- 线上库没有 Alembic 版本表。本地新框架尚未部署，不能将本地实现视为生产接入完成。

Alembic 的 [迁移教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)、[现有数据库接入模式](https://alembic.sqlalchemy.org/en/latest/cookbook.html) 和 [自动生成的检测范围](https://alembic.sqlalchemy.org/en/latest/autogenerate.html) 作为后续设计依据。自动生成结果仍需审查；写入基线版本不等于执行数据库升级。

## 已准备的只读工具

- `backend/app/schema_audit.py`：从 SQLAlchemy Inspector 读取结构，核对字段类型和可空性、主键、唯一键、必要的具名索引及外键。
- `scripts/audit_production_schema.py`：核对运行中的 `monipan` backend 容器身份，只在该容器中启动一次独立的 Python 读取进程，不运行应用生命周期。
- `scripts/build_schema_audit.py`：从当前本地模型构建预期结构清单，将清单和只读逻辑嵌入单文件交付件。服务器不需要先安装 Alembic 或更新镜像。

交付文件：桌面 `monipan/monipan-schema-audit-20261018.py`，可上传到服务器 `/root` 后通过交付时提供的 SHA-256 校验再执行。

构建方式（使用已有后端虚拟环境，不连接数据库）：

```powershell
& .\backend\.venv\Scripts\python.exe scripts/build_schema_audit.py
```

生产检查只要求 Docker CLI 和 backend 镜像中原有的 SQLAlchemy/PyMySQL：

- 使用既有 Compose 项目及容器标签核对目标，未确认身份时不执行容器内读取。
- 禁止执行 SQL 白名单之外的语句；白名单只允许结构读取、SELECT，以及显式将当前连接的事务默认设为只读。
- 使用一个新连接设置 MySQL `SET SESSION TRANSACTION READ ONLY`，只影响此次读取会话。
- 不拉取或构建镜像，不创建、停止或重启容器，不读取业务行，不输出连接字符串、数据库密码或原始异常输出。
- 如果存在 `alembic_version`，只读取最多 10 个版本号作为结构元数据，仍不写版本。
- 不调用 `create_all()`、手写迁移、seed 或行情后台任务。

输出中的 `matches_model_precheck` 只表示本次覆盖的结构项匹配，不表示可以直接 stamp。`differences_found` 表示已成功盘点并发现差异，应先审查差异；这两种完成状态都打印 `SCHEMA_AUDIT_OK_READ_ONLY`。执行错误打印 `SCHEMA_AUDIT_FAILED`。

未检查服务器默认值、字符集/排序规则、CHECK 表达式等价性、触发器、视图和历史数据回填覆盖情况。额外索引单独列为 warning，可能是 MySQL 为外键自动创建的索引，不能一律删除。MySQL 的 BOOL 与 TINYINT(1)、NUMERIC 与 DECIMAL 等常见反射差异做有限归一化，不比较或打印业务值。

## 生产只读预检结果（用户已回传）

`matches_model_precheck`：31 张模型/业务表、339 个字段、37 个外键，`differences=[]`，没有 `alembic_version`，业务行未读取、结构未修改。该结果仍为 `ready_to_stamp=false`，没有执行正式迁移。

23 项额外索引警告中，21 项名称对应模型唯一约束的实现索引，`account_transactions.order_id` 是外键支撑索引，`invitation_codes.used_by_user_id` 是匿名唯一约束的实现索引（也位于外键列）。MySQL 会以唯一索引实现 UNIQUE，并为需要的外键创建索引，参见 [SQLAlchemy MySQL 唯一约束与反射](https://docs.sqlalchemy.org/en/20/dialects/mysql.html#mysql-mariadb-unique-constraints-and-reflection)。因此不删除这些索引；新检查器仅在名称、列、唯一性与约束签名匹配时归类，仍阻止未识别的额外索引接入。

## 本地基线实现

- 固定基线版本为 `20261018_01`。`app/migrations/frozen_v04.py` 保存一次性冻结的 31 表定义，包含 339 字段、37 外键和 14 个 CHECK，不导入实时 ORM；今后模型变化不能重新生成这个已发布历史快照。
- 初始 revision 对空库创建冻结表结构；对已有业务表直接拒绝初始升级，避免部分建表后失败。
- `python -m app.schema status` 只读版本；`check` 进行基线兼容核验；`adopt-baseline` 对通过核验的旧库登记版本；`upgrade` 显式执行版本升级。现阶段 `check` 专指初始基线兼容性，后续增加增量版本时需扩展对应版本的发布检查。
- 原始 `alembic stamp/upgrade` 缺少受控连接标记时被拒绝，防止跳过检查。MySQL 的写入操作使用服务器 advisory lock，避免两个迁移任务并发执行。
- 基线接入增加或写入 `alembic_version`，保留业务表及业务行；不自动删除历史索引、修改字段或回填业务数据。MySQL DDL 不是完整事务，故仍需备份与隔离演练；异常时保留状态供诊断。
- 严格核验另外检查 CHECK 名称、表达式语法树及 MySQL ENFORCED 状态，表和字符串列的 `utf8mb4_0900_ai_ci` 排序规则，以及触发器、视图和服务器默认值。只允许历史兼容默认值 `users.status='ACTIVE'`；未知表达式或默认值直接要求审查。列注释、索引可见性和历史回填完整性不在自动验收范围。
- 生产启动只检查版本，不自动迁移或接入无版本库；开发空库通过同一版本体系初始化，开发旧库同样需要显式核验接入。邀请管理 CLI 不再隐式建表。
- `/readyz` 同时检查数据库连通及迁移版本，版本错误返回不含连接信息的 503；存活检查仍不依赖数据库。
- 旧手写兼容 helper 暂留在 `database.py` 供旧数据研究和既有测试使用，已不被应用启动或邀请 CLI 调用；不是现版本的发布升级入口。

## 单文件隔离演练步骤（已执行通过）

交付文件为桌面 `monipan/monipan-baseline-rehearsal-20261018.py`。构建方式：

```powershell
& .\backend\.venv\Scripts\python.exe scripts/build_schema_rehearsal.py
```

该文件嵌入既有恢复/清理工具、本次候选代码、固定基线，以及 Alembic 1.20.0、Mako 和 MarkupSafe 的 Python 源码及许可证；不携带 SQLAlchemy/PyMySQL，也不携带 Windows 二进制。MarkupSafe 使用官方纯 Python 回退，已实际验证包内依赖可导入及模板可运行。服务器不安装依赖、不拉镜像、不修改运行中的生产 backend 代码。

用户上传并校验后，流程为：

1. 在新的 `backups/migration-drill-20261018-*` 私有目录生成独立备份，原有保留目录不受清理影响。
2. 复用已验证恢复工具，使用现有 MySQL 镜像创建 network=none 的隔离 MySQL，导入新备份。
3. 使用当前 backend 不可变镜像创建临时应用容器，仅共享隔离 MySQL 网络；候选应用及依赖解压到该临时容器自己的文件系统。
4. 严格核验恢复库，登记基线、重跑 `upgrade`，比较接入前后全部 31 张业务表的行数与内容指纹。
5. 仅在同一隔离 MySQL 中创建 `monipan_baseline_fresh` 空库，验证从零升级至基线及完整结构检查。
6. 使用候选版运行应用 API 验证，覆盖注册、登录、账户读取、账务、持仓及 AI 隔离；计数清单只增加预期的一个版本表。
7. 仅在全部通过后清理本次自有临时应用、MySQL 及匿名卷，保留备份和状态；失败则保留恢复 MySQL 供诊断。

成功输出 `BASELINE_REHEARSAL_OK_PRODUCTION_NOT_DEPLOYED`。这只表示隔离演练通过；生产库尚未登记版本，正式新镜像也尚未构建/部署。不能直接据此将阶段 0 的迁移验收勾选完成。

## 真实 MySQL 隔离演练结果

来源为用户回传的服务器执行结果，交付文件 SHA-256 为 `a6583e9778e29c162bf9466dbec64b27e703644f1d93e7f21d9ac4e20c8ebe33`，校验为 `OK`。

- 新备份：`/opt/monipan/backups/migration-drill-20261018-bqvi7n9t/monipan-20261018T082835Z.sql.gz`，gzip 和 SHA-256 验证通过。
- 临时 MySQL：`monipan-restore-drill-2efc25735a4244423e474fd9`，已导入 31 张业务表；四项基础异常计数均为 0。
- 隔离库的基线为 `20261018_01`，`existing_database_adopted=true`。接入前后 31 张业务表的行数和内容指纹一致。
- 23 项额外索引识别为已有约束支撑索引，14 个 CHECK 通过严格核验；同一隔离实例内的新空库升级和结构检查均通过。
- 候选应用通过 9 个恢复账户读取、9 个账户资金对账、持仓数量重放、37 个外键检查、3 条 AI 运行读取及跨用户隔离、合成账号注册登录退出。
- 应用阶段指纹核对为 29 张表：此前的 28 张非运行时业务表，加上新增的 `alembic_version`；原始行没有变化。会话及两个限流表依旧按既有规则排除运行时过期清理。
- `production_schema_modified=false`。最终输出 `RESTORE_APPLICATION_OK`、`TEMPORARY_RESOURCES_CLEANED` 及 `BASELINE_REHEARSAL_OK_PRODUCTION_NOT_DEPLOYED`。
- 隔离导入步骤 16.37 秒；全部自动检查合计 37.47 秒。该计时不含手工准备、上传和正式发布，也不是整机灾难恢复 RTO。
- 状态文件：`/opt/monipan/backups/migration-drill-20261018-bqvi7n9t/restore-run-cbayvq6b/restore-state.json`。保留新备份、状态及私有探针日志，临时容器和匿名卷已清理。

仍未验证真实用户密码登录或浏览器完整回归；未在生产库创建 `alembic_version`，也未替换正式 backend。

## 正式发布准备

`scripts/build_schema_release.py` 将这次通过演练的候选源码和 Python 依赖按原字节打包为 `.docker/monipan-schema-release-20261018.tar.gz`，包含源文件校验清单及许可证。它拒绝使用与上述成功演练 SHA-256 不同的交付件，也拒绝本地候选源码已经变化的情况。

包内 Dockerfile 要求显式传入当前 backend 镜像的本地保留标签，以该镜像为父层，仅加入本次候选后端文件和依赖，不执行联网 pip 解析。包内没有 Compose、真实 `.env`、frontend、宿主机 Nginx 或数据库文件。此方式保留当前已经运行及演练过的底层依赖，完整依赖锁定仍需后续单独落地。

下一步先读取服务器 Git 状态、当前 backend 镜像/Compose 配置文件标签及源码哈希。线上配置存在昨天的手工修复，不能直接用本地工作区整体覆盖或 Git reset。确认差异后再完成：保留旧镜像和配置 → 构建派生候选镜像 → 对该最终镜像再做隔离核验 → 生产备份和受控基线接入 → 仅更新 backend → 健康及公网检查。发布前保留能选择旧 backend 镜像的回滚方式，应用回滚不自动删除版本表。

### 用户回传的正式发布前盘点

服务器为 `master...origin/master`；未提交修改为 `compose.yaml`、`frontend/nginx.conf`、`frontend/security-headers.conf`。当前 backend 镜像 ID 为 `sha256:2bc78105792958fe58f54438a4d892067f28cb9f62d9ee247515d09136a2b2e2`，用户为 `monipan`，Compose 配置文件标签为 `/opt/monipan/compose.yaml`。

运行中 `app/main.py`、`app/models.py`、`app/invites.py` 和 `requirements.txt` 的 SHA-256 均与本地 `797ac75` 旧版本匹配；`PYTHONPATH` 未设置；生产库仍没有 `alembic_version`。这些事实来自用户回传，准备工具执行时还会重新检查。

### 最终镜像准备工具（首版验证失败，已准备权限修正）

`scripts/prepare_schema_release.py` 及打包器 `scripts/build_schema_preparation.py` 生成桌面 `monipan/monipan-schema-prepare-20261018.py`。交付件包含经过校验的镜像上下文、既有隔离恢复工具和诊断探针；SHA-256 为 `4359a8f163b9529b81c804ea99eb6984291a56071d3c994b2473a208873201f0`。

- 执行前核对三个生产容器身份和健康状态、已确认的 backend 镜像/源码及无版本表状态；不导入旧 `app.main`。
- 在新的私有 `backups/schema-release-20261018-*` 下保存当前 Compose、前端配置、`.env` 和涉及的旧后端源码副本及校验值；不覆盖这些原文件。目录权限由 077 umask 限定，副本为 0600。
- 为旧 backend 镜像建立唯一保留标签；以它为父镜像构建唯一标签的候选版。禁止拉取父镜像，构建步骤关闭网络，不运行 pip。检查父镜像层、启动命令等配置保留，仅增加指定的 vendor 导入路径。
- 另建私有备份目录，运行既有备份脚本；用新备份恢复隔离 MySQL。临时应用使用最终候选镜像的不可变 ID，只连接隔离 MySQL 的 network namespace，无生产网络或数据挂载。
- 在候选镜像内核验全部 154 个应用及 vendor 文件的字节校验值和依赖实际导入位置；只写入诊断脚本，不叠加应用源码。随后执行与成功演练相同的基线接入、空库升级及应用/账务检查。
- 成功后清理本次自有临时容器和匿名卷；保留新备份、旧镜像标签、候选镜像、配置副本、构建日志及 `release-state.json`。再次确认三个生产容器 ID、旧源码、相关配置和无版本表状态未改变。
- 失败时不停止或回滚生产服务；已开始的恢复演练失败时沿用原工具的隔离数据库保留机制。诊断日志位于私有目录，不输出原始数据库错误或连接信息。

成功标志为 `CANDIDATE_READY_PRODUCTION_NOT_DEPLOYED`。这一步不登记生产版本、不重启生产 backend，也不同步服务器后端源码。生产接入工具须消费成功状态并重新检查当前运行环境；正式版本登记、后端切换和失败回退尚待随后实施。

### 首版候选镜像失败记录及修正

用户回传：首版交付文件 SHA-256 校验通过，候选镜像 `sha256:eda0ed340117ae73b7bd511a76406dba2691b43a481be07f8d1074904e5bc04a` 构建成功；新备份及隔离导入成功，但应用探针返回 `PermissionError`，没有输出就绪标志，也没有进入生产接入。

- 发布状态：`/opt/monipan/backups/schema-release-20261018-kn9o5neq/release-state.json`。
- 新备份：该目录下 `drill-backup/monipan-20261018T085247Z.sql.gz`，SHA-256 检查通过。
- 隔离状态：`drill-backup/restore-run-wv2ih0o7/restore-state.json`；隔离 MySQL 为 `monipan-restore-drill-2d364248cc30ccc054689663`，失败后保留。导入阶段 16.39 秒，31 张业务表、四项基本异常均为 0。
- 已在准备代码中发现权限缺陷：077 umask 使解压文件为 0600、目录为 0700，解包没有恢复 tar 中的 0644。首版 Dockerfile 把 vendor 以 root 属主复制，应用使用 monipan，因此该依赖路径可能不可读。首版探针只返回错误类型，具体服务器路径须由下述重试诊断确认。
- 修正解包器：仅对经过清单验证的公开源码设置文件 0644、子目录 0755，外围上下文目录和 release 根目录仍为私有；不更改 `.env` 或配置副本的权限。
- 修正镜像：vendor 同样明确由 monipan 持有，复制后对 `/opt/monipan-vendor` 及 `/app/app` 设置读取权限，再切回 monipan。应用及依赖文件内容保持与原成功 MySQL 演练一致，154 项内容校验值不变；Dockerfile 的打包权限步骤发生变化。
- 修正版上下文 SHA-256 为 `3896cd7cb5d61a69b1ea16d662fb8f9e47244f52adecd266e2c6c7e4448db052`。旧上下文另存为 `.docker/monipan-schema-release-20261018-v1.tar.gz`，原交付文件不覆盖。

`scripts/build_schema_preparation.py` 现在生成通用 `monipan-schema-prepare-20261018-v2.py`（SHA-256：`7dc5ed5a587e65d1017e614ffad15676f2b794e5382eaadf84a3711c23c6c40b`）。本次已有恢复库，实际交付专用的 `monipan-schema-retry-20261018.py`，由 `scripts/build_schema_retry.py` 生成，SHA-256 为 `9a05ee82b97bc5e34dd11347ccfe4a0600582c7e24f3169cb6ca36adb7f8b7fe`。

重试工具先核对原发布状态、三个生产容器 ID、配置校验值、旧镜像标签、备份校验以及保留克隆的身份/隔离条件。以镜像原有 monipan 身份在无网络、只读文件系统、无挂载/业务环境的临时容器中输出指定源码路径的读取权限；仅在旧 vendor 不可读被重现后继续重建。

新上下文与候选镜像使用独立目录和标签，旧候选及失败上下文保留。新镜像先验证文件读取，再复用原隔离 MySQL 执行完整候选镜像探针，不重复备份或导入，不修改生产库。全部通过后清理该隔离容器及匿名卷，将原 `release-state.json` 更新为候选已验证状态，记录新 `candidate_context` 和镜像 ID。失败则保留诊断和恢复状态。探针新增安全的阶段及清单内文件名错误定位，不输出原始数据库异常或密码。

本地新增/相关检查共 38 项通过：公开代码权限在 077 umask 下的设置、私有目录范围、镜像权限步骤、诊断容器隔离条件、复用原克隆、过期状态阻止重建、失败保留记录，以及探针错误信息脱敏。Windows 本地测试不能证明 Linux 容器内的实际读取权限；该项由服务器重试中的真实 monipan 身份读取和后续完整 MySQL 验证完成。

## 验证与当前状态

### 最新收尾：重试在诊断条件处停止（2026-10-18）

用户回传旧重试文件 SHA-256 校验通过。真实 monipan 用户 uid=100、gid=101：`/app/app/main.py` 为 0600 且可读；`/opt/monipan-vendor` 为 root 属主、0755 且目录可读；内部 `alembic/__init__.py` 返回 PermissionError、文件不可读。依赖文件读取权限问题已确认，但重试工具错误地以顶层目录不可读作为继续条件，因此在构建前停止，尚未改变发布状态或部署生产。

下次先将 `scripts/retry_schema_release.py` 的条件改为检查指定依赖文件不可读，并补“目录可读、文件不可读”的真实情况回归测试；此前 38 项本地检查未覆盖该情况。重新生成交付文件和 SHA-256 后，再复用保留隔离库完成最终镜像验证。当前旧重试交付件不要直接再次运行，权限修正版镜像仍未重建验收。服务器断点仍是上述 `schema-release-20261018-kn9o5neq` 及其保留克隆。生产基线登记、backend 切换和自动失败回退尚未执行；本轮按用户要求收尾，只更新规划和记录。

桌面阶段规划已更新：`C:/Users/Czh13/Desktop/monipan/MoniPan_01_阶段0_线上生产底座加固.md`，第 9 节记录完整交接点、暂缓事项及下一步。

- [x] 现有启动及邀请 CLI 的自动建表路径已定位。
- [x] 只读工具和服务器单文件交付件已准备。
- [x] 15 项本地测试通过：真实 SQLite 临时库结构读取、类型/缺列/多列/唯一索引差异检测、版本表识别、8 类写操作在执行前被拒绝，以及容器身份和错误输出保护。
- [x] 交付件通过 Python 3.8 语法检查，嵌入读取代码与源码一致，预期模型清单包含 31 张表。
- [x] 生产 MySQL 只读结果已回传，已记录其覆盖范围和 23 项索引警告。
- [x] 本地 Alembic 冻结基线、受控版本命令、生产启动保护及 readiness 版本检查已实现。
- [x] 本地空库升级、已有数据接入与指纹不变性、降级保护、CHECK 语义及应用认证/账务回归已验证。
- [x] 后端完整回归 209 项通过，包含原有 API、账务、恢复及本机 Nginx 行为检查；候选包内依赖的纯 Python 导入和模板执行也已实际验证。SQLite 结果不代替下面的 MySQL 隔离演练。
- [x] 真实 MySQL 隔离基线演练通过，用户已回传完整结果和自动清理成功标记。
- [x] 后端发布上下文已在服务器构建；应用/依赖内容与成功演练一致，权限修正版待服务器重建验证。
- [x] 当前生产镜像、源码与配置差异已由用户回传，运行时后端匹配已演练的旧版本。
- [x] 最终镜像构建和隔离验证的单文件准备工具已生成；本地失败路径及交付件检查已通过。
- [ ] 正式派生镜像的隔离验证首版因权限错误失败，修正版重试、生产库基线接入、部署和冒烟验收待执行。

本地 requirements 已新增 Alembic 版本范围，完整依赖锁定仍待阶段 0 后续工作；本地候选版已改变启动逻辑，真实隔离 MySQL 演练已通过，但线上运行环境、生产库和生产容器均未因此改变，没有在生产库执行 stamp/upgrade。正式发布仍须遵循“备份 → 检查 → 迁移 → 启动 → 冒烟”的流程。
