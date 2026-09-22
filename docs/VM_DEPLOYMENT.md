# 当前发布规则（2026-09-19）

2026-09-22 任务 6 补充：前端在本地构建。update_knyc_runtime.py 纳入市场目录、信号、Paper、回测与 Live 模块的实际服务映射；只停止已经安装且受影响的实例。新增 nice-weather-knyc-live@kalshi/poly_us，读取 /etc/nice-weather/knyc-live-{venue}.env 中的 NICE_WEATHER_LIVE_CREDENTIALS 文件路径与 NICE_WEATHER_BALANCE_PRECISION（Kalshi 4、Poly US 2）。凭据存 /etc/nice-weather/credentials，root:nice-weather、640；环境文件只含路径/精度，不含密钥。两实例使用现有虚拟环境和 /var/lib/nice-weather-knyc，初始交易关闭。部署前核验包大小、实际展开增量及可用空间；不增加固定容量门槛，不修改采集数据。

2026-09-19 存储修订：GitHub 保存代码历史，VM 只保留正在使用的运行目录和依赖环境，不按每次提交复制虚拟环境或累计回退包。代码回退从 GitHub 的精确提交重建发布包，校验后更新现役目录；只临时上传本次产物。版本由 runtime-manifest.json 和服务记录标识，旧物理目录名不能单独作为当前版本证据。部署成功后的临时包、废弃资源及旧目录列入精确清理清单，经已有明确授权或人工审核后删除；业务数据库不跟随代码回退。

`scripts/update_knyc_runtime.py` 对已安装 KNYC 环境执行有限范围的更新，默认仅检查；只有显式 `--apply` 才写入。验证发布包、manifest、现役基线与路径。终端改动只重启终端；`feed.py` 改动重启引用它的六个 KNYC 服务；仅 `us_runtime.py` 改动时重启两个 Paper、回测 worker 和用于刷新版本身份的终端，公开采集/HRRR 及旧 KLGA 服务保持运行。其他模块的改动拒绝应用，需单独核验影响服务。重复执行可接续相同清单的部分写入，不将旧账目恢复覆盖现役数据库。它不创建历史目录，不复制环境，不删除业务数据。

KNYC 终端按精确 SHA/产物清单部署，仅重启受影响服务。用户已授权常规部署，不要求逐步确认。实盘默认关闭，用户自行启用。回滚只切代码与环境，禁止用旧数据库覆盖新增订单/成交/采集数据；不兼容时暂停受影响交易服务并保留证据。

2026-09-19 用户明确补充 SSH 来源授权，适用目标和边界见仓库 AGENTS.md。`ssh.cloud.google.com` 已登录会话的访问、正常登录与重连、扩展重启后的同一来源恢复、普通 Connect/Retry，以及上传审核后的发布包和执行本项目部署命令，均已授权，不逐次询问。先核验项目/实例身份和当前输出；连接恢复后接续已有步骤，避免重复安装或覆盖数据。平台独立权限审批与未核验安全警告不能靠文档绕过，若受阻需准确说明。

下文为旧 KLGA 部署操作参考，其中全服务停机、自动恢复旧数据库和禁止实盘开发的条款不再适用于新终端发布。迁移必须先验证备份和兼容性。新终端验收前保留旧服务。

KNYC 于 2026-09-19 16:42:11 UTC 首次安装，后续原位更新沿用该目录与环境。`https://niceweather.trade/terminal` 已复用现有 Cloudflare Access；旧入口保留。最新运行版本、产物哈希、验证结果及剩余事项见 [部署记录](acceptance/knyc-vm-2026-09-19.md)。公网可访问仍不足以通过完整产品验收。

# Ubuntu VM 部署与自动验收

本文以 schema v7 统一部署为 canonical 流程。后续旧 `weather.sqlite3` 双目录命令仅用于回滚参考。

## 0. 统一部署摘要

