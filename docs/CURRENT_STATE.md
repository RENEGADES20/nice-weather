# 当前状态

## 2026-09-22 任务 1：跨日市场与天气独立组件（本地，未集成部署）

新增双平台历史/当天/已挂牌未来日目录、按市场日查询天气与赔率、所选合约公开报价；独立选择器与交互天气组件恢复分钟差值、同有效时刻预报修订、纽约显示时间及缺口分段。真实 9 月 19 日、22 日、23 日合约读取成功，历史天气/价格图本地验证；数据缺口如实保留。实现与接线说明见 TASK1_MARKET_WEATHER.md。仅任务 1；主入口由任务 6 接线，本次没有 VM 操作或修改实盘未提交代码。
## 2026-09-22 任务 2：持续信号与历史研究，独立交付待集成

独立分支 `codex/knyc-continuous-signals` 基于 7303091 开发。S1/S2/S3 保留既定阈值、数量优化及每日机会；自动交易关闭、机会消耗及订单待对账均继续天气判断，候选和执行状态分别输出。信号变化复用现有 inputs 日志并与 Paper 检查点原子提交，原生订单反馈更新机会状态。历史来源时间研究与真实接收回放分开，缺失 received_at 保持 null，模型生成时间与训练数据截止分别保存。

本地 KNYC/HRRR 9 月 1–13 日研究有 100 个可用时点，S1/S3 各 53 次天气条件成立；历史时点前合约目录缺失，候选仍为零，S2 跨档无法确认。不将后来合约或当前模型倒灌历史，不改变生产模型。详见 KNYC_TASK2_SIGNALS.md 及 acceptance/knyc-task2-historical-signals-2026-09-22.json。原 9 月 21 日回放记录已复核，其观测/HRRR/规则缺口与结算目标不匹配分别保留，未重跑远端日志。

本地交易测试 202 项通过，另 2 项 Redis 恢复测试因本机未安装 redis-server 无法执行，交由已配置 Redis 的 CI 核验；最终定向测试结果见任务交付说明。任务 5 未提交改动保持不变，任务 6 负责集成与发布；本轮没有 VM 操作、安装依赖、批量删除或真实资金请求。
## 2026-09-22 任务 3：市场价格近似模拟（独立分支，未部署）

`codex/task3-paper` 为 KNYC Paper/新回测增加 market-price-v1，复用 Nautilus 订单、成交及账户检查点。人工模拟不再依赖双边完整深度、30 秒报价时限、已知费用或固定 5/20 美元额度；未知费用默认成交额 1%，滑点默认 0，合约歧义继续拒绝。新增服务端预检、权益序列和独立 PaperTicket；主页面接线交任务 6，未触及 VM 或任务 5 未提交代码。

相关回归 30 项通过；Chrome 双平台买卖、资金与持仓变化通过，开发样例明确标注且使用真实模拟后端。真实 9 月 20 日报价来源核对及回放完成，两平台历史合约均因原记录规则歧义拒绝成交，切片缺预测事件，不构成策略收益验证。接口、测试及剩余项见 TASK3_PAPER_HANDOFF.md。PR/CI 以最终交付记录为准。
## 2026-09-22 任务 4 独立回测工作区待集成

已实现独立回测参数/历史运行/账户曲线/赔率图/策略标记和交易定位组件，保留主入口由任务 6 接线。18 项 Python 回归、4 项浏览器用例、TypeScript/Ruff 通过。本机真实公共采集片段通过任务 1–3 模块联调，双平台共四次回测（含网页提交 S3）参数、账户和曲线末值一致；缺 prediction 均明确 no-data、零成交。真实成交/最终结算标记仍缺样本；正式入口集成及部署未执行。详见 acceptance/task4-backtest-ui-20260922.md。
## 2026-09-22 任务 5：双平台 Live 本地交付，待任务 6 集成发布

承接下节未提交原生客户端后，已增加独立双平台 Live 运行器、原生订单/成交/持仓整体对账、私有成交流与 REST 恢复、账户资金口径、版本化风险配置、累计平台预算及独立账户/票据组件。复用现有 requests/results/transport 数据库和命令 API；未修改策略、Paper 或回测业务、终端主入口和部署配置。自动及人工 Live 初始关闭。停止策略保存禁用状态并撤销对应策略挂单，人工挂单保留，断线后的撤单继续处理。

本地交易回归 195 通过，2 项既有 Redis 测试因本机缺少 redis-server 失败，留待仓库 CI 既有 Redis 环境验证。组件真实浏览器流程通过（模拟传输），前端类型检查和构建通过。2026-09-22 最终原生代码双平台真实只读复验均 authenticated/reconciled=true，Kalshi 18 次 GET、10 笔平台历史订单、零当前持仓；Poly US 8 次 GET、订单和持仓为空；私有流均已连接，真实写请求为零。完整原文保留本地忽略目录，摘要见 acceptance/task5-live-2026-09-22.json。

已知平台限制：Kalshi 实际 KNYC 规则审计仍有结算/修订歧义，相关新增风险继续阻止；账户照常显示。Poly US 断线漏失的成交若无订单关联证据，保留未知与预算预留；不得按价格时间猜测。完整市场日/结算后恢复与用户实盘交易未验收。任务 6 接入说明见 TASK5_US_LIVE.md。本任务未上传、安装、重启或部署 VM，未执行真实提交、撤单、改单或资金操作。下节为被本次实现替代的开发历史。

## 2026-09-22 原生订单接线开发中，尚未发布

原生批量订单报告已开始接入持久化请求与账户历史；Kalshi 当前/历史订单均须存在且归属匹配，Poly 对已知订单逐笔读取最终状态。注册市场内的外部未归属订单、缺少回执的未知提交或重复归属均拒绝对账成功。新增 Kalshi 原生报告测试验证终结订单保留部分成交、缺失历史和未知提交不产生空成功；账户/订单测试共 8 项通过，Ruff 通过。Poly 批量路径尚需专门验证；完整成交、持仓及预算对账仍未实现，reconciled 保持 false。

恢复准备：USRest 增加账户范围持久化请求读取，保留 submitting/unknown 及原始证据；超过 10000 条明确中止，避免以截断列表完成对账。单笔查询复用同一读取路径。18 项传输回归再次通过，新增其他账户隔离检查；后续需将此读取接入原生批量报告与未知订单恢复，当前尚无完整恢复成功证据。

补充验证：账户与双平台订单身份回归 7 项通过；Kalshi 撤单增加 market_ticker 自动路由，路由参数参与持久化请求身份，签名继续使用不含查询参数的路径。18 项 US 传输测试通过，含签名校验、重复撤单不重发及同请求换市场拒绝。依据 2026-09-22 读取的官方 https://docs.kalshi.com/llms.txt 中 Cancel Order (V2) 说明；对应接口 https://docs.kalshi.com/api-reference/orders/cancel-order-v2.md。未调用真实撤单接口。

本地新增 USExecutionClient 的提交、查询与单笔撤单路径，复用现有签名传输及持久化预算。已有提交记录禁止重复发送或误报拒单；账户刷新失败、断线和未知提交撤销可交易状态。查询与撤单校验本地订单、持久化请求、交易所回执及市场的一致性。HTTP 提交回执不生成虚构成交。只读客户端原有隔离和报告转换回归 23 项通过，随后新增未知请求检查及账户回归 5 项通过。

这些改动尚未接入生产运行器，批量报告、私有成交流、完整恢复对账、余额映射与终端风险配置仍需完成；reconciled 尚无成功置真路径，不能据此启用 Live。VM 仍为下述 348123a。未提交真实资金订单，未改变 VM 配置或删除文件。

## 2026-09-22 回放审计已部署并完成真实数据核验

PR #70 七项 CI 全通过并合并 348123a4f11fb5acb2c3427ca1aad3bd708534bb。08:07 UTC 核验 VM 74 文件哈希、健康版本一致、六服务 active，两个 Paper 保留启用、各 100 现金、零成交；剩余容量约 2.07 GB。只更新应用，未改变基础设施，旧资源保留待审核。浏览器连接重建后恢复同一已授权 SSH 来源并核对基线，未重复部署；固定前台性能采样需重新开始。详见 acceptance/knyc-replay-audit-deployment-2026-09-22.json。

最终代码上的双平台 9 月 21 日真实回放已完成。Kalshi 582 条、Poly US 228 条完整决策变化与持久化日志计数一致，均零成交。各 148 条概率事件仅覆盖末尾约 76 分钟；盘口与合约出现约 22.4 小时接收空缺。六条平台结算输入不代表完成持仓结算链。逐策略拒绝分布、完整日志哈希及恢复证据见 acceptance/knyc-real-replay-audit-2026-09-22.json。真实成交闭环、终端性能、完整市场日仍未通过。

下一步继续完整 Live 原生客户端、账户配置和对账；当前 USReadOnlyExecutionClient 仍不是可写接线交付。官方账户文档原文已保存于忽略目录 var/knyc/rules-audit-20260922/：Kalshi 余额跨分区汇总，实际响应含 0/1/2/3 四个分区，市场 exchange_index 才是路由依据；Poly buyingPower 含证券估值与挂单影响，不能直接冒充现金。私有订单流快照仅涵盖开放订单，需结合持久化执行证据和 REST 核验，缺少成交归属不得制造记录。尚未修改 Live 实现，未启用或发送实盘订单。

## 2026-09-22 引擎热点修复已部署，回放过程证据补齐中

PR #69 七项 CI 全通过并合并 59b8eabc61033a50ab6e69136cb5f75a5db1ed06，07:40 UTC 已核验 VM 74 个文件哈希、健康版本一致、六服务 active。只重启终端、回放及两个 Paper；两个模拟账户仍启用、各 100 现金且零成交。VM 配置未改，剩余容量约 2.10 GB。详见 acceptance/knyc-depth-deployment-2026-09-22.json；性能和完整市场日仍未通过。

本地回放补充实际接收覆盖、最大接收间隔、概率输入原因与逐策略决策变化。完整变化信号连同 feed seq、接收时间保存在现有 inputs 表，按批次写入；每五秒至多更新一次紧凑进度，运行中断后明确失败，不重复执行已有请求。终端区分请求区间与实际输入范围，旧记录明确未保存过程审计。两项原生回放测试覆盖局部数据、外平台排除、完整决策归属和中断恢复，构建/Ruff 通过；尚待审查、CI 与部署后真实回放核验。

## 2026-09-22 真实回放热点优化待发布

一分钟真实 Kalshi 接收事件（443 条）剖析显示，重复按价位扫描成交和重建不参与当前策略的 NO/历史日期盘口占用明显。候选实现将成交在单次计算内汇总，覆盖深度更新、FOK 预检及持续重评；不跨原生成交或重启保存缓存。执行版策略只构造当前气候日候选 YES 盘口，冻结版保持既有输入范围。49 项策略/原生交易/结算回归通过，补充的买卖方向、合约、价格及恢复一致性检查通过，Ruff 通过。

现有 VM 上交替运行三组同段真实数据对照，中位耗时从 4.6566 秒降至 2.8443 秒；全部六次结果保留，除内部 evaluation_signature 外的完整快照一致。候选仅在独立诊断进程执行，服务仍运行 d0383ff；详见 acceptance/knyc-depth-profile-2026-09-22.json。该证据不代替真实成交、全市场日或终端绘制验收。

已完成 9 月 21 日双平台真实事件回放，两边均零成交、权益 100。请求窗口为 24 小时，实际各 148 条概率事件仅覆盖末尾约 76 分钟；Kalshi 全部 unavailable，Poly US 118 条 unavailable，最终三策略均因 STALE_KNYC_OBSERVATIONS 拒绝。回放尚未保存中间决策审计，不能从最终原因推定全程拒绝分布。见 acceptance/knyc-real-replay-2026-09-21.json；完整市场日及真实成交结算闭环仍未通过。

## 2026-09-22 终端分段计时已发布，30 分钟资源观察完成

PR #68 七项 CI 全通过并合并 d0383ff4c9bf293b2ce55d77959c35e9a77cec9e，07:02 UTC 已核验部署，74 文件哈希与健康版本一致、六服务 active；只重启终端。新增本地命令反馈计时，分离 React 提交与绘制帧等待，并记录焦点/可见性。真实可见且有焦点样本仍出现总耗时 946.8ms，其中提交 3.9ms、等待帧约 943ms，性能尚未通过。见 acceptance/knyc-feedback-deployment-2026-09-22.json。

