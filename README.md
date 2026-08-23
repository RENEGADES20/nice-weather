# Nice Weather

Polymarket 纽约 KLGA 每日最高温市场研究、Live Shadow、Paper Trading 与交易员只读看板。

当前第一版聚焦机场气象站每日最高温市场，目标是建立一条可重放、可审计的完整链路：

```text
研究证据
→ 数据采集
→ 合约映射
→ 概率定价
→ 策略候选
→ 风险审批
→ 订单执行
→ 历史回测与实时纸盘
→ 监控、对账和复盘
```

## 开始阅读

新对话或新协作者按以下顺序阅读：

1. [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md)
2. [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md)
3. [`docs/DECISIONS.md`](docs/DECISIONS.md)
4. 具体开发任务再阅读 [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md)
5. 需要研究证据、Obsidian 或 Skill 时阅读 [`docs/RESEARCH_INDEX.md`](docs/RESEARCH_INDEX.md)

新开 Codex 对话时，可以直接复制 [`docs/NEW_CHAT_PROMPT.md`](docs/NEW_CHAT_PROMPT.md) 中的启动词。

## 当前边界

- 交易平台：Polymarket。
- 市场类型：机场站每日最高温。
- 运行模式：研究、回测和 Paper Trading。
- 当前没有实盘权限。
- Kalshi 暂不进入第一版。
- 所有策略均为研究假设或待验证规则。

系统不包含真实下单客户端、私钥配置或自动资金操作。

## 开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m nice_weather.cli config-check
.\.venv\Scripts\python -m nice_weather.cli db-init --db var\dev.sqlite3
.\.venv\Scripts\python -m pytest tests\unit -q
```

完整 fixture、Live Shadow、持续 Runner 和 Dashboard 命令将在纵向闭环实现后补充。

## 原项目与研究库

现有研究、代码骨架和 Obsidian 资料仍位于：

```text
D:\ALLPROJECTS\x learner
```

新项目不能假定旧代码已经迁入。迁移或重构时应先阅读 `docs/RESEARCH_INDEX.md` 中的入口。
