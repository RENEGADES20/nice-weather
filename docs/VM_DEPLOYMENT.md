# 当前发布规则（2026-09-19）

## 2026-09-28：人工清理完成后的发布安排

用户已完成旧 KNYC feed 整库删除、新库及 Paper 游标重置，并接受根盘当前约 19.10 GB（63.5%）可用的结果。16:52 UTC KNYC feed、两 Paper、两 Live、终端与 R2 timer 已恢复 active，KLGA collector/R2 timer 继续 active；HRRR/backtest 待本轮保留策略和旧历史门禁发布后恢复。新库 feed 1,042 条，Paper 游标 1,034/1,039；不再以三分之二空间作为本轮发布前置条件。发布后验证 R2 天气正文 GET 回读后清空、市场原文为空、HRRR 不写索引/GRIB 原文、两 Paper 推进、超过 24 小时且已消费的市场/HRRR 事件回收、根盘容量趋势和两服务健康。若 Paper 停滞导致回收暂停，仍须按 1 GiB 空间保护处理故障；不能声称硬上限。

## 2026-09-28：KNYC feed 人工整库重建与持续留存

用户决定亲自删除 `/var/lib/nice-weather-knyc/feed.sqlite3`、`feed.sqlite3-wal`、`feed.sqlite3-shm`，要求根盘至少三分之二可用。此操作会丢失 KNYC 旧 feed 的天气解析、采集元数据、规范化行情和本地 R2 索引；保留 R2 对象、`results.sqlite3`、请求库、实盘账户/订单/成交和 KLGA。批量文件删除只能在列出这三个精确目标并由用户人工审核后执行。不能用整库删除代替天气 R2 核验。

1. 停止 `nice-weather-terminal.service`、`nice-weather-knyc-r2.timer`/service、KNYC feed、backtest、两 Paper、两 Live；确认所有 KNYC 清理脚本已退出。执行 `sudo systemctl disable --now nice-weather-knyc-hrrr.service`。KLGA 的 `nice-weather-collector.service` 和 `nice-weather-r2-sync.timer` 保持 active。
2. 用 `sudo systemctl reset-failed nice-weather-knyc-r2.service` 清除此前人为中止留下的 failed 状态，再运行 `sudo systemctl start nice-weather-knyc-r2.service`。要求 `systemctl show ... -p Result -p ExecMainStatus` 返回 `success` 和 `0`。归档器逐批上传天气正文，GET 回读比对原文字节及 SHA，核验成功才清空 VM 正文。只读查询所有天气来源的 `capture_bodies`，如仍有非空 body，停止整库删除并排查归档；不能仅凭 R2 中存在同名对象判断成功。
3. 确认步骤 2 后由用户人工审核并删除上述三个精确文件。新库由 `nice-weather` 账户创建，在建表前设置 `PRAGMA auto_vacuum=INCREMENTAL` 并 `VACUUM`；创建 `market_history_progress(id,seq,evicted_before)`，以真实重建时间插入 `(1,0,time.time())`，再启动 KNYC feed 初始化其余表。新版本回测据此拒绝已清除时段。不要删除其他 SQLite 文件或 KLGA 文件。
4. 在恢复两 Paper 前，将 `results.sqlite3` 中 `sandbox-kalshi-knyc`、`sandbox-poly_us-knyc` 的 `runs.config.feed_cursor` 和对应 `paper_state.body.config.feed_cursor` 同时设为 0，按 `json.dumps(...,sort_keys=True,separators=(",", ":"),allow_nan=False)` 对修改后的完整 state 重新计算 SHA-256，写入 `paper_state.checksum`。只修改这两个游标与校验值，保留其余账户、订单、成交和检查点内容。缺任一账户或检查点应回滚该事务并保持 Paper 停止；旧游标超过新库序号会导致 Paper 长期跳过新事件。
5. 依次恢复 feed、两 Paper、两 Live、terminal、KNYC R2 timer；现役回测 worker 尚未包含历史删除门禁，待本轮 PR 部署后再启动，HRRR 不恢复。检查所有服务状态、R2 后续周期、新天气正文核验后清空、两个 Paper 游标推进、根盘 `df -B1 /`。30,083,776,512 字节根盘的验收线为至少 20,055,851,008 可用字节。发布器不再因 feed/storage 改动重启 HRRR；现役策略模型需要 HRRR 特征，停止采集后预测为 `unavailable`，保持 no-trade，不能替换成未经验证的数据。未来市场原文和无用截图不持久化，旧 `book`/`market_price` 事件在两 Paper 检查点越过后按 24 小时有界回收。

逐步命令与校验门槛见 [KNYC feed 人工重建操作单](acceptance/knyc-feed-manual-reset-20260928.md)；具体删除、容量和恢复结果以 `CURRENT_STATE.md` 的最新实测为准。整库删除尚未由用户回报完成。

## 2026-09-27：#96 已发布，缺概率组点击通过