旧部署 697be59 的 31 个分钟资源样本已完成，零采样错误、六服务 PID 稳定，最大待处理延迟约 0.977 秒。连接复用后空闲回放 write_bytes 增量为零，各服务 cancelled_write_bytes 增量均零；KNYC feed 数据库 30 分钟增加约 14.94 MB，长期容量仍待解决。详见 acceptance/knyc-resource-post-reader-2026-09-22.json。该观察不能代替完整市场日、最终结算或 Live 验收。

## 2026-09-22 队列连接及订单证据已部署，前台性能未达标

PR #67 检查通过后合并，06:28 UTC 核验 VM 精确版本 697be59c18d29f9a346bfe4b9a7632a9eb1f9175：74 文件哈希和健康版本一致，六个应用服务 active。该版本同时包含 PR #66 的发送前订单意图证据；两个 Paper 策略启用状态在重启后保留，仍无成交。未改变 VM 配置，剩余容量约 2.19 GB。见 acceptance/knyc-queue-reader-deployment-2026-09-22.json。

用户确认 Chrome 终端前台后，保留的 500 条真实消息绘制样本 p95=881.2ms，未满足 100ms 门槛。全部保留样本参与统计，控制台窗口未覆盖采样起点后的全部事件；详见 acceptance/knyc-foreground-baseline-2026-09-22.json。本地新增命令反馈及提交/等待绘制帧分段计时，尚未发布；不能将测量改进写成性能修复。完整市场日、最终结算、Live 接线与用户交易验收仍未完成。

资源诊断发现 rsyslog 对 /dev/console 的 Permission denied 导致反复 suspended/resumed 日志。未改系统日志权限或基础设施配置，长期容量仍待解决。06:29 UTC 启动当前版本 31 次分钟采样，输出 /home/hdharrison1206/knyc-post-reader-20260922-0629.jsonl，结果待核验。

## 2026-09-22 双平台 Paper 已启用，队列连接复用待发布

06:12 UTC 核对两个持久化 start 请求均 accepted，Kalshi/Poly US Paper 的 S1/S2/S3 均已启用，各 100 模拟现金、零成交；现有模拟限制为每策略预算 5、单档 5、当日 20 美元。当前 HRRR 尚未覆盖日末，三策略继续明确 no-trade；Kalshi 规则门禁未解除。Live 保持关闭，其用户测试限额仍为每平台累计 5 美元。启用及回执证据见 acceptance/knyc-paper-activation-2026-09-22.json，不能据此宣称成交或完整市场日通过。

订单意图证据 PR #66 七项 CI 全通过并合并 f52cc86b40a418f00db1664dc31b7c8759b93ced，尚待随下一版部署。当前本地优化复用 Paper/回放队列只读连接，不保留查询事务；新请求可见性、只读权限及 WAL 截断测试通过。此前 VM 30 次查询测量见 acceptance/knyc-resource-post-upsert-2026-09-22.json：短连接有 write_bytes/cancelled_write_bytes 计数，持久连接两者均零；这些是任务 I/O 计数，不能当作设备实际写入量。

## 2026-09-22 Poly US 天气规则已部署；订单意图证据待发布

规则 PR #65 七项 CI 全部通过，06:07 UTC 验证部署 d1365291359f616a657f2104b25b45964d4462d0：74 个文件哈希及健康版本一致，六个服务 active，实时采集的六档 Poly US 合约全部 parsed。只重启受影响应用，HRRR 持续运行；磁盘剩余约 2.22 GB，长期容量与性能尚未验收。详见 acceptance/knyc-weather-rules-deployment-2026-09-22.json。两个 Paper 账户仍关闭策略、各 100 模拟现金；当前数据拒绝原因为 HRRR_DAY_END_NOT_COVERED。

传输层新增发送前原子持久化原始正文、有效期、归属及规则证据；超时恢复不重复提交，旧历史缺失证据保持缺失。23 项传输/预算/原生只读测试通过，Ruff 通过；该变更目前仅本地，完整 Live 对账与交易接线仍待完成，未发送真实订单。

### 天气规则依据

取得并核验官方 TC 温度产品认证，明确来源报告精度、首次正式发布及修订处理；结合 NWS 标准时气候日和实际 KNYC 合约加入严格规则匹配。今天六档真实响应均通过，旧历史接收记录保持 no-trade，不倒填审计知识。API 结束时间与观测日界独立：真实 endDate 为次日 09:00 UTC，气候窗口仍为 05:00 UTC。31 项规则/数量/策略测试及 Ruff 通过。详见 KNYC_US_RULES_AUDIT.md 与 acceptance/knyc-poly-weather-rules-2026-09-22.json；Kalshi 未解除门禁。

原生只读账户 PR #64 修复进程工厂污染后七项 CI 全部通过，已合并并部署 840e922d316a7b3b27eefb36cd344517a3260429。05:46 UTC 验证 74 个文件哈希、健康版本、六个服务和原生模块导入通过；仅重启终端。完整可写接线与对账仍未完成，证据见 acceptance/knyc-native-account-deployment-2026-09-22.json。

## 2026-09-22 原生只读客户端与 VM 数量规则发布

USReadOnlyExecutionClient 已通过原生消息总线、Portfolio 和 Cache 应用账户事件，真实 Kalshi/Poly US 只读验证均通过，订单提交尝试为零。首次全量 CI 揭示 Paper 改变进程级 AccountFactory；新增混用拒绝检查，并按独立进程边界验证，53 项账户/报告/传输/发布测试通过，覆盖旧快照拒绝、刷新中断后保留账户事实及只读命令拒绝。完整订单报告对账明确失败，不能以空报告当作已完成；现金映射仍未知、Live 仍关闭，尚未交付可写客户端或终端账户控制。真实只读证据见 acceptance/knyc-native-account-bus-2026-09-22.json，记录保留实际执行的源码哈希，不替换成后续隔离修正版本。

PR #63 七项 CI 通过后合并，数量规则修复 55dd640216e83499d8c1c699a28a336aa4b9186a 已于 05:19 UTC 核验部署：73 个文件哈希、健康版本一致，六个服务 active；只改 us_markets.py，HRRR 未重启。见 acceptance/knyc-quantity-deployment-2026-09-22.json。

此前 298f7e9 固定版本的 31 个资源样本覆盖 30 分钟，无错误、服务 PID 稳定；Kalshi/Poly Paper 的 write_bytes 平均增长 354,496/282,432 bytes/s，对应 cancelled_write_bytes 为 154,812/160,419；空闲回测为 57,924/56,324。最大采样待处理延迟 0.957 秒。此前“物理写盘”用词不准确：这些为内核任务计数，短连接可产生随后取消的写入，不能直接认定设备实际落盘量。写盘放大、长期容量与完整市场日仍待验收。原始文件及哈希见 acceptance/knyc-resource-post-upsert-2026-09-22.json。

## 2026-09-22 原生账户事实（本地只读验证，未完成实盘接线）

加入 Nautilus AccountState / MarginAccount 只读构造：平台金额保留为精确字符串；尚未核验的现金/冻结映射保留未知，balance_total/free/locked 均返回 None，不填零、不放行风险。私有 HTTP 响应小数直接按原文字串解析，避免进入浮点后丢失末位。44 项账户/订单持仓报告/传输测试及 Ruff 通过。

已有凭据的双平台真实只读认证和原生账户对象构造成功。Kalshi 返回 15 笔非 KNYC 历史订单及 28 笔成交，当前持仓为零，历史订单均缺原始有效期字段；Poly US 无订单和持仓。仍为 reconciled=false、balance_mapping_status=unverified，订单提交尝试为零。原文只保留忽略目录，摘要见 acceptance/knyc-native-account-readonly-2026-09-22.json。LiveExecutionClient、完整余额映射及终端控制仍待完成，不能把原生对象构造视作完整接线验收。

## 2026-09-22 Poly US 数量规则（已发布，原本地验证记录）

官方 Create Order OpenAPI 明确 minimumTradeQty<1 的市场支持小数份数，移除旧的通用整份帮助页冲突原因；缺失数量不再默认 0.01，非小数市场使用整数步长。六档已记录真实市场的重新归一化通过，天气观察窗口、舍入和修订门禁保留，仍全部 no-trade。35 项数量/策略/传输/结算测试及 Ruff 通过。规则证据和剩余项见 KNYC_US_RULES_AUDIT.md；部署事实见顶部更新。

## 2026-09-22 检查点页复用（已部署，等待实测）

最新资源观测 31 个样本、无采样错误；当前 a9f5c53 段 14 个样本中，Kalshi/Poly US Paper 物理写盘仍为 963,434/722,721 bytes/s。完整摘要保留 VM，记录见 acceptance/knyc-resource-post-readonly-2026-09-22.json。不能将只读轮询和 WAL 闲置空间限制视为写盘放大已解决。

Paper 检查点由 INSERT OR REPLACE 改为冲突时原位 UPDATE，保留完整内容、提交频率、原子事务和原有恢复格式。独立 SQLite 实验中 168,312 字节状态连续提交 50 次，WAL 从 9,076,392 降至 597,432 bytes；此数字仅为本地合成实验。29 项相关测试及 5 项发布测试通过，包含大检查点的 WAL 页复用上限、最新游标恢复、账户恢复及视图更新失败时检查点/回执整体回滚。Ruff 与最终提交七项 CI 通过，PR #62 合并 298f7e90114bcdd793d1764e6f16268ea9e3ea16 已于 04:40 UTC 原位部署。73 个文件哈希及健康版本一致，六个 KNYC 服务 active；两个 Paper 仍为 100 美元、零成交、策略关闭。仅重启四个受影响服务，采集未重启；实际写盘降幅等待同方法采样，见 acceptance/knyc-checkpoint-deployment-2026-09-22.json。

## 2026-09-22 Live 原生金额精度（开发中）

独立 Live 进程的 USD 初始化模块已实现并通过 8 项测试：显式核验账户精度、同时注册 Python/Rust 币种、原生账户事件序列化不丢失 0.0001 USD，同进程禁止切换平台/精度，Paper 父进程仍为 USD2。此模块尚未接入 Live worker；账户余额/抵押映射、原生客户端和真实完整对账继续待实现。具体边界及官方余额字段依据见 US_ADAPTER_ACCEPTANCE.md。

## 2026-09-22 WAL 保留空间修复（已部署，持续容量待验收）

共享 KNYC 存储连接及旧 WeatherStore 增加 16 MiB 的 `journal_size_limit`，仅限制 SQLite 完成检查点并重用 WAL 后保留的闲置空间；读事务仍需要的页面可超过该值，不能当作总磁盘硬上限。实际 20 MiB 写入测试覆盖旧读快照继续可读、释放读事务后正常回收、全部记录保留及重新打开后的完整性检查。9 项定向测试和七项 CI 通过，PR #61 合并 `a9f5c53ade8f81369e12a851268341b907e238f5` 已部署，72 个文件哈希及健康版本一致。旧服务实际导入路径和修复前哈希核验后，单独原子更新 WeatherStore，三个常驻写入服务恢复 active；其余旧代码未升级。详见 acceptance/knyc-wal-deployment-2026-09-22.json。

Paper 检查点的 config 包含 feed_cursor，导致游标变化触发完整原生状态重写；后续减少此写入时必须同时证明游标、账户事实及恢复行为一致，当前未跳过这些持久化事务。

## 2026-09-22 容量保护与恢复（实施中）

03:31 UTC VM 系统盘仅剩约 13 MB，KNYC 采集触发 1 GiB 余量保护并反复启动。短暂停止项目数据库访问后，旧库 WAL 检查点完成；单个已轮转日志无损压缩，解压哈希一致，可用空间恢复到 2,406,498,304 bytes。旧库历史和备份保留，VM 配置未变。维护期间存在采集缺口，长期容量与完整市场日验收仍未通过。详细现场记录见 acceptance/knyc-capacity-incident-2026-09-22.md。

PR #60 七项 CI 通过，合并 `1e8a76df9a42e1e9c6d4eece5ecc2e94dbfaf604` 已于 03:45 UTC 原位部署；72 个文件哈希全部匹配，健康版本一致，六个 KNYC 服务 active。只重启终端、回测和两个 Paper 服务。03:43:33 UTC 已恢复此前暂停的 KNYC 采集。SSH 上传认证失败经普通 Retry 恢复，未创建或配置密钥。部署证据见 acceptance/knyc-readonly-poll-deployment-2026-09-22.json。

## 2026-09-20 写盘基线与只读轮询（实施中）

