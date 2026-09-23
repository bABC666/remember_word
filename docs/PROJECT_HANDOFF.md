# 拾词项目交接说明

> 更新日期：2026-09-23（本轮更新对应 **Phase 2.8 S-1（管理员敏感操作二次认证）** 完成）
> 当前分支：`feat/v1.2-phase1-safe`；最近功能代码 `10dcfa5`（S-2）与 **`c3a6106`（S-1，本地，未 push）**，规划修订起点 `0d20a51`；实时 HEAD 以 Git 为准。
> 当前阶段：**V1.2 Phase 2.8 收尾阶段**（门禁已恢复、verified backup 与基线已建立、F-1/F-7/S-2/**S-1**/**G5 会话上限** 已交付；剩余 G6 审计保留、G8「每日新词数」、T8 三账号副本验收、T11–T17 卫生项）
> 本文用途：让新的开发者或 AI 不依赖历史对话，也能安全接手维护。
> 工作区另有一份**未跟踪**的 `docs/PROJECT_STATUS_V1.2.md`（另一次只读审计会话的产物，非本次交接内容）；本文与它无关，两者不要混用。
> **工作区当前不干净，且含他人未交付的改动**：规划文档（本文件、`PROJECT_STATUS_CURRENT.md`、`PROJECT_ROADMAP.md`、`PROJECT_ARCHITECTURE.md`、`AI_DEVELOPMENT_GUIDE.md`）有**未提交**的 Phase 2.9 规划修订；本目录还出现了一组**属于另一个独立 worktree（OCR 超时修复）的前端改动**（`frontend/src/api.ts`、`frontend/src/pages/ImportPage.tsx` + 两个未跟踪测试文件）。以上都不是 S-1/G5 的内容：**不要代提交、不要合并、不要回滚**。

---

## 当前状态

> 本块是接手者的**第一屏**：只写"现在是什么"，历史过程见 §12 与各阶段文档。每次重要提交后更新。

| 项 | 值 |
|---|---|
| HEAD | `10dcfa5` `feat(v1.2): require a same-origin write request (S-2)`（2026-09-23 12:28） |
| 分支 / 上游 | `feat/v1.2-phase1-safe` → `origin/feat/v1.2-phase1-safe`；**本地领先 4 个提交**（S-1 实现 `c3a6106` + S-1 文档收口 `acde4f8` + 笔记归位 `46e0c55` + 本批 G5），**均未 push** |
| 最近完成 | **G5 每用户会话数量上限**（`VOCAB_MAX_SESSIONS_PER_USER`，默认 10、`0` = 不限制；`services/auth.py::create_session` → `enforce_session_limit`；`backend/tests/test_session_limit.py` 16 项 + 设计记录 `docs/V1.2-PHASE2.8-C-SESSION-LIMIT-DESIGN.md`）。**本地提交，未 push** |
| 上一批 | Batch 3：S-1 管理员敏感操作二次认证（`c3a6106`）+ 其文档收口（`acde4f8`）；Batch 2A：S-2 CSRF 同源校验；Batch 1：F-1/F-7；Batch 0.5 / 0：verified backup + baseline 重建 + `check.ps1` 恢复 |
| 本批关闭的任务 | **G5**（§6.1 功能目标）、**T9**（技术任务） |
| 门禁 | `scripts/check.ps1` **exit 0**；后端 **408 passed** + ruff 全通过 / 前端 **29 passed** |
| 数据 | revision **0007**、18 表 / 26 外键、`integrity_check=ok`、`foreign_key_check=0`、1 个用户（`admin`）；0007 **verified backup** 已存在；基线已从该备份重录（**本批未触碰生产库**） |
| 运行实例 | `127.0.0.1:8000`（本机，单 worker；交付时在运行，PID 见 `data/server.pid`）。**该进程启动于 2026-09-23 12:27，早于 `c3a6106` 与本批：S-1 与 G5 的代码与测试都已完成并提交，但都尚未在此进程生效。** 让它们生效需重启（`stop-vocab.bat` → `start-vocab.bat`；无新 migration，`alembic upgrade head` 是空操作）——本批**未重启**（不触碰生产实例） |
| 下一步 | Phase 2.8 剩余：**G6 `history_event` 保留策略、G8「每日新词数」生效、T8 三账号副本验收、T11–T17 卫生项**；之后 Phase 2.9 外部公共词库（可并行 Phase 3 移动端设计） |
| 第一阅读文件 | `docs/PROJECT_STATUS_CURRENT.md`（接手入口）；协作规则见 `docs/AI_DEVELOPMENT_GUIDE.md` |

**本文件的历史内容一律保留**：§0 事故规则、§5.x 认证细节、§9 已知限制、§12 后续动作均为长期有效记录，不要因为"看起来过时"而删除；有过时的**事实陈述**请就地更正并注明原因。

---

## 0. 必读：2026-09-22 数据事故与仍然生效的安全规则

V1.2 Phase 1 期间，`data/vocab.db` 的全部业务表曾被删除。原因：**测试夹具对应用模块级 engine 执行了 `Base.metadata.drop_all(engine)`，而该 engine 实际绑定到了真实数据库。**

已恢复，但**有永久性数据丢失**（`review_event` 10 条、`article_word_lookup` 5 条、文章译文、`history_event` 6 条、`app_setting.onboarding_seen`、9 个词的 status 推进）。任何接手者请先读：

