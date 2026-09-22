# KNYC US 合约核验：2026-09-19

## 2026-09-22 Poly US 温度产品细则核验

从官方公告页实际加载的 `/files/products` 目录取得 [TC 产品认证（2026-04-03）](https://polymarketexchange.com/files/products/PMUS%20-%20TC%20-%20%282026.04.03%29.pdf)，原文已保存并渲染核对第 2、3 页。细则规定使用指定来源首次正式发布的数据及其报告精度，来源的舍入/截断直接决定结算，通常不纳入正式发布后的修订；明显数据不一致可延期等待纠正，缺失资料或来源中断由交易所处理。它补齐了此前仅有 FAQ 的精度、首次正式发布及修订依据。结合 NWS 官方标准时气候日说明与 KNYC 实际 CLI 合约，可核验固定 EST 00:00–次日 00:00 的观测窗口；不将 METAR 的转换结果作为结算值。

新解析器严格匹配实际描述中的站点、日期、最高温、NWS Daily CLI、华氏档位和 Yes/No 标签。API endDate 单独保留，不用于推定观测日界：今天实际六档均为次日 09:00 UTC，气候窗口仍于 05:00 UTC 结束。31 项规则/数量/策略测试通过，包含纽约夏令时切换、前述两个时间的分离、描述冲突、暂停交易及审计时间边界。

审计知识从实际读取证据的时间生效。今天六档真实响应均匹配，9 月 19 日的原始接收记录继续 ambiguous，不回填当时尚未取得的审计知识。规则版本包含审计版本与证据摘要；API 档位、价格精度或描述无法匹配时继续 no-trade。详细哈希、来源接收时间和逐档结果见 acceptance/knyc-poly-weather-rules-2026-09-22.json。本次实现待发布，解析成功不代替模型/行情新鲜度、风险、启用及交易所最终结算检查。另 15 项结算/终端集成回归通过，共 46 项。Kalshi 天气口径、最小数量和账户精度继续待核验。

## 2026-09-22 数量规则更新

重新读取 [Poly US Create Order OpenAPI](https://docs.polymarket.us/api-reference/orders/create-order.md)，quantity 的定义明确允许 minimumTradeQty 小于 1 的市场使用小数数量。此合约级 API 规则取代下文 9 月 19 日记录的通用整份帮助页冲突判断。保存的 9 月 19 日六档真实合约均报告 minimumTradeQty=0.01；重新归一化后仅移除过时的数量冲突原因，其余天气门禁保持，全部仍为 no-trade。原文与收到时间、哈希见 acceptance/knyc-poly-quantity-2026-09-22.json；原始材料保留忽略目录。

不再将缺失 minimumTradeQty 补为 0.01；空值、布尔值、非有限或非正值均拒绝。minimumTradeQty≥1 时使用整数份约束，小数市场继续使用现有两位份数执行精度。规则版本纳入数量解释版本和执行步长。Kalshi 最小数量与两平台天气口径仍待核验，未解除全局交易门禁。

## 历史核验记录

2026-09-20 结算接口核验：[Kalshi Get Market](https://docs.kalshi.com/api-reference/market/get-market) 的 finalized 状态、settlement_ts 与 settlement_value_dollars 共同作为证据；[Poly US Get Market Settlement](https://docs.polymarket.us/api-reference/markets/get-market-settlement) 与市场的 resolved/expired 状态及 outcomePrices 交叉核对。实际 9 月 18 日合约已读取；Poly US 一档限流时保留未完成状态。市场 closed 标志或 CLI 页面出现数字均不直接触发资金结算。

状态：两平台仍有未解决项，禁止把本记录视为交易放行。实际市场原文与采集时间保存在本地 `var/knyc/rules-audit-20260919`，不得以测试夹具代替。

| 项目 | Kalshi | Poly US |
| --- | --- | --- |
| 站点、指标 | 实际合约 New York City CLINYC，日最高温 | 实际合约 Central Park KNYC，日最高温 |
| 结算来源 | The Weather Company | NWS Daily Climate Report CLI |
| 温标、档位 | 实际合约华氏整数区间，边界随市场日采集 | 实际合约华氏整数区间，边界随市场日采集 |
| 观察窗口 | 程序固定 EST 日仅为待核验映射，不能凭结束时间推定 | 同左 |
| 修订 | 实际次级规则允许初次非临时资料有重大错误时延迟；极端缺失按 fair price | 官方 FAQ：次日 08:00 ET；与 METAR 不一致可延至 11:00 ET；一周无资料按 last fair price |
| 最小数量 | 尚未独立证实，不能凭小数接口推定 | 实际 API 0.01 与帮助页整份要求冲突，待解除 |
| 费用 | quadratic 系数来自系列；官方逐订单余额对齐算法需要账户精度 | 2026-09-17 起 taker 0.0695，半偶数分币舍入，逐订单累计封顶 |

## 费用实现依据

2026-09-20 接线补充：[Poly US Orders & Trading](https://docs.polymarket.us/concepts/orders) 明确 API 价格以 YES 为基准，NO 报价需转换补数；[Create Order](https://docs.polymarket.us/api-reference/orders/create-order) 说明 minimumTradeQty 小于 1 的市场支持小数数量，已记录为比通用整份帮助页更具体的证据，尚未解除其余天气合约门禁。[官方 Python SDK](https://github.com/Polymarket/polymarket-us-python) 核验提交 `83128f4db0245641e88427a4fa504760ed76e038`，独立 ORDER_SNAPSHOT 订阅以 eof 结束；实际只读验证收到空快照。官方私有 WS 文档要求优先小数净持仓字段，旧整数值经过舍入。

- [Poly US 费用](https://docs.polymarket.us/fees)：每笔主动成交先半偶数舍入，累计收费不得超过累计精确费用的半偶数结果；只能调低该笔收费。模拟累加器持久化，实盘以交易所 commission 为事实，不推算成交。策略使用主动 IOC，未将 maker rebate 计入预期收益。
- [Kalshi 舍入](https://docs.kalshi.com/getting_started/fee_rounding)：模型费向上取六位美元；余额按会员类型对齐到 0.0001 或 0.01 美元；订单累计多收部分按余额精度退回，单笔净费用不小于零。辅助算法已验证官方数例；账户精度及原生记账精度未确认前不启用该规则。
- [Poly US 天气规则](https://docs.polymarket.us/faqs/weather-faqs) 提供结算时间与站点来源。
- [Poly US 整份规则](https://docs.polymarket.us/learn/trading/basics/fractional-shares) 与实际 API 数量字段不一致。

仍需核验两平台的观察窗口、结算整数形成与修订截止，及 Kalshi 账户精度、Poly US 数量冲突。当前模型的 NWS CLI 回顾标签不自动取得 Weather Company 口径资格。

后续官方来源补充：[NWS 观测与气候产品 FAQ](https://www.weather.gov/lot/weather_observations_faq) 明确 CLI 的日界使用当地标准时，夏令时期间对应 01:00 至次日 01:00；METAR 发布值有摄氏转换及采样缺口，不能用其最高值替代 CLI。该依据支持 NWS 口径的固定 EST 窗口，尚不能独自证明 Weather Company 的修订规则。[Poly US 2026-09-14 规则书](https://polymarketexchange.com/files/legal/latest/rulebook) 将各合约细则单独定义，并未在正文提供天气温度的完整细则；继续保留实际合约证据与未解决项。
