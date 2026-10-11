# 交接执行与只读核查（2026-10-10）

核查日期按 America/Chicago：2026-10-10；VM/网页采样跨入 UTC 与纽约 10 月 11 日。代码基线 `588d61d`，VM manifest `37209f7537a8672914d2e50c4dd9633aa239e437`。范围为交接阶段 A/B；未改业务代码、未部署、未改交易配置、未提交订单或回测任务。

## 阶段 A

- GitHub 核查：#104 已 squash 合并为 `588d61d`，七项 CI SUCCESS；#102/#35 已 CLOSED，无重复合并或关闭。
- 原目录三项 R2 配置写入被忽略的 `.env`，逐项验证非空，全程未输出值；还原四个已存档文件，main 从 `60dad0a` 快进到 `588d61d`。`.env`、`KALSHI.txt`、`POLY.txt` 均被 gitignore 覆盖。
- 快进前保留两个阻挡合并的未跟踪文件于 `tmp/handoff-20261010-preserved/`：`repricing-minute-differences-2026-09-14.md`、`repricing-minute-performance.json`。未覆盖其原有内容。
- 已移除七个已批准 worktree 的 Git 注册，分支保留。`knyc-chart-orders/weather forecast`、`knyc-integration`、`knyc-market-weather` 完整移除；`knyc-terminal`、`nautilus-trading`、`knyc-task6-record`、`repricing-response-deploy` 有 Windows 删除残留（Directory not empty / Function not implemented），依交接要求跳过。
- 保留 `trading-interaction`、`vm-data-retention`、`audit-rebaseline`、既有文档 worktree、研究数据和虚拟环境；指定的 pytest/task3 残留未处理。

## VM 只读核查

通过已授权的 Chrome 浏览器 SSH 会话访问指定 VM。查询使用 SQLite `mode=ro`；未调用采集、归档、回收或更新函数。

| 项目 | 实测 |
|---|---|
| 04:31 UTC 根盘 | 29G 总量、17G 已用、12G 可用、58% 使用率；`df -h` 为舍入值 |
| 数据目录 | `/var/lib/nice-weather` 508M；`/var/lib/nice-weather-knyc` 5.7G；`/var/lib/nice-weather-trading` 3.4M |
| 版本 | manifest 为 `37209f7537a8672914d2e50c4dd9633aa239e437` |
| 服务 | 10 个 service active/running：KLGA collector/dashboard；KNYC feed/HRRR/backtest、双 Paper、双 Live、terminal。两个 R2 timer active/waiting；两个 oneshot inactive/dead，本身不表示故障。三个 9 月 22 日旧 retention service 保留 failed 状态 |
| 04:32 UTC Paper 采样 | feed 最新 seq 5,021,665；Kalshi cursor 5,021,663，差 2；Poly US cursor 5,019,014，差 2,651；两者 runs.status=running |
| 04:39 UTC Paper 采样 | feed 最新 seq 5,022,186；Kalshi cursor 5,022,175，差 11，最老未消费事件 2.9 秒；Poly US cursor 5,020,504，差 1,682，最老未消费事件 666.3 秒。事件数差缩小，但尚未追齐 |
| 活跃合约 parse | feed_latest 当前气候日为 10 月 10 日；Kalshi 6/6 ambiguous，归一化结算源 weather_company，原因 `observation window, rounding and revision rules awaiting venue audit`；Poly US 6/6 parsed、nws_cli、歧义列表空。该结算源为运行解析结果，未另行证明平台规则长期不变 |
| 回收证据 | `market_history_progress=(id=1,seq=4732785,evicted_before=1791605890.913871)`；水位非零，确认已实际触发。天气原文归档表 2,147 行，最新 verified_at `1791692290.4897866`。本轮没有重新下载 R2 对象验证 |
| 七天错误 | `journalctl -p err --since '7 days ago' -u 'nice-weather*' -o json` 成功返回 17 条：KNYC R2 service 15 次、KLGA R2 sync service 2 次 Failed；初次 20 秒超时。只输出聚合，未输出任意日志正文或凭据。错误级别过滤不会涵盖所有应用异常，根因未在本轮确认 |
| 最近两小时回收日志 | 五条 `transient_events_pruned` 分别为 3,868、3,732、3,533、3,575、3,976。前两条 checkpoint busy，后三条 busy=false、wal_bytes=0。HRRR 最早保留事件 seq=4,741,759、received=1791608516.5751982，与已推进水位共同支持市场/HRRR 回收正在执行 |

