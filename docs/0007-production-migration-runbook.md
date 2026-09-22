# 0006 → 0007 生产迁移 Runbook

**范围**：`data/vocab.db` 从 `0006_article_exposure_entry` 迁移到 `0007_bridge_foreign_keys`。
**代码基线**：分支 `feat/v1.2-phase1-safe`，包含 0007 及其证据工具的 commit。
**本文只描述迁移的发布与验证，不描述产品功能。**

| 事实 | 值 |
| --- | --- |
| 数据库 | `data/vocab.db`（WAL 模式） |
| 迁移前 revision | `0006_article_exposure_entry` |
| 迁移后 revision | `0007_bridge_foreign_keys`（= 代码 head） |
| 迁移前 sha256 | `95d09866c5b2d830156338748b123c40f9c84db13fead5ca6b61aba9f576b37a` |
| 迁移前字节数 | 569344 |
| 表 | 18（17 张业务表 + `alembic_version`） |
| 外键 | 17 → 26（0007 新增 9 条，全部 `ON DELETE SET NULL`） |
| 0007 的作用 | 重建 7 张表以补上 ORM 已声明、物理上缺失的 9 条外键；**不改列、不改类型、不动索引、不移动数据** |

所有命令的工作目录均为项目根 `D:\背单词web`。

---

## 0. 硬性规则

1. **只允许** `upgrade 0007_bridge_foreign_keys`（显式 revision）。
2. **禁止** `upgrade head`。禁止任何 downgrade。
3. **禁止**在 production 上创建用户、修改密码、登录测试账号。
4. 迁移前**必须**有一份校验过的备份；没有备份不得开始。
5. Part A 与 Part B 必须分开执行、分开留证。
6. 每一步都留证据（命令 + 输出 + 时间戳），不要靠记忆。

### 0.1 两个必须知道的陷阱

**陷阱一：`scripts/start-vocab.ps1` 会自动迁移。**

`scripts/start-vocab.ps1:64` 执行 `alembic upgrade head`，`scripts/check.ps1` 也会调用。因此在发布窗口内**不得启动应用**（包括桌面快捷方式）：一次启动就等于一次没有备份、没有日志的隐式迁移。

放行方式：迁移并验证完成后，先确认 `alembic current` 已是 `0007_bridge_foreign_keys`，此时 `upgrade head` 退化为空操作，才允许启动。见 Step A5。

**陷阱二：失败后的残留临时表会让重跑失败。**

0007 逐表重建。若在中途失败，SQLite 会回滚全部重建（数据不丢），但第一个被重建的表的 `CREATE TABLE _alembic_tmp_<表名>` 是在事务开启前以 autocommit 执行的，**会残留一张 0 行的 `_alembic_tmp_word`**。此时直接重跑会报 `table _alembic_tmp_word already exists`。处理见 Step A4。

---

## Part A — Production migration verification

**只允许验证数据库本身。禁止创建用户、修改密码、登录测试账号。**

### Step A0 · 停机

```powershell
# 用真实端口占用反查进程；不要相信 data/server.pid
.\scripts\stop-vocab.ps1
```

该脚本同时检查 pid 文件、端口监听与本 checkout 的 python 进程，并且**只有在端口无监听且服务进程不存在时才输出 stopped**；否则输出 `FAILED` 并以非零码退出。若它输出 `FAILED`，停下来处理，不要继续。

随后证明数据库确实静止：

```powershell
# 端口必须无监听
Get-NetTCPConnection -LocalPort 8000 -State Listen   # 期望：无输出

# 连续 5 次取样：sha256 与 WAL 必须恒定，且 WAL 必须为 0 字节
1..5 | ForEach-Object {
  (Get-FileHash data\vocab.db -Algorithm SHA256).Hash
  (Get-Item data\vocab.db-wal).Length
  Start-Sleep 2
}
```

放行条件：sha256 始终为 `95D09866…B37A`，`vocab.db-wal` 始终为 `0`。