- `_INCIDENT-20260922/README.md`（本地事故现场，**不入 Git**）
- `data/recovery/*.json`（恢复与校验证据）
- `docs/V1.2-PHASE0-AUDIT-AND-DESIGN.md`（V1.2 设计基线）

**当前生效的安全规则（全部有代码或工具兜底）**：

1. 测试进程**不得**触碰真实 `data/`：`backend/app/testing_guards.py` 对「绑定 engine / 解析设置 / 破坏性 schema 操作」做 fail-fast。
2. `drop_all` 出现在任何测试夹具中都是禁止的（`tests/test_static_guards.py` 静态拦截）。
3. 破坏性操作只允许发生在带测试标识的临时目录或 `data/staging/` 副本内。
4. Alembic 测试必须在子进程里跑，显式传 `-x db_url=`，事后校验真正被迁移的文件。
5. 「备份」只有通过 `tools/verify_backup.py` 全部校验后才可称为 **verified backup**（复制成功 ≠ 可信备份）。
6. 应用启动会校验数据库 revision 与代码 head 是否一致，不一致**直接拒绝启动**（失败关闭，无警告路径）。
7. `alembic downgrade` 只允许对一次性副本执行（`alembic/env.py` 在 DBAPI 连接建立时校验最终解析路径）。

自查命令：

```powershell
backend\.venv\Scripts\python.exe tools\verify_backup.py <database>
backend\.venv\Scripts\python.exe tools\prove_test_isolation.py   # 证明 pytest 不碰 data/
backend\.venv\Scripts\python.exe tools\compare_with_source.py    # 实盘与恢复源逐字段比对
powershell -File scripts\check.ps1                               # 全量检查（见 §8 的已知例外）
```

## 0.1 三级数据库环境（强制）

| 级别 | 位置 | 用途 | 规则 |
|---|---|---|---|
| **Level 1** | pytest 临时目录 | 单元 / API / migration 测试 | 只允许存在于 pytest 临时目录 |
| **Level 2** | `data/staging/*.db` | 用**真实数据内容**演练迁移与双用户验收 | 唯一允许拿真实数据做演练的地方；可随时删除重建；不是生产 |
| **Level 3** | `data/vocab.db` | 生产 | 开发期间**禁止迁移、禁止写入** |

Level 2 工作流：

```powershell
backend\.venv\Scripts\python.exe tools\make_staging.py                  # 从已验证源克隆 + 记录迁移前基线
backend\.venv\Scripts\python.exe tools\staging_migration_check.py       # 0003 → head 并逐项断言真实数据未损坏
backend\.venv\Scripts\python.exe tools\staging_two_user_check.py        # 真实启动应用指向 staging，双用户验证
```

迁移等级由 `VOCAB_DATABASE_PATH` 显式指定数据库文件；`VOCAB_DATA_DIR` 决定 uploads / backups / config 的位置。

## 0.2 分支与本轮进度

分支 `feat/v1.2-phase1-safe`（从事故后的安全基线 `recovery/v1.1-guarded` 建立）。本轮完成的 commit：

| Commit | 内容 |
|---|---|
| `cad5271` | Phase 2.1：删除词库会连带删除学习进度 → 加 409 守卫 |
| `39b036b` | Phase 2.2：Session 生命周期治理（活跃更新、闲置超时、启动/CLI 清理） |
| `dc1b9b4` | Phase 2.3：认证流程审计（只读，产出审计文档） |
| `abac842` | Phase 2.4-a：统一登录失败语义、消除时间侧信道、删除 Cookie 属性对齐、auth 响应 `no-store` |
| `3e7f0f4` | Phase 2.4-b：`logout` 幂等且匿名安全；登录审计补 `user_id`、新增 `login_failed` |
| `a53b7a4` | Phase 2.5：登录防滥用设计（只读） |
| `6b0e60b` | Phase 2.6-a：并发闸门 + 按 IP 失败窗口 |
| `5213b3d` | Phase 2.7-a：Session 管理设计（只读） |
| `0f32b07` | Phase 2.7-b/c：`GET /api/auth/sessions`、`DELETE /api/auth/sessions/{id}` |
| `3b6ca5c` | Phase 2.7-d-a：敏感操作二次认证设计（只读） |
| `b096d4b` | Phase 2.7-d-b：统一密码校验入口 `verify_user_password` |
| `0256039` | Phase 2.7-d-c：`_require_password` 守卫（预算 + 共享闸门 + `reauth_failed` 审计） |
| `043e221` | Phase 2.7-d-d：`POST /api/auth/sessions/revoke`（退出其它/全部设备） |

更早的 0007 生产迁移相关提交：`86c1d5c`（迁移预演）、`4f536e5`（runbook 拆分）、`5ea1233`（runbook 定稿）、`f6d4da4`（启动脚本修复）。

---

## 1. 一句话概况

拾词是一个 Windows 优先、浏览器使用的英语词汇学习应用，现已支持**多用户与登录**：React/Vite 前端由 FastAPI 托管，SQLite 是唯一事实来源；已走通单词书图片导入 → PaddleOCR → DeepSeek 结构化 → 人工校对 → 复习调度 → 阅读生成 → 阅读后测试 → 点词解释 → 全文翻译 → 生词入库 → 备份 → 重启持久化，并新增了**账号、会话、权限隔离与登录防滥用**。

## 2. 技术栈与运行方式

