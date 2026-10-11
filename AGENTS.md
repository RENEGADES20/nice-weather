# 项目工作规则

## 开始任务

1. 核对工作目录、分支、HEAD 与 origin/main。开发一律在 `.worktrees/` 下从 origin/main 建分支；不依据落后分支的源码判断现役实现。
2. 读 docs/CURRENT_STATE.md（事实与阻断）和 docs/KNYC_TERMINAL_DELIVERY.md（验收）。按需读 PROJECT_REBASELINE（工作包 W0–W6）、AUDIT_2026-10-10（缺陷编号）、KNYC_STRATEGY_DELIVERY（策略 v3）、SYSTEM_ARCHITECTURE、DECISIONS、VM_DEPLOYMENT。同一任务内未变化的文档不重复读取。
3. 涉及交易功能时，先确认代码级可交易性（A0：合约 parsed、有概率、门控原因可解释），再谈功能完成。

## 范围与业务约束

- 个人桌面统一终端；KNYC 首站，Kalshi 优先、Poly US 其次。Poly Intl 暂不开发、部署或验收，保留 KLGA 历史与回滚能力。
- S1/S2/S3 接入回测、模拟与实盘接口，复用已接入的 Nautilus 订单/账户事件；研究收益不代表可实现收益。
- 实盘接口可开发、部署，默认关闭；账户、白名单、额度和启用由用户在终端控制。用户允许 Agent 提交真实订单，但额度在功能开发完成后才确定，确定前不启用；工具或平台要求人工提交的环节交给用户；不进行充提或资金转移。
- 历史判断只使用当时已收到的数据。保存来源时间、实际 received_at、规则/模型版本和修订证据，不补造历史时间。
- 站点、日期、温标、档位、舍入、观测窗口或结算源歧义时 no-trade。KNYC/KLGA 与 CLI/小时最高值不混用。Kalshi 经用户批准可用 CLI 模型代理，结算源与模型标签分开记录。
- 密钥只由服务端读取，不入仓库、前端、日志或回测；Paper/Live 隔离。未知订单先对账，不重复提交。
- 回滚保留新增订单、成交和采集数据，禁止旧库覆盖新账目；保留异常和停止交易原因。
- 批量删除文件（本地、仓库、VM）先列出精确目标并取得人工确认；编辑文件内容不属于批量删除。

## 交付方式

- 闭环：验收条件 → 实现 → 测试 → 审查 → 修复 → PR → 检查通过后合并 → VM 部署 → 观察 → 记录。普通步骤已授权，无需请求继续；不绕过分支保护、伪造审核或完成状态。缺权限/凭据、不可消除的数据风险或待批准删除时记录阻塞，继续独立工作。
- 一个完整用户功能或紧密相关模块一个 PR，关联修复并入；严重生产事故可单独 hotfix。不按单个按钮、单条 SQL 或单张截图发布。
- 每个功能 PR 必须在真实整链测试底座（W0：真实 FastAPI、worker、SQLite、Nautilus，浏览器从界面操作，不拦截订单/账户接口）上新增或更新用例。mock 浏览器测试只用于界面与失败分支，不能证明业务闭环。
- 验收按 KNYC_TERMINAL_DELIVERY 的真实用户操作与账目结果，不以固定次数或等待天数代替。未变化的通过项不重复全量验收。同一失败无新变化时再现，停止重试，转根因分析。
- 回测先复用已有天气及官方历史数据；新增持续采集须有明确必要性和容量/成本方案。VM 磁盘有限，不向 VM 上传研究数据、历史快照或构建中间产物。
- 架构、范围或数据口径变化同步 CURRENT_STATE 与 DECISIONS；历史决定保留并标明被替代。CURRENT_STATE 只记能力、阻断与证据，过程放 acceptance 或 history。

## VM 与权限

- SSH 访问与重连已由用户授权：本项目使用的 agent 浏览器工具（含 Codex 浏览器扩展、Claude in Chrome）可沿用用户已登录的 `https://ssh.cloud.google.com` 会话，连接项目 `project-be5e00e4-0ddc-40cf-ba8`、区域 `us-west1-b` 的 `nice-weather-vm`，读取输出、上传已审核发布包、执行部署与核验；正式终端可沿用已登录的 Cloudflare Access 会话。正常登录、断线重连、浏览器重启后的同源恢复、普通 Connect/Retry 无需重复确认。
- 仅沿用已有账户与权限，不扩大 IAM/网络访问，不绕过安全警告或未核验的主机身份变化，不输出密钥。工具若独立拒绝审批，报告具体动作与原因，不绕过。
- 2026-09-19 用户批准：为 VM 服务账户 `644686621710-compute@developer.gserviceaccount.com` 添加 `roles/logging.logWriter` 与 `roles/monitoring.metricWriter`；仅此两项。
- 部署细节见 VM_DEPLOYMENT.md。

## Skills

仅调用任务实际需要的 Skill。本地策略代码/数据分析/证据读取不自动触发 X 采集或完整学术管线。付费 X 调用使用 weather-market-research，先确认本轮预算并遵守去重与费用记录；优先本地证据，索引见 RESEARCH_INDEX.md。不修改个人全局 Skills/插件。不要求固定理解汇报、逐阶段确认或默认缩减范围。
