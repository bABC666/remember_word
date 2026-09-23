# 拾词项目开发路线图（PROJECT_ROADMAP）

> 文档版本：1.0
> 生成日期：2026-09-22
> 基线：分支 `feat/v1.2-phase1-safe`，HEAD `51b163d`（= `043e221` Phase 2.7-d-d + 交接文档更新）
> 生成方式：**只读盘点**。本文档编写过程中未修改任何代码、未执行 migration、未写入数据库、未创建 commit。
> 用途：让后续 AI 与开发者在**不依赖历史对话**的前提下，知道项目未来要往哪走、下一步做什么、为什么是这个顺序、以及每个阶段做完的判定标准。

---

## 0. 如何使用本文档

### 0.1 读者与用途

| 读者 | 应该怎么用 |
|---|---|
| 新接手的 AI / 开发者 | 先读 §1 定位、§2 当前状态、§4 未完成登记册，再挑 §6 中对应阶段动手 |
| 项目负责人 | 读 §5 路线图总览、§8 待决策事项、§9 里程碑，用于确认顺序与投入 |
| 正在改代码的人 | 动手前必读 §7 跨阶段硬约束；阶段收尾时按 §10.2 走完成协议 |

### 0.2 本文档在文档体系中的位置

| 文档 | 角色 | 与本文档的关系 |
|---|---|---|
| `README.md` | 安装与日常启动 | 本文档不重复，只引用 |
| `docs/PROJECT_HANDOFF.md` | **当前事实交接**：架构、认证模型、已知限制、复验命令 | 本文档的"现状"输入源；两者冲突时以它 + 代码实测为准 |
| `docs/PROJECT_STATUS_V1.2.md` | 只读审计记录（含证据分级 [实测]/[记录]/UNKNOWN） | 本文档的"证据"输入源 |
| `docs/V1.2-PHASE0-AUDIT-AND-DESIGN.md` | V1.2 设计基线 | 本文档 Phase 3/4 的原始规划来源（见 §5.3 编号说明） |
| `docs/V1.2-PHASE2.*-*.md` | Phase 2 各子阶段设计/审计 | 已完成工作的设计依据 |
| `docs/0007-production-migration-runbook.md` + `-checklist.md` | 生产迁移操作规程 | Phase 4/5 任何 migration 必须遵循 |
| `_INCIDENT-20260922/`、`data/recovery/` | 事故与恢复证据（**不入 Git**） | 只读证据，不得覆盖 |

**权威顺序**：代码与数据库实测 > `PROJECT_HANDOFF.md` > 本文档 > 其它设计文档。
已知历史文档中至少有两处**已过时结论**（`0007-release-record.md` §11.2 与 `PROJECT_HANDOFF.md` 早期版本关于 P1.3"尚未实施"的说法），不要据此重新实施已完成的工作。

### 0.3 置信度与估算口径

本文档事实陈述沿用既有标记约定：

| 标记 | 含义 |
|---|---|
| **[实测]** | 本次会话只读读取仓库/数据库/Git 得到，可复现 |
| **[记录]** | 来自仓库内既有证据文件，本次未重跑 |
| **[推断]** | 基于现状的合理推导，**未经验证**，动手前需确认 |

工作量估算为**相对规模**，不是承诺：

| 规模 | 含义 |
|---|---|
| S | ≤ 1 个工作日 |
| M | 2–3 个工作日 |
| L | 约 1 周 |
| XL | > 1 周，需要拆分为子阶段 |

优先级定义见 §5.4。

---

## 1. 项目定位

### 1.1 一句话定位

**拾词是一个"数据自主、AI 辅助、可长期持有"的英语词汇学习系统。**

它把**单词书照片导入 → OCR → AI 结构化 → 人工校对 → 复习调度 → AI 阅读复现 → 阅读后测试 → 点词解释 → 全文翻译 → 生词入库 → 备份 → 重启持久化**串成一条闭环，全部数据落在**自己掌控的单一 SQLite 数据库**里。

### 1.2 目标形态演进

| 形态 | 状态 | 说明 |
|---|---|---|
| **V1.0/V1.1 · 单机本地应用** | 已完成 | Windows 优先，`127.0.0.1:8000`，单用户 |
| **V1.2 · 多用户本地应用** | **当前**（Phase 2 基本完成，未发布） | 账号、会话、权限隔离、登录防滥用；仍只监听回环地址 |
| **V2 目标形态 · 多用户 + 移动可用 + 公网可访问** | 未开始 | PWA 安装到手机主屏、HTTPS 公网访问、自动备份、自适应复习算法 |

### 1.3 不可让渡的产品承诺（红线）

这些是产品之所以成立的理由，**任何阶段都不得为了一时方便而破坏**：

1. **完整中文释义永久保留** —— 原书 OCR 文本、`source_meanings`、`source_raw`、逐图 OCR JSON **永不被 AI 覆盖**。AI 只产出候选值、anchor、semantic note、文章、翻译。
2. **未经人工确认的内容不进词库** —— `import_candidate` 必须人工确认才成为 `lexicon_entry`。
3. **每次复习都留痕** —— 每次复习必须写 `review_event`；累计数字（`recall_success` 等）只是缓存，可随时由事件重算。
4. **数据可迁移、不可重建** —— schema 变化必须新增 Alembic migration，**永远不能要求用户删库重建**。
5. **归属只来自会话** —— 任何端点都不接受客户端传入的 `user_id` 决定数据归属。
6. **认证不变量** —— 明文口令永不落库；会话 token 只存 SHA-256；失败登录/敏感操作的审计与响应**不得**包含口令、token 或 `token_hash`；响应不泄露账号或会话状态。

### 1.4 明确的非目标

Phase 0 已明确排除，**路线图沿用以避免范围蔓延**：

- ❌ 开放注册 / OAuth / 第三方登录（账号由管理员或 CLI 创建，这是有意的）
- ❌ PostgreSQL / Redis / Celery（V2 目标形态内不引入）
- ❌ 社交、排行榜、词库商店
- ❌ 离线写入队列 / 后台同步（PWA 只缓存静态资源）
- ❌ 多 worker 横向扩展（限流与闸门状态在内存，见 §4.1 S-4）
- ❌ 用 AI 依据常识编造词频排名（Phase 0 §"总控第五节"明令禁止）

### 1.5 长期成功判据

| 判据 | 当前 |
|---|---|
| 手机上能装、能读、能看到完整释义 | ❌ 不满足（§4.3 F-1） |
| 离开本机也能访问，且数据不丢 | ❌ 不满足（§4.2 D-1） |
| 复习间隔随个人记忆表现自适应 | ❌ 不满足（固定阶梯，§6.4） |
| 任意时刻有一份经过校验的可用备份 | ❌ 不满足（§4.2 D-2） |
| 换一台机器能完整恢复 | ⚠ 部分满足（迁移 runbook 已就绪，但缺异地副本） |

---

## 2. 当前版本状态

### 2.1 版本口径（重要，避免误判）

> **"V1.2"目前只是分支名与阶段名，不是已发布的版本号。**

**[实测]** 三处版本号仍为 `1.0.0`：`backend/pyproject.toml`、`frontend/package.json`、`backend/app/main.py` 的 `FastAPI(version="1.0.0")`。发布状态：**未发布 / 未合并 / 未打 tag**。

### 2.2 代码与 Git 状态 [实测]

| 项 | 值 |
|---|---|
| 分支 | `feat/v1.2-phase1-safe` |
| HEAD | `51b163d` |
| 上游 | `origin/feat/v1.2-phase1-safe` @ `6ad8fce` |
| 领先 / 落后 | **ahead 33 / behind 0** ← 33 个 commit 只存在于本机 |
| 工作区 | 干净；仅一个未跟踪文件 `docs/PROJECT_STATUS_V1.2.md`（他人审计产物，**保留**） |
| `main` 分支 | `8216a4c`，仅含 V1，**不含 V1.1/V1.2**，不要误当主线 |
| 安全基线 | `recovery/v1.1-guarded` @ `d4d7ffb` |

### 2.3 数据库状态 [实测]

| 项 | 值 |
|---|---|
| 生产库 | `data/vocab.db`（SQLite，WAL） |
| alembic revision | `0007_bridge_foreign_keys`（唯一 head） |
| 迁移链 | `0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007`，线性无分叉 |
| 表数 / 物理外键 | **18 / 26**（ORM 声明 26，已对齐） |
| 完整性 | `integrity_check=ok`、`foreign_key_check=0` |
| 用户 | 1 个（`admin`，role=admin，argon2id） |
| 核心数据量 | `word` 19、`lexicon` 1、`lexicon_entry` 19、`user_word_state` 19、`article` 2、`review_event` 10 |
| **备份** | ⚠ `data/backups/` 中**没有任何 0007 之后的备份**（最新为 `pre-0007-production.db`，仍是 0006） |

### 2.4 验收基线与证据 [实测 + 记录]

| 检查 | 结果 |
|---|---|
| 后端测试 | **365 passed**（30 个测试文件） |
| 后端 lint | `ruff check` All checks passed |
| 前端测试 | **29 passed / 5 files**（Vitest + Testing Library） |
| 前端类型 / lint / 构建 | `typecheck` / `lint` / `build` 均通过 |
| 测试隔离取证 | `tools/prove_test_isolation.py`：`data/` 零变化 + 全量测试通过 |
| `scripts/check.ps1` | ✅ **exit 0**（2026-09-23 Batch 0 重建 baseline 后恢复；见 §4.6 E-1） |

> 本表是**快照**。2026-09-23 的 Batch 0 / 0.5 / 1 / 2A 已使若干历史快照过期（本表数字、§2.2 的 ahead 33、§4.6 E-1「必然失败」等）；**当前事实以 `docs/PROJECT_HANDOFF.md` §8 为准**，本节其余行未逐条回填。

**代码规模参考**：后端 8 个 router、**46 个业务端点 + `GET /api/health`**；`services/` 11 个模块；前端 7 个页面、4 个组件、`styles.css` 约 34 KB。

### 2.5 能力矩阵

| 领域 | 已完成 | 部分完成 | 未开始 |
|---|---|---|---|
| 导入 | 多图拖入、去重、软删除、失败保留、OCR 复用、DeepSeek 结构化、人工确认 | | |
| 学习 | 一次一词、空格揭晓、1/2/3 评分、`review_event` 全量留痕 | 算法为**固定阶梯**（§6.4） | 自适应间隔 |
| 阅读 | 选词、AI 生成 + 后端校验、测验、点词、全文翻译、生词入库 | 选词缺词频依据（§4.4 P-1） | |
| 词库 | 搜索/筛选/详情、删除 409 守卫 | `word` 表双轨并存（§4.5 T-1） | 词频排序 |
| 设置 | DeepSeek 配置、每日新词数、文章长度、OCR 开关、**自助改密（F-7，2026-09-23）** | | |
| 账号安全 | 登录/登出、会话列表与撤销、退出其它/全部设备、改密、**会话数量上限与淘汰（G5，2026-09-23）**、管理员用户管理、防滥用、失败审计、**写请求同源校验（S-2，2026-09-23）**、**管理员敏感操作二次认证（S-1，2026-09-23，提交 `c3a6106`）** | `PUT /api/settings` 的实例级字段仍无二次认证（§4.1 S-1 关闭后的剩余项）；被挤掉的设备在 UI 上得不到解释（无 `revoke_reason`） | |
| **前端设备管理** | **F-1 已完成（2026-09-23：设置页「登录设备」区，单设备与批量撤销都要求口令）** | | |
| **PWA / 移动端** | | 已有响应式断点（≤900px / ≤620px / ≤720px、`prefers-reduced-motion`） | ❌ **未开始**（无 manifest / SW / 图标，§6.2） |
| **部署** | 本机迁移 runbook 已定稿并实战过一次 | 单机启动脚本、桌面快捷方式 | ❌ **未开始**（无 `deploy/`、无 CI、仅监听回环，§6.3） |
| **学习算法升级** | | | ❌ **未开始**（§6.4） |

### 2.6 状态可信度声明（UNKNOWN 清单）

以下事项**无法从仓库现状确认**，接手者需自行确认，**不要当作已知事实**：

1. `admin` 口令是通过 CLI 还是界面设置的（仓库无日志留存）。
2. `data/backups/` 之外的目录是否存在手工备份。
3. `data/staging/` 残留副本是否仍被其它证据文件引用（清理前必须核对）。
4. 生产实例当前是否仍在运行（本路线图编写时未启动、未停止任何服务）。
5. 本机 33 个 commit 之外是否存在其它未推送分支。

---

## 3. 已完成 Phase 回顾

### 3.1 Phase 0 · 项目审计与设计基线

| 项 | 内容 |
|---|---|
| 交付物 | `docs/V1.2-PHASE0-AUDIT-AND-DESIGN.md`（69 KB 设计基线）、`docs/2026-09-22-migration-history-forensics.md` + `migration-history-manifest.json` |
| 核心产出 | 三级数据库环境（强制）、P1.0–P1.3 拆分、13 项风险登记（F1–F13）、V1.2 目标 schema、Phase 3/4 边界声明 |
| 遗留 | F2（DeepSeek Key 归属，已按"实例级共享"落地）、F3（词频数据缺失，**仍未解决**） |

### 3.2 Phase 1 · 多用户数据模型

| 子阶段 | 内容 | 证据 |
|---|---|---|
| P1.0 | 迁移可靠性：删除启动期 `create_all`、`0001` 改为显式建表、revision 不匹配拒绝启动、`busy_timeout` | `verify_schema_revision`、`testing_guards.py` |
| P1.1 | `user` / `user_session` / `user_settings` + argon2id + 服务端会话 | migration `0004` |
| P1.2 | `lexicon` / `lexicon_entry` / `user_lexicon` / `user_word_state` + V1.1 数据回填为"系统公共词库 + 每用户学习状态" | migration `0005` |
| P1.3 | **全部私有业务 API 用户级隔离**：所有权只来自会话，越权与不存在统一 **404** | `services/userdata.py`（唯一入口）、`test_authz.py` IDOR 矩阵 |
| 附带 | `0006` 补 `lexicon_entry_id` 并回填；新护栏与工具链（staging、备份校验、隔离取证、迁移预演） | `tools/` 19 个脚本 |