- 前端：React 19、Vite、TypeScript、React Router、TanStack Query、Lucide、自定义 CSS。
- 后端：FastAPI 0.141、SQLAlchemy 2、Pydantic v2、Alembic、httpx、pwdlib(Argon2id)。
- 数据库：SQLite（WAL）。
- OCR：PaddleOCR/PaddlePaddle（本地能力，云端应设 `VOCAB_ENABLE_OCR=false`）。
- AI：DeepSeek OpenAI 兼容 API，默认模型名 `deepseek-flash`。
- 生产形态：`scripts/start-vocab.ps1` 以 **单 worker** 启动 uvicorn，监听 `127.0.0.1:8000`，同时提供 API 与 `frontend/dist`。

```powershell
.\start-vocab.bat    # 构建前端 + alembic upgrade head + 启动 + 打开浏览器
.\stop-vocab.bat     # 停止（用端口与进程证明已停止）
```

## 3. 不可破坏的数据原则

1. SQLite 是唯一真实数据源。
2. Source、Learning、History 必须保持分离。
3. `source_raw`、`source_meanings`、逐图 OCR JSON、批次 OCR 文本和原始图片不能被 AI 覆盖。
4. AI 只能生成候选值、anchor、semantic note、文章、翻译和辅助判断。
5. `import_candidate` 未经人工确认，绝不能自动进入词库。
6. 每次复习必须写 `review_event`；累计数字只是缓存。
7. Schema 变化必须新增 Alembic migration，不能要求用户删库重建。
8. API Key、数据库、图片、备份和本机配置不能提交到 Git，也不能放进普通源码交接包。
9. **（本轮新增）认证不变量**：明文口令永不落库（只有 Argon2id 哈希）；会话 token 只存 SHA-256，明文仅在 HttpOnly Cookie；失败登录/敏感操作失败**不得**把口令、token 或 `token_hash` 写进任何审计或响应。

## 4. 当前数据库真实状态（2026-09-22 实测，只读）

| 项 | 实测值 |
|---|---|
| alembic revision | `0007_bridge_foreign_keys` |
| 表数 | 18 |
| 物理外键 | **26**（`PRAGMA foreign_key_check` = 0 违规） |
| 用户 | 1 个：`admin`（role=admin，is_active=1，Argon2id 已设密码） |
| 会话 | 1 条（1 条存活） |
| 业务数据 | `word` 19、`lexicon` 1（系统公共，`migrated_v11`）、`lexicon_entry` 19、`user_word_state` 19、`article` 2、`article_word_exposure` 16、`review_event` 10、`history_event` 8、`import_batch` 1、`import_candidate` 19 |

迁移历史与内容哈希证据：`docs/2026-09-22-migration-history-forensics.md`、`data/recovery/`。

## 5. 认证与权限模型现状（本轮核心，接手必读）

### 5.1 登录 / 会话 / 生命周期

- **登录**：`POST /api/auth/login`。四种失败（用户不存在 / 口令错误 / 未设置密码 / 账号停用）返回**完全相同**的 `401 {"detail":"用户名或密码不正确"}`；未知用户也执行一次 dummy Argon2 校验以消除时间侧信道（实测时间比 **1.0×**）。
- **Cookie**：`shici_session`，`HttpOnly` + `SameSite=lax` + `Path=/` + `Max-Age=30d`，`Secure` 由 `VOCAB_COOKIE_SECURE` 控制，无 `Domain`（host-only）。名称唯一定义于 `services/auth.py::COOKIE_NAME`。
- **生命周期**：绝对过期（`VOCAB_SESSION_DAYS`，签发时固定、**永不续期**）+ 闲置超时（`VOCAB_SESSION_IDLE_DAYS`，默认 7 天）+ **每账号存活会话上限**（`VOCAB_MAX_SESSIONS_PER_USER`，默认 10，`0` = 不限制；见 §5.4）。`last_seen_at` 由 `touch_session` 写入（节流 5 分钟）。启动与 `python -m app.cli prune-sessions` 会删除"永不可再用"的会话行。
- **登出**：`POST /api/auth/logout` **幂等且匿名安全**——无 cookie / 已撤销 / 已过期 / 未知 token 一律 `200 {"ok":true}` 并返回删除 Cookie；重复调用不改写首次 `revoked_at`。
- **会话判定**：`services/auth.py::session_is_live` 是唯一"可用"定义，`prune_sessions` 删除的正是它的补集。

### 5.2 权限模型

- 归属**只来自会话**，任何端点都不接受客户端 `user_id`。
- `CurrentUser`（已认证）/ `AdminUser`（管理员）；资源不属于调用者时一律 **404**（不泄露存在性），能力缺失时才 **403**。
- 未登录访问受保护端点 → **401**（响应体一致，不透露会话状态）。
- 管理员端点：`GET/POST /api/users`、`PATCH /api/users/{id}`、`POST /api/settings/backup`。
- **已关闭（2026-09-23，Phase 2.8 S-1）**：`POST /api/users` 与 `PATCH /api/users/{id}` 现在**每次调用都要求管理员输入自己的当前口令**，仅凭管理员会话不再能改他人密码/角色、停用账号或建号（含新建管理员）。原缺口记录见 `docs/V1.2-PHASE2.7-D-A-REAUTH-DESIGN.md` §1.2 与 §3.1；实现与验收见 `docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md`。
- **未关闭**：`PUT /api/settings` 的**实例级**字段（DeepSeek Key/Base URL/Model、OCR 配置）仍只需管理员会话，**无二次认证**（有意不在本批范围：该端点同时服务普通用户保存自己的偏好，无条件加口令会打断普通用户；条件加口令需另行设计）。

