# 从 GitHub 获取阶段 1 验证包

代码分支：`codex/stage1-research-20261027`。

预发布页面：<https://github.com/czh749/monipan/releases/tag/stage1-verify-20261030>。

候选包包含已测试的前后端、兼容回退镜像及隔离副本检查工具。业务镜像源码对应 `ae813233507b1d003a26222fbf48f79b29d712d8`，副本检查工具已提交到同一开发分支；不包含运行时密钥或数据库备份。这是验证候选，不表示生产已部署。

服务器下载：

```sh
curl --connect-timeout 10 --max-time 900 --retry 2 -fL \
  https://github.com/czh749/monipan/releases/download/stage1-verify-20261030/monipan-stage1-server-20261030.zip \
  -o /home/ubuntu/monipan-stage1-server-20261030.zip
printf '%s  %s\n' \
  f482eaeffec1362ff6fa7b2819435a9183ff0691364b3551c5c301b211a563cc \
  /home/ubuntu/monipan-stage1-server-20261030.zip | sha256sum -c -
```

只有校验为 OK 才继续。解压到新的候选目录后，再执行包内的 `sha256sum -c SHA256SUMS`，最后使用 `docker load -i stage1-images.tar.gz` 导入镜像。导入镜像本身不替换运行中的容器。

`check_stage1_restore.py` 需要显式指定已创建的隔离恢复状态文件及包内 `manifest.json`；它拒绝生产容器、共享数据卷、不匹配的镜像或不正确的恢复状态。原表原列的值全部比对，随后检查新版本和兼容回退版本的就绪、账户和股票 API。账户 API 使用测试进程内认证替换，不代表真实密码登录或生产 TLS 已验收。

主站源码同步、正式迁移及服务替换在核对服务器现有配置和发布计划后进行。
