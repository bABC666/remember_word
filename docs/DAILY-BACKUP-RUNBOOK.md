# 拾词每日备份操作手册

交付状态：**本地功能已验收，尚未在服务器安装或启用**。
本轮只在 Windows 本地用合成数据库开发和验收，未读取或修改真实数据库，未操作生产服务、远端服务器或 Git 远端。

## 实现与边界

复用 `backend/app/services/backup.py` 的 SQLite online backup 能力，提供
`python tools/backup.py` 和 `python -m app.cli backup` 两个等价入口。
前者只需要 Python 3.11+ 标准库及系统时区数据；后者使用应用现有后端环境。
备份命令不加载应用默认数据库配置、不创建业务表、不执行迁移。
现有应用启动备份和设置页手动备份保持原入口与命名，复用加固后的快照函数，未新增进程内定时器。

源库以 `mode=ro` 打开，通过 `Connection.backup()` 读取一致的已提交快照，包含 WAL 中已提交数据；
不直接复制活库主文件、不对活库使用 `immutable=1`，也不强制 checkpoint。
SQLite 可能为只读 WAL 连接维护 SHM，服务器运行用户仍需相应目录权限。
目标库关闭连接并转换为独立 DELETE journal 文件后，执行完整 `integrity_check` 和
`foreign_key_check`，计算 SHA-256、文件大小、Alembic revision 列表（无版本表则为空列表）。
这些结构检查不等同于历史业务数据完整性证明；如有可信业务 baseline，可额外使用原有
`tools/verify_backup.py` 比对恢复副本，本轮未对真实库运行该工具。

每日文件为 `shici-daily-YYYY-MM-DD.db`，旁边的同名 `.json` 记录管理标记、类型、
源路径的 SHA-256 标识、带时区的开始/完成时间、revision、大小、哈希及校验结果。
临时文件先校验，再发布 DB 和 JSON；只有两者完整且持久化成功才进入保留策略。
当天再次运行会重新验证已有副本与元数据，成功后返回 `already_exists`，不覆盖、不清理。
需再次采样当天最新状态时，用 `--kind manual` 创建唯一命名的手动备份。

默认只保留最近 **30 份有效的本功能每日备份**，不是最近 30 个日历日。
清理前重新核对精确命名、管理标记、daily 类型、同一源标识、文件名、大小、哈希及 SQLite 校验。
按文件日期排序；系统时钟回拨时当前刚生成的副本也保留。
手动备份、启动时旧命名备份、其他来源、缺少/损坏元数据、符号链接和损坏副本全部不删，
因此目录内总文件数可能超过 30。无效文件不计入有效份数，需人工检查。
源路径变更会产生新标识，旧源备份不自动清理；建议一个数据库使用一个独立备份目录。

在复制、校验或发布失败时不执行清理。清理本身出现权限/I/O 错误时返回失败，已成功发布的新备份保留；
已完成的旧副本删除无法回滚。DB 与 JSON 不能组成单次文件系统原子提交，极端中断可能留下孤立 DB；
再次执行会拒绝覆盖，需先把孤立文件移入**独立人工检查目录**后重试。
正常异常会清理本次临时文件；强制终止可能留下 `.partial*`，不参与保留策略。

同一备份目录内通过操作系统文件锁保护整个任务，CLI、启动备份及手动备份共享锁。
并发 CLI 调用返回 3；原有应用启动/设置页入口最多等待 30 秒，避免短暂冲突立即导致启动失败，
超过等待上限仍按原有“备份失败则拒绝启动”的策略报告失败。
锁随进程退出释放，不要删除 `.backup.lock` 文件。
使用本地文件系统，跨主机/NFS/SMB 锁语义不在本次保证范围。
快照复制有 300 秒上限；systemd 对整个任务（含校验）设 15 分钟上限。

## 配置

| 项目 | 配置位置 | 示例 |
|---|---|---|
| Python/代码路径 | service 的 `ExecStart` / `WorkingDirectory` | `/usr/bin/python3` / `/opt/shici` |
| 运行身份 | service 的 `User` / `Group` | `shici`，与应用数据权限匹配的非 root 用户 |
| 源数据库 | `VOCAB_DATABASE_PATH` 或 `--database` | `/var/lib/shici/vocab.db` |
| 备份目录 | `VOCAB_BACKUPS_DIR` 或 `--backups-dir` | `/var/backups/shici` |
| 保留份数 | `VOCAB_BACKUP_KEEP` 或 `--keep` | `30`，正整数 |
| 日期时区 | `VOCAB_BACKUP_TIMEZONE` 或 `--timezone` | `Asia/Shanghai`；命令缺省为 UTC |
| 调度时区 | timer 的 `OnCalendar` | `*-*-* 03:30:00 Asia/Shanghai` |
| 写入白名单 | service 的 `ReadWritePaths` | 源库所在目录及备份目录 |

