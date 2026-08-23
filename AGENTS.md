# Project instructions

开始任务前，先完整阅读：

1. `docs/PROJECT_CONTEXT.md`
2. `docs/CURRENT_STATE.md`
3. `docs/DECISIONS.md`

涉及架构或实现时，再阅读 `docs/SYSTEM_ARCHITECTURE.md`。涉及天气作者、策略证据、X API 或 Obsidian 时，再阅读 `docs/RESEARCH_INDEX.md`。

工作原则：

- 第一版只覆盖 Polymarket 机场站每日最高温市场。
- 当前只做研究、历史回测和 Paper Trading。
- 禁止配置实盘密钥、实盘下单权限或自动资金操作。
- 策略、作者观点和收益案例必须标记为研究假设或未独立核验。
- 历史决策只能使用当时已经收到的数据，禁止未来数据泄漏。
- 合约站点、日期、温标、舍入、观测窗口或结算源有歧义时，输出 `no-trade`。
- X 研究使用 `weather-market-research` Skill；调用前必须有明确预算，并遵守去重和费用记录流程。
- 优先读取本地 Obsidian 与研究状态，避免重复付费读取 X 资源。
- Nautilus Trader 是暂定运行引擎，领域逻辑保持独立接口。
- 保留失败案例、反例、异常和停止交易原因。
- 修改架构、项目范围或数据口径后，同步更新 `docs/CURRENT_STATE.md` 与 `docs/DECISIONS.md`。
- 不批量删除文件；涉及删除时先列出精确目标并请求人工确认。

