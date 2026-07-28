# 模拟盘（MoniPan）

一个面向 A 股学习场景的模拟交易 MVP，采用 Vue 3、FastAPI 和 SQLite。行情按固定股票池分批获取东方财富沪深京 A 股快照，交易和资金仍为模拟。推荐通过 Docker Compose 部署。

## 已有功能

- 固定的 200 只 A 股股票池
- 交易时段每 5 分钟覆盖更新一次真实行情
- 100 万元初始模拟资金
- 市价买入、卖出与即时成交
- 账户资产、持仓、委托和成交记录
- 前端展示行情来源、最近更新时间、覆盖率与新鲜度状态
- 大盘脉搏模块展示上证、深证、创业板、沪深300和科创50
- SQLite WAL 模式

> 行情来自第三方公开数据接口，不保证无延迟或持续可用；成交和资金均为模拟数据，不构成投资建议，也不连接真实券商。

## 行情更新机制

- 应用启动时获取一次 200 只股票的行情快照。
- 当前 200 只股票合并为一次全池请求，实测可返回 199 只有效行情，避免 40 次小请求触发连续断开。
- 每轮优先请求股票池；成功后冷却 60 秒再更新大盘指数，避免指数请求抢占本轮首个可用连接。
- 股票池与大盘指数都支持主节点失败后切换备用节点。
- 请求顺序仍按数据库更新时间从旧到新排列；如果未来调小批次，优先补齐最陈旧行情。
- 主节点失败后进入延迟队列，冷却 15 秒后切换东方财富备用节点重试，并在状态接口中明确记录节点切换。
- 连续 3 个批次失败时自动暂停；单轮最多使用 240 秒预算，避免采集任务无限拖延。
- 状态接口和日志记录批次总数、成功率、重试数、恢复数、暂停次数与整轮耗时。
- 行情只覆盖股票表中的最新值，不创建不断增长的快照记录。
- 尚未取得真实行情的股票使用内部待行情占位值，不在股票列表展示，也不能下单。
- 工作日 09:15～11:30、13:00～15:00 按 5 分钟间隔刷新。
- 某批次请求失败或股票停牌无有效价格时，该批股票保留最后一笔有效行情；其他成功批次继续更新。
- 整轮请求失败时自动退避到 2～5 分钟，避免持续触发数据源限流。
- 非交易时段不重复下载；应用启动时的一次快照可取得最近收盘行情。
- 数据新鲜度随刷新周期动态计算：正常阈值为刷新间隔的 2.5 倍，陈旧阈值为刷新间隔的 5 倍。当前 300 秒刷新时，750 秒内为正常、750～1500 秒为延迟、超过 1500 秒为陈旧。
- 午间休市、收盘后和周末展示“休市快照”，不会仅因距离当前时间较久而误报为陈旧行情。

可在 `compose.yaml` 中调整行情设置：

```yaml
environment:
      MONIPAN_MARKET_INTERVAL: "300"
```

## Docker 一键部署

在项目根目录执行：

```powershell
docker compose up -d --build
```

启动完成后访问：

- 模拟盘页面：http://localhost:8080
- API 文档：http://localhost:8080/docs
- 健康检查：http://localhost:8080/health
- 行情状态：http://localhost:8080/api/market/status
- 大盘指数：http://localhost:8080/api/market/indices

查看服务状态和日志：

```powershell
docker compose ps
docker compose logs -f
```

停止服务，但保留 SQLite 数据：

```powershell
docker compose down
```

彻底清理容器、网络和 SQLite 数据卷：

```powershell
docker compose down -v
```

`down -v` 会永久删除模拟账户、持仓和成交记录。如需保留数据，请只使用 `docker compose down`。

## 容器结构

```text
浏览器 :8080
    ↓
Nginx + Vue 3
    ↓ /api
FastAPI :8000（仅容器网络可见）
    ↓
SQLite 命名卷 monipan-data
```

SQLite 数据保存在 Docker 命名卷中，因此重新构建镜像或删除容器不会影响交易数据。

## 不使用 Docker 的本地开发

启动后端：

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

启动前端：

```powershell
cd frontend
pnpm install
pnpm dev
```

本地开发页面：http://127.0.0.1:5173

## 测试

```powershell
cd backend
python -m pytest
```
