# 拾词 · 项目当前状态（PROJECT_STATUS_CURRENT）

> **这是接手本项目的第一阅读文件。** 读完本文后，按 §9 的文档地图继续深入。
> 本文只记录**当前事实**与**入口**，不重复架构约束与计划细节；事实与代码冲突时**以代码/数据库实测为准**。

| 项 | 值 |
|---|---|
| 更新时间 | 2026-09-23 |
| 当前版本 | `1.0.0`（三处版本号均未改：`backend/pyproject.toml`、`frontend/package.json`、`backend/app/main.py` 的 `FastAPI(version=...)`）——**"V1.2" 是分支名与阶段名，不是已发布版本**，尚未合并到 `main`、尚未打 tag |
| 当前分支 | `feat/v1.2-phase1-safe` |
| 最近功能代码 / 文档冻结起点 | `10dcfa5`（S-2 写请求同源校验）/ `0d20a51`（其后的文档冻结提交）/ **`c3a6106`（S-1 管理员二次认证，`feat(v1.2): require the admin's own password (S-1)`）**；实时 HEAD 以 `git rev-parse HEAD` 为准 |
| 上游 | `origin/feat/v1.2-phase1-safe`；S-1 提交**仅在本地**（未 push、未打 tag） |
| 安全基线分支 | `recovery/v1.1-guarded`（2026-09-22 数据事故后的安全基线） |
| 工作区 | **未收口，且含他人未交付的改动**：①S-1 的代码/测试/设计文档已提交（`c3a6106`）；②**并发协作者**修订中的规划文档（`PROJECT_ARCHITECTURE.md`、`PROJECT_ROADMAP.md`、`PROJECT_STATUS_CURRENT.md`、`PROJECT_HANDOFF.md`、`AI_DEVELOPMENT_GUIDE.md` 均**未提交**，内容是 Phase 2.9 规划）；③**本目录内出现的他人前端改动**（`frontend/src/api.ts`、`frontend/src/pages/ImportPage.tsx` 修改 + 未跟踪 `frontend/src/api.test.ts`、`frontend/src/pages/ImportPage.test.tsx`；内容是 OCR 请求超时修复，归属另一个独立 worktree 的任务）——**未提交、未合并、未回滚**；④未跟踪产物 `docs/PROJECT_STATUS_V1.2.md`、`backend/.pytest-tmp-codex-*`。**不要顺手提交或回滚②③④** |
| 门禁状态 | `scripts/check.ps1` → **exit 0**（见 §3）；后端 **408 passed** |
| 运行实例 | `127.0.0.1:8000` 单 worker 进程**启动于 12:27，早于 `c3a6106` 与本批 G5**。因此必须把两件事分开读：**S-1 与 G5 的代码与测试都已完成并提交**（后端 408 passed、设计文档齐备），但**当前运行实例尚未重启，两者在该进程上都尚未生效**。重启命令：`stop-vocab.bat` → `start-vocab.bat`（无新 migration，其中 `alembic upgrade head` 是空操作）。本批**未重启实例、未触碰生产库**（2026-09-24 Batch 11：F-1/F-7 的浏览器验收走的是独立端口的 staging 副本，**仍未重启本实例**；生产库三文件指纹验收前后逐字节一致） |
| 冻结基线 | 本文件描述的代码状态已通过全量门禁；**数据侧**另有一份 0007 verified backup 与重录基线（见 §4） |

---

## 1. 项目定位

**拾词是一个"数据自主、AI 辅助、可长期持有"的英语词汇学习系统。**

它不是传统背单词软件：传统软件的核心循环是"给出中文释义 → 记住它"，学习对象是释义文本本身。拾词的核心假设是**词义不是被记住的标准答案，而是在多次真实语境中逐渐长出来的东西**——系统给出的 anchor（提取线索）只是线索，**不替代完整中文释义**：

```
英文词形 → anchor（线索）→ 主动提取（1/2/3 评分）→ 阅读中再次遇见 → 语义丰满
```

数据仍然只落在一个**自己掌控的 SQLite 数据库**里。三条不可让渡的红线：

1. **完整中文释义永久保留**（`source_meanings` / `source_raw` / OCR 原文 / 原始图片），AI 永远不得覆盖，任何时候都能从 anchor 回到完整释义。
2. **未经人工确认的内容不进词库**（`import_candidate` 必须人工确认才成为 `lexicon_entry`）。
3. **每次复习都留痕**（每次复习写一条 `review_event`；累计数字只是可由事件重算的缓存）。

### 1.1 当前技术栈

