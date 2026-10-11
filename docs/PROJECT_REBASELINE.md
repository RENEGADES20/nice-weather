# 项目核查与改进计划

2026-10-10；基线 `37209f7`。本轮交付为核查、文档与计划，未改变交易代码、数据库或部署。

## 核查结论

项目已经具备完整终端的大量组成部分。主要问题是版本、规则、证据与交付单元没有形成一致的管理方式。下面区分可观察事实和改进判断。

| 发现 | 证据 | 影响及处理 |
|---|---|---|
| 本地入口落后 | 原目录 main=`60dad0a`，比 origin/main 少126提交；KNYC 验收文件在旧 checkout 缺失 | 开始任务先确认根目录、分支和发布 SHA；本轮使用从 #101 建立的隔离 worktree，保留原目录修改 |
| 当前文档混入过程日志 | 原 CURRENT_STATE 805 行；#101 主分支写待发布，#102 分支已有完成记录 | 当前事实按能力表维护；历史原文独立保留，状态只在一个入口更新 |
| 验收口径冲突 | TERMINAL 文档已取消固定次数/p95，STRATEGY 文档仍要求30/100/500样本 | 统一到功能行为清单，未变化证据不重复验收 |
| 模拟、回测与实盘证据混杂 | 任务6叙述 Paper 买卖完成；9月27日新市价闭环仍待验；Live JSON write_attempts=0 | 分版本、平台与场景记录；只读认证不能代表实盘订单通过 |
| 策略已有逻辑但规格不集中 | signals.py/v2 和多份交付文档描述不同阶段 | 冻结一份策略规格、列出触发/执行/退出/重评；研究改变走版本化 |
| 回测有引擎但入口窄 | us_runtime.replay 只读 feed.sqlite3，回收区间明确拒绝；engine 实际调用 Nautilus | 保留引擎，接入已有天气及外部历史数据；先验证研究与交易模拟的不同输出 |
| 成交精度有明确限制 | paper_execution 的 market-price-v1 支持显示价回退，费用/滑点为假设 | 用显著模式标签；增加与数据精度匹配的撮合，不将近似成交解释为真实容量 |
| 测试边界不足 | terminal.spec/backtest.spec 多处 page.route.fulfill；CI已有7类检查 | 保留局部测试，补真实服务/worker/数据库/Nautilus 的浏览器整链证据 |
| PR 颗粒度过细 | 最近55个PR的创建日统计中，2026-09-22有22个；#90–97围绕概率/历史/Paper连续修补 | 每个用户功能或紧密相关模块一个PR，普通修复留在该工作包；生产事故才独立hotfix |
| 多城市尚未实现 | us_markets、knyc_model、us_runtime仍有KNYC及纽约时区常量 | 第一阶段完成纽约；第二城检验真实差异，按需提取参数 |

不能仅凭代码体积认定所有复杂性无用。前端加载慢的历史根因包括全历史DOM、冷查询、Paper序列化和WAL竞争；这些已有局部修复。当前页面需恢复登录后测量，不能预先归因于一个组件或框架。

## 交付工作包

下列为后续实施计划，当前未启动代码实现。顺序按依赖排列，前端性能定位可独立提前。

| 工作包 | 内容及范围 | 完成边界 | PR边界 |
|---|---|---|---|
| P0 文档与基线 | 统一范围、状态、策略、验收；保留历史；补证据缺链 | 所有当前文档一致；每个状态有证据或明确缺口 | 本轮一个文档PR |
| P1 策略与天气研究 | 核对v2代码；清点KNYC既有天气/预报/标签；时间切分；逐日触发解释 | A4的机械场景及A6；S1/S2/S3各有触发/失败案例与版本 | 一个完整功能PR，含必要界面入口 |
| P2 历史交易回测 | 官方历史样本、时间/规则映射、已有Session导入、费用与成交模型、结果解释 | A7/A8离线；非零成交案例可重复，覆盖及近似范围明确 | 数据导入与回测闭环同一PR；仅确有独立可验收边界时再拆 |
| P3 终端与Paper | 当前登录/版本复核；精简首屏及按需历史；票据、账户、策略与恢复整链 | 双平台A1–A5、A9、A11 | 一个用户工作流PR，附真实服务验证 |
| P4 Live | 复核平台原生适配/对账、风险配置、未知订单恢复；按后补额度执行受限验收 | A10，认证与真实订单分列，所需人工提交明确 | 一项Live交付PR，金额参数不齐时独立功能继续 |
| P5 第二城 | 选城、市场规则与天气模型映射，共用已有引擎与终端 | A12，纽约回归受影响范围通过 | 第二城完整接入PR |

