# 美国平台实盘适配：协议与验收边界

## 2026-09-22 当前接线状态

后续接通 USReadOnlyExecutionClient → 原生消息总线 → Portfolio → Cache，真实账户只读验证均成功。旧接收时间或已知来源时间倒退的快照拒绝；刷新中断保留已有账户事实，同时清除本次快照和连接状态。只读客户端拒绝交易命令，完整对账报告显式报未完成，禁止空报告冒充对账成功。52 项相关测试通过，见 acceptance/knyc-native-account-bus-2026-09-22.json。独立 Live worker、可写执行及完整余额/订单对账仍待接通。

新增已验证的只读阶段：AccountState 可携带平台余额事实，MarginAccount 保留未知现金/可用/冻结值为 None。真实双平台认证与原生对象构造成功，44 项测试通过；不会将空 balances 解释为零，也不会据此启用交易。Kalshi 实际有 15 笔非 KNYC 历史订单和 28 笔成交；订单缺原始 TIF，而原生 TimeInForce 没有未知枚举，不补造 GTC。源码与原始响应哈希、零订单提交尝试见 acceptance/knyc-native-account-readonly-2026-09-22.json。此实现尚未部署，完整 LiveExecutionClient、缓存及账户对账仍未完成。

账户口径补充：直接读取官方 Markdown OpenAPI 确认 [Kalshi Total Resting Order Value](https://docs.kalshi.com/api-reference/portfolio/get-total-resting-order-value.md) 仅供 FCM 会员使用，不能将其当作当前个人账户的通用冻结金额接口。Poly US 的 [余额定义](https://docs.polymarket.us/api-reference/account/get-account-balances.md) 将 currentBalance 定义为不含证券价值的现金，buyingPower 则还考虑证券估值和挂单；两者不能未经验证直接映射为同一现金余额。其 [抵押规则](https://docs.polymarket.us/market-structure/collateral-and-margin) 对挂单按单合约核验，跨合约挂单共享购买力；本项目全账户累计预算必须独立预留，不能依赖交易所替代。当前仍未构造不完整的原生现金事实。

签名传输和双平台原生 OrderStatusReport / FillReport / PositionStatusReport 转换已部署；真实只读账户认证、分页历史读取及 Poly US 空订单 WebSocket 快照已完成，仍未完成 LiveExecutionClient、余额/抵押映射与完整原生对账。下文 9 月 19 日的“未读取密钥”等表述仅记录当时状态。

新增独立 Live 进程使用的 USD 初始化模块 `us_currency.py`，同时注册 Nautilus Python/Rust 币种表，要求显式提供核验后的两位或四位精度；同一进程不允许切换平台或精度。导入模块不修改注册表。8 项测试通过，包括独立子进程中的亚分币 Money 往返、AccountState 序列化恢复及父进程 Paper USD2 隔离。首次测试一个子进程启动超时，保持 60 秒限制重新运行后 8 项通过；没有跳过或放宽断言。该模块尚未接入独立 Live worker，不能视作实盘精度验收完成。

余额口径继续分开核验：[Kalshi balance_dollars](https://docs.kalshi.com/api-reference/portfolio/get-balance) 表示可用余额，不能将 portfolio_value 当作现金；[Poly US balances](https://docs.polymarket.us/api-reference/account/get-account-balances) 分列 currentBalance、buyingPower、marginRequirement 和 balanceReservation。不能以字段相似或空持仓直接认定完整现金/抵押映射。凭据仅沿用已有文件，禁止创建或轮换密钥；真实订单由用户提交。

## 2026-09-19 历史实施记录

2026-09-19。当前为开发组件，尚未交付可启用的实盘账户。`us_transport.py` 不在终端命令链中；API 的 Live 门禁保持关闭，未读取真实密钥或发送真实订单。

## 已实现并在本地验证

- Kalshi RSA-PSS SHA256 与 Poly US Ed25519 签名；固定官方域名，不携带认证跳转，不自动重试写请求。
- YES/NO、买卖方向、限价和数量按各平台协议编码。Kalshi 固定精度不足时拒绝，禁止舍入用户指令。
- SQLite 保存账户命名空间、稳定请求 ID、请求身份摘要及 submitting/accepted/rejected/unknown。重复请求读取原回执；不同载荷重用 ID 拒绝。进程中断或网络异常不能触发第二次提交。
- 未核验合约拒绝新增订单。默认无启用/风险 gate，不能写入平台；今后调用者必须验证账户身份、订单归属、市场白名单及限额。
- 已知交易所订单 ID 可撤单，独立请求 ID 去重。规则过期或另一个未知提交不自动禁止减少既有风险；仍须通过账户归属 gate。撤单回执不替代成交/剩余数量对账。
- 账户余额、订单、持仓有界读取；分页循环或身份冲突失败。保留交易所有符号净持仓，不推算成虚构 YES/NO 账户。读取结果固定 `reconciled: false`，不据空订单列表解除 unknown。

16 项测试使用现场生成的测试密钥和 httpx MockTransport，覆盖签名验证、精度、超时、响应分类、重启去重、规则门禁、分页失败及未知状态期间撤单。无真实认证、无网络下单；Linux CI 安装 `.[trading,terminal,us-live]`。

## 尚需实现和验证

1. Nautilus LiveExecutionClient、标准 OrderStatusReport/FillReport/PositionStatusReport 及事件去重。现有 Paper 是每市场 YES/NO 双合约；平台净持仓与抵押语义需要统一、验证后映射，不能靠新建资金账本掩盖差异。
2. 完整订单/成交历史、断线补齐与重启对账。Kalshi 历史接口与当前窗口分别读取；Poly US 响应丢失且缺交易所订单 ID 时不能用相似价格/时间自动认领，也不能凭列表缺失判为未下单。
3. 服务端凭据配置、外部账户身份绑定、密钥轮换及用户启用；共享风险/白名单、日亏损、撤单和停止策略。真实余额不能由 API 金额字段猜测。
4. 原生行情/账户订阅、US 最终结算、策略归属投影与故障注入。真实凭据缺失阻止平台集成验收，但上述尚未编写的功能仍记为待实现。
5. 逐平台 KNYC 规则审计。现有公开 Kalshi 原文指定 The Weather Company，Poly US 指定 NWS Daily CLI；窗口、舍入和修订规则未完全确认。当前真实合约保持 ambiguous/no-trade。

## 协议依据

2026-09-19 补充规则审计：实际 KXHIGHNY 当日合约指定 The Weather Company，并允许因初次正式数据的重大错误延期处理；`Final` 状态不能直接替代交易所最终结算。官方 [Daily Climate 页面](https://weather.com/kalshi) 说明日值来自 NWS/NOAA CLI、CF6 备用，并保留后续修订；页面所见 9 月 18 日 NYC 最高值为 80°F。此证据支持继续比较 CLI 标签与平台结果，尚不能补造历史接收时间、推断全部修订截止或解除 no-trade。另，系列所链接的 2025 年产品认证文本与当日来源指定不同，不能只按旧 PDF 放行。

- [Kalshi 认证](https://docs.kalshi.com/getting_started/quick_start_authenticated_requests)、[V2 下单](https://docs.kalshi.com/api-reference/orders/create-order-v2)、[V2 撤单](https://docs.kalshi.com/api-reference/orders/cancel-order-v2)、[持仓](https://docs.kalshi.com/api-reference/portfolio/get-positions)。
- [Poly US 认证](https://docs.polymarket.us/api-reference/authentication)、[下单](https://docs.polymarket.us/api-reference/orders/create-order)、[撤单](https://docs.polymarket.us/api-reference/orders/cancel-order)、[账户持仓](https://docs.polymarket.us/api-reference/portfolio/get-user-positions)、[官方 SDK](https://github.com/Polymarket/polymarket-us-python)。

协议事实与本地 mock 通过不等于平台集成验收。实际资金交易仍由用户启用并验证。
