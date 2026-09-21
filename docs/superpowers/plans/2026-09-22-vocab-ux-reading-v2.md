# Vocabulary UX and Reading Assistance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver reliable multi-image import, faster recoverable OCR, explicit current DeepSeek configuration, readable vocabulary typography, onboarding, and persisted reading translation/lookup/new-word workflows.

**Architecture:** Extend the existing FastAPI/SQLAlchemy boundaries rather than bypassing them: import mutations remain in the import API, OCR optimization stays inside the provider/service layer, and reading AI data is persisted in article-specific tables. React receives focused components for file queues, meaning display, onboarding, and interactive article text.

**Tech Stack:** React 19, TypeScript, TanStack Query, FastAPI, SQLAlchemy, Alembic, SQLite, PaddleOCR, DeepSeek OpenAI-compatible API, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-vocab-ux-reading-v2-design.md`

## Global Constraints

- SQLite remains the only source of truth.
- OCR text, image metadata, source meanings and user edits are never overwritten by AI.
- Unconfirmed candidates never become words automatically.
- AI output must pass Pydantic validation.
- Existing user database is upgraded through Alembic; no database recreation.
- Existing uncommitted shortcut/icon work and the user-deleted `.env.example` must be preserved.

## Review Focus

- Repeated drag/drop with duplicate files should accumulate unique files without replacing earlier selections.
- Removing an OCR-complete image must not leave candidates inconsistent; deletion is rejected after candidate creation.
- OCR retries must reuse successful per-image output while processing failed/new images.
- Clicking punctuation or a contraction in article text must not create malformed lookup records.
- Adding an already-known article word must return the existing word instead of duplicating it.

---

### Task 1: Import queue and recoverable image management

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/api/imports.py`
- Modify: `backend/app/api/helpers.py`
- Create: `backend/alembic/versions/0002_reading_assistance.py`
- Modify: `backend/tests/test_import_flow.py`
- Modify: `frontend/src/pages/ImportPage.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Produces: `POST /api/imports/{id}/images`, `DELETE /api/imports/{id}/images/{image_id}`, `DELETE /api/imports/{id}`.
- Produces: `ImportBatch.images[].is_deleted` filtering and batch soft deletion.

- [ ] Add failing backend tests proving image and batch deletion are soft, deleted records are excluded, and candidate-stage image deletion returns 409.
- [ ] Run targeted pytest and verify failures are due to missing endpoints/columns.
- [ ] Add migration/model flags and import API helpers for append and soft deletion.
- [ ] Run targeted pytest and full backend import tests until green.
- [ ] Add failing frontend tests for two sequential file selections and removing one queued file.
- [ ] Run Vitest and verify the queue replacement bug reproduces.
- [ ] Implement merge/dedupe/remove controls, batch append/delete controls, loading and error states.
- [ ] Run frontend tests and typecheck until green.

### Task 2: OCR reuse and bounded image preprocessing

**Files:**
- Modify: `backend/app/services/ocr/paddle.py`
- Modify: `backend/app/api/imports.py`
- Create: `backend/tests/test_ocr_optimization.py`

**Interfaces:**
- Produces: `get_paddle_provider(language: str, use_gpu: bool) -> PaddleOCRProvider`.
- Produces: OCR preprocessing that returns an original or temporary path without changing the stored source path.

- [ ] Add failing tests for provider identity reuse, large-image downscaling, original preservation, and OCR retry skipping.
- [ ] Run targeted pytest and confirm all new behaviors fail before implementation.
- [ ] Implement an LRU provider factory, temporary 2200px preprocessing, cleanup, and per-image result reuse.
- [ ] Run targeted and full backend tests until green.

### Task 3: Current model identity and stricter OCR structuring

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/api/settings.py`
- Modify: `backend/app/prompts/structure_ocr.txt`
- Modify: `backend/app/services/ai/base.py`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/App.test.tsx`
- Create: `backend/tests/test_ai_config.py`

**Interfaces:**
- Produces: `deepseek-flash` default and `deepseek_model_display` settings field.
- Produces: validated candidate lists with phonetics and separately itemized meanings.

- [ ] Add failing tests for the new default, legacy alias mapping, and model display label.
- [ ] Run tests and verify failures reference `deepseek-chat` or missing display data.
- [ ] Implement model resolution and update the settings UI with exact API/version labels.
- [ ] Strengthen the OCR prompt for noise removal, line repair, phonetic preservation, and numbered meaning splitting.
- [ ] Run backend and frontend tests until green.

### Task 4: Meaning presentation, IPA, typography and onboarding

**Files:**
- Create: `frontend/src/components/MeaningList.tsx`
- Create: `frontend/src/components/HelpCenter.tsx`
- Modify: `frontend/src/pages/StudyPage.tsx`
- Modify: `frontend/src/pages/LibraryPage.tsx`
- Modify: `frontend/src/pages/ReadingPage.tsx`
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `backend/app/api/settings.py`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Produces: `splitMeanings(values: string[]) -> string[]` and reusable `<MeaningList>`.
- Produces: `GET/POST /api/settings/onboarding`.

- [ ] Add failing tests for numbered-meaning splitting and first-use help visibility/dismissal.
- [ ] Run pytest/Vitest and verify expected missing behavior.
- [ ] Implement SQLite onboarding state, reusable help dialog, persistent help button, semantic IPA styling, and English font variables.
- [ ] Replace joined meanings with line-based meaning lists across study, library and reading quiz.
- [ ] Run frontend tests, lint and typecheck until green.

### Task 5: Persisted article translation and word lookup

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/alembic/versions/0002_reading_assistance.py`
- Modify: `backend/app/services/ai/base.py`
- Modify: `backend/app/services/ai/deepseek.py`
- Create: `backend/app/prompts/lookup_word.txt`
- Create: `backend/app/prompts/translate_article.txt`
- Modify: `backend/app/api/articles.py`
- Modify: `backend/app/api/helpers.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_reading_flow.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/ReadingPage.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Produces: `ArticleWordLookup`, persisted article translation fields, lookup/translate/add-word endpoints.
- Consumes: DeepSeek provider with `lookup_word` and `translate_article` Pydantic outputs.

- [ ] Add failing backend tests for local-word lookup without AI, cached unknown lookup, persisted translation, duplicate-safe add-to-wordbook, immutable article context source, and exposure creation.
- [ ] Run targeted pytest and verify failures are missing schema/services/endpoints.
- [ ] Implement models/migration, prompts, provider methods, endpoints and history events.
- [ ] Run targeted and full backend tests until green.
- [ ] Add failing frontend tests for clicking a word, showing a translation card, toggling full translation, and adding a lookup to the wordbook.
- [ ] Implement interactive token rendering, lookup history, translation panel and add-to-wordbook states.
- [ ] Run frontend tests, lint, typecheck and build until green.

### Task 6: Migration, production build and browser QA

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-22-vocab-ux-reading-v2-design.md` only if verified behavior requires a documented ruling.

**Interfaces:**
- Consumes: all APIs and UI from Tasks 1–5.
- Produces: migrated persistent database and production assets served by FastAPI.

- [ ] Run Alembic upgrade against the real local database and inspect the resulting schema.
- [ ] Run full backend pytest and Ruff.
- [ ] Run full frontend Vitest, lint, typecheck and production build.
- [ ] Restart the production launcher and verify health plus persistence.
- [ ] Browser-test import queue accumulation/removal, onboarding, model display, reading lookup/translation/add-word and responsive layout; inspect console errors and screenshots.
- [ ] Update README with the exact user workflow, performance behavior, model choice and AI/source boundaries.

