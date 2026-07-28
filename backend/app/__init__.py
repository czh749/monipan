"""MoniPan 模拟盘后端应用包。

本包是一个基于 FastAPI 的 A 股模拟交易系统，包含以下子模块：
- main.py    : 应用入口，FastAPI 实例创建与生命周期管理
- api.py     : RESTful API 路由定义
- models.py  : SQLAlchemy ORM 数据模型（7 张表）
- schemas.py : Pydantic 请求/响应校验模型
- database.py: 数据库引擎、会话工厂与连接配置
- seed.py    : 数据库初始化种子数据
- stock_pool.py: 固定 200 只 A 股标的定义
- services/  : 业务逻辑层
  - market.py  : 行情模拟引擎（随机游走价格生成）
  - trading.py : 交易引擎（下单、费用计算、持仓管理）
"""