PR #59 七项 CI 通过，合并 `a2914b38eb28128cf6e4b9ff3cd5233fcbfc38ca`；VM 仍为 #58 的 `23b540b`，待补齐新报告模块的发布允许清单后一起原位发布。30 分钟资源基线共 31 个样本、无采样错误，各服务均 active；跨部署分段后，当前版本 18 个样本覆盖 17 分钟，最大采样待处理年龄 0.622 秒，最低可用空间 1,678,753,792 bytes。两个 Paper 进程物理写盘分别约 438,664 和 402,852 bytes/s，空闲回测 worker 约 64,680 bytes/s，尚不能视为性能达标。原始文件保留 VM，摘要见 acceptance/knyc-resource-baseline-2026-09-20.json。

检查发现请求队列轮询以可写连接打开 SQLite；改为只读连接，避免空闲读取参与写库关闭/检查点。修改后还需同条件测量，不能预先宣称降幅。Nautilus 亚分币隔离进程实验确认默认 USD 反序列化将 0.0001 舍入为 0.00，同时注册 Python/Rust USD 四位精度后精确保留；此初始化将限定未来独立 Live 进程，不修改现有 Paper 进程币种注册。

## 2026-09-20 US 原生账户报告（实施中，尚未部署）

开发分支增加双平台 Nautilus OrderStatusReport、FillReport、PositionStatusReport 转换；严格校验净仓方向、市场、实际费用、小数精度和来源时间，尚未连接 LiveExecutionClient。核验官方 YES 价格口径后修复 Poly US NO 指令限价转换。00:28 UTC 私有 WebSocket 真实只读认证成功，收到 eof=true 的零订单快照；25 秒窗口无成交消息，余额/持仓同步和断线恢复仍待完成。不能将该快照视为完整账户对账。原文仅留忽略目录 var/knyc/private-stream-20260920。

## 2026-09-20 US 结算闭环（已部署，完整验收待完成）

PR #58 七项 CI 全部通过，合并 `23b540bc781b51905f623f67497a9a092274481c` 已原位部署，包含 #57 的合约拒绝原因修正。健康版本与 manifest 一致，71 个文件哈希无差异，六个应用服务 active。00:25:36 UTC 两个 Paper 账户均 running、100 美元、零成交、策略关闭；双平台 9 月 19 日结算待办已持久化，尚未最终结算。资源基线已有 13 个样本、无采样错误，最新采样队列待处理年龄为零；这段基线跨部署版本，后续必须按 SHA 分段分析，不能作为最终完整市场日。部署证据见 acceptance/knyc-settlement-deployment-2026-09-20.json。

00:12 UTC 后续真实重试成功：Poly US 只补查此前限流的一档，双平台各六档（含 YES/NO 共各十二个原生结算值）全部完成。账户经恢复保持 100 美元、零成交，已结算档位未重复支付。API 调用计数与完整结果见 acceptance/knyc-us-settlement-complete-2026-09-20.json；此前部分结果单独保留。VM 上已启动 30 分钟只读资源基线观测，未作为最终版本完整市场日验收。

PR #57 七项 CI 已通过并合并为 `6f08563dee858038b257db34fa2f2cce16be81a1`；当前 VM 仍运行 #56 的 `3bcbc34`。新增持久化结算待办，市场日切换与采集重启后继续追踪已发现合约。Kalshi 必须返回 finalized 与一致结算金额；Poly US 必须 resolved、EP3 expired，并与独立 settlement 接口一致。真实收到的原文、capture_id 和哈希进入共享 Paper/回放转换，Nautilus 原生结算持仓；重复结果不重复入账，结果变化或原生精度不足时停止并要求对账。

使用 9 月 18 日真实公开合约与当前收到的结算响应完成本地采集→Paper 检查：Kalshi 六档全部结算，Poly US 五档完成、第六档遇 429 保持待办；两账户仍为 100 美元且无成交。本次没有当时的可执行策略信号，不能作为完整策略收益或有仓真实数据验收。已修复重试重复查询已确认档位的问题。43 项定向测试通过；另有当前行情快照排除旧市场盘口、保留历史查询的检查。仅裁剪前台快照，未删除历史数据。

## 2026-09-19 三策略执行版 v2（已部署，完整验收待完成）

PR #56 最终提交七项 CI 全部通过，合并 SHA `3bcbc341cf7d0df5dccd20117fb76635afc7e31b` 已原位部署。健康版本一致，71 个文件哈希全部匹配，六个受影响服务 active。两个 Paper 账户保持 100 美元、零成交、策略关闭，执行版本均为 v2。线上 Poly US 已产生校准概率；Kalshi 保持 `UNVALIDATED_SETTLEMENT_MODEL`，合约歧义门禁仍在。23:53 UTC 可用空间约 1.7 GB，无 VM 配置变化。证据见 acceptance/knyc-v2-deployment-2026-09-19.json。PR #57 增加只读资源观测脚本，尚未作为完整市场日证据运行。

PR #56 首次提交七项 CI 全部通过；追加费用修正后重新检查。Poly US 使用逐订单累计半偶数舍入，Nautilus 模拟费用累加器随账户检查点恢复。其 API 最小数量 0.01 与官方整份规则冲突，保留歧义门禁。Kalshi 新费用算法已按官方例子验证，账户余额精度未核验，尚未用于生产放行。

本轮目标与边界见 KNYC_STRATEGY_DELIVERY.md。保留 VM 规格、磁盘和基础设施配置；已实现相邻双档数量优化、策略关闭时的信号展示、有效输入变化重评、成交限次恢复、重复输入去重及短事务回放。旧 v1 函数及旧触发记录保留。现有 Paper 账户升级仅增加执行版本，保留账户事实与已有触发限制。

复用现有 KNYC 观测与离线 HRRR 训练 64 棵浅树的策略支持模块，724,302 bytes JSON 可用标准库推理。2026-01-01 至 09-10 检验 252 天、2,520 时点；结束事件 Brier 0.08021。历史 HRRR 仅按名义起报加两小时关联，缺真实 received_at；标签仍受归档 CLI 修订限制，此结果不代表可执行回测。模型当前仅允许 nws_cli 口径，Kalshi 的 Weather Company 规则核验尚未解除门禁。

用户授权的两个现有凭据已通过真实 GET 认证；补读 Kalshi 当前/历史订单、成交、持仓及 Poly US activities。私有原文仅保存在忽略目录 var/knyc/account-history-20260919；不输出密钥、不发送真实订单，尚未完成 Nautilus Live 原生对账。传输层已增加每平台累计 5 USD 预算，发送前事务预留，未知订单保留预留，卖出不恢复预算。

本地交易测试 103 项通过，2 项 Redis 恢复测试因本机无 redis-server 未执行；定向 36 项通过，终端两项浏览器回归通过。Linux CI、规则最终核验、Live 接线/账户控制、真实模拟闭环、线上性能及完整市场日仍待完成。SSH 普通 Retry 已恢复，23:02 UTC VM 剩余约 1.7 GB，五项 KNYC 后台服务均 active；无规格或磁盘修改。

## 2026-09-19 KNYC 终端迁移（开发中）

最新运行：PR #54 七项 CI 通过，`6e7098113b0a713e1ab9f368c651efaeb537966a` 已原位部署，65 个文件清单与健康版本校验通过。只重启终端、两个 Paper 和回测 worker，采集/HRRR 与四个旧服务保持 PID。两个模拟账户均为 100 美元、零成交、策略关闭，游标已追平采集的 72,133。三组五秒短样本中，Paper 写盘量由 16,846,848 降至 12,513,280 bytes，共享事件 105→119；每事件写盘约减少 34%，不等于长期性能达标。实际证据见 acceptance/knyc-paper-io-2026-09-19.json。

此前 PR #52 修复重连后 CSRF 未刷新、旧网络提示残留及旧时间戳快照引起图表崩溃。实际执行 #52→#51→#52 回退与恢复，65 个文件清单、健康版本和公网自动重连通过；九个非终端服务保持原 PID，采集游标 53,686→59,073→60,230，两个空 Paper 账户均保持 100 美元、零成交。恢复后 Kalshi 模拟 start/stop 均收到 accepted 回执，最终策略关闭。该结果不覆盖有订单/持仓时的恢复。

线上性能未通过：当前 Windows Chrome、1905×855 视口、现有网络与 VM 环境下，首屏单样本 6,178.4 ms；27 个缓存切档样本 p95 1,009.5 ms，139 个收到行情至渲染帧样本 p95 952.7 ms。浏览器调度与自动化影响尚未分离，不能把延迟全部归因于 React 或 VM，也不能用本地 mock 测试代替线上达标。原始样本见 acceptance/knyc-public-performance-2026-09-19.json；完整产品验收仍未通过。

用户批准后，已为现有 VM 服务账户保存 Logs Writer 和 Monitoring Metric Writer；后续十五分钟检查中，两类代理均无 PermissionDenied。SQLite 保活连接已部署，feed 和系统 journal 单次五秒写盘样本下降，Paper 写入与吞吐同时变化，不能推断整体性能达标。全库只读检查长时间阻止主库 WAL 回收；WAL 接近 1 GB、剩余约 2 GB 后，已停止该检查以保护运行空间。主库完整性未验证，已批准的旧数据库快照仍未删除，需受控校验方案后再执行。

产品目标：KNYC 单站、Kalshi 优先与 Poly US 其次、S1/S2/S3、统一桌面终端及用户控制实盘启用。Poly Intl 本轮排除。完整验收见 KNYC_TERMINAL_DELIVERY.md。

PR #54 写盘规则已部署：US Paper 对其他平台、天气和健康消息只推进游标；下一次本平台事件或每秒心跳保存该游标，崩溃时允许重新读取无账户副作用的消息。本平台合约、盘口、预测及交易命令继续立即持久化。减少空闲心跳写入未修改 50/200 ms 轮询间隔、FULL 同步或实盘门禁。Nautilus 12 项测试通过，补强本平台事件先于心跳落盘的断言后定向重跑通过。首屏补测仍为 7,025.1 ms，页面可见但前台无遮挡环境未获独立确认，不能将写盘下降解释为终端已达标。

PR #49 已接通 `https://niceweather.trade/terminal` 并复用现有 Cloudflare Access 登录，无新增密码；公网显示实时盘口、天气和模拟账户。PR #50 已将历史分页从 1,500 行降至 200 行，只投影顶档，完整原始深度仍保留。双平台初载和继续分页通过，VM 单次查询约 10–23 ms、18 KB；缓存与并行负载不同，不能当作 p95。实盘视图下下单与启动策略均禁用，未发送真实订单。

用户已批准删除所列缓存、安装文件及两份旧数据库快照；缓存和安装文件已释放 340,844,544 bytes，旧快照等待现役主库校验。VM 主库约 11.9 GB、syslog 约 1.39 GB；旧权限错误日志未额外删除，IAM 修复已获授权并完成。详见 VM_DEPLOYMENT.md 的存储修订。

迁移起点为远端 main fc83189ae168ffcdc386ee714a8fe89cb846930d；根目录 main 较旧且有未提交研究记录，已保留。PR #45、#46、#47 均已通过七项 CI 并合并；2026-09-19 16:42:11 UTC，版本 59e0b3e32f3cdb5ea29801ddf346a32de88481c9 已独立部署 VM，六个新服务运行。旧 KLGA 入口和数据继续保留。Nautilus 模拟/回测已接入，S1/S2/S3 共享选择函数参与模拟/重放，尚缺实际 KNYC 模型输入和原生实盘适配。Live 与真实合约规则门禁继续关闭；运行说明见 TERMINAL_OPERATIONS.md，部署事实见 [VM 记录](acceptance/knyc-vm-2026-09-19.md)。下方记录为历史事实，不作为当前授权限制。


当前实施证据：[本地验收](acceptance/knyc-terminal-2026-09-19.md)。PR #45 最终提交 `39924ee6` 的七项 CI 全部通过，已合并至 main `e00ac3cecb0cc262b86dafa6416d91785632f5b8`；其实现已包含在上述 VM 版本。

PR #46 增加 Kalshi RSA-PSS / Poly US Ed25519 认证传输、稳定请求日志、下单/撤单请求及有界账户读取，CI run 35427693363 全部通过。16 项 mock 测试通过，真实账户认证未验收。此模块尚未接入 Nautilus Live 或终端实盘启用；净持仓、成交归属和历史缺口对账仍待实现，API 继续关闭 Live。具体协议与缺口见 [US_ADAPTER_ACCEPTANCE.md](US_ADAPTER_ACCEPTANCE.md)。