### 5.3 受口令保护的敏感操作（`_require_password`）

顺序固定：**预算检查 → 共享并发闸门 → 一次 `verify_user_password` → 审计/计数 →（通过后）执行业务动作**。

- 口令错误 → `400 {"detail":"当前密码不正确"}`，并写 `reauth_failed`（含 `payload.action` 固定枚举），同时记一次**按账号**的失败。
- 连续失败达 `VOCAB_REAUTH_FAILURES`（默认 5）→ `429 {"detail":"密码校验尝试过于频繁，请稍后再试"}` + `Retry-After`，**不写任何事件**。
- 成功 → 清零该账号失败计数。
- 目前受保护的端点（**5 个**）：
  1. `POST /api/auth/password`（改自己的密码）
  2. `POST /api/auth/sessions/revoke`（`others` / `all`）
  3. `DELETE /api/auth/sessions/{id}`（2026-09-23 起，Phase 2.8 F-1）
  4. **`POST /api/users`（2026-09-23 起，Phase 2.8 S-1）**
  5. **`PATCH /api/users/{id}`（同上）**
- **S-1 的请求契约（本批新增，对调用方是破坏性变更）**：两个管理员端点的请求体新增**必填** `current_password`（1–256 字符，指**调用者自己的**口令）。它与 `PATCH` 的 `password`（**目标账号的新口令**）是两个不同的秘密，因此不同名。缺字段 / 空串 / `null` → **422**；口令错 → **400**；预算耗尽 → **429** + `Retry-After`。**校验通过前不读取目标、不写任何业务行**（唯一写入是失败时那条 `reauth_failed`），因此"目标 id 是否存在"在校验失败时不可观察（有逐字节比对响应体的测试）。
- 两条顺序保证：**能力先于契约**（非管理员即使不带口令也得到 **403**，不是 422）；**口令先于读写**（口令错时连目标用户都不查）。
- 两处对 Phase 2.7-d-a 设计矩阵的**扩展**（都命中该矩阵自己的判据「会改变认证材料、会话集合、账号权限或安全设置的操作」）：
  - 「撤销单台设备」（F-1）：**被窃 Cookie 不能再用来把真实用户的其他设备踢下线**；
  - 「管理员用户管理」（S-1）：**被窃 Cookie 不能再建持久后门管理员、不能再改他人密码/角色或停用账号**。
- 成功后在既有事件上留痕（`user_created` / `user_updated`，`payload.by` 为管理员用户名）；`user_updated` 的 `fields` 只列**账号字段**，`current_password` 被显式排除，绝不出现在审计里。

### 5.4 防滥用与资源保护

| 机制 | 键 | 默认 | 行为 |
|---|---|---|---|
| 并发闸门 `LoginGate` | 全局 | `VOCAB_LOGIN_MAX_CONCURRENT=8` | **非阻塞**；满了立即 429（`Retry-After: 1`），绝不排队。所有口令校验（登录 + 敏感操作）共用，上限约 512 MiB 内存 |
| 登录失败窗口 | 客户端 IP | `VOCAB_LOGIN_IP_FAILURES=10` / `VOCAB_LOGIN_IP_WINDOW_SECONDS=300` | 达阈值 → 429；成功登录清零；`0` 关闭 |
| 二次认证预算 | 用户 | `VOCAB_REAUTH_FAILURES=5` / `VOCAB_REAUTH_WINDOW_SECONDS=300` | 同上；与 IP 窗口**互相独立** |
| **每账号会话上限**（G5，2026-09-23） | 用户（**数据库行**） | `VOCAB_MAX_SESSIONS_PER_USER=10` | 登录后存活会话数超限 → **先清理该账号已失效的行**，再撤销 `COALESCE(last_seen_at, created_at)` 最早的存活会话（并列取 id 最小；**当前会话按 id 硬排除**）；新登录**永不因超限被拒**；每条淘汰写 `session_evicted`。`0` = 不限制。**与上面三行不同：它读数据库而非内存计数**——重启、多 worker 都不会让它失真 |

计数全部在**内存**中（`app/services/limiter.py`，键有上限 + LRU），**不写数据库、不需要 schema**；重启清零。**唯一的例外是上表的会话上限**：它是数据库里的行数，属于持久约束（也因此与本文件的"单 worker"约束无关）。

### 5.5 会话管理 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/auth/sessions` | 列出**自己**的存活会话（`id/current/created_at/last_seen_at/expires_at/user_agent`），按最近活动倒序；**绝不返回 token 或 `token_hash`** |
| `DELETE` | `/api/auth/sessions/{session_id}` | 撤销自己的某个会话，**body 必带 `current_password`**（走 5.3 的守卫，校验在先、撤销在后）；他人的 id 与不存在的 id **都返回同一个 404**；重复调用幂等；撤销当前会话时同时删除 Cookie |
| `POST` | `/api/auth/sessions/revoke` | `{"scope":"others"\|"all","current_password":"…"}` → `{"ok":true,"revoked":N}`；`others` 保留当前会话且不动 Cookie，`all` 连当前一起撤销并删除 Cookie；**必须带口令**（走 5.3 的守卫）；每个被撤销会话写一条 `session_revoked` |

### 5.6 与认证相关的环境变量（详见 `.env.example`）

