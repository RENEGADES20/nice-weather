# 研究、Obsidian、Skill 与旧项目入口

最后更新：2026-08-02

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
