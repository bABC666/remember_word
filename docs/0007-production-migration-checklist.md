# 0007 生产迁移 · 执行清单

对象：`data/vocab.db`，`0006_article_exposure_entry` → `0007_bridge_foreign_keys`。
配套说明见 [`0007-production-migration-runbook.md`](./0007-production-migration-runbook.md)。

**用法**：从上到下逐项执行，每项在方框内打勾并填入实测值。任何一项不符预期就**停止**，不要跳过去做下一项。

发布信息

| 项目 | 填写 |
| --- | --- |
| 执行人 | |
| 开始时间 | |
| 分支 / commit | |
| 迁移前 sha256 | |

---

## Before

- [ ] **B1. 工作区干净** — 没有未提交的修改，避免"跑的不是被审的代码"

  ```powershell
  git status --porcelain      # 期望：无输出
  ```

  实测：`________________`

- [ ] **B2. 分支与 HEAD 正确**

  ```powershell
  git rev-parse --abbrev-ref HEAD   # 期望：feat/v1.2-phase1-safe
  git rev-parse --short HEAD
  ```

  实测分支：`________________` 实测 HEAD：`________________`

- [ ] **B3. 代码 head 是 0007，且仓库里有 0007 的迁移文件**

  ```powershell
  cd backend
  .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from app.db import code_head_revision; print(code_head_revision())"
  ```

  期望：`0007_bridge_foreign_keys`　实测：`________________`

- [ ] **B4. 服务已停止（用端口与进程证明，不要只看 pid 文件）**

  ```powershell
  cd D:\背单词web
  .\scripts\stop-vocab.ps1
  ```

  期望输出结尾为 `Shici server stopped.`，退出码 0。
  若输出 `FAILED` 或提示 `WARNING`，**停止**并处理。

  实测退出码：`________________`

- [ ] **B5. 端口确认无监听**

  ```powershell
  Get-NetTCPConnection -LocalPort 8000 -State Listen    # 期望：无输出
  ```

  实测：`________________`

- [ ] **B6. 没有后台任务在写数据库** — 连续取样，sha256 与 WAL 都必须恒定

  ```powershell
  1..5 | ForEach-Object {
    (Get-FileHash data\vocab.db -Algorithm SHA256).Hash
    (Get-Item data\vocab.db-wal).Length
    Start-Sleep 2
  }
  ```

  期望：5 次都是 `95D09866C5B2D830156338748B123C40F9C84DB13FEAD5CA6B61ABA9F576B37A`，WAL 都是 `0`

  实测：`________________`

- [ ] **B7. revision 是 0006**

  ```powershell
  cd backend
  .\.venv\Scripts\python.exe -m alembic -c alembic.ini current
  ```

  期望：`0006_article_exposure_entry`　实测：`________________`

- [ ] **B8. WAL 为空**（非空时裸拷贝会静默丢失已提交页）

  ```powershell
  cd D:\背单词web
  (Get-Item data\vocab.db-wal).Length    # 期望：0
  ```

  实测：`________________`

- [ ] **B9.（强制前置）Preflight 预演已完成** — 没有本项证据**禁止进入 During**

  ```powershell
  cd D:\背单词web
  backend\.venv\Scripts\python.exe tools\rehearsal_migration_0007.py --json data\recovery\rehearsal-0006-to-0007.json
  ```

  三项必须同时满足：

  | 检查 | 期望 | 实测 |
  | --- | --- | --- |
  | 结束行 | `REHEARSAL PASSED: 0006 -> 0007 on the clone, production untouched` | |
  | 退出码 | `0` | |
  | 证据文件 `verdict` / `failures` | `PASS` / `[]` | |

  复核证据文件（`source_sha256` 必须等于 B7 记录的迁移前 sha256）：

  ```powershell
  backend\.venv\Scripts\python.exe -c "import json,pathlib; d=json.loads(pathlib.Path('data/recovery/rehearsal-0006-to-0007.json').read_text(encoding='utf-8')); print(d['verdict'], d['failures']); print(d['phase1']['source_sha256'])"
  ```

  说明：After 段的内容哈希基线（A5 的 `content drift`）取自本文件，因此跳过预演会让 A5 无法执行。