`VOCAB_SESSION_DAYS`、`VOCAB_SESSION_IDLE_DAYS`、**`VOCAB_MAX_SESSIONS_PER_USER`**、`VOCAB_COOKIE_SECURE`、`VOCAB_BOOTSTRAP_USERNAME`、`VOCAB_LOGIN_MAX_CONCURRENT`、`VOCAB_LOGIN_IP_FAILURES`、`VOCAB_LOGIN_IP_WINDOW_SECONDS`、`VOCAB_REAUTH_FAILURES`、`VOCAB_REAUTH_WINDOW_SECONDS`、`VOCAB_CSRF_ALLOW_MISSING_ORIGIN`、`VOCAB_CSRF_TRUSTED_ORIGINS`；数据与安全相关：`VOCAB_DATA_DIR`、`VOCAB_DATABASE_PATH`、`VOCAB_REAL_DATA_DIR`、`VOCAB_ENABLE_OCR`。

### 5.7 CSRF 防护：三层，而不是一层（Phase 2.8 S-2，2026-09-23）

设计文档：`docs/V1.2-PHASE2.8-B-CSRF-DESIGN.md`（含取舍、边界与部署要求）。实现：`backend/app/csrf.py`（纯函数）+ `backend/app/main.py::_same_origin_write_check`。

| 层 | 机制 | 挡住 | 挡不住 |
|---|---|---|---|
| 1 | Cookie 的 **`SameSite=Lax`** | 跨站表单 / 跨站 fetch / `<img>`·`<iframe>` 携带 Cookie | **同站不同源**（子域）、Chromium 的 "Lax+POST" **2 分钟窗口**、未来放宽 SameSite、旧浏览器 |
| 2 | **请求体只接受 `application/json`**（FastAPI `strict_content_type` 默认） | 跨站 HTML 表单造不出合法写请求（→ 422） | 两处 **multipart** 端点（`POST /api/imports`、`.../images`）；未来任何接受表单体的端点 |
| 3 | **写请求同源校验**（本阶段新增） | 以上全部；不依赖内容类型与浏览器行为 | XSS（同源脚本可伪造请求头） |

**规则**：`POST`/`PUT`/`PATCH`/`DELETE`（共 30 个端点）要求 `Origin`（缺失时回落 `Referer`）的 **host:port** 等于请求 `Host` 的 host:port；`GET`/`HEAD`/`OPTIONS` 一律放行；两个头都缺 → **403**（除非 `VOCAB_CSRF_ALLOW_MISSING_ORIGIN=true`）；字面量 `null` 永不接受。**只比较 host:port，不比较 scheme**（Caddy 终止 TLS 后浏览器说 https、FastAPI 看到 http）。**不豁免 `POST /api/auth/login` 与 `/api/auth/logout`**：伪造登录会把受害者登入攻击者账号，伪造登出是骚扰型 DoS。中间件在**认证之前**拒绝，因此被拒请求不解析会话、不写 `last_seen_at`、不写审计（有测试断言）。

> 部署要求（Phase 4 固化进部署脚本）：`uvicorn --proxy-headers --forwarded-allow-ips <代理地址>`。否则 TLS 终止后裸域名 `Host` 会被解释为 80 端口，https 来源的写请求会被 403 —— 与 S-4（代理后 IP 判定）是同一处配置。

## 6. 已完成能力（业务）

- **首页**：今日新词、待复习、weak、今日阅读、连续天数。
- **导入**：多图/连续拖入、去重、软删除、失败保留、PaddleOCR 复用与缩图、DeepSeek 结构化、**必须人工确认**才入库到本人私有词库。
- **学习**：一次一词、空格揭晓、1/2/3 评分、写完整 `review_event` 并更新排期。
- **阅读**：weak/失败/新词优先选词、AI 生成 + `actual_used_words` 后端校验、完成后测试、点词（词库优先、未知词才走 AI）、全文翻译并保存、查词缓存、生词入库。
- **词库**：搜索/状态筛选/weak/最近/陈旧、单词详情（Source + Learning + 复习历史 + 文章暴露）；**删除含学习记录的词库会被 409 拒绝**（Phase 2.1）。
- **设置**：DeepSeek Key/Base URL/Model（实例级，管理员）、每日新词数、文章长度、OCR 配置、onboarding 状态；Key 只回掩码。
- **账号与安全**：登录/登出、会话列表与撤销、退出其它/全部设备、改密（撤销全部会话）、**管理员用户管理（改他人密码/角色/停用/建号，2026-09-23 起每次调用都要求管理员输入自己的当前口令，S-1）**、登录防滥用、失败审计、**写请求同源校验（S-2，2026-09-23）**、**每账号会话数量上限与"最久未活动优先"淘汰（G5，2026-09-23）**；**设置页的「登录设备」区（F-1，2026-09-23）**列出每台设备的 `user_agent`/最近活动/登录时间/到期时间，当前设备不可在此撤销，单设备与"其它全部设备"两处撤销都要求输入当前口令；**设置页的「修改密码」区（F-7，2026-09-23）**校验当前口令与两次新口令一致，成功后全部会话被撤销并回到登录页（登录页会说明原因）。**注意**：管理员用户管理**只有 API/CLI 入口**，前端没有对应页面（见 §9.13）；会话上限与淘汰**对用户不可见**（没有"你被挤掉"的提示，见 §9.5）。

## 7. 代码导航