PR #96 七项 CI 全部成功（run `36306678635`），已合并并部署为 `200c81ce586622803585dffb99f97accb91df79f`。apply 退出 0，stop 0.924 秒，仅 terminal 重启并 active，Paper 未重启。manifest 86 个文件核验一致，mismatches=[]；`/health` 返回 HTTP 200、耗时 0.006796 秒；两平台 Paper active/running、NRestarts=0。

发布包 712,882 字节，SHA-256 `9800465f2de9f8d8431832f09316ee2b858b5dbeb4e68e84adb4729c79f54769`。

#96 已在正式页面验证相邻真实 S2 天气预警组逐组点击：组计数与首原事件匹配，注释行相隔 20 px，无遮挡。 已知概率顶部遮挡另行修复，尚未发布；当前点击与 Paper 预检边界见 [本轮验收记录](acceptance/probability-paper-20260927.md)。Poly US 当日预检已返回原生参考价，token 阻塞解除；仍须追齐且报价新鲜后再验新市价单。

#95 及此前发布实测保留于下方阶段记录。

## 2026-09-27：#95 阶段记录（#96 发布前）

PR #95 七项 CI 全部成功（run `36305461615`），已合并并部署为 `04f2c4d1248c785810e4fd0b0fdab62cfbe5fcc9`。apply 退出 0，stop 0.239 秒，terminal、backtest 和两平台 Paper 均 active，两 Paper NRestarts=0；health 返回 200、耗时 0.145654 秒。08:25 UTC 以服务账户核验 manifest 86 个文件，mismatches=[]。

发布包 712,749 字节，SHA-256 `a9e8723495d97411d4ef05dc7a68ecbe698bf039ff51a3b4f4ce52f5d6020a76`。

正式大规模真实历史页面已验证：首个成交随首页显示，无需等待全量历史；列表展开后每页 50 条，可翻页、检索及定位首末原事件，折叠时不挂载详情节点。真实成交可定位到同期已知市场概率，跨 bin 保留 6H 并清除旧详情。上述为实际页面通过项，不公开账户记录数量、交易时点或明细。

缺概率的相邻单标记及密集分组已在本地改用 SVG 下方注释行，保留原 x 与概率缺失，不遮挡时间控件；已知概率的 y 坐标不变。相关浏览器 4 项通过（20.2 秒），其中一例在桌面 1440 和手机 390 宽度逐个点击 4/6 个相邻成交标记；TypeScript、构建、diff 检查通过，双独立审查无阻塞。另一个相邻密集分组的真实点击专项通过（1 项，6.9 秒），确认每组打开自身原事件。该修正尚未 PR 或部署，正式页面复验待发布后完成。当日 Poly US 预检仍缺 token，Kalshi 规则歧义继续 no-trade，新 Paper 市价闭环仍未通过；策略停止状态保持；既有真实天气预警、天气触发和执行拒绝已在所选 bin 概率图分阶段成组显示，组内原事件定位已验证；早期平台概率证据缺失时详情明确显示 —，不以策略概率补线。本轮新执行成交闭环仍未通过。

R2 第二周期 07:57:52–08:11:01 UTC 退出 0，timer 正常；归档与 prune 已连续完成两个已观察周期。

本地构建 JS 为 `index-BNxqveo1.js`，CSS 未变。下方保留先前版本发布时点；缺概率标记修正尚未发布。

## 2026-09-27：#94 阶段记录（#95 发布前）

PR #94 现役版本为 `c7eb189`，86 个 manifest 文件哈希全部吻合。stop 阶段 0.244 秒，health 耗时 0.493 秒；两平台 Paper 均 active/running、NRestarts=0。此次已发布首轮原生现金/序列化/checkpoint 优化，原生账目和逐事件提交保留。Poly US 仍有追补积压，服务恢复不能替代正式追齐与交易验收。

#93 解释器修正后的 R2 周期从 07:26:52 UTC 运行至 07:42:32 UTC，exit 0，systemd 记录 Finished / Deactivated successfully；CPU 51.045 秒、峰值内存 496.2 M、无 swap。归档和 prune 恢复已验证，07:11:43 缺 boto3 的旧失败不再代表当前周期。

当前 `codex/knyc-events-visible` 的受保护纯行情 checkpoint 合并、事件页渐进显示与有界渲染尚未发布，待本地检查、PR 和 VM 实测。旧历史页折叠时仍挂载全部事件导致卡顿的现象已定位；不得提前记录界面性能修复或追补完成。

一次真实输入的只读候选测量使用进程内存 SQLite：40 条原 feed 中的 16 条本平台 book 全部转换并处理为 32 条原生事件，checkpoint 提交由 16 次减为 8 次；apply 0.999579 秒、内存提交 0.895550 秒、总计 1.935043 秒。此前测量负载不同，不作倍速比较；生产追补速度仍待部署后核验。

