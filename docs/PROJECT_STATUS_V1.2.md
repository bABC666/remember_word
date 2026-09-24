# 拾词 · 项目阶段性交接文档（V1.2）

> **历史审计快照，非当前状态文档。** 正文的 `[实测]`、版本 `1.0.0`、ahead 19、端口/数据库数字与风险判断仅描述 **2026-09-22 19:33–19:40** 的原始只读审计窗口；为保持取证原貌，以下正文不逐段改写。当前事实以 `PROJECT_STATUS_CURRENT.md`、代码和 `docs/V1.2-RELEASE-CANDIDATE.md` 为准。
>
> **2026-09-24 当前索引**：项目版本文件为 `1.2.0`，含前端 lock 顶层与根包，仍只是**本机多用户版发布候选，未推送、合并、打 tag 或正式发布**；本次文档提交前分支 ahead 27，DoD 3 未满足，须推送核验后再改结论。S-1 两个管理员敏感端点已要求每次输入管理员自己的当前口令；G5、G8、T8 与 DoD 4 已有验收。G6 的 365 天四类事件策略、CLI、严格核验及 staging 清理/恢复演练已完成，生产库当前无到期候选，未来生产清理需针对具体计划与运行 ID 另行批准，**不是本次发布必做删除**。
>
> **后续规划索引**：Phase 2.9 的外部电子词库 → 系统公共 `Lexicon` 尚未实施，是 Phase 4 云部署前置，详见 `PROJECT_ARCHITECTURE.md` §3.6 与 `PROJECT_ROADMAP.md` §6.1.1；Phase 3 移动端/PWA、Phase 4 云部署均不属于本次 1.2.0 候选能力。本文件仍作为历史审计证据阅读，不应据它执行启动、迁移或生产清理。

> 文档版本：V1.2
> 生成日期：2026-09-22
> 审计窗口：2026-09-22 约 19:33 – 19:40（本地时间；该窗口内工作区发生过一次外部改动，已记入 §2.2 / §6.6）
> 生成方式：**只读审计**。本文档生成过程中未修改任何代码、未执行 migration、未写入任何数据库、未创建 commit。
> 用途：让下一位开发者（人或 AI）在不依赖历史对话的前提下，准确掌握项目当前真实状态并安全接手。

## 0. 本文档的证据口径（必读）

本文档所有结论分三级来源，请按标记判断可信度：

| 标记 | 含义 |
| --- | --- |
| **[实测]** | 由本次会话在**只读**模式下直接读取仓库 / 数据库 / Git 得到，可复现 |
| **[记录]** | 来自仓库内既有证据文件（`data/recovery/*`、`docs/*`），本次未重跑，但文件存在且内容可查 |
| **UNKNOWN** | 无法从仓库现状确认，**不做猜测**，需下一位开发者自行确认 |

一个重要提醒：`data/`（含数据库、备份、恢复证据）与 `handoff/`、`test-artifacts/`、`_INCIDENT-20260922/` 均在 `.gitignore` 中。因此仓库里的 Git 历史与 `data/recovery/` 下的证据是**两套独立存在的东西**，缺一不可。

**术语消歧（重要）**：`data/recovery/0007-release-record.md` 里的 “Phase 0–5” 指**迁移执行的步骤编号**；本文档的 “Phase 0 / 1 / 2” 指**项目开发阶段**。二者不同名同物，阅读时不要混淆。

---

# 1. 项目概览

## 1.1 项目用途

**拾词（Shici）** 是一个 **Windows 优先、浏览器访问的本地英语词汇学习应用**（本地单机部署，非 SaaS）。

核心能力：单词书图片导入 → PaddleOCR 真实识别 → DeepSeek 结构化 → 人工校对确认 → 复习调度 → AI 阅读生成 → 阅读后测试 → 点词解释 → 全文翻译 → 查词记录 → 生词入库 → 自动备份 → 重启持久化。

日常入口：桌面快捷方式 `拾词.lnk` 或根目录 `start-vocab.bat`（两者都指向同一条启动链）。

## 1.2 技术栈 [实测]

| 层 | 技术 | 版本（实测自 `backend/pyproject.toml`、`frontend/package.json`） |
| --- | --- | --- |
| 前端 | React | 19.1.1 |
| 前端 | React Router | 7.9.0 |
| 前端 | TanStack Query | 5.90.0 |
| 前端 | Vite / TypeScript | 7.1.7 / 5.9.2 |
| 前端 | Vitest / Testing Library / ESLint | 3.2.4 / 16.3.0 / 9.36.0 |
| 前端 | 图标 | lucide-react 0.545.0 |
| 后端 | FastAPI | `>=0.116,<1` |
| 后端 | Uvicorn | `>=0.35,<1` |
| 后端 | SQLAlchemy | `>=2.0.41,<3`（2.x 风格，`select()`） |
| 后端 | Alembic | `>=1.16,<2` |
| 后端 | Pydantic | `>=2.11,<3` |
| 后端 | 口令哈希 | `pwdlib[argon2]`（argon2id） |
| 后端 | 其他 | httpx、python-multipart、Pillow |
| OCR | PaddleOCR / PaddlePaddle | `>=3.1,<4`（Windows CPU，可选依赖） |
| AI | DeepSeek OpenAI-compatible API | 默认模型名 `deepseek-flash`，界面显示 `DeepSeek V4.1-Flash` |
| 数据库 | SQLite | 唯一事实来源；WAL 模式；`PRAGMA foreign_keys=ON`（`app/db.py:42`） |
| 运行形态 | 单进程 | FastAPI 在 `127.0.0.1:8000` 同时提供 API 与 `frontend/dist` 静态资源 |

## 1.3 当前版本定位

- **代码阶段：V1.2 Phase 1 完成**（多用户数据模型 + 全部私有业务 API 用户级隔离），代码位于分支 `feat/v1.2-phase1-safe`。
- **数据库阶段：生产库已从 `0006_article_exposure_entry` 迁移到 `0007_bridge_foreign_keys`**，并已通过发布后校验。
- **发布状态：未发布 / 未合并 / 未推送。** 分支领先远端 19 个 commit，无任何 tag；`pyproject.toml`、`package.json`、`FastAPI(version=...)` 三处版本号仍为 `1.0.0` [实测]。因此“V1.2”目前只是**分支与阶段名**，不是已发布的版本号。
- 生产实例当前**正在运行**（见 §2.4）。

---

# 2. 当前 Git 状态

## 2.1 分支与 HEAD [实测]

| 项 | 值 |
| --- | --- |
| 当前分支 | `feat/v1.2-phase1-safe` |
| HEAD commit | `f6d4da42165585dda3256c62ecc0d265d76d95ed`（短：`f6d4da4`） |
| HEAD 提交时间 | 2026-09-22 19:12:14 +0800 |
| HEAD 标题 | `fix(scripts): add UTF-8 BOM to install-shortcut.ps1` |
| 上游 | `origin/feat/v1.2-phase1-safe` @ `6ad8fce`（2026-09-22 14:53） |
| 领先 / 落后 | **ahead 19 / behind 0** |

## 2.2 工作区状态 [实测]

**本审计进行期间工作区发生了变化**，因此必须分成两次观测记录，不能合并成一句“干净”：

**观测 A（2026-09-22 约 19:33，审计开始时）：完全干净**

```
git status --porcelain  →  （空）
git status -sb          →  ## feat/v1.2-phase1-safe...origin/feat/v1.2-phase1-safe [ahead 19]
```

**观测 B（2026-09-22 19:39，文档定稿前复核时）：出现两个未提交改动**

```
 M backend/app/api/lexicons.py
?? backend/tests/test_lexicon_delete_guard.py
?? docs/PROJECT_STATUS_V1.2.md          ← 本文档本身
```

| 文件 | 状态 | mtime | 内容摘要 |
| --- | --- | --- | --- |
| `backend/app/api/lexicons.py` | 已修改未暂存 | 19:38:04 | 新增 `_count_learning_states()`；`DELETE /api/lexicons/{id}` 在归属校验通过后，若该词库的词条已带学习状态则返回 **409** 并拒绝删除（因为 `user_word_state.lexicon_entry_id` 是 `ON DELETE CASCADE`，删词库会连带永久删除复习进度） |
| `backend/tests/test_lexicon_delete_guard.py` | 新增未跟踪 | 19:38:22 | 上述 409 护栏的测试（含「未学习过的非空词库仍可删」「他人词库必须仍是 404 而非 409」的边界断言） |

**这些都是本次审计范围之外的、正在进行中的工作**（很可能是并行的另一个开发会话在 19:38 写入的；本审计只做只读操作，未修改任何代码）。含义：

