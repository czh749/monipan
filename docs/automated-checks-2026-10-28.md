# 本地一键检查与最小 CI

日期：2026-10-28。目标：复用现有测试，把日常验证自动化；数据库结构和并发改动增加 MySQL 专项。检查通过不等于生产发布获准。

## 日常只需一个入口

在 `D:/monipan` 运行：

```powershell
.\backend\.venv\Scripts\python.exe scripts/check_project.py
```

Linux 或激活虚拟环境后使用 `python scripts/check_project.py`。顺序运行后端回归、`pnpm test`、`pnpm build`，后者包含类型检查。各组独立记录结果，任何失败都返回非零退出码；不自动重试掩盖失败。

首次安装或修改依赖后准备环境：

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
cd frontend
pnpm install --frozen-lockfile
```

Python 使用 3.13；前端部署和 CI 使用 Node.js 22，pnpm 固定为 `11.7.0`。本次也在本机 Node.js 24 验证，Node.js 22 的 Linux CI 结果须在远程首次运行后确认。检查不会自行更新项目依赖或启动生产服务。

单独运行：

```text
python scripts/check_project.py --scope backend
python scripts/check_project.py --scope frontend
python scripts/check_project.py --scope mysql
python scripts/check_project.py --with-mysql
```

最后一条在完整日常检查之外加入 MySQL。默认不运行 MySQL，报告明确写 `not_run`；显式要求 MySQL 时，缺少地址、测试跳过或零测试通过都会失败，不能回退到 SQLite 冒充成功。

## MySQL 的隔离要求

项目部署已使用 MySQL。本项验证本次结构从 `20261018_01` 升级到 `20261026_02`、旧业务行保留、30/31 配额竞争、多账户同股去重及研究股票交易限制。

- 本地需自行准备独立空库，用私有环境变量 `MONIPAN_STAGE1_MYSQL_URL` 指定，格式为 `mysql+pymysql://<测试用户>:<测试密码>@127.0.0.1:<端口>/monipan_stage1_test_<编号>`；不要使用生产凭据。
- 只接受回环地址和上述库名前缀；数据库中已有表或视图则拒绝执行。不会自动删除已有库或表。每次重跑需新的空测试库。
- GitHub Actions 自动提供一次性的 MySQL 8.4 服务、测试库和只供该任务使用的固定测试密码。任务结束由运行平台回收服务，无需用户为 CI 配置数据库密钥。
- 日常后端回归主动排除该专项文件，避免误用继承的测试库地址；其余回归使用本次检查的临时 SQLite 环境。

## CI 如何运行

配置文件：`.github/workflows/checks.yml`。

| 检查 | 内容 | 失败条件 |
| --- | --- | --- |
| Backend regression | 安装固定 Python 依赖、Nginx，运行后端完整回归；检查 Compose CLI 存在 | 依赖锁未更新、实际环境版本不匹配、测试失败、超时、Nginx/Compose 等意外跳过 |
| Frontend regression and build | 按 pnpm 锁安装，执行前端测试和含类型检查的构建 | 安装与锁不匹配、测试、类型检查或构建失败 |
| MySQL migration and concurrency | 独立 MySQL 8.4，运行真实迁移、数据保留、并发和交易限制测试 | 缺少安全测试地址、连接/迁移/断言失败、零通过或任何跳过 |

代码 push、pull request 和手动触发会运行；纯 Markdown 或 `docs/` 改动跳过自动触发。三个任务并行，重复提交会取消同一分支正在运行的旧检查，避免无效排队。CI 权限仅 `contents: read`，各任务设超时，外部 Action 固定到已核对的提交，不触发生产发布。

历史精确发布包不入 Git。CI 允许这类明确的缺档跳过，同时继续运行对应源代码回归；该例外不是历史发布包已验收。发布前仍可在已有归档环境使用 `MONIPAN_REQUIRE_RELEASE_ARTIFACTS=1` 要求其存在。

每组结果的 `summary.json`、JUnit XML 保留为 CI artifact 七天，不上传 pytest 临时库或整份工作目录。仓库分支保护、必须通过后才能合并等规则本次未修改。

## 固定依赖与更新方式