| 层 | 现状 |
|---|---|
| **Frontend** | React 19 + Vite + TypeScript、React Router、TanStack Query、Lucide、自定义 CSS（`frontend/src/styles.css`）；测试为 Vitest + Testing Library |
| **Backend** | FastAPI（Python）+ SQLAlchemy 2 + Pydantic v2 + Alembic；8 个 router、**46 个业务端点 + `GET /api/health`**（其中非安全方法 **30 个**）；口令哈希 pwdlib(Argon2id) |
| **Database** | **SQLite（WAL）**，`data/vocab.db` 是唯一事实来源；revision **0007**（迁移链 0001→0007 线性）；18 表 / 26 物理外键；单写者 → **uvicorn 必须单 worker** |
| **Authentication** | **服务器端会话 + HttpOnly Cookie**：Cookie 名 `shici_session`（`HttpOnly` + `SameSite=lax` + `Path=/` + host-only 无 `Domain`，`Secure` 由 `VOCAB_COOKIE_SECURE` 控制）；数据库只存 token 的 SHA-256；**账号由管理员/CLI 创建，不开放注册、不接 OAuth** |
| **Deployment 方向** | 当前：本机 Windows 应用，`scripts/start-vocab.ps1` 单 worker 监听 `127.0.0.1:8000`，同时托管 API 与 `frontend/dist`。目标：**Caddy + HTTPS + systemd + PWA**（Phase 3/4，尚未开始） |

---

## 2. 当前阶段：**Phase 2.8「认证收尾与工程基线修复」收尾阶段**

Phase 2（认证体系，2.1–2.7）此前已完成。Phase 2.8 于 2026-09-23 分五批推进（Batch 0 / 0.5 / 1 / 2A / **3**），**前五批均已完成，阶段本身尚未收口**：

### 2.1 基础系统（已完成）

- **用户系统**：`user` / `user_settings` / `user_lexicon`；管理员与普通用户两种角色；账号由 `POST /api/users`、CLI `create-user`、`promote`、`set-password` 管理。
- **Cookie Session 认证**：登录（四种失败**响应完全一致** + dummy Argon2 消除时间侧信道）、登出（幂等且匿名安全）、会话列表 / 单台撤销 / 批量撤销、绝对过期 + 闲置超时、启动与 CLI 清理。
- **多用户隔离**：归属**只来自会话**（`api/deps.py` 的 `CurrentSession` / `CurrentUser` / `AdminUser` 是唯一入口，任何端点都不接受客户端 `user_id`）；越权与不存在统一 **404**；`services/userdata.py` 是 owner-scoped 访问层；`test_authz.py` 维护 IDOR 矩阵。

### 2.2 安全与工程基线（已完成）

| 项 | 状态 |
|---|---|
| **verified backup（Batch 0 / T6）** | `data/backups/post-0007-verified-20260923-001237-vocab.db`（589824 B，sha256 `21d3d821…`），用 **SQLite online backup API** 从只读源生成，`verify_backup.py` 判定 **VERIFIED BACKUP** |
| **baseline 重建（Batch 0 / T5）** | `data/recovery/baseline.json`（17317 B，sha256 `81c370cf…`），从上述备份重录；旧基线**归档保留**：`baseline.prior-attempt-20260923-001325.json`（0003 时代）、`…-111637.json`（首个 0007 基线） |
| **`check.ps1` 门禁恢复** | 由"必然失败"恢复为 **exit 0**（Batch 0 重建基线 + 同步修改 `test_verified_db.py`） |
| **校验口径稳定化（Batch 0.5）** | `tools/verified_db.py`：运行期可变列退出"行身份"（`IGNORED_COLUMNS`，六张表 50→22 列）+ 行容忍表 `ROW_TOLERANT_TABLES={user_session, user_lexicon}`；正常使用不再误报，且安全列（`token_hash`、归属与桥接列）仍严格校验。证据 `data/recovery/batch05-verifier-evidence.json` |

### 2.3 功能（已完成）

| 项 | 状态 |
|---|---|
| **F-1 登录设备管理** | 设置页「登录设备」区：列出每台会话的 `user_agent` / 最近活动 / 登录时间 / 到期时间；**当前设备不提供撤销按钮**；单台撤销与"退出其它全部设备"**都必须输入当前口令**；**绝不渲染 token / token_hash** |
| **F-7 自助修改密码** | 设置页「修改密码」区：校验当前口令 + 两次新口令一致（≥8 位、不得与旧密码相同）；成功后服务端撤销全部会话，前端回到登录页并说明原因 |
| **S-1 管理员敏感操作二次认证** | `POST /api/users`、`PATCH /api/users/{id}` 的**每一次调用**都要管理员输入**自己的当前口令**（复用 `_require_password`；零 schema、零新配置）；提交 **`c3a6106`**。失败行为固定：口令错 400、无口令 422、预算尽 429 + `Retry-After`、非管理员 403、未登录 401；**校验通过前不改任何账号与会话**；两个端点与 `GET /api/users` 的响应都带 `no-store`；口令/token/`token_hash` 不进响应与审计。设计 `docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md`，测试 `backend/tests/test_admin_reauth.py`（27 项）。**前端仍无管理员管理入口，本次未建页面** → 见 §5.2 与 §6 风险 #29 |
| **S-2 CSRF 防护** | 见 §5.1 |
| **G8 每日新词数生效** | 「每日新词目标」现在真的限制学习队列：当日额度 = 当天（UTC）`review_event.status_before = 'new'` 的**不同词条数**，所以多次请求、复习后再请求都不会叠加；用户级 `daily_new_words` 封顶当日总量，`user_lexicon.daily_new_words` 各自限制本词库（两层取 `min`）；`limit` 仍是整份队列的长度上限，**到期与 `weak` 词优先占用它，且不被新词额度削减**。`GET /api/study/today` 新增只读字段 `daily_new_words: {target, consumed_today, remaining}`。零 schema、零 migration、未改调度算法；设计 `docs/V1.2-PHASE2.8-E-DAILY-NEW-WORDS-DESIGN.md`，测试 `backend/tests/test_daily_new_words.py`（25 项） |
| **G5 每用户会话数量上限** | `VOCAB_MAX_SESSIONS_PER_USER`（默认 10，`0` = 不限制）；超限时**新登录仍成功**：先清理该账号的已失效行，再按 `COALESCE(last_seen_at, created_at)` 最早撤销**最久未活动**的存活会话（并列取 id 最小），**刚签发的当前会话按 id 硬排除**；每条淘汰写一条不含凭据的 `session_evicted` 审计。零 schema、零 migration、不引入 `revoke_reason`。提交 **`9fef2a4`**；设计记录 `docs/V1.2-PHASE2.8-C-SESSION-LIMIT-DESIGN.md`，测试 `backend/tests/test_session_limit.py`（16 项） → 见 §5.3 |