本轮本地检查：新增纯行情合并 23 项与既有 KNYC terminal、Paper catchup 回归共 41 项通过（17.69 秒）；terminal.spec 14 项通过（34.7 秒），包含万级列表与密集分组两例。TypeScript、Ruff、生产构建和独立审查通过。测试总耗时不作为生产交互指标，本轮候选仍待 PR、部署与正式页面验证。

下方保留 #92/#93 当时的发布与测量记录。

## 2026-09-27：#92/#93 发布阶段记录（#94 发布前）

#92 发布版本为 `7d974d4d7fad759652e261e1132ee4458e41b398`，七项 CI 成功（run `36301926257`）并正常合并。其后 #93 于 07:25:50 UTC 成功发布为 `bdda89139e19644bfd5be558084316e64b4a7972`，release exit 0，仅一个 R2 unit 变化；stop 0.465 秒，terminal 重启，Paper 未重启。#93 产物 SHA-256 `eaa7016ca7b30153eeeb46bf3f07c8a80f6be945927bb51db071f5a885b520d7`。以下预热、86 文件和接口数值属于 #92 发布测量。包 710,739 字节、SHA-256 `7ee3eb33cb6891f6b64b149b62a02c5aa4ced8f7c828e5eec47b5fefdab29aa2`；更新器 SHA-256 `2b3374130be3653b5ce7e6c90713b6030a952fd059abf604cc8aefe77b955b9c`，VM 校验一致。prepare-only 退出 0，天气投影 0.020 秒，3,330 点/21 CLI/seq 3,296,321；apply 退出 0，停服务 0.885 秒、最终投影 0.131 秒，3,330 点/21 CLI/seq 3,296,542。manifest 86 个文件哈希全部匹配，health 200、0.018942 秒。

两平台 Paper 均 active/running、NRestarts=0。Poly US 正在追补暂停期间积压，当次 cursor 3,151,195、feed max 3,297,690、最新 worker contract day 2026-09-26，当日预检缺 token，真实新订单尚未提交。四组天气接口 0.057 / 0.047 / 0.053 / 0.045 秒、300 点历史接口 1.065 / 0.518 / 1.057 / 0.082 秒，仅记录本次请求样本；完整交易与信号验收未完成。

R2 timer 07:11:42 UTC 恢复 active；07:11:43 oneshot 因 runtime 虚拟环境缺 boto3 退出 1，未归档或 prune，此为 #92 的历史失败。#93 unit 已复用 `/opt/nice-weather/.venv/bin/python`，保留 `PYTHONPATH=/opt/nice-weather/knyc-current/src`，无依赖安装；VM 只读核验 boto3 可用且模块来自现役源码。07:26:52 UTC 新归档周期已开始，截至本次观测仍 running、无新错误，成功退出尚未观察，归档/prune 仍不能标记通过。#93 86 个 manifest 文件哈希复核无差异。

Paper 追补性能分析显示八个 book 事件合计 3.037 秒：apply 0.818 秒、内存提交 2.216 秒，重复 JSON 处理 1.757 秒。追补优化现已本地完成：原生现金直接读取、账户序列化缓存按原生事件身份失效、checkpoint 编码/SHA 复用、提交标记事务成功后更新；逐事件持久化及原有账目保留。定向 54 项（29.11 秒）、Ruff 和两位独立审查通过，当前准备 PR，尚未部署。#93 未重启 Paper，VM 继续运行原追补路径；实际新订单、追齐速度和真实信号仍待验证。

归档运行中四组查询按 Kalshi 09-26/09-27、Poly US 09-26/09-27 顺序：天气 0.054 / 0.052 / 0.183 / 0.042 秒，300 点历史 2.667 / 1.520 / 1.495 / 0.023 秒；天气投影 seq 3,304,471、3,332 点。07:28 UTC 正式多 bin/范围切换、键盘历史定位与单边盘口原生概率样本通过，实际 Paper 新订单与真实信号仍待验证。

下方 a51 和 #92 发布前段落保留历史时点；本次实时状态及剩余项见 [本轮验收记录](acceptance/probability-paper-20260927.md)。

## 2026-09-27：a51 后续恢复与 R2 修正（#92 发布前记录）

生产仍运行 `a51f0d69f164505947d5a21b387d74d5fcefb0eb`。Poly US Paper 反复重启至计数 24 后已明确停止，现 inactive/dead；终端 active/running。后续修正版包含恢复持仓索引、原生官方结算生命周期、300 点历史分页和分区错误，以及 R2 有界读取；本地检查通过，尚未部署。受影响 Paper 须随修正版重新启动并验收，R2 timer 保持暂缓至正式核验后显式恢复。

已查明旧 R2 unit 实际导入 `/opt/nice-weather/repo/src`。本次必须同时发布 R2 Python 文件和 unit：`PYTHONPATH=/opt/nice-weather/knyc-current/src`，解释器为现役 runtime `.venv/bin/python`。更新器在 R2 代码、共享 storage 或 unit 改变时读取 timer 原启停状态，停止 timer 和正在运行的 oneshot，再安装代码/unit；主服务启动并检查通过后仅恢复原 active timer，不直接触发 prune。本次原已 paused，将保留 paused；若发布中途失败，重试后须重新核对原 timer 状态，不能据重试成功推定归档已恢复。R2 6 项及发布器 24 项本地回归通过，实际新路径、归档耗时和 timer 恢复待 VM 验证。

