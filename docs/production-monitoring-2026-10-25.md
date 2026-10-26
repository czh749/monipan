# 阶段 0：个人项目的轻量邮件告警

日期：2026-10-25。**本地实现与上传文件已验证；用户回传服务器只读预检全部通过，最近备份约 16.96 小时前。生产监测安装和 SMTP 收件尚待验收，阶段 0 继续保持进行中。**

当前断点：用户使用 QQ 邮箱，`test-mail` 返回 `SMTPServerDisconnected`；随后脱敏诊断确认 QQ 主机、SSL/465 组合正确，但保存的授权码为 **63 字符**，不符合 QQ 的 16 字符格式，诊断在联网前即停止。下一步只隐藏重填 `smtp.password`，通过 16 字符检查后原子保存，再复测 `test-mail`，保留其他字段；不重复安装或启用 timer。此前断开是否只由此引起仍待复测。见 [预检证据](evidence/production-monitor-server-preflight-2026-10-25.json)、[SMTP 失败证据](evidence/production-monitor-smtp-failure-2026-10-25.json) 和 [配置诊断](evidence/production-monitor-qq-config-diagnostic-2026-10-25.json)。完整 unit 安装结果与真实收件均仍待验收。

2026-10-26 整理记录补充：用户在上述修复步骤后反馈“好了”，记为即时问题已恢复的简短反馈；具体成功步骤、两封测试邮件实际接收及 timer 启用仍缺明确输出，不继续把 63 字符视为已核实的当前配置。历史失败记录保留。见 [用户反馈](evidence/production-monitor-user-feedback-2026-10-26.json)。

用户选择 SMTP 邮件，并要求个人项目无需过于复杂或频繁。采用一个独立 Python 脚本和一组 systemd 单元，使用服务器已有 Python 3.8+ 标准库，无额外 pip 依赖。助手没有直接登录生产服务器；以下操作由用户在腾讯云网页终端执行。

## 默认行为

- 每次检查结束约 5 分钟后再检查，systemd 精度窗口 15 秒；首次启用前会先检查一次。连续两次异常才通知，通常在故障发生后约 5～10 分钟加检查耗时识别。
- 单轮多个异常合并成一封邮件。持续故障每 24 小时提醒一次；连续两次正常后发一封恢复邮件。恢复后再次发生的故障按新事件处理。
- SMTP 单次操作超时 10 秒；失败不即时循环重发，下次符合退避时间才重试，间隔约 5、10、20、40、60 分钟，随后最多每小时一次。重试状态持久化，不把失败记成送达。
- 安装时不启用 timer；先交互保存 SMTP，再用合成事件发送两封测试邮件，实际看到两封邮件后才启用。
- 计划维护可临时静默，最长一天，到期自动恢复通知；探测仍继续。默认不静默。

| 检查 | 判定 |
| --- | --- |
| 生产容器 | Compose 项目 `monipan` 的 backend/frontend/mysql 各有一个普通容器，正在运行且 healthy；不创建或重启容器 |
| 宿主机入口 | `nginx.service` 为 active |
| 应用、数据库和迁移版本 | 请求本机 `http://127.0.0.1:8080/readyz`，要求 200、`status=ok`、`database=ok`；禁用环境代理和重定向，不保存响应原文 |
| 磁盘 | 根目录、项目、每日备份和 Docker 实际数据目录所在文件系统；使用率达到 85% 或剩余不足 2 GiB |
| 自动备份调度 | 既有 `monipan-backup.timer` 为 loaded、enabled、active |
| 备份任务 | 正在运行时不把上次失败结果当作当前失败；结束后要求 Result=success、ExecMainStatus=0，成功的 inactive oneshot 正常 |
| 备份时效 | 每日目录存在非空正式备份及格式正确的 SHA-256 sidecar，以文件名 UTC 时间判断，最近完整文件对不超过 26 小时；运行中也不无限豁免过期 |

每轮只检查备份元数据和 sidecar 格式，不重新计算大文件哈希、解压、导入或触发备份；这不替代已有完整性和隔离恢复验收，也不能发现压缩内容损坏。未成对发布的新文件暂不计作成功备份。

