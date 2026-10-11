# 发布与运行核验

更新：2026-10-10。本轮仅文档，不部署。当前运行事实见 [CURRENT_STATE](CURRENT_STATE.md)，功能完成条件见 [KNYC_TERMINAL_DELIVERY](KNYC_TERMINAL_DELIVERY.md)。

## 发布入口

- `scripts/build_runtime_release.py`：从精确提交构建运行发布包。
- `scripts/update_knyc_runtime.py`：现有 prepare/apply 更新器；运行前以当前代码帮助与影响清单核对参数。
- `deploy/systemd/nice-weather-knyc-*.service`：feed、HRRR、回测、Paper/Live 与 R2 运行单元。
- `scripts/observe_knyc_runtime.py`：运行核验辅助；服务健康不替代界面功能验收。

SSH 项目、VM、普通重连及已批准 IAM 范围沿用 AGENTS.md。本轮未改变基础设施或权限。

## 一次功能发布

1. 功能工作包的实现、隔离整链案例、审查和 required checks 完成；同一功能的关联修复合并进同一 PR。
2. 正常合并，生成精确 SHA 的发布包并核验 manifest；只重启受影响服务。
3. 核验版本、账户/订单保留、数据新鲜度和 worker 积压；按影响范围完成真实浏览器操作，保存业务回执和账目证据。
4. 更新 CURRENT_STATE 对应功能行，证据留在 acceptance；失败保留事实，按同一问题修复，避免不断重置所有验收。

## 回滚及数据保护

代码回滚必须兼容新产生的订单/成交/账户和采集数据，禁止用旧数据库替换新账目。数据迁移和回收先验证覆盖与恢复方案。批量删除文件仍需列出精确目标并取得人工确认。旧事故中的删除、停服或游标重置命令不能自动用于新故障。

## 既有部署记录

#101 合并为 `37209f7`，2026-09-28 部署：发布包 713,984 字节、86 个运行文件、SHA-256 `d27fd36cfa147036307ba40281bd8a49036788bb6434f7994ed6d783542b5a3c`；预演、准备、应用均退出 0，manifest 逐文件 `mismatches=[]`；HRRR 另行 `enable --now`；手动触发 KNYC R2 一次完成 GET 核验并清空 8 条天气正文；11 个服务 active，terminal health 200；根盘可用约 63.3%。原 #102 记录已并入本节后关闭。这是历史记录，10 月 10 日 VM 实测待阶段 B。

VM 磁盘有限：发布包只含运行代码与 `index.html` 引用的前端资产；不向 VM 上传研究数据、历史快照、测试或构建中间产物。VM 清理须先列精确目标并经用户确认。

9 月全部具体操作、哈希和故障过程完整保存于 [历史发布记录](history/2026-10-10-baseline/VM_DEPLOYMENT.md)。旧相对链接应按原位置 `docs/` 解析；后续使用具体命令前须核对现役路径与脚本。