单个PR可以含多个逻辑清楚的提交。一次功能修复应涵盖调用方、后端、界面、测试和文档；避免按“某个按钮/某条SQL/某张截图”逐个发布，也避免把无关功能塞入无法评审的巨型PR。

## 验证与 agent 协作

采用一个集成负责人维护功能目标、接口和证据。仅将独立工作包交给 agent；需要并行时明确文件/接口边界及交付证明，共享VM由单一负责人操作。实现者不把自己的完成声明当作独立审查。资金、订单、迁移等高影响改动独立复核；简单文档或UI修订不强制多轮agent审查。

将现有可复用的启动、环境诊断、浏览器驱动、回执/账目核验和证据保存整理为项目内验证入口。优先复用现有测试框架；先证明一个真实闭环，再扩展用例。无需新增长期协调服务、消息总线或多层编排平台。

通过条件和失败归属见 KNYC_TERMINAL_DELIVERY。若同一失败在没有新变化时再现，停止无效重试，转根因分析；权限/样本阻塞记录解除条件。普通测试、修复、PR和合并依既有授权推进，本轮只读核验 main ruleset：要求PR、解决审查讨论、lint/unit/fixture-dashboard三项必需检查；required_approving_review_count=0，另有未归属变更的额外审批规则。传统branch protection接口404并不代表没有ruleset。当前配置没有普遍强制人工approving review；频繁确认需进一步区分工具审批和agent工作流。不能绕过有效规则，也不承诺工具会自动批准所有操作。

## Lauren Tan / pstack 参考

已定位 [Matt Pocock 原采访](https://www.youtube.com/watch?v=MN9dGgmLyso) 及 Lauren Tan 的 [pstack 官方源码](https://github.com/cursor/plugins/tree/main/pstack)。原视频全文未成功取得；“每月两千/两千五百PR”的二手转述未作为绩效事实或本项目目标。本轮以下建议主要根据公开实现，而非冒充看完采访。

[项目验证生成器](https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md) 强调真实用户路径、可观测副作用及可保留证据；[编排说明](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/orchestrate.md) 区分验证等级、绑定当前PR版本，并让协调成本与任务规模匹配。对本项目的适用做法是：固定功能通过条件、让agent能操作真实应用、确定性工具处理重复任务、按当前版本保存证据，再有限并行。PR数量无法说明功能是否可用，不照搬大规模队列与团队编制，也不安装或修改个人全局插件。

## 历史数据路线

用户明确要求尽量不自行采集，先利用历史天气和已有数据。先清点 `research_runs/2026-09-14-knyc-migration/`、KNYC模型训练输入、本地既有feed/结果、天气R2与RESEARCH_INDEX；本轮未逐份验证这些数据仍完整可读。

1. 天气模式先交付。只有最终日最高温时研究终值分布；盘中触发需要历史时序观测、对应预报版本。最终修订只作标签，不能虚构历史接收时间。
2. 需要价格时优先官方历史。[Kalshi历史数据文档](https://docs.kalshi.com/getting_started/historical_data) 确认市场、K线、成交及账户历史分实时/归档层，按各自cutoff分页合并去重。
3. [Poly US 官方索引](https://docs.polymarket.us/llms.txt) 列出 Get Price History（由订单簿生成的YES/NO显示价）、Time & Sales、日终报告及机构报表。已确认入口存在，尚未实际下载KNYC样本、核实覆盖和权限；不能承诺完整历史L2。US数据与International链上数据分开。
4. Nautilus提供数据模型、回放和撮合能力；它不能生成缺失历史。按[官方数据文档](https://nautilustrader.io/docs/latest/concepts/data/)转换外部记录，按项目1.231.0版本验证；稀疏历史只支持相应精度的模拟。
5. 只有外部历史与已有资产无法满足某个必要假设时，才提交新增采集范围、留存和成本方案，不将此设为第一阶段前提。

## 天气市场文献调研

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

## 本轮限制

正式页面需恢复Cloudflare登录后才能复核；未重新连接VM、未发送真实订单、未重跑代码测试、未下载平台历史样本或复现文献。根目录未提交修改保留；不删除worktree、历史数据或文件。文档检查仅证明结构、引用和历史保存，不代表新增功能完成。