**遗留**：生产环境只有 1 个 `user` 行，**生产双账号端到端验收未做**（§4.6 E-3）。准确表述是「P1.3 代码完成 + 副本验收 26/26 PASS；生产双账号验收待补」。→ **2026-09-23 Batch 8**：E-3 已用**副本上**的三账号验收关闭（`data/recovery/t8-three-user-report-20260923T134500Z.json`，126/126 PASS、`verified: true`）；生产库仍只有 1 个 `user` 行，且**不得**在其上建测试账号（验收一律在副本进行）。

### 3.3 Release · 0007 生产迁移

| 项 | 内容 |
|---|---|
| 交付物 | `data/recovery/0007-release-record.md`（含全部留证与 7 项执行偏差） |
| 流程 | `rehearsal → backup → migration → verification` 四闸门，全部通过 |
| 结果 | revision `0006 → 0007`；新增 **+9 外键（17 → 26）**；重建 7 张表；**业务数据零改动**（18/18 表行指纹 IDENTICAL，独立复核） |
| 副本验收 | `staging_two_user_check.py` 17 项 PASS；`staging_isolation_check.py` **26/26 PASS** |
| 遗留 | ⚠ 迁移后**未建立新备份**、**未重建 baseline**（§4.2 D-2、§4.6 E-1） |

### 3.4 Phase 2 · 认证体系（2.1 – 2.7-d，基本完成）

| 子阶段 | 内容 | 关键 commit |
|---|---|---|
| 2.1 | 删除含学习记录的词库 → **409 守卫**（`user_word_state` 是 CASCADE，删词库会连带删复习进度） | `cad5271` |
| 2.2 | Session 生命周期治理：绝对过期 + 闲置超时（节流 5 min 写 `last_seen_at`）+ 启动/CLI 清理 | `39b036b` |
| 2.3 | 认证流程审计（只读）→ `V1.2-PHASE2.3-AUTH-FLOW-AUDIT.md` | `dc1b9b4` |
| 2.4 | 统一登录失败语义、消除时间侧信道（dummy Argon2，实测 1.0×）、Cookie 属性对齐、auth 响应 `no-store`；`logout` 幂等且匿名安全、审计补 `user_id` 与 `login_failed` | `abac842`、`3e7f0f4` |
| 2.5 | 登录防滥用设计（只读）→ `V1.2-PHASE2.5-LOGIN-ABUSE-PROTECTION-DESIGN.md` | `a53b7a4` |
| 2.6 | 并发闸门 `LoginGate`（全局，非阻塞 429）+ 按 IP 失败窗口 | `6b0e60b` |
| 2.7-a | Session 管理设计（只读）→ `V1.2-PHASE2.7-A-SESSION-MANAGEMENT-DESIGN.md` | `5213b3d` |
| 2.7-b/c | `GET /api/auth/sessions`、`DELETE /api/auth/sessions/{id}` | `0f32b07` |
| 2.7-d | 二次认证：设计文档 → 统一口令入口 → `_require_password` 守卫（预算 + 共享闸门 + `reauth_failed` 审计）→ `POST /api/auth/sessions/revoke`（退出其它/全部设备） | `3b6ca5c`、`b096d4b`、`0256039`、`043e221` |

**Phase 2 的实质成果**：认证体系已完整可用——**登录、登出、会话列表、单会话撤销、批量撤销（退出其它/全部设备）、改密、管理员用户管理、并发闸门、两个独立失败窗口、失败审计，全部有测试覆盖**（认证相关测试文件 9 个）。

### 3.5 各阶段明确交接给后续的东西

| 来源 | 交给谁 | 内容 |
|---|---|---|
| Phase 0 §G.4 | Phase 3 | PWA（manifest / SW / 图标）、移动端 UI（含 M1/M2） |
| Phase 0 §G.4 | Phase 4 | Caddy / systemd / 部署脚本 / 域名 / HTTPS；备份 timer 与保留策略 |
| Phase 0 §F3 | 待外部资料 | 词频导入脚本 |
| Phase 0 §F4 | Phase 6 | `word` 表退场（需独立风险评估） |
| Phase 2 §9（handoff） | Phase 2.8 | 前端设备管理页、CSRF、管理员二次认证、会话上限、基线重建 |
| Phase 1 §6.1 | Phase 2.8 | 生产双账号验收 |

---

## 4. 未完成事项登记册

> 这是路线图的**工作队列**。每条含 ID、事实、证据、严重度、影响、归属阶段。
> 严重度：🔴 阻塞上线 / 🟠 应尽快 / 🟡 可计划 / ⚪ 记录备查

### 4.1 安全与合规

| ID | 事项 | 证据 | 严重度 | 影响 | 阶段 |
|---|---|---|---|---|---|
| **S-1** | ~~**管理员敏感操作无二次认证**：改他人密码/角色、停用账号、创建账号（含新建管理员）仅凭管理员会话即可执行~~ → ✅ **已关闭（2026-09-23，Batch 3）**：`POST /api/users` 与 `PATCH /api/users/{id}` 的**每一次调用**都要求管理员输入**自己的当前口令**（复用 `_require_password`；零 schema、零新配置）；失败矩阵固定为 401/403/422/400/429，**校验通过前不改账号与会话**；`/api/users` 响应纳入 `no-store`；口令/token/`token_hash` 不进响应与审计。设计 `docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md`；测试 `backend/tests/test_admin_reauth.py`（27 项）；契约变化见 handoff §5.3 | handoff §5.2、`V1.2-PHASE2.7-D-A-REAUTH-DESIGN.md` §1.2/§3.1 | 🟠→⚪ | 剩余边界：恢复路径仍可被短暂封锁（S-3）；`PUT /api/settings` 仍无二次认证（另议） | 2.8 ✅ |
| **S-2** | ~~**CSRF 纵深防御缺失**：全仓库无 `Origin`/`Referer` 校验、无 CORS 中间件，仅依赖 `SameSite=Lax`~~ → ✅ **已关闭（2026-09-23，Batch 2A）**：写请求同源校验（`backend/app/csrf.py` + `main.py` 中间件，30 个非安全端点，`GET`/`HEAD`/`OPTIONS` 放行），与 `SameSite=Lax`、JSON-only 请求体构成三层；仍无 CORS（有意为之） | handoff §5.7、`docs/V1.2-PHASE2.8-B-CSRF-DESIGN.md`、`backend/tests/test_csrf.py`（45 项） | 🟠→⚪ | 剩余边界：XSS 不在防御范围；`VOCAB_CSRF_ALLOW_MISSING_ORIGIN=true` 会让所有客户端一起失去第三层；TLS 代理须配 `--proxy-headers` | 2.8 ✅ |
| **S-3** | **恢复路径可被短暂封锁**：持被窃会话者可烧掉 re-auth 预算，使合法用户在窗口（默认 300 s）内无法执行敏感操作 | handoff §9.3 | 🟡 | 拒绝服务窗口有限；已有 CLI `set-password` 逃生口 | 2.8（评估） |
| **S-4** | **限流状态在内存**：重启清零；**多 worker 下每 worker 各一份计数（等效阈值 × worker 数）**；反向代理后未配置 `--proxy-headers` 会导致所有用户共用一个 IP 计数桶 | handoff §9.4 | 🟠 | 部署阶段若误开多 worker，防滥用形同虚设 | 2.8（约束固化）/ 4（部署校验） |
| **S-5** | **`data/staging/` 残留含真实数据的副本**（`v1.1-realdata-migration-test.db`、`v1.3-acceptance.db` 等）→ ✅ **2026-09-23（Batch 10 / T14）已逐个盘点、分类并处置**：删除 **6 个文件**（两次**中止**的 G6 演练副本及其 sidecar：`history-retention-drill-…130939Z.db` + `-shm`/`-wal`、`…131010Z.db` + `-shm`/`-wal`）；**其余全部保留**，因为它们是被引用的证据或不可重建（逐项理由见 T14 行与本批处置记录） | status §6.5 #4；处置记录 `data/recovery/t14-staging-disposal-20260923T155046Z.json` | 🟡→⚪ | 已处置部分不再长期驻留；保留项都有明确用途（证据链 / 工具默认目标 / 被测试引用 / 用途未定） | 2.8（已处置）/ 6 |
| **S-6** | ✅ **已实现并在 data/staging 副本上验收（2026-09-23，Batch 7 / G6 / T10）；生产清理仍待批准** 原缺陷：**无保留策略的审计增长**：`history_event` append-only（约 129 字节/行），登录失败与审计持续增长 | handoff §9.5；实现与验收见 `docs/V1.2-PHASE2.8-D-HISTORY-RETENTION-DESIGN.md` §8 | ✅ 已实现 | 在线保留 365 天、仅四类事件可归档；**生产清理需要负责人对具体计划与运行 ID 另行批准** | 2.8 |

### 4.2 数据与运维

| ID | 事项 | 证据 | 严重度 | 影响 | 阶段 |
|---|---|---|---|---|---|
| **D-1** | **公网部署未开始**：仅监听 `127.0.0.1:8000`，无 `deploy/`、无反向代理、无 HTTPS、无域名、无 CI（`.github/` 不存在） | [实测] `Test-Path deploy` = False、`.github` = False | 🔴 | 产品无法离开本机；这是长期成功判据的直接阻塞项 | 4 |
| **D-2** | **没有任何 0007 之后的备份**：`data/backups/` 最新为 `pre-0007-production.db`（0006）；原因是 `backup.py` 天级幂等（当日文件已存在即跳过），而 `POST /api/settings/backup` 从未被调用 | [实测] 目录列表；status §6.3 | 🔴 | **一次磁盘故障即丢失全部 V1.2 数据与 `admin` 口令** | 2.8 |
| **D-3** | **生产库 `-wal` 未 checkpoint**（曾达 156592 B，含 `admin` 口令、会话、第 2 篇文章） | status §3.3 / §6.2 | 🟠 | 只拷贝 `vocab.db` 主文件会**回退到 `password_hash='!'` 且登不进去** | 2.8 |
| **D-4** | **备份策略不适合常驻服务**：仅"启动时 + 手动"两个触发点，24h 运行可数周不备份；无保留策略、无异地副本、无失败可见性、无完整性校验 | Phase 0 §9.2（S1–S6） | 🟠 | 备份存在性与可信度均无保障 | 4 |
| **D-5** | 生产仍是**单用户**（1 个 `user` 行），隔离能力全靠副本证据与测试证明 | status §6.1 | 🟡 | 多用户承诺未经真实多账号生产验证 | 2.8 |

### 4.3 前端功能

| ID | 事项 | 证据 | 严重度 | 影响 | 阶段 |
|---|---|---|---|---|---|
| **F-1** | **前端"登录设备"页面未做（2.7-f）**：后端能力**全部就绪**（列表 / 单撤销 / 批量撤销），前端未接线 | handoff §9.6；`frontend/src/` 无相关页面 | 🟠 | 已投入的后端能力无法被用户使用；"退出其它设备"这一安全能力对用户不可见 | **2.8（首选）** |
| **F-2** | **移动端 ≤900px 隐藏单词详情面板**（`.word-detail { display: none }`），**无替代入口** | Phase 0 §8.3 M1；`styles.css` | 🟠 | 手机上**完全看不到音标、完整释义、复习历史、文章暴露** —— 直接违反"完整中文释义永久保留"的可见性承诺。**最严重的移动端缺陷** | 3 |
| **F-3** | 移动端 ≤900px 隐藏导入图片列表（`.image-list`） | Phase 0 §8.3 M2 | 🟡 | 手机上无法查看/移除已上传图片 | 3 |
| **F-4** | 缺 iOS safe-area 适配：全文件零处 `env(safe-area-inset-*)`，未设 `viewport-fit=cover` | Phase 0 §8.3 M3 | 🟡 | 固定底部导航被 iPhone Home Indicator 遮挡 | 3 |
| **F-5** | 无 PWA viewport / apple 元信息 | Phase 0 §8.3 M4 | 🟡 | 无法"添加到主屏幕"独立运行 | 3 |
| **F-6** | 底部导航 6 项在 390px 下每项约 60px，偏拥挤 | Phase 0 §8.3 M6 | 🟡 | 若要新增入口需重新设计（建议 5 项 + 中央加号） | 3 |
| **F-7** | **无自服务改密 UI**：`POST /api/auth/password` 存在但前端无入口；且改密会撤销全部会话（含当前），调用方需重新登录（代码注释与行为需一并修正） | handoff §9.9 | 🟡 | 用户改密只能找管理员 | 2.8 |

### 4.4 产品能力

| ID | 事项 | 证据 | 严重度 | 影响 | 阶段 |
|---|---|---|---|---|---|
| **P-1** | **词频数据缺失（F3）**：`lexicon_entry.frequency_rank` / `frequency_count` / `frequency_source` **全为 NULL**，无任何代码读取或写入它们；选词回落到 `sequence`/`id` | [实测] grep 全仓库：仅 model / migration `0005` / `staging_migration_check.py`（断言其为 NULL）出现 | 🟠 | "考研词频优先"功能被阻塞；**不得用 AI 编造排名**，需用户提供外部词频文件 | 5 |
| **P-2** | **复习算法为固定阶梯，不随个人记忆表现自适应** | [实测] `services/scheduler.py`（41 行） | 🟠 | 产品核心价值的上限；易词与难词同节奏，复习量随时间失控或覆盖不足 | **5** |
| **P-3** | 无学习效果统计/洞察（留存曲线、到期预测、进度趋势） | [推断] `frontend/src/pages/` 无相关页面 | 🟡 | 用户无法感知长期进步，影响留存 | 5 |
| **P-4** | 无注册/找回流程（**有意为之**） | handoff §9.10 | ⚪ | 账号由管理员或 CLI 创建；若未来开放需重新评估安全面 | 6（决策） |
| **P-5** | ✅ **已关闭（2026-09-23，Batch 6，G8/T16）** 原缺陷：**「每日新词数」设置存而不用**：`user_settings.daily_new_words` 与 `user_lexicon.daily_new_words` 可经 `/api/settings` 与词库启用写入，但 `GET /api/study/today` **只使用自己的 `limit` 查询参数（默认 50，上限 200），从不读该设置，也没有"每日新词"上限** | [实测，修复前] `backend/app/api/study.py:18-50`；查询条件只有 `next_review_at` / `status`，无 `daily_new_words` | ✅ 已关闭 | **设置页对用户撒谎**：用户以为每日新词数已生效，实际队列是"全部到期 + 全部 new，直到 50 条"。这是功能正确性缺陷，不是技术债 | **2.8** |
| **P-6** | `lexicon_entry.sequence` 已声明并建了专用索引 `ix_lexicon_entry_sequence`，但**应用代码从不读也不写** | [实测] 全仓库 grep：仅 model 定义与 migration `0005:137` 的 DDL | 🟡 | 选词顺序的"预留字段"从未接线；与 P-1 词频回落的实现同时处理 | 5 |

