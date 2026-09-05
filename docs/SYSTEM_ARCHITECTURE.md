# 系统架构概览

双图时间范围同步由当前操作的图单向驱动，输入捕获阶段确定驱动图；程序化 Follow/Reset/完整窗口切换由主图驱动，避免两个范围回调持续相互触发。

天气查询的 observed_at/valid_at 保留来源时区偏移，窗口筛选及排序通过 SQLite julianday 统一比较实际时刻；市场日边界仍由纽约当地午夜转换 UTC，覆盖 23/25 小时 DST 日。采集 received_at 使用 UTC，as-of 截止保留微秒精度，历史存储不改写。

最后更新：2026-09-05

本文描述模块职责、输入、输出和主要失败方式。具体模型权重、风险比例、交易阈值、数据库字段和运行参数留给后续技术设计。

Repricing 图表 transport 使用 gzip/base64 封装原有 full/delta/status payload，父页面仅转发压缩内容。iframe 原生解压后执行原有数据与选择校验；相同传输去重，解码失败保留最后成功画面并提示。该层不改变时间索引、价格精度或历史计算输入。

绘图顶点与审计数组分开：阶梯线只绘制值变化和非空段端点，悬停仍从完整审计点取 as-of 输入。两图共同时间基底合并绘图顶点与一分钟网格。SQLite 写连接使用最多 30 秒的原生锁等待，超过五秒记录等待/持锁耗时；数据库仍保持 WAL 和 Dashboard 只读。

会话内按 bin 保存三类来源候选、待到事件时间的消息和最新实际数据时间；普通刷新只处理新增及待到时消息，避免反复扫描整日历史选择当前报价。原始历史点和分钟差值缓存继续独立保留。

## 0A. 当前统一运行架构

```text
官方天气源 -> Collector -> nice-weather.sqlite3/WAL -> R2 v2
                               |
                               +-> WeatherRepository(as-of)
                                        |
Polymarket Gamma -> Runner -> 阶段 A 分布 -> 候选 token -> CLOB Quote
                                        |                     |
                                        +-> Decision / Paper <-+
Dashboard <---------------------- 统一库只读查询
```

Tmax 重定价研究旁路：

```text
Gamma 合约发现 -> CLOB WebSocket 全部 YES token -> market_top_ticks
                                                        |
对象时间天气流 -> Tmax knowledge events ----------------+-> repricing report
                                                        |
Dashboard Lightweight Charts <- BroadcastChannel 数据泵 -----+
```

- schema v7 市场流保存 nullable event_kind、独立来源/token 的报价或真实成交及双时间。Gamma 与 CLOB 状态隔离；A→B→A 与同价新成交保留，重复消息幂等。旧无类型记录保留核验限制，旧成交不参与近期成交回退。
- 断线重连先以批量 CLOB book 恢复状态，Gamma 只作发现和 fallback，不进入可交易延迟统计。Market Stream 保留单个 SQLite 连接，各 tick 仍独立提交，避免逐 tick 关闭最后连接、清理 WAL/SHM 导致只读 Dashboard 无法打开数据库；进程退出时关闭连接。
- 图表组件使用随 Python 包发布的静态资源；Repricing 在同一浅色 Lightweight Charts 实例内用双 pane 分离天气温度与唯一所选温度档的 Price。下方 Difference 图与主图双向同步时间范围和十字线，显示六组固定普通差值。
- 可见组件只挂载一次；独立零高度两秒 Streamlit fragment 查询当前窗口，通过 session channel ID 对应的单个 `BroadcastChannel` 发送 feed。前端按 series ID 和 timestamp 合并增量，使用 signature 与 `selectedBinId` 拒绝旧选择消息；单一市场日、温度档或主图来源集合改变时全量协调。选择签名和 full/delta 序号/基线共同验证消息，缺序请求 full；完整快照替换，撤销点显式移除。天气版本缓存与价格接收游标减少重算，失败保留最后成功画面。ResizeObserver 通知父 iframe 实际高度，两图使用真实时间范围和共同透明时间基底。浏览器缺少 `BroadcastChannel` 时停止自动 feed，不采用周期性重建回退。
- Difference 的基础输入固定为 Forecast、Weather.gov Hourly Temp、METAR 和 Price。后端在纽约一分钟网格上重建每个 t 当时已收到的最新有效值：Forecast 只在同一已知 capture 内线性插值，METAR 与 Weather.gov Hourly Temp 按对象时间和 90 分钟新鲜度保持，Price 按十分钟内 CLOB 报价/快照接收时间、五分钟内真实成交、十分钟内 Gamma 回退，断流即时失效。主图历史温度复用差值输入；NWS Station Observations 仍为可选线。未来日只使用当前已知 NWS 预报与当前 Price 水平线，计算单一快照差值，不生成历史价格或回测数据。
- Difference 固定计算三个天气 `°F` 差值和三个 Price `display spread`；任一输入缺失时保留真实断线。它只供人工研究展示，不进入研究延迟标签、阶段 A、风险、Paper、历史标签或交易逻辑。
- 对象时间决定纽约市场日、Tmax、forecast 覆盖、日落和结算。Dashboard 展示统一固定 `America/New_York` 并标注 `ET`；浏览器时区只在 Overview 显示当前 DST 时差。

