# 研究、Obsidian、Skill 与旧项目入口

最后更新：2026-10-10（第 7 节）

## 1. Obsidian 天气研究库

根目录：

```text
D:\ALLPROJECTS\x learner\obsidian vault\x scraper\weather forecast
```

根目录当前只保留阶段目录：

```text
阶段 0 - 天气博主筛选结果/
```

阶段性研究结果：

```text
D:\ALLPROJECTS\x learner\obsidian vault\x scraper\weather forecast\阶段 0 - 天气博主筛选结果
```

关键入口：

```text
00 总览与记录/01 运行记录.md
00 总览与记录/02 现有天气策略.md
00 总览与记录/03 通用策略大全.md
00 总览与记录/04 策略看板.base
00 总览与记录/05 策略导航.canvas
04 策略档案/01 天气 Alpha/
04 策略档案/02 通用 Alpha/
04 策略档案/03 风控与执行控制/
04 策略档案/04 待补证假设/
04 策略档案/05 证据缺口.md
01 高置信天气博主/00 作者索引.md
02 待人工复核/00 待复核索引.md
03 跨主题关注作者/00 作者索引.md
05 范围外线索/00 范围外作者与工具线索.md
```

高置信作者目录：

```text
D:\ALLPROJECTS\x learner\obsidian vault\x scraper\weather forecast\阶段 0 - 天气博主筛选结果\01 高置信天气博主
```

每位正式作者通常包含：

```text
@handle\
  00 账号档案.md
  01 内容与关系证据.md
  02 策略逆向工程.md
```

人工复核目录：

```text
D:\ALLPROJECTS\x learner\obsidian vault\x scraper\weather forecast\阶段 0 - 天气博主筛选结果\02 待人工复核
```

跨主题关注作者目录：

```text
D:\ALLPROJECTS\x learner\obsidian vault\x scraper\weather forecast\阶段 0 - 天气博主筛选结果\03 跨主题关注作者
```

使用规则：

- `00 总览与记录/01 运行记录.md` 按研究任务追加轮次、单轮总价、证据分母、策略影响、缺口和停止原因。
- `00 总览与记录/05 策略导航.canvas` 只保存天气 Alpha、通用 Alpha、控制和待补证入口。
- `00 总览与记录/02 现有天气策略.md` 与 `00 总览与记录/03 通用策略大全.md` 是紧凑看板，显示 ID、优势/控制机制、成熟度、范围、作者和档案链接。
- `04 策略档案/` 保存 canonical dossier；同一机制只维护一份，天气参数化通过关联说明进入通用机制。
- `00 总览与记录/04 策略看板.base` 通过 `strategy-dossier` 标签收集 canonical dossier，提供天气/通用卡片以及控制、待补证、可测试和已否定筛选视图；不要使用依赖 Vault 根目录层级的硬编码文件夹过滤。
- 旧 24 个标题的迁移表已归档到项目 `research_runs/2026-08-02-obsidian-sequence-cleanup/historical-artifacts/`；重构前正文保存在 `research_runs/2026-08-02-obsidian-layout-cleanup/historical-artifacts/99 历史快照/`。
- `05 证据缺口.md` 记录缺少正文、Article 正文或媒体上下文的来源，补证前不得提升策略成熟度。
- `03 跨主题关注作者/` 保存用户指定的通用预测市场作者；每位作者使用与天气作者一致的三页档案结构。
- 作者证据页保存完整高价值短帖原文；长帖、线程和 Article 保存连续相关片段、位置、上下文和正式来源。
- 策略页保存原子主张、推断、假设、失败条件和改进。
- 六个根目录旧文件已归并并移出活动 Vault；原件保存在 `research_runs/2026-08-01-existing-authors-03/legacy-root-cleanup/originals`。旧人工复核目录仍可作为历史材料保留。

## 2. Weather Market Research Skill

Skill 主文件：

```text
C:\Users\14370\.codex\skills\weather-market-research\SKILL.md
```

研究协议：

```text
C:\Users\14370\.codex\skills\weather-market-research\references\research-protocol.md
```

预算、游标和去重脚本：

```text
C:\Users\14370\.codex\skills\weather-market-research\scripts\research_control.py
```

本地研究状态：

```text
D:\ALLPROJECTS\x learner\data\weather_market_research_state.json
```

Skill 工作流：

```text
关系网络滚雪球
→ 增量读取未检查帖子
→ 归档高价值内容
→ 逆向工程或改进策略
→ 汇报作者、证据、策略和 API 费用
```

调用原则：

