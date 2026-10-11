# 当前实现与目标改进

核查基线：`37209f7`，2026-10-10。下述区分现有实现和拟议改进，不把设计写成完成事实。

## 已有运行主链

```text
Kalshi / Poly US + KNYC 天气 / HRRR
  → FeedStore / feed.sqlite3（来源时间、实际接收时间、版本）
  → feed_event → signals_v2 → 风险 / 命令
  → Paper: Session / Nautilus BacktestEngine → 原生订单 / 成交 / 账户
  → Live: US 执行、传输、原生报告及对账客户端
  → results / 请求状态 / UI 投影 → FastAPI → React terminal

现有回放: feed.sqlite3 → us_runtime.replay → 同一 Session → 结果投影
历史天气研究: 既有研究资产及脚本 → 离线模型 / 触发评价
```

## 已核对的职责

| 文件 | 当前职责 / 限制 |
|---|---|
| `frontend/terminal/src/main.tsx` | 主入口及组件接线；包含交易、历史、回测状态，不等于所有后台任务均已完成真实验收 |
| `trading/signals.py`, `signals_v2.py` | 冻结研究版与持续执行版；重复/未知/已成交机会状态处理 |
| `trading/engine.py` | 建立 Nautilus BacktestEngine、二元合约、fee/fill/settlement 模型、原生订单及账户；逐事件 streaming 调用 |
| `trading/paper_execution.py` | `market-price-v1` 近似成交；报价缺失时可使用其他显示价格，费用及滑点为可配置假设 |
| `trading/us_runtime.py` | 实时 Paper 和历史回放共享事件转换；回放当前读 feed.sqlite3，拒绝被清除的区间 |
| `trading/us_live.py`, `us_execution.py`, `us_reconcile.py` | 双平台 Live 及对账路径；不能用旧 Polymarket TradingNode 接线替代 US 验证 |
| `trading/backtest_view.py` | 结果和事件投影；展示代码不定义策略或撮合规则 |
| `trading/r2_retention.py` | 天气归档和条件式回收；当前长期天气归档不能自动提供完整历史行情 |

路径均相对 `src/nice_weather/`，前端路径除外。`pyproject.toml` 将 Nautilus 固定为 1.231.0 / Python 3.12；在线 latest 文档仅供设计参考，实施须按该版本核验接口。

## 已证实的实现阻断（2026-10-10 审核）

Kalshi 合约全部 ambiguous 且无模型预测；实盘累计 $5 写死、无策略执行、Poly US 超时未知单锁账户；回测只读约 24 小时热库；终端浏览器测试全 mock。详见 [审核报告](AUDIT_2026-10-10.md)，修复顺序见 [W0–W6](PROJECT_REBASELINE.md)。

## 首要改进

1. 给既有引擎增加外部历史数据导入边界和覆盖清单，避免将在线热库作为唯一回测数据源；先用下载样本验证，暂不另建服务。
2. 复用策略选择及订单/账户事件，明确天气研究、价格近似与有深度执行模拟三种证据等级。数据不足时降低结论范围，不能伪造盘口、接收时间或成交。
3. 以真实 profile 决定是否批量导入 Nautilus 数据、减少逐事件序列化/提交；行为与资金账目对等后才替换热路径。采用 BacktestNode/Parquet catalog 的前提是解决明确的批量历史需求，并验证固定版本支持。
4. 浏览器沿用现有 React/FastAPI；完整服务验收补足 mock UI 测试。历史/诊断按需加载，独立错误不阻塞下单与撤单。
5. 多城只提取实际存在的 station/timezone/rules/model 参数；第二城进入时验证差异，不预建插件市场、通用调度平台或多层配置框架。

## 数据分工

运行热数据继续受当前保留策略约束。离线研究优先读取已有文件或按需官方历史下载，保存来源、获取时间、覆盖、规则、模型版本及缺口。长期行情存储是否需要，须由具体策略及外部数据缺口决定；本轮未批准或实施新采集。

历史 KLGA/Streamlit 架构完整保存在 [原文](history/2026-10-10-baseline/SYSTEM_ARCHITECTURE.md)。旧入口保留与否属于后续实测范围，未经人工确认不批量删除文件。
