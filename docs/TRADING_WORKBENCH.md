# Nautilus 工作台运行与验收

## 版本 3 的实时运行口径

本节覆盖下文旧 L1 模拟的盘口/恢复描述；Backtest 和已有版本的历史重放继续沿用原规则。当前 Trading 是唯一交易入口，Paper 可操作，Live 只展示公开市场且全部操作关闭。旧独立 Execution/Paper 在 System & Audit 中只读保留。

Paper 盘口驻留 Worker 内存，WebSocket 完整快照初始化和增量更新，回环接口 `127.0.0.1:8766/depth` 提供展示。订阅按所选 YES/NO、持仓、挂单、策略需求增减。静态盘口每 25 秒重接核验；断连/不完整/30 秒过期暂停成交。Nautilus L2_MBP 跨档撮合，保留已消耗份额；公开档位缩量或相同快照不能补回已用深度。市价票据发出有保护价的 IOC，默认 1¢ 滑点。买入金额含保守费用上限，卖出以份数计。

`paper_state` 只保留每个账户的一个原生事实检查点，`paper_receipts` 保留稳定请求 ID；它们和结果投影在同一事务提交。重启加载账户/成交/持仓事件，取消旧挂单并记录原因，再接新簿。旧原生会话先按原 L1 规则验证重放，再转换，不使用 L2 重放旧成交。独立旧 Paper 和 Backtest 不迁入资金。结算证据保留，盘口不进入检查点、输入日志或文件，因此无法精确复原历史流动性。

`paper_equity` 常规每分钟最多一条，成交/结算/有效性变化额外记录，唯一键去重。曲线和日历共用权益减初始资金的净 PnL（Bid 浮盈亏及费用已计入），纽约归日；首日从实际初始模拟资金起算，缺失估值显式断开，跨日缺口不归到恢复日。外部充提未开放。日历可查看成交、费用、已实现/未实现变化。

发布以提交内的明确文件清单打包，仅复制运行源码、配置和当前 index 引用的静态资源；不上传测试数据、截图、node_modules 或缓存。只备份受影响 results.sqlite3 一次并核验完整性。新交易服务共享 journald 命名空间，总容量上限 100 MiB。回滚保留新账目；发生不兼容时暂停 Worker，不能恢复旧备份覆盖新成交。部署后进行至少 15 分钟观察，测试成交只在本地隔离账户产生。

范围：KLGA 每日最高温 YES/NO，历史回测与实时模拟。真实执行默认关闭，CLI 的 LIVE 入口拒绝运行，不加载钱包环境变量。阶段 A 策略迁移和 Repricing 延迟优化后置。

## 环境与启动

使用 Python **3.12**、`nautilus_trader[polymarket]==1.231.0`；独立环境安装，不与 `[collector]` 或 `[dev]` 的 PyArrow <22 合并。

```bash
uv venv --python 3.12 /opt/nice-weather/.venv-trading
uv pip install --python /opt/nice-weather/.venv-trading/bin/python -e '.[trading]' pytest
nice-weather trading run --mode sandbox --db var/weather.sqlite3 --root var/trading
nice-weather backtest worker --root var/trading
nice-weather trading status --root var/trading
```

Dashboard 使用原环境；设置 `NICE_WEATHER_TRADING_ROOT`。Ubuntu 安装两个新 service 及 dashboard 的 `trading.conf` drop-in，先创建 `/var/lib/nice-weather-trading/requests` 并赋予服务用户访问权限，再启动服务。Dashboard 的写权限只开放到 requests 子目录；结果与天气库继续只读。两个 worker 使用独立 OS 文件锁。不要加载原含资金凭据的 env 到模拟或回测服务。

Windows 原生扩展若被应用程序控制阻止，使用已安装的 Ubuntu WSL 环境；不关闭系统防护。原生测试已在 WSL 和部署 Ubuntu VM 的 Python 3.12 环境执行。2026-09-13 部署版本及证据见 [验收报告](acceptance/nautilus-2026-09-12/REPORT.md)。

worker 运行期间保留一个无事务的结果库连接，使 WAL/SHM 在写入间隔仍可被只读 Dashboard 打开。不要把结果目录改成 Dashboard 可写来规避 WAL 生命周期问题。初始化及日志重放期间应等待 fresh snapshot；超过 10 秒的快照不允许提交交易操作。

原生故障验收还需要 `redis-server`：`sudo apt-get install -y redis-server`，随后运行
`python -m pytest tests/trading -q`。测试仅启动随机回环端口的独立 Redis/AOF 目录，
官方客户端使用固定测试凭据及受控 HTTP/WebSocket 输入，不向真实市场发单。

## 数据与策略

```bash
nice-weather backtest export --db var/weather.sqlite3 --root var/trading \
  --start 2026-09-12T00:00:00-04:00 --end 2026-09-13T00:00:00-04:00
nice-weather backtest run --root var/trading --request request.json
```

请求 JSON 示例：

