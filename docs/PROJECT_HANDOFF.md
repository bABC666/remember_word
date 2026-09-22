# 拾词项目交接说明

> 更新日期：2026-09-22  
> 当前阶段：**数据恢复后的 V1.1 安全基线**（V1.2 开发已暂停，见第 13 节）  
> 当前分支：`recovery/v1.1-guarded`  
> 本文用途：让新的开发者或 AI 不依赖历史对话，也能安全地继续维护项目。

## 0. 必读：2026-09-22 数据丢失事故与恢复

在 V1.2 Phase 1 期间，`data/vocab.db` 的全部业务表被删除。原因：**测试夹具对应用模块级 engine 执行了 `Base.metadata.drop_all(engine)`，而该 engine 实际绑定到了真实数据库。**

已恢复，但**有永久性数据丢失**。任何接手者请先读：

- `_INCIDENT-20260922/README.md`（本地保留的事故现场，**不入 Git**）
- `data/recovery/*.json`（恢复与校验证据）
- `docs/V1.2-PHASE0-AUDIT-AND-DESIGN.md`（V1.2 设计基线，仍然有效）

**永久丢失（禁止伪造补齐）**：`review_event` 10 条、`article_word_lookup` 5 条、文章译文 1089 字、`history_event` 6 条、`app_setting.onboarding_seen`、9 个词的 status 推进与对应 `next_review_at`。

**当前生效的安全规则**：

1. 测试进程**不得**触碰真实 `data/`。`backend/app/testing_guards.py` 会对「绑定 engine / 解析设置 / 破坏性 schema 操作」做 fail-fast。
2. `drop_all` 出现在任何测试夹具中都是禁止的。
3. 破坏性操作只允许发生在带测试标识的临时目录内。
4. Alembic 测试必须在子进程里跑，并显式传 `-x db_url=`，且事后校验真正被迁移的文件。
5. 「备份」只有在通过 `tools/verify_backup.py` 的全部校验后才可称为 **verified backup**。复制成功 ≠ 备份可信（本次事故中 `pre-p1.2-*` 副本本身就是损坏的）。
6. 应用启动会校验数据库 alembic revision 与代码 head 是否一致，不一致直接拒绝启动。

自查命令：

```powershell
backend\.venv\Scripts\python.exe tools\verify_backup.py <database>
backend\.venv\Scripts\python.exe tools\prove_test_isolation.py   # 证明 pytest 不碰 data/
backend\.venv\Scripts\python.exe tools\compare_with_source.py    # 实盘与恢复源逐字段比对
powershell -File scripts\check.ps1                               # 全量检查（隔离取证为必过项）
```

## 0.1 三级数据库环境（强制）

| 级别 | 位置 | 用途 | 规则 |
|---|---|---|---|
| **Level 1** | pytest 临时目录 | 单元 / API / migration 测试 | 只允许存在于 pytest 临时目录 |
| **Level 2** | `data/staging/v1.1-realdata-migration-test.db` | 用**真实数据内容**演练迁移 | 唯一允许拿真实数据做迁移演练的地方；可随时删除重建；不是生产 |
| **Level 3** | `data/vocab.db` | 生产 | 开发期间**禁止迁移** |

Level 2 工作流：

```powershell
backend\.venv\Scripts\python.exe tools\make_staging.py                  # 从已验证 V1.1 源克隆 + 记录迁移前基线
backend\.venv\Scripts\python.exe tools\staging_migration_check.py       # 0003 → head 并逐项断言真实数据未损坏
backend\.venv\Scripts\python.exe tools\staging_two_user_check.py        # 真实启动应用指向 staging，双用户验证
```

迁移等级由 `VOCAB_DATABASE_PATH` 显式指定数据库文件；`VOCAB_DATA_DIR` 仍决定 uploads / backups / config 的位置。

## 0.2 分支与当前状态（2026-09-22）

- `recovery/v1.1-guarded`：事故后的安全基线（V1.1 + 全部护栏）。
- `feat/v1.2-phase1-safe`：**当前开发分支**，从安全基线建立。已含 P1.0（迁移地基）、P1.1（用户体系）、P1.2（词库模型 + staging 演练）。
- `wip/v1.2-phase1-code` / `codex/vocab-ux-reading-v2` / `stash@{0}`：事故前工作，**仅作参考**。其 conftest 改动包含 `drop_all`，**禁止直接套用**。
- **生产库仍是 V1.1（revision 0003）**，sha256 `c8be615d…f67944`。V1.2 代码面对它会因版本护栏拒绝启动——这是正确行为。
- 尚未实施：P1.3（按用户隔离全部业务 API）、前端登录页、PWA、部署。

## 1. 一句话概况

拾词是一个 Windows 优先、浏览器使用的本地英语词汇学习应用。React/Vite 前端由 FastAPI 托管，SQLite 是唯一事实来源；应用已走通单词书图片导入、真实 PaddleOCR、DeepSeek 结构化、人工校对、复习调度、阅读生成、阅读后测试、点词解释、全文翻译、查词记录、生词入库、备份和重启持久化。

