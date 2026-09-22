# 2026-09-22 容量中断与恢复记录（处理中）

03:31 UTC 经已有 SSH 会话核验，VM 仍运行 `23b540bc781b51905f623f67497a9a092274481c`；PR #60 七项 CI 已通过，合并 `1e8a76df9a42e1e9c6d4eece5ecc2e94dbfaf604` 尚未部署。系统盘只有约 13 MB 可用，KNYC feed / HRRR 反复启动；HRRR 明确报 `KNYC capture paused: less than 1 GiB disk headroom`。先停止五个 KNYC 写入服务，终端保持运行。未修改 VM 规格、磁盘、IAM 或网络配置。

旧主库约 14 GB，WAL 文件约 1,010 MB；KNYC 目录约 646 MB，轮转日志 `/var/log/syslog.1` 为 1,398,551,041 bytes。旧 KNYC 发布目录的 1.4 GB 主要是现役虚拟环境，不作为可删除旧产物。旧数据库备份仍保留。

初次 SQLite PASSIVE 检查点返回 `(0,151,63)`；TRUNCATE 返回 `(1,3771,63)`，说明读事务阻止完成。此时可用空间 10,080,256 bytes。短暂停止旧库的 collector、market-stream、runner、sandbox、backtest、dashboard 与 R2 timer/service 后，TRUNCATE 返回 `(0,0,0)`，可用空间恢复到 1,072,058,368 bytes。数据库页数 3,512,027、空闲页数 0；检查点只回写已提交 WAL，未删除历史记录。这些结果不等于全库完整性校验通过。

单个已轮转日志使用 gzip 无损压缩，压缩包约 62 MB；`gzip -t` 通过，压缩前与解压后的 SHA-256 输出一致，完整内容保存在 `/var/log/syslog.1.gz`。随后可用空间为 2,406,498,304 bytes。没有批量删除文件。旧库六个常驻应用服务与 R2 timer 已恢复 active；R2 service 的 failed 来自此次维护停止时的 SIGTERM，重新启动后 `Result=success`、`ExecMainStatus=0`，完成后正常 inactive。

03:43:33 UTC 五个 KNYC 后台服务均已恢复 active。03:45:25 UTC 已完成 `1e8a76d` 原位部署，72 个 manifest 文件全部匹配，健康版本一致，六个 KNYC 服务 active。部署后 Paper 两账户 running、各 100 USD、零成交。只读观察进程 PID 1729290 写入 `/home/hdharrison1206/knyc-post-readonly-20260922-0347.jsonl`，计划 31 个一分钟样本；首样本无错误，两账户待处理年龄均为零。此观察用于新版本写盘基线，不是完整市场日。

此次实际采集中断和维护停机必须计入数据缺口。最终版本完整纽约市场日尚未开始，不能使用此前 30 分钟基线或本次恢复作为完整市场日验收。旧库增长、持久化写放大和长期容量预算仍需解决。