### Step A1 · 备份

```powershell
# WAL 必须为空；非空时的裸拷贝会静默丢失已提交页
(Get-Item data\vocab.db-wal).Length        # 必须为 0

New-Item -ItemType Directory -Force data\backups | Out-Null
Copy-Item data\vocab.db data\backups\pre-0007-production.db

Get-FileHash data\backups\pre-0007-production.db -Algorithm SHA256
(Get-Item data\backups\pre-0007-production.db).Length
```

记录四项并写进发布记录：

| 项目 | 期望值 |
| --- | --- |
| sha256 | `95d09866c5b2d830156338748b123c40f9c84db13fead5ca6b61aba9f576b37a` |
| size | 569344 |
| revision | `0006_article_exposure_entry` |
| integrity_check | `ok` |

> 复核提示：仓内 `data/backups/` 与 `data/recovery/` 下**没有任何现存文件的 sha256 等于上面的值**，所以这一份备份必须现做，不能复用旧快照。

### Step A2 · 迁移

```powershell
cd D:\背单词web\backend
.\.venv\Scripts\python.exe -m alembic -c alembic.ini current      # 必须输出 0006
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade 0007_bridge_foreign_keys 2>&1 |
    Tee-Object ..\data\recovery\0007-production-migration.log
```

- 省略 `-x db_url` 时，alembic 使用 `settings.database_url`，即生产库 `data/vocab.db`。这正是本步骤的意图。
- 只允许 `upgrade 0007_bridge_foreign_keys`。**不要写 `head`。**
- 完整保存 stdout+stderr 到 `data/recovery/0007-production-migration.log`。

### Step A3 · 迁移后校验（只读）

把下面的脚本保存为 `data/recovery/verify_0007_production.py`，然后运行。它对生产库只读打开，不做任何写入。

```powershell
backend\.venv\Scripts\python.exe data\recovery\verify_0007_production.py
```

```python
# data/recovery/verify_0007_production.py   — 只读
import json, sqlite3, hashlib
from pathlib import Path

db = sqlite3.connect("file:data/vocab.db?mode=ro", uri=True)
recorded = json.loads(Path("data/recovery/rehearsal-0006-to-0007.json").read_text("utf-8"))

def fingerprint(conn, table):
    cols = [r[1] for r in conn.execute(f'pragma table_info("{table}")')]
    order = "key" if "key" in cols else ("id" if "id" in cols else "rowid")
    digest, count = hashlib.sha256(), 0
    for row in conn.execute(f'select {", ".join(cols)} from "{table}" order by {order}'):
        digest.update("\x1f".join("" if v is None else repr(v) for v in row).encode())
        digest.update(b"\x1e"); count += 1
    return [count, digest.hexdigest()]

print("revision          :", [r[0] for r in db.execute("select version_num from alembic_version")])
print("integrity_check   :", db.execute("pragma integrity_check").fetchone()[0])
print("foreign_key_check :", len(db.execute("pragma foreign_key_check").fetchall()))
tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' order by name")]
print("tables            :", len(tables))
print("foreign keys      :", sum(len(db.execute(f'pragma foreign_key_list("{t}")').fetchall()) for t in tables))
print("tmp leftovers     :", [t for t in tables if t.startswith("_alembic_tmp")] or "none")
bad = [t for t in tables if t != "alembic_version"
       and fingerprint(db, t) != recorded["phase1"]["before_fingerprints"][t]]
print("content drift     :", bad or "none")
```

必须全部满足：

| 检查 | 期望 |
| --- | --- |
| `revision` | `['0007_bridge_foreign_keys']` |
| `integrity_check` | `ok` |
| `foreign_key_check` | `0` |
| `tables` | `18` |
| `foreign keys` | `26` |
| `tmp leftovers` | `none` |
| `content drift` | `none` |