- `backend/app/models.py`：SQLAlchemy 模型（Source / Learning / History / 用户与会话）。
- `backend/app/api/`：`auth.py`（登录、会话、用户管理）、`deps.py`（**`CurrentSession` / `CurrentUser` / `AdminUser`**，归属判定的唯一入口）、业务路由。
- `backend/app/services/`：`auth.py`（会话与口令策略）、**`limiter.py`（并发闸门 + 两个失败窗口）**、`userdata.py`（owner-scoped 数据访问与统一 404）、`study.py`、`reading.py`、`imports.py`、`scheduler.py`、`backup.py`、`ai/`、`ocr/`。
- `backend/app/security.py`：Argon2id 原语 + `dummy_password_hash()`。
- `backend/app/csrf.py`：**写请求同源校验的纯函数**（`authority_of` / `request_authority` / `request_origin` / `write_is_allowed`）；无数据库、无网络、无状态。挂载点见 `main.py::_same_origin_write_check`。
- `backend/app/config.py`：全部 `VOCAB_*` 配置项（含会话、限流、二次认证、CSRF）。
- `backend/alembic/versions/`：0001–0007（**0007 为当前 head**）。
- `backend/tests/`：**32 个测试文件（408 个用例）**，其中认证相关为 `test_auth.py`、`test_authz.py`、`test_session_lifecycle.py`、`test_session_management.py`、`test_session_revoke_all.py`、**`test_session_limit.py`（G5，16 项）**、`test_login_limiter.py`、`test_reauth_guard.py`、`test_admin_reauth.py`（S-1，27 项）、`test_csrf.py`、`test_isolation_guards.py`、`test_static_guards.py`。
- `frontend/src/`：`pages/`（6 个应用页 + `LoginPage`）、`auth.tsx`（登录态与缓存清理）、`session.ts`（401 回调）、`api.ts`（统一 401/`credentials:'same-origin'`）。
- 设计/审计文档：`docs/V1.2-PHASE0-AUDIT-AND-DESIGN.md`、`docs/V1.2-PHASE2.3-AUTH-FLOW-AUDIT.md`、`docs/V1.2-PHASE2.5-LOGIN-ABUSE-PROTECTION-DESIGN.md`、`docs/V1.2-PHASE2.7-A-SESSION-MANAGEMENT-DESIGN.md`、`docs/V1.2-PHASE2.7-D-A-REAUTH-DESIGN.md`、`docs/V1.2-PHASE2.8-B-CSRF-DESIGN.md`（S-2）、**`docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md`（S-1；编号字母取自安全条目序号 S-1/S-2，不是时间顺序——批次顺序是 Batch 2A → Batch 3）**。
- 接手/协作入口（2026-09-23 新增）：**`docs/PROJECT_STATUS_CURRENT.md`**（项目当前状态，接手第一阅读文件）、**`docs/AI_DEVELOPMENT_GUIDE.md`**（AI 协作开发规范：必读清单、设计门槛、测试与文档与 Git 要求、禁止事项）。
- 迁移资料：`docs/0007-production-migration-runbook.md`、`...-checklist.md`、`docs/2026-09-22-migration-history-forensics.md`。
- 工具：`tools/`（staging、备份校验、隔离取证、迁移预演、事故取证）、`scripts/`（启动/停止/全量检查）。

## 8. 验收基线与复验命令（2026-09-23 复验）

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `cd backend; .\.venv\Scripts\python.exe -m pytest tests -q` | **408 passed**（32 个测试文件；含 S-1 的 `test_admin_reauth.py` 27 项、G5 的 `test_session_limit.py` 16 项） |
| 后端 lint | `cd backend; .\.venv\Scripts\python.exe -m ruff check --no-cache app tests` | All checks passed |
| 前端测试 | `cd frontend; npm test` | **29 passed / 5 files** |
| 前端类型/构建 | `npm run typecheck` / `npm run lint` / `npm run build` | 通过 |
| 测试隔离取证 | `backend\.venv\Scripts\python.exe tools\prove_test_isolation.py` | `data/` 全量指纹**零变化** + 全量测试通过（文件数随证据文件增减，2026-09-23 实测 100） |
| 全量门禁 | `powershell -File scripts\check.ps1` | **exit 0**（含 verified backup 一步） |
| 生产库 | 只读抽样 | revision 0007、26 外键、`foreign_key_check` = 0 |

**已修复（2026-09-23，Batch 0 / T5）**：原先 `scripts/check.ps1` 的最后一步 `tools/verify_backup.py` 必然失败（Phase 2 审计的 B6），因为验收基线 `data/recovery/baseline.json` 冻结在 `0003_article_reading_tools`（label `restored V1.1 verified source`，录制自 `data/recovery/vocab-restored-v1.1.db`），而生产库已是 0007。现按"**先建副本、再录基线**"的顺序修复：旧基线归档为 `data/recovery/baseline.prior-attempt-20260923-001325.json`（**不覆盖**），新基线从 **verified 0007 备份** `data/backups/post-0007-verified-20260923-001237-vocab.db` 录制。注：路线图 T5 曾写"`tools/verified_db.py` 里有硬编码 `0003`"——与实际不符，`0003` 只出现在事故恢复工具（`promote_restore.py` / `restore_v1_1.py` / `seal_restore.py`）里。

### 8.1 备份校验口径：行身份与行容忍（2026-09-23）

`tools/verify_backup.py` 是"**verified backup**"这一定义的唯一入口（`PROJECT_ARCHITECTURE.md` §4.5 第 5 条）。它把目标库与基线快照**逐表逐行**比对。2026-09-23 之前，它对**运行期会被改写的列**也做严格行指纹，使"正常使用"被误判成数据损坏（登录写 `last_seen_at`、复习写 `word_state`、改设置写 `user_settings`）。现按**方案 A：字段级忽略 + 行级容忍**收敛（`tools/verified_db.py`）。

