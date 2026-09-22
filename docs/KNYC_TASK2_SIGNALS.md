# 任务 2：共享信号与历史研究交付

范围仅为信号、必要的引擎调用与事件持久化、历史研究及测试。基于 7303091 独立开发；任务 5 的未提交代码未复制。任务 6 负责合并与部署，本任务无 VM 操作、依赖安装、批量删除及真实交易。

## 调用接口

`signals_v2.evaluate(strategy, contracts, weather, books, asof, risk, context="online")`
返回原有 `action/triggered/legs/reason`，增加 `signal_id/basket_id/stage/events`、
时间口径及模型验证信息。所有时间为 UTC Unix 秒，策略窗口为纽约 12:00:00–22:00:00；
档位为现有整数华氏完整分区，市场日期和结算源必须匹配。

- `online`：沿用实时收到时间和当前执行报价校验。
- `historical_received`：使用实际接收时间与历史 as-of，不采用实时秒级 TTL。
- `historical_source`：缺失接收时间时显式使用 `source_time`，观测使用 `observation_at`。
  实际接收时间存在时优先遵守；未来接收的修订和合约不能通过来源时间提前进入历史。
  原始 `received_at` 保持 null，不写回原始库。

历史报价需提供明确的 `valid_until`，区间为 `[证据时间, valid_until)`。
该字段由提供方依据采样覆盖/断线证据确定，不能自动设为下个跨缺口样本的时间。
可提供真实深度，或任务 3 的 `price/price_source/max_quantity` 近似报价。
`max_quantity` 是模拟配置或实际容量上限，不声称为观察到的深度；缺失则不能完成数量优化。
历史价格、费用仍通过原有合约精度和费用函数校验，不用模型概率生成价格。
任务 3 的费用估算需明确进入带版本的费用输入；此任务未改变费用及撮合公式。

`signal_research.research_signals(records, context=...)` 逐条读取时间递增的标准化输入：

```python
record = {
    "asof": evaluation_seconds,
    "contracts": historical_contracts,
    "weather": historical_weather,
    "books": prices_by_yes_token,
    "risk": {
        "cash": 100, "budget": 5, "day_remaining": 20, "loss_remaining": 20,
        "bin_remaining": remaining_by_yes_token,
    },
    "strategies": ["S1", "S2", "S3"],
}
```

这是任务 3/4 可直接调用的研究入口；原 `us_runtime.replay` 保留真实接收事件回放。
研究生成候选不自动执行，不将信号次数解释为独立每日机会或成交次数。
完整可运行的合成契约样例见 `tests/trading/test_signal_research.py`，均明确用于测试。

## 订单与图上标记

候选保留每腿 `token/side/quantity/price/tif/cost/probability`，以及报价来源、年龄与真实接收时间。
`signal_id` 和 `basket_id` 关联组合，S1 两腿各自对应 bin；价格和数量未能确定时使用 `targets`
表达天气目标，不虚构价格。事件包含账户、模式、平台、日期、策略、概率、模型/规则版本和证据引用。

阶段分别为 `waiting/weather_warning/weather_trigger/candidate/order/execution_rejected/fill`。
`candidate_reason` 解释候选是否成立；`execution_reason` 解释账户侧执行限制。
关闭自动交易后仍可得到 `action=buy` 的研究候选，实际提交必须同时满足 `execution_eligible=true`。
`fill` 仅由原生累计成交反馈产生，`executions` 包括订单 ID、状态、累计数量及实际均价。

`opportunity_status(state, executions)` 可由任务 3 复用：

```python
state = {"attempt": 1, "orders": ["order-id"]}
feedback = [{"order_id": "order-id", "status": "PARTIALLY_FILLED", "filled": 0.25}]
reason = opportunity_status(state, feedback)  # DAILY_OPPORTUNITY_CONSUMED
```

