# 任务 4：交互式回测组件交付

## 修改范围

- `frontend/terminal/src/backtest/`：独立 BacktestWorkspace、账户/赔率图、可复用策略标记、类型及局部样式。
- `trading/backtest_view.py`：运行列表/详情、队列状态、原生账户观察及信号事件的展示投影。
- `trading/api.py`、`trading/us_runtime.py`：仅回测路由挂载、市场日参数解析、资金/模拟参数传递和展示观察钩子。
- 本地 preview HTML、浏览器测试配置和测试不进入默认生产构建。不改主入口、全局样式、策略公式、模拟成交规则、实盘或部署配置。未访问或上传 VM，未删除文件。

## 任务 6 接入

在现有回测页签挂载 `<BacktestWorkspace csrf={csrf} initialVenue={venue} />`，替换旧结果区；组件自行读取 `/api`，也可传 `apiBase`。主入口保持由任务 6 修改。

实时图复用 `attachStrategyMarkers(chart, series, {venue, day, token}, events, onLocate)`，卸载时调用返回的清理函数。事件有稳定 ID；同时间多事件保留，S1 双腿按 token 分开，group_id 保留组合联系。图表时间为 UTC epoch，显示使用 America/New_York。

### 后端接口和依赖

- `GET /api/backtests`：历史运行与已排队请求，按更新时间排序。
- `GET /api/backtests/{run_id}`：config、status/error、snapshot、curve、events、history_complete、settlement。旧运行仅有最终账户值时不补造曲线。
- `POST /api/commands` 沿用原命令结构；payload 为 `start_day/end_day/strategy/cash/simulation`。simulation 沿用任务 3 的 `estimated_fee_rate`（0–1）与 `slippage_pp`（百分点）；已知费用继续使用平台规则。
- 任务 1：调用 `market_weather.market_day` 将首尾市场日解析为合约观测窗口；前端读取 `/api/market-day?venue&day` 和 `/api/history?venue&day&token&before`，沿 next_before 读取分页。报价完整性/缺口标志若上游提供则保留；不再按 30 秒切断历史。当前任务 1 草稿仅暴露顶层买卖价，历史连通性还需结合覆盖说明理解，不能从线段推定完整连续采集。
- 任务 2：接受原有 replay-decisions 及新增 signal.events。展开 legs/targets，weather_warning 使用 warning.upper_bin；阶段转换为 warning/trigger/candidate/rejection/diagnostic。native fill 才生成成交标记。存在 execution.order_id 时关联组合及概率；旧记录缺少关联时显示“未记录”。
- 任务 3：保留 replay 的 cash/simulation 参数和 execution_model；优先读取 simulation_equity，有该运行的账户观察时不重复写账户曲线。ReplayView 仍负责原生订单状态与成交事件。合并 us_runtime.py 时保留任务 3 的执行实现，接入本任务的观察钩子即可。
- 日期参数和模拟参数依赖任务 1、3 的模块；依赖未合入时明确报错，不能静默忽略参数。独立分支保留旧 start/end 回测兼容。

## 验证结果

- Python：`pytest tests/trading/test_backtest_view.py tests/trading/test_knyc_terminal.py -q`，18 通过。覆盖同时间同内容的两笔原生成交、旧运行不造历史、优先共享权益、队列展示、双腿/预警映射、日期窗口及参数传递、既有回放/认证回归。
- 浏览器：`node node_modules/@playwright/test/cli.js test --config tests/backtest.config.ts`，4 通过。覆盖曲线切换、S1 双腿定位、历史运行替换、旧运行响应和旧 bin 报价竞态、空/失败状态及提交全部参数。用例数据明确为开发样例。
- TypeScript、Ruff 通过。独立 preview 构建输出仅在本地 tmp，约 416 KB JS / 134 KB gzip；未新增依赖。无固定性能采样或 p95 门槛。
- 浏览器测试发现并修复运行切换时图表重复释放的问题。缺失账户采样分钟以图表缺口表达；无有效双边报价不生成中间价。

## 真实数据与实际网页验收

真实来源为本机已有 KNYC 公共 feed.sqlite3。只读抽取原始合约记录和 9 月 19、20 日各三分钟的采集窗口，共 1,941 行，保留 seq、received 和 body；本地样本库约 8.1 MiB。未把片段描述为完整市场日，未补造预测或成交。模块及样本来源见 task4-backtest-real-20260922.json。

使用任务 1、2、3 当时工作树模块进行本地组合联调，不修改其文件；通过正式命令 API 和后台 worker 运行：

| 平台 / 策略 | 市场日 | 初始资金 | 结果 |
| --- | --- | ---: | --- |
| Kalshi / S1 | 2026-09-19 | $100 | no-data，零成交，权益 $100 |
| Poly US / S2 | 2026-09-20 | $250 | no-data，零成交，权益 $250 |
| Kalshi / 三策略 | 2026-09-19–20 | $150 | no-data，零成交，权益 $150 |
| Poly US / S3（网页提交） | 2026-09-19 | $175 | no-data，零成交，权益 $175 |

前三次估算费率 2%、滑点 0.5 pp，网页提交采用 1%、0 pp；实际运行 config 与设置一致，曲线末值与账户一致。数据缺少 prediction 事件，页面明确提示缺少策略概率输入、无法验证触发。

在本地浏览器亲自操作登录、双平台历史运行重载、PnL 切换、bin 切换、双日结果中的日期切换、真实赔率图缩放/平移/十字线、网页 S3 提交及刷新。刷新后仍为同一运行，历史选择器共四次运行。Poly US 75–76 档缺双边报价，空状态正确；73–74 档的真实中间价正常显示。

真实历史成交、最终结算和 S1 成交双腿在本次样本中不存在，其标记交互通过开发样例和原生事实测试验证；不声称真实成交或结算验收通过。任务 6 的正式入口接线、上游最终合并后的集成复验、合并及部署尚待完成。
