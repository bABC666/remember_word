# 拾词 · AI 协作开发规范（AI_DEVELOPMENT_GUIDE）

> 适用对象：任何接手本项目的 AI Agent 或开发者。
> 本文把 `PROJECT_ARCHITECTURE.md`（约束）、`PROJECT_ROADMAP.md`（计划）、`PROJECT_HANDOFF.md`（事实）里的**硬规定**提炼成可执行的作业流程。
> 三者与本文冲突时：**架构约束 > 事实文档 > 计划 > 本文**；代码与文档冲突时：**以代码实测为准**。

---

## 0. 三十秒版

1. **先读四份文档**（§1），再动手。
2. **改 schema / 认证 / 安全策略 → 先写设计文档，后写代码**（§2）。
3. **完成 = 代码 + 测试 + 文档 + `check.ps1` exit 0 + commit**（§3/§4/§5），缺一项都不算完成。
4. **绝不触碰生产库**：开发期不迁移、不写入 `data/vocab.db`（§6）。
5. **不删除安全验证、不绕过测试、不重写历史 migration**（§7）。

---

## 1. 开始任务

### 1.1 必读（按顺序）

| 顺序 | 文档 | 你要从里面拿走什么 |
|---|---|---|
| 1 | `docs/PROJECT_STATUS_CURRENT.md` | 当前 HEAD、当前阶段、测试/门禁状态、已知风险、下一阶段 |
| 2 | `docs/PROJECT_ARCHITECTURE.md` | **红线**：数据分层、归属判定、AI 写权限、三级数据库环境、技术排除项 |
| 3 | `docs/PROJECT_ROADMAP.md` | 任务的 DoD、优先级、依赖顺序、风险登记册 |
| 4 | `docs/PROJECT_HANDOFF.md` | 认证模型、DB 现状、已知限制、复验命令（**当前事实的权威**） |

按主题补充阅读：触碰 CSRF/请求头 → `docs/V1.2-PHASE2.8-B-CSRF-DESIGN.md`；触碰认证语义 → `docs/V1.2-PHASE2.3-AUTH-FLOW-AUDIT.md` 与 `docs/V1.2-PHASE2.7-D-A-REAUTH-DESIGN.md`；任何 migration → `docs/0007-production-migration-runbook.md`。

### 1.2 动手前的三件事

```powershell
git status                    # 1. 保留他人未提交/未跟踪的文件，不要覆盖或顺手提交
git log -1 --oneline          # 2. 记住起始 HEAD（写进交付说明）
powershell -File scripts\check.ps1   # 3. 确认起点是绿的；起点不绿就先报告，不要在上面盖代码
```

> 若发现**未提交代码 / 门禁失败 / 状态异常**：**暂停并报告**，不要"顺手修好再继续"。

---

## 2. 修改原则

### 2.1 必须先写设计、评审通过再实现的改动

| 类别 | 例子 | 为什么 |
|---|---|---|
| **数据库 schema** | 新表/新列/新索引/外键变更 | 迁移不可逆、有事故前科；须走四闸门（§6） |
| **认证 / 会话 / 权限模型** | Cookie 属性、会话生命周期、归属判定入口、新敏感端点 | 一旦松动即全站失守 |
| **安全策略** | CSRF 层、re-auth 守卫范围、限流阈值语义、审计内容 | 需要判定边界与失败模式，代码里写不清 |
| **架构级技术选型** | 引入 PostgreSQL / Redis / 多 worker / 本地 LLM / OCR 服务化 / JWT | `PROJECT_ARCHITECTURE.md` §3.4 / §9 **明确排除**；要引入必须先改那份文件并说明触发条件 |
| **删除或改写既有约束** | 移除某个测试、放宽某条校验、删除 `IGNORED_COLUMNS` 条目 | 删约束必须写明"为什么原约束不再成立" |

**设计文档的合格线**（沿用本仓库 Phase 2.x 的成例）：① 现状实测（含代码位置）；② 方案对比与取舍；③ 失败模式与边界（**明确写出挡不住什么**）；④ 测试策略；⑤ 回滚/逃生口；⑥ 明确"本次不改什么"。产出放在 `docs/V1.2-<阶段>-<主题>-DESIGN.md`，**历史设计文档不再修改**，新阶段另建新文档。

### 2.2 可以直接实现的改动

纯前端 UI/交互、纯只读端点、日志/文案、测试补充、文档。**但仍要跑完整门禁**。

### 2.3 每次改动都要过一遍"架构自检清单"