全表 transient 分组查询达到只读查询期限后被 progress handler 中断；未以缺失结果声称全表覆盖验证通过。服务 active 和一次小积压均不足以证明交易闭环或长期性能。

## 正式终端

沿用 Cloudflare 既有账户登录成功。纽约 00:31–00:36 处于固定 UTC−5 气候日与夏令时民用日不同的小时，分别检查页面默认 10 月 11 日与当前气候日 10 月 10 日。

| 流程 | 操作与结果 |
|---|---|
| 市场目录 | 双平台均能选择 10 月 11 日六个档位并看到报价；已挂牌日期列出 9 月 28 日至 10 月 11 日，仅 10 月 9/10 日标记有历史行情 |
| Kalshi Paper 预检 | 10 月 10 日 `65° or below`、1 份、限价 0.01、IOC：`Ambiguous contract: no-trade`，提交按钮禁用。D1 获线上支持 |
| Poly US Paper 预检 | 10 月 10 日 `65 or below`、1 份、限价 0.01、IOC：预计价 0.01、ask、费用 0、预计 FILLED；报价年龄 649–662 秒。未点击模拟买入。可预检不等于模型、成交时效或交易闭环已通过 |
| 次日 Paper 预检 | 10 月 11 日 Kalshi `61° or below`（0.55）和 Poly US `61 or below`（0.57），1 份 IOC：只显示带单引号的合约 ID，提交按钮禁用。这是不可解释错误，不能记为有效规则门控或交易通过 |
| 天气与信号 | 10 月 11 日无信号；10 月 10 日两平台 S1/S2/S3 显示 `STALE_KNYC_OBSERVATIONS`、`AUTO_TRADING_DISABLED`。Poly US 天气面板有 METAR/HRRR 图层与一版 CLI；官方小时温度和 NWS 预报缺失。市场概率与模型概率分开解释，本轮未看到可用模型概率证据 |
| Live 只读 | 双平台已认证、数据有效；票据显示“该市场未加入白名单”，提交禁用。预算均为累计 $5、已支出 $0、预留 $0、剩余 $5，并明示卖出回款不恢复预算。未记录真实账户余额和订单 ID |
| 回测入口 | 默认起止日 10 月 9 日，能打开已有 9 月 24 日 Poly US S3 结果（零成交、缺口原因明确）。开始日期控件未设 min/max；尝试填 10 月 1 日后仍显示 10 月 9 日，未据此判定业务日期限制。未点击运行；已回收区间拒绝由数据库水位和 `us_runtime.py:252–261` 代码确认，未冒充本轮提交复验 |
| 页面可用性 | 切换 10 月 10 日后天气区一度持续“读取所选市场数据…”，随后完成；浏览器控制曾超时。无 profile，无法把浏览器传输耗时归因为前后端某个函数；A11 不记通过 |

本轮截图只查看回测入口及 VM 只读输出，未截取 Live 账户余额。UI 和 VM 数据均为线上真实观察；无合成交易成功记录。

## D9 更正

原报告只查 `docs/acceptance/knyc-task6-2026-09-22.md`，遗漏根目录 [任务 6 验收记录](../../acceptance/knyc-task6-2026-09-22.md)。该文件已由 `314de4e` 纳入 main，记载 Paper 人工买卖、请求回执及账目核对。因此撤回“所有分支均无附件”的结论。附件同时明确回测零成交、结算未验、历史覆盖不足和页面延迟，不能据此提升为全部功能完成。

## VM 清理评估

所有 VM 文件及服务均未删除或停用。下表为人工评估清单，不是删除批准。目录大小来自 `du -B1`（占用字节）；单文件来自 stat（逻辑字节），时间不同、WAL 动态变化，不能逐项相加视为同一时点总量。