## 2026-09-27：PR #91 已发布，正式验收继续

#91 七项 CI 全部通过，合并及运行版本为 `a51f0d69f164505947d5a21b387d74d5fcefb0eb`。发布包 709,551 字节，SHA-256 `c892c2900bc7cbf37e5372bd4c9298041af961f6bf67943ec9874912f2f90b6e`。此次发布退出码 0，八个服务 active，manifest 86 个文件哈希全部匹配；health 返回 200、耗时 0.008735 秒，可用空间约 2.6 GiB。

`prepare-only` 运行 1,089.319 秒，期间旧服务在线。完成时概率采集游标 2,812,761，天气投影 3,304 点、21 个 CLI 记录、seq 3,280,696。后续 `apply` 在线追补 9.712 秒，stop 阶段 90.262 秒，停机后 final 增量 21.359 秒。此前 #90 首次部署因约 16 GB 原库新索引扫描而中断，已先恢复旧代码；本次复用已有索引及独立源码在线预热，保留原始数据。恢复过程中通过 SQLite 正常 TRUNCATE checkpoint 释放 WAL，未手动删除文件。

发布后真实天气接口四次 0.710 / 0.056 / 0.194 / 0.038 秒，1,500 点历史页四次 6.263 / 3.228 / 6.300 / 0.742 秒。Poly US 抽查的 1,500 点均有原生概率，Kalshi 昨日缺概率区段保留缺口；正式网页已加载新 UI。浏览器历史冷页仍偶发 10 秒超时，错误当前落在天气区。`codex/knyc-probability-acceptance` 正在缩短至 300 点分页并分区显示错误，后续修正尚未发布，不能标记本轮完整正式验收通过。

R2 timer 仍暂缓，发布后恢复与核验尚未完成；后续发布前继续复核既有空间门槛。时间范围/bin 连续切换、信号归属和 Paper 完整流程继续按 [本轮验收记录](acceptance/probability-paper-20260927.md) 执行。下方旧版本与恢复记录保留历史范围，当前运行版本以上述 SHA 为准。

## 2026-09-27：派生查询在线预热

已审核发布包先运行 `sudo python3 scripts/update_knyc_runtime.py <archive> <sha256> --prepare-only`。更新器校验包、清单与现役基线，将固定六个准备模块保留在 `/opt/nice-weather-knyc-prepare/<archive_sha256>/`，以 `nice-weather` 用户、现役虚拟环境和该目录的 `PYTHONPATH` 在线构建概率查询表与天气投影；不停止服务或替换现役代码。每阶段开始/完成输出 JSON，批次游标支持中断后重试。

随后对同一包运行 `--apply`；它会先在线追补，再停止受影响服务、写入新版源码、完成最后增量，成功后提交 manifest 并启动。任一在线准备失败时现役服务保持运行；停服后的准备失败保留旧 manifest 供原包重试。六个源码文件约 70 KB，不复制数据库或虚拟环境；停服等待与准备耗时分别记录。staging 保留待人工审核清理，更新器不自动删除。

## 2026-09-23 任务 6 当前生产版本

`niceweather.trade/` 的 KNYC 终端已原位更新至 `0c3ac70a517acb66a24f37274b64ce38663c8da3`。发布包 SHA-256 为 `4b1d9c36615ab2c86b108345c2466815a5b37f65985065598bcf4bdae702e0f0`，大小 705,892 字节；manifest 86 个文件哈希一致。更新器预览仅改变 `terminal_dist/index.html` 和 `assets/index-CLW0uzcX.js`，只重启 `nice-weather-terminal.service`。旧 `assets/index-DWyKH0wS.js` 留在 `obsolete_pending_review`，尚未删除。VM 可用约 19 GiB，KNYC 采集、HRRR、回测、两 Paper、两 Live 实例均运行；数据目录和现有虚拟环境未改动。前一兼容代码包留本地用于按精确提交回滚，回滚不覆盖业务数据。

## 2026-09-28 PR #98 市场原文留存修复

运行版本 `d390ca8f2fa55808d4b5b930857d88fb64025438` 已部署。根盘曾达到 97% 使用率，feed/HRRR 在可用空间低于 1 GiB 时触发既有保护并反复自动重启。根因是旧 `FeedStore.capture` 为 Kalshi/Poly 保存压缩响应正文，KNYC R2 只处理天气来源，市场正文因此持续留在 SQLite。

