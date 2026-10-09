# 拾词每日备份部署包

交付状态：**本地功能已验收，尚未在服务器安装或启用**。

本目录是部署模板包，配合本仓库 `tools/backup.py`、`backend/app/services/backup.py`、
`backend/app/testing_guards.py` 及 Python 包目录使用，不是已经部署的服务。
无需 OCR、API Key 或运行中的 Web 进程。完整步骤、恢复演练、异机副本接入和服务器执行清单见
[操作手册](../../docs/DAILY-BACKUP-RUNBOOK.md)，本地证据见
[测试记录](../../docs/DAILY-BACKUP-ACCEPTANCE-2026-10-06.md)。

| 文件 | 用途 |
|---|---|
| `shici-backup.service` | 非 root 用户执行一次备份；日志进 journal；15 分钟超时 |
| `shici-backup.timer` | Asia/Shanghai 每天 03:30；漏执行后补跑一次 |
| `backup.env.example` | 数据库、备份目录、份数和日期时区示例，无真实凭据 |

仅安装配置文件不会启用定时任务。服务器检查通过后才执行手册中的 `enable --now`。
