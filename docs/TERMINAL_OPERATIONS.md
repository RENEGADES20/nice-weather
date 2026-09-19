# KNYC 终端开发与运行

状态：迁移版本 59e0b3e 已部署 VM，尚未通过完整产品验收。实盘适配和 KNYC 模型未完成；真实采集合约的规则门禁关闭。旧 Streamlit 入口继续保留。部署及恢复证据见 acceptance/knyc-vm-2026-09-19.md。

主分支已合并首批实现 PR #45（`e00ac3ce`）。后续 US 签名传输模块使用额外依赖 `.[us-live]`，仅供适配开发；安装该依赖不会读取密钥、启动实盘账户或解除终端门禁。它的验收边界见 US_ADAPTER_ACCEPTANCE.md。

VM 发布前核验：原 `/opt/nice-weather/repo/runtime-manifest.json` 指向 `fc83189`，清单文件哈希全部匹配；旧 git HEAD 较早，不能单独作为运行版本依据。新版本位于 `/opt/nice-weather/knyc-current`。2026-09-19 16:44 UTC 安装后剩余约 2.3 GB（92% 已用），需继续评估完整市场日容量；KNYC 采集在磁盘余量不足 1 GiB 时拒绝新增原文及行情写入，保留旧数据与少量健康状态。此保护不清理其他服务的数据，也不能限制旧服务增长。

计划中的迁移预览使用 `/terminal`，API `/api/*`、资源 `/assets/*` 通过现有 HTTPS 隧道转发到回环端口 8767；这些路由尚未配置。其余路径沿用现有入口，完整验收后再做最终切换；同一个新终端内包含交易与回测。

## 本地运行

Python 3.12 独立环境，安装 `pip install -e ".[trading,terminal,weather-feed]"`。旧 collector 的 PyArrow 版本范围与 Nautilus 环境分开。进入 `frontend/terminal` 执行 `npm ci && npm run build`。

各进程使用同一独立数据目录；不要指向历史生产交易库：

```bash
python -m nice_weather.trading.feed --root var/knyc
python -m nice_weather.trading.hrrr --root var/knyc
python -m nice_weather.trading.us_runtime --root var/knyc --venue kalshi
python -m nice_weather.trading.us_runtime --root var/knyc --venue poly_us
python -m nice_weather.trading.us_runtime --root var/knyc --backtests
python -m nice_weather.trading.api --root var/knyc
```

API 启动前在服务端环境设置 `NICE_WEATHER_TERMINAL_PASSWORD` 和 `NICE_WEATHER_TERMINAL_ORIGIN`（本机默认 `http://127.0.0.1:8767`）。密码不写入仓库或浏览器存储。默认仅监听回环地址，部署必须经过现有 HTTPS 代理并配置准确 Origin。重启使浏览器会话失效；订单、回测请求和采集数据保留。

交易指令在发送前写入浏览器本地存储（不含密码或 API 密钥），使用原生 Web Locks 协调同源标签页。请求超时或刷新后继续按原 ID 查询回执；仅明确 accepted/rejected 才解除待核验门禁。收到 202 只表示排队；404 不能证明交易所未接单，只允许向本项目去重队列重发同 ID、同参数。此重试机制不允许直接重发平台资金订单。保留本地待核验记录，勿通过清除浏览器数据解除门禁。停止策略和已有订单撤单仍可提交；若账户进程离线，由服务端拒绝并显示原因。

`feed --once` 和 `us_runtime --once` 用于有界检查。HRRR 独立进程按循环采集；未启动这些命令或服务时不会后台采集。未采集的历史不生成真实接收时间。

## 发布与回滚

`python scripts/build_runtime_release.py <exact-commit> <output.tar.gz>` 仅打包该提交中 HTML 实际引用的静态资源、Python 源码、配置和服务定义；附逐文件 SHA256 清单。不打包账户、原文库、凭据和研究目录。

新服务模板使用 `/opt/nice-weather/knyc-current` 指向独立版本目录，每个版本内创建 `.venv`；共享新增数据只保存在 `/var/lib/nice-weather-knyc`。`nice-weather-knyc-paper@kalshi` 与 `@poly_us` 有各自原生账户和进程锁；两者共用采集库，资金不互相混用。终端密码/Origin 文件是 `/etc/nice-weather/terminal.env`，只允许服务用户及管理员读取。

核验 VM 现有 manifest、服务、磁盘与备份后才切版本。只重启上面新增/变更的服务，保留 KLGA collector 与旧入口。回滚切回兼容代码/环境，保留全部新增数据库；不恢复旧库覆盖新订单。原终端替换、完整市场日和实盘验收完成前，不撤下旧入口。

## 验证边界

本地 Playwright 使用 6 档、每档 1500 历史点的合成行情，记录切档、命令反馈、事件到显示。此测试不测交易所延迟或 VM 网络，也不能证明一整天性能。无模型的后台回测标记 `no-data`，不能解释为零交易但已成功验证策略。