当前 `FeedStore.capture` 只为 `metar`、`nws_observations`、`cli_index`、`cli`、`hrrr_index` 保存正文；市场仍保留 `captures` 元数据、哈希及规范化行情，Kalshi/Poly 原文不进入 VM 或天气 R2。KNYC 天气正文沿用 KLGA 口径，R2 上传并 GET 回读核验通过后再清空 VM 正文。`nice-weather-knyc-r2.timer` 仍 active，00:52:03–00:59:37 UTC 一轮 exit 0，24 个天气对象验证并清理成功。`settlement_evidence` 记录为零，数据目录没有采集截图文件。

一次性清理已清空 24,132 条历史非天气正文，共 2,142,918,106 压缩字节；保留采集元数据、规范化行情、订单、成交与账本。首批增量回收把 SQLite 从 17,930,833,920 字节缩至 16,593,846,272 字节，物理释放 1,336,987,648 字节；第二批正文已清空，当前主库 16,583,704,576 字节。全历史清理仍未完成，在线清理已暂停。

在线手动清理/增量回收时，feed 于 01:55 与 01:56 UTC 记录 SQLite `database is locked` 并自动重启，01:56:23 UTC 恢复。02:01:46 UTC 又记录一次锁错误，feed 于 02:02:01 UTC 恢复；该错误发生在 R2 服务运行时段，时间重叠不足以认定由 R2 引起。手动清理脚本均已退出。

R2 周期于 01:59:44–02:43:29 UTC 运行，exit 0，核验并清理 7 条天气正文。02:04 至 02:24 UTC 根盘可用空间从 2,507,543,336 降至 2,296,840,192 字节；02:24 UTC 主库为 16,600,502,272 字节，`feed.sqlite3-wal` 为 226,068,552 字节。周期结束后 WAL 降至 4,849,272 字节，主库为 16,583,704,576 字节，02:50:54 UTC 根盘可用空间回升至 2,525,757,440 字节（92% 已用）。这次下降主要是 R2 周期中的 SQLite WAL 暂存，周期结束后空间已回收。只读抽查最新 10 条 Kalshi/Poly 记录，body 长度均为空。

为缩短下一轮扫描，当前工作分支把 R2 候选查询改为按 `capture_bodies.hash` 有界分页，并用既有 `captures_hash` 检查来源，不再重复分页扫描约 307 万条 `captures` 元数据。该改动沿用现有归档表、服务与对象前缀，保留上传、GET 回读、哈希核验、并发市场引用检查后清空的流程；R2 保留及发布回归共 36 项通过，Ruff 通过，PR/生产部署和下一周期时长待验。

## 2026-09-22：天气原文验证归档后自动清理

用户已批准清理 VM 市场历史、旧账本和模拟数据。天气原文仅在 R2 GET 回读与本地字节、SHA-256、大小和记录身份一致后清空；解析结果、来源时间、实际 received_at、版本、哈希及远端对象索引保留。实盘账户与订单记录不属于本次旧账本清理。

- 旧 KLGA：`nice-weather-r2-sync.service` 的 `verified-retention.conf` 设置 `R2_PRUNE_VERIFIED_RAW=true`，沿用 15 分钟 timer。首次积压允许长时间执行；失败保留未验证原文。
- KNYC：`nice-weather-knyc-r2.timer` 在上次任务结束 15 分钟后调用 `nice_weather.trading.r2_retention --db /var/lib/nice-weather-knyc/feed.sqlite3 --prune`。使用同一服务器 R2 环境文件，原始对象位于 `knyc/v1/weather-raw/`。
- KNYC 市场采集只保留来源、URL、请求/接收时间及哈希；Kalshi/Poly body 不写盘，也不进入天气 R2。存量市场 body 的一次性清理只清空原文字段并保留 `captures`、规范化行情与订单/账本记录。
- 旧混合库退役：`scripts/archive_legacy_weather.py` 只归档天气表，逐对象回读校验后写远端 manifest；移除原库前再次核验文件大小/修改时间、占用、WAL 与远端 manifest。不能用上传台账代替真实回读。
- 首次通过保留天气的副本压缩，设置 SQLite `auto_vacuum=INCREMENTAL`；以后清空原文字段后回收空闲页。逻辑字节清空和磁盘容量回收分别记录。
- 旧 Poly Intl 市场、runner、sandbox、backtest 服务停用，保留 KNYC 服务及 KLGA 天气采集。回滚代码不能恢复清理前的旧数据库覆盖新记录。


2026-09-22 任务 6 补充：前端在本地构建。update_knyc_runtime.py 纳入市场目录、信号、Paper、回测与 Live 模块的实际服务映射；只停止已经安装且受影响的实例。新增 nice-weather-knyc-live@kalshi/poly_us，读取 /etc/nice-weather/knyc-live-{venue}.env 中的 NICE_WEATHER_LIVE_CREDENTIALS 文件路径与 NICE_WEATHER_BALANCE_PRECISION（Kalshi 4、Poly US 2）。凭据存 /etc/nice-weather/credentials，root:nice-weather、640；环境文件只含路径/精度，不含密钥。两实例使用现有虚拟环境和 /var/lib/nice-weather-knyc，初始交易关闭。部署前核验包大小、实际展开增量及可用空间；不增加固定容量门槛，不修改采集数据。