### 4.5 技术债

| ID | 事项 | 证据 | 严重度 | 影响 | 阶段 |
|---|---|---|---|---|---|
| **T-1** | **`word` 表与 `user_word_state` 双轨并存**：`word` 是 V1.1 兼容垫片，被 4 张表外键引用（`review_event` / `article_word_exposure` / `article_word_lookup` / `import_candidate`），且已无写入方 | [实测] `models.py`；status §5.3 / §6.5 #7 | 🟡 | 两套 id 命名空间（`word.id` vs `user_word_state.id`）**刻意不互相回退**，混用得 404；未来最大结构风险 | 6 |
| **T-2** | **遗留死代码**：~~`helpers.word_dict`、`schemas.py::WordSummary`~~ → ✅ **已于 2026-09-23（Batch 9 / T12）删除**；**剩余**：`services/words.py::apply_learning_update`（仅被 `test_import_flow.py` 引用，属旧 `word` 路径）与 `schemas.py::ORMModel`（随 `WordSummary` 删除后已无任何使用者） | [实测] 全仓库搜索：`word_dict(` 仅命中定义自身（无任何调用点）；`WordSummary` 仅命中定义与把它记为死代码的文档 | ⚪ | **剩余**：随 `word` 表退场（Phase 6）一并清理 `apply_learning_update` 与 `ORMModel` | 2.8（低风险部分 ✅）/ 6 |
| **T-3** | **未知 `/api/*` 路径返回 200 HTML**：SPA 兜底路由在 router 之后匹配 `/{path:path}` | [实测] status §6.5 #8 | 🟡 | 权限测试时不要误读为漏洞；客户端错误处理会拿到 HTML | 2.8 |
| **T-4** | 响应体全为手写 dict，无统一出口（F7） | Phase 0 §F7 | 🟡 | 一个遗漏的查询就会泄露他人数据；**应对是用 IDOR 测试矩阵覆盖，不重构为 Pydantic**（已接受） | 持续 |
| **T-5** | 时区语义：SQLite 不存时区，`DateTime(timezone=True)` 读出为 naive | Phase 0 §F13 | ⚪ | 现有代码同源比较无症状；跨时区展示需处理 | 4/6 |
| **T-6** | **连续天数（streak）在 Python 中重算**：`/api/dashboard` 取最近 **1000** 条 `ReviewEvent.timestamp` 后在 Python 里算连续天数，不存储 | [实测] `backend/app/api/dashboard.py:74-88` | 🟡 | 复习事件超过 1000 条后连续天数可能算错；随使用时长增长必然触发。需改为 SQL 聚合或按需存储 | 5（与学习统计同批） |
| **T-7** | `.env.example` **未记录 `VOCAB_DATABASE_PATH`**（已记录其余全部 `VOCAB_*` 变量） | [实测] `.env.example` 47 行 vs `config.py` | ⚪ | 迁移等级切换（三级数据库环境）依赖该变量，缺文档容易误配 | 2.8 |

### 4.6 发布流程与工程基线

| ID | 事项 | 证据 | 严重度 | 影响 | 阶段 |
|---|---|---|---|---|---|
| **E-1** | ✅ **已关闭（2026-09-23，Batch 0 / T5）** ~~**`scripts/check.ps1` 必然失败（B6）**：`tools/verify_backup.py` 的基线 `data/recovery/baseline.json` 仍冻结在 `0003_article_reading_tools`，而生产库已是 `0007`~~ → 基线已从 verified 0007 备份重录（旧基线归档保留），`check.ps1` 恢复 **exit 0**；另注：`tools/verified_db.py` 并无硬编码 `0003`（实际位置见 T5 行） | handoff §8 | 🟠→⚪ | 已恢复自动兜底 | 2.8 ✅ |
| **E-2** | **33 个 commit 只在本地**，`origin` 停在 `6ad8fce`；发布证据（`data/recovery/*`）按设计不入 Git | [实测] `git status -sb` | 🔴 | 本机故障即丢失 P1.0–P1.3 与 0007 迁移成果 | **2.8** |
| **E-3** | ✅ **已关闭（2026-09-23，Batch 8 / T8）** 原缺陷：生产双账号/三账号端到端隔离验收未做（须**在副本上**，禁止在生产建测试账号） | status §6.1；证据 `data/recovery/t8-three-user-report-20260923T134500Z.json` | ✅ 已关闭 | 副本三账号矩阵 126 项全通过，含 409 守卫与跨用户 404 等价性 | 2.8 |
| **E-4** | 版本号未更新（`1.0.0` × 3 处） | [实测] | 🟡 | 无法标记发布点；"V1.2"仅是阶段名 | 2.8 |
| **E-5** | 无 CI（`.github/` 不存在） | [实测] | 🟡 | 所有门禁依赖人工执行 `check.ps1`（而它当前失败） | 6 |
| **E-6** | 文档过时陈述：`0007-release-record.md` §11.2、`PROJECT_HANDOFF.md` 早期版本关于 P1.3/登录页"尚未实施"的说法与现状矛盾 | status §6.1 | 🟡 | **会诱导接手者重复实施已完成工作** | 2.8 |
| **E-7** | `data/recovery/acceptance-report.json` 是 V1.1 / `0003` 时期证据 | status §6.5 #6 | ⚪ | 易被误当作 0007 之后的证据 | 2.8 |

---

## 5. 路线图总览

### 5.1 Phase 地图