每个 state 归属一个账户/模式/平台/市场日/策略。存在未知、开放或处理中订单时需对账；
明确零成交终态允许重评。部分成交永久消耗机会，重复反馈和后续零成交状态不能恢复机会。
旧 v1 消耗状态继续保留。账户限制不再提前终止天气计算。

当前信号仍位于账户 snapshot 的 `signals`；在线变化批次写入既有 `inputs` 表的
`kind=signal-events`。相同事件批次通过稳定 ID 去重，与 Paper 状态在同一事务提交。
原回放 `replay-decisions` 每条 signal 内也包含 `events`，任务 4/6 无需从最终快照推断中途信号。
本任务不增加数据库服务、前端页面或新的部署入口。

## 真实数据诊断

9 月 21 日原始接收回放证据见 `acceptance/knyc-real-replay-audit-2026-09-22.json`。
本轮复核已保存的日志计数和覆盖记录，没有再次访问 VM，也不声称重跑了远端完整日志。

| 平台 | 各策略的决策变化原因计数 | 结论 |
| --- | --- | --- |
| Kalshi | 观测陈旧 129、HRRR 缺失/陈旧 6、结算模型未验证 59 | 原模型仅接受 NWS CLI，不能直接当作 Weather Company 模型；该项包含实质目标不匹配，不能统一解除 |
| Poly US | 规则未核验 43、观测陈旧 32、HRRR 缺失/陈旧 1 | 当时规则证据和天气确有缺口，不能用今天状态回填 |

两个平台各 148 条预测仅覆盖约 76 分钟，盘口最大间隔约 22.4 小时。
以上为变化次数，含报价重评，不能作为独立预测次数。
30 秒盘口门禁是历史研究路径的错误限制，但该已保存样本尚未走到价格层，不能将它当作该样本的唯一根因。

本轮另用本地 KNYC 特征与 HRRR、9 月 1 日以前的标签训练/校准，分析 9 月 1–13 日。
结果见 `acceptance/knyc-task2-historical-signals-2026-09-22.json`：100 个可用时点，
S1/S3 各 53 次天气条件成立；候选 0。真实时点之前的本地合约目录缺失，S2 无法核定跨档，
S1/S3 无法形成可交易档位及报价组合。没有已验证满足全部候选条件的真实样本。
这些天气触发证明研究计算可进行，不能解释为可实现收益、生产模型通过或真实成交。

历史研究使用原有 HRRR `cycle + 2h` 可用时间假设，缺历史 received_at；标签修订证据有限。
明确保留此限制。模型产物生成时间为本次执行时间，`model_data_cutoff` 为研究截止时间，
二者分开保存；不修改或替换生产模型 JSON。

本地复现（在项目研究环境中，`PYTHONPATH=src`）：

```text
python scripts/research_knyc_signals.py --features <pilot_features.csv> --hrrr <forecasts.jsonl> --feed <local-feed.sqlite3> --start 2026-09-01 --end 2026-09-13 --output <report.json>
python scripts/research_knyc_signals.py --inputs <normalized.jsonl> --context historical_source --output <report.json>
```

报告保存输入文件哈希、模型截止、实际覆盖、逐层计数和触发/不触发样本；不复制大数据集。

## 验收状态

最终定向回归 45 项通过，修改文件 Ruff 检查通过。完整交易回归曾运行 204 项：202 项通过，
2 项因本机缺少 redis-server 无法运行，原有 CI 会在 Linux 安装该依赖后执行。
未为了测试向本机或 VM 安装 Redis。

- 已实现：共享判断、持续天气信号、候选/执行状态分离、机会反馈、历史研究及事件契约。
- 已验证：真实历史天气触发；合成完整价格样例可生成三策略候选；原生模拟成交后继续更新天气。
- 真实候选和下游完整模拟：受真实历史合约/价格证据缺口影响，未宣称完成。
- 生产模型验收、页面集成、任务 3 近似撮合与 VM 发布由对应任务处理。
