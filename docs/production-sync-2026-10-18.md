# 2026-10-18 线上修复同步到源码

## 同步依据

2026-10-17 保存的 `monipan-apply-https-fix.py`、`monipan-enable-csp.py`、`monipan-add-hsts.py`、`monipan-extend-hsts.py` 和 `monipan-final-public-check-2026-10-17.json`。原文件位于当日任务的 `nginx-secure-cookie-csp-report-only-2/outputs/`，本次读取原文件核对，不以早期 Report-Only / 300 秒 HSTS 快照作为最终状态。

已确认线上链路：浏览器 → 宿主机 Nginx（TLS）→ Docker frontend Nginx → backend。项目 `/opt/monipan`，Compose 项目名 `monipan`，项目网段 `172.18.0.0/16`，宿主机入口 `172.18.0.1`。这些地址来自当日检查，不是其他部署可直接套用的默认值。

## 源码对应关系

| 已验证的线上修复 | 本地保存位置 |
|---|---|
| 仅可信入口可提供原始协议；其他请求使用当前连接协议 | `frontend/nginx.conf` 的协议映射 |
| 只信任宿主机入口的 `X-Real-IP`，关闭递归处理 | `deploy/nginx/proxy-trust.production.conf` |
| 向后端覆盖真实 IP，清除外部传入的地址链 | `frontend/nginx.conf` 的四组 API 代理设置 |
| production、Secure Cookie、Uvicorn 可信网段 | `compose.production.yaml`、`.env.production.example` |
| 内部 Nginx 与安全头文件只读挂载 | `compose.yaml`；生产覆盖文件替换可信配置的挂载来源 |
| 强制 CSP，保持原策略内容 | `frontend/security-headers.conf` |
| 宿主机一年 HSTS 和外层转发设置 | `deploy/nginx/host-hsts.conf`、`deploy/nginx/host-proxy.conf` |

为保持本地 HTTP 开发可用，新增一个不信任任何入口转发头的本地默认文件 `frontend/proxy-trust.conf`，生产通过覆盖文件加载已确认的可信入口。与昨日脚本相比，仅将环境相关的信任配置拆出，协议校验、地址覆盖、CSP 内容和 HSTS 有效期沿用原修复。未改数据库、数据卷和业务模型，也未更改本地真实 `.env`。

宿主机片段只归档已经确认的相关指令，不是服务器完整站点配置的导出，不包含证书私钥、未知站点设置或任何自动安装步骤。

## 发布边界

本次任务只同步本地。线上已修改的受版本控制文件需要先保存快照，再合并此次源码配置，不能直接丢弃服务器改动后拉取。生产 `.env` 合并 `COMPOSE_FILE` 后，普通 Compose 命令和备份脚本才会持续选用生产覆盖文件。具体操作与回滚要求见 `production-hardening.md`。

证书自动续期、自动/异地加密备份、真实恢复、正式迁移、告警和 CI 不因本次源码同步变为已完成。两个公网客户端隔离、其他限流规则、完整登录与下单预览的强制 CSP 回归仍须单独验收。

## 本地验证

- 对照昨日修复脚本与最终公网响应：CSP 策略全文相同；将可信配置拆出后，API 请求处理指令与昨日修复一致；HSTS 为一年。
- 真实 Compose CLI 检查基础/生产配置合并、缺少可信来源时拒绝配置、通过环境文件自动加载覆盖文件；核对数据库服务、连接串、数据卷、网络和发布端口不变。
- 使用官方 Nginx 1.27.5 Windows 二进制运行隔离 HTTP 测试：四类 API 转发、可信/非可信来源、非法协议回退，以及 200/401/404/429 安全响应头，共 20 项。测试将生产网关映射到测试回环地址，只验证配置行为，不验证生产 Docker 网络。
- 后端全套测试（包含上述配置及 Nginx 行为测试）142 项全部通过；前端回归 12 项、类型检查及生产构建通过；`git diff --check` 通过。

Nginx 行为测试位于 `backend/tests/test_nginx_proxy.py`。设置 `MONIPAN_NGINX_BINARY` 为本地 Nginx 可执行文件路径后可运行；未提供且 PATH 中没有 Nginx 时明确跳过。本次未启动 Docker 引擎，因此没有进行 Docker 镜像构建和整栈启动，也没有重新访问或发布线上服务。

## 配置参考

信任来源依据原始连接地址判断，使用 Nginx 的 [realip 模块](https://nginx.org/en/docs/http/ngx_http_realip_module.html)及 [geo 模块](https://nginx.org/en/docs/http/ngx_http_geo_module.html)。生产挂载按目标路径替换本地挂载，遵循 [Compose 文件合并规则](https://docs.docker.com/reference/compose-file/merge/)，并通过真实 Compose CLI 检查合并结果。