| Phase | 名称 | 优先级 | 规模 | 前置依赖 | 核心收益 | 状态 |
|---|---|---|---|---|---|---|
| **2.8** | 认证收尾与工程基线修复 | **P0** | L | 无 | 恢复门禁、消除提权风险、兑现已投入的后端能力、建立可用备份 | **进行中**（2026-09-23：Batch 0/0.5/1/2A/**3**/**4**/**5**/**6**/**7**/**8**/**9**/**10**/**11** 已完成门禁恢复、verified backup + 新 baseline、F-1、F-7、S-2、**S-1**、**G5**；剩余 T13 版本决策、G6 的**生产执行**与 DoD 4 口径的负责人确认（F-1/F-7 已于 Batch 11 在真实浏览器验收通过）（保留策略已于 Batch 7 实现并在副本验收、T8 三账号验收已于 Batch 8 完成、T12 死代码已于 Batch 9 清理、**T14 staging 残留已于 Batch 10 处置**）；**T11（T-3）与 T17 已于 Batch 5 完成、G8「每日新词数」已于 Batch 6 完成**） |
| **3** | PWA 与移动端可用性 | **P1** | L–XL | 无（可与 2.8 并行） | 手机上真正可用；核心承诺在移动端成立 | 未开始 |
| **4** | 生产部署上线 | **P1** | L–XL | 2.8（备份 + 门禁 + CSRF）；建议在 3 之后 | 产品离开本机；自动备份与灾难恢复 | 未开始 |
| **5** | 学习算法升级 | **P2** | XL | 4（可并行启动设计与离线验证） | 核心价值提升：自适应间隔、复习量可控、可量化效果 | 未开始 |
| **6** | 平台化与长期演进 | **P3** | XL | 4 + 5 | 降低长期维护成本；支撑规模增长 | 未开始 |

### 5.2 依赖关系图

```
                    ┌─────────────────────────────────────────┐
                    │  P0   Phase 2.8 认证收尾 + 工程基线修复   │
                    │  · F-1 前端设备管理页（后端已就绪）        │
                    │  · E-2 推送 33 commits  ┃ D-2 verified backup │
                    │  · E-1 重建 baseline（恢复 check.ps1）    │
                    │  · S-2 CSRF 同源校验   ┃ S-1 管理员二次认证 │
                    └───────────────┬─────────────────────────┘
                          ┌─────────┴─────────┐
                          ▼                   ▼
        ┌──────────────────────────┐  ┌──────────────────────────┐
        │ P1  Phase 3 PWA/移动端    │  │ P1  Phase 4 生产部署      │
        │ · manifest + SW + 图标    │  │ · Caddy + HTTPS + systemd │
        │ · F-2 单词详情移动化 ★    │  │ · --proxy-headers（S-4）  │
        │ · F-3/F-4/F-5/F-6        │  │ · D-4 备份 timer + 保留   │
        │ · 无 DB 影响             │  │ · 无 schema 影响          │
        └──────────────────────────┘  └───────────┬──────────────┘
                                                  ▼
                                  ┌────────────────────────────────┐
                                  │ P2  Phase 5 学习算法升级        │
                                  │ · P-2 自适应调度（SM-2/FSRS）   │
                                  │ · migration 0008+ 新增字段      │
                                  │ · P-1 词频导入（待外部资料）    │
                                  │ · P-3 统计与到期预测            │
                                  └───────────────┬────────────────┘
                                                  ▼
                                  ┌────────────────────────────────┐
                                  │ P3  Phase 6 平台化与长期演进    │
                                  │ · T-1 word 表退场（风险先行）   │
                                  │ · E-5 CI  ┃ 可观测性 ┃ 多租户   │
                                  └────────────────────────────────┘
```

★ = 该阶段的最高风险项

### 5.3 编号说明（与既有文档对齐）

Phase 0 §G.4 已声明 **PWA/移动端 → Phase 3**、**Caddy/systemd/部署 → Phase 4**，本路线图**沿用该编号**，避免与既有文档冲突。

**两处需要澄清的编号不一致**：

1. Phase 0 §9.4 把备份 timer 标为"Phase 3 建议方案"，而 §G.4 标为 Phase 4。**本路线图统一归入 Phase 4（部署）**，理由：备份 timer 依赖 systemd，与部署同批交付更自然。
2. `handoff` 中"前端设备管理页"编号为 **2.7-f**，"会话数量上限"为 **2.7-e**。本路线图把它们与其它收尾项合并为 **Phase 2.8**，因为 2.7 系列已完成其主体（会话管理与二次认证），剩余项与工程基线修复同批处理成本更低、收益更集中。

### 5.4 优先级定义

| 级别 | 判据 |
|---|---|
| **P0** | 不做就无法安全继续：数据可能丢失、门禁失效、已认定的提权路径敞开 |
| **P1** | 不做产品承诺不成立：产品无法被目标场景实际使用 |
| **P2** | 核心价值提升：做了明显更好，不做仍可用 |
| **P3** | 长期健康：降低维护成本、支撑规模，可延后 |

### 5.5 为什么是这个顺序（排序理由）

1. **先堵泄漏，再谈扩张**：D-2（无备份）+ E-2（成果只在单机）是"随时可能全部丢失"的敞口，且成本极低（S–M）。任何后续阶段都在生产库上叠加风险，必须先把可回滚性建立起来。
2. **先修门禁，再谈质量**：E-1 使 `check.ps1` 恒失败 → 后续每个阶段的验收都失去自动兜底。修它只需 S 规模。
3. **F-1 是投入产出比最高的一项**：后端 3 个端点 + 测试**已经全部就绪**，只差前端接线。它是唯一"零后端风险、直接兑现已投入成本"的任务，因此排在 2.8 首位。
4. **PWA/移动端与部署可并行**：两者无代码依赖（Phase 3 不动后端、Phase 4 不动 schema），但**部署建议在 PWA 之后**——否则公网上线的第一版在手机上不可用（F-2），会浪费首次用户接触。
5. **算法升级放在部署之后**：P-2 是唯一需要**新 schema + 回填策略 + 对已有用户生效日期的影响评估**的改动（XL）。它必须在一个**已经具备 verified backup 与自动备份**的环境里做，否则回滚成本不可承受。
6. **`word` 表退场排在最后**：Phase 0 §F4 已判定其为"未来最大结构风险"，且 SQLite 下任何重建都会牵动 4 张引用表。在算法升级引入 `user_word_state` 新字段之前动它就是自找麻烦。

---

## 6. 阶段详情

---

### 6.1 Phase 2.8 · 认证收尾与工程基线修复

> **一句话**：把 Phase 2 已建成的能力**兑现给用户**，把已失效的门禁**修回可用**，把"随时可能丢数据"的敞口**关掉**。

| 项 | 内容 |
|---|---|
| **优先级** | **P0** |
| **规模** | L（约 1 周） |
| **前置依赖** | 无 |
| **出口里程碑** | M1（见 §9） |

#### 预计收益

| 收益 | 可验证的判定信号 |
|---|---|
| 数据可回滚 | 存在一份通过 `tools/verify_backup.py` 全部校验的 **0007 verified backup**；新 baseline 已记录 |
| 成果不再单点 | `git status -sb` 显示 `ahead 0`（或明确记录的推送点） |
| 门禁恢复 | `scripts/check.ps1` **exit 0 全绿**（含隔离取证与 verified backup 两步） |
| 用户体验到安全能力 | 用户可在界面上看到自己的登录设备、撤销单台设备、一键退出其它/全部设备 |
| 提权路径关闭 | 管理员改他人密码/角色/停用/建号均需二次输入自己的口令 |
| CSRF 纵深建立 | 对 `/api/**` 写操作统一校验 `Origin`/`Referer`，有测试覆盖 |
| 设置不再撒谎 | 「每日新词数」确实限制每日新词数量（P-5），有测试断言 |
| 移动端前置就绪 | 移动端不再有"返回 200 HTML"的错误路径误判（T-3） |

#### 功能目标

| ID | 目标 |
|---|---|
| G1 | ✅ **已完成（2026-09-23，Batch 1）** **前端"登录设备"页面（2.7-f，★ 首选任务）** —— 设置页「登录设备」区：列表（`user_agent`/最近活动/登录时间/到期时间）、单会话撤销、"退出其它全部设备"；当前设备不可在此撤销；两处撤销**都要求当前口令** |
| G2 | ✅ **已完成（2026-09-23，Batch 1）** **自服务改密入口（F-7）** —— 设置页「修改密码」区，复用同一口令确认对话框；成功后全部会话被撤销并回到登录页（登录页说明原因） |
| G3 | ✅ **已完成（2026-09-23，Batch 3）** **管理员敏感操作二次认证（S-1）** —— 改他人密码/角色、停用、建号，以及"什么都没改"的空更新，**每一次调用都要管理员输入自己的当前口令**；失败矩阵 401/403/422/400/429 固定；设计 `docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md` |
| G4 | ✅ **已完成（2026-09-23）** **CSRF 同源校验（S-2）** 覆盖 `/api/**` 写操作（30 个非安全端点；`POST /api/auth/login` 与 `/logout` **不豁免**） |
| G5 | ✅ **已完成（2026-09-23，Batch 4）** **会话数量上限（2.7-e）** —— `VOCAB_MAX_SESSIONS_PER_USER`（默认 **10**，**0 = 不限制**）；每次登录后若该账号存活会话数超限，**先清理已失效行**，再按 `COALESCE(last_seen_at, created_at)` 最早**撤销最久未活动**的存活会话（并列取 id 最小者），**刚签发的当前会话按 id 硬排除**；新登录**永不因超限被拒**；每条淘汰写一条不含凭据的 `session_evicted` 审计。设计记录 `docs/V1.2-PHASE2.8-C-SESSION-LIMIT-DESIGN.md`，测试 `backend/tests/test_session_limit.py`（16 项） |
| G6 | ✅ **已实现并在 data/staging 副本上验收（2026-09-23，Batch 7 / T10）；生产执行未批准** **`history_event` 保留策略（S-6）** —— 在线保留 **365 天**，仅 `login_failed`、`reauth_failed`、`user_login`、`article_word_lookup` **四类事件**可到期归档（其余审计事件长期在线保留）；`history-retention preview --plan` 固定候选 ID、UTC cutoff 与逐行 hash，`apply --plan --confirm <运行 ID>` 在 `BEGIN IMMEDIATE` 事务内按明确 ID 删除并核对删除数，提交后核验通过才原子发布 committed 凭证；**`data/vocab.db` 从未执行 preview/apply**（生产执行需负责人针对具体计划与运行 ID 批准并安排维护窗口）。设计 `docs/V1.2-PHASE2.8-D-HISTORY-RETENTION-DESIGN.md` §8 |
| G7 | **`/api/**` 未知路径返回 404 JSON（T-3）** |
| G8 | ✅ **已完成（2026-09-23，Batch 6）** **让「每日新词数」真正生效（P-5）** —— 学习队列按「当天已开始学的新词数」**累计**限制 `new`，而不是每次请求最多显示 N 个；到期与 `weak` 词优先占用 `limit`，且不受新词额度削减。语义、取舍与边界见 `docs/V1.2-PHASE2.8-E-DAILY-NEW-WORDS-DESIGN.md`，测试 `backend/tests/test_daily_new_words.py`（25 项） |

#### 技术任务

| # | 任务 | 涉及位置 | 规模 |
|---|---|---|---|
| T1 | ✅ **已完成（2026-09-23，Batch 1）** 新增设备管理 UI：调用 `GET /api/auth/sessions`、`DELETE /api/auth/sessions/{id}`、`POST /api/auth/sessions/revoke`；展示 `user_agent/created_at/last_seen_at/expires_at` 与"当前设备"标记；**绝不渲染 token 或 token_hash**（测试把二者塞进夹具，泄漏即会以文本出现） | `frontend/src/pages/SettingsPage.tsx` | M |
| T2 | ⚠ **部分完成（2026-09-23，Batch 1）** 口令确认对话框已抽成共享组件 `PasswordConfirmDialog`，并对**二次认证预算与并发闸门的 429 展示 `Retry-After` 倒计时**（等待期禁用按钮）；**未做**：登录页对登录限流 429 只显示文案，没有倒计时 | `frontend/src/components/PasswordConfirmDialog.tsx` | S–M |
| T3 | ✅ **已完成（2026-09-23，Batch 3）** 把 `_require_password`（顺序固定：预算检查 → 共享闸门 → 一次 `verify_user_password` → 审计/计数 → 业务动作）扩展到管理员端点 `POST /api/users`、`PATCH /api/users/{id}`。守卫本体**一行未改**（零新类、零新配置、零 schema）；请求体新增必填 `current_password`，属**破坏性契约变更**，已同步更新既有测试的调用参数（未削弱任何安全断言）；`/api/users` 一并纳入 `no-store` 前缀 | `backend/app/api/auth.py`、`backend/app/schemas.py`、`backend/app/main.py`、`backend/tests/test_admin_reauth.py` | M ✅ |
| T4 | ✅ **已完成（2026-09-23）** 新增 `Origin`/`Referer` 校验中间件（只作用于写方法；`GET`/`HEAD`/`OPTIONS` 不拦）；前端 `credentials:'same-origin'` 不受影响（零前端改动） | `backend/app/csrf.py`（新）+ `backend/app/main.py` + `config.py` | M |
| T5 | ✅ **已完成（2026-09-23，Batch 0）** 重建验收基线：从 verified 0007 备份录制新 `data/recovery/baseline.json`，旧基线归档为 `baseline.prior-attempt-*.json`（**未覆盖**），并同步更新 `test_verified_db.py` 的项目基线断言。**注**：任务里"`tools/verified_db.py` 硬编码 `0003`"与实际不符——该文件没有硬编码 revision；`0003` 字面量在**事故恢复工具**（`seal_restore.py`/`promote_restore.py`/`restore_v1_1.py`）与 `fresh_clone_migration_check.py`（断言克隆起点，合法）中，前者按规则**未改动** | `data/recovery/baseline.json`、`backend/tests/test_verified_db.py` | S |
| T6 | ✅ **已完成（2026-09-23，Batch 0）** 建立 0007 verified backup：`data/backups/post-0007-verified-20260923-001237-vocab.db`（589824 B，sha256 `21d3d821…`），用 SQLite online backup API 从**只读**源生成；`verify_backup.py` 判定 **VERIFIED BACKUP**；生产主文件/WAL 在生成前后逐字节相同 | `data/backups/` | S |
| T7 | ✅ **已完成（2026-09-23）** 本地 commit 已推送（Batch 0/1/2A 三次推送），当前 `git status -sb` 为 **ahead 0 / behind 0**；发布证据 `data/recovery/*` 按设计**不入 Git**，随源码包另行交接 | git | S |
| T8 | ✅ **已完成（2026-09-23，Batch 8）** 副本上的**三账号验收**：`tools/staging_three_user_check.py` 从生产库**只读**建唯一命名副本（SQLite online backup API）→ 核验 revision / integrity / 外键 / 原始 baseline → 应用指向副本（独立端口 + 显式 `VOCAB_DATABASE_PATH`）跑 **126 项**矩阵：匿名与普通用户越权、三人各自登录、私人词条/学习状态/文章/复习的跨用户读写与列表计数（越权与不存在同形 404）、公共词库三人可读而不可改、私人词库他人读/改/启用均 404、**409 删除守卫及拒绝后数据未变**、实例级设置与备份仅管理员、会话与每日新词队列不串号、S-1 `current_password` 与 CSRF 同源契约。报告 `data/recovery/t8-three-user-report-20260923T134500Z.json`（`verified: true` / `failures: []`，新文件名，既有双账号证据未覆盖）；生产库及其 WAL/SHM 前后逐字节一致 | `tools/staging_three_user_check.py`、`backend/tests/test_staging_three_user_check.py` | M ✅ |
| T9 | ✅ **已完成（2026-09-23，Batch 4）** 会话数量上限淘汰逻辑 + 测试。落在 `services/auth.py::create_session` → `enforce_session_limit`（服务层，CLI/测试/未来登录路径共享同一语义）；**无 migration、无新字段、不引入 `revoke_reason`**；`.env.example` 记录 `VOCAB_MAX_SESSIONS_PER_USER` | `backend/app/services/auth.py`、`backend/app/config.py`、`.env.example`、`backend/tests/test_session_limit.py` | S–M ✅ |
| T10 | ✅ **已完成（2026-09-23，Batch 7）** `history_event` 保留策略：`history-retention preview [--plan]` 与 `apply --plan --confirm <运行 ID>`；窗口由 `VOCAB_HISTORY_EVENT_RETENTION_DAYS` 控制（默认 365、最小 365、`0` = 关闭）；归档 + 清理前备份 + 待提交→committed 凭证，任何失败都故障关闭。测试 `backend/tests/test_history_retention_apply.py`（39 项）与副本演练 `tools/history_retention_staging_drill.py`（含恢复演练） | `backend/app/cli.py`、`backend/app/history_retention.py`、`backend/app/history_retention_preview.py`、`tools/history_archive.py`、`.env.example` | M ✅ |
| T11 | ✅ **已完成（2026-09-23，Batch 5）** SPA 兜底路由加 `/api/**` 例外，返回 404 JSON（T-3）：未知 `/api/**` 的 `GET` 返回 404 JSON，正常前端路由仍返回 SPA 页面；既有 API 与 `/assets` 挂载不变；**未放宽 CSRF**——跨源写请求仍由最外层同源中间件 403 拒绝（`POST`/`HEAD`/`OPTIONS` 打到未知 `/api/**` 由路由给出 405）。测试 `backend/tests/test_spa_fallback.py` 16 项 | `backend/app/main.py` | S ✅ |
| T12 | ✅ **已完成（2026-09-23，Batch 9）** 清理死代码：删除 `backend/app/api/helpers.py::word_dict`（连带因此多余的 `Word` import）与 `backend/app/schemas.py::WordSummary`（连带因此多余的 `from datetime import datetime`）。依据是全仓库引用搜索（`word_dict(` 只命中定义自身，`WordSummary` 只命中定义与文档），**未改动任何序列化路径、未改任何 API 响应**。`services/words.py::apply_learning_update` **按 T-2 决策保留**（`test_import_flow.py` 仍在使用）；剩余候选 `schemas.py::ORMModel` （已无使用者）留待 `word` 表退场决策 | `backend/app/api/helpers.py`、`backend/app/schemas.py` | S ✅ |
| T13 | 版本号决策与落地（E-4）：三处统一；若确定发布则打 tag | `pyproject.toml`、`package.json`、`main.py` | S |
| T14 | ✅ **已完成（2026-09-23，Batch 10）** 处置 `data/staging/` 残留副本（S-5）：只读盘点 **顶层 21 个文件 + 12 个一级目录**（绝对路径/大小/SHA-256/revision/行数；整棵树 86 个文件 / 40 个目录见记录 §addendum），逐项搜索 `data/recovery`、`test-artifacts`、`docs`、`tools`、`backend/tests` 的引用后分三类处置。**删除（第二类：可从可信来源重建且无有效引用）6 个文件 = 两次中止的 G6 演练副本及其 sidecar**；**保留（第一类）**：`migration-rehearsal-0006.db`（被 `rehearsal-0006-to-0007.json` 等引用）、`v1.1-realdata-migration-test.db` 与 `v1.3-acceptance.db`（各自被报告引用，且是 `tools/staging_*_check.py` 的默认目标）、`fresh-clone-0003-to-head.db`（**被 `tests/test_downgrade_guard.py` 引用**）、`batch05-normaluse-baseline.json`（被 Batch 0.5 证据引用）、G6 演练 `…131041Z.db`（设计文档 §8.3 引用其路径与哈希）、T8 采纳运行的副本目录；**保留并报告（第三类）**：`batch05-ab-*.json`、`batch05-normaluse-verification.json`（零引用但记录了过去库状态，不可重建）、`backups/2026-09-22-vocab.db`（用途未定）、6 个 T8 运行副本目录（各自被自己的报告引用；其中 4 个是失败运行）。删除逐条显式路径、无通配符、删除前校验解析路径位于 `data/staging/` 内且无进程占用；删除后复核保留数据库 `integrity_check=ok` 且内容哈希未变、证据文件仍可解析、生产库及 WAL/SHM 逐字节一致（`74442def…`/`6a65ddfe…`/`1c8eced5…`） | `data/staging/`、`data/recovery/t14-staging-disposal-20260923T155046Z.json` | S ✅ |
| T15 | ✅ **基本完成（2026-09-23）** 过时文档陈述（E-6）：`PROJECT_HANDOFF.md` 中与现状矛盾的陈述已就地更正（§8 的"check.ps1 必然失败"已改为已修复并新增 §8.1 校验口径）；`0007-release-record.md` 的 §11 原本就有 §12 附注（"§11 原文不改写"）。**仍存在**：`PROJECT_ROADMAP.md` 自身的历史快照（§2.2 ahead 33、§4.6 E-1 等）未逐条回填——已在 `PROJECT_STATUS_CURRENT.md` 风险 #21 登记 | `docs/` | S |
| T16 | ✅ **已完成（2026-09-23，Batch 6）** 让 `daily_new_words` 生效（P-5）：额度 = 当天（UTC）`review_event.status_before = 'new'` 的**不同词条数**，所以多次请求、复习后再请求都不会叠加；用户级设置封顶当日总量，词库级 `user_lexicon.daily_new_words` 各自限制本词库，两层取 `min`；`limit` 仍是整份队列的长度上限，到期/`weak` 词优先占用它。**零 schema、零 migration、未改调度算法**。（原计划写的 `services/userdata.py` 未改：队列组装落在 `services/study.py`，UTC 日边界抽到 `services/day.py` 与 dashboard 共用） | `backend/app/services/study.py`、`backend/app/services/day.py`、`backend/app/api/study.py`、`backend/app/api/dashboard.py` | M ✅ |
| T17 | ✅ **已完成（2026-09-23，Batch 5）** 补 `.env.example` 的 `VOCAB_DATABASE_PATH`（T-7）：说明它用于**明确选择数据库环境**（留空即 `<数据目录>/vocab.db`），并写明开发与测试**不得指向生产库 `data/vocab.db`**；相对路径按进程工作目录解析，因此要求绝对路径 | `.env.example` | S ✅ |

#### 数据库影响

| 项 | 判定 |
|---|---|
| 是否需要新 migration | **原则上不需要**。S-1/S-2/S-3 的预算与窗口全部在**内存**（`services/limiter.py`，键有上限 + LRU），**不写数据库、不需要 schema** |
| 唯一可能的例外 | **G5 会话上限已落地且未用 migration**（淘汰只写 `revoked_at` + 一条 `session_evicted`）；只有要在 UI 里解释"某设备为何被登出"时才需要 `revoke_reason` → **migration `0008`（可空列，纯增量）**，仍属**可选项，默认不做** |
| 是否触碰生产数据 | **否**。T6 是备份（读），T5 是记录基线（写 `data/recovery/`），T8 只在 `data/staging/` 副本上跑 |
| 硬约束 | 若确需 `0008`：必须**先在 `data/staging/` 用真实数据预演**、提供升级后逐表行数与行指纹不变的证据、走 `rehearsal → backup → migration → verification` 四闸门、**显式 revision 而非 head** |

#### 前端影响

| 项 | 影响 |
|---|---|
| 新增 | 登录设备管理界面（G1）、口令确认对话框复用组件（G2/G3）；**S-1 不新增页面**（前端本来就没有管理员用户管理入口；将来建页面时复用同一对话框，它已支持 429 的 `Retry-After` 倒计时） |
| 修改 | 设置页挂载入口；`api.ts` 的 401/429 统一处理（`Retry-After` 展示）；`auth.tsx` 改密后会话全部撤销的跳转逻辑 |
| 缓存安全 | 确认登出与登录成功时清理 TanStack Query 缓存（`App.tsx` 已用 `key={user.id}` 保证子树重建；**仍需确认 `queryClient.clear()` 或 `queryKey` 前缀**，见 Phase 0 §8.5） |
| 测试 | 设备列表渲染、撤销后列表更新、429 提示、无 token 泄露断言 |

#### 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| 设备管理页误渲染 token 或 `token_hash` | 🔴 | 后端已**绝不返回** token；前端测试加显式断言；沿用"审计与响应不含口令/token"不变量 |
| CSRF 中间件误伤同源前端请求 | 🟠 | 只校验写方法；先在 `scripts/check.ps1` 与前端测试上验证；提供显式白名单（如 `GET /api/health`） |
| 管理员二次认证导致**合法管理员被锁**（改密流程变更） | 🟠 | 保留 CLI `set-password` 逃生口（已存在）；先加测试再改语义 |
| 重建 baseline 时**误把当前库当成可信源** | 🔴 | 严格顺序：**先 T6 建立 verified backup → 再 T5 记录 baseline**；旧 baseline 归档为 `*.prior-attempt-<时间戳>`，**不覆盖** |
| 清理 `data/staging/` 时删掉仍被证据引用的文件 | 🟠 | 逐个核对引用（`rehearsal-0006-to-0007.json` 引用 `migration-rehearsal-0006.db`）；删除后复核 |
| 会话数量上限淘汰策略（G5，2026-09-23 实现） | 🟡→🟢 | 采用"最旧未活动优先"（`COALESCE(last_seen_at, created_at)` 最早，并列取 id 最小）；**新登录永不因超限被拒**，被淘汰者重新登录即可，不丢数据；每次淘汰写 `session_evicted`。**残余**：用户只会看到"掉线"，UI 无法解释原因（需 `revoke_reason`，未做）；持口令者可用反复登录制造登出骚扰（方向相反的取舍已在设计中被论证） |
| 触碰生产库 | 🔴 | 本阶段**不执行任何 migration、不直改生产库**；T8 严格限定在副本 |

#### 出口标准（DoD）

1. `scripts/check.ps1` **exit 0 全绿**（含 `prove_test_isolation.py` 与 `verify_backup.py`）。
2. 存在 0007 生产库的 **verified backup**，且新 baseline 已记录（旧 baseline 归档保留）。
3. `git status -sb` 的 ahead 数已归零或已按记录推送。
4. 设备管理、自服务改密、管理员二次认证三者在浏览器中可用，且有前端测试。
5. 「每日新词数」设置确实影响学习队列（P-5），有后端测试断言；`.env.example` 已补 `VOCAB_DATABASE_PATH`（T-7）。
6. 副本三账号验收报告 `verified: true` / `failures: []`，证据落盘为**新文件名**（不得覆盖既有证据）。
7. 生产库仍为 `0007`（或经批准的新 revision），`integrity_check=ok`、`foreign_key_check=0`、行数与行指纹不变。
8. 后端 + 前端全量验收通过（后端 pytest、ruff；前端 test/typecheck/lint/build）。

#### 出口标准达成情况（2026-09-23 知识冻结时实测）

| DoD | 状态 | 证据 / 缺口 |
|---|---|---|
| 1 `check.ps1` exit 0 | ✅ 达成 | `All checks passed.`，含 `prove_test_isolation.py`（`data/` 106 文件零变化）与 `verify_backup.py`（VERIFIED BACKUP） |
| 2 0007 verified backup + 新 baseline | ✅ 达成 | `post-0007-verified-20260923-001237-vocab.db` + 重录的 `baseline.json`；两份旧基线归档保留 |
| 3 ahead 归零 | ✅ 达成 | ahead 0 / behind 0 |
| 4 设备管理 / 自助改密 / **管理员二次认证** 浏览器可用 + 前端测试 | ⚠ **部分** | 设备管理（F-1）与自助改密（F-7）已交付：10 条前端集成测试（`security.test.tsx`）+ 活实例上线新 bundle。**S-1 的后端守卫与 27 项测试已完成**，但**前端从来就没有管理员用户管理入口**（[实测] `frontend/src/` 全仓库无 `/api/users` 调用），所以"在浏览器里点一遍"这一条只对 F-1/F-7 成立，对 S-1 **不适用**（其验收口径改为后端契约 + 测试，见 `V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md` §7）。是否新建管理员页面属**另一次产品决策**，本批不做。**2026-09-24 Batch 11 已在真实浏览器上完成 F-1/F-7 的完整点击验收（13/13 步骤通过，记录 `data/recovery/dod4-browser-acceptance-20260923T162528Z.json`），并给出建议口径：F-1/F-7 走浏览器验收、S-1 走后端契约 + 安全测试 + T8 三账号副本验收——证据与口径见 `docs/V1.2-PHASE2.8-F-DOD4-BROWSER-ACCEPTANCE.md`，**待负责人确认，故本行仍为 ⚠ 部分** |
| 5 `daily_new_words` 生效 + `.env.example` 补 `VOCAB_DATABASE_PATH` | ✅ **达成（2026-09-23）** | **T17 已于 Batch 5 完成**、**G8/T16 已于 Batch 6 完成**：额度按当天已开始学的新词累计（跨请求不叠加、复习后不补词），到期词不被新词额度或 `limit` 挤掉；测试 `backend/tests/test_daily_new_words.py`（25 项） |
| 6 副本**三账号**验收 `verified: true` | ✅ **达成（2026-09-23，Batch 8）** | `data/recovery/t8-three-user-report-20260923T134500Z.json`：**126/126 PASS**、`failures: []`、`verified: true`，新文件名；既有双账号证据（`post-0007-two-user-report.json`、`post-0007-isolation-report.json`）未被覆盖 |
| 7 生产库仍为 0007、完整性不变 | ✅ 达成 | revision `0007`、`integrity_check=ok`、`foreign_key_check=0`；`check.ps1` 逐表行指纹比对 17/17 `ok` |
| 8 后端 + 前端全量验收 | ✅ 达成 | 后端 **365 passed** + ruff 全通过；前端 **29 passed** + typecheck/lint/build 通过 |

> **结论**：Phase 2.8 **尚未完成**（S-1 已于 2026-09-23 Batch 3 关闭、T11 与 T17 已于 Batch 5 关闭、G8/T16 已于 Batch 6 关闭，但 DoD 4 对 S-1 的"浏览器可用"口径不适用）。剩余：**DoD 4 口径的负责人确认**（F-1/F-7 已于 Batch 11 在真实浏览器验收通过，证据与建议口径见 `docs/V1.2-PHASE2.8-F-DOD4-BROWSER-ACCEPTANCE.md`；S-1 无前端入口，其口径见该文件 §4）、T13 版本决策，以及 **G6 保留策略的生产执行**（需负责人批准具体计划与运行 ID 并安排维护窗口）。**不要把本阶段标记为已完成，也不要提前统一版本号或打 tag。**

---

### 6.2 Phase 3 · PWA 与移动端可用性

> **一句话**：让拾词在手机上**真的能用**——装到主屏、底部导航不遮挡、**能翻到完整释义**。

| 项 | 内容 |
|---|---|
| **优先级** | **P1**（可与 Phase 2.8 并行开发） |
| **规模** | L–XL |
| **前置依赖** | 无（本阶段**不动后端、不动 schema**） |
| **出口里程碑** | M2 |

#### 预计收益

| 收益 | 可验证的判定信号 |
|---|---|
| 手机上可用 | 在 ≤900px 视口下能看到音标、完整释义、复习历史、文章暴露 |
| 可安装 | Android/iOS 可从浏览器"添加到主屏幕"，以 `standalone` 启动，有独立图标与状态栏配色 |
| 二次访问秒开 | SW 预缓存后二次访问静态资源命中缓存；`/api/**` **永不**进入缓存 |
| 弱网可用 | 首次加载 ≈114 KB（gzip）→ 配合 `.br` 预压缩再省约 20% |
| 不遮挡 | iPhone Home Indicator 不再压住底部导航 |
| 不越账号边界 | 切换账号后 SW 缓存**不含**任何上一账号私有数据（红线） |

#### 功能目标

| ID | 目标 |
|---|---|
| G1 | **F-2（★ 最高风险项）单词详情面板移动化**：≤900px 不再 `display:none`，改为抽屉 / 底部卡片 / 独立路由（**需先做 UI 设计决策**） |
| G2 | **F-3** 导入图片列表移动化（或明确产品上"导入是桌面功能"并给出移动端提示） |
| G3 | **F-4** iOS safe-area 适配 + `viewport-fit=cover` |
| G4 | **F-5** PWA viewport + apple 元信息 |
| G5 | PWA 安装能力：manifest、SW、192/512/180 图标、`display: standalone`、`theme-color` |
| G6 | **F-6** 底部导航拥挤问题（若新增入口则重设计为 5 项 + 中央加号） |
| G7 | 静态资源预压缩（`.br`） |

#### 技术任务

| # | 任务 | 涉及位置 | 规模 |
|---|---|---|---|
| T1 | 为 F-2 做**移动端交互设计决策**（三选一：抽屉 / 贴底卡片 / 独立路由）并记录理由。**注意**：≤720px 时 `.lookup-card` 已变贴底卡片（`bottom: 78px`），需与其避让 | `docs/` 设计文档 | S |
| T2 | 实现 F-2 | `frontend/src/pages/LibraryPage.tsx`、`styles.css` | M–L |
| T3 | 实现 F-3 | `frontend/src/pages/ImportPage.tsx`、`styles.css` | S–M |
| T4 | 新建 `frontend/public/manifest.webmanifest`（`name` / `short_name` / `start_url` / `display: standalone` / `theme_color` / `icons`） | `frontend/public/`（**当前不存在，需新建**） | S |
| T5 | 生成 PWA 图标 192/512/180（apple-touch）。**注意** `assets/shici-app.png`(741KB) 与 `.ico` 是**桌面快捷方式**用的，不要直接复用 | `frontend/public/` | S |
| T6 | 写 Service Worker。**缓存策略是硬约束**（Phase 0 §8.4）：`CacheFirst` 仅用于 `/assets/*`（带 hash、内容寻址）、PWA 图标与 manifest；`/index.html` 用 `NetworkFirst` 或 `StaleWhileRevalidate`；**`/api/**` 与任何带 Cookie 的响应一律 `NetworkOnly`** | `frontend/` | M–L |
| T7 | SW 版本管理与更新：版本号写死在 SW 内，`activate` 时清除旧版本 Cache；**不做后台同步、不做离线写队列**（非目标） | SW | S–M |
| T8 | `index.html` 补 apple 元信息、`viewport-fit=cover`、保留 `theme-color` | `frontend/index.html` | S |
| T9 | safe-area 适配：`env(safe-area-inset-*)` 用于固定底部导航 | `styles.css` | S |
| T10 | 底部导航重排（F-6） | `styles.css`、`AppShell.tsx` | S–M |
| T11 | `.br` 预压缩产物生成（构建脚本），为 Phase 4 的 Caddy `precompressed` 做准备 | 构建流程 | S |
| T12 | 移动端回归测试：断点快照/交互测试（≥900px 与 ≤900px 两套）；账号切换后 SW 不返回上一账号数据的断言 | `frontend/src/**/*.test.tsx` | M |

#### 数据库影响

**无。** 本阶段**不新增 migration、不改任何表、不触碰生产库**。全部改动落在 `frontend/`。

> 唯一需要注意：SW 绝不能让私有 API 响应进入缓存——这既是隐私红线，也是"一个用户的数据不能出现在另一个用户的设备上"的延伸。

#### 前端影响

| 项 | 影响 |
|---|---|
| 新增文件 | `frontend/public/`（manifest、图标）、Service Worker 注册代码 |
| 修改 | `styles.css`（约 34 KB，多个断点）、`index.html`、`LibraryPage.tsx`、`ImportPage.tsx`、`AppShell.tsx`、构建脚本 |
| 构建产物 | `frontend/dist` 新增 manifest/图标/SW/`.br`；由 `scripts/start-vocab.ps1` 构建，**构建或迁移失败即中止**（不会运行旧页面） |
| 路由 | 若 F-2 选择"独立路由"，`App.tsx` 需新增路由；`main.py` 的 SPA 兜底**已能正确优先返回真实文件**（Phase 0 §B8），无需改后端 |

#### 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| **SW 缓存污染跨账号私有数据** | 🔴 | `/api/**` 与带 Cookie 响应用 `NetworkOnly`；账号切换后清理 Cache；加自动化断言 |
| 部署新版本后用户停留在旧 shell | 🟠 | SW 版本号写死 + `activate` 清旧 Cache；明确更新提示策略（不做静默强制刷新） |
| F-2 改动破坏桌面端布局 | 🟠 | 断点回归测试两套；`styles.css` 是多断点耦合文件，改动需在 ≥900px 与 ≤900px 双向验证 |
| 固定底部导航与新浮层互相遮挡 | 🟠 | T1 设计阶段显式处理与 `.lookup-card`（`bottom: 78px`）的避让 |
| 图标/主题色与现有视觉不一致 | 🟡 | 复用 `#faf9f6` 主题色与既有品牌资产 |
| 用 PWA 图标直接复用 741KB 桌面 PNG 导致安装包过大 | 🟡 | T5 单独生成专用尺寸 |
| 范围蔓延到离线写入 | 🟠 | 明确非目标：**不做后台同步、不做离线写队列** |

#### 出口标准（DoD）

1. ≤900px 视口下**完整释义、音标、复习历史、文章暴露全部可达**（F-2 消除）。
2. Android 与 iOS 均可"添加到主屏幕"并以 `standalone` 启动，图标与状态栏正确。
3. iPhone 上底部导航不被 Home Indicator 遮挡。
4. SW 缓存的**自动化断言**通过：`/api/**` 请求未被缓存；切换账号后无上一账号数据。
5. 桌面端（≥900px）无视觉回归；前端 test / typecheck / lint / build 全绿。
6. 无后端改动、无 migration（若确实需要，必须重新评估并走 §7.1 流程）。

---

### 6.3 Phase 4 · 生产部署上线

> **一句话**：把拾词从"本机应用"变成"**可公网访问、自动备份、能回滚**的服务"。

| 项 | 内容 |
|---|---|
| **优先级** | **P1**（建议在 Phase 3 之后） |
| **规模** | L–XL |
| **前置依赖** | **Phase 2.8**（必须有 verified backup、门禁可用、CSRF 到位）；建议 Phase 3 完成 |
| **出口里程碑** | M3 |

#### 预计收益

| 收益 | 可验证的判定信号 |
|---|---|
| 产品离开本机 | 域名 + HTTPS 可访问；`/api/health` 返回 200 |
| 数据不再靠人工 | systemd timer 每日自动备份 + 保留策略 + `integrity_check` 校验，失败以非零退出码结束并被记录 |
| 灾难可恢复 | 存在**项目外**的备份副本；已按 runbook 完整演练一次恢复 |
| 登录安全成立 | `VOCAB_COOKIE_SECURE=true` + HTTPS；Cookie 不再可能被明文传输 |
| IP 限流正确 | 反向代理后配置 `--proxy-headers --forwarded-allow-ips <代理地址>`，每用户独立计数桶（S-4） |
| 云端可启动 | `VOCAB_ENABLE_OCR=false`，PaddleOCR 缺失不再导致启动崩溃 |

#### 功能目标

| ID | 目标 |
|---|---|
| G1 | 反向代理 + HTTPS + 域名（Phase 0 §G.4 指向 Caddy） |
| G2 | systemd service + timer，服务常驻 |
| G3 | **D-4 备份体系**：定时备份、保留策略、完整性校验、失败可见性、异地副本 |
| G4 | 云端 OCR 关闭（`VOCAB_ENABLE_OCR=false`），OCR 代码与数据原样保留 |
| G5 | 部署期 migration 执行规程（生产库只能走 §7.1 流程） |
| G6 | 回滚预案与一次完整的恢复演练 |
| G7 | 基础可观测性：健康检查、日志轮转、磁盘水位告警 |

#### 技术任务

| # | 任务 | 涉及位置 | 规模 |
|---|---|---|---|
| T1 | 新建 `deploy/`（**当前不存在**）：Caddyfile、systemd unit、timer、部署脚本 | `deploy/` | M–L |
| T2 | HTTPS 与域名；`VOCAB_COOKIE_SECURE=true`（Phase 0 §F9 已预留该开关） | 配置 | M |
| T3 | **`uvicorn --proxy-headers --forwarded-allow-ips <代理地址>`**；**保持单 worker**（S-4：内存限流在多 worker 下等效阈值 × worker 数） | `deploy/`、`scripts/` | S |
| T4 | 新增 CLI `python -m app.cli backup --label daily`：复用 `create_backup`（保持天级幂等语义），脚本层负责 ①迁移/写入前确保当日备份存在 ②备份后 `PRAGMA integrity_check` ③失败非零退出（Phase 0 §9.4） | `backend/app/cli.py` | M |
| T5 | systemd timer：每日固定时刻（如 03:30）+ `Persistent=true` 补跑漏过的；**timer 独立于 service**，服务未运行也能备份 | `deploy/` | M |
| T6 | 保留策略：保留最近 N 份日备份（建议 30）+ **全部 manual 备份**；清理逻辑**只删自己生成的 `daily-*.db`，绝不匹配 `manual-*`** | CLI / 脚本 | S–M |
| T7 | 异地副本：每日把当天备份复制到项目外目录或对象存储（解决 Phase 0 §9.2 的 S4「同盘丢失」） | `deploy/` | S–M |
| T8 | 备份事件写入 `history_event(event_type='scheduled_backup')`，Dashboard 可展示（Phase 0 §9.4 第 5 条） | 后端 | S |
| T9 | 云端 `VOCAB_ENABLE_OCR=false`；验证缺 Paddle 时启动不崩溃（Phase 0 §F11） | 配置 + 测试 | S |
| T10 | 日志轮转与磁盘水位告警；`data/logs/` 已有 `server-out.log` / `server-error.log` | `deploy/` | S–M |
| T11 | 写部署 runbook（与 0007 迁移 runbook 同风格）：首次部署、升级、回滚、恢复演练四节 | `docs/` | M |
| T12 | 在服务器上执行**一次真实恢复演练**：从异地备份恢复到一台干净机器并验证 `integrity_check` / `foreign_key_check` / 行指纹 | 运维 | M |
| T13 | 依赖与运行时固化：Python/Node 版本、`frontend/dist` 构建产物交付方式、`pip` 与 `npm` 锁文件 | `deploy/` | S–M |
| T14 | （若面向中国大陆）ICP 备案与域名合规确认 | 非技术 | — |

#### 数据库影响

| 项 | 判定 |
|---|---|
| schema（migration） | **不需要新 migration**。本阶段不新增表/列 |
| 生产库写入 | **仅通过应用与 §7.1 迁移流程**；禁止手工 UPDATE/INSERT/DELETE |
| 运维性影响（重要） | ①备份体系从"启动时"改为"定时 + 保留策略 + 异地"；②**必须保持 WAL 感知**——备份只能用 SQLite backup API 或先满足"WAL = 0 字节"门（D-3：当前生产库曾积累 156592 B 未 checkpoint 的 WAL，裸拷贝会静默丢页并回退到 `password_hash='!'`）；③保留策略不得删除 manual 备份 |
| 若部署期需要升级 revision | 严格走 `rehearsal → backup → migration → verification` 四闸门，**显式 revision 而非 head**；`scripts/start-vocab.ps1` 会先跑 `alembic upgrade head`，因此**一次未受控的启动就等于一次没有备份和日志的隐式迁移** |

#### 前端影响

| 项 | 影响 |
|---|---|
| 构建产物 | `frontend/dist` 由 Caddy 直接托管（含 Phase 3 的 manifest/SW/图标/`.br`） |
| 缓存头 | 带 hash 的 `/assets/*` 长缓存；`index.html` 与 SW **短缓存或不缓存**，避免更新不生效 |
| 预压缩 | Caddy `precompressed` 消费 Phase 3 的 `.br` 产物 |
| 环境差异 | 前端需确认在 HTTPS 下所有请求走同源（无 CORS）；`credentials:'same-origin'` 行为不变 |
| 无需改动 | 本阶段**不改前端源码**（除可能的基线路径/构建脚本微调） |

#### 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| **误开多 worker 导致限流失效** | 🔴 | 部署脚本显式固定 `workers=1` 并加注释说明原因；加部署自检 |
| **反向代理后 IP 计数退化为全局单桶** | 🔴 | 强制 `--proxy-headers --forwarded-allow-ips <代理地址>`；上线后验证两个不同 IP 的独立计数 |
| **裸拷贝数据库导致静默丢数据** | 🔴 | 只用 backup API 或先验证 WAL = 0；把该检查写进部署脚本（既有 runbook 已有此门） |
| 备份与数据库同盘 → 磁盘损坏同时丢失 | 🟠 | T7 异地副本 |
| `VOCAB_COOKIE_SECURE` 配错（true 但无 HTTPS → 登不上；false 但有 HTTPS → 弱化安全） | 🟠 | 上线前同时验证两种配置；先 HTTPS 后开 Secure |
| 首次部署即执行未预演的 migration | 🔴 | 强制 rehearsal；禁止 `upgrade head` 打生产库 |
| 磁盘写满（SQLite + 图片 + OCR 模型 + 备份） | 🟠 | 磁盘水位告警 + 保留策略 + 明确 `VOCAB_DATA_DIR` |
| 云上无 PaddleOCR 致启动崩溃 | 🟠 | `VOCAB_ENABLE_OCR=false` + 启动测试 |
| 无监控 → 故障静默 | 🟠 | 健康检查 + 日志轮转 + 备份失败非零退出 |
| 公网暴露放大 S-1/S-2 的影响 | 🔴 | **必须在 Phase 2.8 完成后再上线**；本阶段不得跳过该依赖 |

#### 出口标准（DoD）

1. 域名 + HTTPS 可访问；`GET /api/health` 200；`VOCAB_COOKIE_SECURE=true`。
2. 单 worker 已固化并有文档说明原因；`--proxy-headers` 生效且经过双 IP 验证。
3. systemd timer 连续运行 ≥3 天，每日备份存在、`integrity_check` 通过、保留策略生效、异地副本存在。
4. **真实恢复演练完成一次**并有证据记录。
5. 部署 runbook（首次/升级/回滚/恢复）定稿。
6. 生产库 revision 与代码 head 一致；`integrity_check=ok`、`foreign_key_check=0`。
7. 未出现任何：手工改生产库、`upgrade head` 直打生产、跳過 rehearsal、生产 downgrade、改写历史 migration。

---

### 6.4 Phase 5 · 学习算法升级

> **一句话**：把复习调度从**固定天数阶梯**升级为**随个人记忆表现自适应**的间隔重复算法，并把词频与学习统计补齐。

| 项 | 内容 |
|---|---|
| **优先级** | **P2**（产品核心价值，但必须在有备份与自动备份的环境里做） |
| **规模** | **XL**（需拆成 5.1–5.4 子阶段） |
| **前置依赖** | Phase 4（verified backup + 自动备份 + 可回滚）；设计工作可与 Phase 3/4 并行 |
| **出口里程碑** | M4 |

#### 现状（[实测]，必须作为设计出发点）

`backend/app/services/scheduler.py` **全文 41 行**，算法是**固定天数阶梯**，**没有任何按词或按人的难度参数**：

```python
PROGRESSION = {
    "new":       ("familiar", 1),    # 1 天
    "familiar":  ("learning", 3),    # 3 天
    "learning":  ("known",    7),    # 7 天
    "known":     ("mastered", 14),   # 14 天
    "mastered":  ("mastered", 30),   # 30 天，封顶
}

def calculate_schedule(current_status, result, consecutive_failures, *, now=None) -> Schedule:
    if result == "fail":   return Schedule("weak", now + 12h 或 4h, failures + 1)   # 首次失败 12h，连续失败 4h
    if result == "fuzzy":  return Schedule("learning", now + 1d, 0)
    if result == "know":   → 按 PROGRESSION 阶梯推进；weak 状态答对则回到 learning + 2 天
```

**评分制**：`1/2/3` → `fail` / `fuzzy` / `know`（学习页空格揭晓、三键评分）。

**已存储的调度相关字段**（`user_word_state`，V1.2 的每用户每词条学习状态）：`status`、`next_review_at`、`first_seen`、`last_review`、`recall_success`、`recall_fail`、`consecutive_failures`、`context_exposure`、`anchor_override`、`semantic_note`、`legacy_word_id`。**注意：没有 ease / interval / stability / difficulty / lapse 之类字段**——这正是升级要新增的。

**已具备的关键优势**：`review_event` **每次复习都全量留痕**（`timestamp` / `result` / `source` / `status_before` / `status_after` / `review_type`），累计数字只是缓存。**这意味着新算法可以从历史事件离线回放/标定**，而不必从零开始。

**相关但独立的三处现状（升级时必须一并处理）**：

1. **学习队列没有每日上限**：`GET /api/study/today` 只用 `limit`（默认 50，上限 200），**不读 `daily_new_words`**（见 P-5）。算法升级会改变到期分布，因此必须先把"每天给多少"这个口径定清楚。
2. **选词（文章生成）优先级**：`services/reading.py::select_target_words` 用的是**学习状态字段**而非词频——优先级为 `weak`(0) → `consecutive_failures > 0`(1) → `first_seen` 2 天内(2) → `new`(3) → 其他(4)，再按 `last_review IS NOT NULL` / `last_review` / `context_exposure` 排序。**词频接入（P-1）会改动这一层**，与调度算法是两个独立的改动点。
3. **连续天数与"新词"口径目前是临时实现**：`streak_days` 在 Python 里从最近 1000 条 `ReviewEvent.timestamp` 重算（T-6，超过 1000 条后可能算错）；`today_new` 用"`first_seen` 是今天"判定。算法升级与统计页（G5）需要把这两个口径固化为可测试的定义。

#### 预计收益

| 收益 | 可验证的判定信号 |
|---|---|
| 复习量可控 | 同一用户的日均到期量随熟练度下降而收敛，而非固定阶梯下的刚性增长 |
| 难词得到更多关注 | 失败词的实际下次复习间隔显著短于一次通过的词（统计可验证） |
| 效果可量化 | 可产出留存曲线 / 首次通过率 / 平均复习次数等指标 |
| 核心承诺不破 | 每次复习仍写 `review_event`；累计数字仍可由事件重算；**历史数据零丢失** |
| 词频能力补齐 | `frequency_rank` 有真实数据来源，选词从 `sequence`/`id` 回落升级为词频优先 |

#### 功能目标

| ID | 目标 |
|---|---|
| G1 | **自适应间隔**：引入按词（或按 用户×词）的难度/稳定度参数，替代固定阶梯 |
| G2 | **算法可回放**：能从 `review_event` 历史重建调度状态（用于标定与迁移校验） |
| G3 | **切换安全**：算法切换对已有用户**不产生"到期洪峰"或"到期真空"** |
| G4 | **P-1 词频导入**（外部资料就绪时）：导入脚本 + `frequency_source` 标注 + 选词顺序升级 |
| G5 | **P-3 学习统计**：进度趋势、到期预测、难词清单 |

#### 技术任务（建议拆为 4 个子阶段）

**5.1 设计（只读，先设计后实现——沿用本仓库 Phase 2.x 的一贯节奏）**

| # | 任务 |
|---|---|
| T1 | 算法选型评估：**SM-2 风格（ease factor + interval）** vs **FSRS 风格（stability + difficulty + retrievability）**。给出取舍矩阵：实现复杂度、可解释性、数据需求、调参风险 |
| T2 | **离线标定**：用现有 `review_event`（当前 10 条）与副本上的历史数据评估两种算法，产出对比报告。**必须说明数据量不足时的降级策略** |
| T3 | 定义新字段与语义（候选：`interval_days`、`ease_factor`、`stability`、`difficulty`、`lapse_count`、`lapses`、`reps`、`algorithm_version`） |
| T4 | 定义**切换策略**：`algorithm_version` 字段 + 逐用户/全局开关；旧数据回填规则；切换时 `next_review_at` 的重算与上限保护 |
| T5 | 定义口径：为什么新字段放在 `user_word_state`（**每用户每词条**，语义正确）而不是 `word` 或 `lexicon_entry`（词条本体，跨用户共享） |
| T6 | 产出设计文档（`docs/`），含回滚预案 |

**5.2 实现**

| # | 任务 | 涉及位置 | 规模 |
|---|---|---|---|
| T7 | migration `0008_*`：`user_word_state` 新增可空列 + `server_default`（**纯增量，旧代码仍可运行**）。**注意到期查询索引已存在，不要重复添加**：`ix_uws_user_due`（`user_id, next_review_at`）与 `ix_uws_user_status`（`user_id, status`）已在 migration `0005` 建立；`ix_lexicon_entry_frequency`（`lexicon_id, frequency_rank`）同样已存在 | `backend/alembic/versions/` | M–L |
| T8 | 新调度器实现（保留 `scheduler.py` 的纯函数风格与 `Schedule` 结构，便于测试与回放） | `backend/app/services/scheduler.py` | L |
| T9 | 学习服务接线：`services/study.py` 写 `review_event` 时同时更新新字段；**保持"每次复习必须写 `review_event`"不变量** | `services/study.py` | M |
| T10 | **回填**：为已有 `user_word_state` 行计算初始难度参数；产出"回填前后 `next_review_at` 分布对比"证据 | 脚本 + 迁移 | M–L |
| T11 | 回放工具：从 `review_event` 重建任意时点的调度状态，用于验证与审计 | `tools/` | M–L |
| T12 | 全覆盖测试：边界（长时间未复习、连续失败、一次性大量复习、时区 naive/aware）、以及"旧算法 → 新算法"的一致性对照 | `backend/tests/` | L |
| T13 | `word` 表遗留字段（`recall_success` / `consecutive_failures` 等）在新算法下的处置口径（**与 T-1 退场计划对齐，本阶段不删表**） | 设计 | S |

**5.3 词频（P-1，依赖外部资料）**

| # | 任务 |
|---|---|
| T14 | 接收外部词频文件并定义格式（**不得用 AI 编造排名**，Phase 0 §F3 明令） |
| T15 | 导入脚本：写 `frequency_rank` / `frequency_count` / `frequency_source`（**`frequency_source` 必填**，用于溯源） |
| T16 | 选词逻辑升级：从 `frequency_rank` 为 NULL 时回落 `sequence`/`id` → 变为词频优先，并明确回落顺序。**注意 `sequence` 当前从未被应用代码读或写（P-6）**，接线时需同时定义它由谁写入 | `services/reading.py::select_target_words` | M |
| T17 | 按词频重排时**不得改变已有用户的学习状态与 `next_review_at`**（只影响"选哪些新词"） |

**5.4 统计与洞察（P-3）**

| # | 任务 |
|---|---|
| T18 | 后端统计端点（留存、进度、到期预测），**必须在 SQL 里按 `user_id` 过滤**（沿用 §7.2 不变量） |
| T19 | Dashboard / 新页面展示；移动端可用（依赖 Phase 3 的响应式基础） |
| T20 | 前端测试 + 后端 IDOR 测试（新端点必须纳入权限矩阵） |
| T21 | **修正连续天数计算（T-6）**：`streak_days` 改为 SQL 聚合或按需存储，替代"取最近 1000 条 `ReviewEvent.timestamp` 在 Python 里算" | `backend/app/api/dashboard.py` | S–M |
| T22 | 固化「新词」与「到期」的定义：当前 `today_new` = `first_seen` 为当日、`due_reviews` = `next_review_at <= now` OR `status == 'weak'`。算法升级后需作为可测试口径写进文档 | `docs/`、`api/dashboard.py` | S |

#### 数据库影响

| 项 | 判定 |
|---|---|
| **需要 migration** | **是**，预计 `0008`（新字段）与可能的 `0009`（索引/统计）。**这是本路线图唯一必然需要 schema 变更的阶段** |
| 表 | 主要影响 `user_word_state`（每用户每词条学习状态）；`lexicon_entry` 仅受词频任务影响（**列已存在，只需填数**） |
| 写入路径 | `services/study.py` 在写 `review_event` 的同一事务中更新新字段 |
| 硬约束 | ①纯增量（可空 + `server_default`，保证"旧代码 + 新库"可运行）；②**不改历史 migration**；③**不改业务数据语义**；④在 `data/staging/` 用**真实数据**预演；⑤提供升级前后**逐表行数与行指纹不变**的证据（除目标列）；⑥显式 revision 执行；⑦`downgrade` 只允许对一次性副本执行 |
| 回填风险 | 回填会改变 `next_review_at` → 直接改变用户"今天该学什么"。**必须先产出分布对比并设定上限保护**（例如到期日不得晚于/早于某阈值） |
| 不可破坏 | `review_event` 是审计真相，**任何情况下不得删除或重写历史事件**；累计数字始终可由事件重算 |

#### 前端影响

| 项 | 影响 |
|---|---|
| 学习页 | 可能展示"下次复习间隔"提示；评分语义（1/2/3）**保持不变以免破坏用户习惯**（若需扩展为 4 档，必须单独设计并迁移旧事件语义） |
| 词库页 | 可展示词频排序/筛选项（依赖 P-1） |
| 统计 | 新增统计视图（G5） |
| 兼容 | 若用 `algorithm_version` 控制，前端需能处理两种口径的字段；避免在前端硬编码算法假设 |
| 测试 | 学习流程回归（评分 → 排期 → 下次到期）、统计页渲染 |

#### 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| **切换算法产生到期洪峰或到期真空**（用户某天突然几百词到期，或连续多天无词可学） | 🔴 | T4 定义切换策略 + 上限保护；T10 产出分布对比证据；先在副本上用真实数据验证 |
| **历史数据不足以标定参数**（当前生产仅 10 条 `review_event`） | 🔴 | 明确降级策略：参数取文献默认值 + 慢速自适应；**不得假装已完成标定**；T2 必须如实报告数据不足 |
| 回填脚本误改生产数据 | 🔴 | 只能在副本预演 + 行指纹证据 + 显式 revision；沿用三级数据库环境（Level 3 开发期禁止写入） |
| 算法复杂度上升导致可解释性下降（用户不理解为何某词明天/30 天后出现） | 🟠 | 保留可读的"下次复习"提示；文档化算法行为；不引入不可解释的黑箱 |
| FSRS 需要大量复习历史才有收益 | 🟠 | 若数据量不足，先在 SM-2 风格上落地（可解释、参数少），路线图上标注 FSRS 为后续演进 |
| 新字段与 `word` 遗留字段双写不一致（T-1） | 🟠 | 明确唯一写入方（`user_word_state`），`word` 表标记为只读遗留；加静态守卫测试 |
| 词频导入引入脏数据/无溯源数据 | 🟠 | `frequency_source` 必填；导入前校验；**禁止 AI 编造** |
| 新统计端点漏掉用户隔离 | 🔴 | 新端点必须走 `services/userdata.py` 同一访问层，并加入 IDOR 测试矩阵 |
| migration 与 `word` 表退场（T-1）混在一起 | 🔴 | **严格分离**：本阶段不删任何表/列；退场留到 Phase 6 |

#### 出口标准（DoD）

1. 设计文档完成并通过评审，含算法选型矩阵、回填策略、切换策略、回滚预案。
2. `0008` migration 在 `data/staging/` 用真实数据预演 PASS，且**逐表行数与行指纹不变**（除目标列）证据落盘。
3. 副本上完整验证：切换前后 `next_review_at` 分布对比在可接受范围内，**无到期洪峰/真空**。
4. 回放工具能从 `review_event` 重建调度状态，与线上状态一致。
5. 后端与前端全量验收通过；新端点已纳入 IDOR 权限矩阵。
6. 生产库 revision 与代码 head 一致；`integrity_check=ok`、`foreign_key_check=0`。
7. `review_event` 历史零丢失、零改写；累计数字仍可由事件重算。
8. 若 P-1 未就绪（外部资料未提供），**如实标注为未完成，不得用编造数据填充**。

---

### 6.5 Phase 6 · 平台化与长期演进

> **一句话**：降低长期维护成本，为规模增长留出空间。**本阶段的每一项都必须先有独立的风险评估。**

| 项 | 内容 |
|---|---|
| **优先级** | **P3** |
| **规模** | XL（按需拆分，可能长期滚动） |
| **前置依赖** | Phase 4 + Phase 5 |

#### 预计收益

| 收益 | 判定信号 |
|---|---|
| 结构风险消除 | `word` 表退场完成，单一 id 命名空间 |
| 质量门禁自动化 | CI 在每次推送时跑后端测试 + ruff + 前端四件套 + 隔离取证 |
| 故障可定位 | 结构化日志 + 关键指标（到期量、AI 调用量、失败率） |
| 规模可扩展 | 多用户/大词库下的性能与并发行为有数据支撑 |

#### 功能目标与技术任务

| ID | 目标 | 技术任务 | 风险 |
|---|---|---|---|
| G1 | **T-1 `word` 表退场**（Phase 0 §F4：未来最大结构风险，被 4 张表外键引用） | ①**风险评估先行**（独立文档）；②双读校验期；③`user_word_state.legacy_word_id` 解析路径的统一；④SQLite 下重建表会牵动 `review_event` / `article_word_exposure` / `article_word_lookup` / `import_candidate`；⑤清理 T-2 残余死代码（`services/words.py::apply_learning_update`、`schemas.py::ORMModel`；`helpers.word_dict` 与 `schemas.py::WordSummary` 已于 Batch 9 删除） | 🔴 **高风险**：SQLite 重建表 + 遗留 id 命名空间（两条路径**刻意不互相回退**，混用得 404）。必须独立阶段、独立预演、独立回滚预案 |
| G2 | **E-5 CI** | `.github/workflows`：后端 pytest、ruff、前端 test/typecheck/lint/build、`prove_test_isolation.py`。**注意** `check.ps1` 的 verified backup 步骤依赖本机 `data/`，CI 中需参数化或跳过并标注 | 🟠 测试隔离必须成立（不得让 CI 触碰真实 `data/`） |
| G3 | 可观测性 | 结构化日志、请求耗时、AI/OCR 失败率、到期量指标；**必须脱敏**（不含口令/token/Key） | 🟠 日志泄露风险 |
| G4 | 并发与规模验证 | 多用户/大词库压测；评估 S-4（内存限流）是否需要迁移到持久化存储；**任何多 worker 决策必须先解决限流共享问题** | 🟠 误开多 worker 直接削弱防滥用 |
| G5 | 导入/OCR 体验 | 大图与批量导入性能、OCR 失败恢复、移动端快速添加 | 🟡 |
| G6 | 数据可移植 | 导出/导入（JSON 或 SQLite dump）、跨机器恢复文档化 | 🟡 |
| G7 | 多租户/云端形态决策（P-4 注册、Key 归属长期方案、共享词库） | 需产品决策；若开放注册需重新评估安全面（S-1/S-2/S-3 全部升级） | 🔴 安全面显著扩大 |

#### 数据库影响

| 项 | 判定 |
|---|---|
| G1 必然涉及 schema 重建 | SQLite 增删列/外键的唯一办法是重建表；**历史已证明这是本项目的高危操作**（`0007` 补 9 个外键即重建 7 张表，且 `0004/0005` 曾因 `batch_alter_table` 静默丢弃外键约束） |
| 硬约束 | 走 §7.1 全流程；**不得删除或重建 `data/vocab.db`**；`downgrade` 只允许对一次性副本执行 |
| 其他项 | G2–G7 原则上无 schema 影响（G4/G7 若引入持久化限流或租户隔离则需重新评估） |

#### 前端影响

| 项 | 影响 |
|---|---|
| G1 | `word` 命名空间退场后，`GET /api/words/{id}` 与 `POST /api/study/words/{id}/review` 的 id 语义需要统一 → **前端调用点必须同步**（当前两套命名空间刻意不互相回退） |
| G5/G6 | 导入体验与数据导出/导入 UI |
| G7 | 若开放注册 → 登录/注册/找回页面 |

#### 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| G1 重建表期间数据丢失 | 🔴 | 沿用 `0007` 的成例：迁移内建"断言外键关闭"与收尾 `foreign_key_check` 守卫；失败整体回滚；先在副本注入故障演练 |
| G1 遗留 id 命名空间混用导致静默错行 | 🔴 | 保留 `test_id_namespaces.py` 并扩充；坚持"两条路径不互相回退"的设计 |
| CI 触碰真实 `data/` → **复现 2026-09-22 数据丢失事故** | 🔴 | CI 必须在隔离环境运行；`testing_guards.py` 与 `test_static_guards.py`（禁止 `drop_all`）必须保留；`prove_test_isolation.py` 作为必过项 |
| 可观测性日志泄露敏感信息 | 🟠 | 日志脱敏规则 + 测试断言 |
| 开放注册引入未评估的攻击面 | 🔴 | G7 必须单独设计文档 + 安全评审；S-1/S-2 已关闭（2026-09-23），但开放注册仍会同时放大 S-3（恢复路径）与 S-4（内存限流）并重开 Key 归属问题 → **在完成该评审前不得开放** |

#### 出口标准（DoD）

1. 每项均有独立风险评估文档；G1 有独立预演与回滚预案。
2. CI 在每次推送自动跑全量门禁且**证明不触碰真实数据**。
3. G1 完成后：单一 id 命名空间，`foreign_key_check=0`，全部历史数据行指纹可验证。
4. 未出现任何违反 §7 硬约束的行为。

---

## 7. 跨阶段硬约束

> 这些约束**优先于任何阶段目标**。为满足某个功能而破坏约束，等同于把项目退回 2026-09-22 事故前的状态。

### 7.1 数据与迁移

1. **三级数据库环境（强制）**

| 级别 | 位置 | 用途 | 规则 |
|---|---|---|---|
| Level 1 | pytest 临时目录 | 单元 / API / migration 测试 | 只允许存在于 pytest 临时目录；Alembic 测试必须在子进程跑并显式传 `-x db_url=` |
| Level 2 | `data/staging/*.db` | 用**真实数据内容**演练迁移与多用户验收 | 唯一允许拿真实数据做破坏性演练的地方；可随时删除重建；**不是生产** |
| Level 3 | `data/vocab.db` | 生产 | 开发期间**禁止迁移、禁止写入**；只允许在发布窗口内经闸门后写入 |

2. **迁移四闸门**：`rehearsal → backup → migration → verification`，每步留证。
3. **只能用显式 revision**，禁止 `upgrade head` 直接打生产库（注意 `start-vocab.ps1` 会自行 `upgrade head`）。
4. **「复制成功 ≠ 备份可信」**：只有通过 `tools/verify_backup.py` 全部校验后才可称 **verified backup**（事故中 `pre-p1.2-*` 副本本身就是损坏的）。
5. **证据文件不得覆盖**：同名冲突归档为 `*.prior-attempt-<时间戳>`。
6. **迁移后核对每张表的行数与行指纹**（`integrity_check` 无法发现"从未创建的外键"）。
7. **禁止**：直接修改生产库、修改历史 migration（`0001`–`0007`）、对生产执行 `downgrade`、跳过 rehearsal、在测试夹具中出现 `drop_all`。
8. **WAL 感知**：备份/拷贝必须用 SQLite backup API，或先满足"WAL = 0 字节"门。

### 7.2 安全不变量（认证相关改动必须保持）

1. **响应不泄露账号或会话状态**：四种登录失败返回**完全相同**的响应；未知用户也执行一次 dummy Argon2 校验（消除时间侧信道）。
2. **审计不含口令与 token**：失败登录/敏感操作失败**不得**把口令、token 或 `token_hash` 写进任何审计或响应。
3. **限流判定不依赖账号是否存在**（否则会重新引入已修复的枚举旁路）。
4. **归属只来自会话**：任何端点都不接受客户端 `user_id`；"不属于你"与"不存在"统一 **404**（不泄露存在性），能力不足才 **403**，未登录 **401**。
5. **明文口令永不落库**；会话 token 只存 SHA-256；明文仅在 HttpOnly Cookie。
6. **前端缓存不得跨账号残留**：登出/登录成功清理缓存；SW **绝不缓存** `/api/**` 或任何带 Cookie 的响应。

### 7.3 工程流程

1. 安全类改动遵循**先设计文档、再实现、后回归**的既有节奏（Phase 2.x 全部如此）。
2. **每个阶段收尾必须跑完整验收**：后端 pytest + ruff、前端 test/typecheck/lint/build、`tools/prove_test_isolation.py`。
3. **新端点必须复用** `services/userdata.py` 访问层，并纳入 IDOR 测试矩阵。
4. **每个 commit 前自查**：无 `DROP TABLE`/`DROP COLUMN`、未删改生产库、未提交密钥、`git diff` 已人工检查。
5. **保留他人的未提交工作**：动手前先 `git status`；不得覆盖或擅自提交别人的改动或未跟踪文件。

---

## 8. 待决策事项

> 这些**需要产品或用户决策**，会阻塞对应阶段。在决策前，实现方应保持现状并明确标注"未决策"。

| ID | 决策 | 阻塞 | 选项与影响 | 默认 |
|---|---|---|---|---|
| **D1** | ✅ **已决策并落地（2026-09-23，Batch 3）**：管理员敏感操作是否要求二次认证（S-1） | Phase 2.8 | (a) 全部要求二次口令 ← **采用**（两个端点的**每一次调用**都要求）(b) 仅"创建管理员/改角色"要求（折中）(c) 保持现状 | **按 (a) 实现并验收**：`docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md`。理由：(a) 的判据单一可执行，不给"哪种字段组合需要口令"留实现缝隙；(b)/(c) 被否决的取舍记录见设计文档 §2.2 |
| **D2** | ✅ **已决策并落地（2026-09-23，Batch 4）**：会话数量上限策略（G5/2.7-e） | Phase 2.8 | 上限数值 + 淘汰规则 | **上限 = 10**（`VOCAB_MAX_SESSIONS_PER_USER`，`0` = 不限制），**淘汰 = "最久未活动优先"**（`COALESCE(last_seen_at, created_at)` 最早，并列取 id 最小），**当前会话永不淘汰**，**新登录永不因超限被拒**。实现见 `docs/V1.2-PHASE2.8-C-SESSION-LIMIT-DESIGN.md`；`revoke_reason` 仍不做 |
| **D3** | **是否需要 `revoke_reason`**（migration `0008`） | Phase 2.8 / 5 | (a) 需要 → UI 能解释"某设备为何被登出"，代价是一次 migration (b) 不需要 → 零 schema 变更 | 建议 **(b)**，本阶段不加 |
| **D4** | **发布版本号**（E-4） | Phase 2.8 收尾 | 三处 `1.0.0` 是否改为 `1.2.0`；是否打 tag | 建议改为 `1.2.0` 并在推送时打 tag |
| **D5** | **部署目标环境** | Phase 4 | 云厂商/地域/域名；是否面向中国大陆（涉 ICP 备案）；带宽（Phase 0 以 1 Mbps 为约束设计） | 需要用户提供 |
| **D6** | **移动端单词详情交互形式**（F-2/G1 of Phase 3） | Phase 3 | (a) 抽屉 (b) 贴底卡片 (c) 独立路由。需与既有 `.lookup-card`（≤720px 变贴底卡片 `bottom:78px`）避让 | 建议先做设计决策再实现 |
| **D7** | **学习算法选型**（G1 of Phase 5） | Phase 5 | SM-2 风格（可解释、参数少）vs FSRS 风格（更强、需更多历史数据）。当前生产仅 10 条 `review_event` | 建议先用 SM-2 风格落地，FSRS 作为后续演进 |
| **D8** | **`word` 表退场时机**（T-1） | Phase 6 | 何时、是否；涉及遗留 id 命名空间，**风险高** | 建议推迟到 Phase 6 且独立风险评估先行 |
| **D9** | **词频数据来源**（P-1） | Phase 5 | 需要用户提供外部词频文件（**禁止 AI 编造排名**）。在拿到之前 `frequency_rank` 允许为 NULL | 等待用户提供 |
| **D10** | **是否开放注册 / 多租户形态**（P-4） | Phase 6 | 当前账号由管理员或 CLI 创建（有意为之）。开放注册会显著扩大安全面（S-1/S-2/S-3 全部升级） | 建议保持关闭 |

---

## 9. 关键路径与里程碑

| 里程碑 | 名称 | 判定证据（必须可复现） | 对应阶段 |
|---|---|---|---|
| **M0** | 当前基线 | 312 后端测试 / 19 前端测试通过；revision `0007`；26 外键；`foreign_key_check=0` | 现状 |
| **M1** | **工程基线恢复**（⚠ **部分达成**，2026-09-23） | ①`scripts/check.ps1` exit 0 全绿 ✅ ②存在 0007 **verified backup** + 新 baseline ✅（`post-0007-verified-20260923-001237-vocab.db`；旧基线归档保留）③`git status -sb` ahead 归零 ✅ ④设备管理 / 自助改密 / 管理员二次认证在浏览器中可用 ⚠ **部分**（F-1/F-7 已有 10 条前端集成测试 + 活实例上线；**S-1 的后端守卫与 27 项测试已完成，但前端本来就没有管理员管理入口，"在浏览器里点一遍"对它不适用**；人工点击验收待用户确认）⑤副本三账号验收 `verified: true` ✅（Batch 8：`data/recovery/t8-three-user-report-20260923T134500Z.json`，126/126 PASS） | 2.8 |
| **M2** | **移动端可用** | ①≤900px 可见完整释义/音标/复习历史/文章暴露 ②Android + iOS 可添加到主屏并 standalone 启动 ③SW 断言：`/api/**` 未被缓存、切号无残留 ④桌面端无回归 | 3 |
| **M3** | **公网可服务** | ①域名 + HTTPS + `/api/health` 200 ②`VOCAB_COOKIE_SECURE=true` ③单 worker + `--proxy-headers` 经双 IP 验证 ④systemd timer 连续 3 天备份 + 异地副本 ⑤**完成一次真实恢复演练** | 4 |
| **M4** | **自适应学习** | ①设计文档评审通过 ②`0008` 在 staging 真实数据预演 PASS + 行指纹证据 ③切换前后到期分布无洪峰/真空 ④回放工具与线上状态一致 ⑤`review_event` 零丢失零改写 | 5 |
| **M5** | **结构风险消除** | ①`word` 表退场完成且单一 id 命名空间 ②CI 每次推送跑全量门禁且证明不触碰真实数据 ③G1 独立预演与回滚预案存在 | 6 |

#### 关键路径（决定整体时间的最长链）

```
M1（2.8: 备份 + 门禁 + 前端接线）
   → M3（4: 部署，依赖 M1 的备份与 CSRF 及 M2 的移动端可用）
      → M4（5: 算法升级，必须建在自动备份与可回滚之上）
         → M5（6: 结构风险消除）
```

**M2（Phase 3）不在关键路径上**，可与 2.8 并行；但**建议在 M3 之前完成**，否则公网上线的第一版在手机上不可用。

---

## 10. 文档维护规则

### 10.1 更新触发条件

本文档必须在以下事件后更新：

1. 任一 Phase **完成**（按 §10.2 协议）；
2. 任一**待决策事项（§8）被决策**——把结论写入对应阶段并移除决策项；
3. 新增**已知限制或风险**（例如审计发现新缺口）；
4. **优先级顺序调整**（必须记录调整理由）。

**不要**在本文档里写"当前 HEAD 的临时状态"——那属于 `PROJECT_HANDOFF.md`。本文档只维护**方向、顺序、判定标准**。

### 10.2 阶段完成协议

一个 Phase 只有同时满足以下条件才可标记为完成：

1. 该阶段 §6 的**出口标准（DoD）逐条**有可复现证据；
2. **§4 未完成登记册中归属该阶段的条目**已关闭，或被显式重新分配到后续阶段并写明理由；
3. 已更新 `docs/PROJECT_HANDOFF.md`（当前事实）与本文档（方向与状态列）；
4. 已跑完整验收（§7.3 第 2 条）；
5. 若阶段涉及 schema：生产库 revision 与代码 head 一致，证据已落盘且**未覆盖既有证据**。

### 10.3 维护责任与同步

| 文档 | 谁在什么时候更新 |
|---|---|
| `README.md` | 用户可见的安装/启动方式变化时 |
| `docs/PROJECT_HANDOFF.md` | **每次重要提交后**；它是"当前事实"的唯一权威 |
| `docs/PROJECT_ROADMAP.md`（本文档） | 按 §10.1 触发条件 |
| `docs/V1.2-PHASE*-*.md` | **不再修改历史设计文档**；新阶段另建新文档（沿用 `V1.2-PHASEn-*-DESIGN.md` 命名） |

### 10.4 已知的文档不一致（接手者须知）

1. `0007-release-record.md` §11.2 与 `PROJECT_HANDOFF.md` 早期版本关于 **P1.3 / 前端登录页"尚未实施"** 的说法**已过时且与代码矛盾**——P1.3 早已完成，`LoginPage.tsx` 存在。**不要据此重新实施。**
2. Phase 0 §9.4 把备份 timer 标为"Phase 3"，§G.4 标为"Phase 4"——本路线图统一为 **Phase 4**（见 §5.3）。
3. `data/recovery/acceptance-report.json` 是 V1.1 / `0003` 时期证据，**不是** 0007 之后的证据。
4. `data/recovery/0007-release-record.md` 里的 "Phase 0–5" 指**迁移执行步骤编号**，与本文档的 **Phase 0–6 开发阶段编号**不同名同物，阅读时不要混淆。

---

## 附：本路线图的事实来源

| 结论类别 | 来源 |
|---|---|
| 技术栈、路由、模型、服务 | [实测] 直接读取 `backend/app/**`、`frontend/src/**` |
| 测试基线（312 / 19） | [实测] pytest 收集计数；`PROJECT_HANDOFF.md` §8 |
| 数据库 revision / 表数 / 外键数 | [记录] `PROJECT_STATUS_V1.2.md` §3.2；[实测] 迁移链与模型定义 |
| Git 状态（HEAD / ahead 33） | [实测] `git log`、`git status -sb` |
| 备份与 baseline 缺口 | [实测] `data/backups/`、`data/recovery/` 目录列表与 mtime |
| 部署/PWA/CI 缺口 | [实测] `Test-Path deploy` / `.github` / `frontend/public` 均为 False |
| 调度算法现状 | [实测] `backend/app/services/scheduler.py` 全文 41 行 |
| 词频字段全空 | [实测] grep 全仓库，仅 model / migration / staging 断言出现 |
| Phase 0/1/2 与 Release 回顾 | [记录] `PROJECT_HANDOFF.md`、`PROJECT_STATUS_V1.2.md`、`docs/V1.2-PHASE0-AUDIT-AND-DESIGN.md`、`docs/V1.2-PHASE2.*.md` |
| 移动端与 PWA 缺陷清单 | [记录] `V1.2-PHASE0-AUDIT-AND-DESIGN.md` §8.3 / §8.4 |
| 备份策略缺陷 | [记录] `V1.2-PHASE0-AUDIT-AND-DESIGN.md` §9.2 |