1. **本节其余内容（§3–§9）描述的是 `HEAD = f6d4da4` 的已提交状态**，不含上述两个未提交改动；
2. 下一位开发者到达时，工作区**可能已包含其他人的未提交工作**——先 `git status` 看清，**保留、不要覆盖或擅自提交**；
3. 上述改动尚未提交，因此它的测试是否通过 = **UNKNOWN**；它对 §6 讨论的权限语义的影响，应在 Phase 2 审计中一并纳入。

被测得为 ignored（因此不在 `git status` 中出现，但**实际存在且重要**）：`data/`、`handoff/`、`test-artifacts/`、`_INCIDENT-20260922/`、`.env`、`data/config/`。

被测得为 ignored（因此不在 `git status` 中出现，但**实际存在且重要**）：`data/`、`handoff/`、`test-artifacts/`、`_INCIDENT-20260922/`、`.env`、`data/config/`。

其余本地分支：

| 分支 | 位置 | 说明 |
| --- | --- | --- |
| `main` | `8216a4c` | 仅含 V1，**不含 V1.1/V1.2**，不要误当作主线 |
| `recovery/v1.1-guarded` | `d4d7ffb`（= origin） | 事故后的 V1.1 安全基线 |
| `codex/vocab-ux-reading-v2` | `a5f79c8` | 事故前工作，**仅作参考** |
| `wip/v1.2-phase1-code` | `a5f79c8` | 同上，二者同点 |
| `stash@{0}` | `v1.2-p1.2-wip-lexicon-backfill` | 事故前 stash，**仅作参考** |

> ⚠️ 事故前分支的 `conftest` 中含 `drop_all`，是 2026-09-22 数据丢失的直接原因，**禁止套用**其中任何测试夹具改动。

## 2.3 最近重要 commit（新 → 旧）[实测]

| commit | 时间 | 阶段 | 标题 |
| --- | --- | --- | --- |
| `f6d4da4` | 09-22 19:12 | 发布 | fix(scripts): add UTF-8 BOM to install-shortcut.ps1 |
| `5ea1233` | 09-22 17:59 | 发布 | docs(v1.2): finalize 0007 production migration runbook ← **迁移执行时的代码 HEAD** |
| `4f536e5` | 09-22 17:50 | 发布 | docs(v1.2): split the 0007 verification and add the migration runbook |
| `6474aef` | 09-22 17:49 | 发布 | fix(v1.2): make stop-vocab prove the server is actually stopped |
| `86c1d5c` | 09-22 17:46 | 发布 | test(v1.2): commit the reproducible 0006 → 0007 production rehearsal |
| `9def720` | 09-22 17:16 | 审计 | docs(v1.2): correct the 0005 forensics: the preflight was added, not moved |
| `81b68d4` | 09-22 17:15 | P1.3 | fix(v1.2): give every word route exactly one identifier namespace |
| `ddc30ca` | 09-22 17:15 | P1.3 | fix(v1.2): check article ownership before a review writes anything |
| `fa6a618` | 09-22 17:15 | P1.0 | fix(v1.2): take CLI passwords from the terminal only |
| `6f3f794` | 09-22 17:15 | 审计 | docs(v1.2): record the 2026-09-22 migration history forensics |
| `6bbd52e` | 09-22 16:50 | P1.0 | test(v1.2): attribute a backup-verify failure to added columns |
| `5db5519` | 09-22 16:50 | P1.0 | test(v1.2): add the fresh-clone 0003 → head migration check |
| `cfbabc6` | 09-22 16:50 | P1.2 | fix(v1.2): abort the lexicon migration before it touches anything |
| `567bdf6` | 09-22 16:50 | P1.0 | fix(v1.2): refuse destructive migrations against protected databases |
| `1c935d6` | 09-22 16:50 | P1.2/0007 | fix(v1.2): give the bridge columns the foreign keys the ORM declares |
| `61eed4f` | 09-22 16:50 | P1.0 | fix(v1.2): fail closed when the schema revision cannot be proven |
| `56c0fee` | 09-22 14:44 | **P1.3** | feat(v1.2): scope every private business API to the authenticated user |
| `7efc9ef` | 09-22 14:44 | P1.3 | test(v1.2): add the IDOR matrix and rewrite tests for user-scoped APIs |
| `6cc5dac` | 09-22 14:53 | 前端 | feat(v1.2): add the sign-in screen and account-aware shell |
| `0d789c9` | 09-22 早 | P1.2 | feat(v1.2): add the lexicon model and rehearse the V1.1 migration on staging |
| `e14b70c` | 09-22 早 | P1.1 | feat(v1.2): port users, sessions and per-user settings onto the safe baseline |
| `122f875` | 09-22 早 | P1.0 | fix: bind the database lazily and refuse to start on a revision mismatch |

**关键事实 [实测]**：`git diff 5ea1233 HEAD -- backend` 为**空**（commit↔commit 比较，不含 §2.2 观测 B 的未提交改动）；`5ea1233 → HEAD` 的全部差异只有 `scripts/install-shortcut.ps1` 一行（加 UTF-8 BOM）。也就是说：
1. 迁移执行时的代码与当前 HEAD 在**应用与迁移代码上完全一致**，仅启动脚本的编码修复不同；
2. 当前 HEAD 的 `code_head_revision()` 仍为 `0007_bridge_foreign_keys`，与生产库 revision 一致，启动护栏不会拦。

## 2.4 运行态 [实测]

| 项 | 值 |
| --- | --- |
| 端口 8000 | `127.0.0.1:8000 LISTENING`，持端口进程 pid **72140**（`python.exe`） |
| `data/server.pid` | `78892`（`backend\.venv\Scripts\python.exe`）；两进程均存活，启动时间 2026-09-22 19:12:30 |
| 进程启动组合 | 与 `stop-vocab.ps1` 注释所述历史情形一致（venv 记录 pid / 基础解释器持端口），停止脚本会一并处理 |
| 健康检查 | `GET /api/health` → **HTTP 200**，`{"status":"ok","app":"拾词"}` |
| 日志 | `data/logs/server-out.log`（408 B，19:12 起）、`server-error.log`（258 B） |

> 本会话未启动、未重启、未停止任何服务。

---

# 3. 数据库状态

生产数据库路径：`D:\背单词web\data\vocab.db`（`.env` 无 `VOCAB_*`；按 `app/config.py:115-121`，未设置 `VOCAB_DATABASE_PATH` 时默认取 `data_dir/vocab.db`）[实测]。

## 3.1 文件级快照 [实测]

| 文件 | 字节 | 存在 | LastWriteTime（本地） | sha256 |
| --- | --- | --- | --- | --- |
| `data/vocab.db` | 589824 | ✅ | 2026-09-22 18:50:14 | `fff0ea2d5c2a5c3176d16fe1d8289a3a326e8041c9ed26c57b9d93cb958bba93` |
| `data/vocab.db-shm` | 32768 | ✅ | 2026-09-22 19:25:02 | — |
| `data/vocab.db-wal` | **156592** | ✅ | 2026-09-22 19:30:26 | — |

`vocab.db` 主文件 sha256 **与 `0007-release-record.md` 记录的迁移后值逐字节一致**（`FFF0EA2D…BBA93`，589824 字节）→ 主文件自迁移后**未被重新 checkpoint**。
但 WAL 已增长到 156592 字节且 mtime 为 19:30:26 → **迁移之后应用确实向生产库写入过**（详见 §3.4）。这与发布记录 §12.3 记的 “`-wal` 0 字节” 相比已经变化。

## 3.2 结构与完整性（只读实测）[实测]

| 检查项 | 实测值 | 判定 |
| --- | --- | --- |
| `alembic_version` | `['0007_bridge_foreign_keys']` | ✅ |
| migration 状态 | 已在 `0007_bridge_foreign_keys`（唯一 head） | ✅ 无待执行迁移 |
| 表数量 | **18**（17 张业务表 + `alembic_version`） | ✅ 与 runbook 期望一致 |
| `PRAGMA integrity_check` | `ok` | ✅ |
| `PRAGMA foreign_key_check` | **0 违规** | ✅ |
| 物理外键数量 | **26** | ✅ 与 ORM 声明数一致（26/26） |
| `_alembic_tmp*` 残留表 | 无 | ✅ |
| 旁挂文件 | `-shm` 与 `-wal` 均存在 | ✅ |

迁移链（由 `backend/alembic/versions/*.py` 的 `revision`/`down_revision` 实测得到，线性无分叉，唯一 head）：

```
0001_initial → 0002_reading_assistance → 0003_article_reading_tools
→ 0004_multiuser_foundation → 0005_lexicon_and_migration
→ 0006_article_exposure_entry → 0007_bridge_foreign_keys (head)
```