本机公开前瞻采集从 2026-09-19 06:14 UTC 开始，最后记录到 09:01:34 UTC 后因电脑重启中断，已单独保存中断证据；15:18 UTC 恢复，在原库追加，新的有界采集截止时间为 2026-09-20 15:18 UTC。覆盖双平台盘口、METAR/NWS/CLI、KNYC HRRR，实际缺口不回填。状态文件 `var/knyc/forward-capture.json`，日志同目录。该采集不涉及账户、付费 X 或真实下单，不能替代完整市场日终端运行验收。原文与采集库未提交 Git。

PR #47 修复终端网络超时后的请求身份：发送前保存 ID 与完整指令，收到最终执行回执前阻止新增风险，原 ID 重试前查询确切回执。浏览器原生存储与 Web Locks 保留重启记录并协调多个标签页；撤单和停止策略不依赖行情新鲜度。后端按 ID 查询覆盖最近 30 条列表之外的请求。CI run 35453137500 七项通过，已随 59e0b3e 部署，不代表实盘验收。

用户已明确授权 `ssh.cloud.google.com` 中本项目已登录会话的访问及普通重连；具体目标与边界写入 AGENTS.md 和 VM_DEPLOYMENT.md。回环健康接口正常、未认证快照返回 401；公网路由与现有 Cloudflare Access 复用已完成。用户否决新增终端密码步骤，未创建或修改凭据。最近系统盘剩余约 2.3 GB（92% 已用），完整市场日验收前需继续评估容量。

已从现有本地 HRRR 空间块提取 KNYC 独立格点的 26,772 个预报周期（2,446 个 UTC 起报日期），另保留 126 个缺失/失败记录。产物为 `var/knyc/hrrr-archive-20260919/`，历史 received_at 为 null、executable 为 false；零新增下载，仅支持回顾性天气研究，尚未训练生产模型或解决 Kalshi 结算标签核验。

## 补充研究及部署记录

## 2026-09-15 KLGA / KNYC 错位档位假设 H4

- 按用户要求完成跨站最高温研究，文件在 `research_runs/2026-09-15-cross-station/`。2,336 个完整配对日，2024 年以后检验集 944 天（至 2026-09-10）。主口径为 KLGA 民用日 routine/SPECI 整度最高温代理与 KNYC 归档 CLI；同时保留双 CLI、双小时口径对照。
- 检验期主口径相关 0.9948，整数高点相同 27.8%，相差 ≤1°F 为 66.4%；去季节后相关 0.9763。双 CLI 相同率 23.2%，平均温差方向与主代理不同，不能忽略结算数据源。
- 15:00 已观测高点差 ≤1°F 的 596 天，相同率 40.1%、相差 ≤1°F 为 83.1%。弱风、晴空、无降水交集只有 5 个检验日，无法确认“大概率相同”。
- 以开发集固定的 15:00 锚点生成假想错位档位，各买一份时用户方向在上述 596 天双赢 50.5%、双输 8.7%、平均兑付 $1.418。尚未核实逐日实际档位、最终国际盘标签、历史收到时间和费后可成交成本，不能解释为收益或实际机会数。
- H4 仅为研究假设；已输出协议、全子组、反向/同档/单腿对照、置信区间、失败样本、图表与报告。脚本兑付及时间断言通过。未接入生产策略，X 调用及费用为 0。

## 2026-09-15 KNYC / Poly US / Kalshi 迁移首轮研究

- 用户授权继续研究平台迁移；新增独立目录 `research_runs/2026-09-14-knyc-migration/`。研究范围扩展至 Central Park KNYC、Poly US 与 Kalshi 的只读来源比较；生产 KLGA、Collector、Runner、R2 与订单权限不变。
- 归档 2020-01-01 至 2026-09-13 的 2,446 天 CLI 标签及 74,261 个常规 METAR/SPECI 去重时间点。2,370 个完整连接日中，1,348 天 CLI 与整度小时代理不同，22 天 CLI 更低；保留异常，不能直接沿用非负剩余升温标签。全部 CLI 原文版本与最终性尚未逐份验证。
- KNYC 观测基线完成 962 日、10,582 时点的时间顺序天气诊断：独立预测小时高点结束与 CLI 有符号残差。首次 90% 小时结束触发 942 天，平均概率 95.4%、事后小时结束频率 94.4%。缺 HRRR 与真实历史 received_at，不是完整迁移模型或可执行收益回测。
- 9 月 1–13 日 Kalshi 13 个最终温度与 CLI 一致；Poly US 12 个可取胜出区间包含 CLI，9 月 2 日 404。区间一致不证明精确值/修订规则永久等价。
- 采集 9 月 15 日事件的两组夜间三平台订单簿，共 46 个档位快照，计算 100/500/1,000 份显示深度 VWAP。Kalshi 窄价差未保证大规模成本更低；Poly US 部分档位有较大显示深度。两组快照不足以推断下午容量或全平台流动性排名。
- S1/S3 仅生成实际历史档位的天气诊断，缺历史 Ask 与真实接收时间时保持 no-trade；S2 未恢复真实跨档事件，保持 no-trade。后续依赖 CLI 异常/版本核验、KNYC HRRR、信号窗口接收时间与扣费检验。本轮没有启动后台持续采集。


## 2026-09-14 Repricing 分钟变化正式部署

PR #43 / #44 已合并，运行版本 `fc83189ae168ffcdc386ee714a8fe89cb846930d` 已部署至 niceweather.trade。Difference 六选项使用市场日一分钟变化、独立 °F/百分点双轴，移除 Weather update 与事件基线。Forecast 按同有效时刻修订计算。按来源缓存、空刷新复用、页签按需加载已生效。首次部署空刻度范围故障已回滚并修复，重新部署与历史档位复验通过。采集及 Paper 服务 PID 保持。

同 VM 后台热刷新中位数 423 → 57.5 ms；线上 Difference 22.6–71.1 ms。大历史冷载入仍有秒级长尾，手机线上模拟未生效；完整指标、边界及两次部署记录见 `docs/acceptance/repricing-minute-differences-2026-09-14.md`。


## 2026-09-14 Repricing 价格响应 VM 发布

Difference 已改为六选一的下拉框：三个天气温差和 METAR / Forecast revision / Hourly Temp 更新后的价格响应。以事件前一分钟 CLOB mid 为基线，显示百分点变化及首次 ±1 pp 的分钟级等待；缺口停止测量，未来快照不计算延迟。该展示不参与交易决策，也不能解释为因果或可成交延迟。线上功能与验收证据见 [部署记录](acceptance/repricing-response-2026-09-14.md)。

最后更新：2026-09-12

## 2026-09-12 KLGA 三策略独立历史研究

- 研究目录为 `research_runs/2026-09-11-klga-strategies/`，交付中文报告、六组 PNG、三张规则卡、天气训练与市场评价入口、纯函数研究信号接口。原有研究文件、生产接口、Collector、Runner、R2 服务和订单权限均未修改。
- 合格观测特征覆盖 1936-02-02 至 2026-09-10，共 31,577 天；观测与 HRRR 历史预报共同可用 4,337 天，始于 2014-07-30。原始分钟镜像在 2001 年夏季之前存在数值问题，该段保留原文并回退标准观测；小时尖峰和辅助分钟冲突有独立审计。
- 完整模型使用小型随机森林，连续年内日期、时刻、太阳高度和天气变量共同输入；不划分月度模型。天气训练不依赖市场日期，最近 60 个日历日校准，三个滚动版本按此前天气误差选用。966 个天气评价日中，7/30/90 天方案分别被选择 105/309/552 天；选用天数不代表实际重训次数，可复用较旧模型。
- 发现 593 个市场事件、592 个不同日期；589 个规则明确且已结算市场日中，587 天有各档历史价格，574 天可以与天气联合评价。WU/NOAA 合并主结果，实际胜出区间独立保留；发现两天小时温度代理与胜出区间不一致。
- 固定规则的候选证据排序为 S2 新高跨档、S1 相邻等份组合、S3 结束后单档。三者在历史概率口径下均有正毛收益及高于零的日期/七日区块区间；这是候选优势，尚未独立验证可实现收益。S2使用METAR/SPECI名义观测时间，不能直接解释为官方真实公布后仍存在同样折价。
- S1在共同日期上减少错档损失，但比S3付出更多成本；S3入场样本的模型概率偏乐观。天气对照中HRRR带来主要增益，风云雨的额外总体增益很小，区块区间包含零；更长纯观测基线也未改善预测。
- 9月6–10日单列新增回放检查，日期太少，不构成独立前瞻验证。后续优先使用现有采集链保留真实接收时间、官方小时表修订、HRRR版本、每档概率与模型版本，做冻结规则的Paper检查。本轮没有分析订单簿、成本、可成交能力或账户仓位。
- 可运行校验覆盖日期连接、训练/校准截止、更新方案选择、预报时效、夏令时、区间标签、缺失/异常概率、首次天气触发、每日唯一信号和组合兑付。完整结果见 [中文报告](<D:/ALLPROJECTS/weather forecast/research_runs/2026-09-11-klga-strategies/报告.md>) 与 [复现说明](<D:/ALLPROJECTS/weather forecast/research_runs/2026-09-11-klga-strategies/README.md>)。

## 历史记录

# 当前状态

## 2026-09-14 Repricing 分钟变化与性能修订

Difference 保留六选一下拉框：三个天气温差，以及 ΔPrice 与 ΔMETAR、ΔForecast revision、ΔHourly Temp 的双轴比较。采用市场日真实时间、固定一分钟变化，价格用百分点，天气用 °F；Forecast 修订比较新旧 capture 对同一有效时刻的值。缺失、过期、回退价格处断线。移除 Weather update、事件基线、Zoom to update 和自动延迟阈值。

Difference 本地切换不请求 Dashboard 重跑。分钟变化只重算受影响分钟及下一分钟；无变化刷新复用序列和时间轴，事件标记不重复设置。天气按来源失效、从新记录收到的分钟起重建，行情限定 event/bin 并使用既有游标索引。页签按激活状态计算，离开 Repricing 停止其展示刷新，返回恢复选择。最低 Streamlit 1.62，现有 VM 已兼容；没有新增数据库表、服务或缓存框架。

验收方法、性能数据和发布门槛见 `docs/acceptance/repricing-minute-differences-2026-09-14.md`。下面的事件响应设计为已被本次修订替代的历史记录。

## 2026-09-14 Repricing 价格响应发布

Difference 改为单选下拉框：保留三个天气温差，以 METAR、Forecast revision、Hourly Temp 更新后的价格响应替代三个价格减温度选项。事件前一分钟 CLOB mid 为基线，绘制百分点变化，标记首次绝对变化达到 1 pp 的近似等待时间；仅使用一分钟 as-of 输入，同分钟先后未确定，缺口与回退价格停止测量，下一事件或 60 分钟截断窗口。Forecast capture 更新的相邻分钟温度不能解释为同一有效时刻的修订幅度。未来快照不计算延迟。

本次发布仅改变前端展示，沿用线上 Trading 与采集版本；部署结果另存验收记录。

最后更新：2026-09-13 UTC

## 2026-09-13 Trading 交互与 Paper 账户修复（部署待验收）

换档即时更新本地选中状态，服务器先消费选择事件，价格历史在有界内存缓存后台读取，后续只查询新增行；冷历史查询不阻塞盘口与订单。图表使用增量末点更新，折叠 PnL/日历不重复绘图。盘口在同一 WebSocket 内增减订阅，取消固定 25 秒重连；每 20 秒公开快照校验由独立线程完成，失败后沿用过期暂停规则。

新增 Paper 现金编辑和账户重置。现金差额进入资金事件，曲线、日历与摘要扣除净资金流；重置开始新统计期间、停止策略、清空当期仓位委托，旧原生事实在 `paper_resets` 中只读审计。确认摘要携带账户修订，资金或委托变化后需重新确认；稳定请求 ID 和同一 SQLite 事务保证恢复及幂等。Live 继续禁用。

下单提前显示最小份数、剩余 $5 单档/$20 市场日额度、合约不可交易原因和原生拒单；修复 iframe 中确认弹窗超出实际可视区域的问题。单边盘口仍受当前 Nautilus 双边撮合条件限制，明确说明原因；缺少 Bid 不伪造估值。新增真实 Streamlit → SQLite 请求 → Nautilus Worker 的桌面/手机浏览器验收，并保留慢历史加载场景。测试覆盖从其他档位直接平仓，待处理操作统一锁定交易及账户按钮，防止尚未确认的请求相互覆盖。测试数据只留本地/CI。

