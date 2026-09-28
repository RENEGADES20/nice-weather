# KNYC feed 整库人工重建操作单（2026-09-28）

**最新进展（16:52 UTC）**：用户已人工完成第 1–5 步并按第 6 步恢复 KNYC feed、两 Paper、两 Live、终端和 R2 timer；只读核验服务均 active，HRRR/backtest 仍停。新库 feed 1,042 条，两 Paper 游标 1,034/1,039，根盘可用 19,103,068,160 字节（63.5%）。用户明确接受该容量结果，原三分之二目标不再作为本轮验收门槛。下列命令保留为实际操作记录，不应重复执行删除和游标重置。HRRR 与 backtest 待本轮 PR 合并部署后恢复；HRRR 新版只保留 24 小时已消费运行输入，索引/GRIB 原文不写盘。

本操作由用户在 `nice-weather-vm` 上人工执行。只针对 KNYC feed；KLGA collector、KLGA R2、`results.sqlite3`、请求库、账户、订单和成交不得删除。旧库包含天气解析、采集元数据和历史行情，删除后这些本地历史不可恢复；已验证的天气原文继续保留在 R2。当前容量验收线为 `/` 总量 30,083,776,512 字节中的至少 20,055,851,008 可用字节。用户已报告执行至第 5 步；具体删除、重建和可用空间仍待 VM 复测。

## 1. 停止 KNYC，保持 KLGA

```bash
sudo systemctl stop nice-weather-terminal.service \
  nice-weather-knyc-r2.timer nice-weather-knyc-r2.service \
  nice-weather-knyc-backtest.service nice-weather-knyc-feed.service \
  nice-weather-knyc-paper@kalshi.service nice-weather-knyc-paper@poly_us.service \
  nice-weather-knyc-live@kalshi.service nice-weather-knyc-live@poly_us.service
sudo systemctl disable --now nice-weather-knyc-hrrr.service
systemctl is-active nice-weather-collector.service nice-weather-r2-sync.timer
pgrep -af 'prune_knyc_market_bodies.py' || true
```

前两项 KLGA 应为 `active`；最后一条不得出现仍在运行的清理进程。HRRR 不恢复。现役模型必需 HRRR 特征，停用后预测 `unavailable`，策略维持 no-trade。

## 2. 先完成天气 R2 GET 核验

```bash
sudo systemctl reset-failed nice-weather-knyc-r2.service
sudo systemctl start nice-weather-knyc-r2.service
systemctl show nice-weather-knyc-r2.service -p Result -p ExecMainStatus
sudo python3 - <<'PY'
import sqlite3
p = "/var/lib/nice-weather-knyc/feed.sqlite3"
c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
sources = ("metar", "nws_observations", "cli_index", "cli", "hrrr_index")
q = ",".join("?" for _ in sources)
row = c.execute(
    "SELECT b.hash FROM capture_bodies b WHERE length(b.body)>0 "
    "AND EXISTS(SELECT 1 FROM captures x WHERE x.hash=b.hash "
    f"AND x.source IN ({q})) LIMIT 1", sources
).fetchone()
c.close()
if row:
    raise SystemExit(f"STOP: weather body remains: {row[0]}")
print("weather bodies cleared after R2 verification")
PY
```

只有 `Result=success`、`ExecMainStatus=0`、天气检查无剩余 body 才可进入下一步。任何报错均保留旧库并排查；不能因文件太大而跳过天气核验。R2 任务可能持续较久，等待其完成。

## 3. 人工审核精确文件后删除

`AGENTS.md` 要求批量删除文件先列精确目标并取得人工确认。此处目标只有：

```text
/var/lib/nice-weather-knyc/feed.sqlite3
/var/lib/nice-weather-knyc/feed.sqlite3-wal
/var/lib/nice-weather-knyc/feed.sqlite3-shm
```

用户逐项核对后亲自执行：

```bash
sudo stat -c '%s %n' -- /var/lib/nice-weather-knyc/feed.sqlite3
sudo rm -f -- /var/lib/nice-weather-knyc/feed.sqlite3 \
  /var/lib/nice-weather-knyc/feed.sqlite3-wal \
  /var/lib/nice-weather-knyc/feed.sqlite3-shm
df -B1 /
```

不得使用通配符、目录递归删除或删除 `results.sqlite3`。

## 4. 重建新库，阻断旧历史回测

```bash
sudo -u nice-weather python3 - <<'PY'
import sqlite3, time
from pathlib import Path
p = Path("/var/lib/nice-weather-knyc/feed.sqlite3")
if p.exists():
    raise SystemExit("STOP: feed database already exists; do not overwrite it")
c = sqlite3.connect(p)
c.execute("PRAGMA auto_vacuum=INCREMENTAL")
c.execute("VACUUM")
c.execute(
    "CREATE TABLE market_history_progress ("
    "id INTEGER PRIMARY KEY CHECK(id=1), "
    "seq INTEGER NOT NULL, evicted_before REAL NOT NULL)"
)
c.execute("INSERT INTO market_history_progress VALUES (1,0,?)", (time.time(),))
c.commit()
print("auto_vacuum =", c.execute("PRAGMA auto_vacuum").fetchone()[0])
print("historical replay boundary =", c.execute(
    "SELECT evicted_before FROM market_history_progress WHERE id=1"
).fetchone()[0])
c.close()
PY
```