`content drift: none` 表示 17 张业务表逐行内容与迁移前完全一致。被重点核对的表包括
`article`、`review_event`、`word`、`lexicon_entry`、`user_word_state`、`user`，以及
review 历史（`review_event` 10 行、`article_word_exposure` 16 行、`user_word_state` 19 行、`word` 19 行）。

> 注意：本库**没有 `translation` 表**。译文是 `article` 上的列，随 `article` 一起核对。

只要 `tmp leftovers` 不是 `none`，或 `content drift` 不是 `none`，**停止**并进入 Step A4。

### Step A4 · 失败与恢复（仅在 A2/A3 未通过时执行）

0007 的 9 次重建位于同一个隐式事务内，异常会整体回滚；故障注入演练确认：失败后 revision 仍是 `0006`，所有表行数原样，`integrity_check = ok`，`foreign_key_check = 0`。唯一副作用是残留 `_alembic_tmp_word`。

**恢复路径 B（首选，可证明恢复到位）**

```powershell
# 删除损坏文件及其 sidecar，然后还原 Step A1 的备份
Remove-Item data\vocab.db, data\vocab.db-wal, data\vocab.db-shm -ErrorAction SilentlyContinue
Copy-Item data\backups\pre-0007-production.db data\vocab.db
(Get-FileHash data\vocab.db -Algorithm SHA256).Hash    # 必须等于 95D09866…B37A
```

**恢复路径 A（次选，无需还原）**

残留临时表必然是 0 行，可安全删除。把下面两行存为 `data/recovery/drop_tmp_tables.py` 后运行：

```python
# data/recovery/drop_tmp_tables.py — 只删除 0007 失败时残留的临时表
import sqlite3

connection = sqlite3.connect("data/vocab.db")
leftovers = [
    row[0]
    for row in connection.execute(
        "select name from sqlite_master where type='table' and name like '_alembic_tmp%'"
    )
]
for name in leftovers:
    count = connection.execute(f'select count(*) from "{name}"').fetchone()[0]
    assert count == 0, f"refusing to drop {name}: it holds {count} rows"
    connection.execute(f'DROP TABLE "{name}"')
    print("dropped", name)
connection.commit()
connection.close()
```

```powershell
backend\.venv\Scripts\python.exe data\recovery\drop_tmp_tables.py
cd backend
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade 0007_bridge_foreign_keys
```

恢复后必须重新执行 Step A3 的全部检查。

### Step A5 · 解除启动封锁（Part A 与 Part B 都完成后才可执行）

```powershell
cd D:\背单词web\backend
.\.venv\Scripts\python.exe -m alembic -c alembic.ini current
# 必须输出 0007_bridge_foreign_keys，此时 start-vocab.ps1 的 `upgrade head` 是空操作
```

应用本身是失败关闭的：数据库 revision 不等于代码 head 时会拒绝启动。因此 Step A5 之前的任何启动尝试都会失败——这是设计如此，不是故障。

---

## Part B — Post migration application verification

**必须在「迁移后数据库的副本」上执行。禁止直接修改 production 用户表。**

```
production backup (已迁移, 0007)
        │  Copy-Item
        ▼
verification clone  data/staging/post-0007-verification.db
        │  在副本上创建测试用户
        ▼
staging_two_user_check  ──►  报告
```

副本必须放在 `data/staging/` 之下：`tools/staging_accounts.py` 会拒绝任何路径中不含 `staging` 目录名的数据库，这是防止误写生产的内建护栏。

### Step B0 · 前提

Part A 的 Step A3 全部通过。应用处于停止状态（沿用 Step A0 的证明）。

### Step B1 · 从迁移后的 production 制作副本

```powershell
# 仍然必须确认 WAL 为空
(Get-Item data\vocab.db-wal).Length          # 必须为 0
New-Item -ItemType Directory -Force data\staging | Out-Null
Copy-Item data\vocab.db data\staging\post-0007-verification.db
Get-FileHash data\staging\post-0007-verification.db -Algorithm SHA256
```