2026-09-19 存储修订：GitHub 保存代码历史，VM 只保留正在使用的运行目录和依赖环境，不按每次提交复制虚拟环境或累计回退包。代码回退从 GitHub 的精确提交重建发布包，校验后更新现役目录；只临时上传本次产物。版本由 runtime-manifest.json 和服务记录标识，旧物理目录名不能单独作为当前版本证据。部署成功后的临时包、废弃资源及旧目录列入精确清理清单，经已有明确授权或人工审核后删除；业务数据库不跟随代码回退。

`scripts/update_knyc_runtime.py` 对已安装 KNYC 环境执行有限范围的更新，默认仅检查；`--prepare-only` 仅准备派生查询，`--apply` 准备后更新现役代码。验证发布包、manifest、现役基线与路径。终端改动只重启终端；`feed.py` 改动重启引用它的六个 KNYC 服务；仅 `us_runtime.py` 改动时重启两个 Paper、回测 worker 和用于刷新版本身份的终端，公开采集/HRRR 及旧 KLGA 服务保持运行。其他模块的改动拒绝应用，需单独核验影响服务。重复执行可接续相同清单的部分写入，不将旧账目恢复覆盖现役数据库。它仅保留上述六文件准备目录，不复制环境，不删除业务数据。

KNYC 终端按精确 SHA/产物清单部署，仅重启受影响服务。用户已授权常规部署，不要求逐步确认。实盘默认关闭，用户自行启用。回滚只切代码与环境，禁止用旧数据库覆盖新增订单/成交/采集数据；不兼容时暂停受影响交易服务并保留证据。

2026-09-19 用户明确补充 SSH 来源授权，适用目标和边界见仓库 AGENTS.md。`ssh.cloud.google.com` 已登录会话的访问、正常登录与重连、扩展重启后的同一来源恢复、普通 Connect/Retry，以及上传审核后的发布包和执行本项目部署命令，均已授权，不逐次询问。先核验项目/实例身份和当前输出；连接恢复后接续已有步骤，避免重复安装或覆盖数据。平台独立权限审批与未核验安全警告不能靠文档绕过，若受阻需准确说明。

下文为旧 KLGA 部署操作参考，其中全服务停机、自动恢复旧数据库和禁止实盘开发的条款不再适用于新终端发布。迁移必须先验证备份和兼容性。新终端验收前保留旧服务。

KNYC 于 2026-09-19 16:42:11 UTC 首次安装，后续原位更新沿用该目录与环境。`https://niceweather.trade/terminal` 已复用现有 Cloudflare Access；旧入口保留。最新运行版本、产物哈希、验证结果及剩余事项见 [部署记录](acceptance/knyc-vm-2026-09-19.md)。公网可访问仍不足以通过完整产品验收。

# Ubuntu VM 部署与自动验收

本文以 schema v7 统一部署为 canonical 流程。后续旧 `weather.sqlite3` 双目录命令仅用于回滚参考。

## 0. 统一部署摘要

- checkout：`/opt/nice-weather/repo`
- venv：`/opt/nice-weather/.venv`
- 配置：`/etc/nice-weather/nice-weather.toml`
- 数据库：`/var/lib/nice-weather/nice-weather.sqlite3`
- 服务：collector、market-stream、r2-sync timer、dashboard、shadow runner
- R2 新前缀：`nyc-klga/v2`

部署前记录远端合并 SHA，并写入 `/etc/nice-weather/nice-weather.env`：

```dotenv
NICE_WEATHER_GIT_SHA=<merged-main-sha>
```

停写顺序：

```bash
sudo systemctl stop nice-weather-r2-sync.timer
sudo systemctl stop nice-weather-runner.service
sudo systemctl stop nice-weather-market-stream.service
sudo systemctl stop nice-weather-collector.service
sudo systemctl stop nice-weather-r2-sync.service
sudo systemctl stop nice-weather-dashboard.service
```

已有统一库从 schema v4 升级时，先使用 SQLite backup API 保存精确副本，再执行原位迁移和验证：

```bash
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db migrate \
  --db /var/lib/nice-weather/nice-weather.sqlite3
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db verify \
  --db /var/lib/nice-weather/nice-weather.sqlite3
```

验证结果必须为 schema version 7、`integrity_check=ok` 且无 foreign key error。旧天气原文不在
本次迁移中删除，迁移后的空闲页交给 SQLite 后续写入复用。

确认无 writer 后，备份并迁移：

```bash
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db clone-migrate \
  --source /var/lib/nice-weather/weather.sqlite3 \
  --db /var/lib/nice-weather/nice-weather.sqlite3
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db verify \
  --db /var/lib/nice-weather/nice-weather.sqlite3
```

