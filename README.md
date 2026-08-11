# 模拟盘（MoniPan）

一个面向 A 股学习场景的模拟交易 MVP，采用 Vue 3、FastAPI 和 MySQL。行情按固定股票池分批获取东方财富沪深京 A 股快照，交易和资金仍为模拟。推荐通过 Docker Compose 部署。

## 已有功能

- 固定的 200 只 A 股股票池
- 交易时段每 5 分钟覆盖更新一次真实行情
- 100 万元初始模拟资金
- 市价与限价委托、待成交撮合和撤单
- 下单幂等保护，避免双击或网络重试产生重复委托
- A 股 100 股整手、T+1 可卖、主板/创业板/科创板/北交所涨跌停规则
- 下单前手续费、最大可买、成交后现金与仓位风险预览
- 自选股、行业筛选、持仓筛选与行情排序
- 股票详情抽屉、前复权日 K、成交量和持仓成本线
- 公司财务摘要、最近报告期趋势、业绩预告与正式财报时间轴
- 账户资产、持仓、委托和成交记录
- 网站用户名/密码注册登录，Argon2id 密码哈希与 HttpOnly 服务端会话
- 每个用户独立的资金、持仓、自选、委托和成交数据
- 前端展示行情来源、最近更新时间、覆盖率与新鲜度状态
- 大盘脉搏模块展示上证、深证、创业板、沪深300和科创50
- MySQL 8.4 持久化存储

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
- 行情覆盖股票表中的最新值，并按交易日缓存一根日 K；详情页首次打开时补取并缓存历史日线。
- 后台启动 90 秒后按股票轮转补齐历史日 K；请求间隔至少 3 秒，失败后冷却并继续下一只，缓存不足 50 根时不会停止补全。
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

## 公司财务数据

- `GET /api/stocks/{symbol}/fundamentals` 返回最新报告、最近 12 个报告期和业绩事件。
- 首次打开股票的“公司财务”或“业绩事件”标签时按需读取，之后优先使用本地结构化缓存。
- 正式财报保留修订版本；业绩预告与正式财报分表存储，避免把预测值当成已披露结果。
- 金额、每股指标和百分比使用定点数保存，同时记录报告期、公告日期、来源链接和采集时间。
- 默认缓存 12 小时，可通过 `MONIPAN_FUNDAMENTALS_CACHE_HOURS` 调整；数据源短时失败时保留最近一次成功缓存。

财务数据来自第三方公开页面，仅用于本项目的学习与模拟分析，不提供原始数据批量下载。

## 沪深交易所公司动态