此次独立工作树为 `.worktrees/trading-interaction`；另一任务的 Repricing 未提交改动保留在原工作树。PR、CI 和最终部署状态以本轮交付回执为准。本地 149 项 Python 检查、14 项 TypeScript 单元检查、16 项既有浏览器回归与两项原生全链路浏览器验收通过；两项 Redis 恢复检查由 Linux CI 执行。真实组件测试覆盖桌面与 390px 手机，12 次连续换档选中反馈为 1.1–6.2 ms，冷历史读取人为延迟 3 秒，仍能操作盘口与订单；该指标不代表公网盘口就绪时间。当前浏览器工具初始化失败，尚未取得 VM 本轮检查、清理和部署证据。

## Trading 统一工作台：发布与验收记录

导航统一为 Overview → Repricing → Trading → Backtest → System & Audit。Paper/Live 共用日期、温度区间、YES/NO、多档盘口、价格图与交易票据；Live 未接入账户且禁用操作。PnL 曲线和月度日历独立折叠，曲线缺口分段，纽约时区归日。

实时 Paper execution_version=3 使用 Worker 内存 WebSocket 多档簿和 Nautilus L2_MBP；回环只读接口提供展示，新增盘口不落库、文件或日志。结果库保存原生交易/账户事实及稀疏权益，常规每分钟一条。原生旧会话按旧规则核验恢复后转换检查点；独立旧 Paper 历史与 Backtest 余额不迁入。重启取消无法恢复排队位置的旧挂单，现金、持仓、成交与费用保留。

PR #37、#38 均经六项 CI 通过后合并。#38 的 Python 回归覆盖 88 项，浏览器覆盖 16 项，TypeScript 14 项；原生执行检查通过。旧账目与新版检查点重启均保留现金 99.95752、费用 0.00248、两笔成交、零持仓。生产大合约库扫描改为当前源记录指针与增量游标；362bf73 于 06:04:24 UTC 部署，恢复约 16 秒。默认市场按日期固定，所选合约与各合约草稿在浏览器保存，刷新时由服务器验证选择。最终部署哈希、完整 15 分钟观察与存储增量统一追加在 [功能 PR #37 的部署回执](https://github.com/RENEGADES20/nice-weather/pull/37)，避免为记录运行结果再发布一轮运行文件。规则详见 `docs/TRADING_WORKBENCH.md`，发布与备份证据见 [部署验收](acceptance/trading-terminal-2026-09-13.md)。

## 2026-09-13：Nautilus VM 部署验收

VM 已部署 `2f6f4e9cfbc7a9f2a01dff038884a89aeb97af27`，独立 Python 3.12.3 / Nautilus 1.231.0 环境。VM 原生 40 项测试、该提交六项 CI 全部通过；Trading、Backtest、模拟及回测服务已启用，LIVE 关闭。生产约 246 万条行情暴露的初始化扫描已修正；结果库保留无事务连接，解决只读 Dashboard 在 WAL 辅助文件消失时无法读取的问题。原采集、Market Stream、Runner 持续运行，02:38:23 UTC R2 同步成功。

生产网页完成模拟买入和全部退出：KLGA 2026-09-13、73°F or below、YES，5 份，0.009 买入、0.001 卖出，两笔成交，费用 0.00248，最终现金/权益 99.95752，持仓为零。同一会话 `33de5aa0-a436-42f1-b604-5e60a8ada485` 重启后现金、持仓、订单、成交、费用和最大回撤均一致，未重置账户。初始化期间曾出现超过 10 秒的快照空档；运行恢复后连续抽查约 0.5–0.8 秒。完整 KLGA 市场日稳定性仍未通过，PR #36 保持 draft。

用户审核确认的 66 个旧诊断日志已删除，释放 4.89 GB；部署独立环境后系统盘约 48% 已用、15 GiB 可用。活动日志、采集库、R2 数据和交易结果库保留。

VM 首次历史导出由独立 `nice-weather-acceptance-export.service` 后台运行，窗口为 2026-09-13 02:27–02:37 UTC；截至本次收尾仍在读取旧历史库，尚未生成数据集，因此 VM 回测页面的运行/比较复验仍待完成。本次导出临时磁盘副本约 4.3 GB，正常结束自动清理；不能将它误认成已批准删除的旧备份。390px 生产 Trading 页面宽度检查为 viewport/document 均 390，移动端截图已留存。

## 当前阶段

项目处于“KLGA 统一天气存储与阶段 A Shadow Runner 已部署，Tmax 重定价研究采集进入 schema v7，Repricing 数据与交互补修已部署、生产性能指标仍未达标”的阶段。

## 2026-09-05 最终部署记录

- 正式站点目视检查补充发现十字线底部标签使用库默认 UTC，而图例及普通轴刻度已用纽约时间。最终显示补修沿用同一个纽约时间格式化函数，并对主图/差值图实际 Canvas 标签做桌面及移动回归；旧构建两端均复现失败。该补修与本节发布记录一并提交，具体部署 SHA 另存服务环境及部署目录。

- PR #33 已合并，正式服务于 2026-09-06 01:04:34 UTC 发布 `0c88fcecfb7703cd065813444b5e5b403ad57b08`。四个主服务进程版本一致，原 R2 timer 恢复；schema 7 与 519080 条旧 event_kind=NULL tick 保留。发布后 CLOB 快照和 Gamma 均覆盖 33 tokens，报价及天气持续采集，首轮完整 journal 检查未发现数据库访问错误或 Traceback。
- 本轮仅修改前端、图表构建产物及文档。代码/配置回退记录为 `/var/lib/nice-weather/backups/repricing-interaction-20260906T010411Z/`，引用下述已通过完整校验的 pre-boundary.sqlite3；部署没有回滚或重写数据库。
- 最终代码 `2debf1e`（PR #33 合并前）通过五项 CI、14 项前端单元检查及 12 项桌面/移动浏览器检查。真实库验收通过两轮全部 11 个 bins、十次后台刷新、整日 Reset、双图缩放后继续刷新、全屏 API 退出、390px 窄屏高度 940/940、09-06/07 日期归属及断网恢复；浏览器错误为空，mount=1。未单独验证 headless 环境的 Escape 全屏退出。
- 同 VM 运行 headless 浏览器的最终实测：约 75500 个原始价格点，冷首屏 21.263 秒，已加载 bin 切换 p95 4.248 秒；最后一个 bin 的十次后台查询加绘图 p95 175.68ms，传输指标 p95 350.62ms。该后台小样本低于 500ms，不能代表全部 bins；切换仍未达到 1 秒目标。报价年龄与接收到可见更新的延迟分别记录，不能互相替代。功能修复已部署，性能验收保留未完成项。
- 正式服务 60 秒采样中序号 2→30、mount=1、浏览器无错误，期间所选 bin 没有新报价接收时间变化，因此本轮没有可用于判定 3 秒更新目标的新报价样本。该轮约 75522 点的大 bin 末次查询 1631ms、绘图 667ms，说明 500ms 后台目标也尚未在所有 bins 达到。

- 真实库复现缩放后浏览器长时间停止响应。范围同步由当前操作图单向驱动；十字线过滤图表布局产生的延后合成事件，保留真实鼠标移动及离开事件。`09a2cbb` 隔离真实库通过缩放后刷新、全屏 API 退出、390px 高度、09-06/07 选择签名与数据库日期归属、断网恢复，浏览器错误为空，mount=1；首屏 15.91 秒，缩放至下一次刷新完成 6.866 秒，不能视为达到性能目标。
- 进一步确认默认 minBarSpacing=0.5 会裁掉密集日内数据的整日视窗。改为 0.001；70000 原始价格点、约 11425 个共同时间点的 Reset 整日范围回归修复前失败、修复后桌面/移动通过，同时保留双向缩放和鼠标离开检查。补修已随 PR #33 发布。
- 真实库 `69°F or below` 有约 1786 个价格点、238 个由缺失值隔开的非空段。完整快照仅按起始时间复用实例，会在 bin 时间不同但段数相近时重复创建大量实例。完整替换现在复用同指标剩余实例并重设数据，只移除多余实例；相同选项不重复触发图表全量布局。70000 有值点加 233 个真实缺口、两次改变段起始时间的桌面/移动回归通过，原始点和段数保持一致。
- 进一步修复两条热路径：完整快照异步解压与 BroadcastChannel 增量共用串行队列，避免解压期间丢掉新选择的增量；延迟解压回归在旧实现稳定停于序号 1003，未应用 1004。图表分段更新先一次准备旧、新时间点并集，全部更新后再移除旧时间点；非前缀增量复用实例并一次 setData，避免撤销段末点后逐点重建长曲线。

- PR #30、#31、#32 均经五项 CI 通过后合并。此前生产业务代码为 `90712196f1492cf63788fc7b56cdfbed75440d02`，部署时间 23:37:34 UTC；四个主服务及原 R2 timer 已恢复，519080 条旧 tick 保留。
- 最后部署前备份为 `/var/lib/nice-weather/backups/repricing-boundary-20260905T2332Z/pre-boundary.sqlite3`，包含 538785 条 tick、schema 7；内存副本 full integrity_check=ok、foreign_key_check 无记录，校验 24.7 秒。部署只切换代码和版本环境，没有回滚或重写数据库。
- 真实库日期查询已验证 09-04/05/06/07，所有返回的实况/预报对象时刻都属于目标纽约日期；两个未来日各有 24 个 00:00–23:00 预报点。查询实测约 0.06–0.18 秒。
- 早期版本 `62a621b` 的完整性能验收使用约 7.5 万价格点：冷首屏 47.083 秒，已加载 bin 切换 p95 8.252 秒，后台查询与绘图合计 p95 1.468 秒，传输 p95 2.474 秒。VM 同时运行 headless 浏览器；保留该基线，最终样本见本节顶部，性能验收仍不能整体标为通过。
- Python 单元及 Dashboard 73 项通过；相关前端此前完成 14 项 Vitest、TypeScript/Vite 和桌面/移动浏览器 10 项检查，PR #32 CI 再次通过。原截图每次归零缺少逐 tick 原始来源证据，旧记录继续标记来源状态未经核验，不清洗或平滑历史异常。

## 2026-09-05 Repricing 部署及生产规模补修

- PR #31 五项 CI 通过后合并，`62a621b626e3111bbf801f561912d05542bbd90a` 于 23:16:18 UTC 部署。备份 `/var/lib/nice-weather/backups/repricing-performance-20260905T230204Z/pre-performance-consistent.sqlite3` 经内存副本 full integrity/FK 校验通过，包含 536425 条 tick、schema 7；同目录首次中止的 `pre-performance.sqlite3` 不完整，禁止作为恢复输入。四个主服务及 R2 timer 恢复，进程版本一致，519080 条旧事件类型为空的 tick 保留，部署后首轮 journal 无数据库访问异常。
- 生产真实库浏览器通过两轮全部 bins、十次后台刷新、页签切换、390px 高度检查和断网恢复，mount 始终为 1。该 VM 同时运行 headless 浏览器，冷启动 47.1 秒；已加载 bin 切换 2.265–8.252 秒，未达到 p95 ≤1 秒目标。后台多数更新低于 500ms，但存在超过目标的尾部延迟；不得用本地 fixture 成绩替代生产结果。两个未来日的早期脚本只等待模式变化，不能单独证明第二个日期已完成切换，后续按日期对应的选择签名验收。
- 真实库进一步发现 NWS valid_at 与 Weather.gov observed_at 保留本地偏移，旧 SQL 与 UTC 边界作文本比较，漏掉当天凌晨并可混入次日凌晨。天气查询及预报修订事件现按 SQLite julianday 比较实际时刻；UTC 接收截止保持原精度，存量时间及哈希不改写。回归覆盖普通日、23/25 小时 DST 日、两次 01:00、混合 UTC/本地时间、次日午夜排除及接收前一微秒不可见。