项目不是 Demo。日常入口是桌面“拾词”快捷方式或根目录 `start-vocab.bat`。

## 2. 技术栈与运行方式

- 前端：React 19、Vite、TypeScript、React Router、TanStack Query、Lucide、自定义 CSS。
- 后端：FastAPI、SQLAlchemy 2、Pydantic v2、Alembic、httpx。
- 数据库：SQLite。
- OCR：PaddleOCR/PaddlePaddle，当前已在 Windows CPU 环境真实运行。
- AI：DeepSeek OpenAI-compatible API；默认 API 模型名 `deepseek-flash`，界面显示 `DeepSeek V4.1-Flash`。
- 生产形态：FastAPI 在 `127.0.0.1:8000` 同时提供 API 和 `frontend/dist`。

Windows 日常启动：

```powershell
.\start-vocab.bat
```

停止：

```powershell
.\stop-vocab.bat
```

启动脚本会刷新桌面快捷方式、构建前端、执行 `alembic upgrade head`、启动 FastAPI 并打开浏览器。构建或迁移失败时会停止，不会继续运行旧页面。

## 3. 不可破坏的数据原则

这是项目最重要的维护约束：

1. SQLite 是唯一真实数据源。
2. Source、Learning、History 必须保持分离。
3. `source_raw`、`source_meanings`、逐图 OCR JSON、批次 OCR 文本和原始图片不能被 AI 覆盖。
4. AI 只能生成候选值、anchor、semantic note、文章、翻译和辅助判断。
5. `import_candidate` 未经人工确认，绝不能自动进入 `word`。
6. 每次复习必须写 `review_event`；累计数字只是缓存。
7. Schema 变化必须新增 Alembic migration，不能要求用户删除数据库重建。
8. API Key、数据库、图片、备份和本机配置不能提交到 Git，也不能放进普通源码交接包。

## 4. 当前已完成能力

### 首页

- 今日新词、待复习、weak 数量、今日阅读、连续学习天数。
- 开始今日学习和下一步提示。

### 单词导入

- 多图选择和连续拖入会累加，不再互相覆盖，并按文件信息去重。
- 上传前可以移除单张图片。
- 未确认批次可以继续追加图片、软删除错误图片或放弃批次。
- OCR、AI 任一步失败都会保留批次、图片、原始结果和用户修改。
- PaddleOCR 引擎按配置复用；手机超大图只对临时副本缩放，原图不变。
- 重试时只识别新增或失败图片。
- DeepSeek 负责候选结构化、噪声清理、音标保留、义项拆分和 anchor 生成。
- 所有候选必须人工检查并确认后才正式入库。

### 今日学习

- 一次一个英文单词，默认隐藏中文。
- 空格显示答案；1/2/3 对应不会、模糊、会。
- 先显示 anchor，再显示逐行排列的完整原书释义。
- 每次作答写完整 review event，并更新状态和 `next_review_at`。

### 阅读

- weak、失败词、新词和久未出现词优先参与选词。
- DeepSeek 生成英文文章，后端验证 `actual_used_words` 确实属于目标词且出现在正文。
- 完成阅读后只测试实际出现的目标词，最终评分由用户确认；AI 判断不是强依赖。
- 正文单词可以点击。词库已有词优先用本地数据，不调用 AI；未知词才请求 AI。
- 点词结果保存在 `article_word_lookup`，重复点击读取 SQLite 缓存。
- 全文翻译按需生成并保存，重启后仍存在。
- 未知词可加入词库；真实文章句子写入 `source_raw`，AI 中文解释只写 Learning 字段，并标记 `possible_issue`。
- 阅读暴露通过 `article_word_exposure` 显式关联。

### 词库与设置

- 搜索、状态筛选、weak、最近加入、长期未复习。
- 单词详情包含 Source、Learning、统计缓存、完整复习历史和文章暴露。
- 设置支持 DeepSeek Key/Base URL/Model、每日新词数、文章长度和 OCR 配置。
- Key 不返回明文。
- 首次使用说明状态保存在 SQLite；侧栏可随时重新打开。
- 每日自动备份和手动备份均已实现。

## 5. 数据模型与迁移

核心表：

- `word`：Source 与当前 Learning 状态。
- `import_batch`、`import_image`、`import_candidate`：可恢复导入流程。
- `review_event`：每次主动回忆历史。
- `history_event`：导入、备份、查词等通用行为历史。
- `article`：文章、目标词、实际用词、完成状态和译文。
- `article_word_exposure`：文章与真实出现词的显式关联。
- `article_word_lookup`：阅读点词结果与加入词库状态。
- `app_setting`：非敏感本地设置与 onboarding 状态。

当前数据库迁移版本：`0003_article_reading_tools`。

截至 2026-09-22，本机实际数据仅作状态参考：19 个正式词、1 个导入批次、19 个候选、10 条复习事件、1 篇文章、16 条文章暴露、1 条查词记录。交接源码包不包含这些用户数据。