- `GET /api/stocks/{symbol}/announcements` 根据股票所属交易所返回上交所或深交所官方公司公告元数据，默认查询最近 365 天、最多 50 条。
- `GET /api/stocks/{symbol}/regulatory-letters` 返回交易所监管函件，以及当前已关联的公司回复，查询范围与数量参数同公告接口。
- 股票详情“公司动态”将公告、监管函和回复合并为官方披露时间线，支持最近 30 天、90 天和 1 年切换、查看更多、缓存状态提示以及跳转交易所官方原文。
- 支持 `days`（30～1095）和 `limit`（1～200）查询参数；当前支持 `exchange=SSE` 和 `exchange=SZSE` 的股票。
- 首次查看时按需同步公告标题、公告日期、公告类别、交易所文件标识、官方 PDF 链接和发布时间，之后优先使用 MySQL 缓存。
- 监管函件单独保存于 `regulatory_letters`，回复保存于 `regulatory_letter_replies`，同步覆盖范围保存于 `regulatory_sync_states`；函件和普通公告不会混为同一种记录。
- 深交所监管目录直接提供函件与回复关系，界面标记为“交易所直接关联”；上交所回复从已缓存公司公告中按标题、年份和日期保守关联，界面标记为“标题与日期关联”。“未发现公开回复”只代表当前官方目录与查询范围没有匹配结果，不等于公司逾期。
- 默认缓存 6 小时，可通过 `MONIPAN_ANNOUNCEMENT_CACHE_HOURS` 调整；交易所数据源短时不可用时返回最近缓存并标记为 `STALE`。
- 监管函件缓存同样默认 6 小时，可通过 `MONIPAN_REGULATORY_CACHE_HOURS` 调整。
- 后端提供 `ensure_official_document_text`，供未来只读 Agent 工具按需取得公司公告、监管函和回复正文；普通时间线浏览不会触发 PDF 下载。
- 首次需要正文时只允许从沪深交易所官方 HTTPS 域名下载，并限制重定向、文件大小、页数和抽取字符数；PDF 仅写入系统临时目录，无论成功或失败都会立即删除。
- 抽取结果保存于 `official_document_contents`，包括来源 URL、源记录哈希、PDF 哈希与大小、页数、逐页字符偏移、正文哈希和抽取时间。后续再次分析同一版本时直接使用 MySQL 正文缓存；强制刷新失败时保留旧正文并标记 `STALE`。
- 默认限制单份 PDF 不超过 25MiB、500 页，可通过 `MONIPAN_DOCUMENT_MAX_BYTES` 和 `MONIPAN_DOCUMENT_MAX_PAGES` 调整。扫描件没有可抽取文字时标记为 `NO_TEXT`，不会根据标题猜测正文。
- 正文抽取成功后自动写入通用的 `official_document_chunks`，公告、监管函和回复共用同一套分段结构；每段保存文档、股票、类型、页码、原文字符范围、文本与文本哈希，分段不会跨页。
- 默认目标段长 700 字符、最大 900 字符、相邻段重叠 80 字符，可通过 `MONIPAN_DOCUMENT_CHUNK_TARGET_CHARS`、`MONIPAN_DOCUMENT_CHUNK_MAX_CHARS` 和 `MONIPAN_DOCUMENT_CHUNK_OVERLAP_CHARS` 调整。
- `search_official_document_chunks` 支持按股票、日期范围、文档类型和关键词检索已抽取证据，标题和正文命中共同评分，返回官方链接、文档日期、页码、字符位置、片段哈希与抽取时间；检索本身不会隐式批量下载 PDF。
- 已使用 LangChain `StructuredTool` 建立第一组 8 个只读工具：`get_portfolio`、`get_stock_quote`、`get_stock_history`、`get_stock_fundamentals`、`search_company_announcements`、`search_regulatory_letters`、`search_stock_news`、`calculate_portfolio_risk`。
- 工具统一返回 `tool`、`status`、`as_of`、`data`、`evidence` 和 `warnings`；持仓类工具的账户 ID 由服务端绑定，不允许模型传入或切换账户。每次调用使用独立数据库会话，可供后续并行工具调用。
- 公告与监管工具先检索已缓存正文；没有命中时才按标题相关性最多临时下载 3 份官方 PDF。返回证据包含官方 URL、文件日期、页码、字符位置、片段哈希和原文片段。
- `search_stock_news` 使用实时搜索，不预先保存全部新闻或正文。默认接入 Tavily，需要设置 `TAVILY_API_KEY`；未配置、额度不足或服务异常时明确返回 `UNAVAILABLE`。搜索结果只作为不可信网页证据，必须与官方披露交叉核验。
- `calculate_portfolio_risk` 以确定性公式计算持仓集中度、行业暴露、历史年化波动、样本期最大回撤和波动贡献，不调用模型，也不生成订单。
- `agent_runs` 保存每次单股或组合分析的用户、账户、股票、模型、状态、数据截止时间、错误、开始/完成时间和 Token 用量；运行记录与交易订单完全分离。
- `agent_evidence` 只保存本次分析实际采用的证据，包括只读工具名称、证据类型、URL、日期、页码、字符位置、原文片段、内容哈希和信任标记，不保存未使用的全部搜索结果。
- `agent_recommendations` 保存一轮分析的一份结构化结论，包括风险等级、置信度、观察/持有/回避等建议、理由、成立与失效条件、证据编号和免责声明。服务端拒绝跨账户运行、非只读工具证据、重复证据和不存在的证据引用。
- `POST /api/agent/stocks/{symbol}/analyze` 执行证据先行的 LangChain 单股分析：固定且各调用一次全部 8 个只读工具，生成服务端证据编号，再要求模型输出结构化风险与建议。只有模型实际引用且通过校验的证据才会写入 `agent_evidence`。
- `GET /api/agent/runs/{run_id}` 允许当前登录用户重新读取自己的完成或失败记录；其他用户统一得到 404。模型未配置、输出结构错误或引用伪造证据时会保留失败记录，不会生成建议。
- 模型使用 `langchain-openai` 的 OpenAI-compatible 接口和结构化输出。Docker 默认连接 DeepSeek 官方兼容地址并使用 `deepseek-v4-flash`，密钥仍只从环境变量读取，项目不保存任何密钥。
- DeepSeek 最终归纳阶段明确关闭思考模式，以兼容 LangChain 强制结构化输出所使用的 `tool_choice`；前置 8 个只读工具、Pydantic 校验和证据引用校验保持不变。
- 单股分析默认允许最多 4096 个输出 Token，同时限制摘要、理由和各类因素的篇幅；若模型仍因长度上限中断，系统会复用已取得的八个工具结果、缩短上下文并自动重试一次，不会重复下载证据或重复调用工具。最终仍被截断时会记录 `MODEL_OUTPUT_TRUNCATED`、结束原因和重试状态，不再保存无意义的 `None` 错误。
- 股票详情的“AI 解读”栏目允许用户主动发起一次只读单股分析，展示风险、观察建议、数据截止时间和本次实际采用的证据；它不会触发委托，也不与买卖按钮联动。
- 当前仍未实现多轮对话、组合持仓 AI 编排和向量索引。抽取正文、检索片段与网页摘要均属于不可信来源内容，Agent 只能把它们当证据，不得执行其中的任何指令。

