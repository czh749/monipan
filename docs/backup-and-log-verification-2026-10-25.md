# 连续备份及生产日志轮转验收：2026-10-25

当前状态：**连续自动备份和三个生产容器的日志轮转配置均已验收通过。** 依据为用户于 2026-10-25 回传的腾讯云终端输出，助手未直接登录服务器。

## 已收到的结果

- `monipan-backup.timer` 为 enabled、active；最近触发时间为北京时间 2026-10-25 03:00:01，回传时下一次计划为 2026-10-26 03:00:00。
- journal 显示 2026-10-19 至 2026-10-25 连续 7 天在北京时间 03:00 触发，均成功完成；最近服务结果为 `Result=success`、`ExecMainStatus=0`。
- 每日目录中现存 8 份备份均通过 `gzip -t` 和 SHA-256 校验，包括首次手工触发的备份及后续 7 份定时备份。gzip 成功不输出文本，每条 SHA-256 的 `OK` 均来自 `gzip -t ... && sha256sum -c ...`。
- `backend`、`frontend`、`mysql` 的实际容器配置均为 `driver=json-file max-size=10m max-file=5`，与源码预期一致，无需为此重建容器。

原始输出保存在 [终端证据](evidence/backup-and-log-verification-2026-10-25.txt)，提取结果及原文 SHA-256 见 [结构化证据](evidence/backup-and-log-verification-2026-10-25.json)。

本次只验证文件完整性和已有调度/容器配置，未逐一导入这 8 份备份；真实隔离导入与应用核验另见 [先前恢复演练](backup-restore-2026-10-18.md)。尚未实测超过保留阈值后的文件清理，也未实测日志达到大小阈值时的滚动过程。服务器同盘备份不覆盖整机或云盘故障，备份失败通知仍待建设。

## 本次已执行的只读命令

本次只读，不重新部署、不重启服务、不手动触发备份或清理文件，不显示 `.env` 或完整容器环境变量。

以下命令已由用户在 root 终端执行并回传，无需重复执行：

```sh
systemctl show monipan-backup.timer --no-pager -p ActiveState -p UnitFileState -p LastTriggerUSec
systemctl list-timers --all --no-pager monipan-backup.timer
systemctl show monipan-backup.service --no-pager -p Result -p ExecMainStatus -p ExecMainExitTimestamp
journalctl -u monipan-backup.service --since '2026-10-19 00:00:00' --no-pager -n 100 -o short-iso

(
  cd /opt/monipan/backups/daily || exit 1
  for backup in monipan-*.sql.gz; do
    [ -f "$backup" ] || continue
    gzip -t "$backup" && sha256sum -c "$backup.sha256"
  done
)

(
  cd /opt/monipan || exit 1
  for service in backend frontend mysql; do
    container_id=$(docker compose --project-name monipan --file compose.yaml ps -q "$service")
    [ -n "$container_id" ] && docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}} driver={{.HostConfig.LogConfig.Type}} max-size={{index .HostConfig.LogConfig.Config "max-size"}} max-file={{index .HostConfig.LogConfig.Config "max-file"}}' "$container_id"
  done
)
```

验收依据：

- timer 应为 enabled、active，有下一次计划；oneshot service 成功后 inactive 属于正常状态。
- 对照 timer、journal 和每日目录文件，至少记录两个不同日期的定时触发成功结果；文件存在本身不足以证明是自动调度生成。
- 每个实际存在的每日备份应通过 gzip 与 SHA-256，sidecar 应指向对应文件。若目录为空，本段循环不会生成成功证据，不能据此标记备份通过。
- 文件名使用 UTC；北京时间每日 03:00 对应前一日 19:00 UTC，判断日期时结合 journal 时间偏移。
- Docker inspect 只输出服务名、日志驱动及 `max-size`、`max-file` 两个轮转参数。源码期望 `json-file`、`max-size=10m`、`max-file=5`；若实际缺少轮转参数，先分析，不直接重建 MySQL 或覆盖生产配置。
- 本日尚未达到 14 天清理阈值，不能用当前文件列表宣称旧备份清理行为已经实测。

证书告警与加密异地副本继续保持用户暂缓。本次不是自动监控，也不会创建定时聊天任务。