## 6. 代码导航

- `backend/app/models.py`：SQLAlchemy 模型。
- `backend/app/api/`：FastAPI 路由。
- `backend/app/services/imports.py`：导入确认、失败保存和 OCR 批处理。
- `backend/app/services/ocr/`：可替换 OCR provider。
- `backend/app/services/ai/`：可替换 AI provider和 Pydantic 输出模型。
- `backend/app/services/scheduler.py`：透明、可测试的第一版调度规则。
- `backend/app/services/reading.py`：选词、暴露、点词、翻译和文章生词入库。
- `backend/app/prompts/`：所有 DeepSeek prompt。
- `backend/alembic/versions/`：迁移历史。
- `frontend/src/pages/`：六个 V1 页面。
- `frontend/src/components/HelpCenter.tsx`：首次说明和永久入口。
- `scripts/start-vocab.ps1`：生产构建、迁移、启动和快捷方式刷新。

更细的设计依据：

- `docs/superpowers/specs/2026-09-21-vocab-learning-v1-design.md`
- `docs/superpowers/specs/2026-09-22-vocab-ux-reading-v2-design.md`
- `docs/superpowers/plans/2026-09-21-vocab-learning-v1.md`
- `docs/superpowers/plans/2026-09-22-vocab-ux-reading-v2.md`

## 7. Git 状态与重要提交

- `7dc8d7d`：V1 架构与计划。
- `8216a4c`：本地词汇学习 V1。
- `1cd82d6`：品牌图标和桌面启动入口。
- `ebb072f`：可恢复的多图导入。
- `b5fb373`：OCR 引擎复用、缩图和断点续识别。
- `2b465c9`：模型说明、首次帮助、阅读点词、全文翻译和持久化。

`main` 当前停在基础 V1，最新功能位于 `codex/vocab-ux-reading-v2`。接手者不要误以为 `main` 已包含 V1.1。

## 8. 最近一次验证证据

2026-09-22 的最终验收：

- 后端 pytest：31 项通过。
- Ruff：通过。
- 前端 Vitest：7 项通过。
- TypeScript typecheck：通过。
- ESLint：通过。
- Vite production build：通过。
- 在线健康检查：`/api/health` 返回 `ok`。
- Alembic：`0003_article_reading_tools (head)`。
- 浏览器实测：首次说明、导入页、已完成文章、词库优先点词、全文翻译显示。
- 实际重启后恢复：1089 字译文、1 条查词记录、文章完成状态和 16 个阅读测试词。
- 当日自动备份已生成，桌面快捷方式目标和图标已验证。

复验命令：

```powershell
backend\.venv\Scripts\python.exe -m ruff check backend\app backend\tests
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
```

## 9. 已知限制与真实边界

- CPU PaddleOCR 首次运行仍需要加载/下载模型；缓存和缩图优化降低了后续等待，但没有承诺固定耗时。
- 全文翻译、文章生成和未知词解释需要可用的 DeepSeek Key 与网络；本地复习、已有文章、已有点词记录和词库不依赖 AI 在线状态。
- 当前是单用户本地应用，没有账号、云同步、Electron、移动 App 或多人权限。
- 调度是透明的规则式 V1，不是 FSRS/Anki 算法。
- 阅读加入的陌生词没有“原书中文释义”；文章原句是 Source，AI 解释属于 Learning，并会提示人工核对。
- 点击词形目前以表面词和 AI 返回原形为主，尚未实现完整的本地词形还原器。

## 10. 建议的下一步

1. 增加 OCR“速度优先/精度优先”选项，并在真实手机照片集上做可重复基准。
2. 增加按段落对齐的中英对照模式，以及译文隐藏/分段展开偏好。
3. 增加本地词形还原、发音音频和更可靠的派生词归并。

## 11. 给接手 AI 的工作规则

开始任何修改前：

1. 先读本文、README 和对应设计规格。
2. 先检查 Git 状态，保留用户已有改动；当前工作区可能保留用户主动删除 `.env.example` 的状态。
3. 不读取、输出或打包 `data/config/settings.json`、`.env` 等密钥文件。
4. 不删除或重建 `data/vocab.db`；先备份，再通过 Alembic 迁移。
5. 涉及 Source 的任何修改都要证明 AI 不会覆盖原始数据。
6. 新功能先补测试，完成后跑完整后端和前端验收。
7. 每次更换启动入口或图标时，同步更新 `scripts/install-shortcut.ps1`；正常功能迭代不需要改变快捷方式，因为它稳定指向 `start-vocab.bat`。

## 12. 安全交接建议

推荐发送生成的源码交接 ZIP。它只包含 Git 跟踪的代码、迁移、prompt、测试和文档，不包含数据库、图片、备份、模型缓存、API Key、虚拟环境或 `node_modules`。

如果另一个 AI 也必须查看真实学习数据，应单独复制某个 SQLite 备份，并明确这是个人学习数据；不要把 `data/` 整目录和源码包混在一起发送。