- PR #30 在五项 CI 通过后合并，生产版本 `1ec27d7764d787aa5298f3ce74d5ebfdb3063e19` 于 21:34:27 UTC 部署。最终备份位于 `/var/lib/nice-weather/backups/repricing-deploy-20260905T213222Z/pre-v7.sqlite3`；迁移后 full integrity/FK 检查通过，519080 条旧 tick 全部保留，原 active 服务恢复。
- 21:42 UTC 验证三个开放市场、33 tokens 持续采集；新增 trade 不带旧盘口，dashboard/market-stream/runner 的环境版本一致。完整文本日志在 22:13:54 仍发现 market-stream 因 BEGIN IMMEDIATE 等待五秒后退出，随后 systemd 重启；error 优先级过滤不能代替文本异常检查。
- 真实浏览器验收暴露额外性能问题：约 69870 个价格点产生约 22 MB 快照；同一快照因 iframe 尺寸更新重复传输四次。服务器首次计算实测 38.8 秒，带 profiler 为 44.3 秒，其中查询 18.6 秒、时间转换 16.4 秒。小型 fixture 的通过结果不能代表该规模。
- 补修采用标准库 gzip 和浏览器原生 DecompressionStream 无损传输，未丢弃任何点或来源字段；相同传输只解码一次。时间转换使用有上限的缓存，tick 查询只取计算所需列，增量比较直接比较字段，避免每两秒序列化并哈希整日价格。生产复验仍待完成。
- 压缩后生产传输约 1.17 MB，进一步验证发现全量绘图仍约 11 秒。绘图层现在仅使用阶梯变化与每段端点构建顶点，共同时间基底由实际绘图顶点和分钟网格组成；完整原始点仍用于悬停与审计，A→B→A、零值和缺口均保留。增加桌面/移动端 70000 点回归。
- 写连接锁等待上限从 5 秒改为 30 秒，避免短时写竞争直接终止采集；原始接收时间不改写，超过五秒的事务记录等待/持锁时长。实际持锁 5.3 秒的双连接回归验证记录最终落库，读连接仍只读。
- `312d927` 真实库复验完成两轮全部 bins，未发生浏览器卡死或选择覆盖；约 70329 个价格点的首屏绘图降为 2.86 秒。VM 上同时运行浏览器的已加载 bin 切换为 1.12–4.34 秒，尚未达到 1 秒目标，冷读取仍约 23 秒。进一步剖析显示缓存读取还扫描整日 tick 选择当前价格；改为按接收游标维护三类来源候选和待到时事件，保留全量/增量相等回归。完整快照复用现有曲线实例并替换数据，减少 bin 切换的销毁/创建开销。
- `b80fd7d` 真实库通过两轮全部 bins、十次后台刷新、页签切换、390px 窄屏、两个未来日及断网恢复，图表 mount 始终为 1。后台查询/绘图多数在数百毫秒内，切换仍未达标。发现共享时间轴格式化每次创建 Intl.DateTimeFormat；一万次本机基准为 1688ms，复用后 34ms。现按格式、时区和 locale 复用最多 32 个原生格式化实例。

## 2026-09-05 Repricing 数据链路修复

- 当前实现升级到 schema v7：`market_top_ticks.event_kind` 可空，新增 event/bin/接收游标索引。Gamma、CLOB 按来源和 token 隔离；trade 只保存真实成交，不刷新旧盘口或旧成交时间。同价新成交保留，重复消息幂等。旧 tick 保留并标为未经核验，旧成交不参与五分钟回退。
- CLOB midpoint 只保持到最后报价/完整快照接收后十分钟；已知断流即时失效，随后依次尝试五分钟真实成交和十分钟 Gamma。内部概率统一 `[0,1]`；零值保留，缺失形成缺口。
- Market Stream 动态保存所有符合 NYC/KLGA 规则的开放市场。2026-09-05 官方接口实测发现 09-05/06/07 三个市场、33 个 YES tokens，保存 33 条 CLOB 快照及 33 条 Gamma 状态；Runner 原选择及 SHADOW 不变。
- Repricing 仅显示所选市场日，以纽约当地午夜计算 23/24/25 小时窗口。当天/历史日主图和六组差值共享一分钟 as-of 输入；未来日只显示最新已知 NWS 逐小时预报、当前价格水平线与 Price×100−Forecast，显式标注当前快照，不生成未来历史价格或回测输入。
- 控件独立 fragment；天气按版本复用，价格按目标窗口及接收游标增量查询，普通 tick 只重算受影响分钟。跳过完整快照后的重复 feed 计算；无数据变化仍推进年龄与过期状态。
- full/delta 使用选择签名、递增序号和基线序号；缺序重同步、旧选择丢弃、完整快照替换、撤销点使用 tombstone。查询失败保留相同选择最后成功画面；恢复完整重同步。鼠标悬停继续更新，缩放/拖动只关闭 Follow latest。
- 两图采用真实时间范围和共同透明时间基底，保持不等间隔价格与分钟差值对齐；ResizeObserver 按实际内容通知 iframe 高度，支持隐藏页签、窄屏和全屏退出。
- 验证：Python 81 项、TypeScript/Vite 构建、桌面/移动端浏览器 8 项通过。测试涵盖来源污染、零值、成交时间、过期/断流、恢复游标、DST、未来快照和全量/增量一致性；v6→v7 备份迁移回归保留旧 tick。
- 单会话本地复测：已加载 bin 切换 p95 739ms，查询/计算 56ms，渲染 35ms。双会话同时压测：后台查询/计算+渲染 p95 桌面 457ms、移动端 354ms；接收到可见更新 p95 桌面 2347ms、移动端 2395ms；已加载 bin 切换 1616/2029ms，超过 1 秒目标，仍有并发性能限制。测试数据规模不代表线上长期库。
- 两份旧本地开发库 v6 migration checksum 与当前提交不一致，复制备份后迁移被正确拒绝；未改写校验和。线上部署需先核对 migration 记录、备份及完整性，不能沿用这些开发库的结果。线上 v4/v5/v6 checksums 已核对一致，统一库约 1.35GB、517881 条 tick（检查时点）；四个主要服务 active，09-05 journal 确认多次 unable to open database file。迁移前备份 baseline.sqlite3 已完成：518553 条 tick，quick_check=ok；原库尚未迁移。Market Stream 进一步复用单个已提交的连接，避免逐 tick 关闭最后连接导致 WAL/SHM 清理；Dashboard 保持原 ReadOnlyPaths 和 mode=ro。

## 2026-09-05 Repricing 固定实时差值与视图稳定性

- Repricing 的 Difference 已从 reference/z-score 模型收敛为六组固定普通减法；基础输入固定为 Forecast、Weather.gov Hourly Temp、METAR 和所选 bin 的 Price，NWS Station Observations 只保留为主图可选线。
- 小图按纽约市场窗口建立一分钟 as-of 网格。Forecast 使用当时已经收到的最新 snapshot 并只在同一 capture 的相邻 valid-time 点之间插值；METAR 与 Weather.gov 使用当时最新且未超过 90 分钟的当前温度；Weather.gov 小图直接读取 settlement row 的 Temp，不使用 Running Tmax。
- Price 同时要求 `exchange_event_at <= t` 与 `received_at <= t`，沿用 CLOB midpoint、五分钟内 last trade、十分钟内 Gamma approximate probability 的回退顺序。主图、摘要、小图和两秒 feed 共享唯一 `selected_bin_id`，payload 以 bin ID 和 signature 拒绝旧选择消息。
- 前端删除 reference、冻结均值、标准差和 z-score 状态。六个复选框默认开启 `METAR − Forecast`、`Price × 100 − Forecast`、`Price × 100 − METAR`；Price 相关结果标记为 `display spread`，仅用于人工观察。
- 可见 Lightweight Charts 继续单次挂载。主图和 Difference 的拖动、滚轮与触摸会退出 Follow latest；暂停期间的 delta 按 series/time 合并，真实缺口使用有限值分段，增量期间不调用 `setData`、`fitContent` 或可见范围重置。
- iframe 固定高度提高到 930px，内部 shell 不再用纵向 `overflow:hidden` 裁切 Difference 时间轴。主图维持 65/35 pane 比例，实况与 Price 使用阶梯线，透明 time-basis 使用独立 overlay scale。
- 两秒 feed 和首个 Dashboard 数据库打开失败现在记录有限上下文与异常堆栈。2026-09-05 线上出现的 `unable to open database file` 仍需结合 VM journal、SQLite/WAL/SHM 权限和部署观察日志确认操作系统根因；本次没有基于推测修改 systemd 或数据库。

## 2026-09-04 Dashboard 来源差值与稳定刷新

- Dashboard 五个页签的时间文本、表格、Plotly 和 Lightweight Charts 坐标统一固定为纽约时间并标注 `ET`；浏览器时区及其当前 DST 时差只在 Overview 顶部说明，不参与市场日、查询范围或计算。
- Overview 将四类天气摘要移至概率图之前，使用真实来源名称和信息提示，并单独展示合约 `resolutionSource`；只有规范化 URL 与配置的结算证据页一致时才标记为结算来源，审计标识移入折叠区。
- Repricing 收敛为市场日、`1D/2D/3D/5D`、单一温度档和天气源集合。市场 pane 只保留一条 Price，按有效 CLOB midpoint、五分钟内 last trade、十分钟内 Gamma indicative probability 依次回退。
- 主图继续在同一 Lightweight Charts 实例中使用天气与市场双 pane；下方 Difference 图按一分钟网格对齐，采用 `z_other - z_reference`，支持 forecast 线性插值、实况新鲜度约束、当日累计 Tmax 保持和价格有效期保持。首次完整窗口的均值与标准差在增量期间冻结；重叠不足、零方差或无共同范围时显示数据不足。
- 可见图表只挂载一次。独立零高度两秒 fragment 通过 session 级 `BroadcastChannel` 传送数据，前端按 series ID 调用增量更新；选择条件变化才进行全量协调。Overview、Execution、Paper、System 改为手动刷新。
- Difference 只承担共同变化与偏离观察，不进入交易决策或历史标签。本次未修改 Collector、Market Stream、阶段 A、Runner、R2、Paper、风险逻辑、数据库 schema 或历史数据。

## 2026-09-03 Dashboard 浅色重设计

- Dashboard 重组为 `Overview`、`Repricing`、`Execution`、`Paper`、`System & Audit` 五个页签；模型摘要、历史重定价、当前可执行深度和审计信息各自归位，Execution 不再重复展示模型概率与 edge 历史。
- 顶部状态改为无卡片的紧凑网格：桌面四列两行、移动端两列四行；长 Git SHA、数据库路径和版本信息移入 System & Audit。
- Repricing 组件恢复浅色页面语言，同一 Lightweight Charts 实例内以 65/35 双 pane 分离温度和概率轴。默认只显示 NWS forecast、METAR、Running Tmax 及所选 bin 的 mid，focus bin 使用较粗线。
- 永久事件文字已移除；forecast revisions 默认隐藏并仅在展示层做连续值去重和 30 分钟聚合，原始事件、对象时间和研究计算保持不变。
- Price-in 区域使用真实时间比例定位对象发生、系统获知、市场首次变化和阈值持续成立四个节点；移动端用四列摘要避免近邻标签重叠。
- 组件根节点、首屏 HTML、图表和全屏均固定浅色背景；移除 iframe 高度脉冲。2 秒 Repricing fragment 只发送增量数据，组件实例、缩放、图层状态和 focus 状态在常规刷新中保持。
- 浏览器/系统时区继续只影响显示，纽约对象日期、as-of、Tmax、forecast、日落和延迟计算没有变化。本次没有数据库迁移，也没有修改 Collector、Market Stream、Runner、R2 或 Shadow/Paper 逻辑。

## 2026-09-03 Tmax 重定价研究与图表

- H1 信息延迟、H2 尾部升温风险错价和 H3 预报锚定均登记为待验证假说；前 30 个市场日只采集，验证完成前不修改阶段 A 模型。
- 新增独立 `MarketStreamCollector`：Gamma 每五分钟发现合约并承担虚线 fallback，CLOB WebSocket 订阅全部 YES token，只保存顶层状态变化、成交与断线/重连快照；完整 L2 订单簿继续停用。
- 活跃纽约市场窗口内 METAR 轮询缩短到 30 秒，其他时段保持 120 秒；NWS 高频观测保持 300 秒。
- schema v6 为天气、预报、结算和市场 tick 增加显式对象时区/对象日期字段；所有业务归日固定 `America/New_York`，`received_at` 继续承担 as-of 和传播延迟口径。
- 新增 `repair-settlement-dates --dry-run/--apply`，优先从不可变 raw capture 重建跨午夜日期、滚动累计 Tmax 和最终标签；旧原文无法重解析时报告异常并回退已有明细，页面丢失旧行时仍使用截至 as-of 已收到的历史行。
- 新增 `research tmax-repricing` JSON/CSV 报告，区分对象传播、系统领先/落后和 80/90/95/99% 持续 price-in；Gamma 不计入可交易窗口。
- Dashboard 新增自建 Lightweight Charts 5.x 主时间线与 Price-in 响应组件；当前展示形态见同日浅色重设计记录。cursor 按接收顺序推进，晚到事件仍按交易所时间插入。
- 公开 Gamma/CLOB 实流已完成 11 个 YES token 的发现、批量 book 恢复和 WebSocket 双心跳周期验证；该验证没有账户认证、订单或资金操作。
- Python、TypeScript、前端构建和敏感信息扫描已纳入本地及 CI 验证；VM 运行只使用随 Python 包发布的静态资源，不安装 Node。