- checkout：`/opt/nice-weather/repo`
- venv：`/opt/nice-weather/.venv`
- 配置：`/etc/nice-weather/nice-weather.toml`
- 数据库：`/var/lib/nice-weather/nice-weather.sqlite3`
- 服务：collector、market-stream、r2-sync timer、dashboard、shadow runner
- R2 新前缀：`nyc-klga/v2`

部署前记录远端合并 SHA，并写入 `/etc/nice-weather/nice-weather.env`：

```dotenv
NICE_WEATHER_GIT_SHA=<merged-main-sha>
```

停写顺序：

```bash
sudo systemctl stop nice-weather-r2-sync.timer
sudo systemctl stop nice-weather-runner.service
sudo systemctl stop nice-weather-market-stream.service
sudo systemctl stop nice-weather-collector.service
sudo systemctl stop nice-weather-r2-sync.service
sudo systemctl stop nice-weather-dashboard.service
```

已有统一库从 schema v4 升级时，先使用 SQLite backup API 保存精确副本，再执行原位迁移和验证：

```bash
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db migrate \
  --db /var/lib/nice-weather/nice-weather.sqlite3
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db verify \
  --db /var/lib/nice-weather/nice-weather.sqlite3
```

验证结果必须为 schema version 7、`integrity_check=ok` 且无 foreign key error。旧天气原文不在
本次迁移中删除，迁移后的空闲页交给 SQLite 后续写入复用。

确认无 writer 后，备份并迁移：

```bash
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db clone-migrate \
  --source /var/lib/nice-weather/weather.sqlite3 \
  --db /var/lib/nice-weather/nice-weather.sqlite3
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db verify \
  --db /var/lib/nice-weather/nice-weather.sqlite3
```