- Collector 写 `poll_attempts` 和内容变化后的 `source_captures`；天气原文只在该表压缩保存，
  `raw_snapshots` 仅保留迁移前天气记录及市场兼容记录。各来源失败独立记录。
- Runner 只从 Repository 获取天气，所有 as-of 查询强制 `received_at <= decision_time`。
- 阶段 A 使用 NWS 预报与 Weather.gov 官方已实现 Tmax 下界；高频观测只提供诊断和趋势特征。
- CLOB 只对概率门槛候选、持仓和未完成 Paper order请求；生产只保存有限 `execution_quotes`。
- Gamma 版本按内容哈希寻址，完整事件 JSON 仅在内容变化时进入 `market_captures`；
  `raw_snapshots` 中的 Gamma 记录只保存轻量内容引用。
- R2 v1 保持不可变，新数据进入 v2；allowlist 排除所有市场、决策、Paper 和资金数据。
- SQLite 使用 WAL、5 秒 busy timeout、短 `BEGIN IMMEDIATE` 事务和 Runner lease。歧义、过期或锁超时均进入可审计 `no-trade`。

## 0. 纽约 KLGA 标准 MVP

第一版固定 Polymarket 纽约机场站每日最高温市场：

- 城市：纽约。
- 站点：KLGA / LaGuardia。
- 执行：Paper Trading。
- 周期：标准 MVP，2–3 周。
- 当前不配置实盘密钥，不发送真实订单，不进行自动资金操作。

首版运行主链：

```text
CityConfig(NYC / KLGA)
→ MarketDataAdapter / ObservationAdapter / ForecastAdapter
→ UnifiedState + SQLite
→ 合约解析与规则校验
→ ProbabilityModel
→ Signal
→ 领域风险审批
→ PaperBroker
→ 订单、P&L 与数据健康监控
```

首版稳定接口：

- `CityConfig`：城市、机场站、时区、温标、观测窗口和结算口径。
- `MarketDataAdapter`：Polymarket 市场、合约、价格和市场状态。
- `ObservationAdapter`：METAR、ASOS 与历史站点观测。
- `ForecastAdapter`：NWS、ECMWF 等预报产品及模型运行时间。
- `UnifiedState`：某个决策时刻已经收到的市场、规则、观测与预报。
- `ProbabilityModel`：从 `UnifiedState` 生成各温度档概率。
- `ExecutionAdapter`：执行风险批准后的订单意图；首版实现为 `PaperBroker`。

### 两部分分工

天气数据与模型：

- 接入 METAR、ASOS、NWS、ECMWF 和历史站点数据。
- 管理天气数据的 `observed_at`、`received_at`、版本、质量和存储。
- 构建 KLGA 特征、当前天气状态和基线 Tmax 分布。
- 按 `MarketContract` 的温度档输出 `ProbabilityEstimate`。
- 输出 `DataHealth` 并监控数据与模型异常。