天气采集器已通过 PR #3 合并；统一存储与阶段 A 通过 PR #6 合并，live as-of 时钟修复通过 PR #7 合并，隔离状态通过 PR #9 合并。Dashboard 时区改造前的 VM 审计 SHA 为 `69b89f69d0104e57abd7dd8da7155fee147f0c11`；当前运行 SHA 以 Dashboard build 标识和 VM `git rev-parse HEAD` 为准。

## 2026-09-02 schema v5 存储与门控修复

- 24 小时观察确认数据库约增长 232 MiB/日，主要来源为天气完整 JSON 双写、每分钟 Gamma
  完整快照以及天气输入误入旧 `decision_inputs`。
- schema v5 将新增天气观测和预报直接关联 `source_captures`，历史记录保留
  `legacy_snapshot_id`；迁移既有天气决策关联时不丢失输入关系。新决策通过 feature snapshot
  保存完整 capture ID 集，停止每分钟重复写数十条天气关联。
- NWS station observations 改为两小时重叠窗口；Gamma snapshot ID 改为内容寻址，完整事件
  只按内容变化保存，旧快照表只保留轻量引用。
- BLOCKED 决策停止进入市场和风险判断，逐层买入 quote 的实际名义金额不超过目标和单档上限。
- Polymarket 读取增加一次有限重试，错误事件记录 stage、URL、attempts、elapsed 和底层异常类型。
- Dashboard systemd 关闭 Streamlit 使用统计，保留 `ProtectSystem=strict` 和 `ProtectHome=true`。
- 本地 Ruff 和 48 项完整 pytest 通过。VM 尚未升级 schema v5；部署后重新开始 24 小时观察，
  隔离的三个旧 live 数据库文件继续保留。

## 2026-09-02 Dashboard 时区

- Dashboard 顶部时间、阶段 A as-of、天气与决策图表、Paper 订单/成交、heartbeat 和审计表格统一使用访问者浏览器时区展示；无浏览器时区时回退为 Dashboard 主机系统时区。
- SQLite 和 as-of 计算仍使用 UTC；合约 `local_day` 仍固定为市场结算时区，不随访问者改变。Streamlit 依赖下限提高到 1.43，以保证 `st.context.timezone` 可用。
- 新增夏令时、标准时、递归时间字段转换和市场日保持不变的 Dashboard 回归测试；本地 Ruff 和 42 项完整 pytest 通过。

## 2026-09-01 统一存储与阶段 A

- 新增带校验和的 `schema_migrations`、`db migrate/verify/clone-migrate`、METAR 时间派生修复和部署版本查询。
- Runner 的天气输入改为 SQLite `WeatherRepository` as-of 查询，不再在 live 决策周期调用天气 API。
- 阶段 A 固定为 `baseline-nws-official-floor-v2`：NWS 预报提供未来基线，Weather.gov Hourly Tmax 提供官方下界，METAR/NWS 高频观测只作为趋势特征。
- live Runner 先计算概率，再对 `probability >= 0.02` 的候选 token 获取 Quote；生产周期只保存有限 top-5 执行上下文，停止新增完整订单簿层级。
- METAR 以 raw message 的 `DDHHMMZ` 为观测时间，保留 provider receipt 与 report time；NWS 修订增加 value/raw-message/QC/metadata/mixed 分类。
- Weather.gov 每轮先解析，只有首次 finalized、解析失败、非单调回落或 finalized 后变化才截图；逐行结算数据进入 `settlement_rows`。
- R2 新数据使用 `nyc-klga/v2/`，允许项固定为天气、结算、heartbeat、天气特征和标签；市场、决策、Paper 数据继续排除。
- Dashboard 已处理空 Quote，显示部署 Git SHA、官方/NWS/METAR Tmax、阶段 A as-of 与有限深度。
- 本地基线 30 项测试通过；重构与 live as-of 回归修复后 40 项完整测试（含多角色 SQLite WAL 并发验证）、Ruff 和差异格式检查均通过。
- VM 已从 `weather.sqlite3` 生成并验证 schema v4 统一库 `/var/lib/nice-weather/nice-weather.sqlite3`；天气、结算和 R2 账本保留，旧 live decisions 与订单簿未迁移。
- Collector、R2 timer、Dashboard 和 Shadow Runner 已统一到 `/opt/nice-weather/repo`、共享虚拟环境、统一数据库和同一 Git SHA；R2 新对象已验证写入 `nyc-klga/v2/`。
- 2026-09-01 进行了 16 分钟部署验收：服务重启数为 0，Runner 无 `DATA_AS_OF_VIOLATION`、Traceback 或 ERROR；累计完成 17 个 Shadow 决策、17 个阶段 A 预测和 118 个有限深度 Quote，`order_book_levels` 保持 0，R2 pending/failed 均为 0。
- Dashboard 已在公网验证新 build SHA、阶段 A as-of、概率与可执行价格渲染，空 Quote 格式化崩溃未复现。当前因观测/预报陈旧与覆盖缺口输出 `NO_TRADE`，符合 fail-closed 规则。
- 2026-09-01 20:40 UTC 左右，旧 `/var/lib/nice-weather/live.sqlite3`、`live.sqlite3-wal` 和 `live.sqlite3-shm` 已逐一移入固定隔离目录 `/var/lib/nice-weather/quarantine/20260901-unified-store/`；移动后的 SHA-256 与移动前审计值逐项一致，原路径已不存在。移动后四项服务均为 `active`，统一库 schema v4 完整性与外键检查通过，`order_book_levels=0`，R2 pending/failed 均为 0，Runner 无新增相关错误。24 小时观察从隔离移动时间开始计时；观察结束后仍需列出三个精确绝对路径并取得第二次人工批准后才能逐一删除，禁止 glob、递归删除和自动清理。

## 2026-08-27 独立天气采集与 R2

- 新增 AviationWeather KLGA METAR、NWS hourly forecast、NWS station observations 和 Weather.gov 结算页面的独立调度。
- SQLite schema v3 保存压缩原始响应、观测修订、预报版本、结算证据和 R2 上传账本。
- R2 每 15 分钟上传 gzip NDJSON 原始批次和少量结算截图；纽约时间 03:15 后导出前一日 Zstandard Parquet 与 manifest。
- 新增 `collect-weather`、`r2-check`、`r2-sync` 和 `collector-status` 命令。
- 提供 Ubuntu systemd 单元和逐步部署、验收、回滚 runbook；本地与 R2 均不自动删除。
- 当前范围只包含 KLGA 官方天气数据，不包含 Polymarket、订单簿、其他城市或历史回填。
- 本地发布前检查为 Ruff 通过、30 项测试通过；2026-08-27 live smoke 的三类 API 均成功，Weather.gov 真实表格解析为 `parsed`，当时 KLGA 已实现 Tmax 为 `77°F`，尚未跨日最终确认。

独立 Python package、真实 API fixture、合约解析、Tmax 概率、Signal/Risk、PaperBroker、SQLite WAL、持续 Runner、统一查询层和 Streamlit Dashboard 已形成一条可执行主链。旧代码仅按能力审计后择要迁移，研究资料仍在旧项目：

```text
D:\ALLPROJECTS\x learner
```

## 已有成果

### 天气作者与策略研究

- 已建立高置信天气作者、人工复核作者和证据库。
- 已保存一批高价值帖子完整原文。
- 已为核心作者建立策略逆向工程笔记。
- 已形成站点偏差、实况分叉、nowcast、相邻档、来源修订和执行风险等研究方向。
- 作者档案入口以高置信作者目录内的 `00 作者索引.md` 为准；历轮研究见 `00 总览与记录/01 运行记录.md`；天气策略见 `00 总览与记录/02 现有天气策略.md`，通用作者策略见 `00 总览与记录/03 通用策略大全.md`，主要视觉入口见 `00 总览与记录/04 策略看板.base`。
- 策略正文位于 `04 策略档案/` 下的独立机制档案；两份 Markdown 总览承担简明看板作用，Bases 提供卡片和筛选视图。
- 最近一轮未发现新作者，检查 16 位现有研究作者、返回 699 条帖子并归档 50 条高价值原文；9 位作者策略页获得实质更新，`@1985028cronaldo` 保留待补新帖 gap。
- Obsidian 已改为“作者档案 + 追加式运行记录 + 分类策略总览”；六个根目录旧文件已归并并移出活动 Vault，迁移原件保存在项目研究运行目录。

### 现有代码骨架

旧项目的 `src/x_learner/weather` 已包含：

- 标准化天气合约与 as-of 快照。
- Polymarket 和气象数据只读适配器。
- 简单 Tmax 分布与档位概率。
- 净优势候选。
- 风险审批与 `no-trade`。
- 简化 Paper Broker。
- SQLite 存储。
- 概率校准和策略评估入口。

这些模块仍属于早期骨架，需要在迁移前检查接口、测试和数据假设。

### 项目介绍材料

纽约天气交易 MVP 架构与流程 PPT：

```text
D:\ALLPROJECTS\weather forecast\纽约天气交易MVP_项目架构与流程.pptx
```

该版本用于共同开发者沟通，固定纽约、KLGA、每日最高温、Paper Trading 和标准 MVP 路线。

旧版完整启动会 PPT：

```text
D:\ALLPROJECTS\x learner\天气预测市场交易系统_项目启动会.pptx
```

## 当前主要缺口

- PAPER CANARY 尚未人工批准和启动；当前实际 live 验证只运行 SHADOW。
- 尚未连续运行 3 个 Paper canary 市场日和 10 个 continuous paper 市场日。
- 基线正态分布固定 `sigma=3°F`，没有经过样本外校准。
- CLOB 顶层 WebSocket 增量流已经部署；maker 排队模型仍未实现，前 30 个市场日继续只采集研究数据。
- 自动结算等待 Gamma winning outcome；跨机场日未完成结算的旧持仓仍需人工对账。
- 外部 API 失败已有阶段化 `system_events`、有限重试、循环续跑和 heartbeat，尚未经过长时间断流与重连演练。
- schema v6 已部署；Dashboard 最终 SHA 上线后需重置 24 小时观察基线，避免混合两个展示版本的运行记录。
- Nautilus Trader、复杂回放、集合预报和多城市继续后置。

## 2026-08-23 MVP 纵向闭环

- 项目包名固定为 `nice_weather`，运行时使用轻量 Runner，Nautilus Trader 继续后置。
- NYC/KLGA 数据新鲜度、模型、Signal 和 Paper 限额集中在 `config/nyc_klga.toml`。
- SQLite 采用 writer lease、WAL、完整 decision 事务和 Dashboard 只读短连接。
- 真实 fixture 固定 Gamma event `892623`、11 个 YES token 订单簿、KLGA METAR、NWS points/hourly、接收时间和 SHA-256。
- Fixture 重复运行得到稳定 decision/order/fill ID；当前真实快照产生 `82-83°F` Paper 候选和一次成交。
- PaperBroker 支持 submitted、accepted、partially_filled、filled、canceled、rejected、重复 book 幂等、Bid 退出、恢复、账目和情景 P&L。
- 单个 Streamlit/Plotly 应用实现 Overview、Market Detail、Paper、System & Audit 四个 tabs，全部读取同一个 `complete decision_id`。
- 2026-08-23 live smoke：Gamma event `888236`、3 条 KLGA METAR、156 个 NWS period 均成功；一次 SHADOW 和两个连续 SHADOW 周期健康为 `OK`，Paper 订单与 fill 均为 0。
- Streamlit 实际进程 `/_stcore/health` 返回 `ok`；本地完整测试当前为 22 passed。
- GitHub PR #1 的 `lint`、`unit`、`fixture-dashboard` 均通过；功能分支以 squash 方式合并到 `main`。
- `main` 由 active Ruleset `Protect main` 保护：必须通过 PR，required approvals 为 0，必须解决 review conversations，三项 CI 均为 required check，并禁止删除和 force push。

## 推荐的下一步

### 第一优先级：人工检查 LIVE SHADOW