安装仓库内五个 unit，启动顺序固定为：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nice-weather-collector.service
sudo systemctl enable --now nice-weather-market-stream.service
sudo systemctl enable --now nice-weather-r2-sync.timer
sudo systemctl enable --now nice-weather-dashboard.service
sudo systemctl enable --now nice-weather-runner.service
```

部署先运行 `repair-settlement-dates --dry-run`，备份验证后执行 `--apply`。至少观察 16 分钟并检查 `version --json`、五个 unit、collector status、R2 ledger、Dashboard health、CLOB tick 和 SHADOW 决策。随后由部署观察服务持续检查 24 小时；关键日期、数据库完整性、服务或锁异常触发恢复上一精确 SHA 与数据库备份。禁止把 Runner 改为任何实盘模式。

旧库处置分两次人工检查：先逐一确认并移动 `/var/lib/nice-weather/live.sqlite3`、`live.sqlite3-wal`、`live.sqlite3-shm` 到固定隔离目录；24 小时后再次列出精确绝对路径并请求删除批准。不得使用 glob 或递归删除。

## Repricing v7 部署补充

1. 记录当前 SHA、服务状态、migration checksums 和 tick 数；核对 v4/v5/v6 校验和与已部署代码一致。校验和不符时停止迁移并调查，禁止直接覆盖记录。
2. 停止全部 writer，包括 market-stream、collector、runner 和 r2-sync timer/service。通过 SQLite backup API 将统一库复制到带 UTC 时间的独立路径，保留原 SHA 和环境文件副本；不删除历史备份。
3. 在备份的工作副本上执行 v7 migrate/verify，确认 `event_kind` 可空、旧 tick 数量不变、`integrity_check=ok`、无 foreign key error，再更新线上代码和迁移原库。
4. 恢复服务后检查版本 SHA/schema7、33 tokens 等实际发现数量、CLOB/Gamma 各自事件类型、最新接收时间，以及 dashboard/collector/market-stream journal 中的 SQLite/WAL/SHM 访问错误。
5. 浏览器核对今日和未来市场、全部 bin、数据源状态、差值及断流恢复。旧 tick 来源状态仍未经核验；截图中的每个归零需逐条对照原始 tick，不能批量归因或删除。

## 1. Cloudflare 与 GitHub 检查

1. 在 Cloudflare R2 控制台确认现有 bucket 名称。
2. 确认 API Token 对该 bucket 具有对象读写权限。
3. 在 GitHub PR 的 Files changed 中确认没有 `weather data api.txt`、Access Key 或 Secret Access Key。
4. 等待 `lint`、`unit`、`fixture-dashboard` 全部通过，再 squash merge。

## 2. 安装程序

仓库现为 private。现有开发机通过 Git Credential Manager 认证后仍可正常 fetch/push；
不要把凭据写进 remote URL、代码、聊天或部署命令。VM 若已配置对该仓库的只读访问，
可继续使用原有认证；尚未配置时，使用下列离线 Git bundle 流程将已审核提交传到同一项目 VM，
无需将开发机 GitHub Token 保存到 VM。

在已认证的开发机确认 PR 检查与精确 SHA 后：

```powershell
git fetch origin main
git rev-parse HEAD
git bundle create var/nice-weather-reviewed.bundle HEAD
Get-FileHash var/nice-weather-reviewed.bundle -Algorithm SHA256
scp var/nice-weather-reviewed.bundle <existing-ssh-alias>:/tmp/nice-weather-reviewed.bundle
```

VM 上先核对传输哈希、当前 SHA、工作区和服务状态；本轮接入采用独立环境，
保留原库及所有交易日志。下列 fetch 只导入对象，切换代码和重启仍须按照已通过验收的部署步骤执行。

```bash
sha256sum /tmp/nice-weather-reviewed.bundle
sudo -u nice-weather git -C /opt/nice-weather/repo status --short
sudo -u nice-weather git -C /opt/nice-weather/repo rev-parse HEAD
sudo -u nice-weather git -C /opt/nice-weather/repo bundle verify /tmp/nice-weather-reviewed.bundle
sudo -u nice-weather git -C /opt/nice-weather/repo fetch /tmp/nice-weather-reviewed.bundle HEAD
sudo -u nice-weather git -C /opt/nice-weather/repo rev-parse FETCH_HEAD
```

以下保留首次安装命令；`git clone`/`pull` 需要 VM 已有的仓库访问权限。若使用 bundle 首次安装，
将 clone 来源替换为已校验的 bundle 文件。不要为完成部署扩大仓库共享范围。

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv
python3 --version
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
sudo useradd --system --home-dir /opt/nice-weather --create-home --shell /usr/sbin/nologin nice-weather
sudo install -d -o nice-weather -g nice-weather -m 0750 /var/lib/nice-weather
sudo install -d -o root -g nice-weather -m 0750 /etc/nice-weather
sudo -u nice-weather git clone https://github.com/RENEGADES20/nice-weather.git /opt/nice-weather/repo
sudo -u nice-weather python3 -m venv /opt/nice-weather/.venv
sudo -u nice-weather /opt/nice-weather/.venv/bin/python -m pip install --upgrade pip
sudo -u nice-weather /opt/nice-weather/.venv/bin/python -m pip install -e '/opt/nice-weather/repo[collector]'
sudo /opt/nice-weather/.venv/bin/playwright install-deps chromium
sudo -u nice-weather env PLAYWRIGHT_BROWSERS_PATH=/opt/nice-weather/.playwright /opt/nice-weather/.venv/bin/playwright install chromium
sudo install -o root -g nice-weather -m 0640 /opt/nice-weather/repo/config/nyc_klga.toml /etc/nice-weather/collector.toml
```

若仓库已经存在，使用 `sudo -u nice-weather git -C /opt/nice-weather/repo pull --ff-only origin main` 更新。

## 3. 写入 R2 环境文件

不要把真实值直接放在命令行参数中。使用 root 权限编辑文件，减少 shell history 泄漏。