### 2.4 Phase 2.8 尚未完成

| 项 | 说明 |
|---|---|
| G6 / S-6 `history_event` 保留策略 | **已实现并在副本验收（2026-09-23 Batch 7 / T10）**：365 天、四类事件；`preview --plan` 固定候选 ID、UTC cutoff 与逐行 hash，`apply --plan --confirm <运行 ID>` 在 `BEGIN IMMEDIATE` 内只按明确 ID 删除并核对数量，提交后核验通过才发布 committed 凭证。**生产 `data/vocab.db` 从未执行 preview/apply**，而且**当前生产库没有到期候选行**（12 条 `history_event` 全在 365 天窗口内），未来生产清理必须先有到期候选并针对具体计划与运行 ID 获批、在维护窗口内执行——因此这条在"尚未完成"表里只保留生产侧 |
| T8 / E-3 副本三账号验收 | ✅ **已于 2026-09-23（Batch 8）完成**：`data/recovery/t8-three-user-report-20260923T134500Z.json`，**126/126 PASS**、`verified: true`、`failures: []`（新文件名；旧双账号证据未覆盖）。生产 `data/vocab.db` 只被只读复制，验收前后与其 WAL/SHM 逐字节一致——本表不再有它的缺口 |
| T9–T17 卫生项 | **T11（T-3 未知 `/api/**` → 404 JSON）与 T17（`.env.example` 补 `VOCAB_DATABASE_PATH`）已于 2026-09-23 Batch 5 完成**；**T12 死代码的低风险部分已于 Batch 9 清理**；**T-14 `data/staging/` 残留已于 Batch 10 处置**；未完成部分见 §6（T-12 死代码、T-13 版本号、T-14 staging 残留） |
| T-7 浏览器人工验收 | **F-1/F-7 已于 2026-09-24 Batch 11 在真实浏览器（Chrome）里对独立端口的 staging 副本实例完整点过一遍：13/13 步骤通过**（设备列表与「当前设备」标记、错误口令被拒且会话不变、正确口令撤销其他设备、429 等待提示与倒计时、改密后全部会话失效并回登录页），证据 `data/recovery/dod4-browser-acceptance-20260923T162528Z.json` + `test-artifacts/dod4-browser-20260923T162528Z/`。**S-1 无前端入口，不适用浏览器点击**；建议口径（F-1/F-7 浏览器验收 + S-1 后端契约/安全测试/T8 副本验收）见 `docs/V1.2-PHASE2.8-F-DOD4-BROWSER-ACCEPTANCE.md`，**待负责人确认** |

---

## 3. 当前测试与门禁状态

| 检查 | 命令 | 当前结果 |
|---|---|---|
| 后端测试 | `cd backend; .\.venv\Scripts\python.exe -m pytest tests -q` | **522 passed**（38 个测试文件；含 S-1 的 `test_admin_reauth.py` 27 项、G5 的 `test_session_limit.py` 16 项、Batch 5 的 `test_spa_fallback.py` 16 项、Batch 6 的 `test_daily_new_words.py` 25 项、Batch 7 的 `test_history_retention_apply.py` 39 项与 Batch 8 的 `test_staging_three_user_check.py` 9 项，本批新增 1 项 recorder 竞态守卫） |
| 后端 lint | `cd backend; .\.venv\Scripts\python.exe -m ruff check --no-cache app tests`、`… ruff check tools` | All checks passed（两条） |
| 前端测试 | `cd frontend; npm test` | **32 passed / 7 files** |
| 前端类型 / lint / 构建 | `npm run typecheck` / `npm run lint` / `npm run build` | 全部通过 |
| **全量门禁** | `powershell -File scripts\check.ps1` | **exit 0**（`All checks passed.`）——这一条覆盖以上全部 + 下面两项 |
| 测试隔离取证 | `backend\.venv\Scripts\python.exe tools\prove_test_isolation.py` | `data/` 全量指纹**零变化**（S-1 复验实测 **108 个文件**，`untouched: True`，证据 `test-artifacts/pytest-isolation-evidence.json`）+ 全量测试通过 |
| 备份校验 | `backend\.venv\Scripts\python.exe tools\verify_backup.py data\vocab.db --baseline data\recovery\baseline.json` | **VERDICT: VERIFIED BACKUP**（17 张业务表逐行比对全部 `ok`） |

