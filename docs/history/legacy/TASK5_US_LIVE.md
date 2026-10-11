# 任务 5 接入与验收

本任务交付独立组件与完整本地命令链；主入口、全局样式、策略业务、Paper、回测和部署配置均由原负责人保留。承接 us_execution/us_transport/us_reports 的原有未提交修改。未引入依赖、数据库或 VM 文件。

## 后端运行入口

每个平台单独进程，根目录使用现有 KNYC 采集/结果目录；与 Paper/API 分开。示例（由任务 6 配置服务，不要在同一进程调用）：

```sh
python -m nice_weather.trading.us_live --root var/knyc --venue kalshi --credentials /server/path/KALSHI.txt --balance-precision 4
python -m nice_weather.trading.us_live --root var/knyc --venue poly_us --credentials /server/path/POLY.txt --balance-precision 2
```

Kalshi 直接账户网格为 4 位，FCM 需核验后用 2；原生成交费用用 USD6。沿用服务端凭据读取；示例路径不是新凭据。读取 settlement_watch/历史合约，不依赖当天最新快照，新挂牌日自动注册。凭据只用于签名，不进前端或日志。初始启用为 false；配置保存在 results.sqlite3 的 us_live_controls。预算使用同一个 results.sqlite3，所有本地绑定必须共用此根目录；复制或重置账目不属于正常恢复方式。

## 现有 API 契约

`GET /api/snapshot` 与 `/api/events` 保留 accounts 中的 `account/mode/status/updated/snapshot`。账户名 `live-{venue}-knyc`，mode=`live`。snapshot 增加：

- `funds`：十进制字符串或 null，现金、可用、平台预留、挂单名义额、估值及口径；Kalshi partitions。
- `orders/fills/positions`：本终端精确归属的原生报告投影；`external_orders/external_positions/activities` 保留平台事实，包括历史缺字段记录。原生报告方法支持订单与成交时间过滤。
- `authenticated/received_at/reconciled/reconcile_reason/stream_connected`：显示与交易资格分开；失败保留最后成功资金值，received_at 不刷新。
- `config`：revision、binding、manual_enabled、S1/S2/S3 开关、whitelist、order_limit、position_limit、daily_loss_limit。
- `budget`：limit/spent/reserved/remaining；`availability` 按 order/cancel/stop/configure 返回 allowed/reason；具体市场/价格/数量的资金、白名单、费用和持仓检查在执行时重验，拒绝原因进入现有请求回执。
- `market_rules`：每个已采集市场的步长、最小数量、规则/费用状态、开放状态及路由。账户金额未知时不显示零。

认证与 CSRF 沿用现有 API。`POST /api/commands`：request_id、venue=`kalshi|poly_us`、mode=`live`、kind 与 payload；用现有 `/api/requests/{request_id}` 查询结果。

| kind | payload |
| --- | --- |
| configure | revision 与要修改的配置；binding 取当前已认证 snapshot.binding |
| order | market、outcome=YES/NO、side=BUY/SELL、price/quantity 十进制字符串、tif=GTC/IOC/FOK |
| close | 同 order，强制 SELL；按真实持仓和挂卖数量检查，不自动超量平仓 |
| cancel | order_id（本地原请求 ID）或 order_ids；逐笔去重 |
| start / stop | strategies 数组与 revision；stop 持久禁用并撤对应策略单，不平仓 |
| reconcile | 空对象，刷新平台事实并运行原生对账 |

浏览器订单归属固定 manual；任务 2 的候选通过同一 `Requests.submit(id, account, "live", "order", payload)` 提交，payload.owner 为 S1/S2/S3；概率/策略触发由任务 2 判定，Live 执行核验实际配置与资金。HTTP 接单仅为 accepted，成交以交易所报告为准。Unknown 保留身份与预算，Kalshi 利用 client_order_id 精确恢复；Poly 缺可靠关联时保持未知。撤单可在新交易关闭后执行，订单从开放列表消失不视为撤单成功。

## 组件

`LiveAccountPanel({snapshot,send,pending,venue})`、`LiveOrderTicket({snapshot,market,send,pending})`，局部样式 `live.css`。send 为现有 useCommands 的包装：`send(kind,payload)`，固定当前平台及 mode=live。账户切换时按 account 选 snapshot，并 key={venue} 重建票据；沿用既有请求 journal、CSRF 刷新和回执归属校验。独立开发入口已验证旧平台请求不会覆盖新平台。

本地模拟传输验收入口（无真实凭据）：

```sh
cd frontend/terminal
node node_modules/@playwright/test/cli.js test --config examples/playwright.config.ts
```

自行启动 5176 前端与 8775 API，根目录每次唯一；页面显式标记模拟传输。examples 和 tests 不进入生产发布包。流程包含配置、启用、部分成交、刷新恢复、撤单、卖出、策略启停和平台切换；请求经过认证 API、SQLite 队列、USExecutionClient 和原生引擎。双平台原生部分成交/恢复、停止策略保留人工挂单另由 pytest 验证。

## 验收状态与限制

- 实现完成：本地 Live 入口、现有 API 接线、两组件和原生报告/命令链。任务 6 尚未接主入口或发布。
- 模拟命令链通过：双平台 pytest 原生缓存成交 ID 与交易所事实一致；Playwright 完整流程通过。测试覆盖签名、分页、超时未知、预算边界/并发、重启及停止撤单。195 项交易测试通过；本机 2 项已有 Redis 测试缺运行环境，CI 使用已有 Redis 步骤验证。
- 补充双平台 YES/NO 买卖、部分成交、重启及状态/预算检查：10 项通过；Ruff 全仓检查通过。
- 真实只读通过：Kalshi 18 GET、Poly US 8 GET；认证、私有流及当前空持仓对账通过。Kalshi 有 10 笔平台历史订单；Poly US 空订单。原文/账目保存在本地 var/knyc/task5-readonly-*，摘要含源文件 SHA256。真实写请求为零。
- 用户实盘交易未验收；完整市场日、最终结算后持仓恢复未验收。结算使持仓归零但成交历史仍非零时，当前对账会明确报 POSITION_HISTORY_MISMATCH，需取得结算账目后再继续新增风险，不能凭空补成交。
- Kalshi KNYC 合约的既有结算/修订歧义尚未解除，采集合约仍为 ambiguous，仅阻止相关下单，不隐藏资金。当前分区 0 可用余额与聚合可用金额差异明显，运行器不自动划转。
- Poly US REST activities 没有可靠订单关联；私有逐笔成交漏失时保留 FILL_HISTORY_INCOMPLETE。外部未归属活跃订单或无法解释的当前持仓需要对账；终结的外部历史缺 TIF 不影响空仓账户读取。
- VM 未变；PR 不合并、不部署，由任务 6 审查集成。

规则来源沿用本地审计，补核验 Kalshi 分区、[Poly US 余额](https://docs.polymarket.us/api-reference/account/get-account-balances)与私有流协议。原文保存在忽略目录 tmp/task5-docs 与 var/knyc/rules-audit-20260922，未复制凭据。