```bash
sudo touch /etc/nice-weather/r2.env
sudo chown root:nice-weather /etc/nice-weather/r2.env
sudo chmod 0600 /etc/nice-weather/r2.env
sudoedit /etc/nice-weather/r2.env
```

文件内容：

```dotenv
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_BUCKET=<现有bucket名称>
R2_ACCESS_KEY_ID=<本地TXT中的Access Key ID>
R2_SECRET_ACCESS_KEY=<本地TXT中的Secret Access Key>
R2_PREFIX=nyc-klga/v1
```

检查权限，输出只应包含文件元数据：

```bash
sudo stat -c '%U %G %a %n' /etc/nice-weather/r2.env
```

期望值为 `root nice-weather 600 /etc/nice-weather/r2.env`。

## 4. 单轮验证

```bash
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather config-check --config /etc/nice-weather/collector.toml
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather collect-weather --once --db /var/lib/nice-weather/weather.sqlite3 --config /etc/nice-weather/collector.toml
sudo bash -c 'set -a; . /etc/nice-weather/r2.env; set +a; exec runuser -u nice-weather --preserve-environment -- /opt/nice-weather/.venv/bin/nice-weather r2-check --db /var/lib/nice-weather/weather.sqlite3 --config /etc/nice-weather/collector.toml'
```

第三条命令会在 R2 的 `healthchecks/` 下留下一个小型不可变测试对象，不执行删除。

## 5. 启动 systemd

```bash
sudo install -o root -g root -m 0644 /opt/nice-weather/repo/deploy/systemd/nice-weather-collector.service /etc/systemd/system/nice-weather-collector.service
sudo install -o root -g root -m 0644 /opt/nice-weather/repo/deploy/systemd/nice-weather-r2-sync.service /etc/systemd/system/nice-weather-r2-sync.service
sudo install -o root -g root -m 0644 /opt/nice-weather/repo/deploy/systemd/nice-weather-r2-sync.timer /etc/systemd/system/nice-weather-r2-sync.timer
sudo systemctl daemon-reload
sudo systemctl enable --now nice-weather-collector.service
sudo systemctl enable --now nice-weather-r2-sync.timer
```

## 6. 状态与 24 小时验收

```bash
sudo systemctl status nice-weather-collector.service --no-pager
sudo systemctl status nice-weather-r2-sync.timer --no-pager
sudo journalctl -u nice-weather-collector.service -n 100 --no-pager
sudo journalctl -u nice-weather-r2-sync.service -n 100 --no-pager
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather collector-status --db /var/lib/nice-weather/weather.sqlite3 --config /etc/nice-weather/collector.toml
```

运行 24 小时后确认：三类 API 均有新版本、Weather.gov 页面无持续解析错误、R2 存在 raw/evidence/parquet/manifest、预计日增量不超过 10 MiB。达到 7 GiB 时状态命令输出 `warning=true`，系统不会自动删除数据。

## 7. 回滚

```bash
sudo systemctl disable --now nice-weather-collector.service nice-weather-r2-sync.timer
sudo -u nice-weather git -C /opt/nice-weather/repo log --oneline -5
sudo -u nice-weather git -C /opt/nice-weather/repo switch --detach <上一稳定commit>
sudo systemctl start nice-weather-collector.service
sudo systemctl start nice-weather-r2-sync.timer
```

回滚不删除 `/var/lib/nice-weather` 或任何 R2 对象。系统稳定后，由用户人工将本地 TXT 移入密码管理器或删除；项目不会代为删除。

## Nautilus 独立工作台服务

新增 sandbox/backtest service 及 dashboard drop-in；Python 3.12 独立环境，操作目录 `/var/lib/nice-weather-trading/requests`，结果库位于 `/var/lib/nice-weather-trading`。正式部署、完整市场日观察和回滚流程见 [TRADING_WORKBENCH.md](TRADING_WORKBENCH.md)。回滚不得覆盖已经新增的模拟/交易日志。真实服务本轮不启用。