`PROJECT_ARCHITECTURE.md` 附录 A 的 10 条（Source 是否被 AI 写、归属是否只来自会话、是否绕过 `review_event`、是否改了 schema 却没迁移、是否新增端点却没考虑 404 语义、是否引入排除项技术、是否让手机端更差、是否让"从 anchor 回到完整释义"更难、AI 调用是否跨写事务、是否把学习状态写进内容层）。**答不上来就停下来读对应章节。**

---

## 3. 测试要求

### 3.1 完成代码必须全绿

```powershell
# 唯一门禁命令（包含下面全部步骤 + 隔离取证 + verified backup）
powershell -File scripts\check.ps1        # 必须 exit 0

# 需要定位失败时再单项跑
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
cd backend; .\.venv\Scripts\python.exe -m ruff check --no-cache app tests
cd ..;      backend\.venv\Scripts\python.exe -m ruff check tools
cd frontend; npm test; npm run typecheck; npm run lint; npm run build
```

### 3.2 规则

1. **先补测试再改语义**（安全类改动的既有节奏：设计 → 实现 → 回归）。
2. **新增端点必须纳入 IDOR 测试矩阵**（`test_authz.py`），并复用 `services/userdata.py` 访问层。
3. **不修改既有断言来"让测试通过"**：只有当断言本身写错了（例如硬编码了过期的 revision），才在 commit message 里说明理由后修改。仓库有先例：`test_verified_db.py` 的项目基线断言随 Phase 2.8 T5 重录基线一起更新。
4. **禁止**在测试夹具中出现 `drop_all`（`test_static_guards.py` 静态拦截）；破坏性操作只允许发生在 pytest 临时目录或 `data/staging/` 副本内。
5. **测试不得触碰真实 `data/`**：`backend/app/testing_guards.py` 会 fail-fast；收尾时用 `tools/prove_test_isolation.py` 取证。
6. **前端测试**：新 UI 必须有 Vitest + Testing Library 覆盖；涉及请求的用真实 fetch 桩（见 `frontend/src/security.test.tsx` 的写法），并断言**不泄露 token / token_hash**。
7. 后端测试客户端默认带 `Origin: http://testserver`（`conftest.py` 注入）。**这是让测试像浏览器，不是绕过校验**——需要不同来源时逐请求覆盖该头（`Origin: ""` 表示"无来源"）。

---

## 4. 文档要求

功能/修复完成后**同步更新**（同一批次内，不要留给下一个人）：

| 文档 | 更新什么 |
|---|---|
| `docs/PROJECT_HANDOFF.md` | **必需**。当前事实：HEAD、验收数字、认证/安全模型变化、关闭的已知限制、下一步 |
| `docs/PROJECT_STATUS_CURRENT.md` | **必需**。§2/§3/§5/§6/§7 中受影响的状态块（它是接手指南，不能过期） |
| `docs/PROJECT_ROADMAP.md` | 任务状态（✅/部分）、风险登记册条目关闭或重分配、DoD 勾选；**不要提前标记未完成任务** |
| `docs/PROJECT_ARCHITECTURE.md` | 仅当改动触碰附录 B 列出的范围（技术组件、产品形态、数据分层、AI 写权限、归属判定、阶段划分、长期安全约束）——**且必须先改它再改代码** |
| 证据文件 | `data/recovery/`（**不入 Git**）：新文件名，**绝不覆盖既有证据**；同名冲突归档为 `*.prior-attempt-<时间戳>` |

**诚实性要求**：文档里区分 **[实测] / [记录] / [推断]**；发现文档与代码矛盾时，**以代码为准并在文档中更正**，同时说明原表述为何失真。

---

## 5. Git 要求

1. **小步提交**：一个提交做一件事；不要夹带无关格式化或重构。
2. **message 说明目的**：主题行用约定式前缀（`feat(v1.2):` / `fix(tools):` / `test(v1.2):` / `docs:` / `refactor(v1.2):`），正文写**为什么**（现状缺陷 + 取舍 + 边界），不是复述 diff。
3. **提交前自查**（`PROJECT_ROADMAP.md` §7.3）：无 `DROP TABLE`/`DROP COLUMN`、未删改生产库、未提交密钥、`git diff` 已人工过目、未提交他人未跟踪文件。
4. **不 push 强制**、不 rebase/squash 已推送历史（本仓库历史上以 fast-forward 方式推送）。
5. **不自动创建 tag**：tag 需明确确认（当前 `1.0.0` 与 `V1.2` 阶段名的口径尚未统一，属待决策项 D4）。
6. **阶段收尾时**按 `PROJECT_ROADMAP.md` §10.2 的完成协议执行（DoD 逐条有可复现证据 → 关闭登记册条目 → 更新文档 → 全量验收 → 若涉 schema 则核对 revision）。
7. 需要"完成的定义"时认这一条：**代码 + 测试 + 文档 + `check.ps1` exit 0 + commit**，并在交付说明里列出**修改文件、测试结果、commit hash、剩余风险**。

