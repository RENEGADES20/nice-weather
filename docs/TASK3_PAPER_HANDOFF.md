# 任务 3：模拟执行交付

## 范围与执行口径

分支 `codex/task3-paper`，起点 `7303091`。未修改任务 5 工作树、实盘文件、策略公式、终端主入口、全局样式及部署配置；未操作 VM。主页面接线、合并和发布交任务 6。

- 新 KNYC Paper/回测使用 `execution_model=market-price-v1`，保留 Nautilus execution_version=3。已有 Paper 从原生检查点升级，现金、持仓、历史成交和策略机会保留；旧成交的新增 execution 证据为 null，不补造。重启取消未完成挂单并释放预留。
- 买入 Ask、卖出 Bid → 中间价 → 最近成交 → 最近有效报价；限制在同平台、市场日、合约和截至执行时刻已知的信息。保留来源时间、真实接收时间、报价年龄，恢复不刷新报价时间。无价格拒绝，禁止概率冒充价格。
- 模拟假设请求数量可按选定市场价格成交，不模拟容量、排队或部分成交。合成容量只进入 Nautilus 模拟器，不写真实行情。GTC 等待后按当时市场报价成交，IOC/FOK 条件不满足时取消；费用和滑点后的价格遵守限价。
- 未知费用默认成交额 1%，向上取整至美分；已知费用复用平台规则。滑点默认 0 个百分点，买入加、卖出减，然后向不利方向按 tick 取整。修改参数前取消现有挂单，保证已接受订单的假设不变。
- 人工单取消固定 5/20 美元上限，保留资金含费、挂单预留、禁止超卖、合法数量价格及无歧义合约要求。策略额度不变，同平台人工/策略共用账户。停止策略不停止人工交易。
- 持仓按卖出方向市场价估值，不预扣未来退出费用/滑点。缺价格显示未知；只接受既有官方最终结算证据。权益=现金+持仓估值，PnL 扣除初始资金及资金调整。

## 任务 2 接口

复用 `Session.order(request_id, leg, owner)`；owner 为 S1/S2/S3。也可使用 `Session.apply` 输入：

```json
{"kind":"candidate_order","ts":1789844400000000000,"request_id":"stable-leg-id","data":{"token":"actual-token","side":"BUY","quantity":1,"price":0.5,"tif":"IOC","strategy":"S3","signal_id":"stable-signal-id"}}
```

候选需要策略已启动，人工 order 不需要；浏览器 API 不开放 candidate_order。S1 每腿分别提交并检查原生状态，不假设组合原子成交。策略判断、机会消耗及残腿处理沿用任务 2。

历史和在线调用同一执行函数。来源时间研究可通过 `feed_event` 的 market_price 事件传入 `received=null,time_basis=source_time,source_time=<秒>`，仅 Backtest 接受；证据 received_at 保持 null。已有真实收到时间的回放继续使用原始接收顺序。

## 任务 4、6 接口

| 入口 | 输入与结果 |
| --- | --- |
| POST /api/paper/preview | 沿用 Command，mode=sandbox、kind=order；payload 为 token/side/quantity/price/tif。返回 available、reason、selected_price、estimated_fee、fee_estimated、expected_status、simulation。实际提交重新校验。 |
| POST /api/commands | 原有指令不变，新增 simulation_settings，payload 为 estimated_fee_rate（0–1）和 slippage_pp（0–100）。重复 ID 幂等，不同参数冲突。 |
| 回测请求 | 新增可选 cash、simulation，默认 100、1% 估算费用、0 滑点。新运行默认 ID 包含执行版本及参数；旧运行不重算。 |
| GET /api/paper/equity | run_id、after（纳秒游标）；返回 points、next，每页最多 1000 点，按 next 翻页。 |
| 账户 snapshot | 原字段保留，新增 execution_model、approximate、simulation、market_prices、settlement_status；fills.execution 含来源、时间、signal_id/owner，positions.valuation 含估值依据。 |

权益点包含 ts（纳秒）、cash、reserved、available、market_value、equity、fees、realized_pnl、unrealized_pnl、total_pnl、settlement_status。按分钟、成交、结算和估值有效性变化采样，回测另存终点；未保存的旧历史不回填。

独立组件 `frontend/terminal/src/PaperTicket.tsx` 接收 market、accountRevision、simulation、preview、send、onAccountUpdate 和 blocked。主入口应复用现有认证/CSRF和持久化发送器；将发送器的未知请求/恢复阻塞状态传入 blocked。send 对明确成功或拒绝回执 resolve 可读结果，结果未知才抛错。组件遇到未知结果禁用新提交，只允许使用原 request_id 查询/重试；页面重载由现有持久化发送器恢复。组件防止重复点击、切市场后清空限价、忽略旧预检响应；每两秒更新预检。本 PR 不修改 main.tsx，测试页不作为生产入口。

## 验证事实（2026-09-22）

- 较早一轮全量交易测试：190 通过，2 项既有 Redis 实盘恢复测试因 Windows 本机缺 redis-server 失败；没有安装新服务，CI 已配置 Redis。
- 最终相关回归 30 项通过：近似执行、真实 API/队列/权益、旧账户升级、结算、来源时间研究、原生恢复、KNYC 回放及检查点。Ruff、TypeScript 和本地生产构建通过，构建输出仅在 tmp。
- Playwright 新组件回归通过：延迟旧平台预检不能覆盖新平台，缺价格时明确拒绝；发送失败时禁用新单，重试使用相同 request_id。样例测试单独标注。
- Chrome 亲自操作真实本地 API + 两个 Paper worker + Nautilus，开发样例行情明确标注。Kalshi 买入 30、卖出 10、卖出 20：现金 100→87.88→90.85→96.79，费用 0.21，最终无持仓、3 次成交；刷新无重复。Poly US 切换后初始现金仍为 100，买入/卖出各 30 后现金 96.79、费用 0.21、无持仓、2 次成交。未 mock 成交 HTTP 响应。
- 本地真实 2026-09-20 行情：Kalshi 9 条、Poly US 11 条，Ask 分别 0.88/0.89，原 seq/body/接收时间保持。两边原合约记录规则歧义，候选明确拒绝、零成交；切片缺预测事件，回放 no-data。见 `acceptance/task3-real-prices-2026-09-22.json`，包含原文哈希。未修改历史规则来制造成交。

## 复现与剩余项

在已有 Python 3.12 + Nautilus 1.231.0 环境执行：

```text
python -m pytest tests/trading/test_paper_approximation.py tests/trading/test_knyc_terminal.py tests/trading/test_us_settlement.py tests/trading/test_checkpoint_writes.py -q
python tests/trading/paper_browser_app.py --root tmp/task3-browser
# 另一个终端在 frontend/terminal 执行 npm run dev
# 浏览器打开 http://127.0.0.1:5175/tests/paper-ticket.html
python scripts/task3_paper_evidence.py --source <original-feed.sqlite3> --output tmp/task3-real-evidence
```

helper 仅监听本机，使用标注的开发样例及测试密码。证据脚本只读原库，仅摘录约 20 条事件到本地临时库，不复制整库；测试 helper、样例页及临时数据不进入生产发布包。

剩余：任务 6 接入口和发布，任务 2 提供真实合格策略候选，真实历史规则歧义待相应任务解决。完整市场日和自然最终结算未验收，不能由样例成交替代。
