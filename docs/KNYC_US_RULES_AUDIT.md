# KNYC US 合约核验：2026-09-19

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

- [Poly US 费用](https://docs.polymarket.us/fees)：每笔主动成交先半偶数舍入，累计收费不得超过累计精确费用的半偶数结果；只能调低该笔收费。模拟累加器持久化，实盘以交易所 commission 为事实，不推算成交。策略使用主动 IOC，未将 maker rebate 计入预期收益。
- [Kalshi 舍入](https://docs.kalshi.com/getting_started/fee_rounding)：模型费向上取六位美元；余额按会员类型对齐到 0.0001 或 0.01 美元；订单累计多收部分按余额精度退回，单笔净费用不小于零。辅助算法已验证官方数例；账户精度及原生记账精度未确认前不启用该规则。
- [Poly US 天气规则](https://docs.polymarket.us/faqs/weather-faqs) 提供结算时间与站点来源。
- [Poly US 整份规则](https://docs.polymarket.us/learn/trading/basics/fractional-shares) 与实际 API 数量字段不一致。

仍需核验两平台的观察窗口、结算整数形成与修订截止，及 Kalshi 账户精度、Poly US 数量冲突。当前模型的 NWS CLI 回顾标签不自动取得 Weather Company 口径资格。

后续官方来源补充：[NWS 观测与气候产品 FAQ](https://www.weather.gov/lot/weather_observations_faq) 明确 CLI 的日界使用当地标准时，夏令时期间对应 01:00 至次日 01:00；METAR 发布值有摄氏转换及采样缺口，不能用其最高值替代 CLI。该依据支持 NWS 口径的固定 EST 窗口，尚不能独自证明 Weather Company 的修订规则。[Poly US 2026-09-14 规则书](https://polymarketexchange.com/files/legal/latest/rulebook) 将各合约细则单独定义，并未在正文提供天气温度的完整细则；继续保留实际合约证据与未解决项。
