# 本地英语词汇学习应用 V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可一键启动、数据持久、六页闭环完整的本地英语词汇学习 V1。

**Architecture:** React/Vite 前端通过 `/api` 使用 FastAPI；SQLAlchemy/Alembic 管理唯一 SQLite 数据源；OCR 和 AI 均通过可替换 service 接口接入，导入 staging 与正式词库严格分离。

**Tech Stack:** React 19, Vite, TypeScript, FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, SQLite, pytest, Vitest, Playwright

**Spec:** `docs/superpowers/specs/2026-09-21-vocab-learning-v1-design.md`

## Global Constraints

- Windows 一键启动时 FastAPI 托管 frontend build 并自动打开浏览器。
- SQLite 是唯一事实来源；Source、Learning、History 不得混写。
- 未确认 candidate 永远不能进入 word。
- OCR/AI 失败必须保留 batch、图片、OCR 和用户编辑。
- API Key 不入 Git且不返回明文。
- 所有 schema 变化经 Alembic。

## Review Focus

- 外部 provider 超时、无凭据、响应格式错误时，既有 batch 数据保持可恢复。
- 重复点击确认或复习按钮不会产生重复正式词条或无意的双事件。
- 空词库、少于 15 词和无 actual-used-word 的文章流程有可理解空状态。
- Windows 中文路径下启动、数据目录和静态文件路径正确。
- Source 人工编辑与 AI Learning 更新的 API 权限边界不会交叉。

---

### Task 1: 工程骨架、配置与持久化核心

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/core/*.py`, `backend/app/models/*.py`, `backend/alembic/*`
- Create: `backend/tests/test_backup.py`, `backend/tests/test_persistence.py`
- Create: `frontend/package.json`, `frontend/src/main.tsx`

**Interfaces:**
- Produces: `get_session()`, `get_settings()`, `run_daily_backup()`, SQLAlchemy models and Alembic head.

- [ ] **Step 1: Write failing persistence and non-overwriting backup tests.**
- [ ] **Step 2: Run `pytest backend/tests/test_backup.py backend/tests/test_persistence.py -v`; expect missing modules.**
- [ ] **Step 3: Implement settings, data paths, engine/session, complete models, migration, FastAPI lifecycle and backup service.**
- [ ] **Step 4: Re-run tests; expect pass, then run Alembic against a temporary database.**
- [ ] **Step 5: Scaffold React/Vite TypeScript and shared API types; run typecheck.**

### Task 2: 导入、OCR 与 AI 边界

**Files:**
- Create: `backend/app/services/ocr/*.py`, `backend/app/services/ai/*.py`, `backend/app/prompts/*.txt`
- Create: `backend/app/api/imports.py`, `backend/tests/test_import_flow.py`, `backend/tests/test_provider_failures.py`

**Interfaces:**
- Consumes: models/session from Task 1.
- Produces: create/list/get/process/update/confirm import APIs; `OCRProvider`, `AIProvider` protocols.

- [ ] **Step 1: Write failing tests proving raw OCR preservation, failure recovery, Pydantic validation and confirmation gate.**
- [ ] **Step 2: Run focused tests; expect missing routes/services.**
- [ ] **Step 3: Implement upload hashing/storage, PaddleOCR adapter, DeepSeek adapter, prompts and staged transaction flow.**
- [ ] **Step 4: Implement idempotent human-only confirmation; prove AI paths cannot update Source fields.**
- [ ] **Step 5: Run import/provider tests and full backend suite.**

### Task 3: 复习调度、Dashboard、词库与备份 API

**Files:**
- Create: `backend/app/services/scheduler.py`, `backend/app/services/study.py`
- Create: `backend/app/api/dashboard.py`, `study.py`, `words.py`, `settings.py`
- Create: `backend/tests/test_review_flow.py`, `test_dashboard.py`, `test_words.py`

**Interfaces:**
- Produces: deterministic `schedule_review()`, daily queue/review endpoints, dashboard metrics, word history/search/filter, safe settings and manual backup.

- [ ] **Step 1: Write failing table-driven scheduler and review-event tests.**
- [ ] **Step 2: Run focused tests; expect missing behavior.**
- [ ] **Step 3: Implement transaction-safe review recording and dashboard/library queries.**
- [ ] **Step 4: Implement masked settings and manual backup endpoints.**
- [ ] **Step 5: Run focused and full backend suites.**

### Task 4: 阅读生成、显式暴露与阅读后测试

**Files:**
- Create: `backend/app/services/reading.py`, `backend/app/api/articles.py`
- Create: `backend/tests/test_reading_flow.py`

**Interfaces:**
- Consumes: AIProvider, scheduler, word/review/article models.
- Produces: prioritized target selection, generate/read/complete/context/judge endpoints and exposure queries.

- [ ] **Step 1: Write failing tests for weak priority, actual-used validation, exposure idempotency and reading review linkage.**
- [ ] **Step 2: Run focused tests; expect missing service/routes.**
- [ ] **Step 3: Implement target selection, validated generation, article persistence and completion transaction.**
- [ ] **Step 4: Implement optional AI suggestion and user-final review flow.**
- [ ] **Step 5: Run focused and full backend suites.**

### Task 5: 六页 React 应用与视觉系统

**Files:**
- Create: `frontend/src/app/*`, `frontend/src/pages/*`, `frontend/src/components/*`, `frontend/src/styles/*`
- Create: `frontend/src/**/*.test.tsx`

**Interfaces:**
- Consumes: all `/api` contracts.
- Produces: Dashboard、导入、学习、阅读、词库、设置页面及统一 loading/error/empty states.

- [ ] **Step 1: Generate and inspect full desktop UI concept; extract design tokens and allowed copy.**
- [ ] **Step 2: Write failing Vitest interaction tests for navigation, import editing, study keyboard flow and reading completion.**
- [ ] **Step 3: Implement app shell, reusable primitives and six feature pages.**
- [ ] **Step 4: Run unit tests, ESLint and production build.**
- [ ] **Step 5: Start dev app and compare desktop/mobile screenshots with the concept; fix fidelity gaps.**

### Task 6: 生产启动、集成验收与文档

**Files:**
- Create: `start-vocab.bat`, `scripts/start-vocab.ps1`, `.env.example`, `README.md`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_static_hosting.py`, `tests/e2e/*`

**Interfaces:**
- Produces: single-command Windows startup and verified persistent production workflow.

- [ ] **Step 1: Write failing static-hosting and restart-persistence integration tests.**
- [ ] **Step 2: Implement frontend static fallback, migration/start scripts and environment documentation.**
- [ ] **Step 3: Install dependencies, run backend tests, frontend tests/lint/build and migration check.**
- [ ] **Step 4: Launch production server and exercise core browser flows, including keyboard, error and empty states.**
- [ ] **Step 5: Attempt real PaddleOCR and DeepSeek-assisted path; report external blockers truthfully, restart, verify persisted data and daily backup.**

## Self-review

All spec requirements map to Tasks 1–6. Shared interfaces flow one way from persistence → providers/import → study → reading → UI → production. Provider failures, idempotency, Chinese paths and Source protection are explicit test responsibilities. No schema reset is part of the plan.