`auto_vacuum` 必须为 `2`。边界使用真实重建时点；新版本会拒绝此前的收到时点回测。

## 5. 保留 Paper 账户事实，仅重置两个 feed 游标

```bash
sudo -u nice-weather python3 - <<'PY'
import hashlib, json, sqlite3
p = "/var/lib/nice-weather-knyc/results.sqlite3"
accounts = ("sandbox-kalshi-knyc", "sandbox-poly_us-knyc")
encode = lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)
c = sqlite3.connect(p)
c.row_factory = sqlite3.Row
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA synchronous=FULL")
with c:
    for account in accounts:
        run = c.execute(
            "SELECT run_id, config FROM runs WHERE account=? AND mode='sandbox'",
            (account,)
        ).fetchone()
        if run is None:
            raise RuntimeError(f"Missing Paper run: {account}")
        saved = c.execute(
            "SELECT body FROM paper_state WHERE run_id=?", (run["run_id"],)
        ).fetchone()
        if saved is None:
            raise RuntimeError(f"Missing Paper checkpoint: {account}")
        config = json.loads(run["config"])
        state = json.loads(saved["body"])
        config["feed_cursor"] = 0
        state["config"]["feed_cursor"] = 0
        checksum = hashlib.sha256(encode(state).encode()).hexdigest()
        c.execute("UPDATE runs SET config=? WHERE run_id=?",
                  (encode(config), run["run_id"]))
        c.execute("UPDATE paper_state SET body=?, checksum=? WHERE run_id=?",
                  (encode(state), checksum, run["run_id"]))
        print(account, "feed_cursor=0")
c.close()
PY
```

若任一检查点缺失，事务回滚；保持两 Paper 停止，不要重置或删除账户事实。

## 6. 恢复与容量验收

HRRR 不在恢复清单内：其 index 原文经 R2 核验后会自动清空，但 HRRR 解析预报事件、图表投影及采集元数据没有自动回收；重新启动仍会造成 feed 增长。已部署版本对天气原文自动清空，市场规范化行情 24 小时回收须待本轮 PR 发布后生效。

用户随后要求最终恢复 HRRR 实时功能且不积累历史。现役代码尚不满足，不能在执行本节命令时额外启动 HRRR。当前工作分支拟不保存 HRRR 索引/字段原文，仅把当前预报作为运行输入，并在两 Paper 消费后回收超过 24 小时的 HRRR 事件和图表投影；待用户确认该短期缓存口径、PR 发布及线上核验后，另行启用 HRRR 和回测。

```bash
sudo systemctl start nice-weather-knyc-feed.service
sudo systemctl start nice-weather-knyc-paper@kalshi.service nice-weather-knyc-paper@poly_us.service
sudo systemctl start nice-weather-knyc-live@kalshi.service nice-weather-knyc-live@poly_us.service
sudo systemctl start nice-weather-terminal.service nice-weather-knyc-r2.timer
systemctl is-active nice-weather-collector.service nice-weather-r2-sync.timer \
  nice-weather-knyc-feed.service nice-weather-knyc-paper@kalshi.service \
  nice-weather-knyc-paper@poly_us.service nice-weather-terminal.service
systemctl is-enabled nice-weather-knyc-hrrr.service
df -B1 /
```

`nice-weather-knyc-backtest.service` 暂不启动：现役 worker 没有旧历史删除门禁，本轮 PR 合并并部署门禁后再启用。用户要求全部清理和文档完成后只提交一次 PR。容量验收还须考虑新库和运行期 WAL；删除 16,550,109,184 字节旧主库且其余不变时，按 15:22 UTC 可用量推算仍少 951,721,984 字节。额外文件清理先列精确目标供用户人工审核，不自行执行。

当前可供额外审核的是以下 5 个 Snap 下载缓存文件，共 1,202,532,352 字节。只列清单，尚未获批删除，不能把此清单解释为执行命令：

```text
365617152 /var/lib/snapd/cache/6aabee6a36f9b5a7db63dfb509208918aabc11f0170566bcf66ef4898d23a6de1407846bc8b9c815112359d8babd0077b
365092864 /var/lib/snapd/cache/b193115ef8fde4e63f978461a311f78729edce79819551d94afc9e11e082e307106a1e3303f7d463799b7b624e6
349077504 /var/lib/snapd/cache/a908267174d771d65cbf89c6e513427f4f1ae4c838410e1819b392cba14f3f8c803c1702d173d362ee8f5d13f978cf9ce
70033408 /var/lib/snapd/cache/9f84f391c5ace85b755a7530a330f7b52aff891178cddf10bc4f673f261ea9454629dda993391cbede77963e689789c
52711424 /var/lib/snapd/cache/dc3bf15d908c01fd2577731b7f6ff2e2d782221011f2f3521cadff43e77c045a75294dc9a5d4489a14bb10224d86db44
```

Snap 未来如需这些包，会再次下载；仅在旧库实际删除、服务恢复后的容量复测确认仍有缺口，且用户按 AGENTS.md 人工审核批准后，才处理这些文件。