**为什么是字段级忽略，而不是整表忽略**：被排除的列全部是"派生缓存与用户偏好"——`PROJECT_ARCHITECTURE.md` §4.3 第 3 条已定义 `recall_success` / `recall_fail` / `status` / `next_review_at` / `context_exposure` **都只是可由 `review_event` 重算的缓存**，而真相 `review_event` **仍按严格行指纹校验**。整表忽略会让"备份丢了整张 `user_word_state`"也被判 VERIFIED，那是关掉验证而不是修好它。

**不再参与行身份的列（`IGNORED_COLUMNS`）**

| 表 | 忽略列 | 写入方 |
|---|---|---|
| `user` | `display_name`、`role`、`is_active`、`password_hash`、`updated_at` | `PATCH /api/users/{id}`、改密、CLI `promote` / `set-password` |
| `user_session` | `last_seen_at`、`revoked_at` | `touch_session`（节流 5 min）、撤销会话 |
| `user_settings` | `daily_new_words`、`article_length`、`onboarding_seen`、`theme`、`updated_at` | `PUT /api/settings`、`POST /api/settings/onboarding` |
| `user_lexicon` | `enabled`、`daily_new_words` | `POST /api/lexicons/{id}/enable` |
| `user_word_state` | `status`、`last_review`、`next_review_at`、`recall_success`、`recall_fail`、`consecutive_failures`、`context_exposure`、`updated_at`、`anchor_override`、`semantic_note`、`notes`、`possible_issue` | `services/study.py::_apply`、`services/reading.py` 的 exposure 递增；后四列目前只由**死代码** `services/words.py::apply_learning_update` 声明可写 |
| `app_setting` | `value`、`updated_at` | 实例级设置写入（OCR、遗留 `daily_new_words` 等） |

**仍然参与校验的安全字段（不得移除）**

| 字段 | 为什么保留 |
|---|---|
| `user_session.token_hash`、`user_id` | token 被换 = **植入后门**；会话被改指他人 = **越权** |
| `user_word_state.user_id` / `lexicon_entry_id` / `legacy_word_id` | 归属与桥接列，被改指 = **跨用户/跨词条错行**（与 `test_id_namespaces.py` 同一守护目标） |
| `user.id` / `username` / `created_at`；`user_settings.user_id`；`user_lexicon.id` / `user_id` / `lexicon_id`；`app_setting.key` | 行身份与归属；行丢失或账号被换仍必须被发现 |
| `first_seen` / `created_at` / `started_at` / `expires_at` / `user_agent` | 一次写入、从不改写，变化即证据 |

**行容忍：`ROW_TOLERANT_TABLES = {user_session, user_lexicon}`**

- **允许**：行数减少与基线行消失 —— 因为 `prune_sessions` 会删除已撤销/过期/闲置的会话行，而删除**空词库**（`DELETE /api/lexicons/{id}`，409 守卫只在有学习记录时拦截）会经 `ON DELETE CASCADE` 连带删除 `user_lexicon` 行。
- **仍然必须**：存活行的身份与安全字段一致 —— 换 `token_hash`、把行改指他人**照样 FAIL**。
- **不静默**：报告新增 `pruned` 列表与逐表 `rows_pruned` 计数；`verify_backup.py` 控制台打印 `legitimate pruning:` 段；`--json` 报告同样包含。
- **其它任何表都不允许丢行**：`user_word_state`、`user`、`user_settings`、`app_setting` 丢行依旧是 FAIL（有测试覆盖）。

**操作规则（必须遵守）**

1. `identity_columns` 在**录制基线时冻结**写入 `baseline.json` → **只改 `IGNORED_COLUMNS` 而不重录基线没有任何效果**。
2. 一旦有代码开始写某个此前不写的列，**必须同步加入 `IGNORED_COLUMNS` 并重录基线**；`backend/tests/test_verified_db.py::test_runtime_columns_are_not_part_of_row_identity` 会在忽略清单与模型列不一致（含拼写错误）时失败。
3. **残余风险（已知并接受）**：`user` 的 `role` / `is_active` / `password_hash`、`app_setting` 的 `value` 被**直接 SQL 改写**时本工具不再报警。补偿：这些字段的合法变更都会在 `history_event` 留痕（`user_updated` / `user_password_changed`），且"口令哈希被清成 `!` 哨兵"这类形态级损坏仍会在登录时由 `password_is_usable` 暴露。

## 9. 已知限制与未完成项

**安全 / 认证**

1. ~~**管理员敏感操作无二次认证**（2.7-d-e，也是 S-1）：改他人密码/角色/停用/建号仅凭管理员会话~~ → ✅ **已于 2026-09-23（Batch 3）关闭**：`POST /api/users` 与 `PATCH /api/users/{id}` 的**每一次调用**都要求管理员输入**自己的当前口令**，校验通过前不改账号与会话。见 §5.2、§5.3 与 `docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md`。
1a. **`PUT /api/settings` 的实例级字段仍无二次认证**（随 S-1 一并登记）：管理员可改 DeepSeek Key / Base URL / Model 与 OCR 配置；把 `deepseek_base_url` 指向攻击者端点即可外泄后续请求内容。**有意不在 S-1 范围**——该端点同时服务普通用户保存自己的偏好，无条件加口令会打断日常保存；理由见设计文档 §2.4。
2. **恢复路径可被短暂封锁**：持被窃会话者可烧掉 re-auth 预算，使合法用户在窗口（默认 300 s）内无法执行敏感操作；缓解=短窗口 + 登录不受影响 + CLI `set-password` 逃生口 + 失败审计。
3. **限流状态在内存**：重启清零；**若改为多 worker，每个 worker 各有一份计数（等效阈值×worker 数）**。IP 判定依赖部署层——置于反向代理后必须配置 `uvicorn --proxy-headers --forwarded-allow-ips <代理地址>`，否则所有用户共用一个计数桶（与 §5.7 的 scheme 推导是同一处配置）。
4. **`history_event` 无保留策略**（append-only，实测约 129 字节/行）：登录失败与审计会持续增长。

