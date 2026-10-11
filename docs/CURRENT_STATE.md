# 当前状态

核查日期：2026-10-10。代码基线 origin/main `97117cb`（运行代码与 #101 `37209f7` 相同，此后只有文档变化）。本页只写事实与未完成项；缺陷细节见 [审核报告](AUDIT_2026-10-10.md)，顺序见 [改进计划](PROJECT_REBASELINE.md)，完成条件见 [验收边界](KNYC_TERMINAL_DELIVERY.md)。

## 产品范围

完整个人桌面天气交易终端：行情、天气、可解释策略、人工及自动交易、账户、订单、持仓、回测和复盘。第一阶段 KNYC / Kalshi / Polymarket US；第二阶段另一城市。Poly Intl 与 KLGA 只保留历史和回滚用途。当前为功能开发阶段，Live 默认关闭，实盘额度待用户在功能完成后确定。

## 版本与运行

| 对象 | 状态 |
|---|---|
| 最近部署 | 2026-09-28 部署 `37209f7`：发布包 86 个文件、SHA-256 `d27fd36c…5a3c`、manifest 逐文件 `mismatches=[]`；KNYC/KLGA 相关 11 个服务 active，terminal `/health` 200；HRRR 只在内存处理原文；根盘可用 19,057,070,080 / 30,083,776,512 字节（约 63.3%）。来源：原 #102 记录，已并入本页后关闭 |
| 10 月 10 日 VM | **待阶段 B 核查**（需浏览器 SSH）：磁盘、服务、Paper 游标积压、24 小时回收是否已实际触发 |
| 正式终端 | **待阶段 B 核查**（Cloudflare Access 登录后）：双平台 parse 状态、信号、Paper 预检、回测入口、实盘门控原因 |
| 原始目录 | `D:/ALLPROJECTS/weather forecast` 已于 2026-10-10 快进到 main（本地未提交文档 diff 存档于 `D:/ALLPROJECTS/weather-forecast-archive/`）；开发在 `.worktrees/` 中从 origin/main 建分支 |

## 功能状态

“代码存在”不代表可用。下表“阻断”一栏为已证实的代码级问题。

| 能力 | 已有实现 | 阻断 / 缺口 |
|---|---|---|
| 平台可交易性（A0） | Poly US 规则审计、模型、信号链路 | **Kalshi 合约全部 `ambiguous`（D1），且无模型预测（D2）→ Kalshi 的 Paper、Live、策略都不可用** |
| 市场与天气 | React 单入口、跨日/bin、行情、METAR/HRRR、天气历史 | 正式页面现状待核查 |
| S1/S2/S3 | `signals_v2` 天气触发、净优势、每日机会、未知订单先对账；Paper 与回放共用 | 参数硬编码；S1 允许负 edge 腿；S2 零成交可在 120 秒内反复重试（R3/R6）；v3 规格已定待实现 |
| 温度模型 | `knyc_model.py`：p_end 森林 + CLI 残差分布，HRRR 失效时关闭 | 模型文件验证期与训练期重叠、无 `data_cutoff`、特征脚本不在仓库（D7）；研究与线上特征不一致（R4） |
| Paper | Nautilus 原生订单/成交/账户；request_id 幂等 | 日亏损额度实为敞口额度（D6）；成交模型假设无限流动性（R1）；9 月 23 日“闭环完成”缺附件（D9） |
| Live | 双平台签名、下单、撤单、单单查询、原生对账、私有 WebSocket、服务端门控 | **累计 $5 写死不恢复（D3）；无策略执行（D4）；Poly US 超时未知单永久锁账户（D5）**；无全局停止 |
| 回测 | 回放复用 `Session` / `BacktestEngine` | **只读热库，约 24 小时外拒绝；无外部历史导入器（D8）**；逐事件 snapshot 随历史变慢（R2） |
| 测试 | 7 项 CI；后端 tests/trading 较完整 | **终端浏览器测试全部 mock 后端（D10）**；无真实 FastAPI+worker+Nautilus 整链用例 |
| 数据与容量 | #101 条件式 24 小时回收；天气原文 R2 核验后清空；journald 100 MB | 无整体磁盘预算检查；当前值待核查 |
| 多城市 | 部分 station 字段 | KNYC/KLGA 常量散落；美国运行时仍依赖 KLGA worker（R7） |

## 下一步

W0 → W1 → W2，随后 W3/W4 并行，再 W5、W6。阶段 B：用户切换到可用浏览器自动化的账号后，补 VM 与终端实测并列出 VM 清理清单。

历史全文见 [2026-10-10 基线](history/2026-10-10-baseline/CURRENT_STATE.md)，仅供追溯。