- [ ] **B10. 备份已创建**

  ```powershell
  New-Item -ItemType Directory -Force data\backups | Out-Null
  Copy-Item data\vocab.db data\backups\pre-0007-production.db
  ```

- [ ] **B11. 备份 sha256 已记录且与迁移前一致**

  ```powershell
  Get-FileHash data\backups\pre-0007-production.db -Algorithm SHA256
  (Get-Item data\backups\pre-0007-production.db).Length
  ```

  期望 sha256：`95d09866c5b2d830156338748b123c40f9c84db13fead5ca6b61aba9f576b37a`
  期望大小：`569344`

  实测 sha256：`________________`　实测大小：`________________`

- [ ] **B12. 备份可用性已确认**（revision 与 integrity）

  ```powershell
  backend\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:data/backups/pre-0007-production.db?mode=ro',uri=True); print([r[0] for r in c.execute('select version_num from alembic_version')]); print(c.execute('pragma integrity_check').fetchone()[0])"
  ```

  期望：`['0006_article_exposure_entry']` 与 `ok`　实测：`________________`

- [ ] **B13. 发布窗口内不会启动应用** — 已确认 `scripts/start-vocab.ps1` 会执行 `alembic upgrade head`，因此在 Step A6 之前不得启动（含桌面快捷方式）。
  另已确认 `scripts/check.ps1` **不执行任何 alembic 迁移**（只读验证工具，不会 upgrade、不会迁移数据库）；但它的最后一步 `verify_backup.py` 目前会因基线冻结在 `0003_article_reading_tools` 而必然 FAIL，**不要**据它判断本次迁移成败。

---

## During

- [ ] **D1. 只执行这一条迁移命令**（显式 revision，不是 `head`）

  ```powershell
  cd D:\背单词web\backend
  .\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade 0007_bridge_foreign_keys 2>&1 |
      Tee-Object ..\data\recovery\0007-production-migration.log
  ```

  禁止：`upgrade head`、任何 `downgrade`、任何数据修复脚本。

  实测退出码：`________________`

- [ ] **D2. 迁移日志已保存**

  ```powershell
  Get-Content data\recovery\0007-production-migration.log
  ```

  期望含：`Running upgrade 0006_article_exposure_entry -> 0007_bridge_foreign_keys`

  实测：`________________`

- [ ] **D3. alembic 退出码为 0** — 非 0 时立即进入 Rollback，不要重跑

---

## After

- [ ] **A1. revision 已是 0007**

  ```powershell
  cd D:\背单词web\backend
  .\.venv\Scripts\python.exe -m alembic -c alembic.ini current
  ```

  期望：`0007_bridge_foreign_keys`　实测：`________________`

- [ ] **A2. integrity_check = ok**

- [ ] **A3. foreign_key_check = 0**

- [ ] **A4. 外键数量 = 26**（迁移前 17，新增 9）

- [ ] **A5. 内容哈希无漂移** — 17 张业务表逐行内容与迁移前一致

  把 runbook Step A4 的脚本存为 `data/recovery/verify_0007_production.py` 后运行：

  ```powershell
  cd D:\背单词web
  backend\.venv\Scripts\python.exe data\recovery\verify_0007_production.py
  ```

  期望全部满足：

  | 输出项 | 期望值 | 实测 |
  | --- | --- | --- |
  | `revision` | `['0007_bridge_foreign_keys']` | |
  | `integrity_check` | `ok` | |
  | `foreign_key_check` | `0` | |
  | `tables` | `18` | |
  | `foreign keys` | `26` | |
  | `tmp leftovers` | `none` | |
  | `content drift` | `none` | |

  > `tmp leftovers` 不是 `none` 说明迁移失败后残留了 `_alembic_tmp_*`，表数会变成 19；先按 Rollback/恢复处理，不要当成成功。
  > `content drift` 不是 `none` 会逐一列出漂移的表名，必须逐表确认原因。

- [ ] **A6. 重点表行数未变**（与迁移前一致）

  | 表 | 期望行数 |
  | --- | --- |
  | `article` | 1 |
  | `review_event` | 10 |
  | `word` | 19 |
  | `lexicon_entry` | 19 |
  | `user_word_state` | 19 |
  | `article_word_exposure` | 16 |
  | `user` | 1 |
  | `user_session` | 0 |

  实测：`________________`