> **唯一的门禁命令是 `scripts/check.ps1`**。改动后请用它收尾，不要只跑单项。

---

## 4. 数据与备份状态（2026-09-23 只读实测）

| 项 | 值 |
|---|---|
| 生产库 | `data/vocab.db`（SQLite，WAL） |
| alembic revision | `0007_bridge_foreign_keys`（唯一 head） |
| 表 / 物理外键 | **18 / 26** |
| 完整性 | `integrity_check = ok`、`foreign_key_check` = **0 违规** |
| 用户 | **1 个**：`admin`（role=admin、is_active=1、已设 Argon2id 口令） |
| 核心数据量 | `lexicon` 1、`lexicon_entry` 19、`user_word_state` 19、`review_event` 10、`article` 2、`article_word_exposure` 16、`import_candidate` 19、`word` 19（V1.1 遗留） |
| **0007 verified backup** | `data/backups/post-0007-verified-20260923-001237-vocab.db`（**已建立**，D-2 关闭） |
| 项目基线 | `data/recovery/baseline.json`（从上述备份录制，已含新的行身份口径） |
| 备份**策略** | ⚠ 仍是"启动时 + 手动"两个触发点，无定时/保留/异地（D-4，Phase 4） |
| `data/staging/` | **顶层 17 个文件 + 12 个一级目录（整棵树 86 个文件 / 40 个目录，含只读盘点自身为 WAL 库生成的 `-shm`/`-wal`），已于 2026-09-23（Batch 10 / T14）逐个盘点并分类处置**（S-5）：删除 6 个文件（两次**中止**的 G6 演练副本及其 `-shm`/`-wal`）；保留 4 个含真实数据的副本（`migration-rehearsal-0006.db`、`v1.1-realdata-migration-test.db`、`v1.3-acceptance.db`、`fresh-clone-0003-to-head.db`，均被证据/工具/测试引用）、G6 演练采纳副本、4 个 Batch 0.5 JSON 证据、`backups/2026-09-22-vocab.db` 与 7 个 T8 运行目录（各自被自己的报告引用）。逐项理由与哈希见 `data/recovery/t14-staging-disposal-20260923T155046Z.json`（§addendum 含整棵树 86 个文件的逐项哈希与处置后复核：7 个 T8 运行副本仍可读、`integrity_check=ok`，其中 5 个的报告写了行级指纹且逐表一致，2 个中止运行本来就没写指纹段） |

> **三级数据库环境（强制）**：Level 1 = pytest 临时目录；Level 2 = `data/staging/*.db`（唯一允许拿真实数据做演练的地方）；Level 3 = `data/vocab.db`（**生产，开发期禁止迁移、禁止写入**）。完整规则见 `PROJECT_ARCHITECTURE.md` §4.5。

---

## 5. 当前安全状态

### 5.1 已完成：S-2 CSRF 防护（写请求同源校验）

设计文档：`docs/V1.2-PHASE2.8-B-CSRF-DESIGN.md`；实现：`backend/app/csrf.py`（纯函数）+ `backend/app/main.py::_same_origin_write_check`；测试：`backend/tests/test_csrf.py`（45 项）。

**三层防御，互不依赖**：

| 层 | 机制 | 挡住 | 挡不住 |
|---|---|---|---|
| 1 | Cookie 的 **`SameSite=Lax`** | 跨站表单 / 跨站 fetch / `<img>`·`<iframe>` 携带 Cookie | **同站不同源**（子域）、Chromium "Lax+POST" **2 分钟窗口**、未来放宽 SameSite、旧浏览器 |
| 2 | **请求体只接受 `application/json`**（FastAPI `strict_content_type` 默认） | 跨站 HTML 表单造不出合法写请求（→ 422） | 两处 **multipart** 端点（`POST /api/imports`、`.../images`）；未来任何接受表单体的端点 |
| 3 | **写请求同源校验**（`Origin`，缺失时回落 `Referer`；比对 `Host` 的 host:port） | 以上全部；不依赖内容类型与浏览器行为 | XSS（同源脚本可伪造请求头） |

要点：覆盖 **30 个非安全端点**；`GET`/`HEAD`/`OPTIONS` 放行；只比 host:port，**不比 scheme**（Caddy 终止 TLS）；两个头都缺 → **403**（除非 `VOCAB_CSRF_ALLOW_MISSING_ORIGIN=true`）；`null` 永不接受；**不豁免 `POST /api/auth/login` 与 `/logout`**；中间件在**认证之前**拒绝（被拒请求不解析会话、不写审计）。

### 5.2 已完成：S-1 管理员敏感操作二次认证（2026-09-23）

设计文档：`docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md`（含现状审查、方案取舍、失败矩阵、边界与 DoD）；实现：`backend/app/api/auth.py`（复用既有 `_require_password`，守卫本体**一行未改**）+ `backend/app/schemas.py`（两个请求体新增必填 `current_password`）+ `backend/app/main.py`（`no-store` 前缀扩到 `/api/users`）；测试：`backend/tests/test_admin_reauth.py`（27 项）。