本轮不涵盖请求级 5xx/异常堆栈、接口延迟统计、两次采样之间的短暂失败或容器重启、行情陈旧、抓取失败、AI 失败率和成本。公网链路和整机宕机/断网需要外部探测；当前只检查宿主 Nginx 状态与本机容器链路。监测脚本自身报错、状态文件损坏或进程被终止，会留在 systemd/journal，不能承诺依靠同一脚本再发邮件。证书告警、异地备份继续按此前决定暂缓。

## 文件与权限

| 路径 | 用途 |
| --- | --- |
| `/usr/local/lib/monipan-monitor/monitor.py` | 独立监测脚本；目录 0700、文件 0600，root 所有 |
| `/etc/monipan-monitor/smtp.json` | SMTP 和阈值；目录 0700、文件 0600，root 所有，不进入 Git |
| `/var/lib/monipan-monitor/` | 去重、恢复、重试状态与测试回执；目录 0700，文件 0600 |
| `/etc/systemd/system/monipan-monitor.service`、`.timer` | 新增的监测单元，0644 |

安装只新增以上资源。预检只读访问原项目、备份、Docker 状态和既有 systemd 状态；不展开 Compose 或打印环境变量。邮件只包含白名单检查名、枚举或数值，不附异常原文、密码或用户业务数据。外部异常日志只输出类型，不输出 SMTP 服务端响应；工具自身保留固定的脱敏诊断说明。禁止符号链接路径；已有同名资源即停止，避免覆盖未知配置。

收发邮箱和授权码只在服务器交互配置。SMTP 使用证书校验开启的 SSL 或 STARTTLS，不降级明文；在邮箱提供方开启 SMTP 后，使用其提供的主机、端口和授权码。账号需为完整发信邮箱；收件人限一个邮箱，默认同发件人。

## 网页终端操作

桌面上传文件：`C:/Users/Czh13/Desktop/monipan/monipan-monitor-20261025.py`。

- 字节数：23571。
- SHA-256：`2cf33bd53ef6e60e4aa884bcb191f4c31ce27d660d936bd999d457a83a68d618`。
- 实现源文件：`scripts/monitor_production.py`；构建器 `scripts/build_monitor.py` 将两个 unit 原文嵌入单文件。

### 1. 上传后先只读预检

通过腾讯云文件管理上传到 `/home/ubuntu/`。若实际位置不同，仅替换命令中的上传路径。先进入 root：

```sh
sudo -i
```

随后执行：

```sh
printf '%s\n' '2cf33bd53ef6e60e4aa884bcb191f4c31ce27d660d936bd999d457a83a68d618  /home/ubuntu/monipan-monitor-20261025.py' | sha256sum -c - &&
python3 -u /home/ubuntu/monipan-monitor-20261025.py preflight
```

预期校验 `OK`，`PREFLIGHT_READ_ONLY` 中各项 `ok` 都为 true，进程退出 0。任一项异常先回传这一段输出，不要改生产阈值来掩盖真实问题。首次安装会再读一次当前状态；不会把 10 月 25 日以前的成功结果当作当前在线证据。

### 2. 安装监测文件，定时器仍关闭

```sh
printf '%s\n' '2cf33bd53ef6e60e4aa884bcb191f4c31ce27d660d936bd999d457a83a68d618  /home/ubuntu/monipan-monitor-20261025.py' | sha256sum -c - &&
python3 -u /home/ubuntu/monipan-monitor-20261025.py install
```

预期 `MONITOR_INSTALLED_TIMER_DISABLED`。安装前做 unit 语法校验；不会运行原备份安装程序、原发布程序或 `compose up/down`。中途出错保留已创建的监测文件供诊断，不覆盖后反复安装。

### 3. 在服务器填写 SMTP

```sh
python3 -u /usr/local/lib/monipan-monitor/monitor.py configure
```

依次输入邮箱提供方的 SMTP 主机、TLS 模式（默认 ssl）、端口（SSL 默认 465，STARTTLS 默认 587）、完整发信邮箱、收信邮箱、授权码。授权码输入隐藏；不要把配置文件或授权码回传聊天。

预期 `SMTP_CONFIGURED_PRIVATE`。已存在配置会拒绝覆盖；需要修正时在服务器执行 `sudoedit /etc/monipan-monitor/smtp.json`，保持文件 0600，不要打印其内容。阈值也在此文件 `policy` 中，默认值与上表一致。

### 4. 发两封合成测试邮件

