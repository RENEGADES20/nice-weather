# 当前状态

最后更新：2026-08-27

## 当前阶段

项目处于“纽约 KLGA MVP 纵向闭环已通过 PR #1 验收并合并至受保护 main，待人工批准 PAPER CANARY”的阶段。

天气数据基础设施已在 `codex/weather-data-recorder-r2` 分支加入独立采集器、schema v3、Cloudflare R2 追加归档和 Ubuntu systemd 部署方案；合并与 VM 人工部署仍待完成。

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
- CLOB 使用 REST 快照，尚未接 WebSocket 增量簿或 maker 排队模型。
- 自动结算等待 Gamma winning outcome；跨机场日未完成结算的旧持仓仍需人工对账。
- 外部 API 失败已有 `system_events`、循环续跑和 heartbeat，尚未经过长时间断流与重连演练。
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
