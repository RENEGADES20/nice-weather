# 当前状态

最后更新：2026-09-05

## 当前阶段

项目处于“KLGA 统一天气存储与阶段 A Shadow Runner 已部署，Tmax 重定价研究采集进入 schema v6”的阶段。

## 2026-09-05 Repricing 数据链路修复（本地验收，待线上核验）

- 当前实现升级到 schema v7：`market_top_ticks.event_kind` 可空，新增 event/bin/接收游标索引。Gamma、CLOB 按来源和 token 隔离；trade 只保存真实成交，不刷新旧盘口或旧成交时间。同价新成交保留，重复消息幂等。旧 tick 保留并标为未经核验，旧成交不参与五分钟回退。
- CLOB midpoint 只保持到最后报价/完整快照接收后十分钟；已知断流即时失效，随后依次尝试五分钟真实成交和十分钟 Gamma。内部概率统一 `[0,1]`；零值保留，缺失形成缺口。
- Market Stream 动态保存所有符合 NYC/KLGA 规则的开放市场。2026-09-05 官方接口实测发现 09-05/06/07 三个市场、33 个 YES tokens，保存 33 条 CLOB 快照及 33 条 Gamma 状态；Runner 原选择及 SHADOW 不变。
- Repricing 仅显示所选市场日，以纽约当地午夜计算 23/24/25 小时窗口。当天/历史日主图和六组差值共享一分钟 as-of 输入；未来日只显示最新已知 NWS 逐小时预报、当前价格水平线与 Price×100−Forecast，显式标注当前快照，不生成未来历史价格或回测输入。
- 控件独立 fragment；天气按版本复用，价格按目标窗口及接收游标增量查询，普通 tick 只重算受影响分钟。跳过完整快照后的重复 feed 计算；无数据变化仍推进年龄与过期状态。
- full/delta 使用选择签名、递增序号和基线序号；缺序重同步、旧选择丢弃、完整快照替换、撤销点使用 tombstone。查询失败保留相同选择最后成功画面；恢复完整重同步。鼠标悬停继续更新，缩放/拖动只关闭 Follow latest。
- 两图采用真实时间范围和共同透明时间基底，保持不等间隔价格与分钟差值对齐；ResizeObserver 按实际内容通知 iframe 高度，支持隐藏页签、窄屏和全屏退出。
- 验证：Python 80 项、TypeScript/Vite 构建、桌面/移动端浏览器 8 项通过。测试涵盖来源污染、零值、成交时间、过期/断流、恢复游标、DST、未来快照和全量/增量一致性；v6→v7 备份迁移回归保留旧 tick。
- 单会话本地复测：已加载 bin 切换 p95 739ms，查询/计算 56ms，渲染 35ms。双会话同时压测：后台查询/计算+渲染 p95 桌面 457ms、移动端 354ms；接收到可见更新 p95 桌面 2347ms、移动端 2395ms；已加载 bin 切换 1616/2029ms，超过 1 秒目标，仍有并发性能限制。测试数据规模不代表线上长期库。
- 两份旧本地开发库 v6 migration checksum 与当前提交不一致，复制备份后迁移被正确拒绝；未改写校验和。线上部署需先核对 migration 记录、备份及完整性，不能沿用这些开发库的结果。线上 v4/v5/v6 checksums 已核对一致，统一库约 1.35GB、517881 条 tick（检查时点）；四个主要服务 active，09-05 journal 确认多次 unable to open database file。迁移前备份正在核验，原库尚未迁移。

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