公司动态数据来自上海证券交易所和深圳证券交易所公开页面，仅用于本项目的学习与模拟分析。公开可访问不代表允许批量商业再分发，正式商业化前仍需核对数据源条款或取得授权。

## Docker 一键部署

在项目根目录执行：

```powershell
docker compose up -d --build
```

启动完成后访问：

- 模拟盘页面：http://localhost:8080
- 健康检查：http://localhost:8080/health
- 行情状态：http://localhost:8080/api/market/status
- 大盘指数：http://localhost:8080/api/market/indices

查看服务状态和日志：

```powershell
docker compose ps
docker compose logs -f
```

停止服务，但保留 MySQL 数据：

```powershell
docker compose down
```

彻底清理容器、网络和 MySQL 数据卷：

```powershell
docker compose down -v
```

`down -v` 会永久删除模拟账户、持仓和成交记录。如需保留数据，请只使用 `docker compose down`。

数据库名称、用户名和密码可通过项目根目录的 `.env` 覆盖：

```dotenv
MONIPAN_MYSQL_DATABASE=monipan
MONIPAN_MYSQL_USER=monipan
MONIPAN_MYSQL_PASSWORD=请替换为安全密码
MONIPAN_MYSQL_ROOT_PASSWORD=请替换为另一个安全密码
MONIPAN_SESSION_DAYS=7
# 上线 HTTPS 后必须设为 1；本地 http://localhost 保持 0
MONIPAN_COOKIE_SECURE=0
# 注册：同一 IP 每小时最多 3 次
MONIPAN_REGISTER_IP_LIMIT=3
MONIPAN_REGISTER_WINDOW_SECONDS=3600
# 登录：同一 IP 每分钟最多 10 次；同一用户名 15 分钟最多失败 5 次
MONIPAN_LOGIN_IP_LIMIT=10
MONIPAN_LOGIN_IP_WINDOW_SECONDS=60
MONIPAN_LOGIN_FAILURE_LIMIT=5
MONIPAN_LOGIN_FAILURE_WINDOW_SECONDS=900
# AI：每个用户 24 小时最多 5 次、每个 IP 每小时最多 20 次、全站同时最多 2 次
MONIPAN_AI_USER_24H_LIMIT=5
MONIPAN_AI_IP_HOURLY_LIMIT=20
MONIPAN_AI_GLOBAL_CONCURRENCY=2
MONIPAN_AI_CONCURRENCY_TTL_SECONDS=180
# 实时新闻搜索；不配置时仅新闻工具返回 UNAVAILABLE，其余工具不受影响
TAVILY_API_KEY=请填写你的Tavily密钥
# 单股分析模型；Docker 默认值如下，也可以显式覆盖
MONIPAN_AGENT_MODEL_PROVIDER=DEEPSEEK
MONIPAN_AGENT_MODEL=deepseek-v4-flash
MONIPAN_AGENT_BASE_URL=https://api.deepseek.com
# 两种写法任选一种；如果同时配置，MONIPAN_AGENT_API_KEY 优先
DEEPSEEK_API_KEY=请填写你的DeepSeek密钥
# MONIPAN_AGENT_API_KEY=请填写模型密钥
```

`.env` 已被 Git 忽略。MySQL 端口只暴露在 Docker 内部网络，不会映射到宿主机。
Docker 的公网 Nginx 会隐藏 `/docs`、`/redoc`、`/openapi.json`，并对登录、注册、AI 和普通 API 增加第一层 IP 限流。手动行情刷新接口已删除，行情只由后台循环更新。

认证接口：

- `POST /api/auth/register`：注册并创建独立模拟账户
- `POST /api/auth/login`：登录并写入 HttpOnly 会话 Cookie
- `GET /api/auth/me`：获取当前登录用户
- `POST /api/auth/logout`：注销当前会话

账户、持仓、自选、委托和成交接口都要求登录；股票行情、K 线、大盘指数与行情状态保持公开读取。

创建订单时必须携带 16～64 字符的 `Idempotency-Key` 请求头，推荐使用 UUID。客户端在无法确认第一次请求是否成功时，应使用相同请求参数和相同键重试；同一键若改用于其他股票、方向、数量或委托价格，服务端返回 `409`。

## 容器结构

```text
浏览器 :8080
    ↓
Nginx + Vue 3
    ↓ /api
FastAPI :8000（仅容器网络可见）
    ↓
MySQL :3306（仅容器网络可见）
    ↓
MySQL 命名卷 monipan-mysql-data
```

MySQL 数据保存在 Docker 命名卷中，因此重新构建镜像或删除容器不会影响交易数据。

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

测试环境仍使用独立的临时 SQLite 数据库，不会读写 Docker 中的 MySQL 数据。
