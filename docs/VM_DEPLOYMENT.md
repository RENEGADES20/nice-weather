# Ubuntu VM 部署与人工验收

本文以 schema v5 统一部署为 canonical 流程。后续旧 `weather.sqlite3` 双目录命令仅用于回滚参考。

## 0. 统一部署摘要

- checkout：`/opt/nice-weather/repo`
- venv：`/opt/nice-weather/.venv`
- 配置：`/etc/nice-weather/nice-weather.toml`
- 数据库：`/var/lib/nice-weather/nice-weather.sqlite3`
- 服务：collector、r2-sync timer、dashboard、shadow runner
- R2 新前缀：`nyc-klga/v2`

部署前记录远端合并 SHA，并写入 `/etc/nice-weather/nice-weather.env`：

```dotenv
NICE_WEATHER_GIT_SHA=<merged-main-sha>
```

停写顺序：

```bash
sudo systemctl stop nice-weather-r2-sync.timer
sudo systemctl stop nice-weather-runner.service
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

验证结果必须为 schema version 5、`integrity_check=ok` 且无 foreign key error。旧天气原文不在
本次迁移中删除，迁移后的空闲页交给 SQLite 后续写入复用。

确认无 writer 后，备份并迁移：

```bash
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db clone-migrate \
  --source /var/lib/nice-weather/weather.sqlite3 \
  --db /var/lib/nice-weather/nice-weather.sqlite3
sudo -u nice-weather /opt/nice-weather/.venv/bin/nice-weather db verify \
  --db /var/lib/nice-weather/nice-weather.sqlite3
```

安装仓库内四个 unit，启动顺序固定为：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nice-weather-collector.service
sudo systemctl enable --now nice-weather-r2-sync.timer
sudo systemctl enable --now nice-weather-dashboard.service
sudo systemctl enable --now nice-weather-runner.service
```

至少观察 16 分钟并检查 `version --json`、四个 unit、collector status、R2 ledger、Dashboard health 和 SHADOW 决策。禁止把 Runner 改为任何实盘模式。

旧库处置分两次人工检查：先逐一确认并移动 `/var/lib/nice-weather/live.sqlite3`、`live.sqlite3-wal`、`live.sqlite3-shm` 到固定隔离目录；24 小时后再次列出精确绝对路径并请求删除批准。不得使用 glob 或递归删除。

## 1. Cloudflare 与 GitHub 检查

1. 在 Cloudflare R2 控制台确认现有 bucket 名称。
2. 确认 API Token 对该 bucket 具有对象读写权限。
3. 在 GitHub PR 的 Files changed 中确认没有 `weather data api.txt`、Access Key 或 Secret Access Key。
4. 等待 `lint`、`unit`、`fixture-dashboard` 全部通过，再 squash merge。

## 2. 安装程序

以下命令在 Ubuntu VM 上运行。仓库是公开仓库，无需向 VM 写入 GitHub Token。

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