交易系统：

- 接入 Polymarket 市场、合约、订单簿和市场状态。
- 解析站点、日期、温标、舍入、档位和结算来源，生成 `MarketContract`。
- 比较 `ProbabilityEstimate` 与可成交价格，生成 `Signal`。
- 执行风险审批、`no-trade`、Paper 订单、持仓、P&L 和交易监控。

接口方向：

```text
交易系统 → 天气数据与模型：MarketContract + decision_time
天气数据与模型 → 交易系统：ProbabilityEstimate + received_at + DataHealth
```

双方共同维护接口 schema、`no-trade` 原因码、集成测试和发布验收。

核心数据对象：

- `MarketContract`。
- `WeatherObservation`。
- `ForecastSnapshot`。
- `UnifiedState`。
- `ProbabilityEstimate`。
- `Signal`。
- `PaperOrder`。

每条天气和市场快照至少保留 `observed_at`、`received_at`、来源和版本。站点、日期、时区、温标、舍入、观测窗口、结算来源或数据新鲜度存在无法消除的歧义时，输出带原因的 `no-trade`。

标准 MVP 实施顺序：

1. 第 1 周：项目结构、NYC/KLGA 配置、三类数据适配器和 SQLite。
2. 第 2 周：合约解析、基线 Tmax 概率、信号、风险控制和 `PaperBroker`。
3. 第 3 周：持续 Runner、订单生命周期、P&L、数据健康、异常停机和演示验收。

大型历史回放平台、复杂概率校准、多城市比较、高可用部署和实盘执行后置。Nautilus Trader 保留为候选运行层，不阻塞纽约 MVP。

下列分层继续作为长期扩展架构；首版只实现支撑纽约 Paper Trading 闭环的最小子集。

### 独立天气采集与归档

2026-08-27 起，天气采集从决策 Runner 的轮询中拆出独立进程。AviationWeather METAR、NWS hourly forecast、NWS station observations 和 Weather.gov 结算页面按各自频率进入同一个 SQLite WAL。每份版本保存来源时间、接收时间、内容哈希和压缩原始响应。NWS station observations 使用两小时重叠窗口，标准化行按观测时间和内容版本去重，避免每五分钟重复保存完整日内历史。

schema v5 将新增观测和预报直接关联 `source_captures`；迁移前记录继续通过
`legacy_snapshot_id` 关联 `raw_snapshots`。新决策通过 model prediction 对应的 feature snapshot
保存完整天气 capture ID 集，避免每分钟重复写数十条关系；`decision_weather_inputs` 继续读取
迁移前关系。数据健康为 BLOCKED 时，决策在数据门控层结束，不再生成市场、edge、现金或
城市限额原因码。

SQLite 允许采集器和 Paper Runner 以短事务并发写入；决策 Runner 的 lease 继续防止两个交易 Runner 同时运行。R2 同步器只读取已提交记录，上传内容寻址的原始批次、结算截图、每日 Parquet 和 manifest。R2 与本地存储均不自动删除。

## 1. 研究证据层

输入：

- 天气市场作者原帖。
- 官方市场规则。
- 气象知识。
- 历史市场和天气数据。
- 盈亏、错误和异常案例。
- 可迁移的预测市场、做市与 HFT 经验。

处理：

- 保存原文与上下文。
- 拆成原子主张。
- 区分明确陈述和项目推断。
- 定义可证伪假设、反例和失效条件。

输出：

- 作者证据页。
- 策略假设。
- 数据需求。
- 风控和基础设施改进。

主要风险：成功案例选择偏差、营销内容、搬运、事后解释和无法复原的收益。

## 2. 市场与天气采集层

Polymarket 输入：

- 市场和事件元数据。
- 合约档位和 token。
- 规则与结算说明。
- L2 快照和增量。
- 成交和市场状态。
- 结算结果。

天气输入：

- METAR 和机场实况。
- 天气模型与集合成员。
- 模型运行时间和发布版本。
- 风、云、降水和露点。
- 历史站点观测。
- 官方最终报告和修订。

