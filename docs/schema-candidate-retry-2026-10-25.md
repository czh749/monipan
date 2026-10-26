# 最终候选镜像重试：2026-10-25

本页记录的最终候选镜像隔离验证已通过；**后续生产基线登记与 backend 正式发布也于本日通过**，最新状态见 [生产发布记录](schema-production-release-2026-10-25.md)。下文保留候选验收当时的证据与范围，不将隔离结果混作生产结果。

## 已收到的服务器结果

- SHA-256 校验通过；root 实际执行路径为 `/home/ubuntu/monipan-schema-retry-20261025.py`。
- 真实 monipan 用户 uid=100、gid=101 验证修正版源码和依赖可读；文件为 0644、vendor 目录为 0755，属主均为 monipan。
- 最终镜像：`sha256:89a426a296110d25c620a68caae2253c4b8ef71551f8ea5696ee3718a92fcd46`。
- 154 项镜像文件校验通过，无源码覆盖；真实 MySQL 隔离库 31 张业务表基线接入前后指纹不变，空库升级及 14 项 CHECK 核验通过。
- 9 个账户读取及资金对账、37 项外键检查、持仓重放、3 条 AI 记录权限隔离、合成账号注册/登录/退出通过；未测试原用户密码或浏览器 UI。
- 候选验收的应用核验耗时 37.01 秒；临时容器与匿名卷已由工具清理；当时生产容器未变、生产库未登记基线，后续正式发布另见最新记录。
- 已收到 `CANDIDATE_READY_PRODUCTION_NOT_DEPLOYED`。结构化证据见 [服务器回传记录](evidence/schema-candidate-2026-10-25.json)。

**以下上传及重试步骤为已执行的历史操作，不重复执行。** 原隔离库已清理，后续生产接入前盘点、新备份及正式发布也已完成。

## 已完成的本地工作

- 重试条件以 `/opt/monipan-vendor/alembic/__init__.py` 的实际 `PermissionError` 为依据，覆盖“目录可读、文件不可读”的真实情况。
- 修正版镜像必须同时通过 vendor 目录、该依赖文件及 `/app/app/main.py` 的可读性检查，才会进入隔离应用验证。
- 关键成功和失败场景同时测试源码及生成的单文件交付件，避免源码已修复而上传包仍使用旧逻辑。
- 重新执行 `scripts/build_schema_retry.py` 成功；发布上下文仍为 `3896cd7cb5d61a69b1ea16d662fb8f9e47244f52adecd266e2c6c7e4448db052`，应用及依赖内容与此前通过真实 MySQL 演练的版本一致。
- 相关检查 **50 项通过**：镜像准备、重试和隔离应用恢复。测试使用受控命令替身及真实临时文件，不代表 Linux 镜像已验证。

本地验证命令：

```powershell
& .\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_schema_release_preparation.py backend/tests/test_schema_release_retry.py backend/tests/test_restore_application.py -q -p no:cacheprovider --basetemp=.docker/pytest-schema-handoff-20261025
```

## 本次交付文件

- 桌面文件：`C:/Users/Czh13/Desktop/monipan/monipan-schema-retry-20261025.py`
- 字节数：`503854`
- SHA-256：`8009fc49c962dacc5351e1e559bae70feb08f0b3f13b5d8a3cfc3d9b5aa6b4c9`
- 配套校验文件：`monipan-schema-retry-20261025.py.sha256`，可用于同目录校验。

新日期只是交付文件名；数据库基线仍为 `20261018_01`。旧桌面文件 `monipan-schema-retry-20261018.py` 保留作历史记录，旧 SHA-256 `9a05ee82b97bc5e34dd11347ccfe4a0600582c7e24f3169cb6ca36adb7f8b7fe` 对应缺陷版本，不执行。

## 腾讯云网页终端操作

1. 把新的 `monipan-schema-retry-20261025.py` 上传到服务器 `/home/ubuntu/`，切换到 root 终端。
2. 在现有生产服务器的 root 终端粘贴以下完整命令。SHA-256 校验失败时不会执行 Python。

```sh
printf '%s\n' '8009fc49c962dacc5351e1e559bae70feb08f0b3f13b5d8a3cfc3d9b5aa6b4c9  /home/ubuntu/monipan-schema-retry-20261025.py' | sha256sum -c - &&
python3 -u /home/ubuntu/monipan-schema-retry-20261025.py
```

3. 回传终端输出，尤其是 `FAILED_IMAGE_PERMISSIONS`、`FIXED_IMAGE_PERMISSIONS`、最终成功或失败标记，以及 `CANDIDATE_IMAGE` 和 `RELEASE_STATE`。脚本不会输出 `.env` 或数据库连接串。

## 操作边界及成功判定

工具先核对原发布状态、三个生产容器身份、配置指纹、旧镜像标签、备份校验及保留隔离 MySQL。2026-10-18 的断点不保证仍有效，任何不匹配都会停止，不通过修改状态文件绕过检查。

核对通过后，构建一个新的候选镜像，复用原隔离库执行最终镜像的 154 项源码/依赖文件校验、数据库基线和应用/账务检查；成功后仅清理本次工具确认所有权的临时资源。失败时保留诊断记录。

此步骤会创建候选镜像并更新发布诊断状态，会在隔离库内执行验证；不会给生产库登记版本，也不会停止或替换生产 backend、frontend、MySQL，不覆盖线上 Compose、Nginx 和 `.env`。

全部成功时应同时看到：

```text
FINAL_IMAGE_REHEARSAL_OK
TEMPORARY_RESOURCES_CLEANED
PRODUCTION_CONTAINERS_UNCHANGED
PRODUCTION_SCHEMA_NOT_ADOPTED
CANDIDATE_IMAGE=sha256:...
RELEASE_STATE=/opt/monipan/backups/schema-release-20261018-kn9o5neq/release-state.json
CANDIDATE_READY_PRODUCTION_NOT_DEPLOYED
```

只有镜像构建成功或权限检查成功，都不能视为整项通过。若输出 `SCHEMA_RELEASE_RETRY_FAILED`，先回传错误，不删除保留克隆、不改状态、不改用首版准备脚本重跑。

本次候选成功之后，生产只读盘点、prepare 及正式 deploy 均已执行通过，详见 [生产发布记录](schema-production-release-2026-10-25.md)。候选隔离检查和正式生产发布仍作为不同验收步骤记录，实际失败回退与浏览器完整流程另行验收。
