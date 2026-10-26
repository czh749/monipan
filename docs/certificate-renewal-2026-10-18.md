# 证书续期核验：2026-10-18

## 当前公网证书

2026-10-18 14:40:15（北京时间）直接连接 `monipan.top:443`，启用系统信任链和主机名校验，检查通过，协商 TLS 1.3。

- 签发机构：Let's Encrypt YE2。
- 生效时间：2026-08-20 02:42:39 UTC。
- 到期时间：2026-11-18 10:42:38（北京时间）。
- 检查时剩余约 30.83 天。
- 证书 SHA-256：`78a7c0a2801e98c89e005f3d4df789468a33c32e2aaf9f78d335c4292454ad29`。

该检查只证明当前公网证书有效，不能证明自动续期、证书加载或失败告警已经完成。

## 用户截图中的服务器检查结果

证据来源：用户提供的腾讯云终端截图，助手未直接登录服务器。

- 已有 `snap.certbot.renew.timer`，截图中最近触发为 2026-10-18 09:49:00 CST，下次触发为当日 18:59:00 CST；无需重复创建任务。
- 实际服务为 `snap.certbot.renew.service`，`LoadState=loaded`，最近执行 `Result=success`、`ExecMainStatus=0`，结束于当日 09:49:02 CST。
- `certbot.service` 显示 `LoadState=not-found`；在当前 Snap 安装方式下，不能据此判断续期任务缺失，也不能将该不存在单元的默认 Result 当成运行证据。
- 目标续期配置显示 `authenticator = nginx`、`installer = nginx`。
- 实际 Snap 服务的 `OnFailure` 为空，仅说明该单元没有配置 systemd 失败处理单元；是否有独立云监控通知仍未确认。
- 截图未显示 renewal-hooks 目录中的文件条目；配置文件或全局配置中的 hooks 尚未完整检查。

定时任务成功退出可能表示本轮检查没有需要续期的证书，单凭该状态不能认定完成过一次续期。

## 续期演练结果

用户随后在服务器执行目标证书的 `certbot renew --cert-name monipan.top --dry-run`，并回传完整结果：

- 正确处理 `/etc/letsencrypt/renewal/monipan.top.conf`。
- 输出 `Congratulations, all simulated renewals succeeded`。
- `/etc/letsencrypt/live/monipan.top/fullchain.pem` 显示 `(success)`。
- 演练前后的 `nginx -t` 均通过。

判定：目标证书的续期模拟通过，现有自动调度、nginx 验证/安装插件均已有相应证据；无需新增第二套续期任务。该结果不能替代未来真实生产证书续期和新证书加载的验证。

2026-10-18 14:44:56（北京时间），助手独立复核公网：TLS 1.3 证书校验通过，证书仍为原到期时间；首页返回 200，HSTS 为 `max-age=31536000`，强制 CSP 存在且没有 Report-Only 头。当前正式证书未因演练被替换，已验证的安全头仍在生效。

## 告警渠道决定

用户先前选择腾讯云短信／邮件通知，随后明确本轮暂不需要告警。因此本轮证书续期失败及临期告警暂缓，不继续要求配置接收人、通知模板或触发规则；此前的渠道偏好仅作为以后恢复此项时的参考。告警暂缓不等于告警已验收。

本次讨论的告警针对 HTTPS 证书临期和续期失败；域名注册有效期独立于 HTTPS 证书有效期。域名到期时间本轮未检查，不能由证书到期时间推断。

腾讯云控制台资源、采集器和通知模板的当前状态尚未读取，也未新购或启用收费产品，未发送测试通知。不能假设服务器上由 Let's Encrypt 签发的证书已经成为腾讯云 SSL 证书实例，或者实例到期提醒会自动追踪每次 Certbot 续期后的公网证书。

## 服务器侧待核实

1. Certbot 的确切版本；目标证书名称已由演练输出确认为 `monipan.top`，自动调度和 nginx 插件已有截图证据。
2. 配置文件或全局配置中的 pre/post/deploy hooks。
3. 后续真实生产证书续期与新证书安装加载；续期模拟现已通过。
4. 证书续期失败及临期告警按用户要求暂缓；接收人验证、信号接入及通知测试未完成。

本机 SSH 认证未成功，服务器状态及演练结果由用户执行后提供。本轮未新增定时任务或发送通知；自动调度与续期演练已通过，失败及临期告警暂缓。

Certbot 官方建议先检查已有 [自动续期任务](https://eff-certbot.readthedocs.io/en/stable/using.html#automated-renewals)。[续期演练](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)可能执行已有 pre/post hooks 并触发临时 Nginx 配置及 reload，需要先确认现有认证方式和 hooks。