输出：带来源时间、接收时间和版本的标准事件。

主要风险：断流、重复、乱序、时间戳错误、来源修订和产品口径变化。

## 3. 事件存储与时间机器

职责：

- 追加保存原始事件。
- 保留修订历史。
- 构建某个决策时刻的 as-of 视图。
- 支持确定性重放。
- 保留数据健康与断流记录。

输出：给定时间点可用的市场、天气和规则状态。

主要风险：未来数据泄漏、跨源时钟错位、静默覆盖旧版本和无法重现历史状态。

## 4. 合约映射层

输入：市场规则、站点资料、当地日期、温标、档位和结算来源。

处理：

- 确认机场站。
- 解释观测日和时区。
- 解释舍入、截断和档位边界。
- 区分结算取值与结算触发条件。
- 按生效时间保存规则版本。

输出：标准化天气合约，或者带原因的 `no-trade`。

主要风险：站点变化、死档、档位缺口、来源冲突和自然语言规则歧义。

## 5. Tmax 概率层

输入：

- 多模型和集合预报。
- 机场历史偏差。
- 天气型。
- 预测时距。
- 当前实况与已观测最高温。
- 风、云、降水、露点和高空过程。

处理：

- 估计最终最高温连续分布。
- 根据新观测更新剩余升温空间。
- 按真实合约规则映射到离散档位。
- 确保互斥档位概率关系一致。

输出：每个温度档的概率、数据时间和模型版本。

主要风险：小样本过拟合、相关模型重复计权、制度切换、雷暴尾部、夜间最高温和站点传感器差异。

## 6. 策略层

输入：档位概率、Bid/Ask、订单簿深度、费用和当前持仓。

处理：

- 比较合理概率与真实可成交价格。
- 评估单档、相邻档和整组档位。
- 扣除费用、滑点、模型不确定性和执行摩擦。
- 记录候选来源和失效条件。

输出：待风险审批的交易候选。

主要风险：使用页面摘要价、忽视深度、组合残腿、过度依赖单次观测和市场已经完成定价。

## 7. 风险层

检查：

- 合约规则是否明确。
- 数据是否新鲜和一致。
- 净优势是否覆盖成本。
- 城市日库存是否集中。
- 市场是否有可执行深度。
- 系统是否能够撤单和退出。
- 当前天气制度是否在模型适用范围内。

输出：批准、限仓、延迟、撤销候选、`no-trade` 或 kill switch。

主要风险：逐笔风险看似安全，但整个城市日结果敞口高度集中。

## 8. 执行与库存层

职责：

- 接收风控批准的订单意图。
- 管理提交、接受、排队、部分成交、完全成交和撤单。
- 在新信息到达后撤销过时报价。
- 维护订单、现金、token 和结果情景库存。
- 断线后恢复状态并对账。
- 保存成交后的 markout。

主要风险：adverse selection、陈旧报价、ghost liquidity、部分成交、重复事件、撤单失败和本地状态漂移。

## 9. Nautilus 运行层

当前定位：

- 统一市场事件。
- 管理订单与持仓状态。
- 提供 Polymarket 接入。
- 连接回测、Sandbox、Paper 和未来可能的实盘接口。
- 支持重连、缓存与对账。

天气数据、合约规则、概率、策略和领域风险留在本项目模块中。

## 10. 回测、纸盘与评估

历史回测回答：

- 假设是否存在规律。
- 概率是否校准。
- 表现是否依赖少数幸运事件。
- 成本后是否仍有优势。

实时纸盘回答：

- 数据是否连续进入。
- 计算是否及时。
- 模拟成交是否符合订单簿条件。
- 撤单、重连、结算和对账是否可靠。

评估需要区分概率质量、方向信号、执行质量、库存损益和系统可靠性。

## 11. 建设顺序

```text
连续数据采集
→ 单机场日确定性回放
→ Nautilus Paper / Sandbox
→ 多城市与策略验证
→ 长期监控和版本迭代
```