参数优先于环境变量。两个路径必须显式提供，不会回退到负责人本机路径。
配置文件采用 systemd EnvironmentFile 语法，不写 `export`，不依赖 `$VAR` 展开。
配置时区要同时修改 timer 的字面值与环境文件；仅修改 `TZ` 或 `VOCAB_BACKUP_TIMEZONE` 不会改变调度。
Linux 需安装系统 tzdata，Windows 非 UTC 验证另需可用的 IANA 时区数据库；当前 Windows 测试使用 UTC。
修改路径也要同步调整 `ReadWritePaths`。模板的 `ProtectHome=true` 不支持把代码/数据库放在 home 目录。

## 服务器安装与启用（尚未执行）

以下 `/opt/shici`、`shici` 等均是示例，先改为目标主机的实际布局。
本次没有提供服务器地址或凭据，也没有登录或安装。

1. 将本次审阅后的代码包放到 `/opt/shici`，保留目录关系。既有服务器项目无需重新部署 Web 服务。
   验证 Python ≥3.11、sqlite3 模块、tzdata 可用，代码由部署管理员控制、备份用户只读。
2. 确认 `shici` 用户/组存在并与应用数据权限匹配。若尚无该用户，由服务器管理员创建专用用户。
   不盲目递归修改现有数据的属主。源库必须存在且非空，目录允许所需 WAL/SHM 操作。
3. 安装并编辑模板（以下命令仅供服务器管理员执行）：

```bash
sudo install -d -m 0750 /etc/shici
sudo install -d -o shici -g shici -m 0700 /var/backups/shici
sudo install -m 0640 -o root -g shici /opt/shici/deployment/backup/backup.env.example /etc/shici/backup.env
sudo install -m 0644 /opt/shici/deployment/backup/shici-backup.service /etc/systemd/system/shici-backup.service
sudo install -m 0644 /opt/shici/deployment/backup/shici-backup.timer /etc/systemd/system/shici-backup.timer
sudoedit /etc/shici/backup.env /etc/systemd/system/shici-backup.service /etc/systemd/system/shici-backup.timer
sudo systemd-analyze verify /etc/systemd/system/shici-backup.service /etc/systemd/system/shici-backup.timer
systemd-analyze calendar '*-*-* 03:30:00 Asia/Shanghai'
sudo systemctl daemon-reload
```

4. **先手动执行并恢复演练，再启用 timer**：

```bash
sudo systemctl start shici-backup.service
sudo systemctl show shici-backup.service -p Result -p ExecMainStatus
sudo journalctl -u shici-backup.service -n 50 --no-pager
# 校验本次 DB/JSON 并完成下节的独立目录恢复后：
sudo systemctl enable --now shici-backup.timer
systemctl list-timers --all shici-backup.timer
```

`Persistent=true` 会在恢复激活时为错过的日程补跑一次，不能补造停机期间每天的历史快照。
补跑以实际执行日期命名。漏执行补跑也不等于失败重试：磁盘满、锁冲突等失败修复后应手动重试；
本模板不自动设置重试循环。必须在目标 systemd 版本上核对下一次触发时间和实际行为。

## 日常操作与日志

```bash
# 立即执行每日任务；同日已有有效副本则不覆盖
sudo systemctl start shici-backup.service
systemctl status shici-backup.timer shici-backup.service --no-pager
systemctl list-timers --all shici-backup.timer
sudo journalctl -u shici-backup.service --since today --no-pager

# 另存一份永久保留的手动副本；使用当前实际路径
sudo -u shici /usr/bin/python3 /opt/shici/tools/backup.py \
  --database /var/lib/shici/vocab.db --backups-dir /var/backups/shici \
  --kind manual --timezone Asia/Shanghai

# 停用未来调度；不会删除任何备份
sudo systemctl disable --now shici-backup.timer
# 如确需中断正在运行的备份，再执行；可能留下待检查临时文件
sudo systemctl stop shici-backup.service
```