## 3.3 两张“同一时刻的不同视图”（重要，容易误判）

SQLite 在 WAL 模式下，磁盘主文件只反映**最近一次 checkpoint** 的状态；应用实际看到的是**主文件 + WAL**。本会话分别读取了两者 [实测]：

| 数据项 | 主文件快照（`immutable=1`） | 含 WAL（应用实际所见） |
| --- | --- | --- |
| `alembic_version` | `0007_bridge_foreign_keys` | `0007_bridge_foreign_keys` |
| `integrity_check` / `foreign_key_check` | `ok` / 0 | `ok` / 0 |
| 表数 / 外键数 | 18 / 26 | 18 / 26 |
| `user` 行数 | 1 | 1 |
| `admin.password_hash` | **`'!'`（1 字符，不可用占位符）** | **`<已脱敏：生产口令哈希>`（97 字符，已设置口令）** |
| `user_session` | 0 | **1**（`user_id=1`，创建于 2026-09-22 11:29:08 UTC = 19:29:08 本地，`expires_at` 2026-10-22，未撤销） |
| `article` | 1 | **2**（id=2，`user_id=1`，`completed=0`，创建于 19:29:46 本地） |
| `article_word_lookup` | 0 | **1** |
| `history_event` | 5 | **8** |
| `word` / `lexicon_entry` / `user_word_state` | 19 / 19 / 19 | 19 / 19 / 19（不变） |
| `review_event` / `article_word_exposure` | 10 / 16 | 10 / 16（不变） |

**结论**：迁移后的生产使用痕迹（登录、生成文章、点词）**目前只存在于 `-wal` 中，尚未 checkpoint 进主文件**。任何“只拷贝 `vocab.db` 主文件”的操作都会得到一个**看起来像迁移刚结束时**的库（`admin` 口令为 `'!'`、无会话、无第二篇文章）。

## 3.4 迁移后生产库的实际用户活动 [实测]

`data/logs/server-out.log` 记录了 19:12 启动之后一次完整的真实使用（时间顺序节选）：

```
GET  /api/auth/me            → 401（未登录，符合预期）
POST /api/auth/login         → 401（一次失败尝试）
POST /api/auth/login         → 200 OK          ← 登录成功
GET  /api/settings/onboarding, /api/dashboard, /api/imports, /api/study/today,
     /api/articles, /api/articles/1            → 均 200
POST /api/articles/1/translate                 → 200
POST /api/articles/generate                    → 200   ← 生成 article id=2
GET  /api/articles/2, POST /api/articles/2/lookup → 200
GET  /api/words?…, /api/words/state/12|11|5, /api/settings → 200
```

即：**生产实例已可正常登录并使用多用户 API**；`article` +1、`article_word_lookup` +1、`history_event` +3、`user_session` +1 均来自这次使用。
`admin` 口令被设置（`'!'` → argon2id）具体由哪条命令/哪个界面在何时完成，仓库内**无日志留存** → 该细节 **UNKNOWN**；可确认的是 [实测]：主文件与 WAL 的 `user` 表行 `created_at` 均为 `2026-09-22 07:04:30`（UTC，= 15:04 本地）。

## 3.5 `0006 → 0007_bridge_foreign_keys` 迁移说明

### 为什么要做（根因）

`0004` 与 `0005` 用 `batch_alter_table(...).add_column(sa.Column(..., sa.ForeignKey(...)))` 添加桥接列。SQLite 对「可空列的 ADD COLUMN」只做普通 `ALTER TABLE ADD COLUMN`，**无法顺带建立外键，约束被静默丢弃**。于是 ORM 声明 26 个外键、物理 schema 只有 17 个——而 `PRAGMA foreign_key_check` **无法发现这个缺口**，因为“从未创建的约束永远不会被违反”。

### 迁移做了什么

对 7 张表（`word`、`review_event`、`article`、`article_word_exposure`、`import_batch`、`import_candidate`、`history_event`）执行**表重建**（SQLite 增删外键的唯一办法），补上 9 个桥接外键，全部为 `ON DELETE SET NULL`（保护学习历史：删用户/词条不会连带删掉复习、文章、暴露记录）：

```
word.user_id                        → user.id            ON DELETE SET NULL
word.lexicon_entry_id               → lexicon_entry.id   ON DELETE SET NULL
review_event.user_id                → user.id            ON DELETE SET NULL
review_event.lexicon_entry_id       → lexicon_entry.id   ON DELETE SET NULL
article.user_id                     → user.id            ON DELETE SET NULL
article_word_exposure.lexicon_entry_id → lexicon_entry.id ON DELETE SET NULL
import_batch.user_id                → user.id            ON DELETE SET NULL
import_candidate.lexicon_entry_id   → lexicon_entry.id   ON DELETE SET NULL
history_event.user_id               → user.id            ON DELETE SET NULL
```

迁移代码内建两道守卫：`upgrade()` 先断言 `PRAGMA foreign_keys` 为 OFF（重建期间 drop 旧表若开启外键会触发 `ON DELETE` 动作导致静默数据丢失），收尾再跑一次 `PRAGMA foreign_key_check`，有违规就抛错而不是留下缺约束的库。`downgrade()` 同样受 `alembic/env.py` 的护栏限制（见 §8）。`0007` **不触碰 `user` 表**。

### 迁移结果 [实测 + 记录]

| 项 | 值 | 来源 |
| --- | --- | --- |
| 新增外键数量 | **+9（17 → 26）** | **[实测]**：`pre-0007-production.db` 实测 17，生产库实测 26 |
| 重建表数量 | 7 张 | 迁移代码 `BRIDGES`（去重后 7 张）[实测] |
| 迁移前 / 后主文件 | 569344 B（sha256 `95d09866…b37a`）→ 589824 B（sha256 `fff0ea2d…bba93`） | [记录] `0007-release-record.md` §0 |
| 执行命令 | `alembic -c alembic.ini upgrade 0007_bridge_foreign_keys`（显式 revision，非 `head`），exit 0 | [记录] `data/recovery/0007-production-migration.log` |
| 迁移后校验 | `revision=0007`、`integrity_check=ok`、`foreign_key_check=0`、`tables=18`、`foreign keys=26`、`tmp leftovers=none`、`content drift=none` | [记录] `verify_0007_production.py` 输出 / release record §5 |
| **是否改变业务数据** | **否** | 见下方独立复核 |
| 是否改变 schema | 是（仅新增 9 个外键；列、类型、数据均未变） | [实测] |

**本次会话对“是否改变业务数据”做了独立复核 [实测]**：用 `tools/rehearsal_migration_0007.py` 中同一个 `row_fingerprint()` 算法（按主键序对每行取值做 sha256），把生产库主文件的 18 张表与 `data/recovery/rehearsal-0006-to-0007.json` 里的 `phase3.after_fingerprints` 逐表比对：

- 18/18 张表 **完全一致（IDENTICAL）**；
- 与 `phase1.before_fingerprints`（迁移前）比对：17 张业务表全部 **same**，**只有 `alembic_version` 变化**（0006 → 0007）。

→ 独立证明：`0007` 没有改动任何一行业务数据，只改变了约束与版本行。

### 风险评估

| 风险 | 评估 | 依据 |
| --- | --- | --- |
| 重建期间数据丢失 | **已消除**：迁移强制要求外键执行关闭，并在失败时整体回滚（注入演练确认失败后 revision 仍为 0006、行数原样） | [记录] runbook §“故障注入” |
| 失败残留 `_alembic_tmp_word` | 存在过：中途失败会残留 0 行临时表，直接重跑会报 `already exists`，需先跑 `data/recovery/drop_tmp_tables.py` | [记录] runbook Step A5 |
| 外键补齐后行为变化 | **低**：新增约束是 `SET NULL`，且现有数据 0 违规；仅在「删除 user / lexicon_entry」时行为与之前不同（此前无序、现在置空） | [实测] `foreign_key_check=0`、`fk_by_child` 全部 SET NULL |
| 运行时是否真的执行外键 | **是**：应用连接建立时执行 `PRAGMA foreign_keys=ON`（`app/db.py:42`） | [实测] |
| 误对生产执行 `downgrade` | **已防护**：`alembic/env.py` 拒绝 downgrade 非一次性副本；`backend/app/testing_guards.py` 提供 `assert_downgrade_allowed` | [实测] |
| 迁移后没有可用备份 | **存在**（见 §6.3）：`data/backups/` 内**没有任何 0007 之后的备份**，最新只到 `pre-0007-production.db`（0006） | [实测] |

---

# 4. 已完成 Phase

## 4.1 Phase 0 · 项目审计

**产出文档**（均在 `docs/`）：