安装仓库内五个 unit，启动顺序固定为：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nice-weather-collector.service
sudo systemctl enable --now nice-weather-market-stream.service
sudo systemctl enable --now nice-weather-r2-sync.timer
sudo systemctl enable --now nice-weather-dashboard.service
sudo systemctl enable --now nice-weather-runner.service
```

部署先运行 `repair-settlement-dates --dry-run`，备份验证后执行 `--apply`。至少观察 16 分钟并检查 `version --json`、五个 unit、collector status、R2 ledger、Dashboard health、CLOB tick 和 SHADOW 决策。随后由部署观察服务持续检查 24 小时；关键日期、数据库完整性、服务或锁异常触发恢复上一精确 SHA 与数据库备份。禁止把 Runner 改为任何实盘模式。

旧库处置分两次人工检查：先逐一确认并移动 `/var/lib/nice-weather/live.sqlite3`、`live.sqlite3-wal`、`live.sqlite3-shm` 到固定隔离目录；24 小时后再次列出精确绝对路径并请求删除批准。不得使用 glob 或递归删除。

## Repricing v7 部署补充

1. 记录当前 SHA、服务状态、migration checksums 和 tick 数；核对 v4/v5/v6 校验和与已部署代码一致。校验和不符时停止迁移并调查，禁止直接覆盖记录。
2. 停止全部 writer，包括 market-stream、collector、runner 和 r2-sync timer/service。通过 SQLite backup API 将统一库复制到带 UTC 时间的独立路径，保留原 SHA 和环境文件副本；不删除历史备份。
3. 在备份的工作副本上执行 v7 migrate/verify，确认 `event_kind` 可空、旧 tick 数量不变、`integrity_check=ok`、无 foreign key error，再更新线上代码和迁移原库。
4. 恢复服务后检查版本 SHA/schema7、33 tokens 等实际发现数量、CLOB/Gamma 各自事件类型、最新接收时间，以及 dashboard/collector/market-stream journal 中的 SQLite/WAL/SHM 访问错误。
5. 浏览器核对今日和未来市场、全部 bin、数据源状态、差值及断流恢复。旧 tick 来源状态仍未经核验；截图中的每个归零需逐条对照原始 tick，不能批量归因或删除。

## 1. Cloudflare 与 GitHub 检查

1. 在 Cloudflare R2 控制台确认现有 bucket 名称。
2. 确认 API Token 对该 bucket 具有对象读写权限。
3. 在 GitHub PR 的 Files changed 中确认没有 `weather data api.txt`、Access Key 或 Secret Access Key。
4. 等待 `lint`、`unit`、`fixture-dashboard` 全部通过，再 squash merge。

## 2. 安装程序

仓库现为 private。现有开发机通过 Git Credential Manager 认证后仍可正常 fetch/push；
不要把凭据写进 remote URL、代码、聊天或部署命令。VM 若已配置对该仓库的只读访问，
可继续使用原有认证；尚未配置时，使用下列离线 Git bundle 流程将已审核提交传到同一项目 VM，
无需将开发机 GitHub Token 保存到 VM。

在已认证的开发机确认 PR 检查与精确 SHA 后：

```powershell
git fetch origin main
git rev-parse HEAD
git bundle create var/nice-weather-reviewed.bundle HEAD
Get-FileHash var/nice-weather-reviewed.bundle -Algorithm SHA256
scp var/nice-weather-reviewed.bundle <existing-ssh-alias>:/tmp/nice-weather-reviewed.bundle
```

VM 上先核对传输哈希、当前 SHA、工作区和服务状态；本轮接入采用独立环境，
保留原库及所有交易日志。下列 fetch 只导入对象，切换代码和重启仍须按照已通过验收的部署步骤执行。

```bash
sha256sum /tmp/nice-weather-reviewed.bundle
sudo -u nice-weather git -C /opt/nice-weather/repo status --short
sudo -u nice-weather git -C /opt/nice-weather/repo rev-parse HEAD
sudo -u nice-weather git -C /opt/nice-weather/repo bundle verify /tmp/nice-weather-reviewed.bundle
sudo -u nice-weather git -C /opt/nice-weather/repo fetch /tmp/nice-weather-reviewed.bundle HEAD
sudo -u nice-weather git -C /opt/nice-weather/repo rev-parse FETCH_HEAD
```

以下保留首次安装命令；`git clone`/`pull` 需要 VM 已有的仓库访问权限。若使用 bundle 首次安装，
将 clone 来源替换为已校验的 bundle 文件。不要为完成部署扩大仓库共享范围。

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv
python3 --version
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
sudo useradd --system --home-dir /opt/nice-weather --create-home --shell /usr/sbin/nologin nice-weather
sudo install -d -o nice-weather -g nice-weather -m 0750 /var/lib/nice-weather
sudo install -d -o root -g nice-weather -m 0750 /etc/nice-weather
sudo -u nice-weather git clone https://github.com/RENEGADES20/nice-weather.git /opt/nice-weather/repo
sudo -u nice-weather python3 -m venv /opt/nice-weather/.venv
sudo -u nice-weather /opt/nice-weather/.venv/bin/python -m pip install --upgrade pip
sudo -u nice-weather /opt/nice-weather/.venv/bin/python -m pip install -e '/opt/nice-weather/repo[collector]'
sudo /opt/nice-weather/.venv/bin/playwright install-deps chromium
sudo -u nice-weather env PLAYWRIGHT_BROWSERS_PATH=/opt/nice-weather/.playwright /opt/nice-weather/.venv/bin/playwright install chromium
sudo install -o root -g nice-weather -m 0640 /opt/nice-weather/repo/config/nyc_klga.toml /etc/nice-weather/collector.toml
```