| 项 | 内容 |
|---|---|
| **保护范围** | `POST /api/users`、`PATCH /api/users/{id}` 的**每一次调用**（建号含建管理员 / 改显示名 / 改角色 / 停用·启用 / 改他人密码 / 空更新）都要管理员输入**自己的当前口令**——不按操作种类分档 |
| **失败行为** | 未登录 **401**；非管理员 **403**（**先于**契约校验）；缺 `current_password`/空串/`null` **422**；口令错 **400** `{"detail":"当前密码不正确"}` + 1 条 `reauth_failed`（`payload.action` = `create_user` / `update_user`，固定枚举）；预算耗尽或闸门饱和 **429** + `Retry-After` 且**不写事件**；成功清零预算 |
| **无副作用** | 校验通过前不读目标、不写业务行；拒绝时目标账号的 `password_hash`/`role`/`is_active`/`display_name` 不变、其**存活会话不被撤销**（测试断言受害者仍能用原会话访问 `/api/auth/me`），且不产生 `user_updated` / `session_revoked` / `user_password_changed` |
| **不可观察面** | 口令错时"目标 id 存在"与"不存在"返回**逐字节相同**的 400 |
| **缓存** | 两个端点与 `GET /api/users` 的全部响应（含 401/403/404/409/422/429）带 `Cache-Control: no-store` |
| **秘密边界** | 口令、token、`token_hash` 不进任何响应与审计；`user_updated.fields` 不含 `current_password` |
| **未做** | 前端**没有**管理员用户管理入口（实测），本批**未新建页面**；`PUT /api/settings` 的实例级字段仍无二次认证（见 §6 风险 #29）；不引入第二因素、不做 sudo 免密窗口、不加 migration |

**未关闭的对照**：S-2 关闭的是**跨站**这条路径；**会话被盗**这条路径由 S-1 收窄。同源校验**不能替代**二次认证，两者互补。
（另有 3 个用户侧端点已有口令守卫：`POST /api/auth/password`、`POST /api/auth/sessions/revoke`、`DELETE /api/auth/sessions/{id}`——合计 **5 个**受保护端点。）

### 5.3 已完成：G5 每用户会话数量上限（2026-09-23）

设计记录：`docs/V1.2-PHASE2.8-C-SESSION-LIMIT-DESIGN.md`（含设计 ↔ 代码差异 D-1…D-16 的逐条裁定）；实现：`backend/app/services/auth.py`（`create_session` → `enforce_session_limit` + `_purge_dead_user_sessions`，均为新函数；`prune_sessions` / `session_is_live` / `list_user_sessions` **未改**）+ `backend/app/config.py`（`max_sessions_per_user`）+ `.env.example`；测试：`backend/tests/test_session_limit.py`（16 项）。

| 项 | 内容 |
|---|---|
| **上限** | `VOCAB_MAX_SESSIONS_PER_USER`，默认 **10**；`0` = 不限制（此时本功能整段跳过，死行仍由启动/CLI 的 `prune_sessions` 处理）；非法值回落 10，负值等同 0 |
| **超限动作** | **不拒绝新登录**（拒绝会造出"设备丢了 → 登不上 → 撤销不了"的死锁）：登录成功后若该账号存活会话数超限，**先清理已失效行**，再撤销最久未活动的那条，直到 ≤ 上限 |
| **排序** | `COALESCE(last_seen_at, created_at)` 最早的先淘汰（是"最近一次活动"，不是"创建时间"）；**并列取 id 最小**者（确定性） |
| **当前会话** | 刚签发的会话**按 id 硬排除**，永不淘汰（不是"排序上通常不会选中"） |
| **审计** | 每条淘汰一条 `session_evicted`：`user_id`=账号、`entity_type="user_session"`、`entity_id`=被撤销会话、`payload={"reason":"session_limit","limit":N}`；**不含 token / token_hash / 口令 / 客户端文本** |
| **存储语义** | 淘汰 = 写 `revoked_at`（不是删除行）；被挤掉的设备下一次请求即 **401**；行本身要等 `prune_sessions` 才消失 |
| **零 schema** | 无新列、无 migration、**未引入 `revoke_reason`**（因此 UI 仍无法解释"为何被登出"，只能靠审计事件） |
| **与内存限流不同** | 它**读数据库行**，不是内存计数：重启、多 worker 都不会让上限失真（与 S-4 的取舍相反，是有意的） |
| **验收证据** | 先补测试：实现前 **13 failed / 3 passed**；实现后 **16 passed**；全量后端 **408 passed**（392 → +16，无回归） |

---

## 6. 当前已知风险（完整清单，不删除既有条目）

> 严重度：🔴 阻塞上线 / 🟠 应尽快 / 🟡 可计划 / ⚪ 记录备查。更完整的登记册见 `PROJECT_ROADMAP.md` §4。

