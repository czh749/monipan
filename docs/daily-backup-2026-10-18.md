# 每日备份调度：2026-10-18

## 2026-10-25 接续验收（已通过）

用户回传 timer、service、journal 和备份校验结果：2026-10-19 至 2026-10-25 连续 7 天在北京时间 03:00 自动触发并成功完成；timer 保持 enabled、active，最近服务退出码为 0，回传时下一次计划为 2026-10-26 03:00。现存 8 份备份（首次手工触发加 7 次定时备份）均通过 gzip 与 SHA-256 校验。

连续运行验收通过，证据见 [连续备份及日志轮转验收](backup-and-log-verification-2026-10-25.md)。本次未逐一导入这 8 份备份，也未实测超过保留阈值后的清理；加密独立副本继续保持用户暂缓。以下安装过程和首次运行结果为 2026-10-18 的历史记录。

## 已收到的服务器证据

用户从腾讯云服务器终端回传截图，助手未直接登录服务器：

- `timedatectl show -p Timezone --value` 返回 `Asia/Shanghai`。
- 过滤 `monipan|backup` 的 systemd 定时器列表仅显示 `dpkg-db-backup.timer`。它属于操作系统的软件包数据库备份，不能视为 MoniPan MySQL 备份。
- `/etc/cron*` 和 `/var/spool/cron/crontabs` 中检索 `backup_mysql.sh|monipan.*backup` 没有返回文件路径。

这表示在本次名称和内容匹配范围内，尚未发现 MoniPan 的 systemd/cron 调度。不能据此排除使用其他命名的任务或腾讯云外部调度。

## 准备的配置

源码文件：

- `deploy/systemd/monipan-backup.service`
- `deploy/systemd/monipan-backup.timer`
- `scripts/install_backup_timer.py`