副本是生产数据的完整拷贝，因此**同样要当作敏感数据**：验证结束后按 Step B5 删除，不要提交进版本库（`data/` 已被 `.gitignore` 覆盖）。

### Step B2 · 在副本上创建测试用户

```powershell
$env:VOCAB_DATA_DIR     = "$PWD\data\staging"
$env:VOCAB_REAL_DATA_DIR = "$PWD\data\staging"
$env:VOCAB_DATABASE_PATH = "$PWD\data\staging\post-0007-verification.db"

"staging-admin-secret" | backend\.venv\Scripts\python.exe -m tools.staging_accounts set-password admin
"staging-userb-secret" | backend\.venv\Scripts\python.exe -m tools.staging_accounts create-user userb --role user
```

为什么要写这一步：production 的 `user` 表只有 1 行，且 `password_hash = '!'`（不可用口令，任何输入都无法通过 `verify_password`），同时不存在 userB。**在 production 上执行登录测试必然要求写入用户数据**，这正是 Part B 必须在副本上做的原因。

`staging_two_user_check` 自身也会幂等地重复这两步，显式执行一次是为了让"账号建在副本里"可见。

### Step B3 · 运行双用户验收

```powershell
backend\.venv\Scripts\python.exe tools\staging_two_user_check.py `
    --database data\staging\post-0007-verification.db `
    --report   data\recovery\post-0007-two-user-report.json `
    --port     8077
```

期望：`STAGING TWO-USER CHECK VERIFIED`，退出码 0，报告 `verified: true`、`failures: []`。检查项包括 admin 登录、19 个词、保留的文章与 review 历史、userb 登录后被隔离、userb 无法使用 admin 端点（403）。

### Step B4 · 双用户隔离证明（可选，推荐）

```powershell
backend\.venv\Scripts\python.exe tools\staging_isolation_check.py `
    --database data\staging\post-0007-verification.db `
    --report   data\recovery\post-0007-isolation-report.json
```

`--database` 模式明确"按原样使用，不克隆、不迁移"（日志会打印 `used as-is (no clone, no migration)`），因此它只验收副本，不会另跑一次迁移。

### Step B5 · 清理

```powershell
Remove-Item data\staging\post-0007-verification.db*
```

副本包含生产数据与测试口令，验证完成后应删除。

---

## 附：迁移前的预演（可选，但推荐）

在动生产之前，可以先在克隆上把整套流程跑一遍，并留下可复现证据：

```powershell
backend\.venv\Scripts\python.exe tools\rehearsal_migration_0007.py `
    --json data\recovery\rehearsal-0006-to-0007.json
```

该工具：把 `data/vocab.db` 复制为 `data/staging/migration-rehearsal-0006.db` 并证明字节一致 → 只对该克隆执行**显式** `upgrade 0007_bridge_foreign_keys` → 比对 revision、每张表的行数与内容哈希、`integrity_check`、`foreign_key_check` → 比对 ORM 声明与物理外键（26/26）→ 最后证明 `data/vocab.db` 字节未变。生产库始终只读。

它产出的 `phase1.before_fingerprints` 就是 Part A Step A3 用来判断 `content drift` 的基线，所以**先跑预演、再做生产**能让 A3 变成一次自动比对。

---

## 附：证据清单

| 文件 | 内容 | 产生于 |
| --- | --- | --- |
| `data/backups/pre-0007-production.db` | 迁移前完整备份 | Step A1 |
| `data/recovery/0007-production-migration.log` | alembic 完整输出 | Step A2 |
| `data/recovery/rehearsal-0006-to-0007.json` | 预演证据（含内容哈希基线） | 预演 / `rehearsal_migration_0007.py` |
| `data/recovery/post-0007-two-user-report.json` | 副本上的应用验收 | Step B3 |
| `data/recovery/post-0007-isolation-report.json` | 副本上的隔离证明 | Step B4 |

配套的逐步勾选表见 [`0007-production-migration-checklist.md`](./0007-production-migration-checklist.md)。