**功能未完成**

5. ~~**会话数量上限**（2.7-e）~~ → ✅ **已于 2026-09-23（Batch 4，G5）关闭**：`VOCAB_MAX_SESSIONS_PER_USER`（默认 10，`0` = 不限制）；超限时**先清理已失效行、再淘汰最久未活动**的存活会话，**当前会话永不淘汰，新登录永不因超限被拒**。**残余**：被挤掉的设备在 UI 上得不到解释（没有 `revoke_reason`，migration 0008 未做），用户只会看到"掉线"——审计里有 `session_evicted`。
6. **无 `revoke_reason`**：UI 无法解释某设备为何被登出（需 migration 0008，可选）。
7. **无注册/找回流程**（有意为之）：账号由管理员或 CLI 创建。
8. `word` 表已无写入方，`helpers.word_dict`、`services/words.py`、`schemas.py::WordSummary` 属**遗留死代码**；`POST /api/words/quick-add`（Phase 0 规划）未实现。
9. **词频数据缺失**（F3）：`lexicon_entry.frequency_rank` 全为 NULL，选词回落到 `sequence`/`id`。
10. **PWA / 移动端**：无 manifest/SW/图标；Phase 0 §8.3 记录的移动端缺陷（≤900px 隐藏单词详情面板等）仍在。
11. **公网部署未开始**：当前只监听回环地址。
12. **CSRF 的天然边界**：同源校验不防 XSS（同源脚本可同时伪造请求与请求头）；当前前端无 `dangerouslySetInnerHTML`/`innerHTML`，但这条边界必须明说，避免"上了 CSRF 就安全"的错觉。
13. **管理员用户管理没有前端入口**：`frontend/src/` 全仓库无 `/api/users` 调用，创建/停用/改角色只能经 API 或 CLI（`python -m app.cli create-user|promote|set-password`）。S-1 让这两个端点必须二次输入口令后，**将来若建页面，必须复用 `frontend/src/components/PasswordConfirmDialog.tsx`**（已支持 400 文案与 429 的 `Retry-After` 倒计时）。**本批不建页面**。

> **2026-09-23 关闭的四项**（见 §5.2、§5.3、§5.7 与 §6）：**管理员敏感操作无二次认证（S-1，Batch 3）**、CSRF 纵深防御缺失（S-2）、前端"登录设备"页面（2.7-f）、自服务改密入口（F-7）。

## 10. 建议的下一步（按优先级）

1. ✅ **已完成（2026-09-23，Batch 3）** ~~管理员操作二次认证（S-1 / 2.7-d-e，需产品决策）~~：`_require_password` 已扩展到 `POST /api/users` 与 `PATCH /api/users/{id}`（见 §5.3）。**以下为 Phase 2.8 的剩余待办**：
2. **`history_event` 保留策略**（G6 / S-6）—— 会话数量上限（G5）已于 2026-09-23 Batch 4 完成。
3. **`/api/**` 未知路径返回 404 JSON**（T-3）、死代码清理、版本号（E-4）、staging 残留清理（S-5）。
4. 部署前置：反向代理头配置、`VOCAB_COOKIE_SECURE=true`、HTTPS、备份定时器（Phase 0 §9.4）。

## 11. 给接手 AI / 开发者的工作规则

1. 先读本文、`README.md` 与相应设计文档；改动认证相关代码前**必读** `docs/V1.2-PHASE2.3-AUTH-FLOW-AUDIT.md` 与 `docs/V1.2-PHASE2.7-D-A-REAUTH-DESIGN.md`。
2. 动手前先 `git status`，保留他人未提交/未跟踪的文件（例如本机那份未跟踪的状态文档）。
3. 不读取、输出或打包 `data/config/settings.json`、`.env` 等密钥文件。
4. 不删除或重建 `data/vocab.db`；先备份，再通过 Alembic 迁移；开发期不要迁移生产库（Level 3 规则）。
5. 涉及 Source 的修改要证明 AI 不会覆盖原始数据。
6. 新功能先补测试；完成后跑完整后端 + 前端验收（§8），并跑 `tools/prove_test_isolation.py`。
7. 认证相关改动必须保持三条不变量：**响应不泄露账号/会话状态**、**审计不含口令与 token**、**限流判定不依赖账号是否存在**（否则会把已修复的枚举旁路重新引入）。
8. 安全类改动遵循"先设计文档、再实现、后回归"的既有节奏（本仓库的 Phase 2.x 全部如此）。

## 12. 安全交接建议

推荐发送生成的源码交接 ZIP：只包含 Git 跟踪的代码、迁移、prompt、测试和文档，不含数据库、图片、备份、模型缓存、API Key、虚拟环境或 `node_modules`。若另一位 AI 必须查看真实学习数据，应单独复制某个已验证备份，并明确这是个人学习数据；不要把整目录 `data/` 与源码包混在一起发送。