---

## 6. 数据库与数据安全（最高优先级，不接受任何"临时方便"）

**三级环境（强制）**

| 级别 | 位置 | 用途 | 规则 |
|---|---|---|---|
| Level 1 | pytest 临时目录 | 单元 / API / migration 测试 | 只允许存在于临时目录 |
| Level 2 | `data/staging/*.db` | 用**真实数据内容**演练迁移与多用户验收 | 唯一允许拿真实数据做演练的地方；不是生产 |
| Level 3 | `data/vocab.db` | **生产** | **开发期禁止迁移、禁止写入**；只允许在发布窗口内经闸门后写入 |

**migration 四闸门**：`rehearsal → backup → migration → verification`，每步留证；**只用显式 revision**，禁止 `upgrade head` 直接打生产（注意 `scripts/start-vocab.ps1` 自身会执行 `alembic upgrade head`）；迁移后核对**逐表行数与行指纹**（`integrity_check` 发现不了"从未创建的外键"）；对生产执行 `downgrade` 一律禁止（仅允许对一次性副本）。

**其它硬规定**：备份/拷贝必须用 SQLite backup API 或先满足"WAL = 0 字节"门（裸拷贝 `vocab.db` 会静默丢页）；「复制成功 ≠ 备份可信」，只有通过 `tools/verify_backup.py` 全部校验的才可称 **verified backup**；`tools/verify_backup.py`、`tools/verified_db.py`、备份与恢复工具**不得为实现功能而修改**（改动它们等于改动"什么是可信数据"的定义）。

**理由**：2026-09-22 生产库全部业务表曾被测试夹具的 `drop_all` 删除并造成永久数据丢失。上述每条规则都有代码或工具兜底。

---

## 7. 明确禁止

| 禁止 | 说明 |
|---|---|
| 未设计直接修改架构 | 见 §2.1；引入 `PROJECT_ARCHITECTURE.md` §3.4/§9 排除项（PostgreSQL / Redis / 多 worker / 本地 LLM / OCR 服务化 / JWT / 开放注册）**先改那份文件** |
| 未确认修改数据库 schema | 见 §6；schema 变更必须附 migration + 在 staging 用真实数据预演 |
| 绕过测试 | 不删测试、不 `skip` 关键断言、不用 `# type: ignore`/`noqa` 掩盖真实错误、不关闭 CSRF 校验来让测试通过 |
| 删除安全验证 | `testing_guards.py`、`test_static_guards.py`、`test_isolation_guards.py`、CSRF 中间件、re-auth 守卫、`IGNORED_COLUMNS` 与 `ROW_TOLERANT_TABLES` 均为**安全资产**，删除或放宽必须走 §2.1 |
| 重写历史 | 不改已推送的 commit、不改历史 migration（`0001`–`0007`）、不强推 |
| 触碰生产数据 | 不在 `data/vocab.db` 上做任何开发期写入/迁移；不删除或重建它 |
| 读取/输出密钥 | `data/config/settings.json`、`.env`、DeepSeek Key、会话 token/`token_hash` 一律不读、不打印、不写入任何文档或提交 |
| 顺手提交他人的东西 | 先 `git status`；他人未跟踪文件（如 `docs/PROJECT_STATUS_V1.2.md`）保持原样 |

---

## 8. 交付（Handoff）清单

任务结束、交给下一个人时，交付说明必须包含：

1. **修改文件列表**（新增 / 修改，区分"进 Git"与"本地证据"）
2. **测试结果**（后端 / 前端 / `check.ps1` 的原始数字）
3. **commit hash** 与是否已推送（`git status -sb` 的 ahead 数）
4. **剩余风险**（含本次**未解决**的、以及本次**新引入**的）
5. **与文档的差异**（若发现文档失真，说明改了哪一处、为什么）
6. **下一步建议**（不要自行进入下一阶段；等确认）

> 本项目对"诚实性"的要求高于"看起来完成"：不确定就标注 `[推断]` 或 UNKNOWN，**不要假装已完成**（路线图 §6.4 的 DoD 明确写了这一条）。
