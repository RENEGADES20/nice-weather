# Trading 统一工作台部署验收

## 发布依据

- 功能 PR：[#37](https://github.com/RENEGADES20/nice-weather/pull/37)。六项 required CI 全部通过后合并。
- 运行提交：`f083227e1cef65154c6ed6b2e20c190c26016e53`；受测分支提交：`0b66cd0bb04b58fe8642c1347ff764c884bb15fc`。
- CI run：`34739487126`。Python 87 项、Nautilus 原生 51 项、TypeScript 14 项、桌面/移动浏览器 16 项；lint/security 通过。
- 本地 Windows 的 Repricing 大样本耗时检查偶发超过原有 5 秒门槛；Linux CI 的完整浏览器套件通过。本轮没有放宽该门槛，也没有宣称原有 Repricing 性能目标整体达标。
- Live 账户未接入，全部订单操作禁用；没有配置实盘密钥或发送真实订单。

## 发布与备份

- VM：`nice-weather-vm`，`us-west1-b`；入口：<https://niceweather.trade/>。
- 2026-09-13 05:21:36 UTC 开始发布。只重启 sandbox、backtest、dashboard；Collector、Market Stream、Runner 继续运行。
- Git 清单构建的压缩包为 247,371 字节，47 个运行文件加一份 manifest。SHA256：`36129c1e2b8cea8dbe07e595fc655b7ca0a5373c6d8bd0021960bb7c85b84626`。
- VM 使用清单覆盖运行文件，`/opt/nice-weather/repo/runtime-manifest.json` 与服务版本环境记录实际运行提交。VM 的 Git HEAD 保留先前 checkout，不能单独作为实际发布版本的依据。
- 传输包及发布辅助程序暂存 `/run`；没有上传测试库、截图、视频、浏览器、Node、node_modules、研究产物或开发缓存。
- 唯一受影响数据库备份：`/var/backups/nice-weather/trading-terminal-20260913/results.sqlite3`，100,331,520 字节，SQLite backup API 生成并通过 `quick_check`。
- 备份 SHA256：`acac908ea0800586830719d6173726fb82977cb70bf5f51f184db6cbf9519145`。同目录 `backup.json` 记录用途、版本、大小、哈希及部署前账目。
- 部署前可用空间 15,359,209,472 字节。未批量清理历史文件，没有重复制作全库备份。

## 账户与存储基线

- Paper 会话：`33de5aa0-a436-42f1-b604-5e60a8ada485` / `sandbox-001`。
- 部署前现金与权益 `99.95752`，费用 `0.00248`，历史成交 2 笔，持仓 0；历史输入 79,592 条。
- 新盘口仅在 Worker 内存维护；持久化原生交易事实、必要账户事实、稀疏权益，不保存新盘口历史。
- 原生旧会话先按旧规则逐项核对，再转换 execution_version=3。重启时取消不能恢复排队位置的挂单并释放原生资金预留，保留现金、成交和费用。

## 部署后观察

验收进行中，后续填写实际完成时间、账户恢复、公开 UI、服务状态与存储增量。