| # | 风险 | 级别 | 影响 / 归属 |
|---|---|---|---|
| 1 | ~~**S-1 管理员敏感操作无二次认证**~~ → ✅ **已关闭（2026-09-23，Batch 3）** | 🟠→⚪ | 原风险：管理员会话被盗即可建**持久后门管理员**、改他人密码/角色、停用账号。现两个端点每次调用都要求管理员自己的当前口令，且**校验通过前不改账号与会话**（见 §2.3、§5.2）。剩余：恢复路径仍可被短暂封锁（#30，S-3）与 `PUT /api/settings` 无二次认证（#29） |
| 2 | **Phase 4 部署必须配 `uvicorn --proxy-headers --forwarded-allow-ips <代理地址>`** | 🟠 | 不配则两处同时出错：①所有用户共用一个 IP 限流桶（S-4）②TLS 终止后裸域名 `Host` 被推导为 80 端口 → https 来源的写请求被 CSRF 校验 **403**。必须写进部署脚本 |
| 3 | **公网部署未开始**（D-1） | 🔴 | 仅监听回环地址，无 `deploy/`、无 HTTPS、无 CI；产品无法离开本机 |
| 4 | **PWA / 移动端未开始**（F-2…F-6） | 🟠 | 无 manifest/SW/图标；≤900px 隐藏单词详情面板等移动端缺陷仍在 → 手机上不可用 |
| 5 | **XSS 不在 CSRF 防御范围** | 🟠 | 同源脚本可同时伪造请求与请求头；当前前端无 `dangerouslySetInnerHTML`/`innerHTML`，但这条边界必须明说 |
| 6 | **`VOCAB_CSRF_ALLOW_MISSING_ORIGIN=true` 会让所有客户端一起失去第三层** | 🟡 | 脚本客户端逃生口，默认关闭；开启前须读设计文档 §6 |
| 7 | **限流与闸门状态在内存**（S-4） | 🟠 | 重启清零；**多 worker 会让等效阈值 ×worker 数**；单 worker 是架构硬约束 |
| 8 | ~~**`data/staging/` 残留含真实数据的副本 + 测试口令**（S-5）~~ → ✅ **已逐个处置（2026-09-23 Batch 10 / T14）** | ⚪ | 只删了可重建且无有效引用的 6 个文件（两次中止的 G6 演练副本）；其余按证据/工具默认目标/测试引用保留，并在记录里逐项注明理由。**残余**：6 个 T8 运行副本与 3 个 Batch 0.5 的旧 JSON 仍留在 `data/staging/`，若负责人认为失败运行的排查已结束，可再处置一批（记录 §classification 已列出） |
| 9 | **备份策略不适常常驻服务**（D-4） | 🟠 | 仅"启动时 + 手动"；`create_backup` 当日同名即跳过（曾导致"备份看似成功实为旧文件"）→ 需定时 + 保留 + 异地 + 失败可见性 |
| 10 | **`history_event` 无保留策略**（S-6） | 🟡 | 审计表持续增长（约 129 B/行） |
| 11 | **未知 `/api/**` 路径返回 200 HTML**（T-3） | 🟡 | SPA 兜底路由；客户端错误处理会拿到 HTML（`POST` 则 405） |
| 12 | **`word` 表与 `user_word_state` 双轨并存**（T-1） | 🟡 | 两套 id 命名空间**刻意不互相回退**，混用得 404；Phase 6 退场（高风险） |
| 13 | **词频数据缺失**（P-1） | 🟠 | `frequency_rank` 全 NULL，选词回落 `sequence`/`id`；**不得用 AI 编造**，等外部词频文件 |
| 14 | **复习算法是固定天数阶梯**（P-2） | 🟠 | 不随个人表现自适应；Phase 5，且需先设计 + migration |
| 15 | **连续天数在 Python 中重算**（T-6） | 🟡 | 取最近 1000 条 `review_event`，超过后会算错；Phase 5 |
| 16 | ~~**三账号副本验收未做**（T8 / E-3）~~ → ✅ **已关闭（2026-09-23 Batch 8）** | ✅ | `data/recovery/t8-three-user-report-20260923T134500Z.json`：126/126 PASS、`verified: true`、`failures: []`；矩阵含 409 守卫、跨用户 404 等价性、实例级门槛、会话与每日新词额度不串号 |
| 17 | **版本号未更新**（E-4） | 🟡 | 三处仍 `1.0.0`；"V1.2" 只是阶段名；影响打 tag 的口径（见 §7 与本文末） |
| 18 | **无 CI**（E-5） | 🟡 | 门禁全靠人工执行；Phase 6 |
| 19 | ~~**`.env.example` 仍缺 `VOCAB_DATABASE_PATH`**（T-7）~~ → ✅ **已关闭（2026-09-23 Batch 5 / T17）** | ⚪ | 已补：说明它用于明确选择数据库环境（留空即 `<数据目录>/vocab.db`），并写明开发与测试**不得指向生产库 `data/vocab.db`**。三级数据库环境切换依赖它，缺文档易误配 |
| 20 | **死代码**（T-2） | ⚪ | ~~`helpers.word_dict`、`schemas.py::WordSummary`~~ → ✅ **已于 2026-09-23 Batch 9（T12）删除**；**剩余**：`services/words.py::apply_learning_update`（仅 `test_import_flow.py` 引用）与 `schemas.py::ORMModel`（已无使用者），随 `word` 表退场（Phase 6）一并清理 |
| 21 | **文档历史快照未逐条回填** | 🟡 | `PROJECT_ROADMAP.md` §2.2（ahead 33）、§4.6 E-1 等仍是 2026-09-22 快照；`docs/PROJECT_STATUS_V1.2.md` 为**未跟踪**的他人审计产物，与现状可能冲突。**当前事实以 `PROJECT_HANDOFF.md` §8 与本文为准** |
| 22 | **响应体全为手写 dict，无统一出口**（T-4，已接受） | ⚪ | 应对方式是 IDOR 测试矩阵覆盖，不重构为 Pydantic |
| 23 | **时区语义**（T-5）：SQLite 不存时区，`DateTime(timezone=True)` 读出为 naive | ⚪ | 现有代码同源比较无症状 |
| 24 | **CSRF 严格比较的取舍**：同主机**不同端口**即不同源 | ⚪ | 有意为之（避免"同主机另一服务"成为缺口）；出现误拒时用 `VOCAB_CSRF_TRUSTED_ORIGINS` |
| 25 | **DNS rebinding 不由本机制拦截** | ⚪ | 但会话 Cookie 是 **host-only**，不会发给攻击者域名 → 请求到达时无凭据 |
| 26 | **运维观感问题** | ⚪ | 启动脚本会截断 `data/logs/server-out.log`；`data/server.pid` 记的是启动器 PID 而实际监听者是子进程（`stop-vocab.ps1` 以端口为准，可正确定位）；桌面快捷方式存在已知乱码缺陷（`0007-release-record.md` §12.2） |
| 27 | **可选：`revoke_reason`** | ⚪ | UI 无法解释"某设备为何被登出"；需 migration 0008，默认不做 |
| 29 | **`PUT /api/settings` 的实例级字段仍无二次认证** | 🟠 | 管理员可改 DeepSeek Key / Base URL / Model 与 OCR 配置：把 `deepseek_base_url` 指向攻击者端点即可外泄后续请求内容。**有意不在 S-1 范围**——该端点同时服务普通用户保存自己的偏好，无条件加口令会打断日常保存，条件加口令需另行设计（理由见 `V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md` §2.4） |
| 30 | **恢复路径可被短暂封锁**（S-3） | 🟡 | 持被窃会话者可故意烧掉 re-auth 预算，使合法管理员/用户在窗口（默认 300 s）内无法执行任何受口令保护的敏感操作。缓解：窗口短、**登录与日常学习不受影响**、CLI `set-password`/`promote` 逃生口（不经 API）、每次失败都有审计。S-1 使这条风险的作用面扩大（原来只有用户侧改密，现在含管理员用户管理） |
| 31 | **被挤掉的设备在 UI 上得不到解释**（G5 的固有代价） | 🟡 | 达到 `VOCAB_MAX_SESSIONS_PER_USER`（默认 10）后，最久未活动的那台设备会**静默登出**；前端只知道"会话失效"，唯一线索是 `history_event` 里的 `session_evicted`。根治需 `revoke_reason`（migration 0008，未做）。相关取舍：持口令者可用"反复登录"制造登出骚扰（设计记录 §3-3 已论证：与其拒绝新登录造成死锁，宁可接受可恢复的骚扰） |
| 32 | **会话上限与内存限流的取舍相反** | ⚪ | 上限**读数据库行**，因此重启/多 worker 都不会让它失真；而登录/二次认证的限流仍是内存计数（#7 / S-4），**单 worker 约束不变**。两者不要混为一谈 |