- `V1.2-PHASE0-AUDIT-AND-DESIGN.md`（69 KB）——V1.2 的设计基线：三级数据库环境、P1.0–P1.3 拆分、验收标准、风险清单。**当前仍有效。**
- `2026-09-22-migration-history-forensics.md` + `migration-history-manifest.json`——迁移历史取证：逐个 migration 的文件哈希、是否被事后改写。

**完成内容：**

1. **migration 历史审计**：对 `0001`–`0005` 逐一取证，确认历史 migration 是否被篡改；更正了 `0005` 的一处结论（preflight 是「新增」而非「移动」，`9def720`）。审计工具：`tools/verify_migration_history.py`。
2. **代码审查**：识别出 Phase 0 时点的高风险面，其中被明确点名为最高风险的是 `POST /api/study/words/{word_id}/review`——它接受裸 `word_id` 并对匹配到的行执行写入（IDOR 写入点）；以及 `GET /api/articles/{id}`、`GET /api/words/{id}` 的按 id 直取。
3. **事故与恢复取证**：`2026-09-22` 数据丢失事故（测试夹具对绑定到真实库的 engine 执行 `drop_all`）、恢复过程、永久丢失清单、以及随后生效的安全护栏，记录在 `docs/PROJECT_HANDOFF.md` §0 与 `_INCIDENT-20260922/`（本地，不入 Git）。

## 4.2 Phase 1 · 多用户数据模型

**完成内容：**

### （1）多用户数据模型（P1.1 + P1.2）

| migration | 内容 |
| --- | --- |
| `0004_multiuser_foundation` | `user`、`user_session`、`user_settings`，以及各业务表的 `user_id` 桥接列 |
| `0005_lexicon_and_migration` | `lexicon`、`lexicon_entry`、`user_lexicon`、`user_word_state`，并把 V1.1 的历史数据回填成「系统公共词库 + 每用户学习状态」 |
| `0006_article_exposure_entry` | 给 `review_event` / `article_word_exposure` 补 `lexicon_entry_id` 并回填历史行 |
| `0007_bridge_foreign_keys` | 为 `0004`/`0005` 静默丢失的 9 个桥接列补上物理外键（见 §3.5） |

生产数据的实际归属已实测确认 [实测]：`user_word_state` 19 行全部 `user_id=1`；`word` 19 行 `user_id=1` 且 `lexicon_entry_id` 无 NULL；`review_event` 10 行 `user_id=1`；`article` 全部 `user_id=1`；`article_word_exposure` 16 行 `lexicon_entry_id` 无 NULL；`import_batch` `user_id=1`；`user_lexicon` 1 行（user 1 ↔ lexicon 1）；`lexicon` id=1 为 `visibility=public` 且 `owner_user_id=NULL`（系统公共词库「考研核心词汇」）。**没有孤儿桥接行。**

### （2）用户隔离基础（P1.1）

- 服务端口令哈希使用 **argon2id**（`pwdlib[argon2]`）；会话为**服务端会话表** `user_session`（`token_hash` 存储，非明文），带 `expires_at` / `last_seen_at` / `revoked_at`。
- Cookie 名 `shici_session`；`app/api/deps.py::get_current_user` 是唯一的身份来源，`CurrentUser` / `AdminUser` 两个依赖分别给出「已认证」与「管理员」。
- CLI 口令只从终端读取（`fa6a618`），不落命令行历史。
- 实例级配置（DeepSeek Key / Base URL / Model、OCR）与整体备份为**管理员能力**：`PUT /api/settings` 对非管理员的实例级字段直接 403；`POST /api/settings/backup` 使用 `AdminUser`（`settings.py:124-128, 168-169`）[实测]。

### （3）用户级 API 隔离（P1.3）——**已完成**

详见 §6.1（这是本次审计的重点项）。核心实现：

- 所有私有业务读写的所有权判定集中在 `app/services/userdata.py`（**唯一入口**），原则：所有权只来自会话，绝不来自客户端传入的 id；「不属于你」与「不存在」都返回 **404**（403 只用于「能力不足」，如非管理员改系统词库）。
- 路由层全部使用 `CurrentUser`（列表类查询在 SQL 的 `where` 里按 `user_id` 过滤，不在 Python 里对全局结果做二次过滤）。
- 前端新增登录页与账号感知外壳（`frontend/src/pages/LoginPage.tsx`、`auth.tsx`、`session.ts`），401 统一处理。

### （4）FK 修复

即 `0007_bridge_foreign_keys`，见 §3.5。生产库 `foreign_key_check = 0`，ORM 26 / 物理 26 完全对齐 [实测]。

### （5）其它 Phase 1 附带产物

新护栏与工具（`backend/app/testing_guards.py`、`backend/alembic/env.py`、`backend/app/db.py::verify_schema_revision`、`tools/verify_backup.py`、`tools/verified_db.py`、`tools/prove_test_isolation.py`、`tools/fresh_clone_migration_check.py`、`scripts/check.ps1` 等），以及 24 个后端测试文件（含 `test_authz.py` 的 IDOR 矩阵、`test_id_namespaces.py`、`test_downgrade_guard.py`、`test_schema_foreign_keys.py`、`test_revision_guard.py`、`test_isolation_guards.py`）[实测]。

## 4.3 Release · 0007 生产迁移发布

**发布记录**：`data/recovery/0007-release-record.md`（267 行，含全部留证与 7 项执行偏差）。**结论：发布成功，Part A（数据库迁移与校验）与 Part B（副本应用验收）全部通过。**

### （1）rehearsal（预演，强制前置）

- 工具：`tools/rehearsal_migration_0007.py`；产物 `data/recovery/rehearsal-0006-to-0007.json` + `.log` + `-evidence.log`。
- 结果：`verdict=PASS`、`failures=[]`；源 = 生产库 `sha256 95d09866…b37a` / 569344 B / WAL 0；预演库 `data/staging/migration-rehearsal-0006.db`（与源逐字节相同）；`ORM/physical FK = 26/26`；`before_fingerprints` 覆盖全部 18 张表；**预演期间生产库 sha256 未变（`untouched=true`）**。[记录]
- 预演同时充当正式迁移的**强制前置闸门**：预演必须 PASS 且源 sha256 与当前生产库一致。

### （2）backup（备份）

- **停机证明**：`stop-vocab.ps1` 输出 `no running Shici server` + `Verified:`，端口 8000 无监听，无 python/pythonw/uvicorn 进程；5×2s 取样 sha256 恒定、WAL 恒为 0。
- **WAL 门**：裸拷贝前 WAL 必须为 0 字节（非空时的裸拷贝会静默丢失已提交页）。
- 备份：`data/backups/pre-0007-production.db`（sha256 `95d09866…b37a`，569344 B，`source == backup` = True）；同名的既有文件先归档为 `pre-0007-production.prior-attempt-20260922-184056.db`（不销毁既有证据）。证据 `data/recovery/0007-phase2-backup-evidence.log`。

### （3）production migration（迁移）

- 四道闸门全部通过后才写库：`VOCAB_*`/`PYTEST_*` 环境变量为空；alembic 解析出的目标库 == `D:\背单词web\data\vocab.db`；`alembic current` == `0006`；日志文件不得已存在。
- 命令：`cd backend && .\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade 0007_bridge_foreign_keys`，**exit 0**，日志 `data/recovery/0007-production-migration.log`。

### （4）verification（校验）

- Part A：`data/recovery/verify_0007_production.py`（只读，exit 0）→ `revision=0007`、`integrity_check=ok`、`foreign_key_check=0`、`tables=18`、`FK=26`、`tmp leftovers=none`、**`content drift=none`**；A6 确认 `alembic current` == `0007` 后才解除启动封锁。
- Part B（**仅在副本上** `data/staging/post-0007-verification.db`）：`tools/staging_two_user_check.py` → `STAGING TWO-USER CHECK VERIFIED`（17 项全 PASS）；`tools/staging_isolation_check.py` → **`26/26 checks passed` · `STAGING P1.3 ACCEPTANCE VERIFIED`**，报告 `verified: true`、`failures: []`（`data/recovery/post-0007-two-user-report.json` / `post-0007-isolation-report.json`）。全程生产库 sha256/WAL/行数未变。
- 副本已在 19:05 前后按 §12.1 逐个删除（含 `.db-shm`/`.db-wal`，未用通配符），删除后复核文件不存在、生产库未被触碰。

### （5）发布后

