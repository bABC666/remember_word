# 本地英语词汇学习应用 V1 设计规格

## 目标与边界

构建一个可立即投入日常使用的单用户本地词汇学习应用。用户在 Windows 上通过一键脚本启动，FastAPI 托管已构建的 React 前端并自动打开浏览器。系统不做账号、云同步、Electron 或复杂记忆算法；SQLite 是唯一事实来源。

V1 必须闭环覆盖：图片导入、OCR 原始结果持久化、DeepSeek 结构化、人工校对、正式入库、今日复习、阅读生成、阅读后测试、历史查询、设置、自动与手动备份。

## 架构

- `frontend/`：React、Vite、TypeScript、React Router、TanStack Query、Lucide 图标和轻量自定义 CSS 设计系统。
- `backend/`：FastAPI、SQLAlchemy 2、Alembic、Pydantic v2、httpx。
- `data/`：运行时统一数据根目录，包含 `vocab.db`、`uploads/`、`backups/`、`ocr-temp/`、`config/settings.json`。
- 生产入口：FastAPI 同时提供 `/api/*` 与前端静态文件；`start-vocab.bat` 调用 PowerShell 启动脚本，检查环境、执行迁移、启动服务并打开浏览器。
- 开发入口：后端 8000、Vite 5173，Vite 代理 `/api`。

## 数据边界

### Source

`word.word`、`phonetic`、`part_of_speech`、`source_meanings`、`source_raw` 与所有 OCR 原始数据属于 Source。AI 只产出候选值；正式词条确认后，任何 AI 服务都不得写入 Source 字段。只有明确的人工编辑 API 可以修改 Source，并记录历史事件。

### Learning

`anchor`、`semantic_note`、`status`、`next_review_at`、统计缓存和文章属于 Learning。状态值为 `new | familiar | learning | known | weak | mastered`。

### History

`review_event`、`history_event` 与 `article_word_exposure` 属于 History。累计字段仅是缓存；行为可以从事件表重建。

## Schema

### word

包含用户要求的全部字段，并增加 `next_review_at`、`consecutive_failures`、`created_at`、`updated_at`。`source_meanings` 以 JSON 文本保存完整原书释义数组；`source_raw` 保留对应 OCR 文本。

### import_batch / import_image / import_candidate

- `import_batch` 保存状态、错误、OCR provider、原始 OCR JSON、创建和更新时间。
- `import_image` 保存原文件路径、SHA-256、MIME、尺寸、逐图 OCR JSON 与错误。
- `import_candidate` 保存 AI 结构化结果、anchor、疑点、用户编辑后的当前值、确认状态以及最终 `word_id`。
- 任何异常只更新 batch 阶段和错误字段，不删除图片、OCR 或用户修改。
- 仅 `POST /imports/{batch_id}/confirm` 可以将选中的 candidate 复制到 `word`；该操作与 history 事件同一事务。

### review_event

保存 `word_id`、时间、`result`、`source`、`article_id`、`status_before`、`status_after`、`review_type`。结果为 `know | fuzzy | fail`，来源为 `daily | reading | library`。

### article / article_word_exposure

`article` 保存标题、正文、目标词 JSON、实际使用词 JSON、完成状态与时间。`article_word_exposure` 以 `(article_id, word_id)` 唯一关联实际出现词，保存首次/最后暴露时间、次数和上下文片段；完成阅读时事务性增加 `word.context_exposure`。

### history_event / app_setting

`history_event` 记录导入确认、Source 人工编辑、文章完成和手动备份等事件。`app_setting` 保存非敏感设置；API Key 优先读取环境变量，其次读取 `data/config/settings.json`，接口只返回是否已配置和掩码。

## 导入流程

1. 上传图片到 `data/uploads/<batch-id>/`，计算 hash，创建 batch/image。
2. OCR provider 逐图识别，保存未经修改的结构化 JSON 与拼接文本。
3. DeepSeek provider 使用 JSON Schema 将 OCR 结果转换为 candidate，并生成 anchor/semantic note。
4. 用户在校对页编辑 candidate。每次保存立即落库。
5. 用户明确勾选并确认后才写入 `word`。

OCR provider 为稳定接口：`extract(image_path) -> OCRDocument`。首选 PaddleOCR；不可用时返回明确的 `provider_unavailable`，保留 batch，不使用 mock 或假结果。允许以后接入独立 Python 3.11 OCR 进程而不改变路由和数据库。

AI provider 为稳定接口，DeepSeek 采用 OpenAI-compatible chat completions。Prompt 位于 `backend/app/prompts/*.txt`，响应通过 Pydantic 模型和 JSON Schema 校验；失败不回滚已持久化的 OCR 和人工编辑。

## 学习与调度

调度规则集中在 `services/scheduler.py`：

- `fail`：状态 `weak`，连续失败 +1，下次复习为当天稍后或次日。
- `fuzzy`：状态 `learning`，连续失败归零，1 天后复习。
- `know`：按 `new → familiar → learning → known → mastered` 推进，间隔依次为 1、3、7、14、30 天；weak 答对后回到 learning。
- context exposure 不替代主动回忆，但在同状态下作为阅读选词时的次级排序依据。

每次作答在一个事务中写入 `review_event`、更新状态、统计缓存和 `next_review_at`。键盘：空格显示答案，1 不会，2 模糊，3 会。

## 阅读

选词顺序：weak、最近失败、昨日词、今日新词、最久未出现。默认 15–25 个，数量受词库规模约束。DeepSeek 返回 `{title, article, actual_used_words}`，后端校验实际单词必须属于目标集合且确实出现在正文中。

完成阅读后创建 exposure 关联。测试只覆盖实际出现词：用户先输入自己的理解，再显示 anchor、原书释义与文章语境，AI 建议为可选信息，最终由用户点击会/模糊/不会并写入 reading review event。

## 页面与视觉

六个页面全部实现：Dashboard、导入、今日学习、阅读、词库、设置。视觉为安静的编辑式学习工具：暖白背景、墨色文字、靛蓝主色、琥珀色 weak、低密度侧栏、开放式布局。学习页面保持单一焦点；所有异步区域具备 loading、error、empty 状态和可见焦点样式。

## 数据安全

- 每次应用启动调用 SQLite online backup；若 `data/backups/YYYY-MM-DD-vocab.db` 已存在则不覆盖。
- 设置页提供手动备份并返回生成文件名。
- `.env`、数据库、配置、图片、备份、临时 OCR 文件均不入 Git。
- Alembic 是唯一 schema 演进机制；启动脚本执行 `alembic upgrade head`。

## 验收

自动测试覆盖 Source 不可被 AI 覆盖、candidate 确认门、导入恢复、备份不覆盖、复习历史与调度、weak 优先级、文章实际用词、阅读暴露、外部服务失败不丢数据、重启持久化。浏览器验收覆盖六页、桌面与移动布局、键盘学习和主要错误/空状态。

完整外部链路仅在本机配置真实 DeepSeek Key 与可运行 PaddleOCR 时执行；缺少凭据或 Paddle/Python 兼容性属于明确外部阻塞，不得用 mock 冒充通过。