- `backend/requirements.txt` 保留直接依赖范围；`backend/requirements.lock` 固定传递依赖，并包含平台条件。CI 和普通后端 Docker 构建安装同一 lock。
- 检查入口核对源 requirements 的摘要和当前 Python 环境的锁定版本；修改源文件而不更新 lock，或用另一套包运行，会明确失败。摘要兼容 Windows/Unix 换行。
- 前端使用已有 `pnpm-lock.yaml`、冻结安装及 `packageManager` 固定 pnpm；本轮没有升级前端业务依赖。
- lock 固定包版本，不等于完整的操作系统/镜像内容冻结；真实生产发布仍需准备自己的不可变候选。

仅在主动修改依赖时，在专用工具虚拟环境安装 `uv==0.13.6` 后运行：

```text
python scripts/lock_backend.py
```

脚本以 Python 3.13、跨平台条件编译依赖，并更新输入摘要；已有锁定版本默认保留。需要有意升级某个包时，例如 `python scripts/lock_backend.py --upgrade-package fastapi`。重新安装 lock 并运行检查后再提交，常规检查不会自动升级包。`uv pip compile` 的行为参考 [uv 官方说明](https://docs.astral.sh/uv/pip/compile/)。

## 本次结果与剩余边界

结果见 [本地验证记录](evidence/project-checks-local-2026-10-28.json)：

| 本次实际执行 | 结果 |
| --- | --- |
| 全新 Python 虚拟环境按 lock 安装 | 成功，`pip check` 通过；没有复用原虚拟环境的额外安装。 |
| 修复后的干净仓库副本：完整后端回归，包含真实 Nginx | **368 通过、34 跳过**；34 项全部是未入 Git 的历史精确发布包缺档，无意外跳过。 |
| 修复后的目录/并发及检查入口定向回归 | **20 通过**。 |
| 前端 | **17 通过**；类型检查、构建通过，冻结安装未改锁。 |
| 真实 MySQL 8.4.13 / REPEATABLE READ | **4 个全新空库运行均通过**；每次一个包含迁移、旧数据保留和并发断言的综合测试，无跳过。 |
| 显式请求 MySQL 却不配置地址 | 正确失败，退出码 1，没有回退到 SQLite。 |
| 工作流 | YAML 结构和外部 Action 提交已核对；可选 actionlint 下载不完整，未执行该工具；远程 CI 尚未运行。 |

真实 MySQL 首次运行揭示了现有实现的并发缺陷：同股创建中的死锁会清掉嵌套保存点，随后回滚保存点的 1305 错误遮住 1213，使外层重试失效。已在 `services/research_watchlist.py` 使用 MySQL 原子创建，冲突只保留原股票行，不覆盖已有行情；死锁仍按整笔事务有界重试。真实用例保留了原来触发错误的场景，没有弱化断言。

初次检查时 Docker 尚不可用，MySQL 检查使用已核对官方校验值的临时 Windows 运行时，位于 `.docker/mysql-runtime-20261028/`；每次仅绑定回环地址、使用独立数据目录和随机端口，结束后均已关闭，没有安装 Windows 服务。运行时、隔离数据和原始报告作为本地验证材料保留在 `.docker/`，不入 Git；不需要它们才能在 GitHub CI 的临时容器运行专项。

GitHub Actions 配置已在本地编写，未推送或触发远程运行。用户随后启动 Docker，本轮已继续完成当前前后端 Dockerfile 构建、Linux/MySQL 专项、Node.js 22 前端测试及隔离站点功能验证，见 [Docker 验证记录](stage1-docker-verification-2026-10-28.md)。这补齐了本地容器和 Node.js 22 的证据，仍不等于 GitHub CI 已远程运行或已部署生产。

2026-10-30 更新：远程 CI 已运行通过，且已补齐兼容镜像回退演练、受限真实取数、首次失败及单股陈旧提示；完整结果和仍待正式服务器执行的门槛见 [最新发布准备记录](stage1-release-readiness-2026-10-30.md)。自动检查不会证明邮件实际送达或生产网络容量。

服务容器的创建与回收方式参考 [GitHub 官方说明](https://docs.github.com/en/actions/tutorials/use-containerized-services/use-docker-service-containers)。