```sh
python3 -u /usr/local/lib/monipan-monitor/monitor.py test-mail
```

使用正式状态判断和 SMTP 发送逻辑，但使用独立的内存测试状态，不触碰生产去重状态，不停数据库、应用或 Nginx。应收到两封带 `[测试 TEST]` 的邮件：故障、恢复。

`TEST_TWO_MESSAGES_SMTP_ACCEPTED_VERIFY_INBOX` 只表示 SMTP 已接受两封邮件；垃圾箱、退信或收件侧过滤仍要实际检查。任何失败都停止后续启用，回传脱敏日志即可；配置正确后可重新测试。

### 5. 实际收到两封后启用

```sh
python3 -u /usr/local/lib/monipan-monitor/monitor.py enable
```

24 小时内测试通过且配置未改变时才能继续；再次只读预检后，程序要求输入 `RECEIVED`，用于记录实际收件确认。先执行一次真实监测任务，成功后才启用新 timer。预期 `MONITOR_TIMER_ENABLED`，检查间隔 5 分钟。

回传以下白名单结果，并说明是否实际收到故障、恢复两封测试邮件；不要回传 SMTP 配置：

```sh
systemctl show monipan-monitor.timer --no-pager -p ActiveState -p UnitFileState
systemctl list-timers --all --no-pager monipan-monitor.timer
systemctl show monipan-monitor.service --no-pager -p Result -p ExecMainStatus -p ExecMainExitTimestamp
journalctl -u monipan-monitor.service --no-pager -n 12 -o cat
```

安装、测试送达和恢复证据通过后，先标记“基础监测已上线”，后续由 timer 实际触发的记录确认持续运行；不把整项监控或阶段 0 勾为完成。

## 日常操作和撤销

```sh
# 正常维护：静默 30 分钟，到期恢复；0 表示现在恢复通知。
python3 /usr/local/lib/monipan-monitor/monitor.py mute --minutes 30
python3 /usr/local/lib/monipan-monitor/monitor.py mute --minutes 0

# 查看脱敏运行状态和最近一次采样时间。
python3 /usr/local/lib/monipan-monitor/monitor.py status

# 暂停未来监测，仅操作本轮新增单元；保留状态、配置及原有每日备份。
systemctl disable --now monipan-monitor.timer
```

暂停 timer 不会中断已经执行中的 oneshot，最多等待该任务自行结束（上限 120 秒）。恢复使用 `enable`，若测试回执已超过 24 小时须先重新 `test-mail`。不用删除文件来撤销；同名路径拒绝覆盖是防护措施，中途失败先检查 journal 和监测文件，再决定修复。

SMTP 成功后进程崩溃、状态无法落盘，或者网络在服务端已收件但客户端未收到确认时，仍可能重复发送；标准 SMTP 无法保证恰好一次送达。状态损坏时停止并记录错误，不静默重建状态。不要定期清空去重文件。

## 本地验证和验收边界

78 项检查通过，分别运行源码与实际单文件交付件。覆盖阈值、短暂抖动、合并通知、每日去重、恢复、恢复重试期间故障复发、持久化、损坏状态拒绝重置、维护静默、备份进行中/过期/不完整文件对、磁盘阈值、容器字段白名单、readiness 内容、SMTP TLS、异常脱敏、安装拒绝覆盖和失败停止、配置改变后拒绝启用、首次服务失败不启用 timer。

```powershell
& D:/monipan/backend/.venv/Scripts/python.exe D:/monipan/scripts/build_monitor.py
& D:/monipan/backend/.venv/Scripts/python.exe -m pytest D:/monipan/backend/tests/test_production_monitor.py -q -p no:cacheprovider --basetemp=D:/monipan/.docker/pytest-monitor-delivery-20261025
```

使用真实临时文件和受控的 Docker/systemd/网络/SMTP 替身。Windows 无法验证 Linux root 权限位和 systemd 行为，相关权限断言由替身隔离；Python 3.8 验证为语法兼容检查。未在本机启动 Docker、WSL 或生产服务；测试通过不代表真实 SMTP 已送达。

实现参考：[systemd timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)、[systemd service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)、[Python SMTP](https://docs.python.org/3/library/smtplib.html)、[Python TLS](https://docs.python.org/3/library/ssl.html)。最终以服务器现有版本的 unit 校验和实际回传为准。