至少覆盖两个连续 KLGA 市场日，核对规则文本、订单簿接收年龄、METAR、NWS 覆盖、no-trade 原因和 Dashboard decision trace。

### 第二优先级：经人工批准后运行 PAPER CANARY

保持 100 美元起始现金、单档 5 美元和单机场日 20 美元限额，运行 3 个市场日并人工对账订单、部分成交、退出、持仓、P&L 和恢复。

### 第三优先级：持续运行与验收

完成 10 个连续市场日的持续 Paper、断流、重启、规则版本变化和结算验收。Nautilus Trader 保留为后续运行层技术验证。

## 当前 MVP 固定口径

- 平台：Polymarket。
- 城市：纽约。
- 机场站：KLGA / LaGuardia。
- 事件：机场站每日最高温。
- 温标：按合约规则解析，纽约当前目标以华氏温标为主。
- 执行：Paper Trading。
- 周期：标准 MVP，2–3 周。
- 扩展方式：`CityConfig`、数据适配器、`ProbabilityModel` 和 `ExecutionAdapter`。
- 安全边界：规则或数据口径存在歧义时输出 `no-trade`。

## 2026-08-16 纽约 MVP 架构定稿

- 将纽约 KLGA 固定为首个可运行市场，不再把城市重新选择作为 MVP 前置任务。
- 将大型历史复现平台、复杂校准、多城市对比和 Nautilus 深度集成移出首版交付路径。
- 固定主链：配置 → 数据适配器 → `UnifiedState` / SQLite → 合约解析 → 概率 → 信号 → 风险 → `PaperBroker` → 监控。
- 确定三周路线：第一周数据与骨架，第二周决策与纸盘，第三周持续运行与验收。
- 项目拆为两条并行工作流：天气数据与模型、交易系统；通过 `MarketContract`、`ProbabilityEstimate` 和 `DataHealth` 连接。
- 项目架构 PPT 扩展为 11 页，新增分工页并将三周路线改为双轨推进；数据源以页面内可点击官方链接展示，演讲者备注为空。

## 当前禁止事项

- 不配置实盘账户和私钥。
- 不发送真实订单。
- 不把未验证策略描述为有效策略。
- 不用最终修订数据回填早期决策。
- 不在规则有歧义时继续交易。
- 不在没有明确预算时调用 X API。
- 每轮 X 研究只使用用户当轮明确预算；历史累计额度仅用于审计和资源去重。

## 更新要求

完成一个里程碑后更新本文件：

- 已完成内容。
- 新增文件和入口。
- 测试结果。
- 下一步。
- 阻塞项。
- 新的范围或架构决定。

## 2026-08-01 现有作者第 4 轮增量研究

- 关闭作者发现，检查 16 位现有研究作者，每位最多 50 条未见帖子。
- 30 次串行 X 时间线调用返回 638 个 Post 资源；本地保守费用 `$3.190`，低于本轮 `$5` 硬上限。
- `@1985028cronaldo` 遗留的新帖区间已闭合；本轮结束时没有 pending author gap。
- 9 位作者新增完整证据页；作者档案、逐作者策略和分类策略总览均已更新。
- 新增研究方向集中于站点治理、WU 缓存/单位转换、动态 METAR 节奏、Tmax 发生时点、奖励参与者制度和幽灵订单幂等对账；全部仍为待检验假设。
- 运行明细见 `research_runs/2026-08-01-existing-authors-04/retrieval_summary.md` 与 Obsidian `00 总览与记录/01 运行记录.md`。

## 2026-08-01 策略入口调整

- `01 现有作者策略总览.md` 已重命名，当前入口为 `00 总览与记录/02 现有天气策略.md`。
- 通用策略当前入口为 `00 总览与记录/03 通用策略大全.md`，用于明确纳入范围的通用预测市场作者及跨市场机制。
- 原生 Canvas 当前入口为 `00 总览与记录/05 策略导航.canvas`；两份策略 Markdown 顶部保留标题直达表，策略正文和溯源格式保持不变。
- 当前 Vault 已启用 Canvas 与 Bases；现阶段采用 Canvas，避免为了卡片视图把每条策略拆成独立笔记并产生双份维护。

## 2026-08-01 用户指定跨主题作者首轮研究

- 在用户指定跨主题名单加入 `@mmmatt`，完整名单共 9 位；关闭作者发现，每位最多检查 50 条未见帖子。
- 10 次实际 X 调用返回 9 User 与 384 Post；本地保守费用 `$2.010`，低于本轮 `$5` 硬上限；无 unresolved、denied 或 pending author gap。
- 新建的跨主题作者库当前位于 Obsidian `03 跨主题关注作者/`，每位作者包含账号档案、完整高价值证据和策略逆向工程页。
- `00 总览与记录/03 通用策略大全.md` 从空模板扩展为 14 条可证伪策略；Canvas 改为直接链接具体通用策略。
- 天气侧只增强“天气驱动的体育事件”范围外观察；没有把跨主题执行机制重复写入天气策略。
- `@mmmatt` 的攻击类文章正文缺失，当前只保留防御性订单流威胁模型。
- 本轮 Obsidian 部署快照与检索摘要保存在 `research_runs/2026-08-01-user-specified-mmmatt-01/obsidian-deploy-snapshot/`。

## 2026-08-01 X 转帖与 Article 研究规则

- `weather-market-research` 时间线读取现已包含原创、引用、回复和纯转帖；纯转帖计入作者当轮配额与去重状态。
- 转帖证据明确区分“监控作者选择转发”“原作者提出主张”和“项目推断”，转帖不自动视为背书，也不自动扩张作者范围。
- 有策略价值的 X Article 或作者长文被列为重点逆向工程证据；取得正文后按原子主张、机制、可证伪假设和策略规格深度拆解。
- 只有标题或预览时保留证据缺口，不推测正文；额外付费恢复仍受当轮预算预检和即时记账约束。
- 修改前后 Skill 快照及验证辅助文件保存在 `research_runs/skill-update-weather-market-research-2026-08-01/`。

## 2026-08-01 策略库深度重构

- 将旧天气 10 个标题和通用 14 个标题逐项迁移为 5 个天气 Alpha、6 个通用 Alpha、8 个控制规则和 1 个待补证假设；迁移表保留全部旧标题及拆分/合并关系。
- `00 总览与记录/02 现有天气策略.md` 与 `00 总览与记录/03 通用策略大全.md` 改为紧凑看板；独立档案统一包含机制、证据链、真实案例、完整研究/交易循环、失败条件和证伪计划。
- 当前 Bases 入口为 `00 总览与记录/04 策略看板.base`，提供天气 Alpha、通用 Alpha、控制、待补证、可测试和已否定视图；`00 总览与记录/05 策略导航.canvas` 只保留导航。
- 旧两份策略正文和旧 Canvas 原样归档到项目 `research_runs/2026-08-02-obsidian-layout-cleanup/historical-artifacts/99 历史快照/`，未删除历史资料。
- 审计旧总览引用的 103 个帖子 ID：102 个可在本地作者原文库找到；`@Clon298363` 的 `2050980442447429922` 缺正文，已降为待补证线索。
- `weather-market-research` 增加作者原文前置、`alpha/control/hypothesis` 分类、正式策略质量门槛和独立档案更新流程；预算与抓取状态脚本保持原结构。
- 本次迁移只使用本地资料，未调用 X API，费用为 `$0`。

## 2026-08-02 Obsidian 活动区编号整理

- 活动区顶层统一为六个连续编号文件夹：`00 总览与记录`、`01 高置信天气博主`、`02 待人工复核`、`03 跨主题关注作者`、`04 策略库`、`05 范围外线索`。
- 原先散落在根目录的运行记录、两份策略看板和 Canvas 已归入 `00 总览与记录/`；范围外作者与工具线索归入 `05 范围外线索/`。
- 策略库内部统一为 `00 策略库.base`、`01 天气 Alpha`、`02 通用 Alpha`、`03 风控与执行控制`、`04 待补证假设`、`05 证据缺口.md`、`06 旧策略迁移表.md`。
- 历史快照移出活动 Vault 保存，全部链接、Bases 过滤路径、Canvas 节点、项目文档与 Skill 固定入口已同步迁移。
- 本次只整理本地资料，未调用 X API，费用为 `$0`。

## 2026-08-02 Bases 空视图修复

- 当前 `00 总览与记录/04 策略看板.base` 曾使用缺少 `weather forecast/` 层级的 Vault 路径过滤，导致界面显示 0 条结果。
- 全局筛选改为 `file.hasTag("strategy-dossier")`，直接匹配 20 份 canonical dossier，避免目录重命名再次破坏视图。
- 本次只修复本地显示，未调用 X API，费用为 `$0`。

## 2026-08-02 活动文件再归类

- `00 总览与记录/` 内统一为 `01` 至 `05`：运行记录、天气策略、通用策略、策略看板、策略导航。
- `04 策略库/` 改名为 `04 策略档案/`，只保留 `01` 至 `05` 的活动档案和证据缺口。
- `06 旧策略迁移表.md` 归档到 `research_runs/2026-08-02-obsidian-sequence-cleanup/historical-artifacts/`，未删除。
- 总览与策略档案目录内不再保留额外的 `00` 文件；本次没有调用 X API，费用为 `$0`。

## 2026-09-12 Nautilus 工作台本地验收

- 隔离分支 codex/nautilus-trading-workbench，基线 e827138f9535996ed0a2162d14bcd9997af2b2b8；原工作目录的未提交修改保留。
- 新增 Trading/Backtest、请求库、原生引擎投影、不可变自采数据集、串行回测 worker 与模拟恢复；旧五页和旧 Paper 保留。
- Ubuntu WSL Python 3.12 / Nautilus 1.231.0：111 项 Python 测试、14 项前端测试、类型检查及构建通过。公开自采发现 44 个 YES/NO token；自然行情与固定样本证据分别记录。
- 桌面及移动端窄屏完成模拟买入/退出：同会话 4 笔成交、持仓归零、现金 99.67405、累计费用 0.22595、P&L -0.32595。重启后现金、持仓、订单、成交、净值及最大回撤核对一致。NETTING 生命周期累计统计修正采用可重放的版本升级事件。
- 固定提交 98fbcb4 对同一自采数据集完成两次回测，配置、源码及完整快照哈希一致，均为 2 笔成交、净值 99.7895。浏览器取消保留部分结果；报告与可复现数据/日志见 [本地验收报告](acceptance/nautilus-2026-09-12/REPORT.md)。
- 真实订单仍关闭。完整 KLGA 市场日的正式 Ubuntu 部署观察、官方执行客户端完整故障矩阵和最终验收结论尚未完成，不能将本地短时测试表述为整体上线验收完成。
- Repricing 延迟优化与阶段 A 策略迁移本轮后置。运行与回滚见 [TRADING_WORKBENCH.md](TRADING_WORKBENCH.md)。
- 用户授权自行审核并提交后，分支已推送并创建 [草稿 PR #36](https://github.com/RENEGADES20/nice-weather/pull/36)；远端 CI 已启动，最终检查结果在 PR 展示。保留草稿状态，完整部署验收门槛未关闭。
- 提交前复查补齐存量挂单检查：规则歧义、费用未知、市场停用或观测窗口结束时，在新行情撮合前撤销挂单；与新订单共用市场有效性判断。

## 2026-09-12 第二轮验收与部署连接检查

- 新增官方客户端提交超时/结果未知、原始 WebSocket 重复成交与状态转换，以及独立 Redis AOF 重启后的原生账户、部分持仓、费用、订单和启动对账测试。原生交易 38 项、其他 Python/Dashboard 87 项通过，共 125 项。
- 修复未知提交的预期交易所订单 ID 仅驻留内存的问题：通过原生 Cache 通用键保存关联，启动加载后恢复；测试不发送真实订单。Redis 尚未落盘写入遇到断电的实盘路径仍未验收，LIVE 保持关闭。
- 退出与撤改单增加提交前预览和重复点击幂等验证；退出保留审核限价。tick size 修订先撤挂单并等待新报价，再使用原生 instrument 更新，避免旧精度舍入。
- 私有仓库 HTTPS 认证与推送可用；部署文档加入已审核 Git bundle 和传输哈希流程，无需将开发机 GitHub Token 复制到 VM。
- 尚无可用 SSH 配置；已发现 Google Cloud VM 控制台，但 Chrome 控制连接超时，未读取到实例信息。正式 VM 部署和完整 KLGA 市场日验收仍未完成，PR #36 保持草稿。