---

## 7. 下一阶段计划（推荐顺序）

1. ✅ **S-1 管理员二次认证已完成（2026-09-23，Batch 3）**：两个端点每次调用都要求管理员自己的当前口令（见 §2.3、§5.2）。`ReauthAction` 字面量已扩展为 5 个动作（`change_password` / `revoke_sessions` / `revoke_session` / `create_user` / `update_user`）。
   **本次未做**：前端管理员管理页面（前端至今没有该入口，见 §2.3 与 §6 风险 #29）；`PUT /api/settings` 的实例级字段二次认证。
1b. ✅ **G5 每用户会话数量上限已完成（2026-09-23，Batch 4）**：默认 10、`0` = 不限制；超限时先清理已失效行、再淘汰最久未活动的存活会话，当前会话永不淘汰（见 §2.3、§5.3）。
   **本次未做**：`revoke_reason`（UI 仍无法解释"为何被登出"）；前端对被挤掉的提示。
2. **Phase 2.8 卫生项打包**（低风险，可并行）
   ✅ **T-3（未知 `/api/**` → 404 JSON，T11）与 T-17（`.env.example` 补 `VOCAB_DATABASE_PATH`）已于 2026-09-23 Batch 5 完成**；**G8「每日新词数」生效已于 Batch 6 完成**；**T-10 `history_event` 保留策略（G6）已于 Batch 7 实现并在副本验收，生产执行仍未批准**；**T8 副本三账号验收已于 Batch 8 完成**（DoD 6 达成）；**T-12 死代码的低风险部分已于 Batch 9 清理**；**T-14 `data/staging/` 残留已于 Batch 10 处置**；**仍待做**：T-13 版本号决策（E-4）。
3. **Phase 3 · PWA 与移动端可用性**（先做设计决策）
   `manifest` + Service Worker + 图标（`/api/**` **永不缓存**）；**F-2 移动端单词详情面板**（当前 ≤900px 直接 `display:none`，违反"完整释义永远可查"的可见性承诺，是本阶段最高风险项，需先定交互形式：抽屉 / 贴底卡片 / 独立路由）；F-4 safe-area；F-6 底部导航。
