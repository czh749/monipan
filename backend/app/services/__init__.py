"""业务逻辑服务层。

本包包含模拟盘系统的核心业务逻辑：

- market/    : 行情服务包
  - constants   : 常量与日志器
  - types       : 数据类型、异常与运行时状态
  - session     : A 股交易时段判断与刷新间隔配置
  - eastmoney   : 东方财富公开行情接口的请求与解析
  - tick        : 行情刷新（覆盖写入数据库）
  - status      : 行情状态计算（覆盖率与新鲜度）
  - formatters  : ORM 对象格式化为前端友好的字典

- trading.py : 交易引擎
  - get_demo_account(db)         : 获取 demo 用户的模拟账户
  - calculate_fee(side, amount)  : 计算交易手续费（佣金 + 印花税）
  - place_market_order(db, ...)  : 执行市价委托（买入/卖出）
"""
