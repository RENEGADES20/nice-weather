# 当前状态

核查日期：2026-10-10（America/Chicago）。文档基线 origin/main `588d61d`；VM manifest 实测 `37209f7`，后续 main 变更为文档与过期静态资源清理。本页只写事实与未完成项；缺陷细节见 [审核报告](AUDIT_2026-10-10.md)，顺序见 [改进计划](PROJECT_REBASELINE.md)，完成条件见 [验收边界](KNYC_TERMINAL_DELIVERY.md)。

## 产品范围

完整个人桌面天气交易终端：行情、天气、可解释策略、人工及自动交易、账户、订单、持仓、回测和复盘。第一阶段 KNYC / Kalshi / Polymarket US；第二阶段另一城市。Poly Intl 与 KLGA 只保留历史和回滚用途。当前为功能开发阶段，Live 默认关闭，实盘额度待用户在功能完成后确定。

## 版本与运行

| 对象 | 状态 |
|---|---|
| 最近部署 | 2026-09-28 部署 `37209f7`：发布包 86 个文件、SHA-256 `d27fd36c…5a3c`、manifest 逐文件 `mismatches=[]`；KNYC/KLGA 相关 11 个服务 active，terminal `/health` 200；HRRR 只在内存处理原文；根盘可用 19,057,070,080 / 30,083,776,512 字节（约 63.3%）。来源：原 #102 记录，已并入本页后关闭 |
| 10 月 10 日 VM | 已只读核查（UTC 10 月 11 日 04:31 起）：manifest `37209f7`；根盘初样 58% 已用、约 12G 可用，KNYC 5.7G，后样约 7.10 GB；10 service running、两个 R2 timer waiting；回收已触发。04:39 Kalshi/Poly US Paper 落后 11/1,682 个事件，时间延迟 2.9/666.3 秒，详见 [记录](acceptance/handoff-verification-2026-10-10.md) |
| 正式终端 | 已登录只读核查：当前气候日 Kalshi 6/6 ambiguous、Poly US 6/6 parsed（VM feed 核对）；双平台行情可读。Kalshi 预检拒绝，Poly US 预检使用约 649–662 秒旧报价；次日预检仅显示合约 ID。信号观测陈旧，Live 白名单阻断及累计 $5 可见。回测仅查看已有结果，A0/交易闭环/A11 不记通过 |
| 原始目录 | `D:/ALLPROJECTS/weather forecast` 已于 2026-10-10 快进到 main（本地未提交文档 diff 存档于 `D:/ALLPROJECTS/weather-forecast-archive/`）；开发在 `.worktrees/` 中从 origin/main 建分支 |

## 功能状态

“代码存在”不代表可用。下表“阻断”一栏为已证实的代码级问题。

| 能力 | 已有实现 | 阻断 / 缺口 |
|---|---|---|
| 平台可交易性（A0） | Poly US 规则审计、模型、信号链路 | **Kalshi 合约全部 `ambiguous`（D1），且无模型预测（D2）→ Kalshi 的 Paper、Live、策略都不可用** |
| 市场与天气 | React 单入口、跨日/bin、行情、METAR/HRRR、天气历史；正式页面双平台与档位切换已观察 | 天气区加载等待、官方小时温度/NWS 预报缺口；模型与信号仍被门控 |
| S1/S2/S3 | `signals_v2` 天气触发、净优势、每日机会、未知订单先对账；Paper 与回放共用 | 参数硬编码；S1 允许负 edge 腿；S2 零成交可在 120 秒内反复重试（R3/R6）；v3 规格已定待实现 |
| 温度模型 | `knyc_model.py`：p_end 森林 + CLI 残差分布，HRRR 失效时关闭 | 模型文件验证期与训练期重叠、无 `data_cutoff`、特征脚本不在仓库（D7）；研究与线上特征不一致（R4） |
| Paper | Nautilus 原生订单/成交/账户；request_id 幂等；[9 月人工买卖记录](../acceptance/knyc-task6-2026-09-22.md)存在（D9 已更正） | 日亏损额度实为敞口额度（D6）；无限流动性（R1）；生产预检旧报价、次日合约异常及 Poly US 消费延迟待 W0/W1/W5 修复 |
| Live | 双平台签名、下单、撤单、单单查询、原生对账、私有 WebSocket、服务端门控 | **累计 $5 写死不恢复（D3）；无策略执行（D4）；Poly US 超时未知单永久锁账户（D5）**；无全局停止 |
| 回测 | 回放复用 `Session` / `BacktestEngine` | **只读热库，约 24 小时外拒绝；无外部历史导入器（D8）**；逐事件 snapshot 随历史变慢（R2） |
| 测试 | 7 项 CI；后端 tests/trading 较完整 | **终端浏览器测试全部 mock 后端（D10）**；无真实 FastAPI+worker+Nautilus 整链用例 |
| 数据与容量 | #101 条件式 24 小时回收已实际推进；天气原文归档表有 2,147 条核验记录 | KNYC 目录后样约 7.10 GB，results WAL 单次约 1.41 GB；仍缺整体容量归因与预算。七天 KNYC/KLGA R2 失败日志 15/2 条；VM 清理需精确清单和人工确认 |
| 多城市 | 部分 station 字段 | KNYC/KLGA 常量散落；美国运行时仍依赖 KLGA worker（R7） |

## 下一步

W0 → W1 → W2，随后 W3/W4 并行，再 W5、W6。阶段 B 只读实测已记录；将次日预检异常、陈旧报价和 Paper 延迟纳入相应工作包。VM 清理按核查记录评估，未经人工确认不删除。

历史全文见 [2026-10-10 基线](history/2026-10-10-baseline/CURRENT_STATE.md)，仅供追溯。