若仓库已经存在，使用 `sudo -u nice-weather git -C /opt/nice-weather/repo pull --ff-only origin main` 更新。

## 3. 写入 R2 环境文件

不要把真实值直接放在命令行参数中。使用 root 权限编辑文件，减少 shell history 泄漏。

```bash
sudo touch /etc/nice-weather/r2.env
sudo chown root:nice-weather /etc/nice-weather/r2.env
sudo chmod 0600 /etc/nice-weather/r2.env
sudoedit /etc/nice-weather/r2.env
```

文件内容：

```dotenv
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_BUCKET=<现有bucket名称>
R2_ACCESS_KEY_ID=<本地TXT中的Access Key ID>
R2_SECRET_ACCESS_KEY=<本地TXT中的Secret Access Key>
R2_PREFIX=nyc-klga/v1
```

检查权限，输出只应包含文件元数据：

```bash
sudo stat -c '%U %G %a %n' /etc/nice-weather/r2.env
```

期望值为 `root nice-weather 600 /etc/nice-weather/r2.env`。

## 4. 单轮验证

```bash
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather config-check --config /etc/nice-weather/collector.toml
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather collect-weather --once --db /var/lib/nice-weather/weather.sqlite3 --config /etc/nice-weather/collector.toml
sudo bash -c 'set -a; . /etc/nice-weather/r2.env; set +a; exec runuser -u nice-weather --preserve-environment -- /opt/nice-weather/.venv/bin/nice-weather r2-check --db /var/lib/nice-weather/weather.sqlite3 --config /etc/nice-weather/collector.toml'
```

第三条命令会在 R2 的 `healthchecks/` 下留下一个小型不可变测试对象，不执行删除。

## 5. 启动 systemd

```bash
sudo install -o root -g root -m 0644 /opt/nice-weather/repo/deploy/systemd/nice-weather-collector.service /etc/systemd/system/nice-weather-collector.service
sudo install -o root -g root -m 0644 /opt/nice-weather/repo/deploy/systemd/nice-weather-r2-sync.service /etc/systemd/system/nice-weather-r2-sync.service
sudo install -o root -g root -m 0644 /opt/nice-weather/repo/deploy/systemd/nice-weather-r2-sync.timer /etc/systemd/system/nice-weather-r2-sync.timer
sudo systemctl daemon-reload
sudo systemctl enable --now nice-weather-collector.service
sudo systemctl enable --now nice-weather-r2-sync.timer
```

## 6. 状态与 24 小时验收

```bash
sudo systemctl status nice-weather-collector.service --no-pager
sudo systemctl status nice-weather-r2-sync.timer --no-pager
sudo journalctl -u nice-weather-collector.service -n 100 --no-pager
sudo journalctl -u nice-weather-r2-sync.service -n 100 --no-pager
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather collector-status --db /var/lib/nice-weather/weather.sqlite3 --config /etc/nice-weather/collector.toml
```

运行 24 小时后确认：三类 API 均有新版本、Weather.gov 页面无持续解析错误、R2 存在 raw/evidence/parquet/manifest、预计日增量不超过 10 MiB。达到 7 GiB 时状态命令输出 `warning=true`。2026-09-22 起，天气原文按本页顶部规则在 R2 回读核验后自动清理；未核实的对象继续保留。

## 7. 回滚

```bash
sudo systemctl disable --now nice-weather-collector.service nice-weather-r2-sync.timer
sudo -u nice-weather git -C /opt/nice-weather/repo log --oneline -5
sudo -u nice-weather git -C /opt/nice-weather/repo switch --detach <上一稳定commit>
sudo systemctl start nice-weather-collector.service
sudo systemctl start nice-weather-r2-sync.timer
```

回滚不删除 `/var/lib/nice-weather` 或任何 R2 对象。系统稳定后，由用户人工将本地 TXT 移入密码管理器或删除；项目不会代为删除。

## Nautilus 独立工作台服务

新增 sandbox/backtest service 及 dashboard drop-in；Python 3.12 独立环境，操作目录 `/var/lib/nice-weather-trading/requests`，结果库位于 `/var/lib/nice-weather-trading`。正式部署、完整市场日观察和回滚流程见 [TRADING_WORKBENCH.md](TRADING_WORKBENCH.md)。回滚不得覆盖已经新增的模拟/交易日志。真实服务本轮不启用。