- §12.2：修复桌面快捷方式乱码（`install-shortcut.ps1` 缺 UTF-8 BOM 导致 WinPS 5.1 按 GBK 解码），提交 `f6d4da4`，桌面重扫后仅剩 1 条正确快捷方式。
- §12.3：19:12:22 经快捷方式首次启动生产实例，`/api/health` 200；数据库只读复核 `revision=0007`、`integrity_check=ok`、`foreign_key_check=0`、`tables=18`、`FK=26`、`content drift=none`。
- **此后（19:25–19:30 本地）发生了一次真实登录与使用**，见 §3.4——这部分**晚于发布记录**，是本文档新增的现状。

---

# 5. 当前系统架构状态

## 5.1 Backend

| 关注点 | 现状 [实测] |
| --- | --- |
| 框架 | FastAPI（`app/main.py`），`lifespan` 中依次执行：`ensure_directories` → **`verify_schema_revision`（revision 不匹配则拒绝启动）** → OCR 环境配置 → `create_backup` |
| ORM | SQLAlchemy 2.x，声明式模型集中在 `app/models.py` |
| 迁移 | Alembic（`backend/alembic/`），`env.py` 里含外键关闭、downgrade 护栏、`-x db_url=` 覆盖支持 |
| 路由模块 | `auth`、`dashboard`、`imports`、`study`、`words`、`articles`、`lexicons`、`settings` 共 8 个 router（`main.py:33-40`） |
| 鉴权依赖 | `app/api/deps.py`：`CurrentUser`、`AdminUser`、`ensure_user_settings`；**没有**任何端点接受客户端传入的 `user_id` 决定数据归属 |
| 所有权访问层 | `app/services/userdata.py`（`load_user_word` / `load_user_word_state` / `load_user_article` / `load_user_batch` / `load_readable_lexicon` / `load_writable_lexicon` / `accessible_lexicon_ids` / `get_or_create_word_state`） |
| 业务服务 | `services/imports.py`（导入/OCR 批处理）、`services/reading.py`（选词、暴露、点词、翻译、生词入库）、`services/study.py`（复习记录）、`services/scheduler.py`（规则式调度）、`services/auth.py`（用户/会话/口令）、`services/backup.py`、`services/ocr/`、`services/ai/` |
| Prompt | `app/prompts/`（全部 DeepSeek prompt 集中存放） |
| 错误语义 | 资源不存在 / 不属于你 → **404**（`main.py` 的 `NotFoundError` 处理器统一输出同一条 detail，避免通过响应差异探测他人数据存在性）；能力不足 → **403** |
| 静态托管 | `frontend/dist` 挂 `/assets`，其余路径回落到 `index.html`（SPA） |
| 运行库护栏 | 连接建立时 `PRAGMA foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`（`app/db.py:42-47`） |

## 5.2 Frontend

| 关注点 | 现状 [实测] |
| --- | --- |
| 框架 | React 19 + Vite + TypeScript，React Router 7 路由，TanStack Query 管数据 |
| 页面 | `DashboardPage`、`ImportPage`、`LibraryPage`、`StudyPage`、`ReadingPage`、`SettingsPage`、**`LoginPage`（新增）** |
| 会话 | `auth.tsx` / `authContext.ts` / `useAuth.ts` / `session.ts`；`api.ts` 统一处理 401 与 `credentials: include` |
| 组件 | `AppShell`、`HelpCenter`、`MeaningList`、`States` |
| 测试 | `App.test.tsx`、`auth.test.tsx`、`pages/ReadingPage.test.tsx`、`components/MeaningList.test.tsx`（Vitest + Testing Library） |
| 构建产物 | `frontend/dist`（由 `start-vocab.ps1` 构建；构建或迁移失败即中止，不会运行旧页面） |

## 5.3 数据库主要实体与关系

```
user ──1:1── user_settings            每用户偏好（每日新词数、文章长度）
     ──1:N── user_session             服务端会话（token_hash / expires_at / revoked_at）
     ──1:N── user_lexicon ──N:1── lexicon     用户启用了哪些词库（含 daily_new_words）
     ──1:N── user_word_state ──N:1── lexicon_entry   每用户的学习状态（核心）
     ──1:N── word                     V1.1 遗留表（仍保留，见下）
     ──1:N── review_event
     ──1:N── article ──1:N── article_word_exposure ──N:1── lexicon_entry
     │                    └─1:N── article_word_lookup
     ──1:N── import_batch ──1:N── import_image
     │                     └─1:N── import_candidate
     ──1:N── history_event

lexicon ──1:N── lexicon_entry      词条本体（word / default_anchor / source_raw /
                                   source_meanings / semantic_note）
```

| 实体 | 角色 | 关键约束 |
| --- | --- | --- |
| `user` | 账号、角色（`role`）、`is_active`、`password_hash`（argon2id） | 用户名规范化唯一 |
| `user_session` | 服务端会话 | `user_id → user.id ON DELETE CASCADE` |
| `user_settings` | 每用户偏好 | `user_id → user.id ON DELETE CASCADE` |
| `lexicon` | 词库；`owner_user_id=NULL` 且 `visibility='public'` 即**系统公共词库**（生产上 id=1「考研核心词汇」） | `owner_user_id → user.id ON DELETE CASCADE`；公共库仅管理员可写（否则 404） |
| `lexicon_entry` | 词条本体（Source 侧） | `lexicon_id → lexicon.id ON DELETE CASCADE` |
| `user_word_state` | **每个用户对每个词条的学习状态**：`status`、`next_review_at`、`first_seen`、`last_review`、`anchor_override`、`semantic_note`、`legacy_word_id` | 唯一约束 `(user_id, lexicon_entry_id)`；`user_id`/`lexicon_entry_id` 均 CASCADE |
| `word` | **V1.1 遗留表，未删除**（兼容垫片）：保留旧 `word.id` 命名空间，通过 `user_id` / `lexicon_entry_id` 桥接到新模型 | `user_id`/`lexicon_entry_id` 均 `SET NULL` |
| `review_event` | 每次复习事件（学习的审计真相） | `word_id` CASCADE、`article_id` NO ACTION、`user_id`/`lexicon_entry_id` SET NULL |
| `article` | 用户文章（正文、目标词、实际用词、完成状态、译文） | `user_id → user.id ON DELETE SET NULL` |
| `article_word_exposure` | 文章 ↔ 词条的显式暴露关系（含 `context`、`exposure_count`） | `article_id`/`word_id` CASCADE，`lexicon_entry_id` SET NULL |
| `article_word_lookup` | 阅读点词缓存（`surface` / `normalized_word` / `meaning` / `added_word_id`） | `article_id` CASCADE，`added_word_id` SET NULL |
| `import_batch` / `import_image` / `import_candidate` | 可恢复的导入流程（批次 → 图片 → 候选） | `user_id` SET NULL；批次内 CASCADE |
| `history_event` | 通用行为历史（导入、备份等） | `user_id → user.id ON DELETE SET NULL` |

**两个容易踩的 id 命名空间**：`word.id`（V1.1 遗留，经 `user_word_state.legacy_word_id` 解析）与 `user_word_state.id`（V1.2 新命名空间）。两条路径**刻意不互相回退**：`GET /api/words/{id}` 与 `POST /api/study/words/{id}/review` 只认 `word.id`；`GET /api/words/state/{id}` 与 `POST /api/study/word-states/{id}/review` 只认 `user_word_state.id`。混用会得到 404 而不是“猜中的另一行”。（`81b68d4`，测试 `test_id_namespaces.py`）

---

# 6. 当前已知问题

## 6.1 重点核查：`/api/words`、`/api/articles`、`/api/study`、review 接口的用户级隔离

**结论：这些接口的用户级隔离（P1.3）在代码上已经完成，未发现未完成项。** 逐项实测证据：

| 接口 | 隔离实现 [实测] | 判定 |
| --- | --- | --- |
| `GET /api/words` | `words.py:22-41`：`CurrentUser` + `where(UserWordState.user_id == user.id)`（SQL 内过滤，非 Python 二次过滤） | ✅ 已隔离 |
| `GET /api/words/{word_id}` | `words.py:90-100` → `userdata.load_user_word`（`where(user_id == user.id, legacy_word_id == word_id)`），越权得 404 | ✅ 已隔离 |
| `GET /api/words/state/{state_id}` | `words.py:77-86` → `userdata.load_user_word_state`，按 `user_word_state.id` 且限本人 | ✅ 已隔离 |
| `GET /api/articles` | `articles.py:25-33`：`where(Article.user_id == user.id)` | ✅ 已隔离 |
| `GET /api/articles/{article_id}` | `articles.py:37-44` → `load_user_article`（`id == article_id, user_id == user.id`）；quiz 词表也按 `UserWordState.user_id` 过滤 | ✅ 已隔离 |
| `POST /api/articles/{id}/complete｜judge｜lookup｜translate｜lookups/{id}/add-word` | 全部先经 `load_user_article`；`judge` 额外用 `word_state_id` 做本人态解析；`add-word` 校验 lookup 归属本文章 | ✅ 已隔离 |
| `GET /api/study/today` | `study.py:19-50`：`where(UserWordState.user_id == user.id, …)` | ✅ 已隔离 |
| `POST /api/study/words/{word_id}/review` | `study.py:54-81` → `record_review_for_user`（先按本人 `legacy_word_id` 解析） | ✅ 已隔离 |
| `POST /api/study/word-states/{state_id}/review` | `study.py:85-105` → `record_review_for_user_state`（本人 state） | ✅ 已隔离 |
| `GET /api/dashboard` | `dashboard.py:20+`：每个计数都在 SQL 里按 `user_id` 限定 | ✅ 已隔离 |
| `/api/imports/*`、`/api/lexicons/*`、`/api/settings/*` | 分别经 `load_user_batch` / `load_readable_lexicon` / `load_writable_lexicon` / `ensure_user_settings`；实例级写操作限管理员 | ✅ 已隔离 |

