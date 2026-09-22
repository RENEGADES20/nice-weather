# 任务 6 集成记录

## 修改范围

统一入口、URL 选择、已有组件接线、信号/原生成交展示适配、Paper 请求恢复、Live 平台隔离与未来目录注册、服务映射和最小发布。三策略公式、成交规则、原生客户端保持各任务实现；任务 5 工作树未提交 depth.py 未纳入。

## 接口

沿用 /api/markets、market-day、weather-history、history、market-quote、paper/preview、paper/equity、commands、requests、snapshot、events、backtests。新增只读 /api/account-events?venue&day&after，投影既有 Paper inputs 与原生成交，返回 events/next/more，不增加存储表。阶段和时间转换复用回测投影。

## 当前验证

- 五个业务 PR 按序检查通过并合并：#73 → #71 → #72 → #74 → #75。
- 本地组件/入口 10 项通过；新增账户心跳、WebSocket 重连用例通过。定向 Python 回归 33 项及后续 Live/事件/发布 15 项通过。
- 实际公开 Poly US 2026-09-22 lt63f：买入 1 份 Ask 0.01，现金 100 → 99.99，持仓 1；卖出 1 份 last_quote 0.01 后无持仓，两张原生订单 FILLED。无模型概率代替价格，无真实交易。
- 历史库来自任务 1 已采集数据的本地 SQLite 备份，未复制到 VM。当前报价从平台公开接口读取。天气缺口如实显示。
- VM 预检：348123a，磁盘约 1 GB，既有 feed/HRRR 因余量检查退出。按用户确认删除单个 361205760 字节 Snap 下载缓存；没有清理数据、备份或依赖。

工程发布、正式 Paper、Live 只读仍在验收；用户实盘交易、完整纽约市场日及最终结算尚未验收。不得将本地开发样例或长期观察状态替换为已通过。