4. **Phase 4 · 生产部署上线**（建议在 Phase 3 之后）
   Caddy + HTTPS + systemd；`VOCAB_COOKIE_SECURE=true`；**固化 `--proxy-headers --forwarded-allow-ips`**（风险 #2）；备份定时器 + 保留 + 异地 + 恢复演练。
5. （更远）Phase 5 学习算法升级（需 migration，先设计 + staging 演练）、Phase 6 平台化（`word` 表退场 + CI）。

---

## 8. Agent 协作规则（完整版见 `docs/AI_DEVELOPMENT_GUIDE.md`）

**任何 Agent 开始工作前必须阅读**（按此顺序）：

1. `docs/PROJECT_ARCHITECTURE.md` —— 什么允许、什么禁止（**改代码前必读**）
2. `docs/PROJECT_ROADMAP.md` —— 往哪走、下一步做什么、判定标准
3. `docs/PROJECT_HANDOFF.md` —— 当前事实、认证模型、已知限制、复验命令
4. `docs/PROJECT_STATUS_CURRENT.md`（本文）—— 接手入口与冻结基线

**禁止**：未设计直接修改架构 / 未确认就改数据库 schema / 绕过测试 / 删除安全验证 / 重写历史 migration / 改动 `data/vocab.db`（开发期）。

**完成任务后必须**：更新 `PROJECT_HANDOFF.md`（与本文的状态块）→ 运行 `scripts/check.ps1` 至 exit 0 → 提交 commit（小步、message 说明目的）。

---

## 9. 文档地图与权威顺序

| 文档 | 角色 | 何时读 |
|---|---|---|
| `docs/PROJECT_STATUS_CURRENT.md`（本文） | **接手入口 / 冻结基线** | 第一份 |
| `docs/PROJECT_ARCHITECTURE.md` | **架构约束**（什么允许、什么禁止） | 改任何代码前 |
| `docs/PROJECT_HANDOFF.md` | **当前事实的唯一权威**（认证模型、DB 状态、限制、命令） | 动手前 + 收尾时更新 |
| `docs/PROJECT_ROADMAP.md` | **计划**（阶段、任务、DoD、风险登记册） | 决定做什么、验收标准 |
| `docs/V1.2-PHASE0-AUDIT-AND-DESIGN.md` | V1.2 设计基线（历史依据） | 需要原始设计理由时 |
| `docs/V1.2-PHASE2.*-*.md` | Phase 2 各子阶段设计/审计 | 对应主题 |
| `docs/V1.2-PHASE2.8-B-CSRF-DESIGN.md` | S-2 设计与边界 | 触碰 CSRF/认证头时 |
| `docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md` | **S-1 管理员二次认证**：现状审查（两个端点的全部操作与保护）、失败矩阵、无副作用承诺、验收标准、与本文件/代码的不一致登记 | 触碰管理员端点或 re-auth 守卫时 |
| `docs/V1.2-PHASE2.8-C-SESSION-LIMIT-DESIGN.md` | **G5 每用户会话数量上限**：设计（`V1.2-PHASE2.7-A` §5）与代码现状的**差异裁定 D-1…D-16**、判定顺序、审计、边界与验收 | 触碰会话上限、淘汰或会话清理逻辑时 |
| `docs/AI_DEVELOPMENT_GUIDE.md` | AI 协作开发规范（可执行版） | 每个任务开始/结束时 |
| `docs/0007-production-migration-runbook.md`（+`-checklist`） | 生产迁移操作规程 | **任何** migration |
| `data/recovery/*` | 发布/校验/验收证据（**不入 Git**） | 查证据、复验 |

**权威顺序**：**代码与数据库实测** > `PROJECT_HANDOFF.md` > `PROJECT_ROADMAP.md` > 其它设计文档 > 本文（快照类）。
本文与代码冲突时，**以代码为准**，并请顺手更新本文。

---

## 10. 常用命令

```powershell
# 启动 / 停止（单 worker，监听 127.0.0.1:8000）
.\start-vocab.bat                      # = npm build + alembic upgrade head + 启动 + 打开浏览器
.\stop-vocab.bat                       # 以端口与进程证明已停止

# 全量门禁（唯一必跑命令；改完必须 exit 0）
powershell -File scripts\check.ps1

# 单项（调试用）
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
cd backend; .\.venv\Scripts\python.exe -m ruff check --no-cache app tests
cd frontend; npm test; npm run typecheck; npm run lint; npm run build

# 安全与数据证据
backend\.venv\Scripts\python.exe tools\prove_test_isolation.py     # 证明 pytest 不碰 data/
backend\.venv\Scripts\python.exe tools\verify_backup.py data\vocab.db --baseline data\recovery\baseline.json
backend\.venv\Scripts\python.exe -m app.cli list-users             # CLI：用户管理（口令只从 getpass 读）
```

---

## 附：Git 阶段标签

仓库**当前没有任何 tag**。Phase 2.8 出口标准（DoD）全部满足后再统一版本号（三处 `1.0.0`，roadmap D4）并决定是否打正式 tag；**S-1 完成不等于阶段完成**，因此本次**未创建 tag、未改版本号**。若现在需要一个"已知良好状态"的锚点，建议用描述性名称且**不声称阶段完成**（尚未创建、等待确认）。