oneshot 成功执行后显示 inactive 是正常状态，检查 `Result=success`、`ExecMainStatus=0` 和日志。
退出码：0 成功或已存在有效副本；1 备份/校验/发布/保留失败；2 必填配置缺失或整数解析失败；3 锁冲突。
日志同时输出 stderr 和备份目录 `backup-events.jsonl`，包含时间、阶段、状态、异常类型，
不输出源路径、SQL 数据、配置值或异常原文。目录不可写/磁盘满时可能无法保存文件日志，
此时依靠 journal；服务器需确认 journal 持久化、空间配额与失败通知。
日志长期增长需由服务器日志轮转管理，不能把轮转规则误用于 DB/JSON。
文件完整性校验成功不代表异机副本成功；两者应分别告警。

## 恢复到独立目录

不要覆盖活库，不要把恢复副本指回现有应用实例，也不要自动迁移恢复文件。
在独立终端使用下面的检查脚本，替换 `BACKUP` 为实际已完成的备份；
`RESTORE` 必须是一个**尚不存在**的独立目录。

```bash
cd /opt/shici
BACKUP=/var/backups/shici/shici-daily-YYYY-MM-DD.db
RESTORE=/var/tmp/shici-restore-unique
sudo -u shici env PYTHONPATH=/opt/shici/backend /usr/bin/python3 - "$BACKUP" "$RESTORE" <<'PY'
import json, shutil, sys
from pathlib import Path
from app.services.backup import inspect_backup

source, directory = map(Path, sys.argv[1:])
metadata = json.loads(source.with_suffix('.json').read_text(encoding='utf-8'))
facts = inspect_backup(source)
assert metadata['filename'] == source.name
assert all(metadata.get(key) == value for key, value in facts.items())
directory.mkdir(mode=0o700, parents=False, exist_ok=False)
restored = directory / 'vocab.db'
shutil.copyfile(source, restored)
assert inspect_backup(restored) == facts
print('Independent restore verified; revision:', facts['schema_revision'])
PY
```

此步骤对独立文件重新核对 SHA、大小、完整性、外键及 revision，副本不需要原 WAL/SHM。
需要应用级恢复验收时，另设 `VOCAB_DATA_DIR`、`VOCAB_DATABASE_PATH` 指向恢复目录，
核对 revision 与代码匹配后使用独立端口/独立服务，并进行词库、用户及学习历史抽样。
正式切换、停服、覆盖或迁移生产库须另行安排，不属于本次交付或上面脚本。

## 接入异机副本（未配置）

当前只有本机文件备份，**没有实际异机存储目标，也未配置上传或异机副本**。
同盘故障、整机丢失不能靠同机副本恢复。
确定目标主机或对象存储后，为备份服务配置独立最小权限身份，凭据交给服务器密钥管理，
不要写入仓库、unit、命令行或日志。选择经审阅的 SSH/对象存储客户端和独立复制任务。
只上传完成校验的 DB/JSON 对，排除 `.partial*`、锁和事件日志；
在同一个 `backup_lock` 下读取或复制到独立待传目录，再上传，避免保留策略与传输竞争。
远端收到后重新计算 SHA、校验 SQLite，并定期下载到独立目录做恢复演练。
远端保留/加密/不可变策略需单独设置；不要用带删除镜像的同步把本地清理传播为唯一异机副本删除。
复制失败要独立记录和告警；本地备份成功不能据此宣称灾备已经完成。

## 服务器待执行与待验收清单

- [ ] 审阅交付 commit/文件，确定主机、非 root 用户、绝对路径、容量、时区及本地文件系统。
- [ ] 确认 Python/sqlite3/tzdata、源库及 WAL/SHM 权限，配置 `ReadWritePaths` 和 0700 备份目录。
- [ ] 安装模板但暂不启用，运行 `systemd-analyze verify` 和 `calendar`，核对下一次为指定时区 03:30。
- [ ] 先在服务器合成库验证 POSIX 锁、进程终止后解锁、写入权限、失败日志、超时与保留边界。
- [ ] 手动备份实际目标库并验证 DB/JSON，再恢复到独立目录做结构与业务抽样。
- [ ] 确认日志持久化、轮转、磁盘容量监控和失败通知负责人。
- [ ] 通过后启用 timer，观察至少一次实际 03:30 触发。
- [ ] 在隔离任务验证停机/停用后重新激活的漏执行补跑，不以修改生产系统时间测试。
- [ ] 验证停用 timer 后没有后续调度，并按需要恢复启用。
- [ ] 确定异机目标后另行配置与恢复验收；当前此项未完成。

Linux systemd 调度、sandbox 权限、POSIX 锁及目录 fsync 分支未在当前 Windows 环境实测。
实现依据：[Python sqlite3 backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)、
[systemd timer 官方文档源码](https://github.com/systemd/systemd/blob/main/man/systemd.timer.xml)。
