# Nice Weather

Polymarket 纽约 KLGA 每日最高温市场的可审计研究、Live Shadow、Paper Trading 与交易员只读看板。

当前纵向链路为：

```text
Gamma / CLOB + AviationWeather / NWS
→ 原始快照
→ KLGA 合约与规则校验
→ decision-time UnifiedState
→ 基线 Tmax 分布
→ 档位概率、可成交 Edge 与风险
→ PaperBroker
→ SQLite WAL
→ Streamlit / Plotly Dashboard
```

项目没有钱包接入、私钥配置、真实下单客户端或自动资金操作。所有策略和 3°F 基线分布均为未校准的研究假设。

## 环境

要求 Python 3.11。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m nice_weather.cli config-check
```

配置集中在 `config/nyc_klga.toml`。默认阈值包括 90 秒订单簿接收年龄、90 分钟 METAR 观测年龄、5 分钟 METAR 接收年龄、6 小时 forecast issue 年龄、0.02 uncertainty buffer、0.03 minimum net edge、100 美元 Paper 现金、单档 5 美元和单机场日 20 美元限额。

## 四条核心命令

确定性真实 fixture 端到端运行：

```powershell
.\.venv\Scripts\python -m nice_weather.cli run-once --mode fixture --fixture tests\fixtures\nyc_klga\2026-08-24T0043Z\manifest.json --db var\fixture.sqlite3
```

一次 Live Shadow：

```powershell
.\.venv\Scripts\python -m nice_weather.cli run-once --mode shadow --city NYC --db var\live.sqlite3
```

有限频率持续 Runner；`paper` 只启用本地 PaperBroker，不调用任何外部下单接口：

```powershell
.\.venv\Scripts\python -m nice_weather.cli run-loop --mode shadow --city NYC --interval-seconds 60 --db var\live.sqlite3
```

只读交易员 Dashboard：

```powershell
.\.venv\Scripts\streamlit run src\nice_weather\dashboard.py -- --db var\live.sqlite3 --refresh-seconds 10
```

Dashboard 每次先选定一个 `complete decision_id`，随后所有规则、天气、订单簿、概率、信号、Paper 和审计查询均固定到该 ID。应用通过 SQLite `mode=ro` 短连接读取，不访问外部 API，也不计算概率、信号或 P&L。

## 数据库与诊断

初始化及查看表计数：

```powershell
.\.venv\Scripts\python -m nice_weather.cli db-init --db var\dev.sqlite3
.\.venv\Scripts\python -m nice_weather.cli db-summary --db var\fixture.sqlite3
```

使用系统 `sqlite3` 时可执行：

```sql
SELECT decision_id, decision_time, mode, overall_action, health_level, reason_codes_json
FROM decisions ORDER BY decision_time DESC;

SELECT source, kind, received_at, content_hash
FROM raw_snapshots
WHERE snapshot_id IN (
  SELECT snapshot_id FROM decision_inputs WHERE decision_id = '<decision_id>'
);
```

Runner 将错误写入 `system_events`，每轮写入 `runner_heartbeats`。同一数据库使用 `runner_locks` 防止并发 writer；Dashboard 在 Runner 未提交事务时仍读取上一条完整 decision。

## 测试与 smoke

```powershell
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m pytest tests\unit tests\integration tests\dashboard -q

.\.venv\Scripts\python -m nice_weather.cli smoke --target polymarket --city NYC
.\.venv\Scripts\python -m nice_weather.cli smoke --target observations --city NYC
.\.venv\Scripts\python -m nice_weather.cli smoke --target forecast --city NYC
.\.venv\Scripts\python -m nice_weather.cli smoke --target dashboard --city NYC --db var\live.sqlite3
```

PR required checks 使用固定 fixture，不依赖网络。Live smoke 手动执行或单独调度。

## Fixture 来源与时间边界

`tests/fixtures/nyc_klga/2026-08-24T0043Z` 保存 2026-08-23 从公开官方接口读取的真实字段：Gamma event `892623`、11 个 CLOB YES token 订单簿、KLGA METAR、NWS points 和 24 个目标当地小时。`manifest.json` 固定 `decision_time`、URL、HTTP 状态、接收时间和各文件 SHA-256。

任何决策输入必须满足 `received_at <= decision_time`。历史与 fixture 不使用最终修订天气数据回填早期决策。规则、站点、日期、温标、温度档、观测窗口或结算来源有歧义时，结果为带原因码的 `NO_TRADE`。

## 文档入口

1. [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md)
2. [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md)
3. [`docs/DECISIONS.md`](docs/DECISIONS.md)
4. [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md)
5. [`docs/RESEARCH_INDEX.md`](docs/RESEARCH_INDEX.md)

旧项目和研究库仍位于 `D:\ALLPROJECTS\x learner`，没有迁入当前运行链或 CI。
