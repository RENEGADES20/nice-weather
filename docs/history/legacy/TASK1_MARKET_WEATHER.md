# 任务 1：跨日市场与天气组件

范围：KNYC、Kalshi、Poly US。独立组件及公开数据查询；主入口接线、合并和部署交给任务 6。不改变策略、模拟执行、实盘账户或回测。

## 接口

复用终端现有认证，所有新端点均为 GET。

| 路径 | 参数 | 返回 |
| --- | --- | --- |
| `/api/markets` | `venue` | `venue/station_id/days`；每日期含该日合约、挂牌状态、是否已有行情及接收时间 |
| `/api/market-day` | `venue/day` | 合约、观察起止、窗口核验状态、显示时区及缺失原因；交易结束时间在各合约 `close_time` |
| `/api/weather-history` | `venue/day` | 该日窗口、`as_of`、分源版本点、CLI 原文版本和缺失来源 |
| `/api/history` | `venue/day/token`，可加 `before` | 归属字段、`points/next_before/reason`；严格核对 token 归属，保留市场日前已发生的报价 |
| `/api/market-quote` | `venue/day/token` | 选定合约的公开即时盘口；无有效双边报价时不生成中间价 |

旧 `/api/history?token=…&before=…` 数组格式保持兼容。新分页沿用 feed 序号，不把全日数据截成最近一页。没有合约和没有价格历史分别为 `NO_CONTRACTS`、`NO_PRICE_HISTORY`；没有数据不改日期。报价失败返回失败状态，不能当成零。

`market_directory` 是目录专用事件，不进入既有策略合约转换。挂牌发现沿用五分钟元数据周期，在同一采集进程内异步进行，不阻塞当天盘口循环；无新服务、无数据库迁移、无新增依赖。Kalshi 使用 series 过滤及 cursor，Poly US 使用公开事件 limit/offset 并严格匹配 KNYC slug。仅保存未来合约元数据；选定合约即时报价通过只读请求取得，不扩大后台盘口订阅。即时读取不补写过去的报价历史。

目录合并既有 `settlement_watch`、历史 `contracts` 事件与最新目录事件，同日新快照替换旧档位。旧版数据库没有 `settlement_watch` 时仍可读取。天气查询读取原事件，去除重复轮询但保留温度修订和预报版本；原库不回填或改写。

## 组件接线

导出位于 `frontend/terminal/src/market-weather/`：`MarketSelector`、`WeatherAnalysis`、`useMarketCatalog`、`useMarketWeather`。样例见该目录 `preview.tsx`；本地 Vite 访问 `/market-weather.html`，在现有终端登录后使用。

```tsx
const catalog = useMarketCatalog(selection.venue);
const contract = catalog.data?.days.find(d => d.day === selection.day)
  ?.contracts.find(c => c.yes_token_id === selection.token);
const weather = useMarketWeather(selection, !!contract?.active);
// selection 为 { venue, day, token }，由主页面统一管理并保存到 URL。
<MarketSelector catalog={catalog.data} value={selection} onChange={setSelection}
  loading={catalog.loading} error={catalog.error} />
<WeatherAnalysis data={weather.weather} quotes={weather.quotes} token={selection.token}
  loading={weather.loading} error={weather.error} priceReason={weather.priceReason} />
```

将现有 WebSocket 的 `book` 事件交给 `weather.acceptBook(event)`；hook 校验 token 和当前选择。目录每五分钟、天气每分钟刷新；所选开放合约公开报价每十五秒刷新。请求取消与归属检查同时生效。缓存按平台/市场日/token 区分，最多保留最近 12 个选择；不限制服务器历史页数。主入口无需从实时 `snapshot.contracts` 重新推断可选日期。

图表继续使用已有 Lightweight Charts 和 `trading-chart/src/difference.ts` 的分段、分钟变化与双向同步函数。六类差值视图分开温度轴和价格轴；HRRR/NWS 预报可独立选择。常规刷新保持视野，换日重定位；独立样例把选择保存到 URL，刷新后恢复。

## 时间与数据口径

- 纽约当地时间用于显示；归日依据所选合约观察窗口，不能用 API 交易结束时间替代。未核验窗口有明确标注。无合约时仅显示标注为展示用途的 KNYC 标准时气候日窗口。
- 主图按观测/有效时间呈现已取得的最新修订，明确为回顾展示；分钟图按实际接收时间构造当时已知序列。HRRR 分片收到时间与完整发布事件时间取较晚值。
- 预报修订比较同来源、不同版本的同一有效小时；无法比较时缺失。没有真实接收时间的点不进入分钟图。
- 价格 `0.40 → 0.45` 表示 `+5` 个百分点。主图为公开盘口中间价，缺少 Bid 或 Ask 时显示单边报价并保持中间价缺失，不以模型概率替代。
- 展示有效性沿用既有天气分析：观测 5400 秒、预报起报 21600 秒、盘口中间价 600 秒；超过有效期断线。它们只用于图形缺口，不进入交易门禁。
- 独立官方小时温度、NWS 站点观测、METAR、CLI 分源保存和展示。未采集的官方小时温度/NWS 预报保持缺失，不能用另一个源改名替代。

## 本地验证（2026-09-22）

真实摘要见 `acceptance/task1-market-weather-20260922.json`。双平台 9 月 19 日历史、9 月 22 日当天、9 月 23 日未来均各有 6 个合约。历史来源是已有本地公共采集库的抽样：天气每半小时保留一个已收事件，盘口每分钟保留一个已收事件，原时间不变，抽样缺口仍显示。生产查询读取完整已有事件，未采用该抽样规则。

9 月 19 日样本包含 22 个 METAR、24 个 NWS 站点观测、173 个 HRRR 有效时刻/版本点、2 个 CLI 版本；没有独立官方小时温度和 NWS 预报。当天/未来验收库没有历史天气与报价，公开即时报价读取成功；两个平台当天最低档只有 Ask，中间价保持缺失，未来最低档取得双边报价。

已在 Chrome 及应用内浏览器完成真实历史图检查、Kalshi 历史档位及天气源切换、Poly US 历史图平移/缩放/十字线、双平台当天及未来市场与公开报价展示、预报来源缺失提示、快速切换和刷新恢复。未来最低档示例中间价 Kalshi 4.50%、Poly US 6.00%，价格会继续变化。发现的初载局部视野问题已修复：加入分钟时间基准并在数据重绘期间保留用户视野。自动浏览器回归补充延迟响应隔离和缺数据日不回退。

Python 相关回归 34 项通过；新增前端数值及组件测试通过，TypeScript 检查与本地构建通过。既有依赖产生弃用警告，没有因本任务添加或升级依赖。最终 PR CI 状态以 GitHub 为准。

补充回归：天气 A→B→A 修订保留后续实际接收时间，新增查询测试合计 6 项通过；旧图表共享分钟函数 8 项通过。公开目录接口依据 [Kalshi Get Events](https://docs.kalshi.com/api-reference/events/get-events) 和 [Poly US 官方 SDK](https://github.com/Polymarket/polymarket-us-python)，未引入 SDK 依赖。

本任务没有部署或修改 VM，没有修改主入口、全局样式、部署配置及任务 5 未提交文件。终端正式接线与线上验收仍由任务 6 完成。