**四重独立证据：**

1. **[实测] 代码**：上述路由均以 `CurrentUser` 为依赖（`grep` 全量核对 8 个 router 的每个端点，无遗漏者；唯一无需鉴权的是 `GET /api/health` 与静态资源）。
2. **[实测] 迁移当时就已具备**：`git show 5ea1233:backend/app/api/words.py` 已包含 `CurrentUser` 与 `user.id` 过滤；`git diff 5ea1233 HEAD -- backend` 为空。P1.3 的实现 commit 是 `56c0fee`（09-22 14:44），**早于** 17:59 的迁移 HEAD。
3. **[实测] 测试**：`backend/tests/test_authz.py` 含 19 个测试（含 3 个显式 IDOR 用例：取他人 article / 取他人 word / 对他人 word 写 review）；另有 `test_id_namespaces.py`、`test_review_article_ownership.py`、`test_auth.py`。
4. **[记录] 副本验收**：`data/recovery/post-0007-isolation-report.json` 记录 **26/26 全 PASS**（`STAGING P1.3 ACCEPTANCE VERIFIED`），覆盖「userb 看不到 admin 的词/文章/导入历史」「按 id 探测得 404 且 404 不泄漏文本」「写他人 word 得 404 且不改变任何数据」「非管理员调 admin 端点得 403」「userb 可读系统公共词库但改/删得 403」「userb 私有词库对 admin 不可见」。

**⚠️ 必须纠正的一处文档错误（会影响判断，务必知晓）**：`data/recovery/0007-release-record.md` §11 第 2 条写着「`/api/words`、`/api/articles`、`/api/study` 的用户级作用域属于 P1.3，**尚未生效**——在 P1.3 落地前，任何已登录用户仍可读取全局数据」。**这句话在成文时就已经过时，与同一份文档 §6 的实际验收结果、以及当时的代码相矛盾**（§6 记录的隔离项正是 P1.3 行为）。同理，`docs/PROJECT_HANDOFF.md:62` 的「尚未实施：P1.3（按用户隔离全部业务 API）、前端登录页」也已过时——`LoginPage.tsx` 存在于 `frontend/src/pages/`。**不要依据这两句话重新实施 P1.3。**

### P1.3 尚未完成的部分（准确表述）

- **生产环境下的多用户实测未做。** 生产库只有 1 个 `user` 行（`admin`），因此“隔离”在生产上只能通过副本证据与代码/测试证明，**没有**在生产数据上用第二个真实账号端到端验证过。这是 Phase 2 需要补上的验收项（建议**在副本上**做，不要在 production 建测试账号）。
- 因此建议的表述是：**「P1.3 代码完成 + 副本验收通过；生产双账号验收待做」**，而不是「P1.3 未完成」。

## 6.2 `admin` 的 `password_hash='!'` 与“不可登录”

**这是本次审计中唯一一处与既有说法需要精确区分的问题，因为它同时“对”和“不对”。**

| 视角 | `admin.password_hash` | 能否登录 |
| --- | --- | --- |
| **仅主文件**（`vocab.db` 本身，最后一次 checkpoint 的状态）[实测] | `'!'`（1 字符） | **不能**。`'!'` 是“无可用口令”的占位符，口令校验必然失败 |
| **迁移前备份** `data/backups/pre-0007-production.db`（0006）[实测] | `'!'`（1 字符） | 不能 |
| **含 WAL 的逻辑库**（应用实际所见）[实测] | `<已脱敏：生产口令哈希>`（97 字符，算法前缀为 argon2id） | **可以**：`server-out.log` 记录 `POST /api/auth/login → 200 OK`（§3.4） |

**说明（重要）：**

1. `password_hash='!'` 是**迁移前状态**，**不属于 0007 的问题**。[实测] 依据：`0007_bridge_foreign_keys.py` 的 `BRIDGES` 只涉及 7 张表，**不含 `user` 表**；且 `user` 表在迁移前备份与迁移后主文件中的行指纹**完全一致**（本会话用同一算法逐表比对：`user` = IDENTICAL）。`0007` 只加外键、不改数据。
2. `admin` 的口令是在 **19:12 启动生产实例之后**被设置的（`'!'` → argon2id），并完成过一次成功登录；这一写操作**目前只存在于 `-wal` 中**，尚未 checkpoint 进主文件。具体设置方式（CLI `set-password` 还是界面）**UNKNOWN**——仓库内无对应日志。
3. **由此产生一个真实风险**：如果有人按“备份 = 拷贝 `vocab.db`”的直觉操作，或在 checkpoint 前单独复制/还原主文件，就会把库**回退到 `password_hash='!'` 的状态**，登不进去，且会丢失 `user_session`、第 2 篇文章、`article_word_lookup` 与 3 条 `history_event`。**必须整体处理 `vocab.db` + `-wal` + `-shm` 三个文件，或使用 SQLite backup API（`app/services/backup.py` 就是这么做的）。**
4. 若确实需要重置口令，走 CLI（从终端读口令），不要手工 UPDATE `user` 表——§8 禁止直接改生产库。

## 6.3 迁移之后没有任何可用备份（建议优先处理）

[实测] `data/backups/` 现有内容与其实质：

| 文件 | 字节 | revision | 说明 |
| --- | --- | --- | --- |
| `2026-09-22-vocab.db` | 376832 | `0003_article_reading_tools`（11 表） | **是 V1.1 时期的每日备份（08:58 生成）** |
| `pre-p1.2-2026-09-22-131040-vocab.db` | 471040 | `0003`（仅 1 张表） | 事故前的**损坏副本**，仅作事故证据 |
| `RECOVERY-20260922-131057/vocab.db` | — | — | 恢复源 |
| `manual-2026-09-22-135936-vocab.db` | 376832 | `0003`（11 表） | 手工备份，V1.1 |
| `pre-0007-production.db`（+`-wal`/`-shm`） | 569344 | `0006_article_exposure_entry` | **正式迁移的回滚点（V1.2 之前的最后状态）** |
| `pre-0007-production.prior-attempt-20260922-184056.db` | 569344 | `0006` | 归档的同名既有文件 |

**结论：`data/backups/` 中不存在任何 `0007` 之后的备份。**
原因 [实测]：`app/services/backup.py:11-12` 在目标名已存在时直接返回（每日备份名为 `YYYY-MM-DD-vocab.db`），而今天的 `2026-09-22-vocab.db` 早在 08:58 就已生成，因此 19:12 的自动备份**跳过了**；`POST /api/settings/backup`（带时间戳、不会被跳过）在日志中**没有被调用过**。

→ Phase 2 的第一件事应当是：用 SQLite backup API 或等价的 WAL 安全方式，为**当前 0007 生产库**建立一份**verified backup**，并记录新的 baseline（见 6.4）。

## 6.4 `scripts/check.ps1` 的最后一个门禁当前必然失败

[实测] 我以只读方式运行了它的最后一步：

```
backend\.venv\Scripts\python.exe tools\verify_backup.py data\vocab.db --baseline data\recovery\baseline.json
→ VERDICT: NOT A VERIFIED BACKUP   (exit 1)
   FAIL: alembic revision '0007_bridge_foreign_keys' != expected '0003_article_reading_tools'
   FAIL: article: 1 baseline rows changed
   FAIL: article_word_exposure: 16 baseline rows changed
   FAIL: review_event: 10 baseline rows changed
```

原因：`data/recovery/baseline.json` 记录的是**恢复后的 V1.1 源**（`0003`，11 张表，`captured_at_utc = 2026-09-22T06:10:33Z`），而 `tools/verified_db.py:271-274` 默认要求 revision 与 baseline 一致。三类 `rows_changed` 是**预期**的：`0004/0005` 给这些表加了 `user_id` / `lexicon_entry_id` 桥接列，行哈希因**列集合变化**而改变，并非数据丢失（本次独立指纹比对已证明这些表的业务内容逐行未变，见 §3.5）。