```json
{"request_id":"acceptance-run-001","dataset_id":"<64-character-content-hash>","strategy_id":"acceptance_roundtrip","parameters":{"quantity":5,"exit_after_quotes":3,"require_both":true},"tokens":["<actual-token-id>"],"cash":100}
```

`noop` 不产生订单；`acceptance_roundtrip` 只用于验收，在首个有效报价买入，在指定报价序号退出，不代表交易优势。示例数量必须满足该市场最小份额以及同档 5、单机场日 20 的限额。

导出使用 SQLite backup 的一致性视图，JSON 内容哈希固定。合约与费用从原始 capture 重建；`contract_bins` 会覆盖历史，不能用作历史规则版本的事实来源。按 `received_at` 和来源序号推进，保留 exchange timestamp。缺失 NO 历史时拒绝双侧需求。NULL event_kind、Gamma 和插值不进入成交回放。缺失费用口径、歧义规则、陈旧报价、无有效双侧深度等均拒绝订单。Market Stream 每 15 秒补充完整公开盘口，恢复顶层深度，不推导 NO 价格。

一致性副本写入临时磁盘，导出正常结束后自动清理，不在 RAM 保存整库。需要预留至少一个源数据库大小的临时空间；生产旧库首次导出包含全库复制及旧合约读取，可能耗时数分钟以上。导出应作为独立进程运行，不能放入模拟账户循环或浏览器请求内。

## 账目与恢复

Nautilus 管理账户、撮合、持仓和订单。项目只投影原生对象和执行事件，不建立第二套成交账本。L1 模型启用 liquidity consumption，无 maker 排队优势或默认 rebate。账户长仓按有效 Bid 估值；过期价格显示不可用。

`inputs` 在执行前提交；每个输入完成后保存原生账户投影哈希。重启重放同一 run/account，逐条核对快照。中断在日志提交与结果提交之间的输入会被重放，已完成请求保持幂等。任何不一致暂停，禁止自动补现金或创建替代会话。撤改单仅在收到原生撤单确认后按剩余目标量下单，token 与方向必须保持一致。

模拟统计仅覆盖接入后，不拼接旧 Paper 历史。实时盘口源时间与 worker 执行接收时间分别记录。未验证的最终结算保持未结算；固定测试中的 0/1 事件标明测试证据，不能当成自然市场结算。官方客户端配置包含原生 Redis Cache 和启动对账；真正的实盘启用、账户资金接入与自动兑付均未开放。

实时入口丢弃接收超过 30 秒或早于该 token 当前报价的积压记录；原始采集库仍保留这些数据供历史回测。持仓盘口每 15 秒公开只读刷新，操作前再次刷新并写入日志。GTD 使用原生策略定时器，在没有新行情时也会撤销到期订单。买单资金预留包含剩余份额的保守费用上限。

手工退出与撤改单也需预览后提交，退出保留预览限价；盘口向不利方向变化时 IOC 剩余份额撤销。
市场 tick size 修订通过原生 instrument 更新事件生效，先撤旧挂单并等待新报价，避免沿用旧精度舍入。
官方客户端的预期交易所订单 ID 另存为原生 Cache 通用键，启动加载后恢复关联；不保存签名正文或密钥。
Redis 写入为异步，本轮测试在核验事件已持久化后重启；机器断电导致尚未落盘的实盘提交仍需额外对账验收，
不能据此开启真实交易。

Sharpe 要求至少 30 个完整纽约市场日：排除接入日、当前日、缺失估值日和超过 5 分钟的采样缺口。外部充提和既有真实资产尚未接入，LIVE 显示未连接且不提供模拟替代值。原生 NETTING 持仓目前显示统一 Workbench 策略 ID，订单保留 manual/strategy 归属；多策略独立持仓归因仍需后续扩展。

运行记录保存 Git SHA 和 Python 源文件内容 SHA256。Windows worktree 的 Git 元数据在 WSL 下可能无法由 Linux Git 解析，可用从 Windows Git 核验的 `NICE_WEATHER_CODE_SHA` 补充；早期本地模拟会话的 SHA 字段为 unavailable，验收报告保留这一限制，不修改旧会话配置。

## 回滚与长时间验收

回滚只切换代码与环境；始终保留 requests/results、输入日志和导出数据。禁止恢复旧数据库覆盖新的订单记录。恢复哈希不一致时保留失败证据并暂停账户。

正式完成前必须另行保留至少一个完整纽约市场日的 Ubuntu 运行证据：YES/NO 接收、连续账户检查、重启恢复、采集/R2 正常、页面操作和导出。短时 WSL 测试不能替代该项。验收报告必须区分固定样本、公开自采样本和真实部署观察。

参考：官方 [v1.231.0 源码](https://github.com/nautechsystems/nautilus_trader/tree/v1.231.0/nautilus_trader)；审阅 [prediction-market-backtesting c76e77a](https://github.com/evan-kolberg/prediction-market-backtesting/tree/c76e77af00ef53472a9da8f66dae7fdd2d3e5928) 的公共数据与报告思路。未复制其代码、启用全局佣金补丁或引入其研究工具链。