- 每轮先读取本地作者库和游标。
- 每轮预算必须由用户明确给出；未给预算时不执行付费 X 调用。
- 历史累计额度和费用阶段只用于审计，当前轮预算决定本轮调用上限。
- X 调用保持串行。
- 每次调用前预算预检，返回后立即记账。
- 所有返回帖子 ID 都进入去重状态。
- 作者时间线包含原创、引用、回复和纯转帖；纯转帖计入作者配额，原作者主张与转帖者选择分开归因。
- 有策略价值的 X Article 或作者长文属于重点证据；取得正文后做完整逆向工程，标题或预览不足时保留缺口。
- 任何进入策略的内容必须先写入作者原文库，并标记影响的策略 ID；单独帖子 ID 或一句转述不足以支撑正式策略。
- 策略记录分为 Alpha、控制规则和待补证假设；Alpha 使用独立档案的机制、证据、案例、交易循环和证伪质量门槛。
- 先闭合未完成的新帖区间，再读取更新内容和更旧历史。
- 新策略不是每轮硬指标；策略改进、失败案例和条件收窄同样有效。
- X Developer Console 是最终费用依据，本地账本采用保守估算。

## 3. 旧项目代码入口

项目根目录：

```text
D:\ALLPROJECTS\x learner
```

天气系统代码：

```text
D:\ALLPROJECTS\x learner\src\x_learner\weather
```

主要模块：

```text
models.py
adapters.py
probability.py
strategy.py
risk.py
paper.py
store.py
evaluation.py
```

迁移前应检查：

- 模块是否仍与新架构一致。
- 测试是否能在新项目独立运行。
- 旧适配器是否混入暂不使用的平台。
- 数据路径是否硬编码到旧项目。
- Paper Broker 与未来 Nautilus 接口如何衔接。

## 4. 项目介绍材料

启动会 PPT：

```text
D:\ALLPROJECTS\x learner\天气预测市场交易系统_项目启动会.pptx
```

该 PPT 适合向新协作者介绍市场、系统架构、数据时间机器、合约映射、概率定价、风控、执行和建设路线。

## 5. 外部数据与技术资源

### Polymarket

- 官方文档：https://docs.polymarket.com/
- WebSocket：https://docs.polymarket.com/market-data/websocket/overview
- 订单：https://docs.polymarket.com/trading/orders/overview
- 费用：https://docs.polymarket.com/trading/fees

### 历史市场数据

- TimeSeventeen Polymarket-v1：https://huggingface.co/datasets/TimeSeventeen/Polymarket-v1
- Jon Becker prediction-market-analysis：https://github.com/Jon-Becker/prediction-market-analysis
- Jon Becker schemas：https://github.com/Jon-Becker/prediction-market-analysis/blob/main/docs/SCHEMAS.md

### 天气数据

- AviationWeather API：https://aviationweather.gov/data/api/
- NWS API：https://www.weather.gov/documentation/services-web-api
- ECMWF Open Data：https://www.ecmwf.int/en/forecasts/datasets/open-data

### 运行引擎

- NautilusTrader：https://nautilustrader.io/docs/latest/
- Polymarket integration：https://nautilustrader.io/docs/latest/integrations/polymarket/

## 6. 权威性规则

发生信息冲突时：

1. 平台和数据源的正式规则优先于社交媒体描述。
2. Obsidian 原帖证据优先于后续摘要。
3. `01 高置信天气博主/00 作者索引.md`、`02 待人工复核/00 待复核索引.md` 与 `03 跨主题关注作者/00 作者索引.md` 决定作者当前分类入口；`00 总览与记录/02 现有天气策略.md` 和 `00 总览与记录/03 通用策略大全.md` 决定 Markdown 看板入口；`00 总览与记录/04 策略看板.base` 决定筛选入口；`04 策略档案/` 的独立档案决定策略正文；`00 总览与记录/05 策略导航.canvas` 只负责导航。
4. `weather_market_research_state.json` 决定帖子去重和抓取边界。
5. 本项目 `docs/DECISIONS.md` 决定当前范围和架构决策。

## 7. 2026-10-10 工程参考与天气市场文献

由 PROJECT_REBASELINE 原样移入（2026-10-10 审核整理）。均为研究线索，未复现实验。

### Lauren Tan / pstack 参考