→ **在按当前库重新记录 baseline 之前，`scripts/check.ps1` 一定以 exit 1 结束**（它把这一步当成必过项）。这不是代码缺陷，而是**证据基线未随 V1.2 更新**。处理方式二选一，并明确记录选择：以 `verify_backup.py --write-baseline` 从**当前已验证的 0007 生产库**记录新基线（推荐，需在 6.3 的备份之后做）；或对 V1.2 之后的库显式使用 `--allow-revision-change`（不推荐，会削弱护栏）。

## 6.5 其它已知问题与限制

| # | 问题 | 级别 | 依据 / 说明 |
| --- | --- | --- | --- |
| 1 | 19 个 commit **只在本地**（`origin` 停在 `6ad8fce`，落后 19） | 高 | 若本机故障，P1.0–P1.3 代码与 0007 迁移代码将只能靠工作区恢复。发布证据（`data/recovery/*`）按设计不入 Git，需随源码包另行交接 |
| 2 | 生产库 WAL 156592 B 未 checkpoint | 中 | 见 §6.2 第 3 条；备份/拷贝必须走 backup API 或先做 WAL 门检查 |
| 3 | 生产仍是单用户（1 个 `user` 行） | 中 | 生产级双账号隔离验收未做（§6.1） |
| 4 | `data/staging/` 残留含生产数据拷贝与测试口令的副本 | 中（安全） | `migration-rehearsal-0006.db`（按证据保留，因被 `rehearsal-0006-to-0007.json` 引用）、`v1.1-realdata-migration-test.db`、`fresh-clone-0003-to-head.db`、`v1.3-acceptance.db`。处置须连同 `-shm`/`-wal`，并确认无进程占用 |
| 5 | 文档过时陈述 | 中 | `PROJECT_HANDOFF.md:62`（P1.3 / 登录页“尚未实施”）与 `0007-release-record.md` §11.2（P1.3 “尚未生效”）均与现状矛盾，见 §6.1 |
| 6 | `data/recovery/acceptance-report.json` 是 **V1.1 / `0003` 时期**的验收证据（`db_revision=0003`、11 张表） | 低（易误读） | 不要把它当作 0007 之后的证据；0007 之后的证据是 `production-final-verify.json`、`post-0007-*.json`、`rehearsal-*.json` |
| 7 | 版本号未更新（`1.0.0` × 3 处） | 低 | `backend/pyproject.toml`、`frontend/package.json`、`FastAPI(version=...)` |
| 8 | 未知 API 路径返回 200 HTML | 低 | `main.py:62-69` 的 SPA 兜底路由在 router 之后匹配 `/{path:path}`，因此 `/api/does-not-exist` 会返回 `index.html` 而非 404 JSON。Phase 2 做权限测试时不要把“非 404”误读为漏洞 |
| 9 | `word` 表与 `user_word_state` 双轨并存 | 低（技术债） | 见 §5.3；`word` 作为 V1.1 兼容垫片仍在维护，退场时机属于后续阶段决策 |
| 10 | 本会话**未**运行 pytest / ruff / 前端测试 | — | 遵守“不执行 migration”的约束（测试套件会在临时库上跑 alembic）。**HEAD 上测试是否全绿 = UNKNOWN**。仓库内最后一次记录的全绿证据是 `PROJECT_HANDOFF.md` §8（后端 31 项通过等），但那是 **V1.1 时期**的，不能代表 HEAD |
| 11 | `data/recovery/` 内 `*.prior-attempt-*` 归档文件 | 低 | 属“不覆盖既有证据”策略的产物，保留原状，勿当作当前证据 |
| 12 | `admin` 口令明文 | — | 只存哈希，**任何人（含后续开发者）都不应也无法从库中读出**；如需重置走 CLI |

## 6.6 审计期间出现的未提交改动（不在本次审计范围内）

`HEAD = f6d4da4` 之外，工作区在 19:38 出现了两个**未提交**改动（详见 §2.2 观测 B）：`backend/app/api/lexicons.py`（新增 `DELETE /api/lexicons/{id}` 的 409 学习记录护栏）与新增测试 `backend/tests/test_lexicon_delete_guard.py`。

需要下一位开发者注意：

- 它们**不属于**本文档描述与核验的状态；本文档未对它们的正确性做任何判定；
- 它们会改变 `DELETE /api/lexicons/{id}` 的状态码语义（409 新增），因此 §6.1 表格中 `/api/lexicons/*` 一行的结论**仅对 `HEAD` 成立**；Phase 2 的权限测试必须把 409 纳入期望矩阵；
- 该改动未运行测试（= **UNKNOWN**），也未提交；接手时请先 `git status`，**保留他人未提交的工作，不要覆盖、不要擅自提交**；
- 若该护栏会被采纳，注意它触及的是一个真实风险点：`user_word_state.lexicon_entry_id` 为 `ON DELETE CASCADE`，删词库会**级联永久删除**学习进度（`review_event`/`article_word_exposure` 的 `lexicon_entry_id` 则被置 NULL）。这正是 §8.1「禁止直接改生产库」之外，API 层需要自己守住的边界。

---

# 7. 下一阶段计划

## Phase 2 · 登录、Session、权限隔离完善

**目标**：把 Phase 1 已建好的多用户模型与代码级隔离，收敛为**生产级、可验收、有证据**的认证与权限体系；不引入破坏性 schema 变更；所有 migration 走 §8 流程。

**前置（Phase 2 开始前必须先做的两件事）：**

- **P2.0-a**：为当前 0007 生产库建立 **verified backup**（解决 §6.3），并记录新的 baseline（解决 §6.4）。
- **P2.0-b**：把本地 19 个 commit 推送到远端（解决 §6.5 第 1 条），避免发布成果只存在于单一机器。

### 1. 认证流程审计

- 逐路径审计 `app/api/auth.py` 与 `app/services/auth.py`：`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`、`POST /api/auth/password`、`/api/users*`（管理员）。
- 明确并记录：口令策略与最小长度、登录失败的处理与是否限速、用户名规范化与大小写/空白、`is_active=False` 的行为、口令变更后是否撤销既有会话（`revoke_all_sessions` 的调用点）。
- 前端：`LoginPage.tsx`、`auth.tsx`、`api.ts` 的 401 处理与账号切换路径，确认切换账号后**不残留**上一账号的缓存（TanStack Query cache 的失效策略）。
- 产出：`docs/phase2/PHASE2-AUTH-AUDIT.md`，含每条路径的现状、缺口、结论。
- **只审计，不改代码**（与本项目 Phase 0/2 的一贯做法一致）。

### 2. Session 管理

- 复核 `user_session` 生命周期：`expires_at`（`get_settings().session_days`）、`last_seen_at` 的滑动续期（`touch_session`）、`revoked_at` 的撤销语义、登出是否真正撤销、过期/撤销行的清理策略（当前是否有清理任务 = UNKNOWN，需实测）。
- Cookie 属性审计：`HttpOnly` / `SameSite` / `Secure` / `Path` / `Max-Age`（本地 HTTP 部署下 `Secure` 的处理需明确记录）。
- 并发会话与设备策略：是否允许同账号多会话；`token_hash` 是否只存哈希（当前是）。
- 产出：会话状态机说明 + 过期/撤销的测试用例清单。

### 3. API user scope 收口

- 以 §6.1 的表格为起点做**回归确认**（而非重写）：逐个端点核对是否仍走 `app/services/userdata.py`，新增端点必须复用同一访问层。
- 处理 §6.5 第 8 条（未知 `/api/*` 不应返回 200 HTML）。
- 检查是否存在“**系统公共词库的读** vs **私有词库的写**”这类混合语义端点的边界（`lexicons.py`），确认 403/404 语义与 IDOR 规则一致。
- 产出：端点 × 所有权 × 期望状态码的矩阵文档（可直接由 `test_authz.py` 反向生成核对）。

### 4. 权限测试

- **在副本上**（禁止在生产建测试账号）跑完整的双账号 + 三账号验收：管理员、普通用户 A、普通用户 B；覆盖跨用户读、跨用户写、跨用户列表计数、公开词库读/写边界、实例级配置与备份接口的管理员门槛、匿名访问。
- 复用并扩展既有工具：`tools/staging_accounts.py`、`tools/staging_two_user_check.py`、`tools/staging_isolation_check.py`（沿用其“证据落盘 + `verified`/`failures` 结构”的约定）。
- 前端侧补登录门禁与账号切换的回归测试。
- 产出：`data/recovery/phase2-*.json` 报告（新文件名，**不得覆盖**既有证据），以及 `scripts/check.ps1` 全绿（依赖 P2.0-a）。
- **验收口径**：越权读/写一律 404 且响应不泄漏文本；能力不足一律 403；匿名一律 401；副本外的数据库文件 sha256 与行数在测试前后不变。

