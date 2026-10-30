# 阶段 0 本地 Git 整理：2026-10-26

本次只整理本地源码和提交，不推送远端，不登录服务器，不重新发布、安装备份或改动生产数据。

整理前分支为 `master`，HEAD 为 `797ac75`。当前工作在新分支 `codex/stage0-git-20261026`；原 master 保留。本轮原有 83 个待整理文件已逐一复制并记录 SHA-256，快照位于被 Git 忽略的 `.docker/git-organization-20261026-tvgrizbd/`，另存原始差异，未使用 reset、clean 或 stash 清理工作区。

## 提交分组

1. 生产代理信任、HTTPS 协议传递、CSP、Compose 配置、Nginx 回归及本地私有配置忽略规则。
2. Alembic 冻结基线、结构只读核验、启动/readiness 版本检查及相关应用回归。
3. 每日备份 systemd 单元、受保护安装器和隔离恢复/应用核验工具。
4. 固定基线的候选准备、权限重试、分阶段发布与受控回退工具，及历史生产回传记录。
5. 独立低频 SMTP 监测、状态去重/恢复/退避和对应单文件构建、测试及验收边界。
6. README、生产运行手册、交接和本次 Git 整理记录。

已记录的五组代码提交：

| 提交 | 范围 |
| --- | --- |
| `78fcc22` | 生产安全配置与代理回归 |
| `8a5b696` | 数据库版本管理、冻结基线与启动校验 |
| `59d9306` | 每日备份与隔离恢复 |
| `f365ebe` | 受保护的候选准备、发布和回退工具 |
| `b7fe7ef` | 低频邮件监测与测试 |

第六组提交保存本文、README、运行手册和交接，以及原始证据行尾保护；其提交身份通过该分支的最新日志核对，避免在文件内自引用自身哈希。

真实 `.env`、`.env.production`、本地覆盖文件、SMTP 私有配置、数据库、SQL 备份、`.docker` 下的上传包/vendor/临时快照、虚拟环境和 node_modules 不进入提交；保留 `.env.example` 和 `.env.production.example` 模板。扫描命中的固定注册测试密码和隔离恢复随机密码前缀均为合成测试数据，未发现待提交文件包含真实凭据；这是本次受限扫描与人工核对结果，不承诺历史仓库绝无密钥。

原始终端证据包含 CRLF 行尾和表格填充空格，按原始字节及既有 SHA-256 保留；`.gitattributes` 只对 `docs/evidence/*.txt` 关闭文本行尾转换和空白错误检查，其他源码仍检查空白错误。工作区原文没有修剪；提交时显式保留原始字节，防止 Git 的默认行尾规范化使证据哈希失效。

本轮 Git 历史记录了已经部署的迁移代码，也包含尚待最终验收的监测代码；本地提交不代表服务器切换到该 Git HEAD。已验证生产后端镜像仍以 [正式发布记录](schema-production-release-2026-10-25.md) 为准，基线为 `20261018_01`。后续功能发布仍须单独检查结构、备份和应用兼容性。

## 验证

- 当前工作区后端：**361 项通过，20 项因缺少指定 Nginx 测试程序而跳过**，使用完整历史交付件严格模式；跳过项不代表生产验收通过。
- 前端：**12 项回归通过**，类型检查和生产构建通过；未自动执行 pnpm install 或升级依赖。
- 从将提交的 167 个文件导出无 `.docker`、`.venv`、`.env`、node_modules 的干净目录后，后端 **327 项通过、54 项明确跳过**；其中 20 项缺 Nginx 测试程序、34 项缺历史审核交付件。监测源码及临时生成的单文件交付件共 78 项均执行通过。运行复用了原有 Python 依赖环境，没有联网安装依赖，故不构成依赖可重复安装或 Linux MySQL 集成验收。
- 本轮不运行真实 MySQL 恢复、Nginx 生产切换或 SMTP 发送；后端 API 使用独立临时 SQLite 和受控网络替身。

## 普通测试与历史发布包测试

迁移发布工具针对 2026-10-18/25 的特定镜像、清单和交付件绑定，不是下一版的通用发布器。精确历史发布包包含已核验的依赖副本，继续保留在 Git 外；不要为了通过测试重新生成并替换这些历史文件。

普通源码测试不要求拷贝这些归档件。涉及精确交付件的测试通过 `backend/tests/artifact_support.py` 显式检查；缺失时只跳过对应历史交付件测试，继续执行源码回归，并在 pytest 输出中保留原因。监测单文件不依赖历史归档，在测试临时目录内用当前构建器自动生成，保持源码和交付件两套场景都运行。

在仓库根目录运行普通测试：

```powershell
& ./backend/.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider
```

检查历史发布包时，须先恢复对应且已校验的 `.docker` 归档件，再开启严格模式；此时缺失任何所需交付件会失败，不能记为跳过：

```powershell
$env:MONIPAN_REQUIRE_RELEASE_ARTIFACTS = '1'
try {
    & ./backend/.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider
} finally {
    Remove-Item Env:MONIPAN_REQUIRE_RELEASE_ARTIFACTS -ErrorAction SilentlyContinue
}
```

开发用 Nginx 二进制、Docker Compose CLI 等条件不具备时，现有条件测试仍会明确跳过。完整 MySQL、Linux 权限、实际 systemd 调度、真实回退及邮件送达需要独立验收；本轮 Git 整理不补造这些结果。

## 告警反馈的记录边界

用户在重填 QQ 授权码及再次测试的步骤后反馈“好了”，目前可记录问题已获简短恢复反馈，但不能据此推断两封测试邮件都已实际收到或 timer 已启用。旧的 63 字符诊断和 SMTP 断开记录保留为历史证据，最新不确定性见 [用户反馈记录](evidence/production-monitor-user-feedback-2026-10-26.json)。下一步仍需收集真实收件与定时运行状态，阶段 0 继续保持进行中。