- [ ] **A7. 生产库未被本步骤以外的操作写入** — `user` 表仍为 1 行、`user_session` 仍为 0 行（证明没有在 production 上创建账号或登录）

---

## Part B（在迁移后的副本上，禁止写 production）

- [ ] **P1. 应用服务仍处于停止状态**

- [ ] **P2. 从迁移后的 production 制作副本**（副本必须在 `data/staging/` 下）

  ```powershell
  (Get-Item data\vocab.db-wal).Length        # 期望：0
  New-Item -ItemType Directory -Force data\staging | Out-Null
  Copy-Item data\vocab.db data\staging\post-0007-verification.db
  ```

- [ ] **P3. 测试用户建在副本上**

  ```powershell
  $env:VOCAB_DATA_DIR      = "$PWD\data\staging"
  $env:VOCAB_REAL_DATA_DIR = "$PWD\data\staging"
  $env:VOCAB_DATABASE_PATH = "$PWD\data\staging\post-0007-verification.db"
  "staging-admin-secret" | backend\.venv\Scripts\python.exe -m tools.staging_accounts set-password admin
  "staging-userb-secret" | backend\.venv\Scripts\python.exe -m tools.staging_accounts create-user userb --role user
  ```

- [ ] **P4. 应用验收已运行**

  ```powershell
  backend\.venv\Scripts\python.exe tools\staging_two_user_check.py `
      --database data\staging\post-0007-verification.db `
      --report   data\recovery\post-0007-two-user-report.json `
      --port     8077
  ```

  期望：`STAGING TWO-USER CHECK VERIFIED`，报告 `verified: true`

  实测：`________________`

- [ ] **P5. 副本已清理**

  ```powershell
  Remove-Item data\staging\post-0007-verification.db*
  ```

- [ ] **P6. 启动封锁解除** — 此时 `alembic current` 已是 0007，`start-vocab.ps1` 的 `upgrade head` 为空操作，可以启动应用

  ```powershell
  cd backend; .\.venv\Scripts\python.exe -m alembic -c alembic.ini current
  ```

---

## Rollback

触发条件（任一）：`upgrade` 退出码非 0；After 的 A1–A5 任一项不符；迁移后应用无法启动。

- [ ] **R1. 停止一切写入**

  ```powershell
  .\scripts\stop-vocab.ps1
  ```

- [ ] **R2. 还原备份**（删除损坏文件及其 sidecar，再复制回来）

  ```powershell
  Remove-Item data\vocab.db, data\vocab.db-wal, data\vocab.db-shm -ErrorAction SilentlyContinue
  Copy-Item data\backups\pre-0007-production.db data\vocab.db
  (Get-FileHash data\vocab.db -Algorithm SHA256).Hash
  ```

  期望：`95D09866C5B2D830156338748B123C40F9C84DB13FEAD5CA6B61ABA9F576B37A`

  实测：`________________`

- [ ] **R3. 还原后复验**

  ```powershell
  backend\.venv\Scripts\python.exe data\recovery\verify_0007_production.py
  ```

  期望：`revision` 回到 `['0006_article_exposure_entry']`，`integrity_check` = `ok`，`content drift` = `none`

- [ ] **R4. 代码回退到与 0006 匹配的版本** — 当前代码 head 是 0007，数据库回到 0006 后应用会拒绝启动（失败关闭）。如需在 0006 上运行，必须切回匹配的代码，**不要**让应用自行 `upgrade head`。

- [ ] **R5. 记录失败原因与对应日志路径**

  `________________________________________________________________`

> 恢复路径 A（删除残留 `_alembic_tmp_*` 后重跑 `upgrade 0007_bridge_foreign_keys`）见 runbook Step A5。仅在确认残留表为 0 行、且 `content drift` 仍为 `none` 时使用。

---

## 签署

| 项目 | 填写 |
| --- | --- |
| Before 全部通过 | ☐ 是 |
| During 退出码 0 | ☐ 是 |
| After A1–A7 全部通过 | ☐ 是 |
| Part B P1–P6 全部通过 | ☐ 是 |
| 结论 | ☐ 发布成功　☐ 已回滚 |
| 结束时间 | |
| 迁移后 sha256 | |
| 迁移后人工签名 | |