| 精确目标 | 大小（字节） | 新账目、回滚与部署影响 / 建议 |
|---|---:|---|
| `/var/lib/nice-weather/nice-weather.sqlite3` | 527,155,200 | 活跃 KLGA collector/dashboard/R2 共用，含历史与新增采集；保留，W5 先停用方案及恢复验证 |
| `/var/lib/nice-weather-knyc/feed.sqlite3` | 3,668,193,280 | 现役行情、天气、实际接收时间和归档证据；禁止作为旧库清理 |
| `/var/lib/nice-weather-knyc/results.sqlite3` | 2,222,813,184 | 现役 Paper/Live/回测账目；保留 |
| `/var/lib/nice-weather-knyc/results.sqlite3-wal` | 1,413,576,152 | 可能含尚未 checkpoint 的新账目；禁止直接删除，应在后续性能任务分析写放大和读事务 |
| `/var/lib/nice-weather-knyc/feed.sqlite3-wal` | 16,916,752 | 现役 feed WAL；禁止直接删除 |
| `/var/lib/nice-weather/backups/repricing-deploy-20260905T213222Z` | 4,653,056 | 旧回滚备份，未证明已有等价可恢复副本；保留待人工决定 |
| `/var/lib/nice-weather/backups/repricing-interaction-20260906T010411Z` | 24,576 | 同上；非现役 KNYC 账目，清理收益很小 |
| `/var/lib/nice-weather/backups/repricing-boundary-20260905T2332Z` | 61,440 | 同上 |
| `/var/lib/nice-weather/backups/repricing-v7-20260905T2110Z` | 8,192 | 同上 |
| `/var/lib/nice-weather/backups/repricing-performance-20260905T230204Z` | 24,576 | 同上 |
| `/var/lib/nice-weather/backups/repricing-time-label-20260906T012105Z` | 24,576 | 同上 |
| `/var/lib/nice-weather/quarantine/20260901-unified-store` | 4,096 | 隔离目录占用很小；不据目录存在推断旧数据库仍在，不执行删除 |
| `/var/lib/nice-weather/retention-20260922` | 270,336 | 历史回收证据；保留审计能力，删除不解决主要容量问题 |
| `/var/lib/nice-weather-trading` | 3,551,232 | 旧回测数据集及 requests；保留 KLGA 历史/回滚能力 |
| `/var/log/journal/2aa952709a834b9692cbc7b326705520.nice-weather-trading` | 8,392,704 | 项目 namespace 日志；含异常证据，已有容量控制，未提议删除 |
| `/var/log/journal/2aa952709a834b9692cbc7b326705520` | 288,440,320 | 系统共享日志，非项目专属；不纳入项目批量清理 |
| `/opt/nice-weather/knyc-releases/59e0b3e32f3cdb5ea29801ddf346a32de88481c9` | 未完成递归大小统计 | 唯一发布子目录且 `knyc-current` 正指向它；目录名与实际 manifest SHA 不同，禁止按旧名称删除 |

`/opt/nice-weather` 全目录 du 扫描耗时明显，先前 20 秒超时，后续较长扫描经 SSH 内建 Ctrl+C 中断；只取得 `.venv-trading` 1,261,477,888 和 `.playwright` 687,366,144 字节，未假定它们无依赖，未列为删除目标。改用小目录 du 和文件 stat 完成上表。

旧服务精确对象：`nice-weather-collector.service`、`nice-weather-dashboard.service` enabled/running，`nice-weather-r2-sync.timer` enabled/waiting、对应 `nice-weather-r2-sync.service` 为按需 oneshot；`nice-weather-market-stream.service`、`nice-weather-runner.service`、`nice-weather-sandbox.service`、`nice-weather-backtest.service` 已 disabled。前三类活动服务仍依赖 `/opt/nice-weather/repo` 与共享 `.venv`，美国运行时仍引用旧 worker（R7），CI 的 fixture-dashboard/frontend 仍使用旧 Dashboard。停用与代码删除统一归 W5；不删其数据库、运行环境或部署单元。

结论：未发现经本轮证据证明可安全删除且有显著收益的旧发布包。六个历史备份合计不足 5 MB，建议保留；主要容量问题在现役数据和 WAL，应在 W0/W5 分析，不能以删库替代。若用户仍要删除历史备份，须对上表六个精确目录另行确认，并先核实回滚副本。此次仅完成清单及影响评估。