时间采用显式 `Asia/Shanghai`，每日 03:00，触发精度窗口 1 分钟。`Persistent=true` 用于在定时器重新激活时补跑关机期间错过的任务，不补造过去日期的数据；首次备份失败也不会据此自动重试。systemd 会记录服务退出结果和 journal 日志，不发送外部通知。参见 [systemd.timer 官方文档](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html) 和 [systemd.time 时间表达式](https://www.freedesktop.org/software/systemd/man/latest/systemd.time.html)。

调度以现有项目名 `monipan` 和目录 `/opt/monipan` 调用已验证的 `scripts/backup_mysql.sh`，不修改 Compose、生产环境文件或现有备份脚本，不重启应用或数据库。输出目录独立设为 `/opt/monipan/backups/daily`，目录 `0700`、文件 `0600`；保留阈值设为 14 天。现有脚本使用 `find -mtime +14`，按完整天数判断，文件达到 15 天时才会匹配删除；不能称为严格只保留 14 个文件。清理不递归，不涉及原备份根目录和演练目录。

服务单次超时为 30 分钟，成功退出后回到 inactive 属于正常 oneshot 行为。定时器应保持 enabled、active。不配置 `RemainAfterExit=yes`，避免后续定时触发被一直活跃的服务抑制。

## 安装与验收流程

安装脚本仅供既有 Linux 生产服务器上的 root 执行。它拒绝覆盖任何已有同名 unit，拒绝符号链接形式的项目、备份脚本或备份目录。首先检查原有备份脚本语法、Compose 配置和现有 MySQL 运行状态，再由服务器 `systemd-analyze verify` 校验 unit。

之后安装两个新 unit，使用新 service 立即运行一次备份，确认服务 `Result=success`、`ExecMainStatus=0`，并独立复核新文件时间、SHA-256、gzip 完整性及私有文件权限。只有这些检查通过，才执行 `enable --now` 启用 timer 并确认 enabled/active。

若安装中途失败，已创建的 unit、备份和 journal 留供诊断；不自动清除它们，也不覆盖后重试。首次备份失败时不会执行启用 timer 的步骤。启用步骤本身失败时应核实真实状态，不能仅凭没有成功标记断言 timer 未启用。

为避免网页终端长文本粘贴错误，生成单文件交付件 `.docker/monipan-daily-backup-20261018.py` 并复制到桌面 `monipan` 文件夹。交付件嵌入与仓库相同的两个 unit，不需要先发布源码或安装额外依赖。上传到服务器 `/root/` 后，按交付时提供的 SHA-256 校验成功再运行。文件上传使用已登录目标服务器的 [腾讯云遨驰终端文件管理](https://cloud.tencent.com/document/product/1665/84580/)。

成功应出现：

```text
FIRST_BACKUP_STARTING
DAILY_BACKUP_INSTALLED
FIRST_BACKUP_VERIFIED=/opt/monipan/backups/daily/monipan-...sql.gz
SCHEDULE=03:00 Asia/Shanghai; retention threshold=14 days
```

并列出 `monipan-backup.timer` 的下一次触发时间。这些只是首次执行和调度启用证据；至少还要收集后续两个不同日期、由定时器触发的成功记录，才能记录“已观察到连续自动运行成功”。本轮不创建 Codex 自动化，后续验收通过用户回传或另行明确授权的检查进行。

查看结果（服务成功后显示 inactive 并不表示失败）：

```sh
systemctl show monipan-backup.service -p Result -p ExecMainStatus -p ExecMainExitTimestamp
systemctl list-timers --all --no-pager monipan-backup.timer
find /opt/monipan/backups/daily -maxdepth 1 -type f -name 'monipan-*.sql.gz' -printf '%TY-%Tm-%Td %TH:%TM %f\n'
```

暂停未来备份仅需 `systemctl disable --now monipan-backup.timer`，不会删除备份，也不会停止已经在运行的 service。正在执行的备份让其正常结束。无需改动生产 Compose。

## 服务器安装与首次执行结果（已通过）

用户回传安装交付件 SHA-256 校验为 `OK`，随后输出 `FIRST_BACKUP_STARTING`、`DAILY_BACKUP_INSTALLED` 和 `FIRST_BACKUP_VERIFIED`。

- 首次备份：`/opt/monipan/backups/daily/monipan-20261018T074648Z.sql.gz`。
- 文件名中的 UTC 时间对应北京时间 2026-10-18 15:46:48；这是备份命名时间，不是服务完成耗时。
- 安装程序确认首次备份新文件、gzip、SHA-256 和私有权限检查通过，并确认 timer 为 enabled、active。
- 用户回传的下一次触发时间：2026-10-19 03:00:00 CST；激活服务为 `monipan-backup.service`。
- 定时器列表的 LAST/PASSED 为 `-`，与首次运行由安装程序手动触发一致，不能将其算作已经观察到的定时运行。

截至 2026-10-18，本项状态为“已安装并启用，每日调度首次试跑通过”，当时连续自动运行仍待后续记录。2026-10-25 已补齐连续运行证据，见本文顶部；无需再次运行安装程序。

## 独立副本存储方向

用户先选择暂缓异地副本，随后询问可否使用自己的 Windows 主机保存，最后明确决定本轮跳过独立副本、不购入存储桶，继续下一项修复。该电脑可以作为与服务器云盘独立的副本位置，但该方案本轮也不实施；尚未下载文件、配置同步任务或完成加密验收。以下仅保留为未来可选方案。

可先通过腾讯云终端文件管理，将同一时间戳的 `.sql.gz` 及 `.sql.gz.sha256` 下载到本机独立备份目录，核对 SHA-256 后作加密归档。建议目录与 Git 工作区分开，例如 `D:\MoniPanBackups`。已有备份和校验文件必须成对保留；gzip 本身不提供加密，SHA-256 校验不等于加密。7z 格式支持 AES-256，见 [7-Zip 官方格式说明](https://7-zip.org/7z.html)。加密密码应由用户在本机管理，恢复验收应包含解密、校验和导入。

若后续选择自动下载，需要另行验证 SSH 认证和主机身份、设置 Windows 任务计划，并确认关机错过后的补拉策略。个人电脑关机不影响服务器继续备份，但电脑副本只能恢复到最后一次成功下载的时点；不能仅凭服务器每日任务宣称本机副本也满足 24 小时 RPO。当前没有创建本机定时任务；后续工作已转入数据库迁移前的只读结构盘点。

## 当前状态与限制

- [x] 已收到时区、定时器及 cron 检索结果。
- [x] 配置、安装脚本和上传文件已准备。
- [x] 8 项本地安装流程保护测试及 7 项既有运维检查通过，共 15 项；现有备份脚本在 Git Bash 下通过语法检查。交付件通过 Python 3.8 语法兼容检查，嵌入 unit 与仓库内容一致。
- [x] 服务器安装、systemd 校验、首次备份验证及调度启用通过，用户已回传成功结果。
- [x] 已观察到 2026-10-19 至 2026-10-25 连续 7 天定时运行成功，现存 8 份备份 gzip 与 SHA-256 校验通过。
- [ ] 超过保留阈值后的文件清理尚未实测。
- [ ] 加密独立副本及取回、解密恢复验收：用户明确本轮暂缓，未完成，不影响已启用的服务器本地备份。

本地测试使用受控 systemd/Docker 命令替身，实际文件内容、SHA-256 和 gzip 校验使用真实临时文件；Windows 测试仅模拟这些文件的 Unix 权限位，不能代替 Linux 权限验收。没有在本机启动 WSL、Docker 或 systemd 服务。本轮仅配置本机云盘备份，不宣称云盘故障下可恢复，也不宣称 RPO 已持续达标。