已定位 [Matt Pocock 原采访](https://www.youtube.com/watch?v=MN9dGgmLyso) 及 Lauren Tan 的 [pstack 官方源码](https://github.com/cursor/plugins/tree/main/pstack)。原视频全文未成功取得；“每月两千/两千五百PR”的二手转述未作为绩效事实或本项目目标。本轮以下建议主要根据公开实现，而非冒充看完采访。

[项目验证生成器](https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md) 强调真实用户路径、可观测副作用及可保留证据；[编排说明](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/orchestrate.md) 区分验证等级、绑定当前PR版本，并让协调成本与任务规模匹配。对本项目的适用做法是：固定功能通过条件、让agent能操作真实应用、确定性工具处理重复任务、按当前版本保存证据，再有限并行。PR数量无法说明功能是否可用，不照搬大规模队列与团队编制，也不安装或修改个人全局插件。

### 历史数据路线

用户明确要求尽量不自行采集，先利用历史天气和已有数据。先清点 `research_runs/2026-09-14-knyc-migration/`、KNYC模型训练输入、本地既有feed/结果、天气R2与RESEARCH_INDEX；本轮未逐份验证这些数据仍完整可读。

1. 天气模式先交付。只有最终日最高温时研究终值分布；盘中触发需要历史时序观测、对应预报版本。最终修订只作标签，不能虚构历史接收时间。
2. 需要价格时优先官方历史。[Kalshi历史数据文档](https://docs.kalshi.com/getting_started/historical_data) 确认市场、K线、成交及账户历史分实时/归档层，按各自cutoff分页合并去重。
3. [Poly US 官方索引](https://docs.polymarket.us/llms.txt) 列出 Get Price History（由订单簿生成的YES/NO显示价）、Time & Sales、日终报告及机构报表。已确认入口存在，尚未实际下载KNYC样本、核实覆盖和权限；不能承诺完整历史L2。US数据与International链上数据分开。
4. Nautilus提供数据模型、回放和撮合能力；它不能生成缺失历史。按[官方数据文档](https://nautilustrader.io/docs/latest/concepts/data/)转换外部记录，按项目1.231.0版本验证；稀疏历史只支持相应精度的模拟。
5. 只有外部历史与已有资产无法满足某个必要假设时，才提交新增采集范围、留存和成本方案，不将此设为第一阶段前提。

### 天气市场文献调研

本轮由独立agent检索，未调用Academic Research Suite或付费X。以下是研究线索与方法边界，未复现论文实验。

| 来源 | 发现及限制 | 项目用途 |
|---|---|---|
| [Crosier 2026, Prediction Markets Beat the Weather Forecast on Tomorrow’s High Temperature](https://arxiv.org/html/2609.23969v1) | 预印本；7城市7590城市日，报告市场隐含温度均值相对NBM的RMSE改善约10%；多预报组合缩小差距；未证明费后交易收益 | 市场隐含概率作为同期基线，按城市/季节评价；检验天气更新是否真的带来尚未被价格吸收的信息 |
| [PredictionMarketBench](https://arxiv.org/html/2602.00133v1) | 预印本；事件回放包含订单簿、成交及结算；仅4个episode，其中1个纽约温度事件，延迟及交易所规则存在简化 | 借鉴确定性事件样本，验证订单/部分成交/费用/结算；不能以此样本宣布策略有效 |
| [Decomposing Crowd Wisdom](https://arxiv.org/abs/2602.19520) | 大规模预测市场校准研究，结果依领域与距结算时间变化；主要稳健结果不能直接外推天气 | 校准按时段、概率区间和站点切分，避免用平台整体命中率替代本策略结果 |
| [ECMWF概率校准综述](https://www.ecmwf.int/sites/default/files/elibrary/2014/9607-calibration-medium-range-weather-forecasts.pdf) | 支持偏差/离散度校准与概率评分的成熟方法线；本轮仅核查检索信息，未完成全文审阅 | 先用简单滚动样本外基线，对比当前模型，复杂模型须有独立增益 |
| [Nautilus 回测文档](https://nautilustrader.io/docs/latest/concepts/backtesting/) | 回放、执行与账户的工程参考，latest接口未必适配固定版本 | 明确fill/fee/latency假设及引擎职责，优先复用已有Session |

规则变化也会影响标签：Kalshi [Miami 2026-08-30官方市场页](https://kalshi.com/markets/kxhighmia/highest-temperature-in-miami-on-aug-30-2026/kxhighmia-26aug30) 公告温度结算源转向The Weather Company。该例不能代替KNYC逐合约核验，却说明不能永久把CLI当作所有平台的结算真相。站点相同仍须分别确认来源、窗口与舍入。

研究交付优先为：S1/S2/S3规格与触发日志、天气概率校准、外部历史覆盖清单、明确假设的交易模拟。收益研究通过与软件功能通过分开；本轮没有建立可实现正收益结论。