### 5. 必要 migration

- **原则**：能不加 schema 就不加。只有在审计中确证存在缺口时才新增 `0008_*`。
- 已被识别的候选（**均需先在审计中确证，不得预先实施**）：
  - 会话清理所需的索引或过期行回收策略（若确证有性能/膨胀问题）；
  - 登录失败的审计记录落点（若需服务端限速或审计）；
  - 若决定推进 `word` 表退场，需要单独的、**风险评估先行**的 migration（涉及遗留 id 命名空间，风险高，建议推迟）。
- 新增 migration 必须：不改历史 migration、不改业务数据语义、在 `data/staging/` 用真实数据预演、提供升级后逐表行数与行指纹不变的证据、并具备 downgrade 护栏下的回滚预案。

**Phase 2 完成判定（Exit Criteria）**

1. `scripts/check.ps1` 全绿（含隔离取证与 verified backup 门禁）；
2. 生产库仍为 `0007`（或经批准的新 revision），`integrity_check=ok`、`foreign_key_check=0`；
3. 有一份 **0007 生产库的 verified backup** 且新 baseline 已记录；
4. 副本上的多用户/三用户权限验收报告全 PASS，且证据文件已落盘；
5. 认证与会话审计文档完成，所有“已确认缺口”要么已修，要么被显式登记为待办；
6. 无任何生产库直改、历史 migration 改写、生产 downgrade、跳过预演的行为。

---

# 8. 开发规则

## 8.1 绝对禁止

| 禁止 | 原因 / 护栏 |
| --- | --- |
| **直接修改 production 数据库**（手工 UPDATE/INSERT/DELETE、`drop_all`、外部工具改表） | 生产库是唯一事实来源；schema 只能经 Alembic 演进。手工改数据会绕过行指纹基线，无法再证明数据完整性 |
| **修改历史 migration**（`0001`–`0007` 的既有内容） | 它们已在真实库上执行过；改写会让“数据库 revision ↔ 代码”不再一一对应。`tools/verify_migration_history.py` 会对历史文件哈希取证；`ruff` 对 `alembic/versions/*.py` 单独放宽了 import 排序，正是为了**避免格式化导致的字节变动** |
| **对 production 执行 `downgrade`** | `alembic/env.py` 已内建拒绝：非一次性副本一律 `UnsafeDatabasePathError`；只有当 downgrade 目标是明确的临时/副本库时才放行 |
| **跳过 rehearsal** | 预演是正式迁移的**强制前置闸门**，且预演成功还要求“源 sha256 与当前生产库一致”。跳过预演 = 在没有可回滚证据的情况下改生产库 |
| 在测试夹具中出现 `drop_all`（或任何绑定真实 `data/` 的 engine 操作） | 这正是 2026-09-22 数据丢失事故的成因。`backend/app/testing_guards.py` 会对「绑定 engine / 解析设置 / 破坏性 schema 操作」fail-fast |
| 把数据库、备份、图片、`data/config/settings.json`、`.env`、API Key 提交进 Git 或放进源码交接包 | `.gitignore` 已覆盖，但**人为绕过**（`git add -f`）是禁止的 |
| 未经许可启动生产实例去“顺手跑一下迁移” | `scripts/start-vocab.ps1` 会先执行 `alembic upgrade head`：**一次启动就等于一次没有备份、没有日志的隐式迁移** |

## 8.2 迁移流程（必须按顺序，每一步都要留证）

```
rehearsal  →  backup  →  migration  →  verification
   ↓            ↓            ↓              ↓
 预演 PASS    verified     显式 revision   逐表指纹 +
 源哈希一致    backup       非 head         integrity/fk
```

对应的真实命令（摘自 `docs/0007-production-migration-runbook.md`，本流程已在 0007 上完整执行过一次）：

```powershell
# 0) 停机并证明静止：端口无监听 + 进程不存在 + 5×2s 取样 sha256/WAL 恒定
powershell -File scripts\stop-vocab.ps1

# 1) rehearsal（强制前置；失败或源哈希不一致即中止）
backend\.venv\Scripts\python.exe tools\rehearsal_migration_0007.py `
    --source data\vocab.db --rehearsal data\staging\migration-rehearsal-0006.db

# 2) backup（WAL 必须为 0 字节；或使用 SQLite backup API）
Copy-Item data\vocab.db data\backups\<新名字>.db
backend\.venv\Scripts\python.exe tools\verify_backup.py data\backups\<新名字>.db --baseline <baseline>

# 3) migration（显式 revision；不要用 head）
cd backend
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade <revision_id>

# 4) verification（只读）
backend\.venv\Scripts\python.exe data\recovery\verify_<revision>_production.py
.\.venv\Scripts\python.exe -m alembic -c alembic.ini current      # 必须等于代码 head
```

**硬性要求**：

1. 迁移**只能**用显式 revision 执行，禁止 `upgrade head` 直接打生产库；
2. 备份只有通过 `tools/verify_backup.py` 全部校验后，才可称为 **verified backup**——「复制成功 ≠ 备份可信」（事故中 `pre-p1.2-*` 副本本身就是损坏的）；
3. 证据文件**不得覆盖**：同名冲突时归档为 `*.prior-attempt-<时间戳>`，保留原状；
4. 迁移后必须核对**每张表的行数与行指纹**（不只看 `integrity_check`——它无法发现“从未创建的外键”）；
5. 迁移与校验完成、`alembic current` 等于 head 之后，才允许启动应用（否则启动器会自行 `upgrade head`）。

## 8.3 三级数据库环境（强制）

| 级别 | 位置 | 用途 | 规则 |
| --- | --- | --- | --- |
| **Level 1** | pytest 临时目录（`backend/.pytest-tmp`） | 单元 / API / migration 测试 | 只允许存在于 pytest 临时目录；Alembic 测试必须在子进程中运行并显式传 `-x db_url=` |
| **Level 2** | `data/staging/*.db` | 用**真实数据内容**演练迁移与多用户验收 | 唯一允许拿真实数据做破坏性演练的地方；可随时重建；**不是生产** |
| **Level 3** | `data/vocab.db` | 生产 | 开发期间**禁止迁移、禁止直改**；只允许在按 §8.2 发布窗口内、经过闸门后写入 |

## 8.4 每次提交前的自检

```powershell
powershell -File scripts\check.ps1        # ruff + 后端测试 + 前端四件套 + 隔离取证 + verified backup（见 §6.4）
backend\.venv\Scripts\python.exe tools\prove_test_isolation.py   # 证明 pytest 没碰 data/
```

其中「测试隔离取证」是**必过项**：任何涉及 migration、downgrade、schema 重置、夹具建表或破坏性 SQL 的改动，都必须通过它。

---

# 9. 给下一位开发者的启动说明

**接手顺序（全部为只读操作，先看再动）：**

1. 读本文档。若只读一份，就读 §6（已知问题）与 §8（开发规则）。
2. 读 `docs/PROJECT_HANDOFF.md`（项目与事故背景；注意其 §0.2 关于 P1.3/登录页的陈述**已过时**）与 `docs/V1.2-PHASE0-AUDIT-AND-DESIGN.md`（设计基线，仍有效）。
3. 确认 Git 与运行态：`git status`（**本审计期间它从“干净”变成了含他人未提交改动，见 §2.2 / §6.6——先看清再动手，保留别人的工作**）、`git log --oneline -20`、检查 8000 端口是否在跑（本审计时**在跑**，pid 72140）。
4. 确认数据库状态（**只读**，不要用 `alembic current` 去打生产库——它会以读写方式打开库并对空 WAL 做 checkpoint）：
   ```powershell
   backend\.venv\Scripts\python.exe tools\verify_backup.py data\vocab.db --baseline data\recovery\baseline.json
   ```
   预期当前输出 `NOT A VERIFIED BACKUP`（原因见 §6.4，属已知问题，不是新故障）。
5. 记住两件事再动手：**生产库现在有 156592 字节未 checkpoint 的 WAL，且没有任何 0007 之后的备份。** 先做备份，再谈改动。
6. 不要相信这类“过期结论”：`0007-release-record.md` §11.2、`PROJECT_HANDOFF.md` §0.2 关于 P1.3 的说法（§6.1 已逐条纠正）。

**如果继续开发，请先执行 Phase 2 audit，不要直接修改代码。**
